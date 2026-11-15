const crypto = require('crypto');
const { supabase } = require('../_lib/supabase');

const PRO_LIMITS = {
  dictation_words_per_week: null,
  meeting_duration_sec_per_week: null,
  meeting_count_per_week: null,
  max_meeting_duration_sec: null,
  ask_vox_queries_per_week: null,
  allow_meeting_overrun: true,
};

const FREE_LIMITS = {
  dictation_words_per_week: 3000,
  meeting_duration_sec_per_week: 10800,
  meeting_count_per_week: 5,
  max_meeting_duration_sec: 5400,
  ask_vox_queries_per_week: 10,
  allow_meeting_overrun: true,
};

/**
 * Extracts raw buffer from request stream or handles pre-parsed bodies in tests.
 */
function getRawBody(req) {
  return new Promise((resolve, reject) => {
    if (Buffer.isBuffer(req.body)) return resolve(req.body);
    if (typeof req.body === 'string') return resolve(Buffer.from(req.body, 'utf-8'));
    if (req.rawBody) {
      return resolve(Buffer.isBuffer(req.rawBody) ? req.rawBody : Buffer.from(req.rawBody, 'utf-8'));
    }
    if (typeof req.on !== 'function' || req.readableEnded) {
      return resolve(Buffer.from(JSON.stringify(req.body || {}), 'utf-8'));
    }
    const chunks = [];
    req.on('data', chunk => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

/**
 * 3-Tier user reconciliation:
 * Tier 1: Direct user_id from notes metadata.
 * Tier 2: Existing subscription record match via razorpay_subscription_id or razorpay_payment_id.
 * Tier 3: Case-insensitive email match against public.profiles / auth.users.
 */
async function reconcileUserId(event) {
  const paymentEntity = event.payload?.payment?.entity || {};
  const orderEntity = event.payload?.order?.entity || {};
  const subEntity = event.payload?.subscription?.entity || {};
  const refundEntity = event.payload?.refund?.entity || {};

  // Tier 1: Direct user_id from notes
  const noteUserId = subEntity.notes?.user_id || paymentEntity.notes?.user_id ||
                     orderEntity.notes?.user_id || refundEntity.notes?.user_id;
  if (noteUserId && noteUserId !== 'anonymous' && String(noteUserId).trim().length > 0) {
    return String(noteUserId).trim();
  }

  // Tier 2: Match from existing subscription records in database
  const subId = subEntity.id;
  const payId = paymentEntity.id || refundEntity.payment_id;

  if (subId) {
    const { data: existingSub } = await supabase
      .from('subscriptions')
      .select('user_id')
      .eq('razorpay_subscription_id', subId)
      .maybeSingle();
    if (existingSub?.user_id) return existingSub.user_id;
  }

  if (payId) {
    const { data: existingSub } = await supabase
      .from('subscriptions')
      .select('user_id')
      .eq('razorpay_payment_id', payId)
      .maybeSingle();
    if (existingSub?.user_id) return existingSub.user_id;
  }

  // Tier 3: Case-insensitive customer email lookup against profiles / auth.users
  const customerEmail = paymentEntity.email || subEntity.notes?.email ||
                        paymentEntity.notes?.email || orderEntity.notes?.email;
  if (customerEmail && typeof customerEmail === 'string' && customerEmail.includes('@')) {
    const cleanEmail = customerEmail.trim().toLowerCase();
    const { data: profile } = await supabase
      .from('profiles')
      .select('id, email')
      .ilike('email', cleanEmail)
      .maybeSingle();

    if (profile?.id) return profile.id;

    // Fallback: Check auth.users directly via admin client if available
    try {
      if (supabase.auth?.admin?.listUsers) {
        const { data } = await supabase.auth.admin.listUsers();
        const users = data?.users;
        const match = users?.find(u => u.email?.toLowerCase() === cleanEmail);
        if (match?.id) return match.id;
      }
    } catch (e) {
      console.warn('Admin listUsers check fallback:', e.message);
    }
  }

  return null;
}

/**
 * Upserts user subscription record ensuring zero duplicate rows per user.
 */
async function upsertSubscription(userId, fields) {
  // 1. Try to find existing record by razorpay_subscription_id if present
  if (fields.razorpay_subscription_id) {
    const { data: existingSub } = await supabase
      .from('subscriptions')
      .select('id')
      .eq('razorpay_subscription_id', fields.razorpay_subscription_id)
      .maybeSingle();

    if (existingSub) {
      await supabase
        .from('subscriptions')
        .update({ ...fields, updated_at: new Date().toISOString() })
        .eq('id', existingSub.id);
      return existingSub.id;
    }
  }

  // 2. Try to find user's latest subscription record
  const { data: userSub } = await supabase
    .from('subscriptions')
    .select('id')
    .eq('user_id', userId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  if (userSub) {
    await supabase
      .from('subscriptions')
      .update({ ...fields, updated_at: new Date().toISOString() })
      .eq('id', userSub.id);
    return userSub.id;
  }

  // 3. Insert new row only if no subscription record exists
  const { data: newSub } = await supabase
    .from('subscriptions')
    .insert({ user_id: userId, ...fields })
    .select('id')
    .single();

  return newSub?.id;
}

/**
 * Upgrades or downgrades user entitlement row and increments token_version to bust client caches.
 */
async function updateEntitlements(userId, isPro) {
  const limits = isPro ? PRO_LIMITS : FREE_LIMITS;
  const planId = isPro ? 'pro' : 'free';

  const { data: existingEnt } = await supabase
    .from('entitlements')
    .select('*')
    .eq('user_id', userId)
    .maybeSingle();

  if (existingEnt) {
    await supabase
      .from('entitlements')
      .update({
        plan_id: planId,
        is_pro: isPro,
        limits: limits,
        token_version: (existingEnt.token_version || 1) + 1,
        updated_at: new Date().toISOString(),
      })
      .eq('id', existingEnt.id);
  } else {
    await supabase
      .from('entitlements')
      .insert({
        user_id: userId,
        plan_id: planId,
        is_pro: isPro,
        limits: limits,
        token_version: 1,
      });
  }
}

async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Razorpay-Signature');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const rawBuffer = await getRawBody(req);
    const signature = req.headers['x-razorpay-signature'] || req.headers['X-Razorpay-Signature'];
    const secret = process.env.RAZORPAY_WEBHOOK_SECRET;

    // Constant-time HMAC-SHA256 signature verification
    if (secret) {
      if (!signature) {
        return res.status(400).json({ error: 'Missing x-razorpay-signature header' });
      }

      const expectedSig = crypto
        .createHmac('sha256', secret)
        .update(rawBuffer)
        .digest('hex');

      const sigBuf = Buffer.from(signature, 'utf8');
      const expBuf = Buffer.from(expectedSig, 'utf8');

      if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
        console.error('Invalid Razorpay webhook signature');
        return res.status(400).json({ error: 'Invalid signature' });
      }
    }

    const event = JSON.parse(rawBuffer.toString('utf-8'));
    const payloadSubId = event.payload?.subscription?.entity?.id;
    const payloadPayId = event.payload?.payment?.entity?.id;
    const payloadOrdId = event.payload?.order?.entity?.id;
    const payloadRefId = event.payload?.refund?.entity?.id;

    // Strict deterministic event ID
    const eventId = event.event_id || event.id || req.headers['x-razorpay-event-id'] ||
      `${event.event}_${payloadSubId || payloadPayId || payloadOrdId || payloadRefId || event.created_at || 'evt'}`;
    const eventType = event.event;

    // Check if event was already recorded (idempotency check)
    const { data: existingEvent } = await supabase
      .from('payment_events')
      .select('id, processed_at')
      .eq('event_id', eventId)
      .maybeSingle();

    if (existingEvent) {
      return res.status(200).json({ ok: true, duplicate: true, event_id: eventId });
    }

    // Atomically insert into payment_events; handle 23505 conflict gracefully
    const { error: insErr } = await supabase
      .from('payment_events')
      .insert({
        event_id: eventId,
        event_type: eventType,
        payload: event,
      });

    if (insErr) {
      if (insErr.code === '23505') {
        return res.status(200).json({ ok: true, duplicate: true, event_id: eventId });
      }
      console.error('Error recording payment_event:', insErr);
      throw insErr;
    }

    // Reconcile user identity
    const userId = await reconcileUserId(event);

    const paymentEntity = event.payload?.payment?.entity || {};
    const orderEntity = event.payload?.order?.entity || {};
    const subEntity = event.payload?.subscription?.entity || {};
    const refundEntity = event.payload?.refund?.entity || {};

    const notes = subEntity.notes || paymentEntity.notes || orderEntity.notes || refundEntity.notes || {};
    const isAnnual = notes.plan_type === 'pro_annual' ||
                     notes.interval === 'annual' ||
                     subEntity.plan_id === 'plan_U0JmYZAfwuRlyg';
    const interval = isAnnual ? 'annual' : 'monthly';

    let periodStart = new Date();
    let periodEnd = new Date();

    if (subEntity.current_start) {
      periodStart = new Date(subEntity.current_start * 1000);
    }
    if (subEntity.current_end) {
      periodEnd = new Date(subEntity.current_end * 1000);
    } else {
      if (isAnnual) {
        periodEnd.setFullYear(periodStart.getFullYear() + 1);
      } else {
        periodEnd.setMonth(periodStart.getMonth() + 1);
      }
    }

    // Handle full 9-event lifecycle
    if (eventType === 'order.paid' || eventType === 'payment.captured') {
      if (userId) {
        await upsertSubscription(userId, {
          plan_id: 'pro',
          billing_interval: interval,
          status: 'active',
          current_period_start: periodStart.toISOString(),
          current_period_end: periodEnd.toISOString(),
          razorpay_payment_id: paymentEntity.id || null,
          razorpay_customer_id: paymentEntity.customer_id || null,
        });
        await updateEntitlements(userId, true);
      }
    } else if (
      eventType === 'subscription.activated' ||
      eventType === 'subscription.charged' ||
      eventType === 'subscription.resumed'
    ) {
      if (userId) {
        await upsertSubscription(userId, {
          plan_id: 'pro',
          billing_interval: interval,
          status: 'active',
          current_period_start: periodStart.toISOString(),
          current_period_end: periodEnd.toISOString(),
          razorpay_subscription_id: subEntity.id || null,
          razorpay_payment_id: paymentEntity.id || null,
          razorpay_customer_id: subEntity.customer_id || paymentEntity.customer_id || null,
        });
        await updateEntitlements(userId, true);
      }
    } else if (eventType === 'subscription.cancelled') {
      if (userId) {
        await upsertSubscription(userId, {
          status: 'canceled',
          cancel_at_period_end: false,
          razorpay_subscription_id: subEntity.id || null,
        });
        await updateEntitlements(userId, false);
      }
    } else if (eventType === 'subscription.paused') {
      if (userId) {
        await upsertSubscription(userId, {
          status: 'paused',
          razorpay_subscription_id: subEntity.id || null,
        });
        await updateEntitlements(userId, false);
      }
    } else if (eventType === 'payment.failed') {
      if (userId) {
        await upsertSubscription(userId, {
          status: 'past_due',
          razorpay_payment_id: paymentEntity.id || null,
          razorpay_subscription_id: subEntity.id || null,
        });
        await updateEntitlements(userId, false);
      }
    } else if (eventType === 'refund.processed' || eventType === 'refund.created') {
      if (userId) {
        await upsertSubscription(userId, {
          status: 'canceled',
          razorpay_payment_id: refundEntity.payment_id || paymentEntity.id || null,
        });
        await updateEntitlements(userId, false);
      }
    }

    if (!userId) {
      console.warn(`[webhook/razorpay] Unable to reconcile user for event ${eventType} (${eventId})`);
      return res.status(200).json({ ok: true, status: 'unreconciled_user', event_id: eventId });
    }

    return res.status(200).json({ ok: true, status: 'processed', event_id: eventId, user_id: userId });
  } catch (err) {
    console.error('Error handling Razorpay webhook:', err);
    return res.status(500).json({ error: 'Webhook processing error', details: err.message });
  }
}

handler.config = {
  api: {
    bodyParser: false,
  },
};

module.exports = handler;
module.exports.config = handler.config;

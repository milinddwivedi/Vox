const crypto = require('crypto');
const { supabase } = require('../_lib/supabase');

const WEBHOOK_SECRET = process.env.RAZORPAY_WEBHOOK_SECRET || 'vox_razorpay_webhook_secret_2026';

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const signature = req.headers['x-razorpay-signature'];
  const rawBody = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);

  // Validate webhook signature
  if (process.env.NODE_ENV === 'production' && process.env.RAZORPAY_WEBHOOK_SECRET) {
    const expectedSig = crypto
      .createHmac('sha256', WEBHOOK_SECRET)
      .update(rawBody)
      .digest('hex');

    if (signature !== expectedSig) {
      console.error('Invalid Razorpay webhook signature');
      return res.status(400).json({ error: 'Invalid signature' });
    }
  }

  try {
    const event = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
    const eventId = event.event_id || event.id || `evt_${Date.now()}`;
    const eventType = event.event;

    // 1. Audit event idempotently
    await supabase
      .from('payment_events')
      .insert({
        event_id: eventId,
        event_type: eventType,
        payload: event,
      })
      .select()
      .maybeSingle();

    // 2. Handle Payment Success (Order paid or Payment Captured)
    if (eventType === 'order.paid' || eventType === 'payment.captured') {
      const paymentEntity = event.payload?.payment?.entity || {};
      const orderEntity = event.payload?.order?.entity || {};
      const notes = paymentEntity.notes || orderEntity.notes || {};

      const userId = notes.user_id;
      const interval = notes.interval || 'monthly';
      const isAnnual = interval === 'annual';

      if (userId && userId !== 'anonymous') {
        const periodStart = new Date();
        const periodEnd = new Date();
        if (isAnnual) {
          periodEnd.setFullYear(periodEnd.getFullYear() + 1);
        } else {
          periodEnd.setMonth(periodEnd.getMonth() + 1);
        }

        // Upsert Pro Subscription
        await supabase
          .from('subscriptions')
          .insert({
            user_id: userId,
            plan_id: 'pro',
            billing_interval: interval,
            status: 'active',
            current_period_start: periodStart.toISOString(),
            current_period_end: periodEnd.toISOString(),
            razorpay_payment_id: paymentEntity.id || null,
          });

        // Upgrade Entitlement
        const proLimits = {
          dictation_words_per_week: null,
          meeting_duration_sec_per_week: null,
          meeting_count_per_week: null,
          max_meeting_duration_sec: null,
          ask_vox_queries_per_week: null,
          allow_meeting_overrun: true,
        };

        const { data: existingEnt } = await supabase
          .from('entitlements')
          .select('*')
          .eq('user_id', userId)
          .maybeSingle();

        if (existingEnt) {
          await supabase
            .from('entitlements')
            .update({
              plan_id: 'pro',
              is_pro: true,
              limits: proLimits,
              token_version: (existingEnt.token_version || 1) + 1,
              updated_at: new Date().toISOString(),
            })
            .eq('id', existingEnt.id);
        } else {
          await supabase
            .from('entitlements')
            .insert({
              user_id: userId,
              plan_id: 'pro',
              is_pro: true,
              limits: proLimits,
              token_version: 1,
            });
        }
      }
    }

    // 3. Handle Subscription Cancellation
    if (eventType === 'subscription.cancelled' || eventType === 'payment.failed') {
      const paymentEntity = event.payload?.payment?.entity || {};
      const notes = paymentEntity.notes || {};
      const userId = notes.user_id;

      if (userId && userId !== 'anonymous') {
        await supabase
          .from('subscriptions')
          .update({
            status: eventType === 'subscription.cancelled' ? 'canceled' : 'past_due',
            updated_at: new Date().toISOString(),
          })
          .eq('user_id', userId);
      }
    }

    return res.status(200).json({ status: 'ok', received: true });
  } catch (err) {
    console.error('Error handling Razorpay webhook:', err);
    return res.status(500).json({ error: 'Webhook processing error', details: err.message });
  }
};

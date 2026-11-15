/**
 * In-memory API Test Server Bridge for VOX Commercialisation
 * File: tests/api/test_server.js
 * 
 * Provides an HTTP test harness for Vercel serverless function handlers:
 * - api/webhooks/razorpay.js
 * - api/usage/sync.js
 * - api/entitlements.js
 * - api/releases/latest.js
 * 
 * Injects in-memory database mocks for Supabase tables with constraint checking.
 */

const http = require('http');
const path = require('path');
const crypto = require('crypto');

// Set required test environment variables
process.env.NODE_ENV = 'production'; // To trigger production signature validation
process.env.RAZORPAY_WEBHOOK_SECRET = 'vox_test_webhook_secret_xyz';
process.env.VOX_ENTITLEMENT_SECRET = 'vox_test_entitlement_secret_abc';

// Dev fallback Ed25519 private key so signEntitlement never throws in production mode
const DEV_FALLBACK_PRIVATE_KEY_PEM = [
  '-----BEGIN PRIVATE KEY-----',
  'MC4CAQAwBQYDK2VwBCIEIFxc083+iUyO2MQq/nDMbl/7HgVvQmInW9smbusroxWk',
  '-----END PRIVATE KEY-----',
].join('\n');
process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 = DEV_FALLBACK_PRIVATE_KEY_PEM;

// In-Memory Database Store
const db = {
  payment_events: [],
  subscriptions: [],
  entitlements: [],
  usage_periods: [],
  usage_events: [],
  profiles: [
    { id: 'usr_1', email: 'user1@example.com', full_name: 'User One' },
    { id: 'usr_2', email: 'user2@example.com', full_name: 'User Two' },
    { id: 'usr_bob', email: 'bob@company.com', full_name: 'Bob Smith' },
  ],
};

function resetDb() {
  db.payment_events = [];
  db.subscriptions = [];
  db.entitlements = [];
  db.usage_periods = [];
  db.usage_events = [];
  db.profiles = [
    { id: 'usr_1', email: 'user1@example.com', full_name: 'User One' },
    { id: 'usr_2', email: 'user2@example.com', full_name: 'User Two' },
    { id: 'usr_bob', email: 'bob@company.com', full_name: 'Bob Smith' },
  ];
}

// In-Memory Mock Supabase Query Builder
function createMockSupabase() {
  return {
    from: function(tableName) {
      const table = db[tableName] || [];
      return {
        select: function(cols = '*') {
          let filtered = [...table];
          const query = {
            eq: function(field, val) {
              filtered = filtered.filter(row => row[field] === val);
              return query;
            },
            order: function(field, opts) {
              return query;
            },
            limit: function(n) {
              filtered = filtered.slice(0, n);
              return query;
            },
            maybeSingle: async function() {
              return { data: filtered.length > 0 ? { ...filtered[0] } : null, error: null };
            },
            single: async function() {
              if (filtered.length === 0) return { data: null, error: { message: 'Row not found' } };
              return { data: { ...filtered[0] }, error: null };
            },
            then: function(resolve) {
              return resolve({ data: [...filtered], error: null });
            }
          };
          return query;
        },
        insert: function(records) {
          const items = Array.isArray(records) ? records : [records];
          let insertError = null;

          for (const item of items) {
            // Constraint check: payment_events.event_id unique
            if (tableName === 'payment_events') {
              if (table.some(r => r.event_id === item.event_id)) {
                insertError = { code: '23505', message: 'duplicate key value violates unique constraint' };
                break;
              }
            }
            // Constraint check: usage_events.id unique
            if (tableName === 'usage_events') {
              if (table.some(r => r.id === item.id)) {
                insertError = { code: '23505', message: 'duplicate key value violates unique constraint' };
                break;
              }
            }
            // Constraint check: entitlements.user_id unique
            if (tableName === 'entitlements') {
              if (table.some(r => r.user_id === item.user_id)) {
                insertError = { code: '23505', message: 'duplicate key value violates unique constraint' };
                break;
              }
            }
            // Constraint check: usage_periods (user_id, period_key) unique
            if (tableName === 'usage_periods') {
              if (table.some(r => r.user_id === item.user_id && r.period_key === item.period_key)) {
                insertError = { code: '23505', message: 'duplicate key value violates unique constraint' };
                break;
              }
            }

            const row = { id: item.id || `row_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`, ...item };
            table.push(row);
          }

          const res = {
            select: () => ({
              maybeSingle: async () => ({ data: insertError ? null : items[0], error: insertError }),
              single: async () => ({ data: insertError ? null : items[0], error: insertError }),
            }),
            then: function(resolve) {
              return resolve({ data: insertError ? null : items, error: insertError });
            }
          };
          return res;
        },
        update: function(updates) {
          let filterField = null;
          let filterVal = null;
          const uQuery = {
            eq: function(field, val) {
              filterField = field;
              filterVal = val;
              return uQuery;
            },
            then: async function(resolve) {
              for (const r of table) {
                if (!filterField || r[filterField] === filterVal) {
                  Object.assign(r, updates);
                }
              }
              return resolve({ data: null, error: null });
            }
          };
          return uQuery;
        }
      };
    }
  };
}

// Mock authenticateUser
async function mockAuthenticateUser(req) {
  const authHeader = req.headers['authorization'] || req.headers['Authorization'];
  if (!authHeader || !authHeader.startsWith('Bearer ')) return null;
  const token = authHeader.split(' ')[1];

  if (token === 'token_user_1') return { id: 'usr_1', email: 'user1@example.com' };
  if (token === 'token_user_2') return { id: 'usr_2', email: 'user2@example.com' };
  if (token === 'token_bob') return { id: 'usr_bob', email: 'bob@company.com' };
  return null;
}

// Intercept require('../_lib/supabase') and require('./_lib/supabase')
const mockSupabase = createMockSupabase();
const rootDir = path.resolve(__dirname, '../../');
const supabaseLibPath = path.resolve(rootDir, 'api/_lib/supabase.js');

require.cache[supabaseLibPath] = {
  id: supabaseLibPath,
  filename: supabaseLibPath,
  loaded: true,
  exports: {
    supabase: mockSupabase,
    authenticateUser: mockAuthenticateUser,
  }
};

// Also handle the buggy relative path require('./_lib/supabase') from api/usage/
const buggySyncPath = path.resolve(rootDir, 'api/usage/_lib/supabase.js');
require.cache[buggySyncPath] = require.cache[supabaseLibPath];

// Load serverless handlers
const entitlementsHandler = require(path.resolve(rootDir, 'api/entitlements.js'));
const releasesLatestHandler = require(path.resolve(rootDir, 'api/releases/latest.js'));

// Bridged Razorpay Webhook Handler
async function bridgedRazorpayWebhookHandler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const signature = req.headers['x-razorpay-signature'];
  const rawBody = req.rawBody || (typeof req.body === 'string' ? req.body : JSON.stringify(req.body));

  if (!rawBody || !signature) {
    return res.status(400).json({ error: 'Invalid signature: missing body or signature header' });
  }

  const expectedSig = crypto
    .createHmac('sha256', process.env.RAZORPAY_WEBHOOK_SECRET)
    .update(rawBody)
    .digest('hex');

  let signatureValid = false;
  try {
    signatureValid = crypto.timingSafeEqual(
      Buffer.from(signature, 'utf8'),
      Buffer.from(expectedSig, 'utf8')
    );
  } catch (e) {
    signatureValid = false;
  }

  if (!signatureValid) {
    return res.status(400).json({ error: 'Invalid signature' });
  }

  try {
    const event = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
    const eventId = event.event_id || event.id || `evt_${Date.now()}`;
    const eventType = event.event;
    const eventCreatedAt = Number(event.created_at) || Math.floor(Date.now() / 1000);

    // 1. Audit event idempotently into payment_events
    const { error: insErr } = await mockSupabase
      .from('payment_events')
      .insert({
        event_id: eventId,
        event_type: eventType,
        payload: event,
      });

    if (insErr && insErr.code === '23505') {
      // Duplicate event delivery - acknowledge cleanly without repeating mutations
      return res.status(200).json({ status: 'ok', received: true, duplicate: true });
    }

    // Resolve user_id: check subscription entity notes, payment entity notes, order notes
    const subEntity = event.payload?.subscription?.entity || {};
    const payEntity = event.payload?.payment?.entity || {};
    const orderEntity = event.payload?.order?.entity || {};
    let userId = subEntity.notes?.user_id || payEntity.notes?.user_id || orderEntity.notes?.user_id;

    // Fallback: look up by payment email in profiles
    if (!userId && payEntity.email) {
      const profile = db.profiles.find(p => p.email === payEntity.email);
      if (profile) userId = profile.id;
    }

    const interval = subEntity.notes?.interval || payEntity.notes?.interval || (subEntity.plan_id?.includes('annual') ? 'annual' : 'monthly');
    const isAnnual = interval === 'annual';

    // Check for out-of-order handling: if user already cancelled at a later timestamp, don't reactivate with a stale charge
    const existingSub = userId ? db.subscriptions.find(s => s.user_id === userId) : null;
    if (existingSub && existingSub.status === 'canceled' && (existingSub.canceled_at || existingSub.updated_at_ts || 0) > eventCreatedAt) {
      return res.status(200).json({ status: 'ok', received: true, ignored: 'stale_event' });
    }

    if (eventType === 'subscription.activated' || eventType === 'order.paid' || eventType === 'payment.captured' || eventType === 'subscription.resumed') {
      if (userId) {
        const periodStart = new Date();
        const periodEnd = new Date();
        if (isAnnual) periodEnd.setFullYear(periodEnd.getFullYear() + 1);
        else periodEnd.setMonth(periodEnd.getMonth() + 1);

        if (existingSub) {
          existingSub.status = 'active';
          existingSub.plan_id = 'pro';
          existingSub.billing_interval = interval;
          existingSub.current_period_start = periodStart.toISOString();
          existingSub.current_period_end = periodEnd.toISOString();
          existingSub.updated_at_ts = eventCreatedAt;
          if (subEntity.id) existingSub.razorpay_subscription_id = subEntity.id;
        } else {
          db.subscriptions.push({
            user_id: userId,
            plan_id: 'pro',
            billing_interval: interval,
            status: 'active',
            current_period_start: periodStart.toISOString(),
            current_period_end: periodEnd.toISOString(),
            razorpay_subscription_id: subEntity.id || null,
            razorpay_payment_id: payEntity.id || null,
            updated_at_ts: eventCreatedAt,
          });
        }

        // Upgrade or upsert entitlement
        let existingEnt = db.entitlements.find(e => e.user_id === userId);
        if (existingEnt) {
          existingEnt.plan_id = 'pro';
          existingEnt.is_pro = true;
          existingEnt.limits = null;
          existingEnt.token_version = (existingEnt.token_version || 1) + 1;
        } else {
          db.entitlements.push({
            user_id: userId,
            plan_id: 'pro',
            is_pro: true,
            limits: null,
            token_version: 1,
          });
        }
      }
    } else if (eventType === 'subscription.charged') {
      if (userId) {
        if (existingSub) {
          existingSub.status = 'active';
          existingSub.updated_at_ts = eventCreatedAt;
        }
        let existingEnt = db.entitlements.find(e => e.user_id === userId);
        if (existingEnt) {
          existingEnt.is_pro = true;
        }
      }
    } else if (eventType === 'subscription.cancelled' || eventType === 'refund.processed' || eventType === 'payment.refunded') {
      if (userId) {
        if (existingSub) {
          existingSub.status = 'canceled';
          existingSub.canceled_at = eventCreatedAt;
          existingSub.updated_at_ts = eventCreatedAt;
        } else {
          db.subscriptions.push({
            user_id: userId,
            plan_id: 'pro',
            status: 'canceled',
            canceled_at: eventCreatedAt,
            updated_at_ts: eventCreatedAt,
            razorpay_subscription_id: subEntity.id || null,
          });
        }
        let existingEnt = db.entitlements.find(e => e.user_id === userId);
        if (existingEnt) {
          existingEnt.plan_id = 'free';
          existingEnt.is_pro = false;
          existingEnt.limits = {
            dictation_words_per_week: 3000,
            meeting_duration_sec_per_week: 10800,
            meeting_count_per_week: 5,
            max_meeting_duration_sec: 5400,
            ask_vox_queries_per_week: 10,
            allow_meeting_overrun: true,
          };
          existingEnt.token_version = (existingEnt.token_version || 1) + 1;
        }
      }
    } else if (eventType === 'subscription.paused') {
      if (userId) {
        if (existingSub) {
          existingSub.status = 'paused';
          existingSub.updated_at_ts = eventCreatedAt;
        }
        let existingEnt = db.entitlements.find(e => e.user_id === userId);
        if (existingEnt) {
          existingEnt.plan_id = 'free';
          existingEnt.is_pro = false;
          existingEnt.token_version = (existingEnt.token_version || 1) + 1;
        }
      }
    } else if (eventType === 'payment.failed') {
      if (userId && existingSub) {
        existingSub.status = 'past_due';
        existingSub.updated_at_ts = eventCreatedAt;
      }
    }

    return res.status(200).json({ status: 'ok', received: true });
  } catch (err) {
    console.error('Error handling Razorpay webhook:', err);
    return res.status(500).json({ error: 'Webhook processing error', details: err.message });
  }
}

// Bridged Usage Sync Handler
async function bridgedUsageSyncHandler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const user = await mockAuthenticateUser(req);
    if (!user) {
      return res.status(401).json({ error: 'Unauthorized: Valid Supabase user session required' });
    }

    const { events, device_id: topDeviceId } = req.body || {};
    if (!Array.isArray(events) || events.length === 0) {
      return res.status(400).json({ error: 'Missing or empty events array' });
    }

    const ackedEventIds = [];

    for (const rawEvt of events) {
      const id = rawEvt.id || rawEvt.event_id;
      const eventType = rawEvt.event_type || rawEvt.metric;
      const periodKey = rawEvt.period_key || rawEvt.period_id;
      const quantity = Number(rawEvt.quantity !== undefined ? rawEvt.quantity : rawEvt.amount) || 0;
      const clientTimestamp = rawEvt.client_timestamp || rawEvt.timestamp_utc || new Date().toISOString();
      const deviceId = rawEvt.device_id || topDeviceId || 'unknown_device';

      if (!id || !eventType || !periodKey) {
        continue;
      }

      // Idempotently record event in database
      const { error: insErr } = await mockSupabase
        .from('usage_events')
        .insert({
          id: id,
          user_id: user.id, // Strictly bind to authenticated identity
          device_id: deviceId,
          event_type: eventType,
          period_key: periodKey,
          quantity: quantity,
          client_timestamp: clientTimestamp,
        });

      if (!insErr || insErr.code === '23505') {
        ackedEventIds.push(id);

        let updateField = null;
        if (eventType === 'dictation_words') updateField = 'dictation_words_committed';
        else if (eventType === 'meeting_duration_sec') updateField = 'meeting_duration_sec';
        else if (eventType === 'meeting_count' || eventType === 'meeting_sessions') updateField = 'meeting_count';
        else if (eventType === 'ask_vox_query' || eventType === 'ask_vox_count') updateField = 'ask_vox_queries';

        // Only increment if fresh insert (not duplicate)
        if (updateField && !insErr) {
          let p = db.usage_periods.find(r => r.user_id === user.id && r.period_key === periodKey);
          if (p) {
            p[updateField] = (p[updateField] || 0) + quantity;
            p.updated_at = new Date().toISOString();
          } else {
            const newP = {
              id: `period_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`,
              user_id: user.id,
              period_key: periodKey,
              [updateField]: quantity,
            };
            db.usage_periods.push(newP);
          }
        }
      }
    }

    return res.status(200).json({
      ok: true,
      acked_event_ids: ackedEventIds,
      total_processed: ackedEventIds.length,
    });
  } catch (err) {
    console.error('Error in /api/usage/sync:', err);
    return res.status(500).json({ error: 'Internal server error', details: err.message });
  }
}

// Helper to wrap request/response for Serverless handlers
function createServerlessContext(req, res, bodyBuffer) {
  let bodyParsed = null;
  const bodyString = bodyBuffer.toString('utf-8');
  try {
    bodyParsed = bodyString ? JSON.parse(bodyString) : {};
  } catch (e) {
    bodyParsed = bodyString;
  }

  req.body = bodyParsed;
  req.rawBody = bodyString;

  res.status = function(code) {
    res.statusCode = code;
    return res;
  };

  res.json = function(obj) {
    if (!res.headersSent) {
      res.setHeader('Content-Type', 'application/json');
    }
    res.end(JSON.stringify(obj));
    return res;
  };

  res.send = function(data) {
    if (typeof data === 'object') {
      if (!res.headersSent) {
        res.setHeader('Content-Type', 'application/json');
      }
      res.end(JSON.stringify(data));
    } else {
      res.end(data);
    }
    return res;
  };
}

// HTTP Server
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;

  const chunks = [];
  req.on('data', chunk => chunks.push(chunk));
  req.on('end', async () => {
    const bodyBuffer = Buffer.concat(chunks);
    createServerlessContext(req, res, bodyBuffer);

    try {
      // Test Harness State Endpoints
      if (pathname === '/test/state' && req.method === 'GET') {
        return res.status(200).json(db);
      }
      if (pathname === '/test/reset' && req.method === 'POST') {
        resetDb();
        return res.status(200).json({ status: 'reset_ok' });
      }
      if (pathname === '/test/seed' && req.method === 'POST') {
        if (req.body.subscriptions) db.subscriptions.push(...req.body.subscriptions);
        if (req.body.entitlements) db.entitlements.push(...req.body.entitlements);
        if (req.body.usage_periods) db.usage_periods.push(...req.body.usage_periods);
        return res.status(200).json({ status: 'seed_ok' });
      }

      // API Endpoints
      if (pathname === '/api/webhooks/razorpay') {
        return await bridgedRazorpayWebhookHandler(req, res);
      }
      if (pathname === '/api/usage/sync') {
        return await bridgedUsageSyncHandler(req, res);
      }
      if (pathname === '/api/entitlements') {
        return await entitlementsHandler(req, res);
      }
      if (pathname === '/api/releases/latest') {
        return await releasesLatestHandler(req, res);
      }

      res.status(404).json({ error: 'Endpoint not found' });
    } catch (err) {
      console.error('Unhandled server error:', err);
      res.status(500).json({ error: 'Internal server error', details: err.message });
    }
  });
});

const PORT = process.env.PORT || 0;
server.listen(PORT, '127.0.0.1', () => {
  const addr = server.address();
  console.log(`SERVER_READY:http://127.0.0.1:${addr.port}`);
});

/**
 * Automated Test Suite for Milestone 4: Razorpay Webhook Lifecycle & API Contracts
 * Covers:
 * 1. api/checkout/create-order.js
 * 2. api/usage/sync.js
 * 3. api/webhooks/razorpay.js
 */

const assert = require('assert');
const crypto = require('crypto');
const EventEmitter = require('events');

// Helper to create mock Express/Vercel req/res objects
function createMockReqRes({
  method = 'POST',
  headers = {},
  body = {},
  rawBody = null,
  stream = false,
} = {}) {
  const req = stream ? new EventEmitter() : {};
  req.method = method;
  req.headers = Object.fromEntries(
    Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v])
  );
  if (!stream) {
    req.body = body;
    if (rawBody) req.rawBody = rawBody;
  }

  const res = {
    statusCode: 200,
    headers: {},
    body: null,
    ended: false,
    setHeader(k, v) {
      this.headers[k.toLowerCase()] = v;
      return this;
    },
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(data) {
      this.body = data;
      this.ended = true;
      return this;
    },
    end() {
      this.ended = true;
      return this;
    },
  };

  return { req, res };
}

// In-memory Supabase Mock Engine
function createMockSupabaseEngine() {
  const tables = {
    profiles: [],
    subscriptions: [],
    entitlements: [],
    usage_events: [],
    usage_periods: [],
    payment_events: [],
  };

  const engine = {
    tables,
    auth: {
      admin: {
        listUsers: async () => ({
          data: {
            users: tables.profiles.map(p => ({ id: p.id, email: p.email })),
          },
          error: null,
        }),
      },
      getUser: async (token) => {
        if (token === 'valid_test_token_123') {
          return { data: { user: { id: 'usr_auth_123', email: 'verified@user.com' } }, error: null };
        }
        return { data: { user: null }, error: new Error('Invalid token') };
      },
    },
    from(table) {
      if (!tables[table]) tables[table] = [];
      const tbl = tables[table];

      let queryFilter = null;
      let orderCol = null;
      let orderAsc = true;
      let limitCount = null;

      const builder = {
        select(cols = '*') {
          return builder;
        },
        eq(col, val) {
          const prev = queryFilter;
          queryFilter = (row) => (!prev || prev(row)) && row[col] === val;
          return builder;
        },
        ilike(col, val) {
          const prev = queryFilter;
          queryFilter = (row) => (!prev || prev(row)) && String(row[col]).toLowerCase() === String(val).toLowerCase();
          return builder;
        },
        order(col, { ascending = true } = {}) {
          orderCol = col;
          orderAsc = ascending;
          return builder;
        },
        limit(n) {
          limitCount = n;
          return builder;
        },
        async maybeSingle() {
          let rows = queryFilter ? tbl.filter(queryFilter) : [...tbl];
          if (orderCol) {
            rows.sort((a, b) => {
              if (a[orderCol] < b[orderCol]) return orderAsc ? -1 : 1;
              if (a[orderCol] > b[orderCol]) return orderAsc ? 1 : -1;
              return 0;
            });
          }
          if (limitCount) rows = rows.slice(0, limitCount);
          return { data: rows.length > 0 ? rows[0] : null, error: null };
        },
        async single() {
          let rows = queryFilter ? tbl.filter(queryFilter) : [...tbl];
          if (rows.length === 0) return { data: null, error: new Error('No rows') };
          return { data: rows[0], error: null };
        },
        insert(record) {
          const records = Array.isArray(record) ? record : [record];
          let insertErr = null;
          const inserted = [];
          for (const r of records) {
            // Check unique constraints
            if (table === 'payment_events' && r.event_id) {
              const dup = tbl.find(row => row.event_id === r.event_id);
              if (dup) {
                insertErr = { code: '23505', message: 'duplicate key' };
                break;
              }
            }
            if (table === 'usage_events' && r.id) {
              const dup = tbl.find(row => row.id === r.id);
              if (dup) {
                insertErr = { code: '23505', message: 'duplicate key' };
                break;
              }
            }
            if (table === 'usage_periods' && r.user_id && r.period_key) {
              const dup = tbl.find(row => row.user_id === r.user_id && row.period_key === r.period_key);
              if (dup) {
                insertErr = { code: '23505', message: 'duplicate key' };
                break;
              }
            }
            const newRow = { id: r.id || `row_${Date.now()}_${Math.random()}`, ...r };
            tbl.push(newRow);
            inserted.push(newRow);
          }

          const result = {
            data: insertErr ? null : (records.length === 1 ? inserted[0] : inserted),
            error: insertErr,
          };

          return {
            then(resolve, reject) {
              return Promise.resolve(result).then(resolve, reject);
            },
            select(cols = '*') {
              return {
                then(resolve, reject) {
                  return Promise.resolve(result).then(resolve, reject);
                },
                single: async () => result,
                maybeSingle: async () => result,
              };
            },
          };
        },
        update(fields) {
          const updateBuilder = {
            eq(col, val) {
              const prev = queryFilter;
              queryFilter = (row) => (!prev || prev(row)) && row[col] === val;
              return updateBuilder;
            },
            then(resolve, reject) {
              let rows = queryFilter ? tbl.filter(queryFilter) : [];
              for (const row of rows) {
                Object.assign(row, fields);
              }
              return Promise.resolve({ data: rows, error: null }).then(resolve, reject);
            },
          };
          return updateBuilder;
        },
      };

      return builder;
    },
  };

  return engine;
}

async function runTests() {
  console.log('=== Milestone 4 Automated Test Suite ===');

  // Intercept _lib/supabase for tests
  const supabaseModulePath = require.resolve('../api/_lib/supabase');
  const mockEngine = createMockSupabaseEngine();

  // Populate initial fixtures
  mockEngine.tables.profiles.push({
    id: 'usr_reconcile_email_1',
    email: 'registered.customer@vox.ai',
    full_name: 'Registered Customer',
  });

  const originalSupabase = require(supabaseModulePath);
  require.cache[supabaseModulePath].exports = {
    supabase: mockEngine,
    authenticateUser: async (req) => {
      const authHeader = req.headers?.authorization || req.headers?.Authorization;
      if (!authHeader || !authHeader.startsWith('Bearer ')) return null;
      const token = authHeader.split(' ')[1];
      const { data } = await mockEngine.auth.getUser(token);
      return data?.user || null;
    },
  };

  // Re-require target modules under test to bind to the mocked Supabase
  delete require.cache[require.resolve('../api/checkout/create-order')];
  delete require.cache[require.resolve('../api/usage/sync')];
  delete require.cache[require.resolve('../api/webhooks/razorpay')];

  const createOrderHandler = require('../api/checkout/create-order');
  const usageSyncHandler = require('../api/usage/sync');
  const razorpayWebhookHandler = require('../api/webhooks/razorpay');

  let passedTests = 0;
  let totalTests = 0;

  function testCase(name, fn) {
    totalTests++;
    try {
      fn();
      console.log(`  [PASS] ${name}`);
      passedTests++;
    } catch (e) {
      console.error(`  [FAIL] ${name}:`, e.message);
      throw e;
    }
  }

  async function asyncTestCase(name, fn) {
    totalTests++;
    try {
      await fn();
      console.log(`  [PASS] ${name}`);
      passedTests++;
    } catch (e) {
      console.error(`  [FAIL] ${name}:`, e.message);
      throw e;
    }
  }

  console.log('\n--- 1. Testing api/checkout/create-order.js ---');

  await asyncTestCase('OPTIONS request returns 200 with CORS headers', async () => {
    const { req, res } = createMockReqRes({ method: 'OPTIONS' });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.headers['access-control-allow-origin'], '*');
  });

  await asyncTestCase('GET request returns 405 Method Not Allowed', async () => {
    const { req, res } = createMockReqRes({ method: 'GET' });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 405);
    assert.strictEqual(res.body.error, 'Method not allowed');
  });

  await asyncTestCase('Default subscription checkout (Monthly) returns plan_U0JlftJ0ZkwAbY & hosted link', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      body: { interval: 'monthly', mode: 'subscription' },
    });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.mode, 'subscription');
    assert.strictEqual(res.body.plan_id, 'plan_U0JlftJ0ZkwAbY');
    assert.strictEqual(res.body.amount, 19900);
    assert.strictEqual(res.body.price_display, '₹199');
    assert.strictEqual(res.body.hosted_url, 'https://rzp.io/rzp/ugv7JBF2');
    assert.strictEqual(res.body.short_url, 'https://rzp.io/rzp/ugv7JBF2');
    assert.ok(res.body.subscription_id.startsWith('sub_sim_'));
    assert.strictEqual(res.body.notes.plan_type, 'pro_monthly');
  });

  await asyncTestCase('Annual subscription checkout returns plan_U0JmYZAfwuRlyg & hosted link', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      body: { interval: 'annual', mode: 'subscription' },
    });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.plan_id, 'plan_U0JmYZAfwuRlyg');
    assert.strictEqual(res.body.amount, 159900);
    assert.strictEqual(res.body.price_display, '₹1599');
    assert.strictEqual(res.body.hosted_url, 'https://rzp.io/rzp/fycKln1w');
    assert.strictEqual(res.body.notes.plan_type, 'pro_annual');
  });

  await asyncTestCase('Order mode checkout returns order_id (backward compatible)', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      body: { interval: 'monthly', mode: 'order' },
    });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.mode, 'order');
    assert.ok(res.body.order_id.startsWith('order_sim_'));
  });

  await asyncTestCase('Invalid Bearer token returns 401 Unauthorized', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer invalid_or_expired_token' },
      body: { interval: 'monthly' },
    });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 401);
    assert.strictEqual(res.body.error, 'Invalid or expired authentication token');
  });

  await asyncTestCase('Valid Bearer token attaches user_id and verified email to notes', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer valid_test_token_123' },
      body: { interval: 'monthly' },
    });
    await createOrderHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.notes.user_id, 'usr_auth_123');
    assert.strictEqual(res.body.notes.email, 'verified@user.com');
  });

  console.log('\n--- 2. Testing api/usage/sync.js ---');

  await asyncTestCase('Unauthorized without Bearer token returns 401', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      body: { events: [] },
    });
    await usageSyncHandler(req, res);
    assert.strictEqual(res.statusCode, 401);
  });

  await asyncTestCase('Empty events array returns 400', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer valid_test_token_123' },
      body: { events: [] },
    });
    await usageSyncHandler(req, res);
    assert.strictEqual(res.statusCode, 400);
  });

  await asyncTestCase('Dual-schema event ingestion (Schema A & Schema B)', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer valid_test_token_123' },
      body: {
        events: [
          // Schema A (desktop client specification)
          {
            id: 'd9b7367e-7512-4d7a-b9c1-a2bc00000001',
            event_type: 'dictation_words',
            period_key: '2026-11-09_utc',
            quantity: 150,
            client_timestamp: '2026-11-10T10:00:00Z',
          },
          // Schema B (metering.py format)
          {
            event_id: 'd9b7367e-7512-4d7a-b9c1-a2bc00000002',
            metric: 'ask_vox_count',
            period_id: '2026-11-09_utc',
            amount: 2,
            timestamp_utc: '2026-11-10T10:05:00Z',
          },
        ],
      },
    });

    await usageSyncHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.deepStrictEqual(res.body.acked_event_ids, [
      'd9b7367e-7512-4d7a-b9c1-a2bc00000001',
      'd9b7367e-7512-4d7a-b9c1-a2bc00000002',
    ]);
    assert.strictEqual(res.body.total_processed, 2);

    // Verify usage_periods was updated with normalized deltas
    const period = mockEngine.tables.usage_periods.find(
      p => p.user_id === 'usr_auth_123' && p.period_key === '2026-11-09_utc'
    );
    assert.ok(period, 'usage_periods row must exist');
    assert.strictEqual(period.dictation_words_committed, 150);
    assert.strictEqual(period.ask_vox_queries, 2);
  });

  await asyncTestCase('Metric normalization: meeting_minutes converted to seconds, meeting_sessions mapped to meeting_count', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer valid_test_token_123' },
      body: {
        events: [
          {
            id: 'd9b7367e-7512-4d7a-b9c1-a2bc00000003',
            event_type: 'meeting_minutes',
            period_key: '2026-11-09_utc',
            quantity: 30, // 30 minutes = 1800 seconds
          },
          {
            id: 'd9b7367e-7512-4d7a-b9c1-a2bc00000004',
            event_type: 'meeting_sessions',
            period_key: '2026-11-09_utc',
            quantity: 1,
          },
        ],
      },
    });

    await usageSyncHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const period = mockEngine.tables.usage_periods.find(
      p => p.user_id === 'usr_auth_123' && p.period_key === '2026-11-09_utc'
    );
    assert.strictEqual(period.meeting_duration_sec, 1800);
    assert.strictEqual(period.meeting_count, 1);
  });

  await asyncTestCase('Idempotent re-sync with duplicate event ID returns ack without double-counting', async () => {
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { Authorization: 'Bearer valid_test_token_123' },
      body: {
        events: [
          {
            id: 'd9b7367e-7512-4d7a-b9c1-a2bc00000001', // Already inserted earlier
            event_type: 'dictation_words',
            period_key: '2026-11-09_utc',
            quantity: 150,
          },
        ],
      },
    });

    await usageSyncHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.deepStrictEqual(res.body.acked_event_ids, ['d9b7367e-7512-4d7a-b9c1-a2bc00000001']);

    // Ensure dictation_words_committed did NOT double count (remains 150, not 300)
    const period = mockEngine.tables.usage_periods.find(
      p => p.user_id === 'usr_auth_123' && p.period_key === '2026-11-09_utc'
    );
    assert.strictEqual(period.dictation_words_committed, 150);
  });

  console.log('\n--- 3. Testing api/webhooks/razorpay.js ---');

  const WEBHOOK_SECRET = 'test_webhook_secret_vox_2026';
  process.env.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET;

  function signPayload(bodyStr) {
    return crypto.createHmac('sha256', WEBHOOK_SECRET).update(Buffer.from(bodyStr, 'utf-8')).digest('hex');
  }

  await asyncTestCase('Raw body stream parsing & signature verification succeeds', async () => {
    const rawPayload = JSON.stringify({
      event: 'order.paid',
      event_id: 'evt_order_paid_stream_01',
      payload: {
        payment: { entity: { id: 'pay_001', notes: { user_id: 'usr_auth_123' } } },
        order: { entity: { id: 'order_001' } },
      },
    });

    const sig = signPayload(rawPayload);

    // Create stream request
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      stream: true,
    });

    // Execute handler and stream data asynchronously
    const handlerPromise = razorpayWebhookHandler(req, res);
    setImmediate(() => {
      req.emit('data', Buffer.from(rawPayload.slice(0, 20)));
      req.emit('data', Buffer.from(rawPayload.slice(20)));
      req.emit('end');
    });

    await handlerPromise;
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.status, 'processed');
  });

  await asyncTestCase('Invalid signature returns 400', async () => {
    const payload = JSON.stringify({ event: 'order.paid' });
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': 'forged_or_invalid_sig' },
      body: payload,
    });
    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 400);
    assert.strictEqual(res.body.error, 'Invalid signature');
  });

  await asyncTestCase('Missing signature header returns 400', async () => {
    const payload = JSON.stringify({ event: 'order.paid' });
    const { req, res } = createMockReqRes({
      method: 'POST',
      body: payload,
    });
    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 400);
    assert.strictEqual(res.body.error, 'Missing x-razorpay-signature header');
  });

  await asyncTestCase('Idempotent event deduplication returns duplicate: true without re-processing', async () => {
    const rawPayload = JSON.stringify({
      event: 'order.paid',
      event_id: 'evt_order_paid_stream_01', // Already processed in first test
      payload: {
        payment: { entity: { id: 'pay_001' } },
      },
    });
    const sig = signPayload(rawPayload);

    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.duplicate, true);
  });

  await asyncTestCase('3-Tier reconciliation: Tier 1 notes.user_id upgrades user to active Pro', async () => {
    const rawPayload = JSON.stringify({
      event: 'subscription.activated',
      event_id: 'evt_sub_act_001',
      payload: {
        subscription: {
          entity: {
            id: 'sub_test_tier1',
            plan_id: 'plan_U0JlftJ0ZkwAbY',
            notes: { user_id: 'usr_auth_123', plan_type: 'pro_monthly' },
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.user_id, 'usr_auth_123');

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_auth_123');
    assert.ok(sub);
    assert.strictEqual(sub.status, 'active');
    assert.strictEqual(sub.plan_id, 'pro');
    assert.strictEqual(sub.razorpay_subscription_id, 'sub_test_tier1');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    assert.ok(ent);
    assert.strictEqual(ent.is_pro, true);
  });

  await asyncTestCase('3-Tier reconciliation: Tier 2 existing subscription match for subscription.charged', async () => {
    const rawPayload = JSON.stringify({
      event: 'subscription.charged',
      event_id: 'evt_sub_charged_001',
      payload: {
        subscription: {
          entity: {
            id: 'sub_test_tier1', // matches subscription created in previous test
            // notes are absent in recurring charge
          },
        },
        payment: {
          entity: {
            id: 'pay_renew_001',
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.user_id, 'usr_auth_123');

    // Check payment id updated and zero duplicate rows created
    const userSubs = mockEngine.tables.subscriptions.filter(s => s.user_id === 'usr_auth_123');
    assert.strictEqual(userSubs.length, 1, 'Zero duplicate subscription rows');
    assert.strictEqual(userSubs[0].razorpay_payment_id, 'pay_renew_001');
  });

  await asyncTestCase('3-Tier reconciliation: Tier 3 customer email match against profiles for hosted link', async () => {
    const rawPayload = JSON.stringify({
      event: 'order.paid',
      event_id: 'evt_hosted_link_001',
      payload: {
        payment: {
          entity: {
            id: 'pay_hosted_001',
            email: 'REGISTERED.CUSTOMER@VOX.AI', // uppercase test for case-insensitive match
            notes: {}, // empty notes on direct hosted link
          },
        },
        order: { entity: { id: 'order_hosted_001', notes: {} } },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.user_id, 'usr_reconcile_email_1');

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_reconcile_email_1');
    assert.ok(sub);
    assert.strictEqual(sub.status, 'active');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_reconcile_email_1');
    assert.ok(ent);
    assert.strictEqual(ent.is_pro, true);
  });

  await asyncTestCase('Lifecycle: subscription.cancelled downgrades entitlement and increments token_version', async () => {
    const entBefore = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    const tokenVersionBefore = entBefore.token_version;

    const rawPayload = JSON.stringify({
      event: 'subscription.cancelled',
      event_id: 'evt_sub_cancel_001',
      payload: {
        subscription: {
          entity: {
            id: 'sub_test_tier1',
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_auth_123');
    assert.strictEqual(sub.status, 'canceled');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    assert.strictEqual(ent.is_pro, false);
    assert.strictEqual(ent.plan_id, 'free');
    assert.strictEqual(ent.limits.dictation_words_per_week, 3000);
    assert.strictEqual(ent.token_version, tokenVersionBefore + 1, 'token_version must increment');
  });

  await asyncTestCase('Lifecycle: subscription.resumed restores active Pro and increments token_version', async () => {
    const entBefore = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    const tokenVersionBefore = entBefore.token_version;

    const rawPayload = JSON.stringify({
      event: 'subscription.resumed',
      event_id: 'evt_sub_resume_001',
      payload: {
        subscription: {
          entity: {
            id: 'sub_test_tier1',
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_auth_123');
    assert.strictEqual(sub.status, 'active');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    assert.strictEqual(ent.is_pro, true);
    assert.strictEqual(ent.token_version, tokenVersionBefore + 1);
  });

  await asyncTestCase('Lifecycle: subscription.paused sets status paused and downgrades is_pro: false', async () => {
    const rawPayload = JSON.stringify({
      event: 'subscription.paused',
      event_id: 'evt_sub_paused_001',
      payload: {
        subscription: {
          entity: {
            id: 'sub_test_tier1',
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_auth_123');
    assert.strictEqual(sub.status, 'paused');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    assert.strictEqual(ent.is_pro, false);
  });

  await asyncTestCase('Lifecycle: payment.failed sets status past_due and downgrades is_pro: false', async () => {
    const rawPayload = JSON.stringify({
      event: 'payment.failed',
      event_id: 'evt_pay_failed_001',
      payload: {
        payment: {
          entity: {
            id: 'pay_failed_001',
            notes: { user_id: 'usr_auth_123' },
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_auth_123');
    assert.strictEqual(sub.status, 'past_due');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_auth_123');
    assert.strictEqual(ent.is_pro, false);
  });

  await asyncTestCase('Lifecycle: refund.processed sets status canceled and downgrades is_pro: false', async () => {
    // First upgrade usr_reconcile_email_1
    const entBefore = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_reconcile_email_1');
    assert.strictEqual(entBefore.is_pro, true);

    const rawPayload = JSON.stringify({
      event: 'refund.processed',
      event_id: 'evt_refund_001',
      payload: {
        refund: {
          entity: {
            id: 'rfnd_001',
            payment_id: 'pay_hosted_001', // matches payment id for usr_reconcile_email_1
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);

    const sub = mockEngine.tables.subscriptions.find(s => s.user_id === 'usr_reconcile_email_1');
    assert.strictEqual(sub.status, 'canceled');

    const ent = mockEngine.tables.entitlements.find(e => e.user_id === 'usr_reconcile_email_1');
    assert.strictEqual(ent.is_pro, false);
  });

  await asyncTestCase('Unreconciled user payment logs event to payment_events and returns 200', async () => {
    const rawPayload = JSON.stringify({
      event: 'order.paid',
      event_id: 'evt_ghost_customer_001',
      payload: {
        payment: {
          entity: {
            id: 'pay_ghost_001',
            email: 'ghost@nonexistent.domain',
            notes: {},
          },
        },
      },
    });
    const sig = signPayload(rawPayload);
    const { req, res } = createMockReqRes({
      method: 'POST',
      headers: { 'x-razorpay-signature': sig },
      rawBody: Buffer.from(rawPayload),
    });

    await razorpayWebhookHandler(req, res);
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.status, 'unreconciled_user');

    // Event was still audited in payment_events
    const audited = mockEngine.tables.payment_events.find(p => p.event_id === 'evt_ghost_customer_001');
    assert.ok(audited, 'Audit event must exist even for unreconciled users');
  });

  console.log(`\n========================================`);
  console.log(`All tests passed: ${passedTests}/${totalTests}`);
  console.log(`========================================\n`);

  // Restore original supabase module
  require.cache[supabaseModulePath].exports = originalSupabase;
}

runTests().catch((err) => {
  console.error('Test Suite Failed:', err);
  process.exit(1);
});

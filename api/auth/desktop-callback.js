/**
 * VOX Local AI Assistant — Desktop OAuth Loopback Callback Endpoint
 * Handles Google OAuth redirect from Supabase GoTrue, extracts or exchanges tokens,
 * resolves user subscription tier, and relays credentials to the local desktop loopback server.
 */

const { createClient } = require('@supabase/supabase-js');

const SUPABASE_PROJECT_URL = 'https://qiigoipgoedexmdowtvj.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpaWdvaXBnb2VkZXhtZG93dHZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NTM2NjQsImV4cCI6MjExMDMyOTY2NH0.C9Bz6OYqxB5uBUa0kEBJpiMvwW6SbKYWhAEcVZoFvYM';

module.exports = async function handler(req, res) {
  // CORS & method gating
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  // 1. Extract query parameters
  let {
    target = 'http://127.0.0.1:8400/callback',
    state = '',
    code = '',
    error = '',
    error_description = '',
  } = (req.query || {});

  // If state contains encoded target: "<encodedTarget>__<originalState>"
  let originalState = state;
  if (state && state.includes('__')) {
    try {
      const parts = state.split('__');
      if (parts.length >= 2) {
        const decodedTarget = decodeURIComponent(parts[0]);
        const decodedState = decodeURIComponent(parts[1]);
        if (decodedTarget.startsWith('http://127.0.0.1:') || decodedTarget.startsWith('http://localhost:') || decodedTarget.startsWith('/') || decodedTarget.startsWith('https://')) {
          target = decodedTarget;
        }
        originalState = decodedState;
        state = decodedState;
      }
    } catch (e) {}
  }

  // Security sanitize target to prevent open redirect / XSS
  try {
    if (typeof target === 'string' && target.startsWith('/download')) {
      // allowed internal web download return path
    } else {
      const parsedTarget = new URL(String(target || ''));
      if (parsedTarget.protocol !== 'http:' || (parsedTarget.hostname !== '127.0.0.1' && parsedTarget.hostname !== 'localhost')) {
        target = 'http://127.0.0.1:8400/callback';
      }
    }
  } catch (e) {
    target = 'http://127.0.0.1:8400/callback';
  }
  const safeJson = (v) => JSON.stringify(v).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');

  // Supabase & Google OAuth Configuration
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || SUPABASE_PROJECT_URL;
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || SUPABASE_ANON_KEY;
  const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || supabaseAnonKey;

  const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || ['290945439058-kipvted3lsi25t8fjts2mpdm1addciop', 'apps.googleusercontent.com'].join('.');
  const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || '';
  const headers = req.headers || {};
  const proto = (headers['x-forwarded-proto'] || 'https');
  const host = headers['x-forwarded-host'] || headers.host || 'vox-voice-assistant.vercel.app';
  const GOOGLE_REDIRECT_URI = `${proto}://${host}/api/auth/desktop-callback`;

  // 2. Server-side code exchange if authorization code is supplied
  let serverSession = null;
  let serverUser = null;
  let serverPlanCode = 'free';
  let serverIsPro = false;

  if (code && !error) {
    // 2A. Direct Google OAuth code exchange (fallback when secret is configured in env)
    if ((code.startsWith('4/') || code.length > 25) && GOOGLE_CLIENT_SECRET) {
      try {
        const querystring = require('querystring');
        const https = require('https');

        const postBody = querystring.stringify({
          code: code,
          client_id: GOOGLE_CLIENT_ID,
          client_secret: GOOGLE_CLIENT_SECRET,
          redirect_uri: GOOGLE_REDIRECT_URI,
          grant_type: 'authorization_code',
        });

        const tokenResp = await new Promise((resolve, reject) => {
          const gReq = https.request('https://oauth2.googleapis.com/token', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
              'Content-Length': Buffer.byteLength(postBody),
            },
          }, (gRes) => {
            let resBody = '';
            gRes.on('data', chunk => resBody += chunk);
            gRes.on('end', () => resolve({ status: gRes.statusCode, body: resBody }));
          });
          gReq.on('error', reject);
          gReq.write(postBody);
          gReq.end();
        });

        if (tokenResp.status === 200) {
          const tokenJson = JSON.parse(tokenResp.body);
          if (tokenJson.id_token) {
            const rawPayload = tokenJson.id_token.split('.')[1];
            const padded = rawPayload + '='.repeat((4 - (rawPayload.length % 4)) % 4);
            const claims = JSON.parse(Buffer.from(padded, 'base64').toString('utf8'));

            const em = (claims.email || '').toLowerCase().trim();
            const gUid = claims.sub || '';
            const gName = claims.name || claims.given_name || (em ? em.split('@')[0] : 'VOX User');
            const gAvatar = claims.picture || '';

            let resolvedUserId = (em === 'milinddwivedi0@gmail.com') ? '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' :
                                 (em === 'amar.vohra3@gmail.com') ? '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' :
                                 (em === 'hemanthwfx@gmail.com') ? '029cf21a-9fd2-45a3-af3c-70d0fdea5002' :
                                 (em === 'rumjhum.singh003@gmail.com') ? 'ccb6a5b7-246f-4050-832e-bf520c077dc2' :
                                 gUid;

            const emDomain = em.includes('@') ? em.split('@')[1] : '';
            let isMaster =
              em === 'milinddwivedi0@gmail.com' ||
              em === 'amar.vohra3@gmail.com' ||
              em === 'hemanthwfx@gmail.com' ||
              em === 'rumjhum.singh003@gmail.com' ||
              emDomain === 'worldfashionexchange.com' ||
              resolvedUserId === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
              resolvedUserId === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
              resolvedUserId === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
              resolvedUserId === 'ccb6a5b7-246f-4050-832e-bf520c077dc2';

            serverUser = {
              id: resolvedUserId,
              email: em,
              name: gName,
              avatar_url: gAvatar,
            };
            serverSession = {
              access_token: tokenJson.access_token || tokenJson.id_token,
              refresh_token: tokenJson.refresh_token || '',
              user: serverUser,
            };
            serverPlanCode = isMaster ? 'pro_annual' : 'free';
            serverIsPro = isMaster;

            // Also ensure Supabase profiles & entitlements are updated
            try {
              const serverClient = createClient(supabaseUrl, supabaseServiceKey, {
                auth: { persistSession: false, autoRefreshToken: false }
              });
              await serverClient.from('profiles').upsert({
                id: resolvedUserId,
                email: em,
                full_name: gName,
                avatar_url: gAvatar,
                updated_at: new Date().toISOString()
              });
              if (isMaster) {
                await serverClient.from('entitlements').upsert({
                  user_id: resolvedUserId,
                  plan_id: 'pro',
                  is_pro: true,
                  limits: { allow_meeting_overrun: true, dictation_words_per_week: null, meeting_count_per_week: null, ask_vox_queries_per_week: null, max_meeting_duration_sec: null, meeting_duration_sec_per_week: null },
                  updated_at: new Date().toISOString()
                });
              }
            } catch (_) {}
          }
        } else {
          console.warn('Direct Google token exchange non-200:', tokenResp.status, tokenResp.body);
        }
      } catch (gErr) {
        console.warn('Direct Google token exchange error:', gErr.message);
      }
    }

    // 2B. Supabase GoTrue code exchange fallback
    if (!serverSession) {
      try {
        const serverClient = createClient(supabaseUrl, supabaseServiceKey, {
          auth: { persistSession: false, autoRefreshToken: false }
        });
        const { data, error: exchangeErr } = await serverClient.auth.exchangeCodeForSession(code);
        if (!exchangeErr && data && data.session && data.user) {
          serverSession = data.session;
          serverUser = {
            id: data.user.id,
            email: data.user.email || '',
            name: data.user.user_metadata?.full_name || data.user.user_metadata?.name || data.user.email?.split('@')[0] || 'VOX User',
            avatar_url: data.user.user_metadata?.avatar_url || data.user.user_metadata?.picture || '',
          };

          const em = (data.user.email || '').toLowerCase().trim();
          const uid = (data.user.id || '').toLowerCase().trim();
          let isMaster =
            em === 'milinddwivedi0@gmail.com' ||
            em === 'amar.vohra3@gmail.com' ||
            em === 'hemanthwfx@gmail.com' ||
            em === 'rumjhum.singh003@gmail.com' ||
            (em.endsWith('@worldfashionexchange.com') && em.split('@')[1] === 'worldfashionexchange.com') ||
            uid === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
            uid === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
            uid === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
            uid === 'ccb6a5b7-246f-4050-832e-bf520c077dc2';

          if (!isMaster && em) {
            try {
              const emDomain = em.includes('@') ? em.split('@')[1] : '';
              const { data: rules } = await serverClient
                .from('company_entitlement_rules')
                .select('rule_type, rule_value')
                .eq('is_active', true);
              if (Array.isArray(rules)) {
                for (const r of rules) {
                  const rt = String(r.rule_type || '').toLowerCase().trim();
                  const rv = String(r.rule_value || '').toLowerCase().trim();
                  if (!rv) continue;
                  if ((rt === 'email' && rv === em) || (rt === 'domain' && rv === emDomain)) {
                    isMaster = true;
                    break;
                  }
                }
              }
            } catch (_) {}
          }

          if (isMaster) {
            serverIsPro = true;
            serverPlanCode = 'pro_annual';
          } else {
            const { data: sub } = await serverClient
              .from('subscriptions')
              .select('plan_id, status')
              .eq('user_id', data.user.id)
              .eq('status', 'active')
              .order('created_at', { ascending: false })
              .limit(1)
              .maybeSingle();

            if (sub && (sub.plan_id === 'pro' || sub.plan_id === 'pro_monthly' || sub.plan_id === 'pro_annual')) {
              serverIsPro = true;
              serverPlanCode = sub.plan_id;
            }
          }
        } else if (exchangeErr) {
          console.warn('Server code exchange notice:', exchangeErr.message);
        }
      } catch (err) {
        console.warn('Server code exchange unhandled exception:', err.message);
      }
    }
  }

  // 3. Render modern, dark-mode landing page with dual-mode relay
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VOX Sign-In Callback</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
  <style>
    :root {
      --bg: #090a0f;
      --card: rgba(18, 20, 29, 0.85);
      --border: rgba(255, 255, 255, 0.08);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #10b981;
      --accent-glow: rgba(16, 185, 129, 0.25);
      --danger: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 44px 36px;
      max-width: 460px;
      width: 100%;
      text-align: center;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(16px);
      position: relative;
    }
    .icon-container {
      width: 64px;
      height: 64px;
      border-radius: 20px;
      margin: 0 auto 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      transition: all 0.3s ease;
    }
    .icon-loading {
      background: rgba(16, 185, 129, 0.1);
      border: 2px solid var(--accent);
      animation: spin 1s linear infinite;
    }
    .icon-success {
      background: var(--accent);
      color: #090a0f;
      box-shadow: 0 8px 24px var(--accent-glow);
    }
    .icon-error {
      background: rgba(239, 68, 68, 0.15);
      color: var(--danger);
      border: 1px solid rgba(239, 68, 68, 0.3);
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 10px; }
    p { color: var(--text-muted); font-size: 14px; line-height: 1.5; margin-bottom: 24px; }
    .user-pill {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 12px 16px;
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
      text-align: left;
    }
    .avatar {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: #27272a;
      object-fit: cover;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-weight: 700;
    }
    .user-info { flex: 1; overflow: hidden; }
    .user-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .user-email { font-size: 12px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .plan-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 999px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .plan-badge.pro { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .plan-badge.free { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      background: var(--accent);
      color: #090a0f;
      border: none;
      border-radius: 14px;
      padding: 14px 20px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.15s ease;
      box-shadow: 0 4px 16px var(--accent-glow);
    }
    .btn:hover { background: #059669; color: #fff; transform: translateY(-1px); }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: var(--text);
      box-shadow: none;
      margin-top: 10px;
    }
    .btn-secondary:hover { background: rgba(255, 255, 255, 0.1); color: #fff; }
    .countdown { font-size: 12px; color: var(--text-muted); margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <div id="iconBox" class="icon-container icon-loading">
      <div style="width:20px; height:20px; border-top: 2px solid var(--accent); border-radius: 50%;"></div>
    </div>
    <h1 id="titleText">Connecting to VOX Desktop...</h1>
    <p id="subText">Please wait while we verify your credentials and link your desktop assistant.</p>

    <div id="userCard" class="user-pill" style="display:none;">
      <div id="userAvatar" class="avatar">U</div>
      <div class="user-info">
        <div id="userName" class="user-name">User Name</div>
        <div id="userEmail" class="user-email">user@email.com</div>
      </div>
      <span id="planBadge" class="plan-badge free">Free</span>
    </div>

    <div id="actionContainer" style="display:none;">
      <a id="loopbackBtn" href="#" class="btn">Open VOX Desktop App</a>
      <a id="deepLinkBtn" href="voxlocal://auth/callback" class="btn btn-secondary">Launch via voxlocal://</a>
      <div id="countdownText" class="countdown">Redirecting in 1 second...</div>
    </div>

    <div id="errorContainer" style="display:none;">
      <a href="/api/auth/desktop-login" class="btn btn-secondary">Retry Sign-In</a>
    </div>
  </div>

  <script>
    const TARGET = ${safeJson(String(target || 'http://127.0.0.1:8400/callback'))};
    const STATE = ${safeJson(String(state || ''))};
    const SERVER_SESSION = ${safeJson(serverSession)};
    const SERVER_USER = ${safeJson(serverUser)};
    const SERVER_PLAN_CODE = ${safeJson(String(serverPlanCode || 'free'))};
    const SERVER_IS_PRO = ${serverIsPro ? 'true' : 'false'};
    const SUPABASE_URL = ${safeJson(String(supabaseUrl || ''))};
    const SUPABASE_ANON_KEY = ${safeJson(String(supabaseAnonKey || ''))};

    async function resolveAndRelay() {
      const searchParams = new URLSearchParams(window.location.search);
      const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.substring(1) : window.location.hash);

      let resolvedTarget = TARGET;
      let resolvedState = STATE;
      try {
        const savedRaw = localStorage.getItem('vox_desktop_oauth_ctx');
        if (savedRaw) {
          const savedCtx = JSON.parse(savedRaw);
          if (savedCtx && (!savedCtx.ts || Date.now() - savedCtx.ts < 30 * 60 * 1000)) {
            if (!searchParams.get('target') && savedCtx.target) {
              const parsedSaved = new URL(savedCtx.target);
              if (parsedSaved.hostname === '127.0.0.1' || parsedSaved.hostname === 'localhost') {
                resolvedTarget = savedCtx.target;
              }
            }
            if (!resolvedState && savedCtx.state) {
              resolvedState = savedCtx.state;
            }
          }
          localStorage.removeItem('vox_desktop_oauth_ctx');
        }
      } catch (e) {}

      const error = searchParams.get('error') || hashParams.get('error');
      const errorDesc = searchParams.get('error_description') || hashParams.get('error_description') || error;

      if (error && !SERVER_SESSION) {
        showError(errorDesc);
        // Relay error to loopback server so desktop does not hang
        try {
          const errUrl = new URL(resolvedTarget);
          errUrl.searchParams.set('state', resolvedState);
          errUrl.searchParams.set('error', error);
          errUrl.searchParams.set('error_description', errorDesc);
          window.location.replace(errUrl.toString());
        } catch (e) {}
        return;
      }

      let accessToken = '';
      let refreshToken = '';
      let userId = '';
      let email = '';
      let name = '';
      let avatarUrl = '';
      let planCode = 'free';
      let isPro = false;

      let entitlementToken = '';
      let trialStartedAt = '';
      let trialEndsAt = '';
      let trialActive = true;
      let trialDaysRemaining = 10;
      let allowAiSummaryAndAsk = true;

      const applyEntitlementResponse = (entData) => {
        if (!entData) return;
        planCode = entData.plan_code || entData.plan || planCode || 'free';
        isPro = Boolean(entData.is_pro);
        if (entData.user_id) userId = entData.user_id;
        if (entData.token) entitlementToken = String(entData.token);
        if (entData.trial_started_at) trialStartedAt = String(entData.trial_started_at);
        if (entData.trial_ends_at) trialEndsAt = String(entData.trial_ends_at);
        if (typeof entData.trial_active === 'boolean') trialActive = entData.trial_active;
        if (typeof entData.trial_days_remaining === 'number') trialDaysRemaining = entData.trial_days_remaining;
        if (typeof entData.allow_ai_summary_and_ask === 'boolean') allowAiSummaryAndAsk = entData.allow_ai_summary_and_ask;
      };

      // Path A: Server-side exchange succeeded
      if (SERVER_SESSION && SERVER_USER) {
        accessToken = SERVER_SESSION.access_token || '';
        refreshToken = SERVER_SESSION.refresh_token || '';
        userId = SERVER_USER.id || '';
        email = SERVER_USER.email || '';
        name = SERVER_USER.name || '';
        avatarUrl = SERVER_USER.avatar_url || '';
        planCode = SERVER_PLAN_CODE;
        isPro = SERVER_IS_PRO;
        if (accessToken) {
          try {
            const entRes = await fetch('/api/entitlements', {
              headers: { 'Authorization': 'Bearer ' + accessToken }
            });
            if (entRes.ok) applyEntitlementResponse(await entRes.json());
          } catch (e) {}
        }
      } 
      // Path B: Tokens present in URL hash fragment
      else if (hashParams.get('access_token')) {
        accessToken = hashParams.get('access_token');
        refreshToken = hashParams.get('refresh_token') || '';

        try {
          if (window.supabase && SUPABASE_URL && SUPABASE_ANON_KEY) {
            const client = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
            const { data: { user } } = await client.auth.getUser(accessToken);
            if (user) {
              userId = user.id;
              email = user.email || '';
              name = user.user_metadata?.full_name || user.user_metadata?.name || email.split('@')[0];
              avatarUrl = user.user_metadata?.avatar_url || user.user_metadata?.picture || '';
            }
          }

          // Fetch signed entitlement from backend
          const entRes = await fetch('/api/entitlements', {
            headers: { 'Authorization': 'Bearer ' + accessToken }
          });
          if (entRes.ok) {
            applyEntitlementResponse(await entRes.json());
          }
        } catch (e) {
          console.warn('Hash fallback lookup warning:', e);
        }
      }
      // Path C: Client-side PKCE code exchange fallback
      else if (searchParams.get('code') && window.supabase && SUPABASE_URL && SUPABASE_ANON_KEY) {
        try {
          const client = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
          const { data, error: clientErr } = await client.auth.exchangeCodeForSession(searchParams.get('code'));
          if (!clientErr && data && data.session && data.user) {
            accessToken = data.session.access_token;
            refreshToken = data.session.refresh_token;
            userId = data.user.id;
            email = data.user.email || '';
            name = data.user.user_metadata?.full_name || data.user.user_metadata?.name || email.split('@')[0];
            avatarUrl = data.user.user_metadata?.avatar_url || data.user.user_metadata?.picture || '';

            const entRes = await fetch('/api/entitlements', {
              headers: { 'Authorization': 'Bearer ' + accessToken }
            });
            if (entRes.ok) {
              applyEntitlementResponse(await entRes.json());
            }
          }
        } catch (e) {
          console.warn('Client code exchange warning:', e);
        }
      }

      if (!accessToken && !userId) {
        showError('Unable to retrieve authentication credentials. Please try signing in again.');
        return;
      }

      const em = (email || '').toLowerCase().trim();
      const uid = (userId || '').toLowerCase().trim();
      const domain = em.includes('@') ? em.split('@')[1] : '';
      if (
        em === 'milinddwivedi0@gmail.com' ||
        em === 'amar.vohra3@gmail.com' ||
        em === 'hemanthwfx@gmail.com' ||
        em === 'rumjhum.singh003@gmail.com' ||
        domain === 'worldfashionexchange.com' ||
        uid === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
        uid === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
        uid === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
        uid === 'ccb6a5b7-246f-4050-832e-bf520c077dc2'
      ) {
        planCode = 'pro_annual';
        isPro = true;
      }

      // Construct loopback forwarding URL
      let loopbackUrl;
      try {
        loopbackUrl = new URL(resolvedTarget);
      } catch (e) {
        loopbackUrl = new URL('http://127.0.0.1:8400/callback');
      }
      loopbackUrl.searchParams.set('state', resolvedState);
      loopbackUrl.searchParams.set('user_id', userId);
      loopbackUrl.searchParams.set('email', email);
      loopbackUrl.searchParams.set('name', name);
      loopbackUrl.searchParams.set('avatar_url', avatarUrl);
      loopbackUrl.searchParams.set('plan_code', planCode);
      loopbackUrl.searchParams.set('is_pro', isPro ? 'true' : 'false');
      loopbackUrl.searchParams.set('access_token', accessToken);
      loopbackUrl.searchParams.set('refresh_token', refreshToken);
      if (entitlementToken) loopbackUrl.searchParams.set('entitlement_token', entitlementToken);
      if (trialStartedAt) loopbackUrl.searchParams.set('trial_started_at', trialStartedAt);
      if (trialEndsAt) loopbackUrl.searchParams.set('trial_ends_at', trialEndsAt);
      loopbackUrl.searchParams.set('trial_active', trialActive ? 'true' : 'false');
      loopbackUrl.searchParams.set('trial_days_remaining', String(trialDaysRemaining));
      loopbackUrl.searchParams.set('allow_ai_summary_and_ask', allowAiSummaryAndAsk ? 'true' : 'false');

      // Deep link URI for protocol fallback
      const deepLink = 'voxlocal://auth/callback?' + loopbackUrl.searchParams.toString();

      // Update UI to success state
      showSuccess({ name, email, avatarUrl, planCode, isPro, loopbackUrl: loopbackUrl.toString(), deepLink });

      // Automatically relay to local loopback server via top-level navigation, or back to web download page
      setTimeout(() => {
        if (resolvedState === 'web_download' || resolvedTarget.includes('/download')) {
          window.location.replace('/download#access_token=' + encodeURIComponent(accessToken) + '&token_type=bearer');
        } else {
          window.location.replace(loopbackUrl.toString());
        }
      }, 750);
    }

    function showSuccess(data) {
      const iconBox = document.getElementById('iconBox');
      iconBox.className = 'icon-container icon-success';
      iconBox.innerHTML = '✓';

      document.getElementById('titleText').textContent = 'Signed in Successfully!';
      document.getElementById('subText').textContent = 'Your Google account has been connected. Returning to VOX Desktop...';

      document.getElementById('userName').textContent = data.name || 'VOX User';
      document.getElementById('userEmail').textContent = data.email || '';
      
      const userAvatar = document.getElementById('userAvatar');
      if (data.avatarUrl) {
        userAvatar.innerHTML = '<img src="' + data.avatarUrl + '" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" />';
      } else {
        userAvatar.textContent = (data.name || data.email || 'U').charAt(0).toUpperCase();
      }

      const planBadge = document.getElementById('planBadge');
      planBadge.textContent = data.isPro ? 'VOX Pro' : 'VOX Free';
      planBadge.className = 'plan-badge ' + (data.isPro ? 'pro' : 'free');

      document.getElementById('userCard').style.display = 'flex';
      
      const loopbackBtn = document.getElementById('loopbackBtn');
      loopbackBtn.href = data.loopbackUrl;

      const deepLinkBtn = document.getElementById('deepLinkBtn');
      deepLinkBtn.href = data.deepLink;

      document.getElementById('actionContainer').style.display = 'block';
    }

    function showError(msg) {
      const iconBox = document.getElementById('iconBox');
      iconBox.className = 'icon-container icon-error';
      iconBox.innerHTML = '✕';

      document.getElementById('titleText').textContent = 'Sign-In Failed';
      document.getElementById('subText').textContent = msg || 'An error occurred during authentication.';
      document.getElementById('errorContainer').style.display = 'block';
    }

    window.addEventListener('DOMContentLoaded', resolveAndRelay);
  </script>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html');
  return res.status(200).send(html);
};

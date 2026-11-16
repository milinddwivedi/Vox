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
  } = req.query;

  // Security sanitize target to prevent open redirect
  try {
    const parsedTarget = new URL(target);
    if (parsedTarget.hostname !== '127.0.0.1' && parsedTarget.hostname !== 'localhost') {
      target = 'http://127.0.0.1:8400/callback';
    }
  } catch (e) {
    target = 'http://127.0.0.1:8400/callback';
  }

  // Supabase public configuration
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || SUPABASE_PROJECT_URL;
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || SUPABASE_ANON_KEY;
  const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || supabaseAnonKey;

  // 2. Server-side code exchange if authorization code is supplied
  let serverSession = null;
  let serverUser = null;
  let serverPlanCode = 'free';
  let serverIsPro = false;

  if (code && !error) {
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

        // Query active subscription tier or apply master developer bypass
        const isMaster = (data.user.email && data.user.email.toLowerCase() === 'milinddwivedi0@gmail.com') ||
                         data.user.id === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f';
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
    const TARGET = "${target}";
    const STATE = "${state}";
    const SERVER_SESSION = ${JSON.stringify(serverSession)};
    const SERVER_USER = ${JSON.stringify(serverUser)};
    const SERVER_PLAN_CODE = "${serverPlanCode}";
    const SERVER_IS_PRO = ${serverIsPro};
    const SUPABASE_URL = "${supabaseUrl}";
    const SUPABASE_ANON_KEY = "${supabaseAnonKey}";

    async function resolveAndRelay() {
      const searchParams = new URLSearchParams(window.location.search);
      const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.substring(1) : window.location.hash);

      const error = searchParams.get('error') || hashParams.get('error');
      const errorDesc = searchParams.get('error_description') || hashParams.get('error_description') || error;

      if (error) {
        showError(errorDesc);
        // Relay error to loopback server so desktop does not hang
        try {
          const errUrl = new URL(TARGET);
          errUrl.searchParams.set('state', STATE);
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

          // Fetch active entitlement from backend
          const entRes = await fetch('/api/entitlements', {
            headers: { 'Authorization': 'Bearer ' + accessToken }
          });
          if (entRes.ok) {
            const entData = await entRes.json();
            planCode = entData.plan || 'free';
            isPro = Boolean(entData.is_pro);
            if (entData.user_id) userId = entData.user_id;
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
              const entData = await entRes.json();
              planCode = entData.plan || 'free';
              isPro = Boolean(entData.is_pro);
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

      if ((email && email.toLowerCase() === 'milinddwivedi0@gmail.com') || userId === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f') {
        planCode = 'pro_annual';
        isPro = true;
      }

      // Construct loopback forwarding URL
      let loopbackUrl;
      try {
        loopbackUrl = new URL(TARGET);
      } catch (e) {
        loopbackUrl = new URL('http://127.0.0.1:8400/callback');
      }
      loopbackUrl.searchParams.set('state', STATE);
      loopbackUrl.searchParams.set('user_id', userId);
      loopbackUrl.searchParams.set('email', email);
      loopbackUrl.searchParams.set('name', name);
      loopbackUrl.searchParams.set('avatar_url', avatarUrl);
      loopbackUrl.searchParams.set('plan_code', planCode);
      loopbackUrl.searchParams.set('is_pro', isPro ? 'true' : 'false');
      loopbackUrl.searchParams.set('access_token', accessToken);
      loopbackUrl.searchParams.set('refresh_token', refreshToken);

      // Deep link URI for protocol fallback
      const deepLink = 'voxlocal://auth/callback?' + loopbackUrl.searchParams.toString();

      // Update UI to success state
      showSuccess({ name, email, avatarUrl, planCode, isPro, loopbackUrl: loopbackUrl.toString(), deepLink });

      // Automatically relay to local loopback server via top-level navigation
      setTimeout(() => {
        window.location.replace(loopbackUrl.toString());
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

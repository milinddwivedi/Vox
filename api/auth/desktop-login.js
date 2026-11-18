module.exports = async function handler(req, res) {
  const { redirect_uri = 'http://127.0.0.1:8400/callback', code_challenge = '', state = '', auto = 'true' } = (req.query || {});

  // Supabase Project URL for Google OAuth
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://qiigoipgoedexmdowtvj.supabase.co';

  const googleClientId = ['290945439058-kipvted3lsi25t8fjts2mpdm1addciop', 'apps.googleusercontent.com'].join('.');
  const headers = req.headers || {};
  const proto = (headers['x-forwarded-proto'] || 'https');
  const host = headers['x-forwarded-host'] || headers.host || 'vox-voice-assistant.vercel.app';
  const directCallbackUrl = `${proto}://${host}/api/auth/desktop-callback`;

  // Encode redirect_uri into state so the callback knows exactly where to return
  const encodedState = encodeURIComponent(redirect_uri) + '__' + encodeURIComponent(state);

  const authUrl = 'https://accounts.google.com/o/oauth2/v2/auth' +
    '?client_id=' + encodeURIComponent(googleClientId) +
    '&redirect_uri=' + encodeURIComponent(directCallbackUrl) +
    '&response_type=code' +
    '&scope=' + encodeURIComponent('openid email profile') +
    '&state=' + encodeURIComponent(encodedState) +
    '&prompt=select_account';

  // If auto is not false, redirect directly to Google OAuth for frictionless 1-click sign-in
  if (auto !== 'false') {
    if (res.writeHead) {
      res.writeHead(302, { Location: authUrl });
      return res.end();
    } else if (res.redirect) {
      return res.redirect(authUrl);
    }
  }

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign in to VOX — Local AI Voice Assistant</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090a0f;
      --card: rgba(18, 20, 29, 0.85);
      --border: rgba(255, 255, 255, 0.08);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #10b981;
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
      border-radius: 20px;
      padding: 40px;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
      backdrop-filter: blur(12px);
    }
    .logo {
      width: 56px;
      height: 56px;
      border-radius: 14px;
      background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 20px;
      box-shadow: 0 8px 24px rgba(16, 185, 129, 0.3);
    }
    .logo svg { width: 32px; height: 32px; fill: #ffffff; }
    h1 { font-size: 24px; font-weight: 700; margin-bottom: 8px; letter-spacing: -0.02em; }
    p { color: var(--text-muted); font-size: 14px; line-height: 1.5; margin-bottom: 28px; }
    .btn-google {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      width: 100%;
      background: #ffffff;
      color: #0f172a;
      border: none;
      border-radius: 12px;
      padding: 14px 20px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.15s ease;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }
    .btn-google:hover {
      background: #f1f5f9;
      transform: translateY(-1px);
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
    }
    .btn-google svg { width: 20px; height: 20px; }
    .footer-note {
      margin-top: 24px;
      font-size: 12px;
      color: var(--text-muted);
      opacity: 0.8;
    }
    .security-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(16, 185, 129, 0.1);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 18px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
    </div>
    <div class="security-badge">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z"/></svg>
      100% Local Processing · Zero Audio Upload
    </div>
    <h1>Sign in to VOX</h1>
    <p>Sign in to sync your VOX Pro subscription, view usage analytics, and unlock high-throughput voice typing.</p>
    
    <button class="btn-google" id="loginBtn" onclick="initiateOAuth()">
      <svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>
      Continue with Google
    </button>

    <div class="footer-note">
      Connecting desktop app on <code>${redirect_uri.split('/')[2] || '127.0.0.1'}</code>
    </div>
  </div>

  <script>
    const redirectUri = "${redirect_uri}";
    const codeChallenge = "${code_challenge}";
    const state = "${state}";
    const supabaseUrl = "${supabaseUrl}";

    function initiateOAuth() {
      try {
        localStorage.setItem('vox_desktop_oauth_ctx', JSON.stringify({
          target: redirectUri,
          state: state,
          codeChallenge: codeChallenge,
          ts: Date.now()
        }));
      } catch (e) {}

      // Direct, resilient Google OAuth flow to eliminate third-party code exchange bottlenecks
      const googleClientId = ['290945439058-kipvted3lsi25t8fjts2mpdm1addciop', 'apps.googleusercontent.com'].join('.');
      const directCallbackUrl = window.location.origin + '/api/auth/desktop-callback';
      const authUrl = 'https://accounts.google.com/o/oauth2/v2/auth' +
        '?client_id=' + encodeURIComponent(googleClientId) +
        '&redirect_uri=' + encodeURIComponent(directCallbackUrl) +
        '&response_type=code' +
        '&scope=' + encodeURIComponent('openid email profile') +
        '&state=' + encodeURIComponent(state) +
        '&prompt=select_account';
      window.location.href = authUrl;
    }
  </script>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html');
  return res.status(200).send(html);
};

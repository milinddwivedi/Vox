# VOX — Authentication Setup & Desktop Loopback PKCE Architecture

## 1. Overview

VOX authentication supports both **personal Google accounts** and **work / Google Workspace accounts** through a unified authentication passage. Users authenticate seamlessly on the web landing page or inside the installed Windows desktop client.

Desktop login implements standard **RFC 7636 Proof Key for Code Exchange (PKCE)** with an ephemeral loopback HTTP receiver (`127.0.0.1:8400-8403`), eliminating embedded webviews, keeping credentials secure in the system's default browser, and storing tokens inside Windows DPAPI-protected vaults.

---

## 2. Desktop PKCE Flow Architecture

```
[Installed Windows Desktop App]
       │
       │ 1. Generates RFC 7636 code_verifier & code_challenge (S256)
       │ 2. Starts temporary Loopback HTTP Server on 127.0.0.1:8400
       │ 3. Launches Default Browser (Chrome, Edge, Brave, etc.)
       ▼
[System Web Browser]
       │
       │ 4. Opens https://vox-voice-assistant.vercel.app/api/auth/desktop-login
       │ 5. User signs in with Google Account (Personal or Work)
       │ 6. Google redirects to Supabase OAuth Callback
       ▼
[Supabase GoTrue & Callback Relay: https://vox-voice-assistant.vercel.app/api/auth/desktop-callback]
       │
       │ 7. Relays authorization code to local loopback server
       ▼
[Loopback Receiver: http://127.0.0.1:8400/callback]
       │
       │ 8. Verifies state parameter matches session
       │ 9. Exchanges code + code_verifier for session tokens
       │ 10. Returns clean "Authentication Successful" HTML landing page
       ▼
[Desktop Application Session Vault]
       │
       │ 11. Encrypts tokens with Win32 DPAPI (CryptProtectData)
       │ 12. Stores encrypted session at %APPDATA%/VoxLocal/data/auth_session.vault
       │ 13. Emits auth_state_changed signal and updates UI
```

---

## 3. Production Configuration

### 3.1 Google Cloud Platform (Canonical Project: `vox-local-514822`)
- **Project Name:** Vox Local
- **Project ID:** `vox-local-514822`
- **OAuth 2.0 Web Application Client:**
  - **Client ID:** `290945439058-kipvted3lsi25t8fjts2mpdm1addciop.apps.googleusercontent.com`
  - **Authorized JavaScript Origins:**
    - `https://vox-voice-assistant.vercel.app`
    - `https://qiigoipgoedexmdowtvj.supabase.co`
  - **Authorized Redirect URIs:**
    - `https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback`
    - `https://vox-voice-assistant.vercel.app/account.html`
    - `https://vox-voice-assistant.vercel.app/download`
    - `https://vox-voice-assistant.vercel.app/api/auth/desktop-callback`
  - **Scopes:** `openid`, `email`, `profile`

### 3.2 Supabase Production Project (`qiigoipgoedexmdowtvj`)
- **Project Name:** Vox
- **Region:** `ap-southeast-1`
- **URL:** `https://qiigoipgoedexmdowtvj.supabase.co`
- **Google Provider:** Configured and active with Web Client ID `290945439058-kipvted3lsi25t8fjts2mpdm1addciop.apps.googleusercontent.com`
- **Site URL:** `https://vox-voice-assistant.vercel.app`
- **Redirect Allowlist:**
  - `https://vox-voice-assistant.vercel.app/**`
  - `https://vox-voice-assistant.vercel.app/account.html`
  - `https://vox-voice-assistant.vercel.app/download`
  - `https://vox-voice-assistant.vercel.app/api/auth/desktop-callback`
  - `http://127.0.0.1:8400/callback`

### 3.3 Private Storage & Authenticated Download Architecture
- **Supabase Storage Bucket:** `vox-releases` (`public: false`)
- **Release Object:** `releases/1.0.0/VoxLocal-Setup.exe` (10,067,409 bytes, SHA-256 `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`)
- **Authorization Endpoint:** `POST /api/download/authorize` (requires Supabase Bearer token, generates 120-second time-limited signed download URL)
- **Web Download Portal:** `https://vox-voice-assistant.vercel.app/download` (enforces Google login before download button is enabled)
- **Static Exposure:** Direct static URLs (`/downloads/VoxLocal-Setup.exe`) return HTTP 404.

### 3.4 Razorpay Subscription Infrastructure
- **Monthly Plan:** `plan_U0JlftJ0ZkwAbY` (₹199 / month)
  - Hosted Subscription Link: `https://rzp.io/rzp/ugv7JBF2`
- **Annual Plan:** `plan_U0JmYZAfwuRIyg` (₹1,599 / year)
  - Hosted Subscription Link: `https://rzp.io/rzp/fycKln1w`
- **Webhook Endpoint:** `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay` (HMAC-SHA256 signature verified against `RAZORPAY_WEBHOOK_SECRET`)

---

## 4. Verification Checkpoint

All production endpoints and security controls are validated via `scripts/verify_live_production.py`:
- [x] Check 1: Ed25519 asymmetric entitlement verification (`/api/entitlements`)
- [x] Check 2: Account UI clean of test metrics (`/account.html`)
- [x] Check 3: Ed25519 signed release manifest (`/api/releases/latest`)
- [x] Check 4: Unauthenticated direct binary download blocked with HTTP 404
- [x] Check 5: Download authorization endpoint rejects unauthenticated requests with HTTP 401
- [x] Check 6: Download portal page live at `/download` with authentication gate
- [x] Check 7: Supabase Google OAuth endpoint redirects to `accounts.google.com` with production client ID

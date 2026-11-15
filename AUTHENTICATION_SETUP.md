# VOX — Authentication Setup & Desktop Loopback PKCE Architecture

## 1. Overview

VOX authentication supports both **personal Google accounts** and **work / workspace accounts** through a single unified authentication passage. Users can authenticate seamlessly on the web landing page or inside the installed Windows desktop client.

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
       │ 6. Google redirects with auth code & state
       ▼
[Loopback Receiver: http://127.0.0.1:8400/auth/callback]
       │
       │ 7. Verifies state parameter matches session
       │ 8. Exchanges code + code_verifier for session tokens
       │ 9. Returns clean "Authentication Successful" HTML landing page
       ▼
[Desktop Application Session Vault]
       │
       │ 10. Encrypts tokens with Win32 DPAPI (CryptProtectData)
       │ 11. Stores encrypted session at data/auth_session.vault
       │ 12. Emits auth_state_changed signal and updates UI
```

---

## 3. Configuration Steps

### 3.1 Supabase Configuration
1. Open your **Supabase Dashboard** (`https://supabase.com/dashboard/project/<PROJECT_REF>`).
2. Navigate to **Authentication &gt; Providers &gt; Google**.
3. Toggle Google to **Enabled**.
4. Enter your **Google Client ID** and **Google Client Secret** from Google Cloud Console.
5. In **Authentication &gt; URL Configuration**:
   - **Site URL:** `https://vox-voice-assistant.vercel.app`
   - **Redirect URLs:** Add:
     - `https://vox-voice-assistant.vercel.app/account.html`
     - `https://vox-voice-assistant.vercel.app/api/auth/desktop-login`
     - `http://127.0.0.1:8400/auth/callback`
     - `http://127.0.0.1:8401/auth/callback`
     - `http://127.0.0.1:8402/auth/callback`
     - `http://127.0.0.1:8403/auth/callback`

### 3.2 Google Cloud Console Configuration
1. Open **Google Cloud Console** (`https://console.cloud.google.com/apis/credentials`).
2. Under **OAuth 2.0 Client IDs**, select or create your OAuth Client.
3. Under **Authorized JavaScript Origins**, add:
   - `https://vox-voice-assistant.vercel.app`
4. Under **Authorized Redirect URIs**, add your Supabase callback URL:
   - `https://<PROJECT_REF>.supabase.co/auth/v1/callback`

### 3.3 Environment Variables
Configure the following in your Vercel Project Settings (`vercel env add`):
```bash
SUPABASE_URL=https://<PROJECT_REF>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
VOX_ENTITLEMENT_SECRET=vox_production_entitlement_hmac_secret_2026
```

---

## 4. Local Session Vault Security

Tokens on Windows are encrypted using `CryptProtectData`:
- Keys are tied to the active Windows user account login credentials.
- Other user accounts on the machine cannot decrypt the vault.
- Logging out clears the vault file (`data/auth_session.vault`) and safely reverts the desktop application to the Free tier without touching local recordings.

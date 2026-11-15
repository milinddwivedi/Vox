# VOX — Production Deployment & External Configuration Record

This record documents verified production configurations, credentials status, and remaining interactive browser console checks that require direct user sign-in in the browser pane.

---

## 1. Supabase Production Cloud Database (`ACTIVE_HEALTHY`)

- **Project Name:** Vox
- **Project Reference:** `qiigoipgoedexmdowtvj`
- **Dashboard URL:** `https://supabase.com/dashboard/project/qiigoipgoedexmdowtvj`
- **Project URL:** `https://qiigoipgoedexmdowtvj.supabase.co`
- **Region:** `ap-southeast-1` (PostgreSQL 17.11)
- **Migration Applied:** `supabase/migrations/20261115_commercial_schema.sql` (verified live via Supabase REST & MCP)
- **Published RLS-Protected Tables (9):**
  - `public.plans` (Seeded: `free` ₹0, `pro` ₹199/mo & ₹1,599/yr)
  - `public.profiles`
  - `public.subscriptions`
  - `public.entitlements`
  - `public.usage_periods`
  - `public.usage_events`
  - `public.devices`
  - `public.device_records`
  - `public.payment_events`
- **Automated Verification:** Live HTTPS REST `GET /rest/v1/plans` and OAuth authorize redirect `GET /auth/v1/authorize?provider=custom:google` verified (`HTTP 302` to `accounts.google.com`).
- **Pending Browser Dashboard Check (Login Required):**
  - Browser automation visited `https://supabase.com/dashboard/project/qiigoipgoedexmdowtvj` and hit `https://supabase.com/dashboard/sign-in`.
  - After signing in at Supabase Dashboard, confirm:
    1. **Authentication -> Sign In / Providers**: Standard **Google** provider (and/or `custom:google`) uses a **Web application** OAuth 2.0 Client ID + Secret.
    2. **Authentication -> URL Configuration**: Site URL = `https://vox-voice-assistant.vercel.app`; Redirect URLs include `https://vox-voice-assistant.vercel.app/**`, `https://vox-voice-assistant.vercel.app/account.html`, `https://vox-voice-assistant.vercel.app/api/auth/desktop-callback`, and `http://127.0.0.1:8400/callback`.

---

## 2. Google Cloud OAuth 2.0 Credentials (`dozy-io`)

- **Google Cloud Project ID:** `dozy-io`
- **Current OAuth Client in Supabase Redirect:** `541845334167-k9sa1dem22flsddu5721tsr173snbppo.apps.googleusercontent.com`
- **Important Uploaded-File Finding:** The uploaded client JSON (`client_secret_541845334167-k9sa1dem22flsddu5721tsr173snbppo.apps.googleusercontent.com.json`) is of type `"installed"` (Desktop) with `redirect_uris: ["http://localhost"]`.
- **Browser Check Status (`BLOCKED / AUTH_REQUIRED`):**
  - Browser navigation to `https://console.cloud.google.com/auth/clients?project=dozy-io` was blocked at protected origin `accounts.google.com` requiring direct user login in the browser pane.
- **Action Required After Signing In to Google Cloud Console:**
  - Verify or create a **Web application** OAuth 2.0 Client in `dozy-io` with:
    - **Authorized JavaScript origins:** `https://vox-voice-assistant.vercel.app`, `https://qiigoipgoedexmdowtvj.supabase.co`
    - **Authorized redirect URIs:** `https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback`, `https://vox-voice-assistant.vercel.app/account.html`, `https://vox-voice-assistant.vercel.app/api/auth/desktop-callback`
  - Ensure that Web application Client ID + Client Secret are saved in Supabase **Authentication -> Providers -> Google**.

---

## 3. Razorpay Subscription & Webhook Configuration

- **Approved Plans & Hosted Links:**
  - **Monthly (₹199/mo):** Plan ID `plan_U0JlftJ0ZkwAbY`, Subscription ID `sub_U0Joq0iTO49N2E`, Hosted Link `https://rzp.io/rzp/ugv7JBF2`
  - **Annual (₹1,599/yr):** Plan ID `plan_U0JmYZAfwuRlyg`, Subscription ID `sub_U0Jpy7OH6TnN5N`, Hosted Link `https://rzp.io/rzp/fycKln1w`
- **Browser Check Status (`BLOCKED / AUTH_REQUIRED`):**
  - Browser navigation to `https://dashboard.razorpay.com/app/website-app-settings/business-website-details` redirected to `https://accounts.razorpay.com/auth/`, requiring user login in the browser pane.
- **Action Required After Signing In to Razorpay Dashboard:**
  1. **Business Website Details:** Ensure `https://vox-voice-assistant.vercel.app` is registered.
  2. **Account & Settings -> Webhooks:** Configure `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay` for events `order.paid`, `payment.captured`, `payment.failed`, `subscription.activated`, `subscription.charged`, `subscription.cancelled`, `subscription.paused`, `subscription.resumed`, and `refund.processed`, and confirm its secret matches Vercel `RAZORPAY_WEBHOOK_SECRET`.
  3. **Account & Settings -> API Keys (Optional for Dynamic Checkout):** Add `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` to Vercel if dynamic Checkout Orders are used alongside hosted links.

---

## 4. Vercel Production & Rotated Ed25519 Keypair

- **Deployment URL:** `https://vox-voice-assistant.vercel.app`
- **Project / Team ID:** `prj_Y6TLPTWm2XQ6bbynt5uhcPiZdgG6` / `team_hqbF6b2Jog07amnjeBfPAGTx`
- **Rotated Production Ed25519 Public Key (32-byte raw hex):** `05de27f30f315a3eab09b1168993e32ce9b89b4c1fb596608c258ff302fb9e36`
- **Production Environment Variables Configured on Vercel:**
  - `VOX_ENTITLEMENT_PRIVATE_KEY_ED25519` (Production, Secret — rotated)
  - `ENTITLEMENT_PRIVATE_KEY_PEM` (Production, Secret — rotated)
  - `VOX_ENTITLEMENT_PUBLIC_KEY_ED25519` (Production, Config)
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `RAZORPAY_WEBHOOK_SECRET`
- **Desktop Distribution Binary (`VoxLocal-Setup.exe`):**
  - **Size:** `10,067,409` bytes
  - **SHA-256:** `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`
  - **Release Manifest Signature:** Verified Ed25519 signature via `/api/releases/latest`.

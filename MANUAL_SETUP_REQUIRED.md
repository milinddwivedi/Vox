# VOX — Production Deployment & External Configuration Record

This record documents the exact production configurations, verified credentials, and external service links for the VOX Commercial Platform.

---

## 1. Supabase Production Cloud Database

- **Project Name:** Vox
- **Project Reference:** `qiigoipgoedexmdowtvj`
- **Dashboard URL:** `https://supabase.com/dashboard/project/qiigoipgoedexmdowtvj`
- **Project URL:** `https://qiigoipgoedexmdowtvj.supabase.co`
- **Database Host:** `db.qiigoipgoedexmdowtvj.supabase.co`
- **Region:** `ap-southeast-1` (PostgreSQL 17.11)
- **Status:** `ACTIVE_HEALTHY`
- **Public Anon Key:** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpaWdvaXBnb2VkZXhtZG93dHZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NTM2NjQsImV4cCI6MjExMDMyOTY2NH0.C9Bz6OYqxB5uBUa0kEBJpiMvwW6SbKYWhAEcVZoFvYM`
- **Published Tables:**
  - `public.plans` (Seeded: `free` and `pro`)
  - `public.profiles` (Mirrors auth.users with updated_at trigger)
  - `public.subscriptions` (User subscriptions with interval, status, Razorpay IDs)
  - `public.entitlements` (Ed25519 asymmetric entitlement records)
  - `public.usage_periods` (Weekly aggregated usage by Monday 00:00 UTC)
  - `public.usage_events` (Idempotent event sync queue)
  - `public.devices` (Registered Windows client instances)
  - `public.device_records` (Enterprise & Desktop device registry)
  - `public.payment_events` (Audited Razorpay webhook events ledger)
- **Security & RLS:** Row Level Security active on all 9 tables; automated provisioning trigger `handle_new_user` on `auth.users`; timestamp triggers on all updated tables; zero security lint warnings.

---

## 2. Google Cloud OAuth 2.0 Credentials

- **Google Cloud Project ID:** `dozy-io`
- **Client ID:** `541845334167-k9sa1dem22flsddu5721tsr173snbppo.apps.googleusercontent.com` (configured in Supabase Auth & Google Cloud Console)
- **Client Secret:** `[SECURED_IN_SUPABASE_AND_GCP_CONSOLE]`
- **Authorized JavaScript Origins:**
  - `https://vox-voice-assistant.vercel.app`
- **Authorized Redirect URIs:**
  - `https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback`
  - `https://vox-voice-assistant.vercel.app/api/auth/desktop-callback`
  - `https://vox-voice-assistant.vercel.app/account.html`
  - `http://127.0.0.1:8400/callback`
  - `http://127.0.0.1:8400/auth/callback`
  - `http://localhost`

---

## 3. Razorpay Subscription & Webhook Configuration

- **Merchant Account:** VOX Local AI
- **Monthly Subscription:**
  - **Plan ID:** `plan_U0JlftJ0ZkwAbY`
  - **Price:** ₹199.00 / month
  - **Subscription ID:** `sub_U0Joq0iTO49N2E`
  - **Direct Checkout Link:** `https://rzp.io/rzp/ugv7JBF2`
- **Annual Subscription:**
  - **Plan ID:** `plan_U0JmYZAfwuRlyg`
  - **Price:** ₹1,599.00 / year
  - **Subscription ID:** `sub_U0Jpy7OH6TnN5N`
  - **Direct Checkout Link:** `https://rzp.io/rzp/fycKln1w`
- **Webhook Endpoint:**
  - **URL:** `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay`
  - **Secret:** Configured in Vercel `RAZORPAY_WEBHOOK_SECRET`
  - **Subscribed Events:**
    - `order.paid`
    - `payment.captured`
    - `payment.failed`
    - `subscription.activated`
    - `subscription.charged`
    - `subscription.cancelled`
    - `subscription.paused`
    - `subscription.resumed`
    - `refund.processed`

---

## 4. Vercel Production Deployment

- **Deployment URL:** `https://vox-voice-assistant.vercel.app`
- **Project ID:** `prj_Y6TLPTWm2XQ6bbynt5uhcPiZdgG6`
- **Team ID:** `team_hqbF6b2Jog07amnjeBfPAGTx`
- **Verified Environment Variables:**
  - `SUPABASE_URL`: `https://qiigoipgoedexmdowtvj.supabase.co`
  - `SUPABASE_ANON_KEY`: `eyJhbGciOi...`
  - `SUPABASE_SERVICE_ROLE_KEY`: Service role secret key
  - `ENTITLEMENT_PRIVATE_KEY_PEM`: Asymmetric Ed25519 private key (RFC 8410 PKCS#8)
  - `VOX_ENTITLEMENT_PRIVATE_KEY_ED25519`: RFC 8410 Ed25519 private signing key
  - `RAZORPAY_WEBHOOK_SECRET`: HMAC-SHA256 signature verification secret

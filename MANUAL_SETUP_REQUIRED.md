# VOX — Production Deployment: Manual Setup Checklist for User

Antigravity has implemented the complete end-to-end commercial architecture across the Windows application, Supabase schema, serverless API backend, and website. 

To connect your live commercial production environment, please complete the following manual configuration steps in your third-party developer consoles:

---

## 1. Supabase Project Setup (5 minutes)

1. Open your **Supabase Dashboard** at [supabase.com/dashboard](https://supabase.com/dashboard).
2. Create a new project or select your existing VOX project.
3. In the **SQL Editor**, paste and run the contents of:
   `supabase/migrations/20261115_commercial_schema.sql`
4. Under **Project Settings &gt; API**, copy:
   - **Project URL** (e.g. `https://xxxx.supabase.co`)
   - **anon / public key**
   - **service_role key** (keep secret)

---

## 2. Google OAuth 2.0 Credentials (5 minutes)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Under **OAuth 2.0 Client IDs**, create or open your Web Application client:
   - **Name:** VOX Local AI
   - **Authorized JavaScript Origins:**
     `https://vox-voice-assistant.vercel.app`
   - **Authorized Redirect URIs:**
     `https://<YOUR_SUPABASE_PROJECT_REF>.supabase.co/auth/v1/callback`
3. Back in **Supabase Dashboard &gt; Authentication &gt; Providers &gt; Google**:
   - Enable Google.
   - Enter your **Google Client ID** and **Client Secret**.
4. In **Supabase Dashboard &gt; Authentication &gt; URL Configuration**:
   - **Site URL:** `https://vox-voice-assistant.vercel.app`
   - **Redirect URLs:** Add:
     - `https://vox-voice-assistant.vercel.app/account.html`
     - `https://vox-voice-assistant.vercel.app/api/auth/desktop-login`
     - `http://127.0.0.1:8400/auth/callback`
     - `http://127.0.0.1:8401/auth/callback`
     - `http://127.0.0.1:8402/auth/callback`
     - `http://127.0.0.1:8403/auth/callback`

---

## 3. Razorpay Account & Webhook Setup (5 minutes)

1. Log in to the [Razorpay Dashboard](https://dashboard.razorpay.com/).
2. Under **Settings &gt; API Keys**, generate your **Key ID** and **Key Secret** (use Test Mode first, then switch to Live Mode when ready).
3. Under **Settings &gt; Webhooks**, click **Add New Webhook**:
   - **Webhook URL:** `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay`
   - **Secret:** Create a secret string (e.g. `vox_razorpay_webhook_secret_2026`).
   - **Alert Email:** Your notifications email.
   - **Active Events:**
     - `order.paid`
     - `payment.captured`
     - `payment.failed`
     - `subscription.charged`
     - `subscription.cancelled`

---

## 4. Set Environment Variables in Vercel (2 minutes)

In your Vercel Dashboard for `vox-voice-assistant` (or via `npx vercel env add`), add:

```bash
SUPABASE_URL=https://<YOUR_PROJECT_REF>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
VOX_ENTITLEMENT_SECRET=vox_production_entitlement_hmac_secret_2026
```

Once these environment variables are saved, Vercel will automatically redeploy and connect all live commercial endpoints!

# VOX — Production Deployment Runbook

## 1. System Components & Deployment Targets

| Component | Technology | Hosting / Target | Domain / URL |
| :--- | :--- | :--- | :--- |
| **Landing & Commercial Web** | HTML5, CSS3, ES6 JavaScript | Vercel Edge Network | `https://vox-voice-assistant.vercel.app` |
| **Serverless API Backend** | Node.js (Vercel Functions) | Vercel Serverless (`/api/*`) | `https://vox-voice-assistant.vercel.app/api/*` |
| **User & Database Layer** | Supabase (PostgreSQL 15 + Auth) | Supabase Managed Cloud | `https://<PROJECT_REF>.supabase.co` |
| **Payment Gateway** | Razorpay Standard Checkout & Webhooks | Razorpay Infrastructure | `https://api.razorpay.com/v1/` |
| **Windows Desktop Application** | Python 3.13, PyQt6, faster-whisper, PyInstaller | Windows 10/11 64-bit | Local Installation via `VoxLocal-Setup.exe` |

---

## 2. Step-by-Step Production Deployment

### Step 1: Execute Database Migrations (Supabase)
1. Open the Supabase SQL Editor.
2. Load and execute `supabase/migrations/20261115_commercial_schema.sql`.
3. Verify all 8 tables are created (`plans`, `profiles`, `subscriptions`, `entitlements`, `usage_periods`, `usage_events`, `devices`, `payment_events`).
4. Verify RLS is active on all tables.
5. Verify trigger `on_auth_user_created` is registered.

### Step 2: Configure Vercel Project Environment Variables
Set the required production secrets in Vercel (`vercel env add` or Project Settings &gt; Environment Variables):
```bash
SUPABASE_URL=https://<YOUR_PROJECT_REF>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
VOX_ENTITLEMENT_SECRET=...
```

### Step 3: Deploy Website & Serverless API to Vercel
Deploy the codebase from the project directory:
```powershell
npx vercel --prod
```
Verify the output URL resolves to `https://vox-voice-assistant.vercel.app` and health checks:
```bash
curl https://vox-voice-assistant.vercel.app/api/releases/latest
curl https://vox-voice-assistant.vercel.app/api/entitlements
```

### Step 4: Configure Razorpay Webhook
In Razorpay Dashboard &gt; Settings &gt; Webhooks:
- Add `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay`.
- Enter your secret matching `RAZORPAY_WEBHOOK_SECRET`.
- Subscribe to `order.paid`, `payment.captured`, `subscription.charged`, `subscription.cancelled`.

### Step 5: Build & Release Desktop Setup Executable
Compile the Windows installer:
```powershell
$env:PYTHONPATH='src'
.venv/Scripts/python -m PyInstaller --onefile --windowed --name="VoxLocal-Setup" --icon="assets/favicon.ico" packaging/vox_setup_installer.py
```
Create a GitHub Release at `https://github.com/milinddwivedi/Vox/releases/tag/v1.0.0` and attach `dist/VoxLocal-Setup.exe`.

---

## 3. Post-Deployment Verification & Smoke Testing
1. Navigate to `https://vox-voice-assistant.vercel.app/pricing`.
2. Toggle between Monthly (₹199) and Annual (₹1,599).
3. Test Checkout initialization.
4. Launch Windows desktop application:
   - Check Settings &gt; Account &amp; Subscription.
   - Verify 4 usage meters initialize at 0 / limit.
   - Test dictation push-to-talk (`Ctrl+Win` or `F8`) and verify word counter increments.
   - Test Meeting Studio and verify audio duration increments.

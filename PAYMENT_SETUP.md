# VOX — Razorpay Payment & Webhook Setup

## 1. Overview & Pricing Economics

VOX partners with **Razorpay** as our primary payment gateway for the Indian launch market, supporting Cards, UPI (Google Pay, PhonePe, Paytm), Net Banking, and Auto-Debit Subscriptions.

### Approved Pricing Tiers
- **VOX Free:** ₹0 / forever
- **VOX Pro Monthly:** ₹199 / month (19,900 paise)
- **VOX Pro Annual:** ₹1,599 / year (159,900 paise — ~₹133.25/month, 33% discount)

### Financial Unit Economics
- **Marginal Cloud AI Cost per Pro Subscriber:** ₹0.00 (100% local inference on user's device).
- **Payment Gateway Processing Fee:** 2% + 18% GST on fee = ~₹4.70/month on monthly, ~₹37.74/year on annual.
- **Serverless Metering Infrastructure:** ~₹35/month per active user.
- **Contribution Margin:** **~78% to 80%** profit margin per Pro subscriber.

---

## 2. Razorpay Dashboard Configuration

### 2.1 API Keys
1. Log in to the **Razorpay Dashboard** (`https://dashboard.razorpay.com/`).
2. Go to **Settings &gt; API Keys**.
3. Generate **Key ID** and **Key Secret**.
4. In Test Mode, Key ID begins with `rzp_test_...`. In Live Mode, Key ID begins with `rzp_live_...`.

### 2.2 Webhook Configuration
1. In Razorpay Dashboard, navigate to **Settings &gt; Webhooks**.
2. Click **Add New Webhook**.
3. Configure the webhook endpoint:
   - **Webhook URL:** `https://vox-voice-assistant.vercel.app/api/webhooks/razorpay`
   - **Secret:** Generate a cryptographically strong secret string (e.g. `openssl rand -hex 24`). Set this as `RAZORPAY_WEBHOOK_SECRET` in Vercel environment variables.
   - **Active Events to subscribe:**
     - `order.paid`
     - `payment.captured`
     - `payment.failed`
     - `subscription.charged`
     - `subscription.cancelled`
     - `subscription.paused`
     - `subscription.resumed`
4. Save the webhook.

---

## 3. Serverless Integration Flow

```
[User on pricing.html]
       │
       │ 1. Clicks "Upgrade to VOX Pro"
       │ 2. Sends POST /api/checkout/create-order with { plan_id: "pro", interval: "monthly" }
       ▼
[Vercel Function /api/checkout/create-order]
       │
       │ 3. Instantiates Razorpay SDK with RAZORPAY_KEY_ID & RAZORPAY_KEY_SECRET
       │ 4. Calls razorpay.orders.create({ amount: 19900, currency: "INR", notes: { user_id, interval } })
       │ 5. Returns { order_id, key_id, amount, currency }
       ▼
[Frontend: Razorpay Standard Checkout Popup]
       │
       │ 6. Opens branded payment modal (UPI, Cards, Netbanking)
       │ 7. Customer completes payment
       ▼
[Razorpay Servers]
       │
       │ 8. Sends POST webhook to https://vox-voice-assistant.vercel.app/api/webhooks/razorpay
       │    Headers: X-Razorpay-Signature: <HMAC-SHA256>
       ▼
[Vercel Function /api/webhooks/razorpay]
       │
       │ 9. Verifies HMAC-SHA256 signature against RAZORPAY_WEBHOOK_SECRET
       │ 10. Records event in public.payment_events table
       │ 11. Upserts public.subscriptions with status = 'active', interval = 'monthly'
       │ 12. Updates public.entitlements with is_pro = true, increments token_version
       │ 13. Returns HTTP 200 { status: 'ok', received: true }
```

---

## 4. Refund & Cancellation Processing

- **7-Day Refund Policy:** Managed via `billing@voxlocal.ai`. Administrators can trigger refunds directly from the Razorpay Dashboard (Payments &gt; Issue Refund) or via Razorpay REST API.
- **Cancellation:** Users can cancel anytime from `account.html`. The webhook `subscription.cancelled` sets `subscriptions.status = 'canceled'`. The user retains Pro access until `current_period_end`.

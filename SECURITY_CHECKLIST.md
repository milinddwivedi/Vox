# VOX — Security, Privacy & Integrity Checklist

## 1. Local Data Protection (Windows Desktop)

- [x] **Zero Raw Audio Transmission:** Microphones and system audio streams are captured via local WASAPI and fed directly into `faster-whisper`. At no point is audio encoded for cloud transmission.
- [x] **Local SQLite Database Security:** Meeting notes, transcripts, vocabulary, and action items are stored in SQLite at `%APPDATA%/VoxLocal/data/voxlocal.db` with restricted file permissions.
- [x] **Win32 DPAPI Cryptographic Protection:** 
  - Entitlement tokens cached in `data/entitlement.vault` are encrypted via Windows Data Protection API (`CryptProtectData`).
  - Session tokens in `data/auth_session.vault` are encrypted via DPAPI.
  - Local metering periods contain a cryptographic DPAPI seal preventing manual editing of committed word counters.
- [x] **Safe Downgrade Policy:** Expiration of a Pro subscription or offline timeout reverts to Free mode without deleting, locking, or encrypting any existing local recordings or user notes.

---

## 2. Authentication & Authorization Security

- [x] **RFC 7636 PKCE Implementation:** Desktop loopback OAuth employs high-entropy `code_verifier` (43-128 chars) and SHA-256 `code_challenge` (S256), mitigating authorization code interception attacks.
- [x] **Loopback Interface Binding:** Ephemeral desktop OAuth receiver binds strictly to `127.0.0.1` (never `0.0.0.0`), preventing external network access to the loopback port.
- [x] **State Parameter Validation:** Cryptographically random state parameter verified on return before processing authorization codes.
- [x] **Short-Lived Tokens:** Supabase JWT access tokens expire in 1 hour with refresh token rotation.
- [x] **Entitlement Token Signing:** Serverless backend issues HMAC-SHA256 signed entitlement tokens with bounded 7-day expiration and 72-hour offline grace limits.

---

## 3. Cloud & Serverless Infrastructure Security

- [x] **Row Level Security (RLS):** All Supabase tables (`profiles`, `subscriptions`, `entitlements`, `usage_periods`, `usage_events`, `devices`) enforce PostgreSQL RLS policies ensuring users can only read and write their own data.
- [x] **Service Role Key Isolation:** `SUPABASE_SERVICE_ROLE_KEY` is restricted strictly to backend Serverless Functions and is never exposed in client bundles or public repositories.
- [x] **Razorpay Webhook Signature Verification:** All incoming webhook payloads on `/api/webhooks/razorpay` require valid `X-Razorpay-Signature` HMAC-SHA256 headers matching `RAZORPAY_WEBHOOK_SECRET`.
- [x] **Idempotent Usage Ingestion:** Usage sync endpoints accept UUID-tagged events with unique database constraints to prevent double-charging or replays.

---

## 4. Payment Gateway & PCI-DSS Compliance

- [x] **Zero Cardholder Data Storage:** Credit card details, debit card PINs, CVVs, and banking passwords are typed directly into Razorpay's PCI-DSS Level 1 certified modal iframe and never touch VOX servers.
- [x] **HTTPS Everywhere:** All web properties and API endpoints enforce TLS 1.3 encryption.

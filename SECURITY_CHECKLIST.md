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
- [x] **Asymmetric Ed25519 Entitlement Signing & Key Isolation:** Serverless backend signs entitlement tokens with an RFC 8032 / RFC 8410 Ed25519 private key stored exclusively in Vercel Production encrypted environment secrets (`VOX_ENTITLEMENT_PRIVATE_KEY_ED25519` / `ENTITLEMENT_PRIVATE_KEY_PEM`) with zero private keys in `src/` or `api/`. The Windows desktop client embeds only the rotated 32-byte public verification key (`05de27f30f315a3eab09b1168993e32ce9b89b4c1fb596608c258ff302fb9e36`) in [entitlement.py](file:///d:/Milind/Voice%20Assistant/src/voxlocal/core/entitlement.py), enforces bounded 7-day token expiration and 72-hour offline grace (`MAX_OFFLINE_GRACE_HOURS = 72.0`), and rejects system clock rollbacks (`< -1.0h`).
- [x] **Multi-User & Calendar Broker Separation:** `DesktopAuthBroker` (`src/voxlocal/auth/desktop_auth.py`) is strictly decoupled from `CalendarAuthBroker` (`src/voxlocal/calendar/auth_service.py`), binding DPAPI vaults and SQLite `local_metering_periods` to `user_id` and verifying `expected_user_id` on entitlement token loads.
- [x] **No Client-Side Activation Backdoors:** Manual payment-ID bypass widgets (`input_key`, `btn_activate`, `_on_activate_clicked`) and client-side signing helpers (`sign_entitlement_payload`) are completely removed from [pro_upgrade_dialog.py](file:///d:/Milind/Voice%20Assistant/src/voxlocal/ui/pro_upgrade_dialog.py) and `src/voxlocal/core/entitlement.py`.

---

## 3. Cloud, Release & Serverless Infrastructure Security

- [x] **Row Level Security (RLS):** All 9 Supabase tables (`plans`, `profiles`, `subscriptions`, `entitlements`, `usage_periods`, `usage_events`, `devices`, `device_records`, `payment_events`) enforce PostgreSQL RLS policies ensuring users can only read and write their own data.
- [x] **Service Role Key Isolation:** `SUPABASE_SERVICE_ROLE_KEY` is restricted strictly to backend Serverless Functions and is never exposed in client bundles or public repositories.
- [x] **Razorpay Webhook Signature Verification:** All incoming webhook payloads on `/api/webhooks/razorpay` verify raw request body HMAC-SHA256 via `crypto.timingSafeEqual` against `RAZORPAY_WEBHOOK_SECRET`, with deterministic deduplication in `payment_events` and 3-tier user reconciliation.
- [x] **Idempotent Usage Ingestion:** Usage sync endpoints accept UUID-tagged events with unique database constraints to prevent double-charging or replays.
- [x] **Signed Release Manifest & Binary Integrity Chain:** `/api/releases/latest` serves an Ed25519-signed release manifest verified by `UpdateManager` (`src/voxlocal/core/updater.py`) alongside the rebuilt `VoxLocal-Setup.exe` (`10,067,409` bytes, SHA-256 `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`).

---

## 4. Payment Gateway & PCI-DSS Compliance

- [x] **Zero Cardholder Data Storage:** Credit card details, debit card PINs, CVVs, and banking passwords are typed directly into Razorpay's PCI-DSS Level 1 certified modal iframe and never touch VOX servers.
- [x] **HTTPS Everywhere:** All web properties and API endpoints enforce TLS 1.3 encryption.

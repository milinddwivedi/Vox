# VOX — Commercialisation & Production Execution Acceptance Report

**Product:** VOX — Local-first AI Voice Typing & Meeting Intelligence  
**Production URL:** `https://vox-voice-assistant.vercel.app`  
**Supabase Project:** `https://qiigoipgoedexmdowtvj.supabase.co` (`qiigoipgoedexmdowtvj`)  
**Repository:** `d:\Milind\Voice Assistant` (`https://github.com/milinddwivedi/Vox`)  

---

## 1. Executive Summary & Remediation Verdict

All critical engineering, security, commercial metering, desktop UI, web dashboard, webhook reconciliation, and installer trust-chain findings raised during the commercialisation verification review have been remediated, rebuilt, and verified:

| Area | Remediation Implemented | Verification Status |
| :--- | :--- | :--- |
| **Asymmetric Ed25519 Entitlement Signing & Key Isolation** | Replaced HMAC-SHA256 with RFC 8032 / RFC 8410 Ed25519 signatures; rotated production keypair so private keys exist exclusively in Vercel encrypted Secrets (`VOX_ENTITLEMENT_PRIVATE_KEY_ED25519`, `ENTITLEMENT_PRIVATE_KEY_PEM`); purged `DEV_FALLBACK_PRIVATE_KEY_PEM` from `api/_lib/token.js` and removed signing keys from `src/voxlocal/core/entitlement.py`. | **Verified (202/202 local + live `/api/entitlements` check)** |
| **72-Hour Offline Grace & Clock Rollback Protection** | `EntitlementManager` dynamically enforces 72h offline expiration and rejects clock rollback (`< -1.0h`) at operation boundaries (`dictation`, `meeting`, `ask_vox`) without terminating active calls mid-meeting. | **Verified (`pytest` e2e & boundary suites)** |
| **Account Broker Separation & Multi-User Isolation** | Split `DesktopAuthBroker` (`src/voxlocal/auth/desktop_auth.py`) from `CalendarAuthBroker` (`src/voxlocal/calendar/auth_service.py`); isolated DPAPI vaults and SQLite `local_metering_periods` by `user_id`; cleared vaults on logout (`controller.logout_user()`). | **Verified (`test_milestone2_desktop_ui.py` & `test_e2e_commercial_journey.py`)** |
| **Removal of Manual Payment-ID Backdoor** | Removed `input_key`, `btn_activate`, and `_on_activate_clicked` from `VoxProUpgradeDialog` (`src/voxlocal/ui/pro_upgrade_dialog.py`), replacing them with official Razorpay links (`https://rzp.io/rzp/ugv7JBF2`, `https://rzp.io/rzp/fycKln1w`) and `controller.sync_entitlements_with_cloud()`. | **Verified (`test_pro_upgrade_dialog_and_gating.py`)** |
| **Desktop Plan & Quota Visibility** | Added sidebar plan badge, live word quota meter, upgrade CTA (`MainWindow._update_plan_card`), and tray menu plan/upgrade actions (`SystemTrayManager`). | **Verified (`test_milestone2_desktop_ui.py`)** |
| **Live Web Account & Google OAuth Dashboard** | Removed hardcoded sample counters and `localStorage.getItem('vox_plan')` authority from `docs/account.html`, `docs/login.html`, and `docs/assets/vox-auth.js`; wired Supabase GoTrue Auth + live `subscriptions` / `usage_periods` / `/api/entitlements` queries and loopback relay (`api/auth/desktop-login.js`, `api/auth/desktop-callback.js`). | **Verified (`test_milestone3_frontend_and_loopback.js`)** |
| **Razorpay Webhooks, Checkout & Usage Sync** | Raw-body `X-Razorpay-Signature` HMAC-SHA256 verification via `crypto.timingSafeEqual`, deterministic idempotency in `payment_events`, 3-tier user reconciliation, stale event ordering guard, and multi-schema `/api/usage/sync`. | **Verified (`test_milestone4_api_contracts.js` & `test_webhooks_and_sync.py`)** |
| **Database Schema & RLS** | Deployed `supabase/migrations/20261115_commercial_schema.sql` across 9 RLS-protected tables (`plans`, `profiles`, `subscriptions`, `entitlements`, `usage_periods`, `usage_events`, `devices`, `device_records`, `payment_events`). | **Verified live (`test_live_supabase_journey.py` & Supabase MCP)** |
| **Installer Rebuild & Signed Release Manifest** | Rebuilt `VoxLocal-Setup.exe` bundling the rotated production public key (`10,067,409` bytes, SHA-256 `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`) and signed `/api/releases/latest` verified by `UpdateManager`. | **Verified (`test_release_trust_chain.py` & live HTTPS checks)** |

---

## 2. Cryptographic & Distribution Artifacts Reference

- **Rotated Production Ed25519 Public Key (32-byte raw hex):**  
  `05de27f30f315a3eab09b1168993e32ce9b89b4c1fb596608c258ff302fb9e36`
- **Rotated Production Ed25519 SPKI Public Key (Base64):**  
  `MCowBQYDK2VwAyEABd4n8w8xWj6rCbEWiZPjLOm4m0wftZZgjCWP8wL7njY=`
- **Embedded Locations (Public Key Only):**
  - `src/voxlocal/core/entitlement.py` (`DEFAULT_ENTITLEMENT_PUBLIC_KEY`)
  - `api/_lib/token.js` (`DEFAULT_PUBLIC_KEY_HEX`, `DEFAULT_PUBLIC_KEY_PEM`)
  - Bundled inside `dist/VoxLocal-Setup.exe` & `docs/downloads/VoxLocal-Setup.exe`
- **Private Signing Key Storage:** Exclusively stored in Vercel Production Secrets (`VOX_ENTITLEMENT_PRIVATE_KEY_ED25519`, `ENTITLEMENT_PRIVATE_KEY_PEM`). Test-only keys reside strictly in `tests/`.
- **Installer Artifact (`docs/downloads/VoxLocal-Setup.exe`):**
  - **Size:** `10,067,409` bytes
  - **SHA-256:** `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`
  - **Ed25519 Manifest Signature:** Verified against `/api/releases/latest` via `verify_manifest_signature` and `UpdateManager`.

---

## 3. Automated & Live Verification Summary

- **Python Test Suite (`pytest`, 7 files):** **172 passed in 14.98s**
  - `tests/test_commercial_metering_and_entitlements.py` (23)
  - `tests/test_pro_upgrade_dialog_and_gating.py` (7)
  - `tests/test_milestone2_desktop_ui.py` (11)
  - `tests/test_e2e_commercial_journey.py` (80)
  - `tests/api/test_webhooks_and_sync.py` (26)
  - `tests/test_release_trust_chain.py` (20)
  - `tests/test_live_supabase_journey.py` (5)
- **Node.js Verification Suites:**
  - `tests/test_milestone3_frontend_and_loopback.js`: **5/5 checks passed**
  - `tests/test_milestone4_api_contracts.js`: **29/29 tests passed** (including authorized download contracts)
- **Live HTTPS Production Audit (`scripts/verify_live_production.py`):** **7/7 checks passed (100%)** against `https://vox-voice-assistant.vercel.app` and `https://qiigoipgoedexmdowtvj.supabase.co`:
  1. `/api/entitlements`: HTTP 200, Ed25519 asymmetric verification valid.
  2. `/account.html`: HTTP 200, clean of mock metrics and client overrides.
  3. `/api/releases/latest`: HTTP 200, Ed25519 signed manifest valid.
  4. `/downloads/VoxLocal-Setup.exe`: HTTP 404 (Public static download bypass permanently blocked).
  5. `POST & OPTIONS /api/download/authorize`: OPTIONS 200 (CORS valid), Unauthenticated POST 401 Unauthorized with proper gating error.
  6. `/download`: HTTP 200, Dedicated download portal live with Google OAuth gating.
  7. Supabase Google OAuth endpoint: HTTP 302 Found redirecting to `accounts.google.com` with client ID `541845334167-k9sa1dem22flsddu5721tsr173snbppo.apps.googleusercontent.com`.

---

## 4. External Console Setup Status (Requires User Sign-In in Browser Pane)

Interactive external dashboards were probed via browser automation (`Model: "flash"`) and are blocked solely on interactive user login:
1. **Google Cloud Console (`dozy-io`):** Protected authentication origin `accounts.google.com` requires user sign-in to verify/create a **Web application** OAuth 2.0 client (noting the previously downloaded JSON was an `"installed"` desktop client) with Authorized Redirect URI `https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback`.
2. **Supabase Dashboard (`qiigoipgoedexmdowtvj`):** Redirected to `https://supabase.com/dashboard/sign-in` (`final_step_6.png`); user login is required to confirm Google OAuth provider credentials & URL Configuration redirect allowlist.
3. **Razorpay Dashboard:** Redirected to `https://accounts.razorpay.com/auth/` (`final_step_9.png`); user login is required to confirm `https://vox-voice-assistant.vercel.app` in Business Website Details, verify the `/api/webhooks/razorpay` webhook secret matches Vercel `RAZORPAY_WEBHOOK_SECRET`, and optionally retrieve `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`.

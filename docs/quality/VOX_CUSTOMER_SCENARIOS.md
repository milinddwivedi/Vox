# VOX Local AI — Customer Scenarios & Behavioural Test Library

**Document Code:** QA-SCENARIOS-2026-V1  
**Target Coverage:** Complete VOX Ecosystem (Desktop App, Web Platform, Cloud Sync, Subscriptions)  
**Total Documented Scenarios:** 120 Comprehensive Real-Customer Journeys across 16 Functional Modules.

---

## Matrix of Modules & Scenario Distribution

| Module ID | Functional Area | Scenario Range | Focus Area |
|---|---|---|---|
| **MOD-01** | First-Time User Experience & Onboarding | SCEN-001 to SCEN-008 | App startup, permissions, audio setup, first dictation |
| **MOD-02** | Google Authentication & Account Switching | SCEN-009 to SCEN-016 | Google OAuth web flow, desktop broker, logout, multi-user |
| **MOD-03** | Main Dashboard & Navigation | SCEN-017 to SCEN-024 | Sidebar routing, search, theme switching, responsiveness |
| **MOD-04** | Voice Typing & Streaming Dictation | SCEN-025 to SCEN-036 | Low-latency streaming, Hinglish, punctuation, hotkeys |
| **MOD-05** | Verbal Self-Correction & Negations | SCEN-037 to SCEN-044 | "Scratch that", "actually", compound negations, retractions |
| **MOD-06** | Focus Preservation & Target Injections | SCEN-045 to SCEN-052 | Win32 focus lock, SendInput, clipboard fallback, Teams/Word |
| **MOD-07** | Meeting Detection & Calendar Integrations | SCEN-053 to SCEN-062 | 1-click join, pre-priming, Zoom/Meet/Teams auto-detection |
| **MOD-08** | Connected Accounts & Sync Health Lifecycle | SCEN-063 to SCEN-072 | Multi-account, pause/resume, offline caching, stale error isolation |
| **MOD-09** | Meeting Studio & Dual Audio Recording | SCEN-073 to SCEN-082 | Mic + system loopback, device disconnect, background noise |
| **MOD-10** | Live Transcription & Diarization | SCEN-083 to SCEN-090 | Speaker identification, cross-talk, Indian English terminology |
| **MOD-11** | Meeting Summaries & Action Items | SCEN-091 to SCEN-098 | Local LLM intelligence, mind maps, grounded action extraction |
| **MOD-12** | Ask VOX & Cognitive Retrieval | SCEN-099 to SCEN-104 | Cross-meeting semantic search, timeline queries, privacy guard |
| **MOD-13** | Quota Metering & Subscription Gating | SCEN-105 to SCEN-112 | Free tier counters, quota exhaustion, Pro upgrade debounce |
| **MOD-14** | Payment & Razorpay Checkout Flow | SCEN-113 to SCEN-116 | Single tab checkout, webhook sync, instant Pro activation |
| **MOD-15** | Local Data Persistence & Disaster Recovery | SCEN-117 to SCEN-120 | SQLite WAL mode, sudden crash recovery, v6 schema migrations |
| **MOD-16** | System Tray, Hotkeys & Background Operation | SCEN-121 to SCEN-125 | Minimize to tray, global hotkey capture, Windows startup |

---

## Module 1: First-Time User Experience & Onboarding

### SCEN-001: Cold First-Run on Fresh Windows Machine
- **Customer Context:** New user downloads `VoxLocal.exe`, installs or launches portable binary for the first time.
- **Action Sequence:**
  1. User double-clicks executable.
  2. Windows SmartScreen or UAC prompt appears (if unsigned/testing); user proceeds.
  3. VOX initializes local SQLite database and checks hardware microphone.
- **Expected Outcome:**
  - App opens within <2.5 seconds.
  - Onboarding banner welcomes user and highlights default global hotkey (e.g., Hold `Caps Lock` to dictate).
  - Sidebar shows "Guest Mode" with default 5,000 Free words.
  - Audio device auto-selects default Windows input device.
- **Edge Case / Disruption:** Default microphone is disabled or muted in Windows.
  - *Expected Handling:* Non-intrusive banner alerts: *"Microphone unavailable. Please select an audio input in Settings."* App does NOT crash.
- **Verification Test:** `tests/e2e/test_speech_benchmarks.py::test_f24_cold_startup_controller_init_sla`

### SCEN-002: User Immediately Closes Window During First Launch
- **Customer Context:** User opens VOX, immediately clicks `X` (close window) within 500ms before reading anything.
- **Expected Outcome:**
  - Window closes smoothly or minimizes to System Tray with subtle notification: *"VOX is still running in the background. Press Caps Lock to dictate anytime."*
  - Background threads cleanly exit or park; no orphan audio capture threads or memory leaks.

---

## Module 2: Google Authentication & Account Switching

### SCEN-009: Google OAuth Sign-In from Desktop App
- **Customer Context:** User in Guest Mode clicks "+ Sign In with Google" in the desktop sidebar.
- **Action Sequence:**
  1. Desktop app calls `DesktopAuthBroker.start_login_flow()`.
  2. Broker opens default web browser to `https://vox-voice-assistant.vercel.app/account.html?flow=desktop`.
  3. User completes Google authentication with email `milinddwivedi0@gmail.com`.
  4. Web portal redirects to loopback callback / deep link `vox://auth/callback?token=...`.
- **Expected Outcome:**
  - Desktop receives and validates Supabase JWT.
  - Profile card in sidebar immediately updates:
    - User avatar: "M" with gradient background.
    - Display Name: "Milind Dwivedi".
    - Email: `milinddwivedi0@gmail.com`.
    - Sign In button replaced with "Sign Out".
  - Usage quota re-hydrates for this specific user.
- **Verification Test:** `tests/test_milestone2_desktop_ui.py::test_sidebar_user_profile_displays_google_account_and_updates_on_auth_state_changed`

### SCEN-010: Switching Google Accounts / Logout Flow
- **Customer Context:** User clicks "Sign Out" to switch accounts or lend workstation.
- **Action Sequence:**
  1. User clicks "Sign Out" in desktop sidebar.
- **Expected Outcome:**
  - Tokens purged from DPAPI-encrypted `token_vault` in SQLite.
  - Sidebar immediately reverts to "Guest Mode" and "+ Sign In with Google".
  - Calendar accounts and local transcripts are isolated to the previous user and hidden from the guest session.
- **Verification Test:** `tests/test_milestone2_desktop_ui.py::test_controller_logout_flow_clears_vault_and_reverts`

---

## Module 4: Voice Typing & Streaming Dictation

### SCEN-025: Rapid Dictation Utterance into Notepad
- **Customer Context:** User holds `Caps Lock`, speaks sentence: *"The quarterly roadmap has been finalized for Q3."*, releases hotkey.
- **Expected Outcome:**
  - Floating pill appears near cursor within 80ms.
  - Interim words stream live: *"The quarterly..."* -> *"roadmap has been finalized for Q3."*
  - Upon release, text is injected into Notepad via Win32 `SendInput`.
  - Previous clipboard content remains 100% untouched.
  - Pill dismisses smoothly after 600ms.
- **Verification Test:** `tests/test_adversarial_m2_empirical.py::test_focus_invariance_on_interactions`

### SCEN-026: Multilingual Hinglish Code-Switching
- **Customer Context:** User dictates technical Hinglish phrase: *"Yeh feature release hone ke baad production pipeline automate ho jayegi."*
- **Expected Outcome:**
  - Engine detects language mixing seamlessly.
  - Script output is romanized Hinglish: *"Yeh feature release hone ke baad production pipeline automate ho jayegi."*
  - Technical terms ("feature", "release", "production", "pipeline") maintain exact English casing and spelling.
- **Verification Test:** `tests/test_adversarial_m2_empirical.py::test_hinglish_phrases_preservation_and_normalization`

---

## Module 5: Verbal Self-Correction & Negations

### SCEN-037: "Scratch That" Retraction in Long Sentence
- **Customer Context:** User speaks: *"We need to allocate 500 dollars scratch that 750 dollars for the cloud infrastructure."*
- **Expected Outcome:**
  - Verbal retraction detector identifies `"scratch that"`.
  - Strikethrough markup / cancellation drops `"500 dollars"`.
  - Injected text: *"We need to allocate $750 for the cloud infrastructure."*
- **Verification Test:** `tests/e2e/test_adversarial_stress_tier4.py::test_stress_verbal_self_correction_chains_and_negations`

### SCEN-038: Negation Guard False Positive Prevention
- **Customer Context:** User speaks: *"Please be careful and do not scratch that glass table in the conference room."*
- **Expected Outcome:**
  - Negation guard evaluates grammar context: `"do not scratch that"` is NOT a command retraction.
  - Text injected verbatim: *"Please be careful and do not scratch that glass table in the conference room."*
- **Verification Test:** `tests/test_adversarial_m2_empirical.py::test_retraction_negation_guard_false_positives`

---

## Module 6: Focus Preservation & Target Injections

### SCEN-045: Dictating into Microsoft Teams / WebView2 Chat
- **Customer Context:** User is typing in Microsoft Teams or Slack (Electron/Chromium WebView2).
- **Expected Outcome:**
  - Inserter detects rich WebView target (`teams.exe`, `slack.exe`, `olk.exe`).
  - Employs hardware scan-code clipboard paste fallback.
  - Saves user's existing clipboard text.
  - Pastes transcribed text via `Ctrl+V`.
  - Restores original clipboard text within 90ms.
  - User can press `Ctrl+V` immediately after and paste their own previous snippet without corruption.
- **Verification Test:** `tests/e2e/test_adversarial_stress_tier4.py::test_stress_clipboard_restoration_fast_sequential`

---

## Module 8: Connected Accounts & Sync Health Lifecycle

### SCEN-063: Pausing and Resuming Microsoft Outlook Account
- **Customer Context:** User has connected `milinddwivedi0@outlook.com`. Wants to pause calendar syncing during personal time.
- **Action Sequence:**
  1. User navigates to Calendar page.
  2. Card shows: `milinddwivedi0@outlook.com`, badge `"Connected"`.
  3. User clicks "Pause".
- **Expected Outcome:**
  - Card badge immediately changes to `"Paused"` (muted gray).
  - Button toggles to "Enable".
  - Reconnect button is hidden (no scary error alerts).
  - Sync Health KPI transitions to `"Paused"` with subtext *"Sync paused by user"*.
  - Accounts KPI displays `"1 Paused"` instead of `"0 Accounts"`.
  - When user clicks "Enable", account resumes, background sync starts on worker thread (UI does not freeze), and badge transitions to `"Syncing..."` then `"Connected"`.
- **Verification Test:** `tests/test_calendar_accounts_lifecycle.py::test_paused_account_state_machine_and_health_summary`

### SCEN-064: Network Disconnect / Traveling Offline Mode
- **Customer Context:** User takes laptop onto airplane or train with no internet.
- **Expected Outcome:**
  - Scheduled background sync encounters `NETWORK_OFFLINE`.
  - Engine preserves all previously synced calendar events in local SQLite.
  - Account card badge shows `"Offline (Cached)"` (muted).
  - Card button shows `"Retry Sync"`.
  - Spurious "Reconnect" or "Sync Error" banners are completely suppressed.
  - Next upcoming meeting continues to display and notify on time.
- **Verification Test:** `tests/test_unified_calendar_engine.py::test_network_offline_resilience_and_no_reauth_spurious_alert`

### SCEN-065: Historical Stale Error Isolation on Startup
- **Customer Context:** Workstation rebooted after network outage 3 days ago. Network is now healthy.
- **Expected Outcome:**
  - Live provider connects and authenticates.
  - Old historical error stored in SQLite (`NETWORK_OFFLINE` from 3 days ago) is completely ignored in favor of live healthy state.
  - SQLite database is updated with clean empty error fields upon first successful sync.
  - UI shows `"Healthy"`, `"Connected"`, and zero error banners.
- **Verification Test:** `tests/test_calendar_accounts_lifecycle.py::test_stale_db_error_does_not_corrupt_live_healthy_provider`

---

## Module 13 & 14: Quota Metering & Subscription Checkout

### SCEN-105: Free User Hits 5,000 Words Limit
- **Customer Context:** Free user completes dictation that brings monthly total to 5,001 words.
- **Expected Outcome:**
  - Dictation finishes and inserts current text without clipping mid-sentence.
  - Sidebar quota meter shows `5,000 / 5,000 words (100%)` in amber/coral.
  - Subsequent dictation attempt gracefully summons the Pro Upgrade Dialog: *"You have reached your Free tier monthly limit."*
- **Verification Test:** `tests/test_pro_upgrade_dialog_and_gating.py::test_controller_signals_upgrade_dialog_on_quota`

### SCEN-113: Rapid Double-Click on "Upgrade Monthly" Button
- **Customer Context:** User in upgrade dialog rapidly double-clicks "Upgrade Monthly" button.
- **Expected Outcome:**
  - Debounce guard (1.2s window) prevents multiple browser tabs from launching.
  - Exactly ONE browser tab opens to Razorpay checkout / account portal.
  - Button shows temporary loading state.
- **Verification Test:** `tests/test_pro_upgrade_dialog_and_gating.py::test_pro_upgrade_dialog_open_links`

---

## Module 15: Local Data Persistence & Disaster Recovery

### SCEN-117: Sudden Power Cut / Process Termination During Meeting
- **Customer Context:** Laptop runs out of battery or user process is killed (`taskkill /F /IM VoxLocal.exe`) while recording a meeting.
- **Expected Outcome:**
  - Audio chunk writer flushes raw PCM chunks to disk incrementally every 3 seconds.
  - SQLite runs in Write-Ahead Log (WAL) mode; no database corruption occurs.
  - On next VOX launch, database integrity check passes.
  - Recovery subsystem detects unfinalized recording and offers: *"Recover unfinalized recording from [Time]?"*
- **Verification Test:** `tests/test_unified_calendar_engine.py::test_recurring_series_not_collapsed_fault_isolation_and_restart_recovery`

# VOX Local AI — Comprehensive Product Behaviour & Business Logic Map

**Version:** 2.0.0-PROD  
**Author:** Principal QA & Senior Application Debugging Engineering  
**Scope:** VOX Desktop Application (PyQt6 / Win32 / PyTorch), Web Platform (Vercel / Next.js), Supabase Authentication & Storage, Razorpay Payment Subsystems.

---

## 1. System Architecture & Ecosystem Boundaries

VOX Local AI is an enterprise-grade, local-first voice dictation and meeting intelligence workstation. Its design is governed by a strict zero-leakage local privacy model combined with seamless cloud synchronization for licensing, authentication, and cross-platform calendar integration.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            VOX LOCAL DESKTOP APP                             │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌──────────────────┐ │
│  │   PyQt6 UI Layer      │  │  AppController & Core │  │ Win32 Subsystem  │ │
│  │  - Dictation Floating │  │  - Dictation Engine   │  │  - LowLevelHook  │ │
│  │  - Meeting Studio     │  │  - Meeting Recorder   │  │  - Focus Tracker │ │
│  │  - Calendar Page      │  │  - Intelligence Host │  │  - Text Inserter │ │
│  │  - Plan & Profile Card│  │  - Metering Engine    │  │  - WinEvent Hook │ │
│  └───────────────────────┘  └───────────────────────┘  └──────────────────┘ │
│                                 │              │                            │
│  ┌──────────────────────────────┴──────────────┴──────────────────────────┐ │
│  │                    Local SQLite Database (v6 Migration)                │ │
│  │  - Encrypted Tokens (DPAPI)   - Calendar Events & Accounts             │ │
│  │  - Transcripts & Summaries    - Local Usage Counters & Cache           │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │ OAuth / Webhooks / Licensing
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         VOX CLOUD & WEB INFRASTRUCTURE                      │
│  ┌────────────────────────┐  ┌───────────────────────┐  ┌─────────────────┐ │
│  │    Vercel Web App      │  │      Supabase         │  │    Razorpay     │ │
│  │  - Landing & Docs      │  │  - Google Web OAuth   │  │  - Subscription │ │
│  │  - User Account Portal │  │  - User Directory     │  │  - Webhook Sync │ │
│  │  - Desktop Deep-Link   │  │  - Signed Tokens      │  │  - Pro Licenses │ │
│  └────────────────────────┘  └───────────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core State Machines & Business Logic Rules

### 2.1 User Authentication & Google Profile Lifecycle

VOX uses an isolated Auth Broker architecture:
1. **Google OAuth Web Application Client:** Registered under Google Cloud Project `vox-local-514822`, authenticating via Supabase OAuth callback (`https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback`).
2. **Desktop Auth Broker:** Listens on a dedicated local loopback port or custom protocol `vox://auth/callback` to securely hand off authenticated user sessions without requiring embedded browser credentials.

#### State Transitions:
- `UNAUTHENTICATED (Guest Mode)`:
  - Sidebar displays "Guest Mode" avatar and "+ Sign In with Google" button.
  - Plan defaults to Free tier based on local hardware identifier.
  - Dictation & local meetings operate within Free tier limits (5,000 words/mo, 3 meetings/mo).
- `AUTHENTICATING (Browser Flow In Progress)`:
  - User clicks "Sign In with Google" or "Account" in Desktop app.
  - Browser launches to authenticated VOX web portal.
  - User completes Google OAuth consent.
  - Web portal redirects session token to desktop via `vox://auth/callback?token=...`.
- `AUTHENTICATED`:
  - Desktop parses validated JWT; extracts `user_id`, `email`, `display_name`, `avatar_url`.
  - Credentials stored in SQLite with DPAPI encryption (`token_vault`).
  - Sidebar profile card immediately transitions: displays user initials/avatar, full display name, and email.
  - "Sign Out" button appears.
  - Metering engine re-hydrates quotas for the specific `user_id`.
- `LOGGED_OUT`:
  - User clicks "Sign Out".
  - Desktop clears session tokens from `token_vault`.
  - Metering engine immediately isolates and switches context back to `default` guest profile.
  - UI updates immediately to "Guest Mode".

---

### 2.2 Voice Typing & Streaming Dictation Engine

The dictation subsystem captures live microphone audio, processes it through streaming acoustic models (Sherpa-ONNX / Whisper / Vosk), applies Indian English / Hinglish normalizers, and delivers text to the user's active window.

#### State Transitions:
- `IDLE`:
  - Global hotkey hook active (Default: `Caps Lock` hold or double-tap `Ctrl`).
  - Win32 Focus Tracker monitors foreground window HWND.
- `LISTENING (Recording Audio)`:
  - Audio capture stream active at 16 kHz 16-bit mono.
  - Floating pill animates with live audio waveform level.
  - Rolling acoustic buffer maintained in memory.
- `STREAMING_INTERIM`:
  - Fast-tier model yields streaming partial tokens.
  - Real-time text preview shown on floating pill.
  - Verbal retraction detector monitors for speech patterns:
    - `"scratch that"` -> drops previous phrase or clause.
    - `"actually <correction>"` -> replaces immediately preceding word/phrase.
    - Negation guard active: phrases like `"I don't want to scratch that"` are preserved literally.
- `FINALIZING / FORMATTING`:
  - Silence detected (VAD pause) or hotkey released.
  - Large-tier model generates finalized text with punctuation and domain vocabulary.
  - Spoken punctuation converted (e.g., `"new line"`, `"comma"`, `"dollar five hundred"` -> `$500`).
- `DELIVERING (Text Injection)`:
  - Focus Tracker ensures the original target window remains focused.
  - Delivery strategy determined by target application:
    - Standard native apps: Win32 `SendInput` UTF-16 Unicode characters.
    - Electron / WebView2 / Microsoft Teams / Slack: Fallback to Clipboard Paste (`Ctrl+V`) with automatic previous clipboard preservation and restoration within 90ms.
- `COMPLETED`:
  - Quota counter incremented by word count in local SQLite.
  - Pill returns to `IDLE`.

---

### 2.3 Connected Accounts & Unified Calendar State Machine

The Unified Calendar Engine bridges Google Calendar, Microsoft 365 / Outlook (Graph API & MAPI), and Zoom into a consolidated schedule.

#### Core States:
- `CONNECTED`:
  - Valid OAuth refresh token or active local feed.
  - Background polling runs every 60 seconds (or on user trigger).
  - Last sync timestamp updated.
  - Error state is `None`, badge displays `"Connected"` (`ok`).
- `SYNCING`:
  - Background daemon thread is currently polling provider APIs.
  - Badge displays `"Syncing..."` (`info`).
  - UI thread remains completely unblocked.
- `PAUSED`:
  - User clicked "Pause" on the account card.
  - Background synchronization is halted for this account.
  - Meetings from this account are excluded from upcoming alerts.
  - Sync health summary transitions to `"Paused"`.
  - Account KPI displays `N Paused` instead of `0 Accounts` or `Not Connected`.
- `OFFLINE_CACHED`:
  - Internet connection lost or remote server unreachable (`NETWORK_OFFLINE`).
  - Refresh token remains valid.
  - Schedule displays cached meetings from SQLite without clearing them.
  - Account card badge shows `"Offline (Cached)"` (`muted`).
  - Action button offers `"Retry Sync"`.
  - Spurious "Reconnect" alert is completely suppressed.
- `EXPIRED`:
  - Refresh token rejected (`AUTH_EXPIRED`, `REFRESH_FAILED`, or revoked).
  - Account card badge shows `"Expired — Reconnect"` (`warn`).
  - Prominent purple `"Reconnect"` button displayed.
  - Sync health KPI reflects `"Reconnect Needed"`.
- `ADMIN_APPROVAL_REQUIRED`:
  - Corporate Azure AD / Google Workspace tenant policy requires IT admin grant (`AADSTS65002`).
  - Card badge shows `"Admin Approval Needed"`.
  - User provided guidance on contacting workspace administrator.

#### Deduplication & Anti-Collapse Invariant:
Meetings appearing across multiple calendars (e.g., personal Google calendar invite accepted in corporate Outlook calendar) are merged into one unified card:
1. Exact `iCalUId` match within +/- 15 minutes.
2. Identical conference join link (Zoom, Google Meet, Microsoft Teams) within +/- 30 minutes.
3. Recurring meetings sharing identical join URLs on different days NEVER collapse into a single meeting.

---

### 2.4 Quota Metering & Subscription Plan Gating

VOX enforces feature tiers strictly through its verified Entitlement Manager.

| Feature / Limit | Free Tier | Pro Tier (Monthly / Annual) |
|---|---|---|
| Monthly Dictation Words | 5,000 words / month | Unlimited |
| Monthly Meeting Minutes | 90 minutes (max 3 meetings) | Unlimited |
| Meeting Transcripts & AI Summaries | Basic action items | Full executive briefings, action items, mind maps |
| Connected Calendars | 1 account | Unlimited accounts |
| Multilingual Hinglish & Code-Switching | Included | Included |
| Audio Privacy & Local Storage | 100% Local | 100% Local |

#### Gating State Machine:
- `QUOTA_NORMAL`:
  - Sidebar meter displays percentage bar and remaining balance (e.g., `3,420 / 5,000 words used`).
- `QUOTA_EXHAUSTED`:
  - When quota reaches 100%, subsequent dictation or meeting recording attempts emit `upgrade_dialog_requested`.
  - In-memory features pause gracefully with an informative user prompt.
  - No data corruption occurs.
- `UPGRADE_DIALOG`:
  - Displays monthly ($19/mo or ₹1,499/mo) and annual pricing.
  - Click "Upgrade Monthly" debounces double-clicks (1.2s debounce timer) to prevent duplicate browser tabs.
  - Opens single checkout tab in default browser.
  - Background license poller listens for webhook confirmation via Supabase/Razorpay.
  - Upon payment confirmation, sidebar upgrades to `"PRO PLAN"` with glowing badge instantly.

---

## 3. Product Invariants & Non-Negotiable Quality Rules

1. **Local Audio Invariant:** Raw audio from microphone and system loopback NEVER leaves the local machine. All acoustic feature extraction, transcription, and diarization run on local neural networks.
2. **UI Thread Cleanliness Invariant:** No synchronous network calls (HTTP, Graph API, Google API, Supabase) and no heavy file operations are permitted on the Qt main thread. All syncs run on daemon worker threads.
3. **Historical State Isolation Invariant:** Historical errors persisted in SQLite must NEVER override live, healthy runtime status in memory.
4. **Data Persistence Invariant:** Closing the application, restarting Windows, or abrupt process termination must never corrupt the SQLite database. SQLite write-ahead logging (WAL) and automatic migration snapshots are required.
5. **Focus Invariance Invariant:** Activating dictation, opening the floating pill, or rendering alerts must NEVER steal keyboard focus from the user's active document or input field.

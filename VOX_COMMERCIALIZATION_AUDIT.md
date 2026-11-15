# VOX — Commercialisation & Production Readiness Audit

**Audit Date:** 2026-11-15  
**Product:** VOX — Local-First AI Voice Typing & Meeting Intelligence  
**Target Platform:** Windows 10 & 11 (64-bit)  
**Deployment Environment:** Vercel (Web & Serverless Backend) + Supabase (Identity & Ledger) + GitHub Releases (Distribution)  

---

## 1. Executive Summary

VOX is an architectural achievement: a genuine **local-first** Windows productivity assistant that provides real-time voice typing across any application and dual-track meeting recording (microphone + WASAPI loopback) without sending private user audio, transcripts, or meeting recordings to third-party cloud AI vendors.

The commercial objective is:
> **Professional-grade local AI productivity at an accessible price (Free ₹0 / Pro ₹199/month / Pro Annual ₹1,599/year).**

Because local Whisper and local Llama 3.2 execute on the customer's hardware (via NVIDIA CUDA or multi-core CPU int8 inference), **VOX has virtually zero variable marginal AI inference costs per active user**. This creates an extraordinary structural contribution margin, allowing generous Free allowances and an aggressive ₹199/month Pro subscription while remaining highly profitable.

---

## 2. Technical Stack & Repository Architecture

| Component | Technology | Role & Location |
|---|---|---|
| **Desktop Application** | Python 3.13 + PyQt6 6.11 | `src/voxlocal/`, `main.py` |
| **Speech Recognition Engine** | `faster-whisper` + CTranslate2 (int8) | `src/voxlocal/engine/transcriber.py` |
| **Meeting Intelligence / LLM** | `Llama-3.2-1B-Instruct` + CTranslate2 | `src/voxlocal/engine/llm_engine.py`, `meeting_intelligence.py` |
| **Audio Capture** | PyAudioWPatch (WASAPI Loopback + Mic) | `src/voxlocal/audio/devices.py`, `dictation_recorder.py`, `meeting_recorder.py` |
| **Screen Capture** | PyAV (FFmpeg H.264 MP4 @ 15 FPS) | `src/voxlocal/video/meeting_capture.py` |
| **Local Storage** | SQLite 3 (WAL mode, multi-thread safe) | `data/voxlocal.db`, `src/voxlocal/storage/database.py` |
| **OS Security / Vault** | Win32 DPAPI (`CryptProtectData`) | `src/voxlocal/calendar/auth_service.py` |
| **Desktop Hotkeys & Insertion** | Win32 `WH_KEYBOARD_LL`, `SendInput` | `src/voxlocal/win32/hotkeys.py`, `text_inserter.py` |
| **Floating UI Surface** | Custom Qt QWidget (Wispr Flow replica) | `src/voxlocal/ui/floating_pill.py` |
| **Website & Landing Page** | HTML5 / CSS3 / Vanilla JS | `docs/index.html` |
| **Web Hosting & Edge API** | Vercel Serverless (Node.js runtime) | `vercel.json`, `api/` |
| **User Identity & Ledger** | Supabase (PostgreSQL + RLS + GoTrue Auth)| Planned in Phase 5 |
| **Payment Gateway** | Razorpay (Subscriptions & Payment Links) | Planned in Phase 8 |
| **Packaging & Distribution** | PyInstaller + Inno Setup + GitHub Releases| `VoxLocal.spec`, `packaging/`, `release_artifacts/` |

---

## 3. Product Capability Inventory & Commercial Readiness

Every VOX capability is inventoried and classified below according to strict commercial standards:

| Capability | Module & Implementation | Classification | Processing Location | Marginal Cost | Commercial Release Readiness | Subscription Gating Approach |
|---|---|---|---|---|---|---|
| **Zero-Click Voice Typing** | `dictation_recorder.py`, `streaming_typer.py` | **Implemented** | 100% Local (CPU/GPU) | ₹0 | **Ready** | Quota: 3,000 words/week for Free; Unlimited for Pro |
| **Universal App Insertion** | `win32/text_inserter.py` (`SendInput` + Clipboard fallback) | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro (Never restrict cursor typing) |
| **Multilingual Support (en, hi, hi_latn)** | `engine/transcriber.py`, `dictation_processor.py` | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Filler Word & Self-Correction Removal** | `engine/dictation_processor.py` | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Custom Vocabulary & Snippets** | `storage/database.py`, `core/vocabulary_manager.py` | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Wispr-Parity Floating Toggle Bar** | `ui/floating_pill.py` (Dual capsule, hover chevrons, mistake safeguard) | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Dual-Track Meeting Audio Recording** | `audio/meeting_recorder.py` (Mic + Loopback WASAPI) | **Implemented** | 100% Local | ₹0 | **Ready** | Quota: 180 min/wk & 5 mtgs/wk on Free; Unlimited on Pro |
| **Meeting Screen Video Capture (1080p)** | `video/meeting_capture.py` (H.264 MP4 @ 15 FPS) | **Implemented** | 100% Local | ₹0 | **Ready** | Included in session allowance |
| **Speaker Separation / Diarization** | `engine/transcriber.py` (Track split + pitch/spectral clustering) | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Extractive Meeting Summaries & MoM** | `engine/meeting_intelligence.py` | **Implemented** | 100% Local | ₹0 | **Ready** | Included in Free & Pro |
| **Generative LLM Synthesis (Llama-3.2-1B)** | `engine/llm_engine.py` (CTranslate2 INT8) | **Implemented** | 100% Local | ₹0 | **Ready** | Free: 10 queries/wk; Pro: Unlimited |
| **Unified Calendar (Google, Outlook, Zoom)** | `calendar/unified_engine.py`, `graph_provider.py`, `google_provider.py` | **Implemented** | Local Broker + Provider APIs | Network only | **Ready** | Included in Free & Pro |
| **Central Usage Metering Engine** | Requires implementation in Phase 4 | **Partially Implemented** | Local Meter + Server Ledger | ₹0 | **Phase 4 Deliverable** | Enforces 3k words, 180 mins, 5 mtgs, 10 Ask Vox |
| **Google Authentication (Web + Desktop)** | Web: Supabase; Desktop: DPAPI + PKCE loopback | **Partially Implemented** | Cloud Auth + Local DPAPI | ₹0 | **Phase 6 Deliverable** | Binds user identity to entitlement ledger |
| **Payment & Subscription Integration** | Razorpay Checkout + Webhooks + Vercel Functions | **Partially Implemented** | Razorpay + Serverless Webhook | Payment gateway fee | **Phase 8 Deliverable** | Auto-provisions Pro entitlement upon payment |
| **Asymmetric Entitlement Token Engine** | Signed JWT/Ed25519 token + 72h offline lease | **Partially Implemented** | Server Signer + Client Verifier | ₹0 | **Phase 7 Deliverable** | Enforces Pro features & bounded offline usage |

---

## 4. Architectural Gap Analysis

To convert the stand-alone desktop software and static Vercel landing page into an integrated commercial product, the following 5 gaps must be closed:

1. **Identity Unification:**
   - *Current state:* Desktop calendar uses local DPAPI for OAuth tokens, but desktop has no user account identity.
   - *Required state:* User logs in via Google (Supabase Auth) on the website or desktop. Desktop uses a local loopback callback (`http://127.0.0.1:8400/callback`) with PKCE to receive an authenticated session token, securely saved in Windows DPAPI.

2. **Central Entitlement & Usage Metering:**
   - *Current state:* Desktop executes without any usage quota checks.
   - *Required state:* A unified `UsageMeteringEngine` tracks final committed words, meeting duration, session count, and Ask VOX queries. Quotas reset on **Monday 00:00 UTC (05:30 IST)**. Server issues signed entitlement tokens with a 72-hour bounded offline lease.

3. **Privacy-Preserving Ledger:**
   - *Current state:* Transcripts and audio remain strictly local in SQLite (`data/voxlocal.db`).
   - *Required state:* **Strict non-negotiable rule:** Never upload raw speech or transcripts. Usage synchronization transmits only coarse numerical counters (`{ metric: 'dictation_words', amount: 48, event_id: 'uuid' }`) to the backend ledger.

4. **Razorpay Subscription & Webhook Pipeline:**
   - *Current state:* No payment hooks on the website or backend.
   - *Required state:* Vercel Serverless API routes (`/api/checkout/create-order`, `/api/webhooks/razorpay`) process payments, verify webhook signatures (`X-Razorpay-Signature`), insert idempotent transaction records into Supabase, and upgrade customer entitlements immediately.

5. **Customer Dashboard & Website Upgrades:**
   - *Current state:* Landing page at `vox-voice-assistant.vercel.app` has hero, speed demo, capabilities, workflows, FAQ, and download links, but no pricing table, login, or billing dashboard.
   - *Required state:* Add interactive Pricing section, Google Sign-in header modal, User Account & Billing Dashboard (`docs/account.html`), and legal pages (`privacy.html`, `terms.html`, `refunds.html`, `support.html`).

---

## 5. Architectural Recommendation

```mermaid
flowchart TD
    subgraph Client["Windows Desktop Application (Local-First)"]
        A[Microphone / WASAPI Audio] --> B[faster-whisper & Llama-3.2]
        B --> C[Win32 Text Insertion & SQLite DB]
        C --> D[Central Usage Metering Engine]
        D -->|Numeric Aggregates Only| E[Entitlement Client]
        E -->|Encrypted via DPAPI| F[Local Entitlement Cache]
    end

    subgraph Web["Vercel Production (vox-voice-assistant.vercel.app)"]
        G[Landing & Pricing Page] --> H[User Account Dashboard]
        H --> I[Razorpay Checkout SDK]
        J["/api/entitlements"] --> E
        K["/api/usage/sync"] <-- D
        L["/api/webhooks/razorpay"] <-- M[Razorpay Webhook Event]
        N["/api/checkout/create-order"] <-- H
    end

    subgraph Backend["Supabase Managed PostgreSQL"]
        O[Supabase Auth - Google OAuth]
        P[profiles & subscriptions]
        Q[usage_periods & usage_events]
        R[entitlements & devices]
    end

    O <--> H
    O <--> E
    J <--> P
    K <--> Q
    L <--> P
    N <--> P
```

This architecture guarantees:
- **100% Privacy:** Raw audio and meeting notes never touch Vercel or Supabase.
- **Resilience:** Desktop functions offline for up to 72 hours via signed local lease.
- **Economic Viability:** Zero recurring model inference costs for VOX.
- **Instant Activation:** Pro unlocks on desktop without restarting or reinstalling.

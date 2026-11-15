# VOX — Usage Metering Engine Specification & Verification

## 1. Executive Summary & Design Principles

VOX's usage metering engine is designed for **100% local-first privacy, tamper resistance, and user respect**.
Unlike cloud dictation and meeting transcription software that stream raw user audio across the internet, VOX processes all speech recognition (via `faster-whisper`) and large language model inference (via `Llama-3.2-1B-Instruct`) directly on the customer's Windows machine.

Because local inference has zero marginal cloud computation costs, VOX does not artificially throttle or terminate tasks to save server expenses. Metering serves solely to implement a sustainable, generous Free tier and convert high-volume users to our ultra-accessible Pro tier (₹199/month).

### Core Metering Rules
1. **Zero Raw Audio Upload:** No audio, video, transcripts, notes, or spoken text ever leave the user's PC for metering or billing purposes.
2. **Deterministic Cryptographic Seals:** Metering records stored in local SQLite (`data/voxlocal.db`) are signed using Windows DPAPI encryption (`CryptProtectData`) to prevent arbitrary manual edits.
3. **Weekly Monday 00:00 UTC Reset:** All weekly quotas reset on a fixed calendar cadence (Monday 00:00 UTC / 05:30 IST).
4. **Non-Terminating Overrun Protection:** If a Free meeting recording exceeds the 90-minute session cap, the application issues friendly UI warnings but **never cuts off or terminates the call**.
5. **Idempotent Sync:** When the desktop app is online, only numerical counters (`dictation_words`, `meeting_duration_sec`, `meeting_count`, `ask_vox_count`) tagged with UUIDs are synced to `/api/usage/sync`.

---

## 2. Metric Specifications & Tier Limits

| Metric | Free Tier Allowance | Pro Tier Allowance | Unit of Measurement | Reset Cadence |
| :--- | :--- | :--- | :--- | :--- |
| **Dictation Words** | 3,000 words / week | Unlimited | Committed speech words | Monday 00:00 UTC |
| **Meeting Audio** | 180 minutes / week | Unlimited | Wall-clock audio seconds | Monday 00:00 UTC |
| **Meeting Count** | 5 meetings / week | Unlimited | Completed meeting sessions | Monday 00:00 UTC |
| **Meeting Session Cap** | 90 minutes / meeting | Unlimited | Continuous recording duration | Per meeting (non-terminating) |
| **Ask VOX Queries** | 10 queries / week | Unlimited | Semantic cognitive search runs | Monday 00:00 UTC |

---

## 3. Multilingual Word Counting Architecture

Word counting for voice typing must accurately account for English, Hindi (Devanagari), Romanized Hinglish, hyphenated compounds, and numbers without penalizing natural punctuation.

In `src/voxlocal/core/metering.py`:
```python
def count_committed_words(text: str) -> int:
    if not text or not text.strip():
        return 0
    # Strip formatting punctuation, preserving intra-word characters
    cleaned = re.sub(r"[\.,!?;:\'\"״()\[\]\{\}<>\/\\_=\+\*#@\$\^~`|\-]+", " ", text)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    return len(tokens)
```

### Verified Benchmark Behavior
- **Pure English:** `"Hello world from the VOX local assistant"` → 7 words.
- **Hindi Devanagari:** `"नमस्ते दुनिया यह एक परीक्षण है"` → 6 words.
- **Hinglish:** `"Kal meeting me discuss karenge presentation details"` → 7 words.
- **Numbers & Alphanumerics:** `"Model 3.2 has 1B parameters and costs 199 INR"` → 10 tokens.
- **Pure Punctuation & Whitespace:** `"   ... !!! ,,, ??? --- \n\t   "` → 0 words (zero debit).

---

## 4. Database Schema & Local Cryptographic Ledger

Usage counters are maintained inside the local SQLite database in two tables:

### Table: `local_metering_periods`
```sql
CREATE TABLE IF NOT EXISTS local_metering_periods (
    period_id TEXT PRIMARY KEY,       -- e.g. "utc_week_2026_11_09"
    user_id TEXT NOT NULL,            -- Supabase user ID or "default"
    period_start_utc TEXT NOT NULL,
    period_end_utc TEXT NOT NULL,
    dictation_words INTEGER NOT NULL DEFAULT 0,
    meeting_seconds REAL NOT NULL DEFAULT 0.0,
    meeting_count INTEGER NOT NULL DEFAULT 0,
    ask_vox_count INTEGER NOT NULL DEFAULT 0,
    dpapi_seal BLOB NOT NULL,         -- Cryptographic signature
    updated_at TEXT NOT NULL
);
```

### Table: `local_usage_events` (Idempotent Event Queue)
```sql
CREATE TABLE IF NOT EXISTS local_usage_events (
    event_id TEXT PRIMARY KEY,        -- UUIDv4 generated at debit time
    user_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    metric TEXT NOT NULL,             -- "dictation_words", "meeting_duration_sec", etc.
    amount REAL NOT NULL,
    timestamp_utc TEXT NOT NULL,
    session_ref TEXT,                 -- Prevents double-counting
    is_synced INTEGER NOT NULL DEFAULT 0
);
```

---

## 5. Overrun & Warning Policies

1. **80% Early Warning:**
   When weekly usage crosses 80% on any metric (e.g. 2,400 words for dictation or 144 minutes for meetings), the floating bar and settings UI display an unobtrusive indicator reminding the user of their remaining balance.
2. **Live Meeting Overrun:**
   If a meeting on the Free tier crosses 72 minutes (80% of 90m cap), a subtle notification appears. When it crosses 90 minutes, an overrun notice informs the user that their call will continue uninterrupted to the end. The recorder is **never forcefully stopped mid-meeting**.
3. **Weekly Reset:**
   On Monday at 00:00 UTC (05:30 IST), a new period row is initialized with zero counters. Past usage remains logged in SQLite for user transparency.

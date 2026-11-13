# Requirement Catalogue & Data-Ownership Map: Phase 6 Enterprise Business Logic Integrity (Revised)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Revised Specification for User Approval  
**Operating Mode**: STRICTLY READ-ONLY ARCHITECTURAL MAPPING  

---

## 1. Revised Requirement Catalogue

| # | Focus Area | Requirement ID | Requirement Description | Revised Specification & Semantics | Target Module & File |
|:---|:---|:---|:---|:---|:---|
| 1 | **Timezone Architecture** | REQ-TZ-01 | **Nullable Legacy Timezones** | Historical meetings retain `timezone = NULL` and `utc_offset_min = NULL`. Zero assumed UTC or invented timezones assigned retroactively. | `src/voxlocal/storage/database.py`, `src/voxlocal/core/config.py` |
| 2 | **Timezone Architecture** | REQ-TZ-02 | **IANA Timezone Capture for New Sessions** | New recordings capture configured IANA timezone identifier (e.g. `'America/New_York'`) and local UTC offset at recording start. | `src/voxlocal/core/config.py`, `src/voxlocal/core/controller.py` |
| 3 | **Relative Dates** | REQ-RD-01 | **Structured Relative Date Resolution** | Temporal phrases ("by next Friday", "tomorrow") resolve to ISO calendar dates (`YYYY-MM-DD`) using source meeting timestamp. | `src/voxlocal/engine/meeting_intelligence.py` |
| 4 | **Relative Dates** | REQ-RD-02 | **Deterministic Uncertainty Retention** | Retain original spoken expression, resolved date, source timestamp, timezone used, resolution status, and uncertainty reason. Zero invented dates. | `src/voxlocal/engine/meeting_intelligence.py`, `src/voxlocal/storage/database.py` |
| 5 | **Decision Evolution** | REQ-DEC-01 | **Decoupled Decision Status vs Evolution** | Separate current business status (`proposed`, `confirmed`, `rejected`, `unresolved`, legacy `unclassified`) from evolution relationship (`new`, `repeated`, `superseded`, `reversed`). | `src/voxlocal/storage/database.py`, `src/voxlocal/engine/meeting_intelligence.py` |
| 6 | **Decision Evolution** | REQ-DEC-02 | **Order-Independent Chronological Comparison** | `compare_meetings(m1, m2)` and `(m2, m1)` sort chronologically into `(m_early, m_later)`, outputting `#early vs #later` and accurate evolution deltas. | `src/voxlocal/engine/meeting_intelligence.py`, `src/voxlocal/ui/components.py` |
| 7 | **Decision Evolution** | REQ-DEC-03 | **Historical Decision Preservation** | Earlier decisions remain intact with supporting evidence when later meetings supersede or reverse them. Later meetings reference `supersedes_id` without overwriting history. | `src/voxlocal/storage/database.py`, `src/voxlocal/engine/meeting_intelligence.py` |
| 8 | **Action Item Lifecycle** | REQ-ACT-01 | **Universal User-Edit Protection** | Action items with `user_edited = 1` or `is_manual = 1` are protected across report regeneration regardless of status (`pending`, `completed`, `cancelled`, `blocked`, `reopened`). | `src/voxlocal/storage/database.py`, `src/voxlocal/core/controller.py` |
| 9 | **Action Item Lifecycle** | REQ-ACT-02 | **Conservative Reconciliation Provenance** | Reconcile AI-regenerated tasks with existing tasks using source evidence and context rather than loose fuzzy text similarity. Prevent bulk deletion. | `src/voxlocal/storage/database.py`, `src/voxlocal/engine/meeting_intelligence.py` |
| 10 | **Action Item Lifecycle** | REQ-ACT-03 | **Persistent Live Markers** | "⚡ Flag Action" and "🔖 Bookmark" persist to `meeting_markers` table; provisional flags do not convert into confirmed commitments without user review. | `src/voxlocal/ui/main_window.py`, `src/voxlocal/storage/database.py` |
| 11 | **Risks & Blockers** | REQ-RSK-01 | **Distinct Risk vs Blocker Semantics** | Table `risks_and_blockers` distinguishes future uncertainties (`risk`: `identified`, `mitigated`, `accepted`, `closed`) from current progress impediments (`blocker`: `active`, `resolved`, `reopened`). | `src/voxlocal/storage/database.py`, `src/voxlocal/engine/meeting_intelligence.py` |
| 12 | **Dashboard KPIs** | REQ-KPI-01 | **Exact Database SQL Aggregates** | Compute `Meetings Recorded`, `Talk Time`, `Open Actions`, `Decisions` via direct SQL queries, eliminating 1,000 / 5,000 in-memory caps. | `src/voxlocal/ui/main_window.py`, `src/voxlocal/storage/database.py` |
| 13 | **Dashboard KPIs** | REQ-KPI-02 | **Validated Status Filtering** | Filter `status = 'completed'` for total meetings and talk time. Align open action definition (`status = 'pending'`). | `src/voxlocal/ui/main_window.py`, `src/voxlocal/storage/database.py` |
| 14 | **Report Templates** | REQ-TPL-01 | **Zero Fact-Fabrication** | Remove hardcoded affirmative statements ("sign-off completed", "terms accepted"). Truthfully display absence of data when records are empty. | `src/voxlocal/ui/main_window.py` |
| 15 | **Report Templates** | REQ-TPL-02 | **Export Template Fidelity** | Serializers (`to_markdown`, `to_txt`) format exports according to active presentation template rather than raw internal fields. | `src/voxlocal/ui/main_window.py`, `src/voxlocal/storage/exporter.py` |
| 16 | **Client Review Export** | REQ-EXP-01 | **Safe-Content Export & Redaction** | Automatically suppress and redact internal `notes` from Client Review exports. | `src/voxlocal/storage/exporter.py` |
| 17 | **Client Review Export** | REQ-EXP-02 | **Export Preview & Confirmation Safeguard** | Generate export preview before saving or copying. Content sensitivity scanner identifies warning candidates; explicit user confirmation required if uncertain. | `src/voxlocal/storage/exporter.py`, `src/voxlocal/ui/main_window.py` |
| 18 | **Stepper Integrity** | REQ-STP-01 | **Honest Error Emission** | Emit honest failure state with failed stage index; do not emit deceptive `"idle"` success signal on worker exception. | `src/voxlocal/core/controller.py`, `src/voxlocal/ui/main_window.py` |
| 19 | **Stepper Integrity** | REQ-STP-02 | **Stage 2 Transcript Persistence** | Commit Whisper transcript segments to SQLite immediately after Stage 2 so Stage 3 diarization crash preserves transcript. | `src/voxlocal/core/controller.py` |
| 20 | **Stepper Integrity** | REQ-STP-03 | **Cooperative Cancellation** | Check `_cancel_finalization_event` between stages to halt cleanly and mark session `'interrupted'`. | `src/voxlocal/core/controller.py` |
| 21 | **Ask Vox Scoping** | REQ-AVX-01 | **Direct Meeting ID Passing** | Pass `meeting_id=self._selected_meeting_id` directly in in-report search, eliminating fragile title regex prefixing. | `src/voxlocal/ui/main_window.py`, `src/voxlocal/engine/meeting_intelligence.py` |
| 22 | **Ask Vox Scoping** | REQ-AVX-02 | **Zero-Match Isolation & Quarantine** | Zero citation matches returns isolated message without leaking unrelated meeting titles. Quarantine filter guarantees 100% of chunks match scoped meeting. | `src/voxlocal/engine/meeting_intelligence.py` |
| 23 | **Ask Vox Scoping** | REQ-AVX-03 | **Uncapped Scoped Meeting Lookup** | Scoped queries fetch target session directly via `get_meeting()` without 250-meeting catalog truncation. | `src/voxlocal/engine/meeting_intelligence.py` |
| 24 | **SQLite Migrations** | REQ-MIG-01 | **WAL-Safe Snapshot & Recovery** | `PRAGMA wal_checkpoint(TRUNCATE)` and SQLite Online Backup API. Verified via `PRAGMA integrity_check`. On failure, rollback, preserve both copies, stop safely. | `src/voxlocal/storage/database.py` |
| 25 | **Sequential Test QA** | REQ-TST-01 | **Win32 Clipboard Sequential Pass** | Resolve OLE clipboard race in `test_meeting_studio_phase5_advanced_intelligence.py` via event loop pumping and retry helper, achieving clean 98/98 sequential pass. | `tests/test_meeting_studio_phase5_advanced_intelligence.py`, `src/voxlocal/ui/components.py` |

---

## 2. Business Logic & Data-Ownership Map

```
┌────────────────────────────────────────────────────────────────────────┐
│                          PRESENTATION LAYER                            │
│  MainWindow (src/voxlocal/ui/main_window.py)                           │
│  Components & Dialogs (src/voxlocal/ui/components.py)                  │
│                                                                        │
│  - Owns: Dashboard KPI widgets (val_m, val_h, val_a, val_d)            │
│  - Owns: Meeting processing card stepper (VoxProcessingProgressCard)   │
│  - Owns: Template combo & summary view (txt_summary)                   │
│  - Owns: In-report Ask Vox direct ID passing (txt_in_report_ask)       │
│  - Owns: Live flag and bookmark buttons -> meeting_markers             │
│  - Owns: Client Review export preview and sensitivity confirmation     │
│  - Owns: Clipboard write event-loop pumping (app.processEvents())      │
└───────────────────────┬──────────────────────────────┬─────────────────┘
                        │                              │
                        ▼                              ▼
┌─────────────────────────────────────────┐  ┌───────────────────────────┐
│              CONTROLLER                 │  │         EXPORTER          │
│  AppController (controller.py)          │  │ TranscriptExporter        │
│                                         │  │ (storage/exporter.py)     │
│  - Owns: Meeting finalization pipeline  │  │                           │
│  - Coordinates: Mux -> Whisper ->       │  │ - Owns: Templated TXT/MD  │
│    Diarization -> MoM                   │  │   file serialization      │
│  - Owns: Early Stage 2 transcript commit│  │ - Owns: Automatic internal│
│  - Owns: Honest failure state emission  │  │   notes redaction         │
│  - Owns: Cooperative cancellation token │  │ - Owns: Sensitive keyword │
│  - Owns: Timezone capture at start      │  │   scanning (warnings)     │
└───────────────────────┬─────────────────┘  └─────────────┬─────────────┘
                        │                                  │
                        ▼                                  │
┌─────────────────────────────────────────────────┐        │
│            INTELLIGENCE & AI ENGINE             │        │
│  MeetingIntelligenceEngine & Pipelines          │        │
│  (engine/meeting_intelligence.py, llm_engine.py)│        │
│                                                 │        │
│  - Owns: Canonical chronological compare_meetings│       │
│  - Owns: Decoupled 7-state decision evolution   │        │
│  - Owns: Structured relative date resolution    │        │
│    with explicit uncertainty retention          │        │
│  - Owns: Pre-meeting briefing anchor citations  │        │
│  - Owns: Strict meeting-scoped Ask Vox RAG      │        │
│    isolation and post-retrieval quarantine      │        │
└───────────────────────┬─────────────────────────┘        │
                        │                                  │
                        ▼                                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        PERSISTENCE LAYER                               │
│  LocalDatabase (src/voxlocal/storage/database.py)                      │
│  AppConfig (src/voxlocal/core/config.py)                               │
│                                                                        │
│  - Owns: AppConfig timezone setting and data_dir property alias        │
│  - Owns: WAL-safe backup creation & integrity verification             │
│  - Owns: Atomic versioned schema migration (_migrate_v6)               │
│  - Owns tables:                                                        │
│    * meetings (timezone NULL for legacy, IANA for new)                 │
│    * decisions (decoupled status + evolution_relationship)            │
│    * action_items (universal user-edit preservation across all statuses│
│      and structured due_date_resolution)                               │
│    * meeting_markers (live flags and bookmarks)                        │
│    * risks_and_blockers (distinct risk vs blocker semantics)           │
│  - Owns: Safe clear_meeting_outcomes() with user-edit preservation     │
│  - Owns: Exact SQL analytical aggregation queries                      │
└────────────────────────────────────────────────────────────────────────┘
```

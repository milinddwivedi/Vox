# Specification Mining & Architecture Report: Milestone 1 Core Meeting Intelligence
**Focus Areas**: 1 (Decision Evolution), 2 (Action Item Lifecycle), 3 (Relative Dates & Timezones), 6 (Stepper Integrity)  
**Agent**: `teamwork_spec_miner` (`spec_miner_m1_core`)  
**Target Path**: `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_core\handoff.md`  
**Reference Document**: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`  
**Timestamp**: 2026-11-02T01:10:00Z  

---

## 1. Executive Summary

This investigation covers the core business logic, schema architecture, controller event sequencing, and user interface state machines for **Focus Areas 1, 2, 3, and 6** in VOX Meeting Studio Phase 6. Operating strictly in read-only discovery mode, the investigation probed code paths, data flows, SQLite schemas, LLM prompts, and existing unit/integration test suites.

### Key Discoveries & Critical Vulnerabilities:
1. **Decision Evolution (Focus Area 1)**:
   - Cross-meeting decision evolution is currently a placeholder. `compare_meetings` merely dumps Meeting A decisions and Meeting B decisions side-by-side with zero semantic or chronological evolution classification.
   - The required chronological evolution states (`proposed`, `confirmed`, `rejected`, `repeated`, `superseded`, `reversed`, `unresolved`) do not exist in the database or comparison engine.
   - The comparison report violates order-independence: passing `(m1, m2)` vs `(m2, m1)` inverts the comparison title, column alignments, topic evolution, and in `_copy_slack` explicitly mislabels earlier meeting decisions as "latest meeting" decisions.
2. **Action Item Lifecycle (Focus Area 2)**:
   - Action item states are hard-limited to binary `pending` and `completed`. The mandatory states `cancelled`, `reopened`, and `blocked` do not exist in the database schema, toggle functions, or UI.
   - **Severe Data Loss Defect**: When a meeting report is re-analyzed or finalized, `clear_meeting_outcomes` runs `DELETE FROM action_items WHERE meeting_id = ? AND status != 'completed'` and `DELETE FROM decisions WHERE meeting_id = ?`. Any manually added tasks, user-edited task descriptions, assigned owners, due dates, or decisions are permanently destroyed.
   - Conflation of live bookmarks and flags: Clicking "⚡ Flag Action" directly injects an item into the `action_items` table as a pending commitment (which is then wiped out upon report finalization), while "🔖 Bookmark" merely writes to a temporary UI feed without persisting.
3. **Relative Dates & Timezones (Focus Area 3)**:
   - Relative deadlines (e.g. "by Friday", "by tomorrow", "next week") are never parsed or resolved into calendar dates. The raw regex matched strings are saved verbatim into SQLite as text.
   - The system possesses zero timezone awareness: `meetings` timestamps are naive local strings (`%Y-%m-%d %H:%M:%S`), `AppConfig` has no timezone configuration, and no standard timezone library (`zoneinfo`) is used.
   - Explicit uncertainty is never retained when an anchor date or timezone is ambiguous; instead, literal strings or unconstrained LLM strings are saved blindly.
4. **Stepper Integrity (Focus Area 6)**:
   - The four-stage stepper (`VoxProcessingProgressCard`) relies on loose string matching in `controller.meeting_state_changed` rather than explicit stage status contracts.
   - **Deceptive Success Defect**: If an exception occurs during finalization (in Stage 2, 3, or 4), the `finally` block in `_finalize_meeting_worker` unconditionally emits `("idle", "Meeting saved & transcribed")`. The UI immediately hides the progress card, completely masking the failure from the user.
   - **Intermediate Artifact Drop**: If Stage 3 (diarization / speaker separation) crashes, the raw transcript segments generated in Stage 2 were never saved to SQLite (line 1744 is reached only after Stage 3 succeeds), resulting in the entire transcription being lost.
   - There is no cooperative cancellation mechanism for meeting finalization.

---

## 2. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Decision Evolution | Side-by-Side Meeting Comparison | Generates comparison table and bullet points for two meetings | `meeting_id_a`, `meeting_id_b` | Dictionary with `comparison_markdown`, `decisions_a`, `decisions_b`, `carry_over_actions` | Raises `ValueError` if either meeting ID not found in SQLite | `src/voxlocal/engine/meeting_intelligence.py:415` |
| 2 | Decision Evolution | Cross-Meeting Strategic Decisions | Retrieves recent decisions across meetings ordered by ID | `limit: int = 50` | `List[Dict[str, Any]]` with meeting titles | Returns empty list if query fails or table empty | `src/voxlocal/engine/meeting_intelligence.py:632` |
| 3 | Decision Evolution | Pre-Meeting Briefing Generator | Synthesizes prior meetings, decisions, and open actions for a topic | `query_or_topic: str` | Dictionary with `briefing_markdown`, `key_decisions`, `open_action_items` | Fallback to latest 5 meetings if topic query matches nothing | `src/voxlocal/engine/meeting_intelligence.py:530` |
| 4 | Decision Evolution | LLM Structured Decision Prompting | Prompts local Llama model for decisions with category, rationale, and quote | Transcript text, duration, platform, title | JSON object containing `decisions` list | Returns `None` on failure, falling back to rule-based regex | `src/voxlocal/engine/llm_engine.py:448` |
| 5 | Decision Evolution | Rule-Based Decision Extraction | Extracted via regex cues: confirmed, tentative, unresolved | Transcript segment items | List of dicts with `category`, `decision`, `timestamp_sec` | Drops decisions under 4 words; drops duplicate strings | `src/voxlocal/engine/meeting_intelligence.py:1673` |
| 6 | Action Item Lifecycle | Add Action Item | Inserts a new action item row into SQLite | `meeting_id`, `description`, `owner`, `due_date`, `status`, `timestamp_sec` | `int` (new row ID or existing ID if exact duplicate) | Ignores empty description, returning 0 | `src/voxlocal/storage/database.py:664` |
| 7 | Action Item Lifecycle | Toggle Action Item Status | Toggles status between `pending` and `completed` | `item_id: int`, optional `new_status` | `str` resulting status | Returns `"pending"` if `item_id` not found | `src/voxlocal/storage/database.py:730` |
| 8 | Action Item Lifecycle | Update Action Item Details | Modifies description, owner, and due date for an action item | `item_id`, `description`, `owner`, `due_date` | `bool` success flag | Returns `False` if cleaned description is empty | `src/voxlocal/storage/database.py:741` |
| 9 | Action Item Lifecycle | Clear Meeting Outcomes (Destructive) | Deletes prior action items and decisions for a meeting | `meeting_id: int`, `preserve_completed: bool` | `None` | Wipes ALL non-completed action items and ALL decisions unconditionally | `src/voxlocal/storage/database.py:651` |
| 10 | Action Item Lifecycle | Live Flag Action Item | Dialog prompt to quickly record an action item while recording | Text description input from user | Injects `action_items` row and appends to feed | Fails silently if not actively recording or active ID null | `src/voxlocal/ui/main_window.py:1929` |
| 11 | Action Item Lifecycle | Live Bookmark Moment | Appends a timestamped bookmark marker to the live transcript feed | Click event during active recording | Appends text `🔖 [BOOKMARK at HH:MM:SS]` to live feed | Toast warning if not recording; does not save to DB | `src/voxlocal/ui/main_window.py:1952` |
| 12 | Relative Dates & Timezones | Regex Due Date Extraction | Extracts temporal phrases from transcript utterances | Raw utterance text | Raw regex string match (e.g. `"By friday"`) | Defaults to `"Not specified"` if no pattern matches | `src/voxlocal/engine/meeting_intelligence.py:1183, 1754` |
| 13 | Stepper Integrity | 4-Stage Finalization Stepper Card | Displays multi-step visual progress card during post-meeting processing | `stage_idx: int`, `message: str` | Progress bar updates (25%, 50%, 75%, 100%) and stage pills | Hides card immediately on transition to `idle` | `src/voxlocal/ui/components.py:1533` |
| 14 | Stepper Integrity | Background Finalization Worker | Orchestrates muxing (Stage 1), Whisper (Stage 2), Diarization (Stage 3), AI MoM (Stage 4) | `meeting_id`, `result`, `video_path` | Saved meeting, segments, and emitted completion signals | Catches exception, sets `status="error"`, but emits `"idle"` in finally | `src/voxlocal/core/controller.py:1673` |

---

## 3. Edge Cases Observed

| # | Feature | Input Scenario | Observed Behavior |
|---|---------|----------------|-------------------|
| 1 | Meeting Comparison (`compare_meetings`) | Swap argument order: `(m2, m1)` where `m1` is earlier than `m2` | Comparison title reads `Session #2 vs #1`; columns are inverted; Slack export in `_copy_slack` labels `m1` decisions as "New/Active" in latest meeting. Zero evolution mapping occurs. |
| 2 | Report Re-Analysis (`clear_meeting_outcomes`) | Meeting with manually edited pending action items or user-added tasks | `clear_meeting_outcomes` deletes all action items where `status != 'completed'`. All manual edits, custom owners, and manual tasks are permanently erased. |
| 3 | Decision Persistence on Regeneration | Meeting with custom or previously curated decisions | `clear_meeting_outcomes` unconditionally executes `DELETE FROM decisions WHERE meeting_id = ?`. All decisions are erased before AI regeneration. |
| 4 | Action Item Status Toggle | Attempting to transition task to `blocked`, `cancelled`, or `reopened` | `toggle_action_item_status` only checks `curr["status"] == "pending"` and toggles to `"completed"`, else `"pending"`. Invalid/unsupported states are inaccessible. |
| 5 | Live Flag Action Item | User flags an action item during recording, then lets meeting finish | Flag is inserted into `action_items` as `pending`. Upon finalization, Stage 4 calls `clear_meeting_outcomes(preserve_completed=True)`, deleting the flagged action item before saving AI items. |
| 6 | Live Bookmark Moment | User clicks "🔖 Bookmark" during active recording | Marker is appended to the UI `QPlainTextEdit` feed. It is never stored in SQLite, so when the meeting is saved or reloaded, the bookmark vanishes completely. |
| 7 | Relative Deadline Resolution | Transcript contains "we must deploy by next Friday" in a meeting recorded Wednesday | Regex matches `"next Friday"`. The string `"Next friday"` is saved directly into SQLite `action_items.due_date`. No calendar date is calculated; no timezone is used. |
| 8 | Ambiguous Deadline | Transcript contains "we should finish this in a couple of weeks" | Regex pattern fails to match; LLM may output "in a couple of weeks" or "Not specified". No explicit uncertainty structure or anchor timestamp reference is maintained. |
| 9 | Stepper Failure in Stage 2/3/4 | Exception raised during Whisper transcription or diarization | Exception caught in controller line 1882; meeting status updated to `"error"`; BUT `finally` block emits `("idle", "Meeting saved & transcribed")`. UI card immediately hides without showing error step. |
| 10 | Intermediate Artifact Preservation | Exception in Stage 3 (`reconcile_meeting_transcript`) | Stage 2 completed and produced transcript segments in local memory, but DB insert (`replace_meeting_segments`) is at line 1744 (after Stage 3). Transcript is lost; meeting is left with 0 segments. |
| 11 | Cooperative Cancellation | User attempts to cancel meeting finalization while Whisper is running | No cancellation token, check, or flag exists in `_finalize_meeting_worker`. Background thread runs to completion or crash; cannot be stopped. |

---

## 4. Deep-Dive Codebase Findings by Focus Area

### Focus Area 1: Decision Evolution

#### Code Architecture & Data Flow
- **Data Model**: `src/voxlocal/storage/database.py:119-127`
  ```sql
  CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id INTEGER NOT NULL,
      decision TEXT NOT NULL,
      rationale TEXT DEFAULT '',
      timestamp_sec REAL DEFAULT 0.0,
      created_at TEXT NOT NULL,
      FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
  );
  ```
  *Defect*: The table lacks `category`, `status`, `evolution_state`, `supersedes_id`, and `is_manual` columns.
- **Extraction & Categorization**:
  - LLM extraction (`src/voxlocal/engine/llm_engine.py:448-450`): Prompt requests `"category": "Confirmed" | "Tentative" | "Unresolved"`.
  - Rule-based extraction (`src/voxlocal/engine/meeting_intelligence.py:1673-1718`): Uses `CONFIRMED_DECISION_PATTERNS`, `TENTATIVE_DECISION_PATTERNS`, `UNRESOLVED_DECISION_PATTERNS`.
  - Formatting into DB (`src/voxlocal/engine/meeting_intelligence.py:1412-1415`): Drops structured category and prepends it as a raw string into `rationale`:
    ```python
    decisions_list = [
        {"decision": d["decision"], "rationale": f"[{d['category']}] {d['evidence_quote']}", "timestamp_sec": d["timestamp_sec"]}
        for d in decisions
    ]
    ```
- **Comparison & Evolution Engine**: `src/voxlocal/engine/meeting_intelligence.py:415-528`
  - Function `compare_meetings(meeting_id_a, meeting_id_b)` claims in its docstring: `- Decision evolution (reaffirmed, revised, newly introduced)`.
  - **Actual Implementation** (lines 481-495):
    It simply prints `Meeting A Decisions ({title_a})` and `Meeting B Decisions ({title_b})` side by side. There is zero matching, comparison, or status tracking.
  - **Order-Dependence Bug**:
    Lines 432-439 determine `m_early` and `m_later`, but only use `swapped` for `open_early_actions`.
    Lines 467-495 format `Meeting A` and `Meeting B` based strictly on parameter order.
    In `src/voxlocal/ui/components.py:1931-1935`:
    ```python
    if dec_b:
        for d in dec_b[:4]:
            slack_lines.append(f"• *[New/Active]* {d.get('decision', '')}")
    else:
        slack_lines.append("• _No new decisions tracked in latest meeting_")
    ```
    If `combo_a` is the later meeting and `combo_b` is the earlier meeting, `dec_b` represents the older meeting's decisions, but they are copied to Slack labeled as "New/Active" in the latest meeting!

#### Milestone 3 Architecture Requirements
1. **Schema Migration**: Add `status` (TEXT DEFAULT 'confirmed'), `evolution_state` (TEXT DEFAULT 'proposed'), `parent_decision_id` (INTEGER NULL), and `user_edited` (INTEGER DEFAULT 0) to `decisions` table.
2. **Evolution State Machine**: Support the 7 explicit chronological states:
   - `proposed`: Decision suggested without definitive closure.
   - `confirmed`: Formally agreed/approved in the meeting.
   - `rejected`: Explicitly rejected or decided against.
   - `repeated`: Re-affirmed/restated from a prior meeting with no change.
   - `superseded`: Replaced by an updated decision on the same topic.
   - `reversed`: Explicitly overturned or retracted from an earlier meeting.
   - `unresolved`: Tabled or deferred due to impasse.
3. **Canonical Chronological Normalization**: `compare_meetings` must sort meetings chronologically into `(m_early, m_later)` regardless of argument order, computing evolution deltas from `m_early` to `m_later` and exposing canonical keys in the output.

---

### Focus Area 2: Action Item Lifecycle

#### Code Architecture & Data Flow
- **Data Model**: `src/voxlocal/storage/database.py:104-114`
  ```sql
  CREATE TABLE IF NOT EXISTS action_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id INTEGER NOT NULL,
      description TEXT NOT NULL,
      owner TEXT DEFAULT 'Unassigned',
      due_date TEXT DEFAULT '',
      status TEXT DEFAULT 'pending', -- 'pending', 'completed'
      timestamp_sec REAL DEFAULT 0.0,
      created_at TEXT NOT NULL,
      FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
  );
  ```
  *Defect*: Comment states `-- 'pending', 'completed'`. Missing states: `cancelled`, `reopened`, `blocked`. Missing fields: `is_manual`, `user_edited`, `evidence_quote`, `priority`.
- **Wiping of Manual Edits**:
  - `src/voxlocal/storage/database.py:651-663`:
    ```python
    def clear_meeting_outcomes(self, meeting_id: int, preserve_completed: bool = True) -> None:
        with self._lock, self._connect() as conn:
            if preserve_completed:
                conn.execute(
                    "DELETE FROM action_items WHERE meeting_id = ? AND status != 'completed'",
                    (meeting_id,),
                )
            else:
                conn.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM decisions WHERE meeting_id = ?", (meeting_id,))
            conn.commit()
    ```
  - `src/voxlocal/core/controller.py:1820`:
    Calls `self.db.clear_meeting_outcomes(meeting_id, preserve_completed=True)` during every post-meeting finalization and re-transcription.
  - *Root Cause of Defect*: Because `clear_meeting_outcomes` deletes all records where `status != 'completed'`, any pending item with user-edited text, reassigned owner, revised deadline, or user-created task is permanently erased.
- **Status Toggle Limitation**:
  - `src/voxlocal/storage/database.py:730-739`:
    `new_status = "completed" if curr["status"] == "pending" else "pending"`
    Hardcoded binary switch prevents setting or cycling to `cancelled`, `blocked`, or `reopened`.
  - `src/voxlocal/ui/main_window.py:4901`:
    `new_status = "completed" if checked else "pending"`
- **Conflation of Live Bookmarks & Flags**:
  - `src/voxlocal/ui/main_window.py:1929-1950`:
    Clicking "⚡ Flag Action" immediately adds a record to SQLite `action_items` with `status="pending"`. This conflates a live cue with a confirmed commitment, which is subsequently destroyed when `clear_meeting_outcomes` runs at the end of the meeting!
  - `src/voxlocal/ui/main_window.py:1952-1960`:
    Clicking "🔖 Bookmark" merely calls `self.live_transcript_feed.appendPlainText(...)`. It never persists to any SQLite table or data structure.

#### Milestone 3 Architecture Requirements
1. **Schema Migration**: Add `status` check constraint supporting (`pending`, `completed`, `cancelled`, `reopened`, `blocked`), `is_manual INTEGER DEFAULT 0`, `user_edited INTEGER DEFAULT 0`, and `evidence_quote TEXT DEFAULT ''` to `action_items`.
2. **Safe Outcome Reconciliation**: Modify `clear_meeting_outcomes` and AI insertion to:
   - NEVER delete items where `is_manual == 1` or `user_edited == 1` or status in (`completed`, `cancelled`, `blocked`).
   - Reconcile AI-extracted action items against existing records by semantic text matching, updating only auto-generated, untouched items.
3. **Dedicated Bookmark/Flag Table or Status**:
   - Store live bookmarks and live flags in a distinct schema (e.g. `meeting_markers` table or `action_items` with `status='provisional_flag'`).
   - Do not promote live flags to confirmed action items without explicit user review.
4. **Evidence-Grounded Task Revival**:
   - An item with status `completed` or `cancelled` must never be reopened in pre-meeting briefings or carry-over without verbatim transcript evidence citation from a newer session.

---

### Focus Area 3: Relative Dates & Timezones

#### Code Architecture & Data Flow
- **Regex Extraction**: `src/voxlocal/engine/meeting_intelligence.py:1183-1186, 1754-1755`
  ```python
  DUE_DATE_PATTERNS = re.compile(
      r"\b(by tomorrow|by friday|by monday|by tuesday|by wednesday|by thursday|by end of day|by eod|by next week|next week|by end of week|by end of sprint|by end of month|in \d+ days?)\b",
      re.IGNORECASE,
  )
  ...
  m_due = cls.DUE_DATE_PATTERNS.search(txt)
  due_date = m_due.group(0).strip().capitalize() if m_due else "Not specified"
  ```
- **Test Evidence**: In `tests/test_meeting_detection_and_intelligence.py:292`:
  `self.assertEqual(milind_action["due_date"], "By friday")`
  The baseline tests specifically assert that the raw string `"By friday"` is preserved as-is.
- **Timezone Absence**:
  - `meetings.created_at` stores naive local time via `datetime.now().strftime("%Y-%m-%d %H:%M:%S")`.
  - Zero imports of `zoneinfo`, `pytz`, or `dateutil` anywhere in `src/voxlocal`.
  - Zero timezone offset or IANA timezone name is persisted.
  - Zero resolution of relative dates exists in the codebase.

#### Milestone 3 Architecture Requirements
1. **Timezone Policy & Schema**:
   - Add `timezone` column to `meetings` (e.g. `Asia/Kolkata`, `America/New_York`, `UTC`).
   - Derive local timezone from system environment (`zoneinfo.ZoneInfo(time.tzname[0])` or Windows registry) when recording starts, with fallback to UTC.
2. **Relative Date Resolver**:
   - Implement `resolve_relative_date(expression: str, anchor_datetime: datetime, timezone: str) -> Tuple[Optional[datetime.date], bool, str]`:
     - Parses relative expressions: `"tomorrow"`, `"next Friday"`, `"by Friday"`, `"by eod"`, `"in N days"`, `"by end of week"`, `"by end of month"`.
     - Calculates target date anchored to meeting `created_at` within the meeting's specified timezone.
   - **Explicit Uncertainty Preservation**:
     - If the expression is vague ("soon", "next sprint", "in a few days") or anchor timestamp/timezone is unverified, set `resolved_date = None`, retain `raw_expression`, and record `uncertainty_reason = "Ambiguous temporal expression"`.

---

### Focus Area 6: Stepper Integrity

#### Code Architecture & Data Flow
- **Stepper Component**: `src/voxlocal/ui/components.py:1533-1605` (`VoxProcessingProgressCard`)
  - 4 stages:
    1. Video Mux (25%)
    2. Whisper (50%)
    3. Diarization (75%)
    4. AI Synthesis (100%)
  - Controlled via `set_stage_message(stage_idx, message)`.
- **Controller Worker Pipeline**: `src/voxlocal/core/controller.py:1673-1897` (`_finalize_meeting_worker`)
  - Stage 1: `screen_recorder.mux_audio_into_video` (line 1684)
  - Stage 2: `transcriber.transcribe_meeting_dual_track` (line 1709)
  - Stage 3: `transcriber.reconcile_meeting_transcript` (line 1722)
  - Stage 4: `LocalMeetingSummarizer.analyze_full` (line 1789)
- **Root Causes of Stepper Defects**:
  1. **Premature / Bogus Success Emit**:
     Lines 1882-1896:
     ```python
     except Exception as exc:
         logger.error("Failed finalizing meeting #%d: %s", meeting_id, exc, exc_info=True)
         self.db.update_meeting(meeting_id, status="error")
         self.set_meeting_lifecycle_state(
             MeetingLifecycleState.FAILED,
             f"Failed finalizing meeting #{meeting_id}: {exc}",
         )
         self.meeting_saved_and_transcribed.emit(meeting_id)
         self.history_updated.emit()
     finally:
         with self._state_lock:
             self._active_meeting_id = None
             self._active_meeting_diarizer = None
             self._meeting_state = "idle"
         self.meeting_state_changed.emit("idle", "Meeting saved & transcribed")
     ```
     *Problem*: The `finally` block ALWAYS emits `"Meeting saved & transcribed"`.
     In `main_window.py:3464-3466`, receiving `state == "idle"` immediately calls `self.meeting_processing_card.hide()`. The user is left believing the meeting was saved successfully, while the meeting is marked as `"error"` in the DB and incomplete.
  2. **Transcript Drop on Diarization Failure**:
     `self.db.replace_meeting_segments(meeting_id, cleaned_segments)` is only called inside the Stage 3 completion block (line 1744). If Stage 3 throws an exception, Stage 2's transcript segments are dropped and never persisted to the database.
  3. **No Cancellation Mechanism**:
     `_finalize_meeting_worker` has no cancellation flag, token, or intermediate abort check.

#### Milestone 3 Architecture Requirements
1. **Stage-Level Persistence (Artifact Preservation)**:
   - Commit Stage 1 video/audio paths immediately to SQLite upon muxing completion.
   - Commit Stage 2 Whisper transcript segments immediately to `transcript_segments` before beginning Stage 3.
   - Update Stage 3 speaker labels incrementally or as a separate pass so transcript text is never lost.
   - If Stage 4 fails, keep status as `"transcribed"` (allowing playback and user review) rather than a total `"error"` wipeout.
2. **Honest Error & Stepper Reporting**:
   - In `finally`, emit `"idle", "Processing failed"` or retain `failed` state if an error occurred.
   - Do NOT emit `"Meeting saved & transcribed"` on failure.
   - `VoxProcessingProgressCard` must display the failed step in red error styling with the error message and a "Retry Finalization" button.
3. **Cooperative Cancellation**:
   - Provide a `cancel_meeting_finalization()` method and check `self._cancel_finalization_requested` between stages.

---

## 5. Data Ownership Matrix

| Focus Area | Domain Entity | Primary Storage Table | Primary Controller / Service Owner | Secondary / Consumer UI Components | Integrity Risks |
|------------|---------------|-----------------------|-----------------------------------|-----------------------------------|-----------------|
| 1 | Strategic Decisions | `decisions` | `MeetingIntelligenceEngine` / `LocalDatabase` | `VoxStructuredDecisionsWidget`, `VoxMeetingComparisonDialog` | No status/evolution column; wiped completely on report re-analysis |
| 1 | Meeting Summary / MoM | `meetings.decisions` (text) | `LocalMeetingSummarizer` / `MeetingMinutesPipeline` | `MainWindow.txt_summary` | De-synchronized from relational `decisions` table |
| 2 | Action Items | `action_items` | `LocalDatabase` / `AppController` | `VoxActionItemMatrixWidget`, `all_actions_table` | Hard-limited to 2 states; all non-completed items wiped on re-analysis |
| 2 | Meeting Commitments | `meetings.action_items` (text) | `LocalMeetingSummarizer` | `MainWindow.txt_summary` | Overwritten on regeneration; de-synchronized from relational table |
| 2 | Live Flags / Bookmarks | `live_transcript_feed` / `action_items` | `MainWindow` | Live Feed QPlainTextEdit | Conflated with confirmed commitments; bookmarks dropped from DB |
| 3 | Relative Due Dates | `action_items.due_date` | `MeetingMinutesPipeline` | `VoxActionItemMatrixWidget` | Stored as unparsed raw regex strings; no calendar resolution |
| 3 | Meeting Timestamp & TZ | `meetings.created_at` | `AppController` / `LocalDatabase` | `MainWindow.lbl_meeting_date` | Naive local timestamp string; zero timezone storage or policy |
| 6 | Finalization Stepper State | Memory (`_current_stage`) | `AppController._finalize_meeting_worker` | `VoxProcessingProgressCard` | String parsing of Qt signal; hidden prematurely on failure |
| 6 | Intermediate Transcript Segments | `transcript_segments` | `LocalWhisperTranscriber` / `LocalDatabase` | `MainWindow.txt_transcript` | Dropped if Stage 3 diarization fails before line 1744 commit |

---

## 6. Defect Root-Cause Register

| Defect ID | Severity | Focus Area | Location | Root Cause Description | Consequence & Impact |
|-----------|----------|------------|----------|------------------------|----------------------|
| DEF-M1-01 | Critical | 2 (Action Items) | `storage/database.py:655` | `clear_meeting_outcomes` runs `DELETE FROM action_items WHERE meeting_id = ? AND status != 'completed'`. | Re-analyzing or regenerating any meeting permanently erases all user manual edits, custom assignees, deadlines, and user-created tasks. |
| DEF-M1-02 | Critical | 1 (Decisions) | `storage/database.py:661` | `clear_meeting_outcomes` runs `DELETE FROM decisions WHERE meeting_id = ?`. | All decisions for a meeting are destroyed unconditionally prior to AI regeneration. |
| DEF-M1-03 | Critical | 6 (Stepper) | `core/controller.py:1896` | `finally` block in `_finalize_meeting_worker` unconditionally emits `("idle", "Meeting saved & transcribed")` regardless of exceptions. | Deceptive UI: Stepper card hides and displays success toast even when transcription or diarization completely failed. |
| DEF-M1-04 | High | 6 (Stepper) | `core/controller.py:1744` | `replace_meeting_segments` is positioned after Stage 3 (`reconcile_meeting_transcript`) instead of Stage 2 (`transcribe_meeting_dual_track`). | If Stage 3 crashes or runs out of RAM, Stage 2's transcript segments are dropped and lost forever. |
| DEF-M1-05 | High | 1 (Decisions) | `engine/meeting_intelligence.py:432-495` | `compare_meetings` does not sort comparisons chronologically and performs no evolution tracking. | Passing meetings in swapped order inverts titles, topic transitions, and mislabels old decisions as new in Slack export. |
| DEF-M1-06 | High | 2 (Action Items) | `storage/database.py:110, 730` | `action_items` schema and toggle method only support `pending` and `completed`. | Mandatory states `cancelled`, `reopened`, and `blocked` cannot be recorded, queried, or tracked. |
| DEF-M1-07 | High | 2 (Action Items) | `ui/main_window.py:1941` | `_on_live_flag_action_clicked` adds directly to `action_items` table as a pending commitment. | In-meeting flags are conflated with confirmed commitments, and then deleted upon meeting finalization by `clear_meeting_outcomes`. |
| DEF-M1-08 | High | 3 (Dates & Timezones) | `engine/meeting_intelligence.py:1754` | `due_date` regex match is saved directly as a raw text string without resolution. | Expressions like "next Friday" remain literal text strings; no calendar date is resolved. |
| DEF-M1-09 | Medium | 3 (Dates & Timezones) | `storage/database.py:59` | `meetings.created_at` stores naive local time; no timezone column or policy exists. | Cannot resolve relative deadlines across timezones or determine absolute deadline timestamps. |
| DEF-M1-10 | Medium | 6 (Stepper) | `core/controller.py:1673` | No cooperative cancellation check or handler exists in `_finalize_meeting_worker`. | User cannot cancel long-running finalization; closing app during finalization leaves inconsistent state. |

---

## 7. Handoff Protocol: 5-Component Report

### Component 1: Observation
1. **Verbatim Code — Manual Edit Deletion**:
   In `src/voxlocal/storage/database.py:654-662`:
   ```python
   if preserve_completed:
       conn.execute(
           "DELETE FROM action_items WHERE meeting_id = ? AND status != 'completed'",
           (meeting_id,),
       )
   else:
       conn.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
   conn.execute("DELETE FROM decisions WHERE meeting_id = ?", (meeting_id,))
   conn.commit()
   ```
2. **Verbatim Code — Stepper Bogus Success**:
   In `src/voxlocal/core/controller.py:1882-1896`:
   ```python
   except Exception as exc:
       logger.error("Failed finalizing meeting #%d: %s", meeting_id, exc, exc_info=True)
       self.db.update_meeting(meeting_id, status="error")
       self.set_meeting_lifecycle_state(
           MeetingLifecycleState.FAILED,
           f"Failed finalizing meeting #{meeting_id}: {exc}",
       )
       self.meeting_saved_and_transcribed.emit(meeting_id)
       self.history_updated.emit()
   finally:
       with self._state_lock:
           self._active_meeting_id = None
           self._active_meeting_diarizer = None
           self._meeting_state = "idle"
       self.meeting_state_changed.emit("idle", "Meeting saved & transcribed")
   ```
3. **Verbatim Code — Raw Due Date Preservation**:
   In `src/voxlocal/engine/meeting_intelligence.py:1754-1755`:
   ```python
   m_due = cls.DUE_DATE_PATTERNS.search(txt)
   due_date = m_due.group(0).strip().capitalize() if m_due else "Not specified"
   ```
   In `tests/test_meeting_detection_and_intelligence.py:292`:
   ```python
   self.assertEqual(milind_action["due_date"], "By friday")
   ```
4. **Verbatim Code — Order-Dependence Inversion in Slack Export**:
   In `src/voxlocal/ui/components.py:1931-1935`:
   ```python
   if dec_b:
       for d in dec_b[:4]:
           slack_lines.append(f"• *[New/Active]* {d.get('decision', '')}")
   else:
       slack_lines.append("• _No new decisions tracked in latest meeting_")
   ```

### Component 2: Logic Chain
- **Step 1**: Observational fact: `clear_meeting_outcomes(mid, preserve_completed=True)` executes `DELETE FROM action_items WHERE meeting_id = ? AND status != 'completed'` (`database.py:656`).
- **Step 2**: Observational fact: `_finalize_meeting_worker` calls `clear_meeting_outcomes` during every meeting finalization and re-transcription (`controller.py:1820`).
- **Step 3**: Deduction: If a user manually edits a pending action item (e.g. changes owner, edits description via `update_action_item`), its status remains `pending`. When the meeting is re-analyzed or finalized, Step 1 unconditionally deletes this item. Therefore, manual edits to pending action items are completely destroyed upon AI report regeneration (DEF-M1-01).
- **Step 4**: Observational fact: `_finalize_meeting_worker` has an `except Exception as exc:` block followed by a `finally:` block that executes `self.meeting_state_changed.emit("idle", "Meeting saved & transcribed")` (`controller.py:1896`).
- **Step 5**: Observational fact: `MainWindow._on_meeting_state` reacts to `state == "idle"` by executing `self.meeting_processing_card.hide()` (`main_window.py:3466`).
- **Step 6**: Deduction: When any failure occurs in Stages 1, 2, 3, or 4 of finalization, the UI emits a success message and hides the stepper card without notifying the user of the failure or the failed step (DEF-M1-03).
- **Step 7**: Observational fact: `replace_meeting_segments` is invoked at line 1744 inside the Stage 3 completion branch. If Stage 3 fails, line 1744 is skipped, and execution branches to line 1882 (`except Exception`).
- **Step 8**: Deduction: The transcript segments produced by Stage 2 are held only in local variable `segments` and are never committed to SQLite. Therefore, downstream diarization failures destroy valid upstream transcription artifacts (DEF-M1-04).

### Component 3: Caveats
- No modifications have been made to any source files, schema definitions, or test fixtures during this investigation, adhering strictly to read-only constraints.
- Investigation evaluated local Whisper models and offline LLM prompts based on codebase static structure and verified unit tests. Full Llama neural inference was not executed directly in this turn to avoid unneeded GPU/CPU load during read-only discovery.

### Component 4: Conclusion
The architecture has strong foundations (7-stage pipeline in `MeetingMinutesPipeline`, multi-channel DSP, and SQLite schema), but contains critical business logic gaps and data-loss vulnerabilities in the four focus areas:
1. Decision evolution does not exist across meetings; comparison is order-dependent.
2. Action item lifecycle is binary (`pending`/`completed`); re-analysis deletes all manual edits; live flags and bookmarks are improperly handled.
3. Relative dates are unparsed text strings; timezones are absent.
4. Finalization stepper falsely reports success on exceptions and drops intermediate transcription artifacts upon downstream failure.

All defects are clearly locatable to specific file lines and have straightforward, minimally invasive architectural remediations for Milestone 3.

### Component 5: Verification Method
1. **Inspection Commands**:
   - Inspect `database.py:651-663` to verify unconditional deletion of non-completed items and decisions.
   - Inspect `controller.py:1882-1896` to verify the deceptive `finally` emit.
   - Inspect `controller.py:1744` to verify delayed segment persistence after Stage 3.
   - Inspect `components.py:1931-1935` to verify order-dependent decision labeling.
2. **Test Command**:
   Execute the baseline clean test suite in powershell:
   `python scratch/run_suites_clean.py`
3. **Invalidation Conditions**:
   The findings in this report would be invalidated if:
   - A schema migration already introduced `is_manual` and protected items in `clear_meeting_outcomes`.
   - `_finalize_meeting_worker` had an error-specific state emission preventing the `finally` success string.
   Neither condition exists in the current codebase.

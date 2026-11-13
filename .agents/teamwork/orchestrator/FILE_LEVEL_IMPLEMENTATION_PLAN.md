# File-Level Implementation Plan: Phase 6 Enterprise Business Logic Integrity (Final)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Final Corrected Specification for User Review & Explicit Approval  
**Integrity Mode**: Pre-Release Development (Local-Only, Zero Cloud AI, Zero Telemetry)  
**Database Context**: Development & Test SQLite Database (`data/voxlocal.db`)  

---

## 1. Operating Principles & Architectural Foundation

1. **Pre-Release Development Environment**:
   - `data/voxlocal.db` is the local development/test database, not a live production customer database.
   - Golden benchmark evidence (Meeting IDs 64 & 65, ~941 MB audio/video media) and historical acoustic assets (> 600 MB) are strictly preserved.
   - Automated tests will strictly execute against ephemeral temporary storage (`config.storage_dir = temp_dir`), never mutating `data/voxlocal.db`.
   - **Zero automatic deletions or cleanup in Phase 6**: Any test record cleanup is decoupled as a separate, future proposal. Test fixtures are corrected directly (`self.config.storage_dir = str(test_dir)`).
2. **Nullable Legacy Timezones**:
   - Historical meetings retain `timezone = NULL` and `utc_offset_min = NULL`. Zero assumed UTC or invented timezones.
   - New meetings record the configured IANA timezone identifier and local UTC offset.
3. **Decoupled Decision Lifecycle & Canonical Naming**:
   - Separate business status (`status`: `'proposed'`, `'confirmed'`, `'rejected'`, `'unresolved'`, legacy `'unclassified'`) from historical evolution relationship (`evolution_relationship`: `'new'`, `'repeated'`, `'superseded'`, `'reversed'`).
   - The term `evolution_relationship` is used canonically throughout.
   - Foreign key chain protection: `supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT`. Earlier decisions and their quotes are never overwritten or deleted.
4. **Universal Action Item Preservation Across All Statuses**:
   - Any task with `user_edited = 1` or `is_manual = 1` is protected across report regenerations, regardless of status (`pending`, `completed`, `cancelled`, `blocked`, `reopened`).
   - `due_date_resolution` explicitly serializes relative date resolution metadata and uncertainty reasons.
5. **Canonical Dashboard Status Definitions**:
   - Single definition of open work across dashboard, reports, matrix, and analytics:
     - `pending`: Open
     - `blocked`: Open (with separate blocked indicator)
     - `reopened`: Open
     - `completed`: Closed
     - `cancelled`: Excluded from open work
     - Direct SQL: `WHERE status IN ('pending', 'blocked', 'reopened')`.
6. **Safe-Content Client Review Exports**:
   - Internal notes (`meeting['notes']`) are automatically excluded from Client Review exports.
   - Export preview is displayed in UI before saving or copying.
   - Sensitive keyword scanner provides warning candidates with explicit confirmation safeguards.
7. **Complete Risk vs Blocker Data Model**:
   - Table `risks_and_blockers` distinguishes future uncertainties (`risk`) from current progress impediments (`blocker`). Resolved items are permanently retained with `resolved_at` timestamps.
8. **WAL-Safe SQLite Migration & Safe Recovery**:
   - Pre-migration backup uses `PRAGMA wal_checkpoint(TRUNCATE)` and SQLite Online Backup API.
   - Backup integrity verified with `PRAGMA integrity_check` before applying migrations.
   - Versioned migrations (`PRAGMA user_version = 6`). On failure, rollback, preserve both database copies, stop safely, and notify user.
9. **Sequential Test Race Fix in Test Suite**:
   - Fix OLE clipboard contention in test code (`tearDown()` cleanup + bounded test retry helper). Production handlers remain clean without event-loop re-entry. Assertions remain strict and unweakened.

---

## 2. Exclusive File Ownership & Detailed Change Plan

```
┌────────────────────────────────────────────────────────┬────────────────────────────────────────────┐
│ File Path                                              │ Owning Subagent / Role                     │
├────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
│ src/voxlocal/storage/database.py                       │ Worker 1: Storage & Schema Lead            │
│ src/voxlocal/core/config.py                            │ Worker 1: Storage & Schema Lead            │
│ src/voxlocal/engine/meeting_intelligence.py            │ Worker 2: Intelligence & RAG Lead          │
│ src/voxlocal/engine/llm_engine.py                      │ Worker 2: Intelligence & RAG Lead          │
│ src/voxlocal/core/controller.py                        │ Worker 3: Pipeline & Controller State Lead │
│ src/voxlocal/ui/main_window.py                         │ Worker 4: UI, Stepper & Presentation Lead  │
│ src/voxlocal/ui/components.py                          │ Worker 4: UI, Stepper & Presentation Lead  │
│ src/voxlocal/storage/exporter.py                       │ Worker 5: Exporter & Privacy Lead          │
│ tests/test_meeting_studio_phase5_advanced_intelligence │ Worker 6: Test & Integration QA Lead       │
│ tests/test_meeting_studio_enterprise_phase2.py         │ Worker 6: Test & Integration QA Lead       │
│ tests/test_cross_feature_coexistence.py                │ Worker 6: Test & Integration QA Lead       │
│ tests/test_phase6_enterprise_integrity.py              │ Worker 6: Test & Integration QA Lead       │
│ tests/test_phase6_migration_and_compatibility.py       │ Worker 6: Test & Integration QA Lead       │
└────────────────────────────────────────────────────────┴────────────────────────────────────────────┘
```

---

### 2.1 Track 1: Storage & Schema Migration (Worker 1)
**Files**: `src/voxlocal/storage/database.py`, `src/voxlocal/core/config.py`

1. **`src/voxlocal/core/config.py`**:
   - Add `timezone: str = "UTC"` to `AppConfig` (default user preference for new recordings).
2. **`src/voxlocal/storage/database.py`**:
   - **WAL-Safe Snapshot**: Implement `create_verified_snapshot(backup_dir: Path) -> Path` executing `PRAGMA wal_checkpoint(TRUNCATE)`, `conn.backup()`, and `PRAGMA integrity_check`.
   - **Schema Migration `_migrate_v6`**:
     - Check `PRAGMA user_version`. If `< 6`, begin transaction:
       - Alter `meetings`: add `timezone TEXT NULL` and `utc_offset_min INTEGER NULL` (both `NULL` for legacy records).
       - Alter `decisions`: add `status TEXT NOT NULL DEFAULT 'unclassified'`, `category TEXT NOT NULL DEFAULT 'General'`, `evolution_relationship TEXT NOT NULL DEFAULT 'new'`, `supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT`, `evidence_quote TEXT DEFAULT ''`, `is_manual INTEGER DEFAULT 0`, `user_edited INTEGER DEFAULT 0`.
       - Alter `action_items`: add `due_date_resolution TEXT DEFAULT ''`, `evidence_quote TEXT DEFAULT ''`, `is_manual INTEGER DEFAULT 0`, `user_edited INTEGER DEFAULT 0`.
       - Create `meeting_markers` table (`flag_action`, `bookmark`).
       - Create `risks_and_blockers` table (`risk`, `blocker` with type-specific status constraints and `resolved_at`).
       - Set `PRAGMA user_version = 6`.
       - Commit transaction.
   - **Universal User Edit Preservation in `clear_meeting_outcomes`**:
     ```python
     def clear_meeting_outcomes(self, meeting_id: int, preserve_completed: bool = True) -> None:
         with self._connect() as conn:
             # Protect user-edited and manual items, as well as completed/cancelled/blocked items
             if preserve_completed:
                 conn.execute(
                     """DELETE FROM action_items 
                        WHERE meeting_id = ? 
                          AND is_manual = 0 
                          AND user_edited = 0 
                          AND status = 'pending'""",
                     (meeting_id,),
                 )
             else:
                 conn.execute(
                     """DELETE FROM action_items 
                        WHERE meeting_id = ? 
                          AND is_manual = 0 
                          AND user_edited = 0""",
                     (meeting_id,),
                 )
             # Protect curated decisions AND decisions referenced as historical anchors
             conn.execute(
                 """DELETE FROM decisions 
                    WHERE meeting_id = ? 
                      AND is_manual = 0 
                      AND user_edited = 0
                      AND id NOT IN (SELECT supersedes_id FROM decisions WHERE supersedes_id IS NOT NULL)""",
                 (meeting_id,),
             )
     ```
   - **Multi-State Task & Decision Helpers**:
     - `update_action_item`: sets `user_edited = 1` and updates fields.
     - `toggle_action_item_status`: cycles/updates status between `pending`, `completed`, `cancelled`, `blocked`, `reopened`.
     - `add_marker`, `list_markers`, `delete_marker`.
     - `add_risk_or_blocker`, `list_risks_and_blockers`, `update_risk_or_blocker`.
   - **Canonical SQL KPI Calculations**:
     - In `get_analytics_summary()`:
       - Total completed meetings: `SELECT COUNT(*) FROM meetings WHERE status = 'completed'`
       - Total talk time: `SELECT SUM(duration_sec) FROM meetings WHERE status = 'completed'`
       - Open action items: `SELECT COUNT(*) FROM action_items WHERE status IN ('pending', 'blocked', 'reopened')`
       - Blocked action items: `SELECT COUNT(*) FROM action_items WHERE status = 'blocked'`
       - Completed action items: `SELECT COUNT(*) FROM action_items WHERE status = 'completed'`
       - Confirmed strategic decisions: `SELECT COUNT(*) FROM decisions WHERE status = 'confirmed'`

---

### 2.2 Track 2: Intelligence, Relative Dates & RAG Isolation (Worker 2)
**Files**: `src/voxlocal/engine/meeting_intelligence.py`, `src/voxlocal/engine/llm_engine.py`

1. **Canonical Chronological Normalization in `compare_meetings`**:
   - Compare meeting timestamps: sort into `(m_early, m_later)` regardless of parameter order.
   - Header is always `Session #early vs #later`.
   - Decision evolution matcher uses canonical `evolution_relationship` (`'new'`, `'repeated'`, `'superseded'`, `'reversed'`). Earlier decisions retain their original status and supporting evidence.
2. **Structured Relative Date Resolution in `MeetingMinutesPipeline`**:
   - Parse relative temporal expressions using source meeting timestamp.
   - Store structured resolution JSON in `action_items.due_date_resolution`:
     ```json
     {
       "original_expression": "by next Friday",
       "resolved_date": "2026-11-06",
       "meeting_timestamp": "2026-10-31 02:01:00",
       "timezone_used": null,
       "resolution_status": "deterministic",
       "uncertainty_reason": null
     }
     ```
   - Retain explicit uncertainty when ambiguous (`resolved_date = null`, `uncertainty_reason = "..."`). Zero dates invented.
3. **Pre-Meeting Briefings with Anchor Citations**:
   - In `generate_pre_meeting_briefing`: include meeting date (`YYYY-MM-DD`) and timestamp anchor (`HH:MM:SS`) for all key items.
   - Cross-meeting task reconciliation: verify whether tasks from older sessions were completed in subsequent sessions before presenting them as open carry-overs.
4. **Strict Meeting-Scoped Ask Vox Isolation**:
   - In `query()`: when `meeting_id` is supplied, retrieve the target session directly via `db.get_meeting(meeting_id)`. Bypasses 250-meeting catalog limit.
   - On zero citation matches: output `"No relevant information found in '<Target Meeting Title>'."` Never print or suggest titles of other recorded meetings.
   - Apply post-retrieval quarantine filter ensuring 100% of chunks belong to the scoped `meeting_id`.

---

### 2.3 Track 3: Finalization Stepper Integrity & Cooperative Cancellation (Worker 3)
**File**: `src/voxlocal/core/controller.py`

1. **Intermediate Transcript Persistence**:
   - In `_finalize_meeting_worker`: call `self.db.replace_meeting_segments(meeting_id, final_segments)` immediately after Stage 2 Whisper transcription completes.
   - If Stage 3 diarization or Stage 4 MoM crashes, transcript segments are safely committed and available.
2. **Honest State Emission**:
   - In `_finalize_meeting_worker`: remove deceptive emit in `finally:` block (`self.meeting_state_changed.emit("idle", "Meeting saved & transcribed")`).
   - On exception: emit honest failure state `self.meeting_state_changed.emit("error", f"Processing failed in Stage {failed_stage}: {err}")`.
3. **Cooperative Pipeline Cancellation**:
   - Add `self._cancel_finalization_event = threading.Event()`.
   - Provide `cancel_finalization()` method.
   - Check `self._cancel_finalization_event.is_set()` between Stage 1, Stage 2, Stage 3, and Stage 4. On cancellation, mark meeting status as `'interrupted'` and preserve intermediate artifacts.
4. **Guard Diagnostics Output**:
   - In line 1466, guard against `MagicMock` string conversions when creating diagnostics paths.

---

### 2.4 Track 4: UI, Stepper & Presentation Fidelity (Worker 4)
**Files**: `src/voxlocal/ui/main_window.py`, `src/voxlocal/ui/components.py`

1. **In-Report Ask Vox Direct Scoping**:
   - In `_submit_in_report_ask_query`: pass `meeting_id=self._selected_meeting_id` directly to `controller.query_meeting_memory()`. Eliminate brittle title regex prefixing.
2. **Truthful Presentation Templates**:
   - In `_render_templated_summary`: remove hardcoded affirmative claims ("Client review and milestone sign-off completed", "Discussion concluded successfully without major blockers", "Root cause investigation underway").
   - Truthfully display: `"_No formal summary recorded._"` and `"_No strategic decisions logged for this session._"` when data is absent.
3. **Direct Database KPI Widget Binding**:
   - In `_refresh_meeting_dashboard_stats`: call `self.controller.db.get_analytics_summary()` directly, displaying exact database-aggregated counts where open actions strictly equal `status IN ('pending', 'blocked', 'reopened')`.
4. **Live Meeting Markers UI**:
   - "⚡ Flag Action": prompt for description and insert into `meeting_markers` table (`marker_type='flag_action'`).
   - "🔖 Bookmark": record timestamp into `meeting_markers` table (`marker_type='bookmark'`).
5. **Slack Export Chronological Fix**:
   - In `_copy_slack()` in `MeetingComparisonDialog`: use canonical `m_later` decisions for active items. **No unconditional `app.processEvents()` re-entry in production.**

---

### 2.5 Track 5: Exporter & Client Review Privacy Safeguards (Worker 5)
**File**: `src/voxlocal/storage/exporter.py`

1. **Template-Aware File Exporters**:
   - Accept `template_name: Optional[str] = None` and `templated_summary: Optional[str] = None` in `to_markdown`, `to_txt`, and `export_to_file`.
   - Serialize templated structure when selected.
2. **Client Review Safe-Content Protection**:
   - In `to_markdown` and `to_txt`: automatically exclude and redact `meeting.get("notes")` when exporting Client Review.
   - Implement `check_sensitive_content(meeting: dict) -> List[str]` to scan meeting text for warning candidates ("confidential", "internal only", "margin", "salary", "private").
   - Present interactive export preview dialog showing potential warning candidates and requiring explicit user confirmation.

---

### 2.6 Track 6: QA, Test Isolation & Integration (Worker 6)
**Files**: `tests/test_meeting_studio_phase5_advanced_intelligence.py`, `tests/test_meeting_studio_enterprise_phase2.py`, `tests/test_cross_feature_coexistence.py`, `tests/test_phase6_enterprise_integrity.py`, `tests/test_phase6_migration_and_compatibility.py`

1. **Test Fixture Storage Isolation Correction**:
   - In `tests/test_meeting_studio_enterprise_phase2.py:26` and `tests/test_cross_feature_coexistence.py:22`:
     Replace `self.config.data_dir = test_dir` with `self.config.storage_dir = str(test_dir)`.
     Permanently ensures tests execute against their temporary directory and never touch `data/voxlocal.db`.
2. **Win32 Clipboard Sequential Test Order Fix (Test Code Only)**:
   - In `tests/test_meeting_studio_phase5_advanced_intelligence.py`:
     - In `tearDown()`: clear clipboard (`cb.clear()`) and call `self.app.processEvents()`.
     - In test clipboard assertion: use a 500ms flushed retry helper.
     - Strict assertion `self.assertIn("Pre-Meeting Briefing", clip_text)` is unaltered and unweakened.
     - Guarantees 98/98 sequential pass cleanly.
3. **Phase 6 Adversarial Test Suite**:
   - Build `tests/test_phase6_enterprise_integrity.py` covering all 34 adversarial scenarios.
4. **Legacy Migration & Compatibility Suite**:
   - Build `tests/test_phase6_migration_and_compatibility.py` verifying legacy meetings with `timezone = NULL`, unclassified decisions, user edits across all 5 statuses, markers, and backup/rollback procedures.
5. **Full Regression Execution**:
   - Run `scratch/run_suites_clean.py` in isolated ephemeral storage, verifying 100% pass across all 98 baseline tests.
6. **Standalone Windows Executable Rebuild**:
   - Rebuild `dist/VoxLocal/VoxLocal.exe` via PyInstaller and execute release smoke test.

---

## 3. Final Approval Gate Checklist

Prior to dispatching any Milestone 3 implementation workers, this final plan must receive explicit user approval:
- [x] Workspace immutability verified; all workers terminated; 0 active timers.
- [x] Canonical decision schema: standardized strictly on `evolution_relationship` and chain preservation (`ON DELETE RESTRICT`).
- [x] Action item migration: `due_date_resolution` explicitly defined with JSON serialization; universal preservation across all 5 statuses (`pending`, `completed`, `cancelled`, `blocked`, `reopened`).
- [x] Canonical dashboard open definition: `status IN ('pending', 'blocked', 'reopened')` across UI and SQL queries.
- [x] Clipboard race correction: fixed in test teardown and bounded retry helper; production handlers clean.
- [x] Test database cleanup: removed from Phase 6 scope; test fixtures corrected directly (`storage_dir = str(test_dir)`).
- [x] Complete risk/blocker model: `risks_and_blockers` table with distinct status constraints and permanent retention.
- [x] WAL-safe SQLite migration and recovery protocol specified.
- [x] 6-track exclusive file ownership and designated QA & Integration Lead assigned.

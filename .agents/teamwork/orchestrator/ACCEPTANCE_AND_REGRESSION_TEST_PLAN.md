# Acceptance & Regression Test Plan: Phase 6 Enterprise Integrity (Final)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Final Corrected Specification for User Approval  
**Operating Mode**: 100% Isolated Ephemeral Testing (`config.storage_dir = temp_dir`)  

---

## 1. Test Architecture & Strict Isolation Principles

1. **Strict Ephemeral Isolation**:
   Every test suite must execute against an ephemeral, temporary SQLite database inside a temporary directory. The shared development database `data/voxlocal.db` must NEVER be written to or touched by test runs.
2. **Test Fixture Storage Isolation Fix**:
   In `tests/test_meeting_studio_enterprise_phase2.py` and `tests/test_cross_feature_coexistence.py`, `self.config.data_dir = test_dir` is corrected to `self.config.storage_dir = str(test_dir)`. This permanently eliminates live database pollution.
3. **Zero Weakened Assertions**:
   No assertions may be relaxed, no tests skipped, and no integration tests mocked out.
4. **Dedicated QA & Integration Ownership**:
   Worker 6 (QA & Integration Lead) oversees test execution, regression preservation, migration tests, and standalone build verification.

---

## 2. Test Suite 1: Legacy Database Migration & Compatibility Suite

File: `tests/test_phase6_migration_and_compatibility.py`  
Executed strictly against isolated copies of historical database fixtures:

| Test Case ID | Test Focus | Fixture Data | Verification Assertions |
|:---|:---|:---|:---|
| **MIG-01** | Legacy Meeting Timezones | Database copy with pre-v6 meetings (IDs 64 & 65) | After migration, `SELECT timezone, utc_offset_min FROM meetings WHERE id IN (64, 65)` returns `(None, None)`. Zero assumed UTC or invented timezones. |
| **MIG-02** | New Meeting Timezone Capture | Create new meeting in configured `'America/New_York'` timezone | New meeting record stores `timezone = 'America/New_York'` and appropriate `utc_offset_min = -300` (or -240). |
| **MIG-03** | Legacy Unclassified Decisions | Pre-v6 decisions (e.g. ID 1 in Meeting 65) | After migration, `SELECT status, category, evolution_relationship, supersedes_id FROM decisions WHERE id = 1` returns `('unclassified', 'General', 'new', None)`. Zero invented classification. |
| **MIG-04** | Decision Chain Immutability | Decision 2 supersedes Decision 1 (`supersedes_id = 1`) | Attempt to delete Decision 1 in `clear_meeting_outcomes`. Verification: Decision 1 is NOT deleted; foreign key constraint and query protection preserve the historical record. |
| **MIG-05** | Structured Relative Date Column | Migrate table with existing action items | Column `due_date_resolution` exists and defaults to `''`. Inserting structured JSON parses and validates correctly. |
| **MIG-06** | User-Edited Action Items in All Statuses | Action items with `user_edited = 1` across `'pending'`, `'completed'`, `'cancelled'`, `'blocked'`, `'reopened'` | Execute `clear_meeting_outcomes(meeting_id, preserve_completed=True)`. All 5 user-edited items remain in the database with 100% text, assignee, deadline, and status preserved. |
| **MIG-07** | Manually Created Tasks Preservation | Action items with `is_manual = 1` | Execute `clear_meeting_outcomes`. Manual action items are NOT deleted. |
| **MIG-08** | Manually Curated Decisions Preservation | Decisions with `is_manual = 1` or `user_edited = 1` | Execute `clear_meeting_outcomes`. Curated decisions are NOT deleted. |
| **MIG-09** | Migration Idempotency | Database migrated to v6 | Re-run `_migrate_v6()` multiple times. Schema version remains 6, no duplicate columns or errors. `PRAGMA integrity_check` returns `[('ok',)]`. |
| **MIG-10** | Rollback & Failure Preservation | Injected SQL failure during migration step | Verify `ROLLBACK` executes, original database copy is preserved, failed copy is saved, and no partial tables exist. |

---

## 3. Test Suite 2: Phase 6 Adversarial Test Suite (34 Scenarios)

File: `tests/test_phase6_enterprise_integrity.py`  
Formalized from `scratch/test_adversarial_m2_reproduction.py` covering all 34 scenarios:

- **Focus Area 1: Decision Evolution (ADV-01 to ADV-05)**:
  - `test_adv01_order_independent_comparison`: `compare_meetings(m1, m2)` and `compare_meetings(m2, m1)` produce identical canonical chronological comparison `#1 vs #2` and accurate topic evolution deltas.
  - `test_adv02_seven_state_evolution_classification`: Correctly identifies `superseded`, `reversed`, `repeated`, `confirmed`, `proposed`, `unresolved` using canonical `evolution_relationship`.
  - `test_adv03_slack_export_chronological_accuracy`: Slack export labels the chronologically latest meeting's decisions as active regardless of dialog combo box selection.
  - `test_adv04_relational_decision_schema`: Verifies relational columns exist (`evolution_relationship`, `supersedes_id`) and chain preservation works.
  - `test_adv05_structured_decision_categorization`: Verifies `category` is stored in dedicated column, not prepended to rationale.

- **Focus Area 2: Action Item Lifecycle (ADV-06 to ADV-11)**:
  - `test_adv06_preserve_user_edited_action_items`: User edits across all statuses survive report regeneration.
  - `test_adv07_preserve_manual_action_items`: Manually added items survive report regeneration.
  - `test_adv08_preserve_curated_decisions`: Decisions survive report regeneration.
  - `test_adv09_multi_state_task_transitions`: Supports transitions across `pending`, `completed`, `cancelled`, `reopened`, and `blocked`.
  - `test_adv10_live_flag_task_preservation`: "⚡ Flag Action" items persist in `meeting_markers` and are not deleted upon finalization.
  - `test_adv11_live_bookmark_persistence`: "🔖 Bookmark" items persist in `meeting_markers` with meeting ID and timestamp.

- **Focus Area 3: Relative Dates & Timezones (ADV-12 to ADV-14)**:
  - `test_adv12_calendar_due_date_resolution`: "by Friday" resolves to verified calendar date (`YYYY-MM-DD`).
  - `test_adv13_timezone_policy_and_offset`: Timezone offset calculation against meeting start timestamp.
  - `test_adv14_explicit_uncertainty_retention`: Ambiguous expressions ("in a couple of weeks") retain explicit `uncertainty_reason` in `due_date_resolution`.

- **Focus Area 4: Dashboard KPI Integrity (ADV-15 to ADV-19)**:
  - `test_adv15_uncapped_kpi_calculation`: Exact counts returned for catalogs > 1,000 meetings and > 5,000 tasks.
  - `test_adv16_completed_status_filtering_in_kpis`: Excludes crashed, recording, and interrupted sessions.
  - `test_adv17_canonical_open_status_definitions`: Verifies open actions metric strictly equals `status IN ('pending', 'blocked', 'reopened')` across UI and DB analytics.
  - `test_adv18_reanalysis_task_deduplication`: Reworded LLM tasks match existing commitments without creating duplicates.
  - `test_adv19_active_decisions_filtering`: Only confirmed decisions counted in active KPIs.

- **Focus Area 5: Report Templates & Export Privacy (ADV-20 to ADV-24)**:
  - `test_adv20_truthful_client_review_summary`: Displays lack of data instead of fabricating sign-offs or terms.
  - `test_adv21_truthful_executive_brief`: Truthfully displays lack of overview without fabricating statements.
  - `test_adv22_truthful_troubleshooting_summary`: Truthfully displays absence of diagnostics.
  - `test_adv23_file_export_template_fidelity`: Export formats according to active presentation template.
  - `test_adv24_client_review_internal_notes_redaction`: Redacts `notes` and flags sensitive dialog with confirmation modal.

- **Focus Area 6: Stepper Integrity (ADV-25 to ADV-27)**:
  - `test_adv25_honest_stepper_error_reporting`: Progress card reflects failed stage; no `"idle"` success signal on crash.
  - `test_adv26_stage2_transcript_preservation`: Transcript segments committed to SQLite before Stage 3 diarization runs.
  - `test_adv27_cooperative_cancellation`: Graceful halt at stage boundary when cancellation token is set.

- **Focus Area 7: Cross-Meeting Intelligence (ADV-28 to ADV-30)**:
  - `test_adv28_anchor_citations_in_briefings`: Pre-meeting briefings include session dates and `HH:MM:SS` anchors.
  - `test_adv29_cross_meeting_task_reconciliation`: Prevents reviving tasks completed in subsequent sessions.
  - `test_adv30_risks_and_blockers_table`: Verifies `risks_and_blockers` schema, type-specific constraints, and permanent historical retention of resolved items.

- **Focus Area 8: Meeting-Scoped Ask Vox Queries (ADV-31 to ADV-34)**:
  - `test_adv31_in_report_ask_direct_scoping`: Passes `meeting_id` directly, eliminating regex title failures.
  - `test_adv32_zero_match_isolation`: Zero citation matches returns isolated message without leaking unrelated meeting titles.
  - `test_adv33_uncapped_scoped_meeting_lookup`: Resolves older meetings directly via `get_meeting` without 250 cap.
  - `test_adv34_ui_meeting_scope_completeness`: All meetings accessible in scope selector.

---

## 4. Test Suite 3: Sequential Baseline Qualification Suite (98 Tests)

Script: `scratch/run_suites_clean.py`

### Win32 Clipboard Race Correction in `tests/test_meeting_studio_phase5_advanced_intelligence.py`:
- **No Unconditional `app.processEvents()` in Production**: Production clipboard handlers in `src/voxlocal/ui/components.py` remain clean without event-loop re-entry.
- **Test Isolation Teardown**:
  ```python
  def tearDown(self):
      try:
          self.window.close()
          self.floating_widget.close()
          self.controller.shutdown()
          cb = QApplication.clipboard()
          if cb:
              cb.clear()
          if hasattr(self, "app") and self.app:
              self.app.processEvents()
      except Exception:
          pass
  ```
- **Bounded Clipboard Read Retry in Test Code**:
  ```python
  def _get_clipboard_text_flushed(self, expected_substring: str = "", timeout: float = 0.5) -> str:
      deadline = time.time() + timeout
      while time.time() < deadline:
          if self.app:
              self.app.processEvents()
          cb = QApplication.clipboard()
          if cb:
              txt = cb.text()
              if not expected_substring or expected_substring in txt:
                  return txt
          time.sleep(0.02)
      return QApplication.clipboard().text() if QApplication.clipboard() else ""
  ```
- **Strict Assertion Unaltered**: `self.assertIn("Pre-Meeting Briefing", clip_text)`.
- **Target Sequential Result**: **98 / 98 PASSED (100%)**.

---

## 5. Release Build & Standalone Executable Verification

1. Rebuild executable via PyInstaller:
   ```powershell
   pyinstaller --noconfirm VoxLocal.spec
   ```
2. Verify `dist/VoxLocal/VoxLocal.exe` executes on Windows:
   - Launches native GUI.
   - Loads SQLite database and executes migrations cleanly.
   - Recording, transcription, and meeting studio interfaces operate without crash.

## 2026-11-02T03:14:21Z
You are teamwork_worker assigned to Track 1: Storage, Schema Migrations & Database Integrity.
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\worker_1_storage\
Your exclusive file ownership:
- `src/voxlocal/storage/database.py`
- `src/voxlocal/core/config.py`
You MUST NOT edit any other files.

Reference documents:
- d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\SCHEMA_AND_MIGRATION_SPECIFICATION.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\FILE_LEVEL_IMPLEMENTATION_PLAN.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\DEFECT_REGISTER_AND_ADVERSARIAL_CATALOGUE.md

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

TASKS:
1. In `src/voxlocal/core/config.py`: Add `timezone: str = "UTC"` to `AppConfig`. (Do NOT add data_dir alias).
2. In `src/voxlocal/storage/database.py`:
   - Implement `create_verified_snapshot(self, backup_dir: Path) -> Path` (forcing WAL checkpoint, streaming pages via backup API, validating integrity).
   - Implement `_migrate_v6(self)`:
     - `meetings`: add `timezone TEXT NULL` and `utc_offset_min INTEGER NULL`. (Legacy records strictly retain `NULL`).
     - `decisions`:
       - `status TEXT NOT NULL DEFAULT 'unclassified' CHECK(status IN ('unclassified', 'proposed', 'confirmed', 'rejected', 'unresolved'))`.
       - `category TEXT NOT NULL DEFAULT 'General'`.
       - `evolution_relationship TEXT NOT NULL DEFAULT 'new' CHECK(evolution_relationship IN ('new', 'repeated', 'superseded', 'reversed'))`. (CANONICAL FIELD NAME).
       - `supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT`.
       - `evidence_quote TEXT DEFAULT ''`.
       - `is_manual INTEGER DEFAULT 0`.
       - `user_edited INTEGER DEFAULT 0`.
     - `action_items`:
       - `due_date_resolution TEXT DEFAULT ''`.
       - `evidence_quote TEXT DEFAULT ''`.
       - `is_manual INTEGER DEFAULT 0`.
       - `user_edited INTEGER DEFAULT 0`.
     - Create `meeting_markers` table (`flag_action`, `bookmark`).
     - Create `risks_and_blockers` table with distinct status constraints for 'risk' and 'blocker', plus `resolved_at`.
     - Set `PRAGMA user_version = 6`.
   - Update `clear_meeting_outcomes`: universally protect user-edited (`user_edited = 1`) and manual items (`is_manual = 1`) across all statuses (`pending`, `completed`, `cancelled`, `blocked`, `reopened`). Protect decisions referenced by `supersedes_id`.
   - Implement entity helpers for markers, risks/blockers, multi-state task transitions, and decision updates.
   - Update `get_analytics_summary()`: filter `status = 'completed'` for total meetings/duration; canonical open actions definition is `status IN ('pending', 'blocked', 'reopened')`.
3. Verify your implementation by running targeted tests against isolated temporary databases (`config.storage_dir = temp_dir`). NEVER mutate `data/voxlocal.db`.
4. Write your completion report in `d:\Milind\Voice Assistant\.agents\teamwork\worker_1_storage\handoff.md` and update `progress.md`.
5. Send a completion message to the orchestrator.

## 2026-11-02T03:48:09Z
[Message] sender=5e6806a0-e398-45c0-86c7-671db54c88e0
**Context**: Track 6 regression verification on test_meeting_studio_phase5_enterprise_ux
**Content**: In `get_analytics_summary()`, `total_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]` was previously returned in the stats dict. The baseline regression test `test_01_meeting_dashboard_header_kpis_and_live_stats` expects the seeded decisions count to appear in the dashboard KPI.
**Action**: Please add `"total_decisions": tot_decisions` to the returned dictionary in `get_analytics_summary()` alongside `"confirmed_decisions"`.

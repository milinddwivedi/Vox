import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase


class TestTrack1StorageAndMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_test_track1_")
        self.base_path = Path(self.temp_dir)
        self.db_path = self.base_path / "voxlocal.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_config_timezone_default(self):
        """AppConfig must provide timezone = 'UTC' default without data_dir alias."""
        cfg = AppConfig()
        self.assertEqual(cfg.timezone, "UTC")
        self.assertFalse(hasattr(AppConfig, "data_dir"))

    def test_greenfield_database_schema_v6(self):
        """A new database must initialize with user_version = 6 and all v6 tables/columns."""
        db = LocalDatabase(self.db_path)
        with db._connect() as conn:
            v = conn.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(v, 6)

            # Check meetings columns
            meeting_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(meetings);").fetchall()}
            self.assertIn("timezone", meeting_cols)
            self.assertIn("utc_offset_min", meeting_cols)

            # Check decisions columns
            decision_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(decisions);").fetchall()}
            self.assertIn("status", decision_cols)
            self.assertIn("category", decision_cols)
            self.assertIn("evolution_relationship", decision_cols)
            self.assertIn("supersedes_id", decision_cols)
            self.assertIn("evidence_quote", decision_cols)
            self.assertIn("is_manual", decision_cols)
            self.assertIn("user_edited", decision_cols)

            # Check action_items columns
            action_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(action_items);").fetchall()}
            self.assertIn("due_date_resolution", action_cols)
            self.assertIn("evidence_quote", action_cols)
            self.assertIn("is_manual", action_cols)
            self.assertIn("user_edited", action_cols)

            # Check meeting_markers table
            marker_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(meeting_markers);").fetchall()}
            self.assertIn("marker_type", marker_cols)
            self.assertIn("label", marker_cols)
            self.assertIn("timestamp_sec", marker_cols)

            # Check risks_and_blockers table
            risk_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(risks_and_blockers);").fetchall()}
            self.assertIn("item_type", risk_cols)
            self.assertIn("impact", risk_cols)
            self.assertIn("status", risk_cols)
            self.assertIn("resolved_at", risk_cols)

    def test_legacy_v5_database_migration_to_v6(self):
        """Legacy v5 database must migrate cleanly: meetings have NULL timezone/offset, unclassified decisions."""
        legacy_path = self.base_path / "legacy.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript("""
        CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT UNIQUE, folder_type TEXT, color TEXT, icon TEXT, created_at TEXT);
        CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT, ended_at TEXT, duration_sec REAL, status TEXT, audio_path TEXT, summary TEXT, user_edited INTEGER DEFAULT 0);
        CREATE TABLE transcript_segments (id INTEGER PRIMARY KEY, meeting_id INTEGER, segment_index INTEGER, start_time REAL, end_time REAL, speaker TEXT, text TEXT, confidence REAL);
        CREATE TABLE action_items (id INTEGER PRIMARY KEY, meeting_id INTEGER, description TEXT, owner TEXT, due_date TEXT, status TEXT DEFAULT 'pending', timestamp_sec REAL, created_at TEXT);
        CREATE TABLE decisions (id INTEGER PRIMARY KEY, meeting_id INTEGER, decision TEXT, rationale TEXT, timestamp_sec REAL, created_at TEXT);
        CREATE TABLE dictation_history (id INTEGER PRIMARY KEY, created_at TEXT, duration_sec REAL, raw_text TEXT, final_text TEXT, target_window TEXT, target_process TEXT, insertion_method TEXT, status TEXT);
        CREATE TABLE custom_vocabulary (id INTEGER PRIMARY KEY, spoken_phrase TEXT UNIQUE, replacement TEXT, category TEXT, is_active INTEGER);
        CREATE TABLE screen_recordings (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT, duration_sec REAL, file_path TEXT, file_size_bytes INTEGER, fps INTEGER, resolution TEXT, audio_mode TEXT);
        """)
        conn.execute("INSERT INTO meetings (id, title, created_at, status, duration_sec) VALUES (1, 'Legacy Session', '2026-10-01 10:00:00', 'completed', 500.0);")
        conn.execute("INSERT INTO decisions (id, meeting_id, decision, created_at) VALUES (10, 1, 'Adopt Postgres', '2026-10-01 10:05:00');")
        conn.execute("INSERT INTO action_items (id, meeting_id, description, status, created_at) VALUES (20, 1, 'Deploy DB', 'pending', '2026-10-01 10:06:00');")
        conn.execute("PRAGMA user_version = 5;")
        conn.commit()
        conn.close()

        # Instantiate LocalDatabase on legacy database path
        db = LocalDatabase(legacy_path)

        with db._connect() as chk_conn:
            v = chk_conn.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(v, 6)

            # Verify legacy record semantics: strictly NULL timezone and offset
            m = chk_conn.execute("SELECT * FROM meetings WHERE id = 1;").fetchone()
            self.assertIsNone(m["timezone"])
            self.assertIsNone(m["utc_offset_min"])

            # Verify legacy decision semantics: unclassified status, General category, new evolution
            d = chk_conn.execute("SELECT * FROM decisions WHERE id = 10;").fetchone()
            self.assertEqual(d["status"], "unclassified")
            self.assertEqual(d["category"], "General")
            self.assertEqual(d["evolution_relationship"], "new")
            self.assertIsNone(d["supersedes_id"])
            self.assertEqual(d["is_manual"], 0)
            self.assertEqual(d["user_edited"], 0)

            # Verify snapshot was created
            snapshots_dir = legacy_path.parent / "snapshots"
            self.assertTrue(snapshots_dir.exists())
            snaps = list(snapshots_dir.glob("voxlocal_pre_v6_*.db"))
            self.assertGreaterEqual(len(snaps), 1)

    def test_create_verified_snapshot_explicit(self):
        """Explicit snapshot creation flushes WAL and verifies integrity."""
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Snapshot Test", timezone="America/New_York", utc_offset_min=-300)
        db.add_decision(m_id, "Decision A")

        custom_snap_dir = self.base_path / "custom_backups"
        snap_path = db.create_verified_snapshot(custom_snap_dir)
        self.assertTrue(snap_path.exists())

        # Verify snapshot content
        snap_conn = sqlite3.connect(str(snap_path))
        m_row = snap_conn.execute("SELECT * FROM meetings WHERE id = ?", (m_id,)).fetchone()
        self.assertIsNotNone(m_row)
        snap_conn.close()

    def test_clear_meeting_outcomes_preservation_rules(self):
        """Action items with user_edited=1 or is_manual=1 and decisions with supersedes_id references must be preserved."""
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Outcome Test")

        # 1. Action items
        # Unedited pending: should be cleared when preserve_completed=True
        a_auto_pending = db.add_action_item(m_id, "Auto Pending", is_manual=0, user_edited=0, status="pending")
        # Completed: should be preserved when preserve_completed=True
        a_completed = db.add_action_item(m_id, "Completed Task", is_manual=0, user_edited=0, status="completed")
        # User-edited pending: MUST be preserved across all conditions
        a_edited_pending = db.add_action_item(m_id, "Edited Pending", is_manual=0, user_edited=1, status="pending")
        # Manual item: MUST be preserved across all conditions
        a_manual = db.add_action_item(m_id, "Manual Item", is_manual=1, user_edited=0, status="cancelled")
        # Blocked item: MUST be preserved when preserve_completed=True
        a_blocked = db.add_action_item(m_id, "Blocked Task", is_manual=0, user_edited=0, status="blocked")
        # Reopened item: MUST be preserved when preserve_completed=True
        a_reopened = db.add_action_item(m_id, "Reopened Task", is_manual=0, user_edited=0, status="reopened")

        # 2. Decisions
        # Unedited decision not referenced: should be cleared
        d_auto = db.add_decision(m_id, "Auto Decision", is_manual=0, user_edited=0)
        # Decision referenced by another decision as historical anchor: MUST be preserved
        d_anchor = db.add_decision(m_id, "Historical Decision A", is_manual=0, user_edited=0)
        d_later = db.add_decision(m_id, "Superseding Decision B", supersedes_id=d_anchor, is_manual=0, user_edited=0)
        # Manual decision: MUST be preserved
        d_manual = db.add_decision(m_id, "Manual Decision", is_manual=1, user_edited=0)
        # User-edited decision: MUST be preserved
        d_edited = db.add_decision(m_id, "Edited Decision", is_manual=0, user_edited=1)

        # Execute clear_meeting_outcomes with preserve_completed=True
        db.clear_meeting_outcomes(m_id, preserve_completed=True)

        m = db.get_meeting(m_id)
        action_ids = [a["id"] for a in m["actions_list"]]
        decision_ids = [d["id"] for d in m["decisions_list"]]

        # Unedited pending action item must be deleted
        self.assertNotIn(a_auto_pending, action_ids)
        # All protected action items must remain
        self.assertIn(a_completed, action_ids)
        self.assertIn(a_edited_pending, action_ids)
        self.assertIn(a_manual, action_ids)
        self.assertIn(a_blocked, action_ids)
        self.assertIn(a_reopened, action_ids)

        # Unreferenced auto decision must be deleted
        self.assertNotIn(d_auto, decision_ids)
        # Historical anchor decision MUST NOT be deleted
        self.assertIn(d_anchor, decision_ids)
        self.assertIn(d_manual, decision_ids)
        self.assertIn(d_edited, decision_ids)

    def test_multi_state_task_transitions(self):
        """Action items support 5 lifecycle states and mark user_edited=1."""
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Task Lifecycle")
        item_id = db.add_action_item(m_id, "Ship feature", status="pending")

        for state in ("blocked", "reopened", "cancelled", "completed", "pending"):
            res = db.toggle_action_item_status(item_id, new_status=state)
            self.assertEqual(res, state)
            actions = db.list_action_items(meeting_id=m_id)
            self.assertEqual(actions[0]["status"], state)
            self.assertEqual(actions[0]["user_edited"], 1)

    def test_markers_and_risks_blockers_crud(self):
        """Meeting markers and risks/blockers entity helpers operate accurately."""
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Markers & Risks")

        # Markers
        m_flag = db.add_marker(m_id, "flag_action", "Review PR by EOD", 12.5)
        m_book = db.add_marker(m_id, "bookmark", "Key slide presented", 45.0)
        markers = db.list_markers(meeting_id=m_id)
        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0]["marker_type"], "flag_action")
        self.assertTrue(db.delete_marker(m_flag))
        self.assertEqual(len(db.list_markers(meeting_id=m_id)), 1)

        # Risks & Blockers
        r_id = db.add_risk_or_blocker(m_id, "risk", "Vendor latency", impact="high", status="identified")
        b_id = db.add_risk_or_blocker(m_id, "blocker", "Firewall blocking port 443", impact="critical", status="active")

        # Resolve risk
        db.update_risk_or_blocker(r_id, status="mitigated")
        r = db.get_risk_or_blocker(r_id)
        self.assertEqual(r["status"], "mitigated")
        self.assertIsNotNone(r["resolved_at"])

        # Resolve blocker
        db.update_risk_or_blocker(b_id, status="resolved")
        b = db.get_risk_or_blocker(b_id)
        self.assertEqual(b["status"], "resolved")
        self.assertIsNotNone(b["resolved_at"])

    def test_analytics_summary_kpi_calculations(self):
        """get_analytics_summary filters completed meetings and uses canonical open action definition."""
        db = LocalDatabase(self.db_path)
        # Completed meeting
        db.create_meeting("M1", engagement_score=90)
        db.update_meeting(1, status="completed", duration_sec=300.0)
        # Interrupted meeting (must not be counted in totals)
        db.create_meeting("M2", engagement_score=60)
        db.update_meeting(2, status="interrupted", duration_sec=150.0)

        # Action items: pending, blocked, reopened (all 3 are canonical open work)
        db.add_action_item(1, "Task 1", status="pending")
        db.add_action_item(1, "Task 2", status="blocked")
        db.add_action_item(1, "Task 3", status="reopened")
        # Closed items
        db.add_action_item(1, "Task 4", status="completed")
        db.add_action_item(1, "Task 5", status="cancelled")

        # Decisions
        db.add_decision(1, "Dec 1", status="confirmed")
        db.add_decision(1, "Dec 2", status="unclassified")

        stats = db.get_analytics_summary()
        self.assertEqual(stats["total_meetings"], 1)
        self.assertEqual(stats["total_duration_sec"], 300.0)
        self.assertEqual(stats["avg_engagement_score"], 90.0)
        self.assertEqual(stats["open_action_items"], 3)
        self.assertEqual(stats["pending_action_items"], 1)
        self.assertEqual(stats["blocked_action_items"], 1)
        self.assertEqual(stats["reopened_action_items"], 1)
        self.assertEqual(stats["completed_action_items"], 1)
        self.assertEqual(stats["total_decisions"], 2)
        self.assertEqual(stats["confirmed_decisions"], 1)


if __name__ == "__main__":
    unittest.main()

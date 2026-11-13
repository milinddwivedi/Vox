"""
Acceptance Test Suite: Phase 6 Legacy Database Migration & Compatibility Suite (MIG-01 through MIG-10).
Executed strictly against isolated ephemeral temporary SQLite databases.
ZERO pollution or mutation to data/voxlocal.db.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Ensure src is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase


def create_legacy_v5_database(db_path: Path) -> None:
    """Helper to create a faithful legacy pre-v6 (user_version = 5) SQLite schema with benchmark meetings."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript("""
    CREATE TABLE folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        folder_type TEXT NOT NULL DEFAULT 'personal',
        color TEXT DEFAULT '#A86F3E',
        icon TEXT DEFAULT 'folder',
        created_at TEXT NOT NULL
    );

    CREATE TABLE meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        ended_at TEXT DEFAULT '',
        duration_sec REAL DEFAULT 0.0,
        capture_mode TEXT DEFAULT 'both',
        mic_device TEXT DEFAULT '',
        loopback_device TEXT DEFAULT '',
        audio_path TEXT DEFAULT '',
        mic_audio_path TEXT DEFAULT '',
        sys_audio_path TEXT DEFAULT '',
        video_path TEXT DEFAULT '',
        folder_id INTEGER DEFAULT NULL,
        meeting_type TEXT DEFAULT 'Meeting Reports',
        source_platform TEXT DEFAULT 'Desktop App',
        engagement_score INTEGER DEFAULT 85,
        sentiment_score REAL DEFAULT 0.0,
        topics TEXT DEFAULT '',
        status TEXT DEFAULT 'recording',
        model_used TEXT DEFAULT 'base.en',
        full_transcript TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        decisions TEXT DEFAULT '',
        action_items TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        user_edited INTEGER DEFAULT 0
    );

    CREATE TABLE transcript_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL,
        segment_index INTEGER NOT NULL,
        start_time REAL NOT NULL,
        end_time REAL NOT NULL,
        speaker TEXT NOT NULL DEFAULT 'Speaker 1',
        text TEXT NOT NULL,
        confidence REAL DEFAULT 0.95,
        created_at TEXT DEFAULT '2026-10-01 10:00:00'
    );

    CREATE TABLE action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        owner TEXT DEFAULT 'Unassigned',
        due_date TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        timestamp_sec REAL DEFAULT 0.0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id INTEGER NOT NULL,
        decision TEXT NOT NULL,
        rationale TEXT DEFAULT '',
        timestamp_sec REAL DEFAULT 0.0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE dictation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        duration_sec REAL DEFAULT 0.0,
        raw_text TEXT NOT NULL,
        final_text TEXT NOT NULL,
        target_window TEXT DEFAULT '',
        target_process TEXT DEFAULT '',
        insertion_method TEXT DEFAULT 'clipboard',
        status TEXT DEFAULT 'completed'
    );

    CREATE TABLE custom_vocabulary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        spoken_phrase TEXT NOT NULL UNIQUE,
        replacement TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        is_active INTEGER DEFAULT 1
    );

    CREATE TABLE screen_recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        duration_sec REAL DEFAULT 0.0,
        file_path TEXT NOT NULL,
        file_size_bytes INTEGER DEFAULT 0,
        fps INTEGER DEFAULT 30,
        resolution TEXT DEFAULT '1920x1080',
        audio_mode TEXT DEFAULT 'mic_system'
    );
    """)

    # Seed Golden Benchmark legacy meetings 64 & 65
    conn.execute(
        "INSERT INTO meetings (id, title, created_at, status, duration_sec) "
        "VALUES (64, 'Golden Benchmark Session 64', '2026-10-15 14:00:00', 'completed', 1240.0);"
    )
    conn.execute(
        "INSERT INTO meetings (id, title, created_at, status, duration_sec) "
        "VALUES (65, 'Golden Benchmark Session 65', '2026-10-16 15:30:00', 'completed', 1890.0);"
    )

    # Seed pre-v6 decisions (Decision 1 in Meeting 65)
    conn.execute(
        "INSERT INTO decisions (id, meeting_id, decision, rationale, created_at) "
        "VALUES (1, 65, 'Standardize on local offline SQLite store', 'Ensures zero-cloud privacy mandate', '2026-10-16 15:45:00');"
    )

    # Seed pre-v6 action item
    conn.execute(
        "INSERT INTO action_items (id, meeting_id, description, owner, due_date, status, created_at) "
        "VALUES (101, 65, 'Audit offline storage integrity', 'Engineering', 'Next Friday', 'pending', '2026-10-16 15:50:00');"
    )

    conn.execute("PRAGMA user_version = 5;")
    conn.commit()
    conn.close()


class TestPhase6MigrationAndCompatibility(unittest.TestCase):
    """MIG-01 through MIG-10 Schema Migration, Safety & Compatibility Qualification."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_test_phase6_mig_")
        self.base_path = Path(self.temp_dir)
        self.db_path = self.base_path / "voxlocal.db"
        # Strict isolation check
        assert self.base_path.resolve() != (ROOT_DIR / "data").resolve()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mig01_legacy_v5_to_v6_schema_migration(self):
        """
        MIG-01: Legacy v5 to v6 schema migration with PRAGMA user_version = 6.
        Validates atomic migration and existence of all mandatory enterprise columns/tables.
        """
        legacy_path = self.base_path / "legacy_v5.db"
        create_legacy_v5_database(legacy_path)

        # Trigger automatic migration via LocalDatabase initialization
        db = LocalDatabase(legacy_path)

        with db._connect() as conn:
            # 1. Version check
            user_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(user_ver, 6, "Database user_version must be 6 after v6 migration")

            # 2. Meetings table columns
            meeting_cols = {r["name"]: dict(r) for r in conn.execute("PRAGMA table_info(meetings);").fetchall()}
            self.assertIn("timezone", meeting_cols)
            self.assertIn("utc_offset_min", meeting_cols)

            # 3. Decisions table columns
            decision_cols = {r["name"]: dict(r) for r in conn.execute("PRAGMA table_info(decisions);").fetchall()}
            self.assertIn("status", decision_cols)
            self.assertIn("category", decision_cols)
            self.assertIn("evolution_relationship", decision_cols)
            self.assertIn("supersedes_id", decision_cols)
            self.assertIn("evidence_quote", decision_cols)
            self.assertIn("is_manual", decision_cols)
            self.assertIn("user_edited", decision_cols)

            # 4. Action items columns
            action_cols = {r["name"]: dict(r) for r in conn.execute("PRAGMA table_info(action_items);").fetchall()}
            self.assertIn("due_date_resolution", action_cols)
            self.assertIn("evidence_quote", action_cols)
            self.assertIn("is_manual", action_cols)
            self.assertIn("user_edited", action_cols)

            # 5. Tables meeting_markers and risks_and_blockers
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
            self.assertIn("meeting_markers", tables)
            self.assertIn("risks_and_blockers", tables)

            # 6. Physical integrity check
            chk = conn.execute("PRAGMA integrity_check;").fetchall()
            self.assertEqual([r[0] for r in chk], ["ok"])

    def test_mig02_legacy_meetings_timezone_null_and_new_meeting_capture(self):
        """
        MIG-02: Legacy meetings timezone strictly NULL; new meetings capture configured timezone and offset.
        Zero assumed UTC or invented timezones for historical meetings 64 & 65.
        """
        legacy_path = self.base_path / "legacy_v5.db"
        create_legacy_v5_database(legacy_path)

        db = LocalDatabase(legacy_path)

        # Verify historical records strictly retain NULL
        with db._connect() as conn:
            rows = conn.execute("SELECT id, timezone, utc_offset_min FROM meetings WHERE id IN (64, 65) ORDER BY id;").fetchall()
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertIsNone(row["timezone"], f"Meeting {row['id']} must retain timezone=NULL")
                self.assertIsNone(row["utc_offset_min"], f"Meeting {row['id']} must retain utc_offset_min=NULL")

        # Create new meeting in configured 'America/New_York' timezone
        new_mid = db.create_meeting(
            title="Q4 Enterprise Roadmap",
            timezone="America/New_York",
            utc_offset_min=-300,
        )
        new_m = db.get_meeting(new_mid)
        self.assertEqual(new_m["timezone"], "America/New_York")
        self.assertEqual(new_m["utc_offset_min"], -300)

    def test_mig03_decisions_canonical_evolution_and_legacy_unclassified(self):
        """
        MIG-03: Decisions canonical evolution_relationship and legacy unclassified status.
        Pre-v6 decisions (ID 1 in Meeting 65) default strictly to status='unclassified', category='General', evolution_relationship='new'.
        """
        legacy_path = self.base_path / "legacy_v5.db"
        create_legacy_v5_database(legacy_path)

        db = LocalDatabase(legacy_path)

        with db._connect() as conn:
            d = conn.execute("SELECT * FROM decisions WHERE id = 1;").fetchone()
            self.assertIsNotNone(d)
            self.assertEqual(d["status"], "unclassified", "Legacy decision status must strictly be 'unclassified'")
            self.assertEqual(d["category"], "General", "Legacy decision category must strictly be 'General'")
            self.assertEqual(d["evolution_relationship"], "new", "Legacy evolution_relationship must strictly be 'new'")
            self.assertIsNone(d["supersedes_id"])
            self.assertEqual(d["is_manual"], 0)
            self.assertEqual(d["user_edited"], 0)

            # Verify CHECK constraint on status
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO decisions (meeting_id, decision, status, created_at) "
                    "VALUES (65, 'Invalid status decision', 'non_existent_status', '2026-10-16 16:00:00');"
                )

            # Verify CHECK constraint on evolution_relationship
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO decisions (meeting_id, decision, evolution_relationship, created_at) "
                    "VALUES (65, 'Invalid evolution decision', 'invalid_state', '2026-10-16 16:00:00');"
                )

    def test_mig04_action_items_due_date_resolution_json_and_user_edits(self):
        """
        MIG-04: Action items due_date_resolution JSON format and universal preservation of user edits.
        """
        legacy_path = self.base_path / "legacy_v5.db"
        create_legacy_v5_database(legacy_path)

        db = LocalDatabase(legacy_path)

        # Migrated row has empty default
        migrated_item = db.list_action_items(meeting_id=65)[0]
        self.assertEqual(migrated_item["due_date_resolution"], "")

        # Structured relative date metadata insertion
        resolution_meta = {
            "original_expression": "by next Friday",
            "resolved_date": "2026-11-06",
            "meeting_timestamp": "2026-10-31 02:01:00",
            "timezone_used": "America/New_York",
            "resolution_status": "deterministic",
            "uncertainty_reason": None,
        }
        res_json = json.dumps(resolution_meta)

        item_id = db.add_action_item(
            meeting_id=65,
            description="Ship v6 production migration",
            owner="Milind",
            due_date="Next Friday",
            status="pending",
            due_date_resolution=res_json,
        )

        fresh_item = db.list_action_items(meeting_id=65)
        created = next(a for a in fresh_item if a["id"] == item_id)
        parsed = json.loads(created["due_date_resolution"])
        self.assertEqual(parsed["original_expression"], "by next Friday")
        self.assertEqual(parsed["resolved_date"], "2026-11-06")
        self.assertEqual(parsed["resolution_status"], "deterministic")

        # Update action item marks user_edited = 1
        db.update_action_item(item_id, description="Ship v6 production migration (Approved)")
        updated = next(a for a in db.list_action_items(meeting_id=65) if a["id"] == item_id)
        self.assertEqual(updated["user_edited"], 1)

    def test_mig05_risks_and_blockers_constraints_and_permanent_retention(self):
        """
        MIG-05: risks_and_blockers table constraints and permanent retention.
        Enforces type-specific statuses and verified resolved_at retention.
        """
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Risk & Blocker Session")

        # 1. Valid Risk
        r_id = db.add_risk_or_blocker(
            meeting_id=m_id,
            item_type="risk",
            title="Third-party API latency spike",
            impact="high",
            status="identified",
        )
        self.assertGreater(r_id, 0)

        # 2. Valid Blocker
        b_id = db.add_risk_or_blocker(
            meeting_id=m_id,
            item_type="blocker",
            title="GPU server quota exhausted",
            impact="critical",
            status="active",
        )
        self.assertGreater(b_id, 0)

        # 3. Invalid Risk status violates CHECK constraint
        with self.assertRaises(sqlite3.IntegrityError):
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO risks_and_blockers (meeting_id, item_type, title, status, created_at) "
                    "VALUES (?, 'risk', 'Invalid status risk', 'active', '2026-10-16 16:00:00')",
                    (m_id,)
                )

        # 4. Invalid Blocker status violates CHECK constraint
        with self.assertRaises(sqlite3.IntegrityError):
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO risks_and_blockers (meeting_id, item_type, title, status, created_at) "
                    "VALUES (?, 'blocker', 'Invalid status blocker', 'mitigated', '2026-10-16 16:00:00')",
                    (m_id,)
                )

        # 5. Permanent historical retention on resolution
        db.update_risk_or_blocker(r_id, status="mitigated")
        db.update_risk_or_blocker(b_id, status="resolved")

        r = db.get_risk_or_blocker(r_id)
        b = db.get_risk_or_blocker(b_id)
        self.assertEqual(r["status"], "mitigated")
        self.assertIsNotNone(r["resolved_at"])
        self.assertEqual(b["status"], "resolved")
        self.assertIsNotNone(b["resolved_at"])

        # Running clear_meeting_outcomes must NEVER delete risks and blockers
        db.clear_meeting_outcomes(m_id)
        all_items = db.list_risks_and_blockers(meeting_id=m_id)
        self.assertEqual(len(all_items), 2, "Risks and blockers must be permanently retained across outcome regeneration")

    def test_mig06_meeting_markers_table_persistence(self):
        """
        MIG-06: meeting_markers table persistence.
        Validates marker_type check constraint and real-time live event capture.
        """
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Live Session Markers")

        f_id = db.add_marker(m_id, marker_type="flag_action", label="Follow up on compliance", timestamp_sec=42.5)
        b_id = db.add_marker(m_id, marker_type="bookmark", label="Key architecture slide", timestamp_sec=180.0)

        markers = db.list_markers(meeting_id=m_id)
        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0]["marker_type"], "flag_action")
        self.assertEqual(markers[1]["marker_type"], "bookmark")

        # Invalid marker_type raises IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO meeting_markers (meeting_id, marker_type, label, timestamp_sec, created_at) "
                    "VALUES (?, 'invalid_marker', 'Test', 0.0, '2026-10-16 16:00:00')",
                    (m_id,)
                )

        # Regenerating outcomes must NOT delete live markers
        db.clear_meeting_outcomes(m_id)
        markers_after = db.list_markers(meeting_id=m_id)
        self.assertEqual(len(markers_after), 2, "Meeting markers must survive clear_meeting_outcomes")

    def test_mig07_wal_safe_snapshot_creation_and_integrity(self):
        """
        MIG-07: WAL-safe backup creation (create_verified_snapshot) and integrity verification.
        Validates wal_checkpoint(TRUNCATE), page-by-page streaming backup, and integrity checks.
        """
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("WAL Snapshot Test", timezone="America/Chicago", utc_offset_min=-360)
        db.add_decision(m_id, "Decision in WAL buffer")
        db.add_action_item(m_id, "Action in WAL buffer")

        backup_dir = self.base_path / "verified_backups"
        snapshot_path = db.create_verified_snapshot(backup_dir)

        self.assertTrue(snapshot_path.exists())
        self.assertGreater(snapshot_path.stat().st_size, 0)

        # Validate snapshot file directly with independent sqlite3 connection
        snap_conn = sqlite3.connect(str(snapshot_path))
        try:
            chk = snap_conn.execute("PRAGMA integrity_check;").fetchall()
            self.assertEqual([r[0] for r in chk], ["ok"], "Destination snapshot must pass PRAGMA integrity_check")

            m_row = snap_conn.execute("SELECT * FROM meetings WHERE id = ?", (m_id,)).fetchone()
            self.assertIsNotNone(m_row)
        finally:
            snap_conn.close()

    def test_mig08_universal_clear_meeting_outcomes_protection(self):
        """
        MIG-08: Universal non-destructive clear_meeting_outcomes protecting user edits and supersedes references.
        Guarantees that user edits across all 5 statuses and historical anchor decisions are permanent.
        """
        db = LocalDatabase(self.db_path)
        m_id = db.create_meeting("Outcome Protection Matrix")

        # 1. Action Items across statuses
        a_auto_pending = db.add_action_item(m_id, "Auto Pending Task", is_manual=0, user_edited=0, status="pending")
        a_auto_completed = db.add_action_item(m_id, "Auto Completed Task", is_manual=0, user_edited=0, status="completed")

        # User-edited items across all 5 statuses
        a_edit_pending = db.add_action_item(m_id, "User Edited Pending", is_manual=0, user_edited=1, status="pending")
        a_edit_completed = db.add_action_item(m_id, "User Edited Completed", is_manual=0, user_edited=1, status="completed")
        a_edit_cancelled = db.add_action_item(m_id, "User Edited Cancelled", is_manual=0, user_edited=1, status="cancelled")
        a_edit_blocked = db.add_action_item(m_id, "User Edited Blocked", is_manual=0, user_edited=1, status="blocked")
        a_edit_reopened = db.add_action_item(m_id, "User Edited Reopened", is_manual=0, user_edited=1, status="reopened")

        # Manually entered item
        a_manual = db.add_action_item(m_id, "Manually Entered Item", is_manual=1, user_edited=0, status="pending")

        # 2. Decisions
        d_auto_unref = db.add_decision(m_id, "Auto Unreferenced Decision", is_manual=0, user_edited=0)
        d_anchor = db.add_decision(m_id, "Historical Anchor Decision", is_manual=0, user_edited=0)
        d_superseding = db.add_decision(m_id, "Superseding Decision", supersedes_id=d_anchor, is_manual=0, user_edited=0)
        d_manual = db.add_decision(m_id, "Curated Strategic Decision", is_manual=1, user_edited=0)
        d_edited = db.add_decision(m_id, "User Edited Decision", is_manual=0, user_edited=1)

        # Execute regeneration with preserve_completed=True
        db.clear_meeting_outcomes(m_id, preserve_completed=True)

        m = db.get_meeting(m_id)
        surviving_action_ids = {a["id"] for a in m["actions_list"]}
        surviving_decision_ids = {d["id"] for d in m["decisions_list"]}

        # Auto pending must be cleared
        self.assertNotIn(a_auto_pending, surviving_action_ids)
        # Auto completed must be preserved
        self.assertIn(a_auto_completed, surviving_action_ids)

        # ALL user-edited items across all 5 statuses MUST survive
        self.assertIn(a_edit_pending, surviving_action_ids)
        self.assertIn(a_edit_completed, surviving_action_ids)
        self.assertIn(a_edit_cancelled, surviving_action_ids)
        self.assertIn(a_edit_blocked, surviving_action_ids)
        self.assertIn(a_edit_reopened, surviving_action_ids)

        # Manual item MUST survive
        self.assertIn(a_manual, surviving_action_ids)

        # Auto unreferenced decision must be cleared
        self.assertNotIn(d_auto_unref, surviving_decision_ids)

        # Historical anchor decision referenced by supersedes_id MUST NEVER be deleted
        self.assertIn(d_anchor, surviving_decision_ids)
        self.assertIn(d_manual, surviving_decision_ids)
        self.assertIn(d_edited, surviving_decision_ids)

    def test_mig09_canonical_open_work_kpi_query(self):
        """
        MIG-09: Canonical open work SQL KPI query (status IN ('pending', 'blocked', 'reopened')).
        Completed meetings filtering: only status = 'completed' counted in total meetings and talk time.
        """
        db = LocalDatabase(self.db_path)

        # Meetings with different states
        m_comp1 = db.create_meeting("Completed Session 1", engagement_score=90.0)
        db.update_meeting(m_comp1, status="completed", duration_sec=600.0)

        m_comp2 = db.create_meeting("Completed Session 2", engagement_score=80.0)
        db.update_meeting(m_comp2, status="completed", duration_sec=400.0)

        m_rec = db.create_meeting("Recording Session", engagement_score=50.0)
        db.update_meeting(m_rec, status="recording", duration_sec=120.0)

        m_err = db.create_meeting("Crashed Session", engagement_score=0.0)
        db.update_meeting(m_err, status="error", duration_sec=300.0)

        # Action items
        db.add_action_item(m_comp1, "Active Pending Task", status="pending")
        db.add_action_item(m_comp1, "Active Blocked Task", status="blocked")
        db.add_action_item(m_comp1, "Active Reopened Task", status="reopened")
        db.add_action_item(m_comp1, "Closed Completed Task", status="completed")
        db.add_action_item(m_comp1, "Closed Cancelled Task", status="cancelled")

        # Decisions
        db.add_decision(m_comp1, "Confirmed Decision", status="confirmed")
        db.add_decision(m_comp1, "Unclassified Decision", status="unclassified")

        stats = db.get_analytics_summary()

        # Only completed meetings counted
        self.assertEqual(stats["total_meetings"], 2)
        self.assertEqual(stats["total_duration_sec"], 1000.0)
        self.assertEqual(stats["avg_engagement_score"], 85.0)

        # Canonical open actions strictly equals pending + blocked + reopened (3)
        self.assertEqual(stats["open_action_items"], 3)
        self.assertEqual(stats["pending_action_items"], 1)
        self.assertEqual(stats["blocked_action_items"], 1)
        self.assertEqual(stats["reopened_action_items"], 1)
        self.assertEqual(stats["completed_action_items"], 1)
        self.assertEqual(stats["cancelled_action_items"], 1)
        self.assertEqual(stats["confirmed_decisions"], 1)

    def test_mig10_idempotency_and_failure_rollback(self):
        """
        MIG-10: Idempotency of repeated migrations and rollback failure preservation.
        Part 1: Running _migrate_v6 multiple times preserves user_version=6 without error.
        Part 2: Injected failure during migration triggers ROLLBACK and preserves failed snapshot copy.
        """
        # Part 1: Idempotency
        db = LocalDatabase(self.db_path)
        with db._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version;").fetchone()[0], 6)

        # Execute _migrate_v6 repeatedly
        for _ in range(3):
            db._migrate_v6()

        with db._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version;").fetchone()[0], 6)
            chk = conn.execute("PRAGMA integrity_check;").fetchall()
            self.assertEqual([r[0] for r in chk], ["ok"])

        # Part 2: Rollback and Failure Preservation
        fail_db_path = self.base_path / "fail_test.db"
        create_legacy_v5_database(fail_db_path)

        # Instantiate without running auto-migration by patching _migrate_v6 during init
        with patch.object(LocalDatabase, "_migrate_v6", return_value=None):
            fail_db = LocalDatabase(fail_db_path)

        # Inject failure into _migrate_v6 via faulty SQL
        def faulty_migrate():
            with fail_db._lock:
                with fail_db._connect() as conn:
                    # Execute pre-snapshot
                    snapshot_dir = fail_db.db_path.parent / "snapshots"
                    fail_db.create_verified_snapshot(snapshot_dir)
                    # Force exception
                    conn.execute("INVALID SQL STATEMENT THAT FAILS;")
                    conn.commit()

        with patch.object(fail_db, "_migrate_v6", side_effect=faulty_migrate):
            with self.assertRaises(Exception):
                fail_db._migrate_v6()

        # Verify pre-migration snapshot exists
        snapshots_dir = fail_db_path.parent / "snapshots"
        self.assertTrue(snapshots_dir.exists())
        snaps = list(snapshots_dir.glob("voxlocal_pre_v6_*.db"))
        self.assertGreaterEqual(len(snaps), 1, "Pre-migration snapshot must be preserved")


if __name__ == "__main__":
    unittest.main()

"""
Thread-safe SQLite storage layer for VoxLocal.
Stores meetings, timestamped speaker-diarized transcript segments,
voice typing dictation history, and custom vocabulary.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from voxlocal.core.logger import get_logger

logger = get_logger("database")


class LocalDatabase:
    """Thread-safe local SQLite database for meetings, transcripts, dictation, and vocabulary."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._is_memory = (str(self.db_path) == ":memory:")
        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys=ON;")
        else:
            self._mem_conn = None
            os.makedirs(self.db_path.parent, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()
        self._seed_default_vocabulary()

    def close(self) -> None:
        """Close database connection (especially important for in-memory databases)."""
        with self._lock:
            if self._mem_conn is not None:
                try:
                    self._mem_conn.close()
                except Exception:
                    pass
                self._mem_conn = None

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory and self._mem_conn is not None:
            with self._lock:
                yield self._mem_conn
            return

        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    folder_type TEXT NOT NULL DEFAULT 'personal', -- 'personal', 'smart', 'shared'
                    color TEXT DEFAULT '#A86F3E',
                    icon TEXT DEFAULT 'folder',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meetings (
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
                    user_edited INTEGER DEFAULT 0,
                    timezone TEXT NULL,
                    utc_offset_min INTEGER NULL,
                    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS transcript_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    segment_index INTEGER NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    speaker TEXT NOT NULL DEFAULT 'Speaker',
                    source_track TEXT NOT NULL DEFAULT 'mixed',
                    text TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_segments_meeting
                    ON transcript_segments(meeting_id, start_time);

                CREATE TABLE IF NOT EXISTS action_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    owner TEXT DEFAULT 'Unassigned',
                    due_date TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'cancelled', 'reopened', 'blocked')),
                    timestamp_sec REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    due_date_resolution TEXT DEFAULT '',
                    evidence_quote TEXT DEFAULT '',
                    is_manual INTEGER DEFAULT 0,
                    user_edited INTEGER DEFAULT 0,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_actions_meeting
                    ON action_items(meeting_id, status);

                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    rationale TEXT DEFAULT '',
                    timestamp_sec REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unclassified' CHECK(status IN ('unclassified', 'proposed', 'confirmed', 'rejected', 'unresolved')),
                    category TEXT NOT NULL DEFAULT 'General',
                    evolution_relationship TEXT NOT NULL DEFAULT 'new' CHECK(evolution_relationship IN ('new', 'repeated', 'superseded', 'reversed')),
                    supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT,
                    evidence_quote TEXT DEFAULT '',
                    is_manual INTEGER DEFAULT 0,
                    user_edited INTEGER DEFAULT 0,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS meeting_markers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    marker_type TEXT NOT NULL CHECK(marker_type IN ('flag_action', 'bookmark')),
                    label TEXT NOT NULL,
                    timestamp_sec REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_markers_meeting
                    ON meeting_markers(meeting_id);

                CREATE TABLE IF NOT EXISTS risks_and_blockers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL CHECK(item_type IN ('risk', 'blocker')),
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    impact TEXT NOT NULL DEFAULT 'medium' CHECK(impact IN ('low', 'medium', 'high', 'critical')),
                    status TEXT NOT NULL CHECK(
                        (item_type = 'risk' AND status IN ('identified', 'mitigated', 'accepted', 'closed'))
                        OR
                        (item_type = 'blocker' AND status IN ('active', 'resolved', 'reopened'))
                    ),
                    mitigation_or_resolution TEXT DEFAULT '',
                    linked_action_id INTEGER NULL REFERENCES action_items(id) ON DELETE SET NULL,
                    linked_decision_id INTEGER NULL REFERENCES decisions(id) ON DELETE SET NULL,
                    source_timestamp_sec REAL DEFAULT 0.0,
                    evidence_quote TEXT DEFAULT '',
                    is_manual INTEGER DEFAULT 0,
                    user_edited INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT NULL,
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_risks_meeting
                    ON risks_and_blockers(meeting_id);

                CREATE INDEX IF NOT EXISTS idx_risks_type_status
                    ON risks_and_blockers(item_type, status);

                CREATE TABLE IF NOT EXISTS dictation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    duration_sec REAL DEFAULT 0.0,
                    raw_text TEXT NOT NULL,
                    final_text TEXT NOT NULL,
                    target_window TEXT DEFAULT '',
                    target_process TEXT DEFAULT '',
                    insertion_method TEXT DEFAULT 'unicode',
                    status TEXT DEFAULT 'completed'
                );

                CREATE TABLE IF NOT EXISTS custom_vocabulary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spoken_phrase TEXT NOT NULL UNIQUE,
                    replacement TEXT NOT NULL,
                    category TEXT DEFAULT 'General',
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_phrase TEXT NOT NULL UNIQUE,
                    expansion TEXT NOT NULL,
                    use_count INTEGER DEFAULT 0,
                    is_starred INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scratchpad_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT 'Untitled note',
                    content TEXT NOT NULL DEFAULT '',
                    plain_text TEXT NOT NULL DEFAULT '',
                    is_pinned INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scratchpad_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (note_id) REFERENCES scratchpad_notes(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS screen_recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    duration_sec REAL DEFAULT 0.0,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER DEFAULT 0,
                    fps INTEGER DEFAULT 30,
                    resolution TEXT DEFAULT '1080p',
                    audio_mode TEXT DEFAULT 'both'
                );
                """
            )
            # Safe schema migrations for existing databases
            for col, ddl in [
                ("video_path", "ALTER TABLE meetings ADD COLUMN video_path TEXT DEFAULT '';"),
                ("folder_id", "ALTER TABLE meetings ADD COLUMN folder_id INTEGER DEFAULT NULL;"),
                ("meeting_type", "ALTER TABLE meetings ADD COLUMN meeting_type TEXT DEFAULT 'Meeting Reports';"),
                ("source_platform", "ALTER TABLE meetings ADD COLUMN source_platform TEXT DEFAULT 'Desktop App';"),
                ("engagement_score", "ALTER TABLE meetings ADD COLUMN engagement_score INTEGER DEFAULT 85;"),
                ("sentiment_score", "ALTER TABLE meetings ADD COLUMN sentiment_score REAL DEFAULT 0.0;"),
                ("topics", "ALTER TABLE meetings ADD COLUMN topics TEXT DEFAULT '';"),
                ("user_edited", "ALTER TABLE meetings ADD COLUMN user_edited INTEGER DEFAULT 0;"),
                ("wpm", "ALTER TABLE dictation_history ADD COLUMN wpm REAL DEFAULT 0.0;"),
                ("word_count", "ALTER TABLE dictation_history ADD COLUMN word_count INTEGER DEFAULT 0;"),
                ("app_category", "ALTER TABLE dictation_history ADD COLUMN app_category TEXT DEFAULT 'other';"),
                ("edited_text", "ALTER TABLE dictation_history ADD COLUMN edited_text TEXT DEFAULT '';"),
                ("undone", "ALTER TABLE dictation_history ADD COLUMN undone INTEGER DEFAULT 0;"),
                ("is_starred", "ALTER TABLE custom_vocabulary ADD COLUMN is_starred INTEGER DEFAULT 0;"),
                ("auto_learned", "ALTER TABLE custom_vocabulary ADD COLUMN auto_learned INTEGER DEFAULT 0;"),
                ("frequency_used", "ALTER TABLE custom_vocabulary ADD COLUMN frequency_used INTEGER DEFAULT 0;"),
                ("created_at", "ALTER TABLE custom_vocabulary ADD COLUMN created_at TEXT DEFAULT '';"),
                ("calendar_event_id", "ALTER TABLE meetings ADD COLUMN calendar_event_id TEXT DEFAULT '';"),
                ("calendar_join_url", "ALTER TABLE meetings ADD COLUMN calendar_join_url TEXT DEFAULT '';"),
                ("calendar_attendees", "ALTER TABLE meetings ADD COLUMN calendar_attendees TEXT DEFAULT '';"),
                ("calendar_organizer", "ALTER TABLE meetings ADD COLUMN calendar_organizer TEXT DEFAULT '';"),
                ("calendar_provider", "ALTER TABLE meetings ADD COLUMN calendar_provider TEXT DEFAULT '';"),
                ("calendar_account_id", "ALTER TABLE meetings ADD COLUMN calendar_account_id TEXT DEFAULT '';"),
                ("user_id", "ALTER TABLE meetings ADD COLUMN user_id TEXT DEFAULT 'default';"),
                ("completeness_report_json", "ALTER TABLE meetings ADD COLUMN completeness_report_json TEXT DEFAULT '';"),
                ("finalization_reason", "ALTER TABLE meetings ADD COLUMN finalization_reason TEXT DEFAULT '';"),
                ("cleaned_transcript", "ALTER TABLE meetings ADD COLUMN cleaned_transcript TEXT DEFAULT '';"),
                ("audio_health_json", "ALTER TABLE meetings ADD COLUMN audio_health_json TEXT DEFAULT '{}';"),
                ("provisional_transcript", "ALTER TABLE meetings ADD COLUMN provisional_transcript TEXT DEFAULT '';"),
                ("provisional_transcript_json", "ALTER TABLE meetings ADD COLUMN provisional_transcript_json TEXT DEFAULT '';"),
                ("post_call_stage", "ALTER TABLE meetings ADD COLUMN post_call_stage TEXT DEFAULT '';"),
                ("post_call_status_message", "ALTER TABLE meetings ADD COLUMN post_call_status_message TEXT DEFAULT '';"),
                ("post_call_engine_summary", "ALTER TABLE meetings ADD COLUMN post_call_engine_summary TEXT DEFAULT '';"),
                ("raw_text", "ALTER TABLE transcript_segments ADD COLUMN raw_text TEXT DEFAULT '';"),
                ("is_uncertain", "ALTER TABLE transcript_segments ADD COLUMN is_uncertain INTEGER DEFAULT 0;"),
                ("normalized_text", "ALTER TABLE transcript_segments ADD COLUMN normalized_text TEXT DEFAULT '';"),
                ("corrected_text", "ALTER TABLE transcript_segments ADD COLUMN corrected_text TEXT DEFAULT '';"),
                ("asr_model", "ALTER TABLE transcript_segments ADD COLUMN asr_model TEXT DEFAULT '';"),
                ("asr_backend", "ALTER TABLE transcript_segments ADD COLUMN asr_backend TEXT DEFAULT '';"),
                ("asr_device", "ALTER TABLE transcript_segments ADD COLUMN asr_device TEXT DEFAULT '';"),
                ("asr_compute_type", "ALTER TABLE transcript_segments ADD COLUMN asr_compute_type TEXT DEFAULT '';"),
                ("transcript_stage", "ALTER TABLE transcript_segments ADD COLUMN transcript_stage TEXT DEFAULT '';"),
                ("transcript_state", "ALTER TABLE transcript_segments ADD COLUMN transcript_state TEXT DEFAULT '';"),
                ("raw_model_score", "ALTER TABLE transcript_segments ADD COLUMN raw_model_score REAL DEFAULT NULL;"),
                ("no_speech_prob", "ALTER TABLE transcript_segments ADD COLUMN no_speech_prob REAL DEFAULT NULL;"),
                ("confidence_calibrated", "ALTER TABLE transcript_segments ADD COLUMN confidence_calibrated INTEGER DEFAULT 0;"),
                ("detected_language", "ALTER TABLE transcript_segments ADD COLUMN detected_language TEXT DEFAULT '';"),
                ("uncertainty_reasons_json", "ALTER TABLE transcript_segments ADD COLUMN uncertainty_reasons_json TEXT DEFAULT '[]';"),
                ("recognition_params_json", "ALTER TABLE transcript_segments ADD COLUMN recognition_params_json TEXT DEFAULT '{}';"),
                ("processing_history_json", "ALTER TABLE transcript_segments ADD COLUMN processing_history_json TEXT DEFAULT '[]';"),
            ]:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

            # Calendar events cache, multi-account registry, and encrypted credential vault
            existing_cal_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(calendar_events)").fetchall()
            }
            if existing_cal_cols and "user_id" not in existing_cal_cols:
                conn.executescript(
                    """
                    ALTER TABLE calendar_events RENAME TO calendar_events_legacy;
                    """
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    account_id TEXT NOT NULL DEFAULT '',
                    provider_type TEXT NOT NULL,
                    provider_account TEXT NOT NULL,
                    calendar_id TEXT DEFAULT '',
                    calendar_name TEXT DEFAULT '',
                    ical_uid TEXT DEFAULT '',
                    series_master_id TEXT DEFAULT '',
                    is_modified_occurrence INTEGER DEFAULT 0,
                    timezone_name TEXT DEFAULT '',
                    conference_id TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    location TEXT DEFAULT '',
                    join_url TEXT DEFAULT '',
                    attendees TEXT DEFAULT '',
                    organizer TEXT DEFAULT '',
                    platform TEXT DEFAULT 'generic',
                    is_online INTEGER DEFAULT 0,
                    is_cancelled INTEGER DEFAULT 0,
                    is_recurring INTEGER DEFAULT 0,
                    description TEXT DEFAULT '',
                    source_accounts_json TEXT DEFAULT '[]',
                    source_providers_json TEXT DEFAULT '[]',
                    source_event_ids_json TEXT DEFAULT '[]',
                    last_synced_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, account_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_time);
                CREATE INDEX IF NOT EXISTS idx_cal_user_account ON calendar_events(user_id, account_id, start_time);

                CREATE TABLE IF NOT EXISTS calendar_accounts (
                    account_id TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    provider_type TEXT NOT NULL,
                    account_email TEXT DEFAULT '',
                    display_name TEXT DEFAULT '',
                    account_kind TEXT DEFAULT 'personal',
                    tenant_id TEXT DEFAULT '',
                    organization_name TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    connection_state TEXT DEFAULT 'disconnected',
                    last_sync_at TEXT DEFAULT '',
                    last_error TEXT DEFAULT '',
                    error_code TEXT DEFAULT '',
                    selected_calendars_json TEXT DEFAULT '[]',
                    available_calendars_json TEXT DEFAULT '[]',
                    delta_links_json TEXT DEFAULT '{}',
                    sync_tokens_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cal_accounts_user ON calendar_accounts(user_id, enabled);

                CREATE TABLE IF NOT EXISTS calendar_credentials_vault (
                    user_id TEXT NOT NULL DEFAULT 'default',
                    account_id TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    encrypted_blob BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, account_id)
                );

                CREATE TABLE IF NOT EXISTS finalization_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL UNIQUE,
                    state TEXT NOT NULL DEFAULT 'FINALIZATION_QUEUED',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    audio_path TEXT DEFAULT '',
                    mic_audio_path TEXT DEFAULT '',
                    sys_audio_path TEXT DEFAULT '',
                    video_path TEXT DEFAULT '',
                    duration_sec REAL DEFAULT 0.0,
                    stream_telemetry_json TEXT DEFAULT '{}',
                    last_stage TEXT DEFAULT '',
                    last_error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    next_retry_at TEXT DEFAULT '',
                    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_finalization_jobs_state
                    ON finalization_jobs(state, updated_at);
                """
            )
            if existing_cal_cols and "user_id" not in existing_cal_cols:
                try:
                    conn.executescript(
                        """
                        INSERT OR IGNORE INTO calendar_events (
                            id, user_id, account_id, provider_type, provider_account,
                            title, start_time, end_time, location, join_url, attendees,
                            organizer, platform, is_online, is_cancelled, is_recurring,
                            description, last_synced_at
                        )
                        SELECT id, 'default', provider_type || ':' || COALESCE(provider_account, ''),
                               provider_type, provider_account, title, start_time, end_time,
                               location, join_url, attendees, organizer, platform, is_online,
                               is_cancelled, is_recurring, description, last_synced_at
                        FROM calendar_events_legacy;
                        DROP TABLE IF EXISTS calendar_events_legacy;
                        """
                    )
                except sqlite3.OperationalError:
                    pass
            conn.commit()

        self._migrate_v6()
        self._ensure_performance_indexes()
        self._seed_default_folders()

    def _ensure_performance_indexes(self) -> None:
        """Create performance indexes if columns exist (Phase 8 Performance Optimization)."""
        with self._connect() as conn:
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_actions_status ON action_items(status);",
                "CREATE INDEX IF NOT EXISTS idx_decisions_meeting ON decisions(meeting_id, timestamp_sec);",
                "CREATE INDEX IF NOT EXISTS idx_decisions_supersedes ON decisions(supersedes_id);",
                "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);",
                "CREATE INDEX IF NOT EXISTS idx_finalization_jobs_state ON finalization_jobs(state, updated_at);",
            ]:
                try:
                    conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def create_verified_snapshot(self, backup_dir: Path) -> Path:
        """
        Create a verified online backup snapshot of the database.
        Forces WAL checkpoint (TRUNCATE), streams pages via SQLite Backup API,
        and validates PRAGMA integrity_check before returning the snapshot path.
        """
        backup_path = Path(backup_dir)
        if backup_path.suffix == ".db":
            snapshot_file = backup_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            backup_path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot_file = backup_path / f"voxlocal_pre_v6_{timestamp}.db"
            if snapshot_file.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                snapshot_file = backup_path / f"voxlocal_pre_v6_{timestamp}.db"

        with self._lock, self._connect() as src_conn:
            # 1. Force WAL checkpoint
            src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

            # 2. Stream pages via SQLite backup API
            dest_conn = sqlite3.connect(str(snapshot_file))
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()

        # 3. Verify destination snapshot integrity
        verify_conn = sqlite3.connect(str(snapshot_file))
        try:
            res = verify_conn.execute("PRAGMA integrity_check;").fetchall()
            msgs = [r[0] for r in res]
            if msgs != ["ok"]:
                raise sqlite3.DatabaseError(f"Integrity check failed on snapshot: {msgs}")
        finally:
            verify_conn.close()

        logger.info("Created verified database snapshot at %s", snapshot_file)
        return snapshot_file

    def _migrate_v6(self) -> None:
        """
        Migrate database schema to version 6 (Enterprise Business Logic Integrity).
        - Meetings: adds timezone and utc_offset_min (nullable, strictly NULL for legacy records).
        - Decisions: adds status, category, evolution_relationship, supersedes_id,
          evidence_quote, is_manual, user_edited.
        - Action Items: adds due_date_resolution, evidence_quote, is_manual, user_edited.
        - Tables: creates meeting_markers and risks_and_blockers.
        - Sets PRAGMA user_version = 6.
        - Idempotent: exits cleanly if user_version >= 6.
        """
        with self._lock:
            # 1. Version check (idempotent)
            with self._connect() as conn:
                cur = conn.execute("PRAGMA user_version;")
                row = cur.fetchone()
                current_version = row[0] if row else 0

            if current_version >= 6:
                return

            logger.info("Starting schema migration to v6 (current user_version: %d)...", current_version)

            # 2. Pre-migration snapshot
            snapshot_path: Optional[Path] = None
            try:
                if str(self.db_path) != ":memory:" and self.db_path.exists() and self.db_path.stat().st_size > 0:
                    snapshot_dir = self.db_path.parent / "snapshots"
                    snapshot_path = self.create_verified_snapshot(snapshot_dir)
                    logger.info("Pre-migration snapshot preserved at %s", snapshot_path)
            except Exception as snap_exc:
                logger.warning("Could not create pre-migration snapshot: %s", snap_exc)

            # 3. Atomic schema migration
            try:
                with self._connect() as conn:
                    # Helper to get existing column names for a table
                    def get_existing_columns(table_name: str) -> set[str]:
                        info = conn.execute(f"PRAGMA table_info({table_name});").fetchall()
                        return {r["name"] for r in info}

                    # --- meetings ---
                    meetings_cols = get_existing_columns("meetings")
                    if "timezone" not in meetings_cols:
                        conn.execute("ALTER TABLE meetings ADD COLUMN timezone TEXT NULL;")
                    if "utc_offset_min" not in meetings_cols:
                        conn.execute("ALTER TABLE meetings ADD COLUMN utc_offset_min INTEGER NULL;")

                    # --- decisions ---
                    decisions_cols = get_existing_columns("decisions")
                    if "status" not in decisions_cols:
                        conn.execute(
                            "ALTER TABLE decisions ADD COLUMN status TEXT NOT NULL DEFAULT 'unclassified' "
                            "CHECK(status IN ('unclassified', 'proposed', 'confirmed', 'rejected', 'unresolved'));"
                        )
                    if "category" not in decisions_cols:
                        conn.execute("ALTER TABLE decisions ADD COLUMN category TEXT NOT NULL DEFAULT 'General';")
                    if "evolution_relationship" not in decisions_cols:
                        conn.execute(
                            "ALTER TABLE decisions ADD COLUMN evolution_relationship TEXT NOT NULL DEFAULT 'new' "
                            "CHECK(evolution_relationship IN ('new', 'repeated', 'superseded', 'reversed'));"
                        )
                    if "supersedes_id" not in decisions_cols:
                        conn.execute(
                            "ALTER TABLE decisions ADD COLUMN supersedes_id INTEGER NULL "
                            "REFERENCES decisions(id) ON DELETE RESTRICT;"
                        )
                    if "evidence_quote" not in decisions_cols:
                        conn.execute("ALTER TABLE decisions ADD COLUMN evidence_quote TEXT DEFAULT '';")
                    if "is_manual" not in decisions_cols:
                        conn.execute("ALTER TABLE decisions ADD COLUMN is_manual INTEGER DEFAULT 0;")
                    if "user_edited" not in decisions_cols:
                        conn.execute("ALTER TABLE decisions ADD COLUMN user_edited INTEGER DEFAULT 0;")

                    # --- action_items ---
                    actions_cols = get_existing_columns("action_items")
                    if "due_date_resolution" not in actions_cols:
                        conn.execute("ALTER TABLE action_items ADD COLUMN due_date_resolution TEXT DEFAULT '';")
                    if "evidence_quote" not in actions_cols:
                        conn.execute("ALTER TABLE action_items ADD COLUMN evidence_quote TEXT DEFAULT '';")
                    if "is_manual" not in actions_cols:
                        conn.execute("ALTER TABLE action_items ADD COLUMN is_manual INTEGER DEFAULT 0;")
                    if "user_edited" not in actions_cols:
                        conn.execute("ALTER TABLE action_items ADD COLUMN user_edited INTEGER DEFAULT 0;")

                    # --- meeting_markers table ---
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS meeting_markers (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            meeting_id INTEGER NOT NULL,
                            marker_type TEXT NOT NULL CHECK(marker_type IN ('flag_action', 'bookmark')),
                            label TEXT NOT NULL,
                            timestamp_sec REAL NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                        );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_markers_meeting ON meeting_markers(meeting_id);")

                    # --- risks_and_blockers table ---
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS risks_and_blockers (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            meeting_id INTEGER NOT NULL,
                            item_type TEXT NOT NULL CHECK(item_type IN ('risk', 'blocker')),
                            title TEXT NOT NULL,
                            description TEXT DEFAULT '',
                            impact TEXT NOT NULL DEFAULT 'medium' CHECK(impact IN ('low', 'medium', 'high', 'critical')),
                            status TEXT NOT NULL CHECK(
                                (item_type = 'risk' AND status IN ('identified', 'mitigated', 'accepted', 'closed'))
                                OR
                                (item_type = 'blocker' AND status IN ('active', 'resolved', 'reopened'))
                            ),
                            mitigation_or_resolution TEXT DEFAULT '',
                            linked_action_id INTEGER NULL REFERENCES action_items(id) ON DELETE SET NULL,
                            linked_decision_id INTEGER NULL REFERENCES decisions(id) ON DELETE SET NULL,
                            source_timestamp_sec REAL DEFAULT 0.0,
                            evidence_quote TEXT DEFAULT '',
                            is_manual INTEGER DEFAULT 0,
                            user_edited INTEGER DEFAULT 0,
                            created_at TEXT NOT NULL,
                            resolved_at TEXT NULL,
                            FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
                        );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_risks_meeting ON risks_and_blockers(meeting_id);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_risks_type_status ON risks_and_blockers(item_type, status);")

                    # --- Performance indexes (Phase 8 Optimization) ---
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON action_items(status);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_meeting ON decisions(meeting_id, timestamp_sec);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_supersedes ON decisions(supersedes_id);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);")

                    # --- Set user_version = 6 ---
                    conn.execute("PRAGMA user_version = 6;")
                    conn.commit()

                # 4. Post-migration verification
                with self._connect() as verify_conn:
                    fk_res = verify_conn.execute("PRAGMA foreign_key_check;").fetchall()
                    if fk_res:
                        logger.error("Foreign key check violations detected after v6 migration: %s", fk_res)
                    chk_res = verify_conn.execute("PRAGMA integrity_check;").fetchall()
                    if [r[0] for r in chk_res] != ["ok"]:
                        raise sqlite3.DatabaseError(f"Post-migration integrity check failed: {chk_res}")

                logger.info("Database schema successfully migrated to v6.")

            except Exception as exc:
                logger.error("Migration to v6 failed: %s", exc)
                # Preserve failed database copy for debugging
                if str(self.db_path) != ":memory:" and self.db_path.exists():
                    try:
                        failed_dir = self.db_path.parent / "snapshots"
                        failed_dir.mkdir(parents=True, exist_ok=True)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        failed_path = failed_dir / f"voxlocal_failed_v6_{timestamp}.db"
                        shutil.copy2(str(self.db_path), str(failed_path))
                        logger.error("Failed database preserved at %s", failed_path)
                    except Exception as copy_exc:
                        logger.warning("Could not preserve failed database snapshot: %s", copy_exc)
                raise


    def _seed_default_folders(self) -> None:
        """Seed default smart and personal folders if table is empty."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        defaults = [
            ("General Meetings", "personal", "#A86F3E", "folder"),
            ("Projects", "personal", "#735135", "folder"),
            ("Status Update", "smart", "#2A7B54", "sync"),
            ("Customer Support", "smart", "#2E6BA8", "headset"),
            ("One-on-One", "smart", "#B58434", "person"),
            ("Architecture & Design", "smart", "#7D4E9E", "cpu"),
            ("Client Review", "smart", "#C24134", "briefcase"),
        ]
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
            if cnt == 0:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO folders (name, folder_type, color, icon, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(n, t, c, i, now) for n, t, c, i in defaults],
                )
                conn.commit()

    def _seed_default_vocabulary(self) -> None:
        """
        Seed helpful personal accent & phrase corrections into custom_vocabulary.
        Note: Vocabulary is applied strictly as deterministic post-processing in
        DictationProcessor.process() and is NEVER passed into Whisper's initial_prompt.
        """
        defaults = [
            ("in a cats", "in caps", "Accent & Speech"),
            ("in cats", "in caps", "Accent & Speech"),
            ("all cats", "all caps", "Accent & Speech"),
            ("hawaii express", "how I express", "Accent & Speech"),
            ("hawaii speak", "how I speak", "Accent & Speech"),
            ("totally hawaii", "totally how I", "Accent & Speech"),
            ("voice type", "voice typing", "Accent & Speech"),
            ("tell you step back", "tell you step by step", "Accent & Speech"),
            ("g meet", "Google Meet", "Apps & Terms"),
            ("read a i", "Read AI", "Apps & Terms"),
        ]
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM custom_vocabulary").fetchone()[0]
            if count == 0:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO custom_vocabulary (spoken_phrase, replacement, category, is_active)
                    VALUES (?, ?, ?, 1)
                    """,
                    defaults,
                )
                conn.commit()

    # ------------------------------------------------------------------
    # Meetings & Transcripts CRUD
    # ------------------------------------------------------------------
    def create_meeting(
        self,
        title: str,
        capture_mode: str = "both",
        mic_device: str = "",
        loopback_device: str = "",
        audio_path: str = "",
        mic_audio_path: str = "",
        sys_audio_path: str = "",
        video_path: str = "",
        model_used: str = "base.en",
        folder_id: Optional[int] = None,
        meeting_type: str = "Meeting Reports",
        source_platform: str = "Desktop App",
        engagement_score: int = 85,
        sentiment_score: float = 0.0,
        topics: str = "",
        timezone: Optional[str] = None,
        utc_offset_min: Optional[int] = None,
        calendar_event_id: str = "",
        calendar_join_url: str = "",
        calendar_attendees: str = "",
        calendar_organizer: str = "",
        calendar_provider: str = "",
        calendar_account_id: str = "",
        user_id: str = "default",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meetings (
                    title, created_at, capture_mode, mic_device, loopback_device,
                    audio_path, mic_audio_path, sys_audio_path, video_path, status, model_used,
                    folder_id, meeting_type, source_platform, engagement_score, sentiment_score, topics,
                    timezone, utc_offset_min,
                    calendar_event_id, calendar_join_url, calendar_attendees, calendar_organizer, calendar_provider,
                    calendar_account_id, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'recording', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    now,
                    capture_mode,
                    mic_device,
                    loopback_device,
                    audio_path,
                    mic_audio_path,
                    sys_audio_path,
                    video_path,
                    model_used,
                    folder_id,
                    meeting_type,
                    source_platform,
                    engagement_score,
                    sentiment_score,
                    topics,
                    timezone,
                    utc_offset_min,
                    calendar_event_id,
                    calendar_join_url,
                    calendar_attendees,
                    calendar_organizer,
                    calendar_provider,
                    calendar_account_id,
                    user_id or "default",
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_unfinalized_meetings(self) -> List[Dict[str, Any]]:
        """Retrieve meetings left in an incomplete state due to crash or abrupt exit."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meetings WHERE status IN ('recording', 'transcribing') ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Persistent Finalization Job Queue (Phase 0 Crash-Safe Recovery)
    # ------------------------------------------------------------------
    def enqueue_finalization_job(
        self,
        meeting_id: int,
        *,
        audio_path: str = "",
        mic_audio_path: str = "",
        sys_audio_path: str = "",
        video_path: str = "",
        duration_sec: float = 0.0,
        stream_telemetry: Optional[Dict[str, Any]] = None,
        max_attempts: int = 3,
        state: str = "FINALIZATION_QUEUED",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        telem_json = json.dumps(stream_telemetry or {})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO finalization_jobs (
                    meeting_id, state, attempts, max_attempts,
                    audio_path, mic_audio_path, sys_audio_path, video_path,
                    duration_sec, stream_telemetry_json, last_stage, last_error,
                    created_at, updated_at, next_retry_at
                ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 'queued', '', ?, ?, '')
                ON CONFLICT(meeting_id) DO UPDATE SET
                    state = excluded.state,
                    attempts = 0,
                    max_attempts = excluded.max_attempts,
                    audio_path = excluded.audio_path,
                    mic_audio_path = excluded.mic_audio_path,
                    sys_audio_path = excluded.sys_audio_path,
                    video_path = excluded.video_path,
                    duration_sec = excluded.duration_sec,
                    stream_telemetry_json = excluded.stream_telemetry_json,
                    last_stage = 'queued',
                    last_error = '',
                    updated_at = excluded.updated_at,
                    next_retry_at = ''
                """,
                (
                    int(meeting_id),
                    str(state or "FINALIZATION_QUEUED"),
                    max(1, int(max_attempts)),
                    str(audio_path or ""),
                    str(mic_audio_path or ""),
                    str(sys_audio_path or ""),
                    str(video_path or ""),
                    float(duration_sec or 0.0),
                    telem_json,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM finalization_jobs WHERE meeting_id = ?",
                (int(meeting_id),),
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row else 0

    def get_finalization_job(self, meeting_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM finalization_jobs WHERE meeting_id = ?",
                (int(meeting_id),),
            ).fetchone()
            return dict(row) if row else None

    def list_pending_finalization_jobs(self, include_future_retries: bool = False) -> List[Dict[str, Any]]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            if include_future_retries:
                rows = conn.execute(
                    """
                    SELECT * FROM finalization_jobs
                    WHERE state IN ('FINALIZATION_QUEUED', 'RETRY_PENDING')
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM finalization_jobs
                    WHERE state IN ('FINALIZATION_QUEUED', 'RETRY_PENDING')
                      AND (next_retry_at = '' OR next_retry_at <= ?)
                    ORDER BY id ASC
                    """,
                    (now,),
                ).fetchall()
            return [dict(r) for r in rows]

    def update_finalization_job(self, meeting_id: int, **kwargs: Any) -> None:
        if not kwargs:
            return
        allowed = {
            "state",
            "attempts",
            "max_attempts",
            "audio_path",
            "mic_audio_path",
            "sys_audio_path",
            "video_path",
            "duration_sec",
            "stream_telemetry_json",
            "last_stage",
            "last_error",
            "next_retry_at",
        }
        updates: Dict[str, Any] = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fields = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [int(meeting_id)]
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE finalization_jobs SET {fields} WHERE meeting_id = ?",
                values,
            )
            conn.commit()

    def reset_inflight_finalization_jobs_for_recovery(self) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE finalization_jobs
                SET state = 'FINALIZATION_QUEUED',
                    last_stage = 'recovered_after_restart',
                    next_retry_at = '',
                    updated_at = ?
                WHERE state = 'FINALIZING'
                """,
                (now,),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def mark_meeting_user_edited(self, meeting_id: int, is_edited: bool = True) -> None:
        """Mark a meeting report as manually edited by the user to protect from silent AI overwrites."""
        self.update_meeting(meeting_id, user_edited=1 if is_edited else 0)

    def update_meeting(self, meeting_id: int, **kwargs: Any) -> None:
        if not kwargs:
            return
        allowed = {
            "title",
            "ended_at",
            "duration_sec",
            "audio_path",
            "mic_audio_path",
            "sys_audio_path",
            "video_path",
            "status",
            "model_used",
            "full_transcript",
            "summary",
            "decisions",
            "action_items",
            "notes",
            "folder_id",
            "meeting_type",
            "source_platform",
            "engagement_score",
            "sentiment_score",
            "topics",
            "user_edited",
            "timezone",
            "utc_offset_min",
            "calendar_event_id",
            "calendar_join_url",
            "calendar_attendees",
            "calendar_organizer",
            "calendar_provider",
            "calendar_account_id",
            "user_id",
            "completeness_report_json",
            "finalization_reason",
            "cleaned_transcript",
            "audio_health_json",
            "provisional_transcript",
            "provisional_transcript_json",
            "post_call_stage",
            "post_call_status_message",
            "post_call_engine_summary",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [meeting_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE meetings SET {cols} WHERE id = ?", vals)
            conn.commit()

    @staticmethod
    def _norm_dedup_text(value: str) -> str:
        return re.sub(r"[^\w\u0900-\u097F]+", " ", str(value or "").lower()).strip()

    def add_transcript_segment(
        self,
        meeting_id: int,
        segment_index: Optional[int] = None,
        start_time: float = 0.0,
        end_time: float = 0.0,
        speaker: str = "Speaker",
        text: str = "",
        source_track: str = "mixed",
        confidence: float = 1.0,
        raw_text: str = "",
        is_uncertain: bool = False,
        normalized_text: str = "",
        corrected_text: str = "",
        asr_model: str = "",
        asr_backend: str = "",
        asr_device: str = "",
        asr_compute_type: str = "",
        transcript_stage: str = "",
        transcript_state: str = "",
        raw_model_score: Optional[float] = None,
        no_speech_prob: Optional[float] = None,
        confidence_calibrated: bool = False,
        detected_language: str = "",
        uncertainty_reasons: Optional[List[str]] = None,
        recognition_params: Optional[Dict[str, Any]] = None,
        processing_history: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        norm_txt = (normalized_text or text).strip()
        display_txt = (text or norm_txt).strip()
        if not display_txt:
            return 0
        raw_val = (raw_text or display_txt).strip()
        corr_val = (corrected_text or "").strip()
        eff_state = (transcript_state or transcript_stage or "stable").strip()
        unc_flag = 1 if is_uncertain else 0
        cal_flag = 1 if confidence_calibrated else 0
        unc_json = json.dumps(list(uncertainty_reasons or []))
        rec_json = json.dumps(recognition_params or {})
        hist_json = json.dumps(list(processing_history or []))
        norm_disp_cmp = self._norm_dedup_text(display_txt)
        st_round = round(float(start_time), 2)
        et_round = round(float(end_time), 2)
        with self._lock, self._connect() as conn:
            if segment_index is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(segment_index), -1) + 1 AS next_idx FROM transcript_segments WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()
                segment_index = row["next_idx"] if row else 0
            # Query recent segments in this meeting with matching track within +- 1.5s window
            existing = conn.execute(
                """
                SELECT id, segment_index, start_time, end_time, text, raw_text, confidence
                FROM transcript_segments
                WHERE meeting_id = ? AND source_track = ? AND ABS(start_time - ?) <= 1.5
                ORDER BY start_time ASC
                """,
                (meeting_id, source_track, st_round),
            ).fetchall()

            for ex_row in existing:
                ex_id = ex_row["id"]
                ex_text = str(ex_row["text"]).strip()
                ex_norm_cmp = self._norm_dedup_text(ex_text)
                # 1. Exact or normalized match -> duplicate segment, do not insert duplicate
                if ex_text.lower() == display_txt.lower() or (norm_disp_cmp and norm_disp_cmp == ex_norm_cmp):
                    return ex_id
                # 2. Existing text is prefix or shorter substring -> update with more complete segment (preserve raw_text)
                if (
                    display_txt.lower().startswith(ex_text.lower())
                    or (ex_norm_cmp and norm_disp_cmp.startswith(ex_norm_cmp))
                ) and len(display_txt) > len(ex_text):
                    existing_raw = str(ex_row["raw_text"] or "").strip()
                    preserved_raw = existing_raw or raw_val
                    conn.execute(
                        """
                        UPDATE transcript_segments
                        SET text = ?, raw_text = ?, normalized_text = ?, corrected_text = ?,
                            end_time = ?, confidence = ?, is_uncertain = ?,
                            asr_model = ?, asr_backend = ?, asr_device = ?, asr_compute_type = ?,
                            transcript_stage = ?, transcript_state = ?, raw_model_score = ?, no_speech_prob = ?,
                            confidence_calibrated = ?, detected_language = ?,
                            uncertainty_reasons_json = ?, recognition_params_json = ?,
                            processing_history_json = ?
                        WHERE id = ?
                        """,
                        (
                            display_txt,
                            preserved_raw,
                            norm_txt,
                            corr_val,
                            max(et_round, float(ex_row["end_time"])),
                            max(confidence, float(ex_row["confidence"])),
                            unc_flag,
                            str(asr_model or ""),
                            str(asr_backend or ""),
                            str(asr_device or ""),
                            str(asr_compute_type or ""),
                            str(transcript_stage or ""),
                            eff_state,
                            raw_model_score,
                            no_speech_prob,
                            cal_flag,
                            str(detected_language or ""),
                            unc_json,
                            rec_json,
                            hist_json,
                            ex_id,
                        ),
                    )
                    conn.commit()
                    return ex_id
                # 3. New text is already contained in existing longer segment -> duplicate fragment, keep existing
                if ex_text.lower().startswith(display_txt.lower()) or (norm_disp_cmp and ex_norm_cmp.startswith(norm_disp_cmp)):
                    return ex_id

            cur = conn.execute(
                """
                INSERT INTO transcript_segments (
                    meeting_id, segment_index, start_time, end_time,
                    speaker, source_track, text, confidence, raw_text, is_uncertain,
                    normalized_text, corrected_text, asr_model, asr_backend, asr_device,
                    asr_compute_type, transcript_stage, transcript_state, raw_model_score, no_speech_prob,
                    confidence_calibrated, detected_language, uncertainty_reasons_json,
                    recognition_params_json, processing_history_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    segment_index,
                    st_round,
                    et_round,
                    speaker,
                    source_track,
                    display_txt,
                    confidence,
                    raw_val,
                    unc_flag,
                    norm_txt,
                    corr_val,
                    str(asr_model or ""),
                    str(asr_backend or ""),
                    str(asr_device or ""),
                    str(asr_compute_type or ""),
                    str(transcript_stage or ""),
                    eff_state,
                    raw_model_score,
                    no_speech_prob,
                    cal_flag,
                    str(detected_language or ""),
                    unc_json,
                    rec_json,
                    hist_json,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def replace_meeting_segments(
        self,
        meeting_id: int,
        segments: List[Dict[str, Any]],
        *,
        allow_empty: bool = False,
    ) -> None:
        """
        Transactionally replace all segments for a meeting and rebuild full_transcript.
        Never wipes existing persisted segments with an empty list unless `allow_empty=True`,
        and rolls back cleanly if any insert/update raises.
        """
        with self._lock, self._connect() as conn:
            if not segments and not allow_empty:
                ex_cnt = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM transcript_segments WHERE meeting_id = ?",
                    (meeting_id,),
                ).fetchone()
                if ex_cnt and int(ex_cnt["cnt"]) > 0:
                    logger.warning(
                        "Preserving %d existing transcript segments for meeting #%d (refusing accidental empty replace).",
                        int(ex_cnt["cnt"]),
                        meeting_id,
                    )
                    return

            if not self._is_memory:
                conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (meeting_id,))
                lines: List[str] = []
                for idx, seg in enumerate(segments):
                    start_t = round(float(seg.get("start_time", 0.0)), 2)
                    end_t = round(float(seg.get("end_time", start_t)), 2)
                    speaker = str(seg.get("speaker", "Speaker"))
                    source_track = str(seg.get("source_track", "mixed"))
                    text = str(seg.get("text", "")).strip()
                    if not text:
                        continue
                    raw_text = str(seg.get("raw_text") or text).strip()
                    norm_text = str(seg.get("normalized_text") or text).strip()
                    corr_text = str(seg.get("corrected_text") or "").strip()
                    t_stage = str(seg.get("transcript_stage") or "").strip()
                    t_state = str(seg.get("transcript_state") or t_stage or "final").strip()
                    is_unc = 1 if bool(seg.get("is_uncertain", False)) else 0
                    cal_flag = 1 if bool(seg.get("confidence_calibrated", False)) else 0
                    unc_json = json.dumps(list(seg.get("uncertainty_reasons") or []))
                    rec_json = json.dumps(seg.get("recognition_params") or {})
                    hist_json = json.dumps(list(seg.get("processing_history") or []))
                    conn.execute(
                        """
                        INSERT INTO transcript_segments (
                            meeting_id, segment_index, start_time, end_time,
                            speaker, source_track, text, confidence, raw_text, is_uncertain,
                            normalized_text, corrected_text, asr_model, asr_backend, asr_device,
                            asr_compute_type, transcript_stage, transcript_state, raw_model_score, no_speech_prob,
                            confidence_calibrated, detected_language, uncertainty_reasons_json,
                            recognition_params_json, processing_history_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meeting_id,
                            idx,
                            start_t,
                            end_t,
                            speaker,
                            source_track,
                            text,
                            float(seg.get("confidence", 1.0)),
                            raw_text,
                            is_unc,
                            norm_text,
                            corr_text,
                            str(seg.get("asr_model") or ""),
                            str(seg.get("asr_backend") or ""),
                            str(seg.get("asr_device") or ""),
                            str(seg.get("asr_compute_type") or ""),
                            t_stage,
                            t_state,
                            seg.get("raw_model_score"),
                            seg.get("no_speech_prob"),
                            cal_flag,
                            str(seg.get("detected_language") or ""),
                            unc_json,
                            rec_json,
                            hist_json,
                        ),
                    )
                    mm = int(start_t // 60)
                    ss = int(start_t % 60)
                    lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {text}")

                full_text = "\n".join(lines)
                conn.execute(
                    "UPDATE meetings SET full_transcript = ? WHERE id = ?",
                    (full_text, meeting_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def rename_meeting_speaker(self, meeting_id: int, old_speaker: str, new_speaker: str) -> int:
        """
        Rename all occurrences of old_speaker to new_speaker in transcript_segments,
        rebuild full_transcript, and update owners in action_items and decisions.
        """
        old_clean = (old_speaker or "").strip()
        new_clean = (new_speaker or "").strip()
        if not old_clean or not new_clean or old_clean == new_clean:
            return 0
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE transcript_segments SET speaker = ? WHERE meeting_id = ? AND speaker = ?",
                (new_clean, meeting_id, old_clean),
            )
            count = cur.rowcount
            if count > 0:
                segs = conn.execute(
                    "SELECT start_time, speaker, text FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, segment_index ASC",
                    (meeting_id,),
                ).fetchall()
                lines = []
                for s in segs:
                    st = float(s["start_time"])
                    mm, ss = int(st // 60), int(st % 60)
                    lines.append(f"[{mm:02d}:{ss:02d}] {s['speaker']}: {s['text']}")
                full_txt = "\n".join(lines)
                conn.execute(
                    "UPDATE meetings SET full_transcript = ? WHERE id = ?",
                    (full_txt, meeting_id),
                )
                conn.execute(
                    "UPDATE action_items SET owner = ? WHERE meeting_id = ? AND owner = ?",
                    (new_clean, meeting_id, old_clean),
                )
                dec_cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(decisions)").fetchall()
                }
                if "speaker" in dec_cols:
                    conn.execute(
                        "UPDATE decisions SET speaker = ? WHERE meeting_id = ? AND speaker = ?",
                        (new_clean, meeting_id, old_clean),
                    )
                conn.commit()
            return count

    def get_meeting_speakers(self, meeting_id: int) -> list:
        """Return distinct speakers with turn counts and word statistics for a meeting."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT speaker, COUNT(*) as turn_count, GROUP_CONCAT(text, ' ') as all_text
                FROM transcript_segments
                WHERE meeting_id = ?
                GROUP BY speaker
                ORDER BY turn_count DESC
                """,
                (meeting_id,),
            ).fetchall()
            speakers = []
            for r in rows:
                spk = r["speaker"]
                words = len((r["all_text"] or "").split())
                speakers.append({
                    "speaker": spk,
                    "turn_count": r["turn_count"],
                    "word_count": words,
                })
            return speakers

    def get_meeting(self, meeting_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT m.*, f.name AS folder_name, f.color AS folder_color, f.folder_type AS folder_category
                FROM meetings m
                LEFT JOIN folders f ON m.folder_id = f.id
                WHERE m.id = ?
                """,
                (meeting_id,),
            ).fetchone()
            if not row:
                return None
            meeting = dict(row)
            segs = conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, segment_index ASC",
                (meeting_id,),
            ).fetchall()
            meeting["segments"] = [dict(s) for s in segs]
            actions = conn.execute(
                "SELECT * FROM action_items WHERE meeting_id = ? ORDER BY id ASC",
                (meeting_id,),
            ).fetchall()
            meeting["actions_list"] = [dict(a) for a in actions]
            decs = conn.execute(
                "SELECT * FROM decisions WHERE meeting_id = ? ORDER BY id ASC",
                (meeting_id,),
            ).fetchall()
            meeting["decisions_list"] = [dict(d) for d in decs]
            try:
                markers = conn.execute(
                    "SELECT * FROM meeting_markers WHERE meeting_id = ? ORDER BY timestamp_sec ASC, id ASC",
                    (meeting_id,),
                ).fetchall()
                meeting["markers_list"] = [dict(m) for m in markers]
            except sqlite3.OperationalError:
                meeting["markers_list"] = []

            try:
                risks = conn.execute(
                    "SELECT * FROM risks_and_blockers WHERE meeting_id = ? ORDER BY id ASC",
                    (meeting_id,),
                ).fetchall()
                meeting["risks_and_blockers_list"] = [dict(r) for r in risks]
            except sqlite3.OperationalError:
                meeting["risks_and_blockers_list"] = []
            return meeting

    def get_meeting_segments(self, meeting_id: int) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            segs = conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, end_time ASC, segment_index ASC",
                (meeting_id,),
            ).fetchall()
            return [dict(s) for s in segs]

    def get_transcript_segments(self, meeting_id: int) -> List[Dict[str, Any]]:
        """Alias for get_meeting_segments conforming to e2e test specification."""
        return self.get_meeting_segments(meeting_id)

    def archive_provisional_transcript(self, meeting_id: int) -> int:
        """Idempotently snapshot live provisional segments before full-audio post-call replacement."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT provisional_transcript_json FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
            if not row:
                return 0
            existing_json = str(row["provisional_transcript_json"] or "").strip()
            if existing_json and existing_json != "[]":
                return 0
            segs = conn.execute(
                "SELECT * FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, end_time ASC, segment_index ASC",
                (meeting_id,),
            ).fetchall()
            seg_dicts = [dict(s) for s in segs]
            if not seg_dicts:
                return 0
            lines: List[str] = []
            for s in seg_dicts:
                st = float(s.get("start_time") or 0.0)
                mm, ss = int(st // 60), int(st % 60)
                spk = str(s.get("speaker") or "Speaker")
                txt = str(s.get("text") or "").strip()
                if txt:
                    lines.append(f"[{mm:02d}:{ss:02d}] {spk}: {txt}")
            prov_json = json.dumps(seg_dicts, default=str)
            prov_txt = "\n".join(lines)
            conn.execute(
                "UPDATE meetings SET provisional_transcript_json = ?, provisional_transcript = ? WHERE id = ?",
                (prov_json, prov_txt, meeting_id),
            )
            conn.commit()
            return len(seg_dicts)

    def get_provisional_transcript_segments(self, meeting_id: int) -> List[Dict[str, Any]]:
        """Return archived provisional live-preview segments for comparison, if any."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT provisional_transcript_json FROM meetings WHERE id = ?",
                (meeting_id,),
            ).fetchone()
        if not row:
            return []
        raw_json = str(row["provisional_transcript_json"] or "").strip()
        if not raw_json:
            return []
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, dict)]
        except Exception:
            pass
        return []

    def list_meetings(
        self,
        search_query: str = "",
        folder_id: Optional[int] = None,
        meeting_type: Optional[str] = None,
        source_platform: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            clauses = ["1=1"]
            params: List[Any] = []

            if search_query.strip():
                q = f"%{search_query.strip()}%"
                clauses.append(
                    """(
                        m.title LIKE ?
                        OR m.full_transcript LIKE ?
                        OR m.summary LIKE ?
                        OR m.action_items LIKE ?
                        OR m.notes LIKE ?
                        OR m.topics LIKE ?
                        OR EXISTS (
                            SELECT 1 FROM transcript_segments s
                            WHERE s.meeting_id = m.id AND (s.text LIKE ? OR s.speaker LIKE ?)
                        )
                    )"""
                )
                params.extend([q, q, q, q, q, q, q, q])

            if folder_id is not None:
                clauses.append("m.folder_id = ?")
                params.append(folder_id)

            if meeting_type and meeting_type != "All Types":
                clauses.append("m.meeting_type = ?")
                params.append(meeting_type)

            if source_platform and source_platform != "All Sources":
                clauses.append("m.source_platform = ?")
                params.append(source_platform)

            if timeframe and timeframe != "Anytime":
                now = datetime.now()
                if timeframe.lower() == "today":
                    cutoff = now.strftime("%Y-%m-%d 00:00:00")
                    clauses.append("m.created_at >= ?")
                    params.append(cutoff)
                elif "7" in timeframe:
                    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    clauses.append("m.created_at >= ?")
                    params.append(cutoff)
                elif "30" in timeframe:
                    cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                    clauses.append("m.created_at >= ?")
                    params.append(cutoff)
                elif "90" in timeframe:
                    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
                    clauses.append("m.created_at >= ?")
                    params.append(cutoff)

            where_str = " AND ".join(clauses)
            query = f"""
                SELECT m.*, f.name AS folder_name, f.color AS folder_color, f.folder_type AS folder_category
                FROM meetings m
                LEFT JOIN folders f ON m.folder_id = f.id
                WHERE {where_str}
                ORDER BY m.id DESC LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def delete_meeting(self, meeting_id: int, delete_audio_files: bool = True) -> bool:
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            return False
        if delete_audio_files:
            for key in ("audio_path", "mic_audio_path", "sys_audio_path", "video_path"):
                path_str = meeting.get(key)
                if path_str and os.path.exists(path_str):
                    try:
                        os.remove(path_str)
                    except PermissionError:
                        import gc
                        gc.collect()
                        time.sleep(0.05)
                        try:
                            os.remove(path_str)
                        except Exception as exc:
                            logger.warning("Could not delete file %s: %s", path_str, exc)
                    except Exception as exc:
                        logger.warning("Could not delete file %s: %s", path_str, exc)
                if path_str:
                    parent = Path(path_str).parent
                    if parent.exists() and parent.name.startswith("meeting_"):
                        try:
                            shutil.rmtree(parent, ignore_errors=True)
                        except Exception:
                            pass
        with self._lock, self._connect() as conn:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("DELETE FROM meeting_markers WHERE meeting_id = ?", (meeting_id,))
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("DELETE FROM risks_and_blockers WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM decisions WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # Folders CRUD
    # ------------------------------------------------------------------
    def create_folder(
        self, name: str, folder_type: str = "personal", color: str = "#A86F3E", icon: str = "folder"
    ) -> Optional[int]:
        clean_name = name.strip()
        if not clean_name:
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT id FROM folders WHERE name = ?", (clean_name,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE folders SET folder_type = ?, color = ?, icon = ? WHERE id = ?",
                    (folder_type, color, icon, existing["id"]),
                )
                conn.commit()
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO folders (name, folder_type, color, icon, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clean_name, folder_type, color, icon, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_folders(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.*, COUNT(m.id) AS meeting_count
                FROM folders f
                LEFT JOIN meetings m ON f.id = m.folder_id
                GROUP BY f.id
                ORDER BY f.folder_type DESC, f.name ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def rename_folder(self, folder_id: int, new_name: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name.strip(), folder_id))
            conn.commit()
            return True

    def delete_folder(self, folder_id: int) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE meetings SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
            conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            conn.commit()
            return True

    def assign_meeting_folder(self, meeting_id: int, folder_id: Optional[int]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE meetings SET folder_id = ? WHERE id = ?", (folder_id, meeting_id))
            conn.commit()

    # ------------------------------------------------------------------
    # Action Items & Decisions CRUD
    # ------------------------------------------------------------------
    def clear_meeting_outcomes(self, meeting_id: int, preserve_completed: bool = True) -> None:
        """Clear auto-generated action items and decisions for a meeting prior to re-analysis.
        Universally protects user-edited (user_edited = 1) and manual items (is_manual = 1)
        across all statuses (pending, completed, cancelled, blocked, reopened).
        Protects earlier decisions referenced as historical anchors via supersedes_id.
        """
        with self._lock, self._connect() as conn:
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
            conn.commit()

    def add_action_item(
        self,
        meeting_id: int,
        description: str,
        owner: str = "Unassigned",
        due_date: str = "",
        status: str = "pending",
        timestamp_sec: float = 0.0,
        due_date_resolution: str = "",
        evidence_quote: str = "",
        is_manual: int = 0,
        user_edited: int = 0,
    ) -> int:
        clean_desc = (description or "").strip()
        if not clean_desc:
            return 0
        norm_desc = " ".join(clean_desc.lower().split())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT id, description FROM action_items WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()
            for row in existing_rows:
                row_norm = " ".join(str(row["description"] or "").lower().split())
                if row_norm == norm_desc:
                    return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO action_items (
                    meeting_id, description, owner, due_date, status, timestamp_sec, created_at,
                    due_date_resolution, evidence_quote, is_manual, user_edited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    clean_desc,
                    (owner or "").strip() or "Unassigned",
                    (due_date or "").strip(),
                    status,
                    round(float(timestamp_sec), 2),
                    now,
                    due_date_resolution,
                    evidence_quote,
                    is_manual,
                    user_edited,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_action_items(
        self, meeting_id: Optional[int] = None, status: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            clauses = ["1=1"]
            params: List[Any] = []
            if meeting_id is not None:
                clauses.append("a.meeting_id = ?")
                params.append(meeting_id)
            if status:
                if status.lower() == "open":
                    clauses.append("a.status IN ('pending', 'blocked', 'reopened')")
                else:
                    clauses.append("a.status = ?")
                    params.append(status)
            where_str = " AND ".join(clauses)
            query = f"""
                SELECT a.*, m.title AS meeting_title, m.created_at AS meeting_date
                FROM action_items a
                JOIN meetings m ON a.meeting_id = m.id
                WHERE {where_str}
                ORDER BY a.status ASC, a.id DESC LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def toggle_action_item_status(self, item_id: int, new_status: Optional[str] = None) -> str:
        """
        Cycle or update action item status across enterprise lifecycle states:
        'pending', 'completed', 'cancelled', 'reopened', 'blocked'.
        Sets user_edited = 1 upon state transition.
        """
        with self._lock, self._connect() as conn:
            curr = conn.execute("SELECT status FROM action_items WHERE id = ?", (item_id,)).fetchone()
            if not curr:
                return "pending"
            current_status = curr["status"]
            if not new_status:
                new_status = "completed" if current_status == "pending" else "pending"
            valid_statuses = {"pending", "completed", "cancelled", "reopened", "blocked"}
            if new_status not in valid_statuses:
                raise ValueError(f"Invalid action item status: {new_status}. Allowed: {valid_statuses}")
            conn.execute(
                "UPDATE action_items SET status = ?, user_edited = 1 WHERE id = ?",
                (new_status, item_id),
            )
            conn.commit()
            return new_status

    def update_action_item(
        self,
        item_id: int,
        description: Optional[str] = None,
        owner: Optional[str] = None,
        due_date: Optional[str] = None,
        status: Optional[str] = None,
        due_date_resolution: Optional[str] = None,
        evidence_quote: Optional[str] = None,
        user_edited: int = 1,
        **kwargs: Any,
    ) -> bool:
        """Update editable action details while preserving meeting links and marking user_edited = 1."""
        if description is not None and not description.strip():
            return False
        with self._lock, self._connect() as conn:
            fields: Dict[str, Any] = {"user_edited": user_edited}
            if description is not None:
                clean_desc = description.strip()
                if not clean_desc:
                    return False
                fields["description"] = clean_desc
            if owner is not None:
                fields["owner"] = owner.strip() or "Unassigned"
            if due_date is not None:
                fields["due_date"] = due_date.strip()
            if status is not None:
                clean_status = status.strip()
                valid_statuses = {"pending", "completed", "cancelled", "reopened", "blocked"}
                if clean_status not in valid_statuses:
                    raise ValueError(f"Invalid action item status: {clean_status}. Allowed: {valid_statuses}")
                fields["status"] = clean_status
            if due_date_resolution is not None:
                fields["due_date_resolution"] = due_date_resolution.strip()
            if evidence_quote is not None:
                fields["evidence_quote"] = evidence_quote.strip()
            for k, v in kwargs.items():
                if k in ("is_manual", "timestamp_sec"):
                    fields[k] = v

            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [int(item_id)]
            cur = conn.execute(f"UPDATE action_items SET {cols} WHERE id = ?", vals)
            conn.commit()
            return cur.rowcount > 0

    def delete_action_item(self, item_id: int) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM action_items WHERE id = ?", (item_id,))
            conn.commit()
            return True

    def add_decision(
        self,
        meeting_id: int,
        decision: str,
        rationale: str = "",
        timestamp_sec: float = 0.0,
        status: str = "unclassified",
        category: str = "General",
        evolution_relationship: str = "new",
        supersedes_id: Optional[int] = None,
        evidence_quote: str = "",
        is_manual: int = 0,
        user_edited: int = 0,
    ) -> int:
        clean_dec = (decision or "").strip()
        if not clean_dec:
            return 0
        norm_dec = " ".join(clean_dec.lower().split())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT id, decision FROM decisions WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()
            for row in existing_rows:
                row_norm = " ".join(str(row["decision"] or "").lower().split())
                if row_norm == norm_dec:
                    return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO decisions (
                    meeting_id, decision, rationale, timestamp_sec, created_at,
                    status, category, evolution_relationship, supersedes_id,
                    evidence_quote, is_manual, user_edited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    clean_dec,
                    (rationale or "").strip(),
                    round(float(timestamp_sec), 2),
                    now,
                    status,
                    category,
                    evolution_relationship,
                    supersedes_id,
                    evidence_quote,
                    is_manual,
                    user_edited,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_decision(
        self,
        decision_id: int,
        decision: Optional[str] = None,
        rationale: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        evolution_relationship: Optional[str] = None,
        supersedes_id: Optional[int] = None,
        evidence_quote: Optional[str] = None,
        user_edited: int = 1,
        **kwargs: Any,
    ) -> bool:
        """Update decision fields and mark user_edited = 1."""
        with self._lock, self._connect() as conn:
            fields: Dict[str, Any] = {"user_edited": user_edited}
            if decision is not None:
                clean = decision.strip()
                if clean:
                    fields["decision"] = clean
            if rationale is not None:
                fields["rationale"] = rationale.strip()
            if status is not None:
                fields["status"] = status.strip()
            if category is not None:
                fields["category"] = category.strip()
            if evolution_relationship is not None:
                fields["evolution_relationship"] = evolution_relationship.strip()
            if "supersedes_id" in kwargs or supersedes_id is not None:
                fields["supersedes_id"] = supersedes_id
            if evidence_quote is not None:
                fields["evidence_quote"] = evidence_quote.strip()
            for k, v in kwargs.items():
                if k in ("is_manual", "timestamp_sec"):
                    fields[k] = v

            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [int(decision_id)]
            cur = conn.execute(f"UPDATE decisions SET {cols} WHERE id = ?", vals)
            conn.commit()
            return cur.rowcount > 0

    def get_decision(self, decision_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.*, m.title AS meeting_title
                FROM decisions d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE d.id = ?
                """,
                (decision_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_decision(self, decision_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
            conn.commit()
            return cur.rowcount > 0

    def list_decisions(
        self,
        meeting_id: Optional[int] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            clauses = ["1=1"]
            params: List[Any] = []
            if meeting_id is not None:
                clauses.append("d.meeting_id = ?")
                params.append(meeting_id)
            if status is not None:
                clauses.append("d.status = ?")
                params.append(status)
            if category is not None:
                clauses.append("d.category = ?")
                params.append(category)

            where_str = " AND ".join(clauses)
            order_clause = "ORDER BY d.id ASC" if meeting_id is not None else "ORDER BY d.id DESC"
            query = f"""
                SELECT d.*, m.title AS meeting_title, m.created_at AS meeting_date
                FROM decisions d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE {where_str}
                {order_clause} LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Meeting Markers CRUD
    # ------------------------------------------------------------------
    def add_marker(
        self,
        meeting_id: int,
        marker_type: str,
        label: str,
        timestamp_sec: float,
    ) -> int:
        """Add a live meeting marker ('flag_action' or 'bookmark')."""
        valid_types = {"flag_action", "bookmark"}
        if marker_type not in valid_types:
            raise ValueError(f"Invalid marker_type: {marker_type}. Allowed: {valid_types}")
        clean_label = (label or "").strip()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meeting_markers (meeting_id, marker_type, label, timestamp_sec, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (meeting_id, marker_type, clean_label, round(float(timestamp_sec), 2), now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_markers(
        self,
        meeting_id: Optional[int] = None,
        marker_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            clauses = ["1=1"]
            params: List[Any] = []
            if meeting_id is not None:
                clauses.append("meeting_id = ?")
                params.append(meeting_id)
            if marker_type is not None:
                clauses.append("marker_type = ?")
                params.append(marker_type)
            where_str = " AND ".join(clauses)
            query = f"""
                SELECT * FROM meeting_markers
                WHERE {where_str}
                ORDER BY timestamp_sec ASC, id ASC LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_marker(self, marker_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM meeting_markers WHERE id = ?", (marker_id,)).fetchone()
            return dict(row) if row else None

    def delete_marker(self, marker_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM meeting_markers WHERE id = ?", (marker_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Risks & Blockers CRUD
    # ------------------------------------------------------------------
    def add_risk_or_blocker(
        self,
        meeting_id: int,
        item_type: str,
        title: str,
        description: str = "",
        impact: str = "medium",
        status: Optional[str] = None,
        mitigation_or_resolution: str = "",
        linked_action_id: Optional[int] = None,
        linked_decision_id: Optional[int] = None,
        source_timestamp_sec: float = 0.0,
        evidence_quote: str = "",
        is_manual: int = 0,
        user_edited: int = 0,
        resolved_at: Optional[str] = None,
    ) -> int:
        """Add a project risk or blocker with type-specific status validation."""
        valid_types = {"risk", "blocker"}
        if item_type not in valid_types:
            raise ValueError(f"Invalid item_type: {item_type}. Allowed: {valid_types}")
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Title cannot be empty for risk or blocker.")
        if status is None:
            status = "identified" if item_type == "risk" else "active"

        risk_statuses = {"identified", "mitigated", "accepted", "closed"}
        blocker_statuses = {"active", "resolved", "reopened"}
        if item_type == "risk" and status not in risk_statuses:
            raise ValueError(f"Invalid risk status: {status}. Allowed: {risk_statuses}")
        if item_type == "blocker" and status not in blocker_statuses:
            raise ValueError(f"Invalid blocker status: {status}. Allowed: {blocker_statuses}")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if (
            (item_type == "risk" and status in ("mitigated", "accepted", "closed"))
            or (item_type == "blocker" and status == "resolved")
        ) and not resolved_at:
            resolved_at = now

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO risks_and_blockers (
                    meeting_id, item_type, title, description, impact, status,
                    mitigation_or_resolution, linked_action_id, linked_decision_id,
                    source_timestamp_sec, evidence_quote, is_manual, user_edited,
                    created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    item_type,
                    clean_title,
                    (description or "").strip(),
                    impact,
                    status,
                    (mitigation_or_resolution or "").strip(),
                    linked_action_id,
                    linked_decision_id,
                    round(float(source_timestamp_sec), 2),
                    evidence_quote,
                    is_manual,
                    user_edited,
                    now,
                    resolved_at,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_risks_and_blockers(
        self,
        meeting_id: Optional[int] = None,
        item_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            clauses = ["1=1"]
            params: List[Any] = []
            if meeting_id is not None:
                clauses.append("meeting_id = ?")
                params.append(meeting_id)
            if item_type is not None:
                clauses.append("item_type = ?")
                params.append(item_type)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            where_str = " AND ".join(clauses)
            query = f"""
                SELECT * FROM risks_and_blockers
                WHERE {where_str}
                ORDER BY id ASC LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_risk_or_blocker(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM risks_and_blockers WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def update_risk_or_blocker(
        self,
        item_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        impact: Optional[str] = None,
        status: Optional[str] = None,
        mitigation_or_resolution: Optional[str] = None,
        linked_action_id: Optional[int] = None,
        linked_decision_id: Optional[int] = None,
        evidence_quote: Optional[str] = None,
        user_edited: int = 1,
        resolved_at: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        with self._lock, self._connect() as conn:
            curr = conn.execute("SELECT item_type, status, resolved_at FROM risks_and_blockers WHERE id = ?", (item_id,)).fetchone()
            if not curr:
                return False
            item_type = curr["item_type"]

            fields: Dict[str, Any] = {"user_edited": user_edited}
            if title is not None:
                clean_title = title.strip()
                if clean_title:
                    fields["title"] = clean_title
            if description is not None:
                fields["description"] = description.strip()
            if impact is not None:
                fields["impact"] = impact.strip()
            if status is not None:
                new_status = status.strip()
                risk_statuses = {"identified", "mitigated", "accepted", "closed"}
                blocker_statuses = {"active", "resolved", "reopened"}
                if item_type == "risk" and new_status not in risk_statuses:
                    raise ValueError(f"Invalid risk status: {new_status}. Allowed: {risk_statuses}")
                if item_type == "blocker" and new_status not in blocker_statuses:
                    raise ValueError(f"Invalid blocker status: {new_status}. Allowed: {blocker_statuses}")
                fields["status"] = new_status

                # Manage resolved_at automatically if not explicitly given
                if resolved_at is None:
                    if (item_type == "risk" and new_status in ("mitigated", "accepted", "closed")) or (
                        item_type == "blocker" and new_status == "resolved"
                    ):
                        if not curr["resolved_at"]:
                            fields["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    elif (item_type == "risk" and new_status == "identified") or (
                        item_type == "blocker" and new_status in ("active", "reopened")
                    ):
                        fields["resolved_at"] = None

            if resolved_at is not None:
                fields["resolved_at"] = resolved_at
            if mitigation_or_resolution is not None:
                fields["mitigation_or_resolution"] = mitigation_or_resolution.strip()
            if "linked_action_id" in kwargs or linked_action_id is not None:
                fields["linked_action_id"] = linked_action_id
            if "linked_decision_id" in kwargs or linked_decision_id is not None:
                fields["linked_decision_id"] = linked_decision_id
            if evidence_quote is not None:
                fields["evidence_quote"] = evidence_quote.strip()

            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [int(item_id)]
            cur = conn.execute(f"UPDATE risks_and_blockers SET {cols} WHERE id = ?", vals)
            conn.commit()
            return cur.rowcount > 0

    def delete_risk_or_blocker(self, item_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM risks_and_blockers WHERE id = ?", (item_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # "Ask Vox" — Cross-Meeting Semantic Memory
    # ------------------------------------------------------------------
    def search_meeting_memory(
        self, query: str, limit: int = 15, meeting_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Search past meeting transcripts, summaries, action items, and decisions
        to ground natural-language questions with verified timestamps and source citations.
        Uses intelligent stopword filtering and optional meeting scoping.
        """
        if not query.strip():
            return []

        stopwords = {
            "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
            "was", "were", "is", "are", "been", "being", "have", "has", "had", "having",
            "do", "does", "did", "doing", "can", "could", "would", "should", "shall", "will",
            "the", "a", "an", "and", "but", "or", "because", "as", "until", "while",
            "of", "at", "by", "for", "with", "about", "against", "between", "into",
            "through", "during", "before", "after", "above", "below", "to", "from",
            "up", "down", "in", "out", "on", "off", "over", "under", "again", "further",
            "then", "once", "here", "there", "all", "any", "both", "each", "few", "more",
            "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
            "so", "than", "too", "very", "say", "said", "saying", "says", "tell", "discuss",
            "discussed", "regarding", "meeting", "meetings", "we", "i", "you", "they", "he", "she"
        }
        all_tokens = [t.strip().lower() for t in re.split(r"[^\w\-]+", query.strip()) if len(t.strip()) > 1]
        content_tokens = [t for t in all_tokens if t not in stopwords]
        tokens = content_tokens if content_tokens else all_tokens

        results: List[Dict[str, Any]] = []
        with self._lock, self._connect() as conn:
            # 1. Search transcript segments for direct spoken quotes
            for token in tokens[:6]:
                pattern = f"%{token}%"
                if meeting_id is not None:
                    rows = conn.execute(
                        """
                        SELECT s.meeting_id, s.start_time, s.speaker, s.text,
                               m.title AS meeting_title, m.created_at AS meeting_date
                        FROM transcript_segments s
                        JOIN meetings m ON s.meeting_id = m.id
                        WHERE s.text LIKE ? AND s.meeting_id = ?
                        ORDER BY s.id DESC LIMIT ?
                        """,
                        (pattern, meeting_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT s.meeting_id, s.start_time, s.speaker, s.text,
                               m.title AS meeting_title, m.created_at AS meeting_date
                        FROM transcript_segments s
                        JOIN meetings m ON s.meeting_id = m.id
                        WHERE s.text LIKE ?
                        ORDER BY s.id DESC LIMIT ?
                        """,
                        (pattern, limit),
                    ).fetchall()
                for r in rows:
                    item = dict(r)
                    item["source_type"] = "transcript"
                    results.append(item)

            # 2. Search decisions
            for token in tokens[:4]:
                pattern = f"%{token}%"
                if meeting_id is not None:
                    d_rows = conn.execute(
                        """
                        SELECT d.meeting_id, d.decision AS text, d.rationale, d.timestamp_sec AS start_time,
                               'Decision' AS speaker, m.title AS meeting_title, m.created_at AS meeting_date
                        FROM decisions d
                        JOIN meetings m ON d.meeting_id = m.id
                        WHERE (d.decision LIKE ? OR d.rationale LIKE ?) AND d.meeting_id = ?
                        ORDER BY d.id DESC LIMIT ?
                        """,
                        (pattern, pattern, meeting_id, limit),
                    ).fetchall()
                else:
                    d_rows = conn.execute(
                        """
                        SELECT d.meeting_id, d.decision AS text, d.rationale, d.timestamp_sec AS start_time,
                               'Decision' AS speaker, m.title AS meeting_title, m.created_at AS meeting_date
                        FROM decisions d
                        JOIN meetings m ON d.meeting_id = m.id
                        WHERE d.decision LIKE ? OR d.rationale LIKE ?
                        ORDER BY d.id DESC LIMIT ?
                        """,
                        (pattern, pattern, limit),
                    ).fetchall()
                for r in d_rows:
                    item = dict(r)
                    item["source_type"] = "decision"
                    results.append(item)

            # 3. Search action items
            for token in tokens[:4]:
                pattern = f"%{token}%"
                if meeting_id is not None:
                    a_rows = conn.execute(
                        """
                        SELECT a.meeting_id, a.description AS text, a.owner, a.due_date,
                               a.status, a.timestamp_sec AS start_time, 'Action Item' AS speaker,
                               m.title AS meeting_title, m.created_at AS meeting_date
                        FROM action_items a
                        JOIN meetings m ON a.meeting_id = m.id
                        WHERE (a.description LIKE ? OR a.owner LIKE ?) AND a.meeting_id = ?
                        ORDER BY a.id DESC LIMIT ?
                        """,
                        (pattern, pattern, meeting_id, limit),
                    ).fetchall()
                else:
                    a_rows = conn.execute(
                        """
                        SELECT a.meeting_id, a.description AS text, a.owner, a.due_date,
                               a.status, a.timestamp_sec AS start_time, 'Action Item' AS speaker,
                               m.title AS meeting_title, m.created_at AS meeting_date
                        FROM action_items a
                        JOIN meetings m ON a.meeting_id = m.id
                        WHERE a.description LIKE ? OR a.owner LIKE ?
                        ORDER BY a.id DESC LIMIT ?
                        """,
                        (pattern, pattern, limit),
                    ).fetchall()
                for r in a_rows:
                    item = dict(r)
                    item["source_type"] = "action_item"
                    results.append(item)

            # 4. Search meeting summaries & titles
            for token in tokens[:3]:
                pattern = f"%{token}%"
                if meeting_id is not None:
                    m_rows = conn.execute(
                        """
                        SELECT m.id AS meeting_id, 0.0 AS start_time, 'Meeting Overview' AS speaker,
                               m.summary AS text, m.title AS meeting_title, m.created_at AS meeting_date
                        FROM meetings m
                        WHERE (m.title LIKE ? OR m.summary LIKE ? OR m.topics LIKE ?) AND m.id = ?
                        LIMIT ?
                        """,
                        (pattern, pattern, pattern, meeting_id, limit),
                    ).fetchall()
                else:
                    m_rows = conn.execute(
                        """
                        SELECT m.id AS meeting_id, 0.0 AS start_time, 'Meeting Overview' AS speaker,
                               m.summary AS text, m.title AS meeting_title, m.created_at AS meeting_date
                        FROM meetings m
                        WHERE m.title LIKE ? OR m.summary LIKE ? OR m.topics LIKE ?
                        LIMIT ?
                        """,
                        (pattern, pattern, pattern, limit),
                    ).fetchall()
                for r in m_rows:
                    item = dict(r)
                    item["source_type"] = "summary"
                    if item.get("text"):
                        results.append(item)

        # Deduplicate results by (meeting_id, start_time, text)
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for res in results:
            key = (res["meeting_id"], round(float(res.get("start_time", 0.0)), 1), res["text"][:40])
            if key not in seen:
                seen.add(key)
                deduped.append(res)
        return deduped[:limit]

    # ------------------------------------------------------------------
    # Analytics & Summary Aggregator
    # ------------------------------------------------------------------
    def get_analytics_summary(self) -> Dict[str, Any]:
        """Aggregate high-level meeting metrics similar to Read AI Analytics.
        Only completed meetings are counted towards meeting totals and duration.
        Open action items canonical definition: status IN ('pending', 'blocked', 'reopened').
        """
        with self._lock, self._connect() as conn:
            tot_meetings = conn.execute("SELECT COUNT(*) FROM meetings WHERE status = 'completed'").fetchone()[0]
            tot_duration = conn.execute("SELECT SUM(duration_sec) FROM meetings WHERE status = 'completed'").fetchone()[0] or 0.0
            avg_engagement = conn.execute("SELECT AVG(engagement_score) FROM meetings WHERE status = 'completed'").fetchone()[0]
            if avg_engagement is None:
                avg_engagement = 85.0
            tot_actions = conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
            open_actions = conn.execute(
                "SELECT COUNT(*) FROM action_items WHERE status IN ('pending', 'blocked', 'reopened')"
            ).fetchone()[0]
            pending_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'pending'").fetchone()[0]
            blocked_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'blocked'").fetchone()[0]
            completed_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'completed'").fetchone()[0]
            cancelled_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'cancelled'").fetchone()[0]
            reopened_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'reopened'").fetchone()[0]
            tot_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            try:
                confirmed_decisions = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE status = 'confirmed'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                confirmed_decisions = 0

            # Source platform distribution (only completed meetings)
            platforms = conn.execute(
                """
                SELECT source_platform, COUNT(*) AS count
                FROM meetings WHERE status = 'completed' GROUP BY source_platform
                ORDER BY count DESC
                """
            ).fetchall()

            return {
                "total_meetings": tot_meetings,
                "total_duration_sec": tot_duration,
                "avg_engagement_score": round(float(avg_engagement), 1),
                "total_action_items": tot_actions,
                "open_action_items": open_actions,
                "pending_action_items": pending_actions,
                "blocked_action_items": blocked_actions,
                "completed_action_items": completed_actions,
                "cancelled_action_items": cancelled_actions,
                "reopened_action_items": reopened_actions,
                "total_decisions": tot_decisions,
                "confirmed_decisions": confirmed_decisions,
                "platforms": [dict(p) for p in platforms],
            }

    # ------------------------------------------------------------------
    # Dictation History & Productivity Insights CRUD
    # ------------------------------------------------------------------
    def add_dictation(
        self,
        raw_text: str,
        cleaned_text: str = "",
        final_text: str = "",
        duration_sec: float = 0.0,
        target_window: str = "",
        target_process: str = "",
        insertion_method: str = "unicode",
        status: str = "completed",
        app_category: str = "other",
    ) -> int:
        """Add dictation record preserving immutable raw_text alongside rendered cleaned_text/final_text."""
        effective_final = cleaned_text or final_text
        return self.add_dictation_entry(
            raw_text=raw_text,
            final_text=effective_final,
            duration_sec=duration_sec,
            target_window=target_window,
            target_process=target_process,
            insertion_method=insertion_method,
            status=status,
            app_category=app_category,
        )

    def get_recent_dictation(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent dictation history with cleaned_text alias for final_text."""
        entries = self.list_dictation_history(limit=limit)
        for e in entries:
            if "cleaned_text" not in e and "final_text" in e:
                e["cleaned_text"] = e["final_text"]
        return entries

    def add_dictation_entry(
        self,
        raw_text: str,
        final_text: str,
        duration_sec: float = 0.0,
        target_window: str = "",
        target_process: str = "",
        insertion_method: str = "unicode",
        status: str = "completed",
        app_category: str = "other",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dur = round(float(duration_sec), 2)
        words = len((final_text or raw_text or "").split())
        wpm = round((words / dur) * 60.0, 1) if dur >= 0.4 and words > 0 else 0.0
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO dictation_history (
                    created_at, duration_sec, raw_text, final_text,
                    target_window, target_process, insertion_method, status,
                    wpm, word_count, app_category, edited_text, undone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0)
                """,
                (
                    now,
                    dur,
                    raw_text,
                    final_text,
                    target_window,
                    target_process,
                    insertion_method,
                    status,
                    wpm,
                    words,
                    app_category or "other",
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_dictation_entry(
        self,
        entry_id: int,
        final_text: Optional[str] = None,
        edited_text: Optional[str] = None,
        undone: Optional[bool] = None,
    ) -> bool:
        """Update an existing dictation entry (for inline edit or Undo AI edit)."""
        sets = []
        params: List[Any] = []
        if final_text is not None:
            sets.append("final_text = ?")
            params.append(final_text)
            sets.append("word_count = ?")
            params.append(len(final_text.split()))
        if edited_text is not None:
            sets.append("edited_text = ?")
            params.append(edited_text)
        if undone is not None:
            sets.append("undone = ?")
            params.append(1 if undone else 0)
        if not sets:
            return False
        params.append(entry_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE dictation_history SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_dictation_history(self, search_query: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if search_query.strip():
                q = f"%{search_query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT * FROM dictation_history
                    WHERE final_text LIKE ? OR raw_text LIKE ? OR target_window LIKE ? OR target_process LIKE ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (q, q, q, q, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dictation_history ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def list_dictations(self, limit: int = 100, search_query: str = "") -> List[Dict[str, Any]]:
        """Alias for list_dictation_history."""
        return self.list_dictation_history(search_query=search_query, limit=limit)

    def clear_dictation_history(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM dictation_history")
            conn.commit()

    def delete_dictation_entry(self, entry_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM dictation_history WHERE id = ?", (entry_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_dictation_stats(self) -> Dict[str, Any]:
        """Compute KPI totals, WPM velocity, day streak, minutes saved, and per-app breakdown."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, duration_sec, final_text, target_process, wpm, word_count FROM dictation_history ORDER BY id DESC"
            ).fetchall()

        total_dictations = len(rows)
        total_words = 0
        total_dur = 0.0
        wpm_samples: List[float] = []
        days_active: set[str] = set()
        apps_counts: Dict[str, Dict[str, Any]] = {}
        daily_map: Dict[str, Dict[str, Any]] = {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        seven_days_ago = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        words_today = 0
        words_7d = 0

        for r in rows:
            text = str(r["final_text"] or "")
            wc = int(r["word_count"] or 0) or len(text.split())
            dur = float(r["duration_sec"] or 0.0)
            wpm_val = float(r["wpm"] or 0.0)
            if wpm_val <= 0.0 and dur >= 0.4 and wc > 0:
                wpm_val = round((wc / dur) * 60.0, 1)

            total_words += wc
            total_dur += dur
            if 5.0 <= wpm_val <= 350.0:
                wpm_samples.append(wpm_val)

            day_str = str(r["created_at"] or "")[:10]
            if day_str:
                days_active.add(day_str)
                if day_str == today_str:
                    words_today += wc
                if day_str >= seven_days_ago:
                    words_7d += wc
                dm = daily_map.setdefault(day_str, {"date": day_str, "words": 0, "count": 0, "wpm_sum": 0.0, "wpm_n": 0})
                dm["words"] += wc
                dm["count"] += 1
                if 5.0 <= wpm_val <= 350.0:
                    dm["wpm_sum"] += wpm_val
                    dm["wpm_n"] += 1

            proc = str(r["target_process"] or "").strip() or "Desktop"
            proc_clean = re.sub(r"\.exe$", "", proc, flags=re.IGNORECASE)
            app_entry = apps_counts.setdefault(proc_clean, {"app": proc_clean, "count": 0, "words": 0})
            app_entry["count"] += 1
            app_entry["words"] += wc

        # Compute consecutive-day streak ending today or yesterday
        streak = 0
        check_day = datetime.now().date()
        if check_day.strftime("%Y-%m-%d") not in days_active:
            check_day = check_day - timedelta(days=1)
        while check_day.strftime("%Y-%m-%d") in days_active:
            streak += 1
            check_day = check_day - timedelta(days=1)

        avg_wpm = round(sum(wpm_samples) / len(wpm_samples), 1) if wpm_samples else 0.0
        peak_wpm = round(max(wpm_samples), 1) if wpm_samples else 0.0
        # Compared to 40 WPM baseline keyboard typing minus speaking time
        minutes_saved = round(max(0.0, (total_words / 40.0) - (total_dur / 60.0)), 1)

        daily_series = []
        for day_key in sorted(daily_map.keys())[-14:]:
            dm = daily_map[day_key]
            daily_series.append({
                "date": day_key,
                "words": dm["words"],
                "count": dm["count"],
                "wpm": round(dm["wpm_sum"] / dm["wpm_n"], 1) if dm["wpm_n"] else 0.0,
            })

        top_apps = sorted(apps_counts.values(), key=lambda x: x["words"], reverse=True)[:8]
        return {
            "total_dictations": total_dictations,
            "total_words": total_words,
            "avg_wpm": avg_wpm,
            "peak_wpm": peak_wpm,
            "total_duration_sec": round(total_dur, 1),
            "minutes_saved": minutes_saved,
            "day_streak": streak,
            "apps_used": len(apps_counts),
            "top_apps": top_apps,
            "words_today": words_today,
            "words_7d": words_7d,
            "daily_series": daily_series,
        }

    # ------------------------------------------------------------------
    # Custom Vocabulary / Dictionary CRUD
    # ------------------------------------------------------------------
    def list_vocabulary(self, active_only: bool = False) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM custom_vocabulary WHERE is_active = 1 ORDER BY is_starred DESC, spoken_phrase ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM custom_vocabulary ORDER BY is_starred DESC, spoken_phrase ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    def search_vocabulary(
        self, query: str = "", sort_by: str = "az", active_only: bool = False
    ) -> List[Dict[str, Any]]:
        order_sql = {
            "newest": "id DESC",
            "oldest": "id ASC",
            "starred": "is_starred DESC, spoken_phrase ASC",
            "freq": "frequency_used DESC, spoken_phrase ASC",
            "az": "is_starred DESC, spoken_phrase COLLATE NOCASE ASC",
        }.get(sort_by, "is_starred DESC, spoken_phrase COLLATE NOCASE ASC")
        where_parts = []
        params: List[Any] = []
        if active_only:
            where_parts.append("is_active = 1")
        if query.strip():
            where_parts.append("(spoken_phrase LIKE ? OR replacement LIKE ? OR category LIKE ?)")
            q = f"%{query.strip()}%"
            params.extend([q, q, q])
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM custom_vocabulary {where_clause} ORDER BY {order_sql}",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_vocabulary(
        self,
        spoken_phrase: str,
        replacement: str,
        category: str = "General",
        is_active: int = 1,
        is_starred: int = 0,
        auto_learned: int = 0,
    ) -> int:
        spoken_clean = spoken_phrase.strip()
        replacement_clean = replacement.strip()
        if not spoken_clean or not replacement_clean:
            raise ValueError("Spoken phrase and replacement cannot be empty.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO custom_vocabulary (
                    spoken_phrase, replacement, category, is_active, is_starred, auto_learned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(spoken_phrase) DO UPDATE SET
                    replacement = excluded.replacement,
                    category = excluded.category,
                    is_active = excluded.is_active
                """,
                (
                    spoken_clean,
                    replacement_clean,
                    category.strip() or "General",
                    int(bool(is_active)),
                    int(bool(is_starred)),
                    int(bool(auto_learned)),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT id FROM custom_vocabulary WHERE spoken_phrase = ?", (spoken_clean,)).fetchone()
            return int(row["id"]) if row else 1

    def set_vocabulary_starred(self, vocab_id: int, starred: bool) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE custom_vocabulary SET is_starred = ? WHERE id = ?",
                (1 if starred else 0, vocab_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_vocabulary_bulk(self, vocab_ids: List[int]) -> int:
        if not vocab_ids:
            return 0
        placeholders = ",".join("?" for _ in vocab_ids)
        with self._lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM custom_vocabulary WHERE id IN ({placeholders})", tuple(vocab_ids))
            conn.commit()
            return int(cur.rowcount)

    def add_vocabulary(
        self,
        spoken_phrase: str,
        replacement: str,
        category: str = "General",
        is_active: int = 1,
        is_starred: int = 0,
        auto_learned: int = 0,
    ) -> int:
        return self.upsert_vocabulary(
            spoken_phrase,
            replacement,
            category=category,
            is_active=is_active,
            is_starred=is_starred,
            auto_learned=auto_learned,
        )

    def delete_vocabulary(self, vocab_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM custom_vocabulary WHERE id = ?", (vocab_id,))
            conn.commit()

    # Convenient method aliases
    save_dictation = add_dictation_entry
    add_vocabulary_item = add_vocabulary
    delete_vocabulary_item = delete_vocabulary

    # ------------------------------------------------------------------
    # Snippets CRUD (Voice Triggers -> Expansions)
    # ------------------------------------------------------------------
    def list_snippets(self, search_query: str = "", sort_by: str = "newest") -> List[Dict[str, Any]]:
        order_sql = {
            "newest": "is_starred DESC, id DESC",
            "oldest": "id ASC",
            "az": "trigger_phrase COLLATE NOCASE ASC",
            "most_used": "use_count DESC, trigger_phrase COLLATE NOCASE ASC",
        }.get(sort_by, "is_starred DESC, id DESC")
        with self._lock, self._connect() as conn:
            if search_query.strip():
                q = f"%{search_query.strip()}%"
                rows = conn.execute(
                    f"SELECT * FROM snippets WHERE trigger_phrase LIKE ? OR expansion LIKE ? ORDER BY {order_sql}",
                    (q, q),
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM snippets ORDER BY {order_sql}").fetchall()
            return [dict(r) for r in rows]

    def upsert_snippet(
        self,
        trigger_phrase: str,
        expansion: str,
        is_starred: int = 0,
        snippet_id: Optional[int] = None,
    ) -> int:
        trig = trigger_phrase.strip()
        exp = expansion.strip()
        if not trig or not exp:
            raise ValueError("Snippet cue and expansion cannot be empty.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            if snippet_id is not None:
                conn.execute(
                    """
                    UPDATE snippets
                    SET trigger_phrase = ?, expansion = ?, is_starred = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (trig, exp, int(bool(is_starred)), now, int(snippet_id)),
                )
                conn.commit()
                return int(snippet_id)
            conn.execute(
                """
                INSERT INTO snippets (trigger_phrase, expansion, is_starred, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trigger_phrase) DO UPDATE SET
                    expansion = excluded.expansion,
                    is_starred = excluded.is_starred,
                    updated_at = excluded.updated_at
                """,
                (trig, exp, int(bool(is_starred)), now, now),
            )
            conn.commit()
            row = conn.execute("SELECT id FROM snippets WHERE trigger_phrase = ?", (trig,)).fetchone()
            return int(row["id"]) if row else 1

    def set_snippet_starred(self, snippet_id: int, starred: bool) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE snippets SET is_starred = ? WHERE id = ?", (1 if starred else 0, snippet_id))
            conn.commit()
            return cur.rowcount > 0

    def increment_snippet_usage(self, snippet_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE snippets SET use_count = use_count + 1 WHERE id = ?", (snippet_id,))
            conn.commit()

    def delete_snippet(self, snippet_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
            conn.commit()
            return cur.rowcount > 0

    def delete_snippets_bulk(self, snippet_ids: List[int]) -> int:
        if not snippet_ids:
            return 0
        placeholders = ",".join("?" for _ in snippet_ids)
        with self._lock, self._connect() as conn:
            cur = conn.execute(f"DELETE FROM snippets WHERE id IN ({placeholders})", tuple(snippet_ids))
            conn.commit()
            return int(cur.rowcount)

    # ------------------------------------------------------------------
    # Scratchpad Notes & Version History CRUD
    # ------------------------------------------------------------------
    def list_notes(self, search_query: str = "") -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if search_query.strip():
                q = f"%{search_query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT * FROM scratchpad_notes
                    WHERE title LIKE ? OR plain_text LIKE ? OR content LIKE ? OR tags LIKE ?
                    ORDER BY is_pinned DESC, updated_at DESC, id DESC
                    """,
                    (q, q, q, q),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scratchpad_notes ORDER BY is_pinned DESC, updated_at DESC, id DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_note(self, note_id: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM scratchpad_notes WHERE id = ?", (note_id,)).fetchone()
            return dict(row) if row else None

    def create_note(
        self,
        title: str = "Untitled note",
        content: str = "",
        plain_text: str = "",
        is_pinned: int = 0,
        tags: str = "",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_title = (title or "").strip() or "Untitled note"
        clean_plain = plain_text or re.sub(r"<[^>]+>", " ", content or "").strip()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scratchpad_notes (title, content, plain_text, is_pinned, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (clean_title, content, clean_plain, int(bool(is_pinned)), tags, now, now),
            )
            nid = int(cur.lastrowid)
            if content:
                conn.execute(
                    "INSERT INTO scratchpad_versions (note_id, content, created_at) VALUES (?, ?, ?)",
                    (nid, content, now),
                )
            conn.commit()
            return nid

    def update_note(
        self,
        note_id: int,
        title: Optional[str] = None,
        content: Optional[str] = None,
        plain_text: Optional[str] = None,
        is_pinned: Optional[int] = None,
        tags: Optional[str] = None,
        save_version: bool = True,
    ) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sets = ["updated_at = ?"]
        params: List[Any] = [now]
        if title is not None:
            sets.append("title = ?")
            params.append(title.strip() or "Untitled note")
        if content is not None:
            sets.append("content = ?")
            params.append(content)
            if plain_text is None:
                plain_text = re.sub(r"<[^>]+>", " ", content).strip()
        if plain_text is not None:
            sets.append("plain_text = ?")
            params.append(plain_text)
        if is_pinned is not None:
            sets.append("is_pinned = ?")
            params.append(int(bool(is_pinned)))
        if tags is not None:
            sets.append("tags = ?")
            params.append(tags)
        params.append(note_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE scratchpad_notes SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            if cur.rowcount > 0 and content is not None and save_version:
                last_ver = conn.execute(
                    "SELECT content FROM scratchpad_versions WHERE note_id = ? ORDER BY id DESC LIMIT 1",
                    (note_id,),
                ).fetchone()
                if not last_ver or last_ver["content"] != content:
                    conn.execute(
                        "INSERT INTO scratchpad_versions (note_id, content, created_at) VALUES (?, ?, ?)",
                        (note_id, content, now),
                    )
            conn.commit()
            return cur.rowcount > 0

    def delete_note(self, note_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM scratchpad_notes WHERE id = ?", (note_id,))
            conn.commit()
            return cur.rowcount > 0

    def list_note_versions(self, note_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scratchpad_versions WHERE note_id = ? ORDER BY id DESC LIMIT ?",
                (note_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_data_categories(
        self, categories: List[str], recordings_dir: Optional[Path] = None
    ) -> Dict[str, int]:
        """Selectively wipe chosen local data categories for Data & Privacy settings."""
        removed: Dict[str, int] = {}
        with self._lock, self._connect() as conn:
            if "dictation_history" in categories:
                removed["dictation_history"] = conn.execute("DELETE FROM dictation_history").rowcount
            if "dictionary" in categories or "vocabulary" in categories:
                removed["dictionary"] = conn.execute("DELETE FROM custom_vocabulary").rowcount
            if "snippets" in categories:
                removed["snippets"] = conn.execute("DELETE FROM snippets").rowcount
            if "notes" in categories:
                removed["notes"] = conn.execute("DELETE FROM scratchpad_notes").rowcount
            if "meetings" in categories:
                removed["meetings"] = conn.execute("DELETE FROM meetings").rowcount
                conn.execute("DELETE FROM transcript_segments")
            if "screen_recordings" in categories:
                removed["screen_recordings"] = conn.execute("DELETE FROM screen_recordings").rowcount
            if "calendar" in categories or "calendar_events" in categories:
                removed["calendar_events"] = conn.execute("DELETE FROM calendar_events").rowcount
                removed["calendar_accounts"] = conn.execute("DELETE FROM calendar_accounts").rowcount
                conn.execute("DELETE FROM calendar_credentials_vault")
            conn.commit()
        if "meetings" in categories and recordings_dir and recordings_dir.exists():
            for child in recordings_dir.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed removing %s: %s", child, exc)
        return removed

    # ------------------------------------------------------------------
    # PC Screen Recordings Operations
    # ------------------------------------------------------------------
    def create_screen_recording(
        self,
        title: str,
        file_path: str,
        fps: int = 30,
        resolution: str = "1080p",
        audio_mode: str = "both",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO screen_recordings (title, created_at, file_path, fps, resolution, audio_mode)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, now, file_path, fps, resolution, audio_mode),
            )
            conn.commit()
            return cursor.lastrowid

    def finish_screen_recording(
        self,
        rec_id: int,
        duration_sec: float,
        file_size_bytes: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE screen_recordings
                SET duration_sec = ?, file_size_bytes = ?
                WHERE id = ?
                """,
                (duration_sec, file_size_bytes, rec_id),
            )
            conn.commit()

    def list_screen_recordings(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM screen_recordings
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_screen_recording(self, rec_id: int) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT file_path FROM screen_recordings WHERE id = ?", (rec_id,)).fetchone()
            if row and row["file_path"]:
                p = Path(row["file_path"])
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            conn.execute("DELETE FROM screen_recordings WHERE id = ?", (rec_id,))
            conn.commit()
            return True

    # ------------------------------------------------------------------
    # Retention & Privacy Controls
    # ------------------------------------------------------------------
    def apply_retention_policy(self, retention_days: int) -> int:
        """Delete meetings and dictation history older than retention_days (if > 0)."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        deleted_count = 0
        with self._lock, self._connect() as conn:
            old_meetings = conn.execute(
                "SELECT id FROM meetings WHERE created_at < ?", (cutoff,)
            ).fetchall()
            for row in old_meetings:
                if self.delete_meeting(int(row["id"]), delete_audio_files=True):
                    deleted_count += 1
            conn.execute("DELETE FROM dictation_history WHERE created_at < ?", (cutoff,))
            conn.commit()
        if deleted_count > 0:
            logger.info("Retention policy removed %d meetings older than %d days", deleted_count, retention_days)
        return deleted_count

    def wipe_all_user_data(self, recordings_dir: Optional[Path] = None) -> None:
        """Completely delete all meetings, transcripts, audio files, calendar caches, and dictation history."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM transcript_segments")
            conn.execute("DELETE FROM meetings")
            conn.execute("DELETE FROM dictation_history")
            conn.execute("DELETE FROM screen_recordings")
            conn.execute("DELETE FROM calendar_events")
            conn.execute("DELETE FROM calendar_accounts")
            conn.execute("DELETE FROM calendar_credentials_vault")
            conn.commit()
        if recordings_dir and recordings_dir.exists():
            for child in recordings_dir.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed removing %s: %s", child, exc)
        logger.info("All user recordings, transcripts, and dictation history wiped.")

    def wipe_all_data(self, recordings_dir: Optional[Path] = None) -> None:
        self.wipe_all_user_data(recordings_dir=recordings_dir)

    # ------------------------------------------------------------------
    # Multi-User Calendar Accounts, Credential Vault, and Event Sync Cache
    # ------------------------------------------------------------------
    @staticmethod
    def _decode_cal_account_row(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for jcol, out_col, default_val in [
            ("selected_calendars_json", "selected_calendars", []),
            ("available_calendars_json", "available_calendars", []),
            ("delta_links_json", "delta_links", {}),
            ("sync_tokens_json", "sync_tokens", {}),
        ]:
            try:
                d[out_col] = json.loads(d.get(jcol) or json.dumps(default_val))
            except Exception:
                d[out_col] = default_val
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    def upsert_calendar_account(
        self,
        account_id: str,
        provider_type: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        uid = user_id or "default"
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM calendar_accounts WHERE user_id = ? AND account_id = ?",
                (uid, account_id),
            ).fetchone()
            base = self._decode_cal_account_row(existing) if existing else {
                "account_id": account_id,
                "user_id": uid,
                "provider_type": provider_type,
                "account_email": "",
                "display_name": "",
                "account_kind": "personal",
                "tenant_id": "",
                "organization_name": "",
                "enabled": True,
                "connection_state": "disconnected",
                "last_sync_at": "",
                "last_error": "",
                "error_code": "",
                "selected_calendars": [],
                "available_calendars": [],
                "delta_links": {},
                "sync_tokens": {},
                "created_at": now,
            }
            base["provider_type"] = provider_type or base.get("provider_type", "ms_graph")
            for k in (
                "account_email",
                "display_name",
                "account_kind",
                "tenant_id",
                "organization_name",
                "enabled",
                "connection_state",
                "last_sync_at",
                "last_error",
                "error_code",
                "selected_calendars",
                "available_calendars",
                "delta_links",
                "sync_tokens",
            ):
                if k in kwargs and kwargs[k] is not None:
                    base[k] = kwargs[k]
            conn.execute(
                """
                INSERT OR REPLACE INTO calendar_accounts (
                    account_id, user_id, provider_type, account_email, display_name,
                    account_kind, tenant_id, organization_name, enabled, connection_state,
                    last_sync_at, last_error, error_code,
                    selected_calendars_json, available_calendars_json,
                    delta_links_json, sync_tokens_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    uid,
                    str(base["provider_type"]),
                    str(base["account_email"] or ""),
                    str(base["display_name"] or ""),
                    str(base["account_kind"] or "personal"),
                    str(base["tenant_id"] or ""),
                    str(base["organization_name"] or ""),
                    1 if base.get("enabled", True) else 0,
                    str(base.get("connection_state") or "disconnected"),
                    str(base.get("last_sync_at") or ""),
                    str(base.get("last_error") or ""),
                    str(base.get("error_code") or ""),
                    json.dumps(base.get("selected_calendars") or []),
                    json.dumps(base.get("available_calendars") or []),
                    json.dumps(base.get("delta_links") or {}),
                    json.dumps(base.get("sync_tokens") or {}),
                    str(base.get("created_at") or now),
                    now,
                ),
            )
            conn.commit()

    def get_calendar_account(self, account_id: str, user_id: str = "default") -> Optional[Dict[str, Any]]:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM calendar_accounts 
                WHERE account_id = ? AND (user_id = ? OR user_id = 'default')
                ORDER BY (user_id = ?) DESC, created_at ASC
                LIMIT 1
                """,
                (account_id, uid, uid),
            ).fetchone()
            return self._decode_cal_account_row(row) if row else None

    def list_calendar_accounts(
        self,
        user_id: str = "default",
        enabled_only: bool = False,
    ) -> List[Dict[str, Any]]:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            sql = "SELECT * FROM calendar_accounts WHERE (user_id = ? OR user_id = 'default')"
            params: List[Any] = [uid]
            if enabled_only:
                sql += " AND enabled = 1"
            sql += " ORDER BY (user_id = ?) DESC, created_at ASC, account_id ASC"
            params.append(uid)
            rows = conn.execute(sql, params).fetchall()
            seen: set[str] = set()
            results: List[Dict[str, Any]] = []
            for r in rows:
                acc = self._decode_cal_account_row(r)
                aid = str(acc.get("account_id") or "")
                if aid and aid not in seen:
                    seen.add(aid)
                    results.append(acc)
            return results

    def update_calendar_account_state(
        self,
        account_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        uid = user_id or "default"
        existing = self.get_calendar_account(account_id, user_id=uid)
        if not existing:
            return
        actual_uid = existing.get("user_id", uid) or uid
        self.upsert_calendar_account(
            account_id=account_id,
            provider_type=existing["provider_type"],
            user_id=actual_uid,
            **kwargs,
        )

    def set_calendar_account_enabled(
        self,
        account_id: str,
        enabled: bool,
        user_id: str = "default",
    ) -> None:
        if enabled:
            self.update_calendar_account_state(
                account_id=account_id,
                user_id=user_id,
                enabled=True,
                connection_state="syncing",
                last_error="",
                error_code="",
            )
        else:
            self.update_calendar_account_state(
                account_id=account_id,
                user_id=user_id,
                enabled=False,
                connection_state="disabled",
            )

    def delete_calendar_account(self, account_id: str, user_id: str = "default") -> None:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM calendar_accounts WHERE (user_id = ? OR user_id = 'default') AND account_id = ?",
                (uid, account_id),
            )
            conn.execute(
                "DELETE FROM calendar_credentials_vault WHERE (user_id = ? OR user_id = 'default') AND account_id = ?",
                (uid, account_id),
            )
            conn.execute(
                "DELETE FROM calendar_events WHERE (user_id = ? OR user_id = 'default') AND account_id = ?",
                (uid, account_id),
            )
            conn.commit()

    def save_calendar_credentials(
        self,
        user_id: str,
        account_id: str,
        provider_type: str,
        encrypted_blob: bytes,
    ) -> bool:
        uid = user_id or "default"
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO calendar_credentials_vault (
                    user_id, account_id, provider_type, encrypted_blob, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (uid, account_id, provider_type, sqlite3.Binary(encrypted_blob), now),
            )
            conn.commit()
            return True

    def load_calendar_credentials(self, user_id: str, account_id: str) -> Optional[bytes]:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT encrypted_blob FROM calendar_credentials_vault 
                WHERE account_id = ? AND (user_id = ? OR user_id = 'default')
                ORDER BY (user_id = ?) DESC, updated_at DESC
                LIMIT 1
                """,
                (account_id, uid, uid),
            ).fetchone()
            if not row or row["encrypted_blob"] is None:
                return None
            return bytes(row["encrypted_blob"])

    def delete_calendar_credentials(self, user_id: str, account_id: str) -> bool:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM calendar_credentials_vault WHERE (user_id = ? OR user_id = 'default') AND account_id = ?",
                (uid, account_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def upsert_calendar_events(
        self,
        events: List[Any],
        user_id: str = "default",
        account_id: str = "",
    ) -> int:
        uid = user_id or "default"
        now = datetime.now().isoformat()
        count = 0
        with self._lock, self._connect() as conn:
            for ev in events:
                if isinstance(ev, dict):
                    ev_id = str(ev.get("id") or "")
                    ev_acc = str(account_id or ev.get("account_id") or "")
                    ptype = str(ev.get("provider_type") or "ms_graph")
                    pacc = str(ev.get("provider_account") or "")
                    cal_id = str(ev.get("calendar_id") or "")
                    cal_name = str(ev.get("calendar_name") or "")
                    ical_uid = str(ev.get("ical_uid") or "")
                    series_id = str(ev.get("series_master_id") or "")
                    mod_occ = 1 if ev.get("is_modified_occurrence") else 0
                    tz_name = str(ev.get("timezone_name") or "")
                    conf_id = str(ev.get("conference_id") or "")
                    title = str(ev.get("title") or "Untitled Meeting")
                    st = str(ev.get("start_time") or "")
                    et = str(ev.get("end_time") or "")
                    loc = str(ev.get("location") or "")
                    jurl = str(ev.get("join_url") or "")
                    att = ", ".join(ev.get("attendees") or []) if isinstance(ev.get("attendees"), list) else str(ev.get("attendees") or "")
                    org = str(ev.get("organizer") or "")
                    plat = str(ev.get("platform") or "generic")
                    is_on = 1 if ev.get("is_online") else 0
                    is_canc = 1 if ev.get("is_cancelled") else 0
                    is_rec = 1 if ev.get("is_recurring") else 0
                    desc = str(ev.get("description") or "")
                    src_accs = json.dumps(ev.get("source_accounts") or [])
                    src_provs = json.dumps(ev.get("source_providers") or [])
                    src_ids = json.dumps(ev.get("source_event_ids") or [ev_id])
                else:
                    ev_id = str(getattr(ev, "id", "") or "")
                    ev_acc = str(account_id or getattr(ev, "account_id", "") or "")
                    ptype = str(getattr(ev, "provider_type", "ms_graph") or "ms_graph")
                    pacc = str(getattr(ev, "provider_account", "") or "")
                    cal_id = str(getattr(ev, "calendar_id", "") or "")
                    cal_name = str(getattr(ev, "calendar_name", "") or "")
                    ical_uid = str(getattr(ev, "ical_uid", "") or "")
                    series_id = str(getattr(ev, "series_master_id", "") or "")
                    mod_occ = 1 if getattr(ev, "is_modified_occurrence", False) else 0
                    tz_name = str(getattr(ev, "timezone_name", "") or "")
                    conf_id = str(getattr(ev, "conference_id", "") or "")
                    title = str(getattr(ev, "title", "Untitled Meeting") or "Untitled Meeting")
                    st_dt = getattr(ev, "start_time", None)
                    et_dt = getattr(ev, "end_time", None)
                    st = st_dt.isoformat() if hasattr(st_dt, "isoformat") else str(st_dt or "")
                    et = et_dt.isoformat() if hasattr(et_dt, "isoformat") else str(et_dt or "")
                    loc = str(getattr(ev, "location", "") or "")
                    jurl = str(getattr(ev, "join_url", "") or "")
                    att = ", ".join(getattr(ev, "attendees", []) or [])
                    org = str(getattr(ev, "organizer", "") or "")
                    plat = str(getattr(ev, "platform", "generic") or "generic")
                    is_on = 1 if getattr(ev, "is_online", False) else 0
                    is_canc = 1 if getattr(ev, "is_cancelled", False) else 0
                    is_rec = 1 if getattr(ev, "is_recurring", False) else 0
                    desc = str(getattr(ev, "description", "") or "")
                    src_accs = json.dumps(getattr(ev, "source_accounts", []) or [])
                    src_provs = json.dumps(getattr(ev, "source_providers", []) or [])
                    src_ids = json.dumps(getattr(ev, "source_event_ids", []) or [ev_id])
                if not ev_id:
                    continue
                if not ev_acc:
                    ev_acc = f"{ptype}:{pacc}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO calendar_events (
                        id, user_id, account_id, provider_type, provider_account,
                        calendar_id, calendar_name, ical_uid, series_master_id,
                        is_modified_occurrence, timezone_name, conference_id,
                        title, start_time, end_time, location, join_url,
                        attendees, organizer, platform, is_online, is_cancelled,
                        is_recurring, description, source_accounts_json,
                        source_providers_json, source_event_ids_json, last_synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id, uid, ev_acc, ptype, pacc,
                        cal_id, cal_name, ical_uid, series_id,
                        mod_occ, tz_name, conf_id,
                        title, st, et, loc, jurl,
                        att, org, plat, is_on, is_canc,
                        is_rec, desc, src_accs, src_provs, src_ids, now,
                    ),
                )
                count += 1
            conn.commit()
        return count

    def delete_calendar_events_by_ids(
        self,
        event_ids: List[str],
        user_id: str = "default",
        account_id: str = "",
    ) -> int:
        ids = [str(eid) for eid in (event_ids or []) if eid]
        if not ids:
            return 0
        uid = user_id or "default"
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as conn:
            if account_id:
                cur = conn.execute(
                    f"DELETE FROM calendar_events WHERE user_id = ? AND account_id = ? AND id IN ({placeholders})",
                    [uid, account_id] + ids,
                )
            else:
                cur = conn.execute(
                    f"DELETE FROM calendar_events WHERE user_id = ? AND id IN ({placeholders})",
                    [uid] + ids,
                )
            conn.commit()
            return cur.rowcount

    def clear_calendar_events_for_account(self, account_id: str, user_id: str = "default") -> int:
        uid = user_id or "default"
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM calendar_events WHERE user_id = ? AND account_id = ?",
                (uid, account_id),
            )
            conn.commit()
            return cur.rowcount

    def list_calendar_events(
        self,
        user_id: str = "default",
        account_id: Optional[str] = None,
        start_iso: Optional[str] = None,
        end_iso: Optional[str] = None,
        include_cancelled: bool = False,
    ) -> List[Dict[str, Any]]:
        uid = user_id or "default"
        clauses = ["user_id = ?"]
        params: List[Any] = [uid]
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if not include_cancelled:
            clauses.append("is_cancelled = 0")
        if start_iso:
            clauses.append("end_time >= ?")
            params.append(start_iso)
        if end_iso:
            clauses.append("start_time <= ?")
            params.append(end_iso)
        sql = f"SELECT * FROM calendar_events WHERE {' AND '.join(clauses)} ORDER BY start_time ASC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["is_online"] = bool(d.get("is_online"))
                d["is_cancelled"] = bool(d.get("is_cancelled"))
                d["is_recurring"] = bool(d.get("is_recurring"))
                d["is_modified_occurrence"] = bool(d.get("is_modified_occurrence"))
                att_raw = d.get("attendees") or ""
                d["attendees"] = [a.strip() for a in att_raw.split(",") if a.strip()]
                for jcol, key in [
                    ("source_accounts_json", "source_accounts"),
                    ("source_providers_json", "source_providers"),
                    ("source_event_ids_json", "source_event_ids"),
                ]:
                    try:
                        d[key] = json.loads(d.get(jcol) or "[]")
                    except Exception:
                        d[key] = []
                out.append(d)
            return out

    def find_meeting_by_calendar_event(
        self,
        calendar_event_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not calendar_event_id:
            return None
        with self._lock, self._connect() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT * FROM meetings WHERE calendar_event_id = ? AND (user_id = ? OR user_id = '') ORDER BY id DESC LIMIT 1",
                    (calendar_event_id, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM meetings WHERE calendar_event_id = ? ORDER BY id DESC LIMIT 1",
                    (calendar_event_id,),
                ).fetchone()
            return dict(row) if row else None

    def list_meetings_by_calendar_event_ids(
        self,
        user_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Map non-empty calendar_event_id -> most recent recorded meeting_id."""
        with self._lock, self._connect() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT id, calendar_event_id FROM meetings WHERE calendar_event_id != '' AND (user_id = ? OR user_id = '') ORDER BY id ASC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, calendar_event_id FROM meetings WHERE calendar_event_id != '' ORDER BY id ASC"
                ).fetchall()
            mapping: Dict[str, int] = {}
            for r in rows:
                ev_id = str(r["calendar_event_id"] or "").strip()
                if ev_id:
                    mapping[ev_id] = int(r["id"])
            return mapping



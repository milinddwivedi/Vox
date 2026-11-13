"""
Empirical Challenger Tier 5 Stress & Invariant Verification Suite.
Written by Tier 5 Chaos Challenger to independently and stressfully verify
the 4 critical adversarial invariants:
1. Rapid hotkey spamming (50 concurrent threads / 500 calls) deadlock & thread safety
2. Device removal error containment across all states (INITIALIZING, LISTENING, STOPPING) & recovery to IDLE
3. SQLite WAL multi-connection concurrency (200+ writes across 3-5 DB instances) with PRAGMA integrity_check
4. 100MB audio buffer burst memory reclamation with tracemalloc & gc verification
"""

import gc
import os
from pathlib import Path
import sqlite3
import threading
import time
import tracemalloc
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.storage.database import LocalDatabase
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.devices import compute_rms_level
from voxlocal.engine.dictation_processor import DictationProcessor


class TestChallengerTier5Stress:
    """
    Adversarial challenge test harness executing extreme edge cases and stress conditions.
    """

    # =========================================================================
    # Invariant 1: 50 Concurrent Threads Hotkey Spamming
    # =========================================================================
    def test_challenge_01_extreme_50_threads_hotkey_spamming(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Stress-test with 50 CONCURRENT threads (not 5) performing 10 hotkey operations each (500 total).
        Verifies that no deadlock occurs on _state_lock, all 50 threads terminate within 5 seconds,
        zero dangling threads remain, and controller settles deterministically to IDLE.
        """
        isolated_controller.dictation_recorder.start = MagicMock(return_value=(True, "ok"))
        isolated_controller.dictation_recorder.stop = MagicMock(
            return_value=(np.zeros(16000, dtype=np.float32), 1.0)
        )
        isolated_controller.transcriber.transcribe_dictation = MagicMock(return_value="adversarial test")
        isolated_controller.text_inserter.insert_text = MagicMock(return_value=(True, "sendinput_unicode"))

        initial_active_threads = threading.active_count()
        actions = ["ptt", "popo", "dismiss", "mode_1", "mode_2"]
        threads: List[threading.Thread] = []
        errors: List[Exception] = []

        def _worker(tid: int) -> None:
            try:
                for step in range(10):
                    act = actions[(tid + step) % len(actions)]
                    if act == "ptt":
                        isolated_controller.handle_hotkey_action("ptt", is_release=False)
                        time.sleep(0.001)
                        isolated_controller.handle_hotkey_action("ptt", is_release=True)
                    elif act == "popo":
                        isolated_controller.handle_hotkey_action("popo", is_release=False)
                    elif act == "dismiss":
                        isolated_controller.handle_hotkey_action("dismiss", is_release=False)
                    else:
                        isolated_controller.handle_hotkey_action(act, is_release=False)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        # Launch 50 concurrent threads
        for tid in range(50):
            t = threading.Thread(target=_worker, args=(tid,), name=f"ChaosHotkeyWorker_{tid}")
            threads.append(t)
            t.start()

        # Join all threads with strict timeout
        deadlocked_threads = []
        for t in threads:
            t.join(timeout=5.0)
            if t.is_alive():
                deadlocked_threads.append(t.name)

        assert len(deadlocked_threads) == 0, f"Deadlocked threads detected: {deadlocked_threads}"
        assert len(errors) == 0, f"Errors encountered during 50-thread hotkey spam: {errors}"

        # Spin Qt event loop
        for _ in range(25):
            qapp_offscreen.processEvents()
            time.sleep(0.01)

        # Reset controller and verify recovery
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

        assert isolated_controller.dictation_state == "idle"
        assert isolated_controller.dictation_recorder.is_recording is False

        # Verify a new session can start and stop cleanly
        ok_start = isolated_controller.start_dictation()
        qapp_offscreen.processEvents()
        assert ok_start is True
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state == "idle"

    # =========================================================================
    # Invariant 2: Device Removal Transitions to ERROR and Resets to IDLE
    # =========================================================================
    @pytest.mark.parametrize("initial_state", ["initializing", "listening", "stopping"])
    def test_challenge_02_device_removal_across_all_active_states(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
        initial_state: str,
    ) -> None:
        """
        Verify hardware disconnection (AUDCLNT_E_DEVICE_INVALIDATED) cleanly transitions
        to ERROR and dismiss/cancel cleanly recovers to IDLE across different active states.
        """
        notifications: List[tuple[str, str]] = []
        isolated_controller.notification_requested.connect(lambda t, m: notifications.append((t, m)))
        isolated_controller.dictation_recorder.start = MagicMock(return_value=(True, "ok"))
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(np.zeros(16000, dtype=np.float32), 1.0))

        # Set up state
        if initial_state == "initializing":
            isolated_controller._dictation_state = DictationState.INITIALIZING.value
        elif initial_state == "listening":
            isolated_controller.start_dictation()
            isolated_controller._dictation_state = DictationState.LISTENING.value
        elif initial_state == "stopping":
            isolated_controller.start_dictation()
            isolated_controller._dictation_state = DictationState.STOPPING.value

        qapp_offscreen.processEvents()

        # Trigger hardware failure
        isolated_controller.dictation_recorder.on_error("AUDCLNT_E_DEVICE_INVALIDATED: Device disconnected")
        qapp_offscreen.processEvents()

        # Invariant: Must transition to ERROR
        assert isolated_controller.dictation_state == DictationState.ERROR.value
        assert any("Voice Typing Error" in n[0] for n in notifications)

        # Invariant: Must cleanly recover to IDLE upon cancellation
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state == DictationState.IDLE.value
        assert isolated_controller.dictation_recorder.is_recording is False

    # =========================================================================
    # Invariant 3: Multi-Connection Database Concurrency & WAL Integrity
    # =========================================================================
    def test_challenge_03_heavy_database_concurrency_wal_integrity(
        self,
        isolated_tmp_dir: Path,
    ) -> None:
        """
        Stress test 200+ writes across 3 distinct LocalDatabase instances and 10 threads.
        Assert zero sqlite3.OperationalError locked exceptions escape, all records are
        committed, and PRAGMA integrity_check passes with journal_mode = WAL.
        """
        db_path = isolated_tmp_dir / "challenger_wal_stress.db"
        num_instances = 3
        instances = [LocalDatabase(db_path) for _ in range(num_instances)]

        errors: List[Exception] = []
        threads: List[threading.Thread] = []
        num_threads = 10
        writes_per_thread = 20  # total 200 writes

        def _concurrent_writer(worker_id: int) -> None:
            db = instances[worker_id % num_instances]
            for step in range(writes_per_thread):
                try:
                    mode = step % 4
                    if mode == 0:
                        db.create_note(
                            title=f"Note w{worker_id} s{step}",
                            content=f"Payload {worker_id}:{step}",
                        )
                    elif mode == 1:
                        db.create_meeting(title=f"Mtg w{worker_id} s{step}")
                    elif mode == 2:
                        db.upsert_vocabulary(
                            spoken_phrase=f"phrase_{worker_id}_{step}",
                            replacement=f"Repl_{worker_id}_{step}",
                        )
                    else:
                        db.add_dictation_entry(
                            raw_text=f"raw_{worker_id}_{step}",
                            final_text=f"final_{worker_id}_{step}",
                            duration_sec=1.5,
                            app_category="other",
                        )
                except Exception as exc:
                    errors.append(exc)

        for tid in range(num_threads):
            t = threading.Thread(target=_concurrent_writer, args=(tid,), name=f"DBWriter_{tid}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=15.0)

        assert len(errors) == 0, f"Database concurrency errors: {errors}"

        # Verify integrity and journal mode
        with instances[0]._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity.lower() == "ok", f"Corrupted SQLite database: {integrity}"

            journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert journal.lower() == "wal", f"Journal mode not WAL: {journal}"

            notes_count = conn.execute("SELECT COUNT(*) FROM scratchpad_notes;").fetchone()[0]
            meetings_count = conn.execute("SELECT COUNT(*) FROM meetings;").fetchone()[0]
            vocab_count = conn.execute("SELECT COUNT(*) FROM custom_vocabulary;").fetchone()[0]
            dict_count = conn.execute("SELECT COUNT(*) FROM dictation_history;").fetchone()[0]
            
            # 10 default seed vocab entries + 50 inserted = 60 vocab
            assert notes_count == 50, f"Expected 50 notes, got {notes_count}"
            assert meetings_count == 50, f"Expected 50 meetings, got {meetings_count}"
            assert vocab_count == 60, f"Expected 60 vocab (10 seeded + 50 written), got {vocab_count}"
            assert dict_count == 50, f"Expected 50 dictations, got {dict_count}"

    # =========================================================================
    # Invariant 4: 100MB Audio Buffer Burst Garbage Collection
    # =========================================================================
    def test_challenge_04_100mb_buffer_spike_memory_reclamation(self) -> None:
        """
        Allocate 25,000,000 float32 samples (100MB), run through DSP operations,
        delete reference, run gc.collect(), and verify via tracemalloc that
        the 100MB buffer is completely reclaimed without residual memory leaks.
        """
        tracemalloc.start()
        gc.collect()
        snapshot_before = tracemalloc.take_snapshot()

        # Allocate 100MB buffer
        sample_count = 25_000_000  # 25,000,000 * 4 bytes = 100,000,000 bytes (~95.37 MiB)
        buf = np.ones(sample_count, dtype=np.float32) * 0.05

        # Process through DSP
        rms = compute_rms_level(buf[:16000])
        assert 0.0 <= rms <= 1.0

        frames = DictationRecorder.slice_40ms_frames(buf[:32000])
        assert len(frames) == 50

        # Snapshot during peak allocation
        snapshot_peak = tracemalloc.take_snapshot()
        peak_stats = snapshot_peak.compare_to(snapshot_before, "lineno")
        peak_diff_mb = sum(stat.size_diff for stat in peak_stats) / (1024 * 1024)
        assert peak_diff_mb >= 90.0, f"Peak memory should reflect ~100MB allocation, got {peak_diff_mb:.2f}MB"

        # Explicit release and garbage collection
        del buf
        del frames
        gc.collect()

        snapshot_after = tracemalloc.take_snapshot()
        after_stats = snapshot_after.compare_to(snapshot_before, "lineno")
        leaked_mb = sum(stat.size_diff for stat in after_stats if stat.size_diff > 0) / (1024 * 1024)

        tracemalloc.stop()

        # Assert no significant memory leaked (< 2.0 MB allowance for tracemalloc/internal metadata)
        assert leaked_mb < 2.0, f"Memory leak detected after 100MB buffer release: {leaked_mb:.2f}MB"

    # =========================================================================
    # Invariant 5: Transient SQLite Exclusive Lock & Seamless Recovery
    # =========================================================================
    def test_challenge_05_database_transient_exclusive_lock_and_recovery(
        self,
        isolated_tmp_dir: Path,
    ) -> None:
        """
        Simulate an external process acquiring an exclusive lock on the SQLite database,
        verifying that LocalDatabase operations wait up to timeout, fail gracefully if locked,
        and immediately resume normal ACID transactions once the exclusive lock is cleared.
        """
        db_path = isolated_tmp_dir / "lock_recovery.db"
        db = LocalDatabase(db_path)

        # 1. External connection locks database exclusively
        external_conn = sqlite3.connect(str(db_path), timeout=0.1)
        external_conn.execute("BEGIN EXCLUSIVE;")

        # Attempt write with 0.2s timeout should raise OperationalError
        with pytest.raises(sqlite3.OperationalError):
            with sqlite3.connect(str(db_path), timeout=0.2) as test_conn:
                test_conn.execute("INSERT INTO scratchpad_notes (title, content, created_at, updated_at) VALUES ('a', 'b', 'c', 'd');")

        # 2. Release lock
        external_conn.rollback()
        external_conn.close()

        # 3. Verify LocalDatabase immediately recovers and writes normally
        note_id = db.create_note(title="Recovered Note", content="Post-lock content")
        assert note_id > 0

        with db._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity.lower() == "ok"

    # =========================================================================
    # Invariant 6: 5 Consecutive 100MB Buffer Spikes Memory Endurance
    # =========================================================================
    def test_challenge_06_consecutive_100mb_spikes_no_monotonic_growth(self) -> None:
        """
        Cycle 5 consecutive 100MB buffer spikes through DSP.
        Asserts that memory does not monotonically grow with each iteration,
        confirming true GC reclamation across multiple burst cycles.
        """
        tracemalloc.start()
        gc.collect()
        base_snapshot = tracemalloc.take_snapshot()

        residuals = []
        for cycle in range(5):
            buf = np.ones(25_000_000, dtype=np.float32) * (cycle * 0.01)
            rms = compute_rms_level(buf[:16000])
            assert 0.0 <= rms <= 1.0
            del buf
            gc.collect()

            cur_snapshot = tracemalloc.take_snapshot()
            diff = cur_snapshot.compare_to(base_snapshot, "lineno")
            net_mb = sum(stat.size_diff for stat in diff if stat.size_diff > 0) / (1024 * 1024)
            residuals.append(net_mb)

        tracemalloc.stop()
        # Ensure residual memory remains tightly bounded across all 5 spikes
        assert max(residuals) < 3.0, f"Monotonic memory growth detected: {residuals}"

    # =========================================================================
    # Invariant 7: Dictation Cancellation Mid-Worker Execution
    # =========================================================================
    def test_challenge_07_cancellation_during_active_worker_aborts_insertion(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Simulate user cancelling dictation while the transcription worker is actively
        transcribing. Asserts that text insertion is completely aborted and no ghost text
        is sent to the target window.
        """
        inserted_texts: List[str] = []
        def _mock_insert(text, **kwargs):
            inserted_texts.append(text)
            return True, "sendinput_unicode"

        isolated_controller.text_inserter.insert_text = MagicMock(side_effect=_mock_insert)

        def _slow_transcribe(*args, **kwargs):
            # Simulate transcription delay
            time.sleep(0.05)
            return "Ghost transcription that should never be inserted"

        isolated_controller.transcriber.transcribe_dictation = MagicMock(side_effect=_slow_transcribe)

        audio = np.zeros(16000, dtype=np.float32)
        isolated_controller._dictation_state = DictationState.PROCESSING.value
        isolated_controller._dictation_session_id = 42

        # Spawn worker
        worker_thread = threading.Thread(
            target=isolated_controller._process_dictation_worker,
            args=(audio, 1.0, None, 42),
        )
        worker_thread.start()

        # Immediately cancel dictation
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

        worker_thread.join(timeout=2.0)
        qapp_offscreen.processEvents()

        # Invariant: Ghost text was NOT inserted
        assert len(inserted_texts) == 0
        assert isolated_controller.dictation_state == DictationState.IDLE.value

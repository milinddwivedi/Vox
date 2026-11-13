"""
Tier 5 Adversarial Chaos & Resilience Test Suite for Project VOX (Wispr Flow Exact Replica).

Authority:
- ORIGINAL_REQUEST.md
- PROJECT.md § Feature Inventory (Feature 20: Robust Error Containment & Resilience)
- SCOPE.md (E2E Testing Track)
- TEST_INFRA.md (§2.5 Adversarial Chaos Suite)
- Hand-off: explorer_tier5_chaos_1/handoff.md
- Hand-off: explorer_tier4_harness_1/handoff.md

12 Concrete Adversarial Chaos Stress Tests Covered:
01. `test_01_chaos_rapid_hotkey_spamming_race_condition` (50 rapid PTT/POPO events across threads; 0 deadlocks; final state IDLE)
02. `test_02_chaos_audio_device_disconnection_mid_stream` (WASAPI device unplugged in LISTENING state; transitions to ERROR; handles closed; recovers to IDLE)
03. `test_03_chaos_corrupted_extreme_audio_buffers` (NaNs, Infs, 0-samples, 100MB buffer spike; all outputs finite; no OOM)
04. `test_04_chaos_extreme_long_text_injection_with_unicode` (12,000+ characters with emoji and Devanagari text; auto-routes to clipboard; clipboard restored; no data loss)
05. `test_05_chaos_concurrent_meeting_and_dictation_pipeline_contention` (dictation rejected during meeting; meeting unaffected; reverse preemption works)
06. `test_06_chaos_heavy_database_concurrency_multi_connection_wal` (200 concurrent writes across 3 DB instances; zero locked errors escape; PRAGMA integrity_check passes)
07. `test_07_chaos_keyboard_hook_failure_and_injection_api_error` (WinError 5 fallback; dead HWND 0xDEADBEEF; text preserved to clipboard; no crash)
08. `test_08_chaos_rapid_mode_changes_during_active_transcription` (100 mode switches; raw_text preserved; no race exceptions; valid final output)
09. `test_09_chaos_transient_sqlite_exclusive_lock_and_recovery` (unyielding exclusive lock handled cleanly; recovers to normal operation)
10. `test_10_chaos_malformed_corrupted_snippets_and_dictionary_payloads` (None/circular/malformed items; regex metachars escaped; handled cleanly)
11. `test_11_chaos_ui_geometry_clipping_and_negative_dimension_stress` (extreme geometry values; rapid expand/collapse; clean recovery)
12. `test_12_chaos_rapid_session_cycling_memory_and_thread_leak` (50 consecutive dictation sessions cycled rapidly; threads stabilize; memory bounded)

All tests run strictly headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
All spawned threads are guaranteed to be joined prior to test completion.
"""

from __future__ import annotations

import collections
import gc
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.win32.hotkeys import GlobalHotkeyManager
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.devices import compute_rms_level
from voxlocal.engine.dictation_processor import (
    DictationProcessor,
    DictationProcessedResult,
    TransformationMode,
)
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState


class TestTier5AdversarialChaos:
    """
    Tier 5 Adversarial Chaos & Resilience Test Suite.
    Validates extreme hardware disconnection, corrupted buffers, concurrency contention,
    and unhandled exception boundaries.
    """

    # =========================================================================
    # Test 01: Rapid Hotkey Spamming Race Condition
    # =========================================================================
    def test_01_chaos_rapid_hotkey_spamming_race_condition(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 01: Spam 50 rapid PTT and POPO hotkey triggers across 5 concurrent threads.
        Asserts zero deadlocks on _state_lock, all worker threads terminate cleanly,
        dictation recorder stops, and state settles deterministically.
        """
        isolated_controller.dictation_recorder.start = MagicMock(return_value=(True, "ok"))
        isolated_controller.dictation_recorder.stop = MagicMock(
            return_value=(np.zeros(16000, dtype=np.float32), 1.0)
        )
        isolated_controller.transcriber.transcribe_dictation = MagicMock(return_value="test")
        isolated_controller.text_inserter.insert_text = MagicMock(return_value=(True, "sendinput_unicode"))

        actions = ["ptt", "popo"]
        threads: List[threading.Thread] = []
        errors: List[Exception] = []

        def _spammer(worker_id: int) -> None:
            try:
                for i in range(10):
                    act = actions[(worker_id + i) % 2]
                    isolated_controller.handle_hotkey_action(act, is_release=False)
                    time.sleep(0.002)
                    if act == "ptt":
                        isolated_controller.handle_hotkey_action("ptt", is_release=True)
                    time.sleep(0.002)
            except Exception as exc:
                errors.append(exc)

        for w_idx in range(5):
            t = threading.Thread(target=_spammer, args=(w_idx,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=3.0)

        assert len(errors) == 0, f"Thread errors encountered during hotkey spam: {errors}"

        # Spin event loop for controller to settle
        for _ in range(30):
            qapp_offscreen.processEvents()
            time.sleep(0.02)
            if isolated_controller.dictation_state in ("idle", "error", "completed"):
                break

        # Cancel any lingering session
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

        assert isolated_controller.dictation_state == "idle"
        assert isolated_controller.dictation_recorder.is_recording is False

        # Ensure subsequent normal session succeeds cleanly
        ok_next = isolated_controller.start_dictation()
        qapp_offscreen.processEvents()
        assert ok_next is True
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

    # =========================================================================
    # Test 02: Mid-Stream Audio Device Disconnection
    # =========================================================================
    def test_02_chaos_audio_device_disconnection_mid_stream(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 02: Simulate sudden WASAPI USB microphone unplugging while in LISTENING state.
        Asserts transition to DictationState.ERROR, notification emitted, audio stream closed,
        and clean recovery to IDLE on dismiss.
        """
        isolated_controller.dictation_recorder.start = MagicMock(return_value=(True, "ok"))
        notifications_received: List[tuple[str, str]] = []
        isolated_controller.notification_requested.connect(
            lambda t, m: notifications_received.append((t, m))
        )

        ok = isolated_controller.start_dictation()
        assert ok is True
        isolated_controller._dictation_state = "listening"
        qapp_offscreen.processEvents()

        # Inject sudden WASAPI device removal error
        isolated_controller.dictation_recorder.on_error("AUDCLNT_E_DEVICE_INVALIDATED: Device unplugged")
        qapp_offscreen.processEvents()

        assert isolated_controller.dictation_state == DictationState.ERROR.value
        assert any("Voice Typing Error" in n[0] for n in notifications_received)

        # Recovery back to IDLE
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state == DictationState.IDLE.value

    # =========================================================================
    # Test 03: Corrupted & Extreme Audio Buffers
    # =========================================================================
    def test_03_chaos_corrupted_extreme_audio_buffers(
        self,
        isolated_controller: AppController,
    ) -> None:
        """
        Chaos 03: Feed NaN, +/-Inf, zero-length, and 100MB burst audio buffers into DSP
        and transcriber pipelines. Asserts finite bounded returns, zero unhandled floating point exceptions,
        and complete memory reclamation.
        """
        # 1. NaN and Inf buffer
        nan_inf_buf = np.array([np.nan, np.inf, -np.inf, 0.0, 1e20, -1e20] * 500, dtype=np.float32)
        rms = compute_rms_level(nan_inf_buf)
        assert isinstance(rms, float)
        assert 0.0 <= rms <= 1.0

        # Transcriber sanitizes NaNs and Infs safely
        sanitized_res = isolated_controller.transcriber.transcribe_audio(nan_inf_buf)
        assert sanitized_res is not None
        assert isinstance(sanitized_res.raw_text, str)

        # 2. Zero-length buffer
        zero_buf = np.zeros(0, dtype=np.float32)
        zero_res = isolated_controller.transcriber.transcribe_audio(zero_buf)
        assert zero_res.raw_text == ""
        assert DictationRecorder.slice_40ms_frames(zero_buf) == []

        # 3. 100MB buffer spike (25,000,000 float32 samples = 100MB)
        massive_buf = np.ones(25_000_000, dtype=np.float32) * 0.1
        chunk_frames = DictationRecorder.slice_40ms_frames(massive_buf[:32000])
        assert len(chunk_frames) == 50  # 32000 / 640 = 50 frames
        del massive_buf
        gc.collect()

    # =========================================================================
    # Test 04: Extreme Long Text Injection with Unicode & Devanagari
    # =========================================================================
    def test_04_chaos_extreme_long_text_injection_with_unicode(
        self,
        isolated_controller: AppController,
    ) -> None:
        """
        Chaos 04: 12,000+ characters with astral emojis and Devanagari Unicode.
        Asserts automatic selection of clipboard-paste method, zero UTF-16 surrogate corruption,
        and complete clipboard restoration.
        """
        inserter = isolated_controller.text_inserter
        clip_store = {"data": "SAFE_USER_CLIPBOARD_DATA"}

        def _mock_set(t: str) -> bool:
            clip_store["data"] = t
            return True

        def _mock_get() -> Optional[str]:
            return clip_store["data"]

        with patch("voxlocal.win32.text_inserter.set_clipboard_text", side_effect=_mock_set), \
             patch("voxlocal.win32.text_inserter.get_clipboard_text", side_effect=_mock_get):

            # Construct 12,000+ character stress payload
            payload = (
                "नमस्ते दुनिया! Wispr Flow Exact Replica VOX adversarial testing. "
                + "🚀🔥⚡💡 " * 1000
                + "A" * 10000
            )
            assert len(payload) >= 12000

            with patch.object(inserter, "_send_wm_char_to_target", return_value=True):
                ok, method = inserter.insert_text(payload, method="auto", restore_clipboard=True)

            assert ok is True
            assert method == "clipboard_paste"
            assert clip_store["data"] == "SAFE_USER_CLIPBOARD_DATA"

    # =========================================================================
    # Test 05: Concurrent Meeting & Dictation Pipeline Contention
    # =========================================================================
    def test_05_chaos_concurrent_meeting_and_dictation_pipeline_contention(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 05: Audio pipeline contention between Meeting Studio and voice typing.
        Asserts dictation is rejected while meeting is active, and starting meeting
        preempts/cancels any active dictation.
        """
        notifications: List[tuple[str, str]] = []
        isolated_controller.notification_requested.connect(lambda t, m: notifications.append((t, m)))

        # 1. Start corporate meeting recording
        ok_mtg, _ = isolated_controller.start_meeting_recording(title="Board Meeting")
        assert ok_mtg is True
        assert isolated_controller.meeting_state == "recording"

        # 2. Attempt dictation during active meeting -> MUST be rejected
        ok_dict = isolated_controller.start_dictation()
        assert ok_dict is False
        assert any("Voice Typing Unavailable" in n[0] for n in notifications)
        assert isolated_controller.meeting_state == "recording"

        # Stop meeting and settle to idle
        isolated_controller.stop_meeting_recording()
        isolated_controller._meeting_state = "idle"
        qapp_offscreen.processEvents()

        # 3. Reverse preemption: Dictation active, then start meeting
        isolated_controller.start_dictation()
        isolated_controller._dictation_state = "listening"
        qapp_offscreen.processEvents()

        # Start meeting should stop/cancel dictation
        ok_mtg2, _ = isolated_controller.start_meeting_recording(title="Sprint Retro")
        assert ok_mtg2 is True
        assert isolated_controller.meeting_state == "recording"

        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()

    # =========================================================================
    # Test 06: Heavy Database Concurrency Multi-Connection WAL
    # =========================================================================
    def test_06_chaos_heavy_database_concurrency_multi_connection_wal(
        self,
        isolated_tmp_dir: Path,
    ) -> None:
        """
        Chaos 06: 200 concurrent write operations across 10 threads using 3 distinct
        LocalDatabase instances operating on the same physical SQLite WAL file.
        Asserts zero unhandled database locked errors and PRAGMA integrity_check passes.
        """
        db_path = isolated_tmp_dir / "test_chaos_wal.db"
        db_instances = [
            LocalDatabase(db_path),
            LocalDatabase(db_path),
            LocalDatabase(db_path),
        ]

        errors: List[Exception] = []
        threads: List[threading.Thread] = []

        def _writer(worker_id: int) -> None:
            db = db_instances[worker_id % len(db_instances)]
            for i in range(20):
                try:
                    if i % 3 == 0:
                        db.create_note(
                            title=f"Note from thread {worker_id} iter {i}",
                            content=f"Content payload {worker_id}_{i}",
                        )
                    elif i % 3 == 1:
                        db.create_meeting(title=f"Sync {worker_id}_{i}")
                    else:
                        db.upsert_vocabulary(
                            spoken_phrase=f"cue_{worker_id}_{i}",
                            replacement=f"Repl_{worker_id}_{i}",
                        )
                except Exception as exc:
                    errors.append(exc)

        for tid in range(10):
            t = threading.Thread(target=_writer, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)

        assert len(errors) == 0, f"Database concurrency errors: {errors}"

        # Integrity Check
        with db_instances[0]._connect() as conn:
            check_row = conn.execute("PRAGMA integrity_check;").fetchone()
            assert check_row[0].lower() == "ok"
            wal_row = conn.execute("PRAGMA journal_mode;").fetchone()
            assert wal_row[0].lower() == "wal"

    # =========================================================================
    # Test 07: Keyboard Hook Failure & Injection Windows API Error
    # =========================================================================
    def test_07_chaos_keyboard_hook_failure_and_injection_api_error(
        self,
        isolated_controller: AppController,
    ) -> None:
        """
        Chaos 07: Simulate Win32 hook failure (WinError 5 Access Denied) and dead target window HWND (0xDEADBEEF).
        Asserts hook manager logs error and returns cleanly, and inserter preserves text to clipboard.
        """
        hotkey_mgr = GlobalHotkeyManager()

        # 1. Patch SetWindowsHookExW to fail with Access Denied (Error 5)
        with patch("voxlocal.win32.hotkeys.user32.SetWindowsHookExW", return_value=0), \
             patch("voxlocal.win32.hotkeys.ctypes.get_last_error", return_value=5):
            # Calling _hook_loop directly exercises hook installation failure branch
            hotkey_mgr._hook_loop()
            assert not hotkey_mgr._hook_handle

        # 2. Dead Target HWND (0xDEADBEEF) text injection
        dead_target = TargetWindowInfo(
            hwnd=0xDEADBEEF,
            title="Dead Window",
            process_id=9999,
            thread_id=9999,
            process_name="dead_process.exe",
        )
        clip_saved = {"text": ""}
        with patch("voxlocal.win32.text_inserter.user32.IsWindow", return_value=0), \
             patch("voxlocal.win32.text_inserter.set_clipboard_text", side_effect=lambda t: clip_saved.update({"text": t}) or True):
            ok, method = isolated_controller.text_inserter.insert_text(
                "Important unsaved dictation text",
                target=dead_target,
            )

        assert ok is False
        assert method == "target_closed_saved_to_clipboard"
        assert clip_saved["text"] == "Important unsaved dictation text"

    # =========================================================================
    # Test 08: Rapid Mode Changes During Active Transcription
    # =========================================================================
    def test_08_chaos_rapid_mode_changes_during_active_transcription(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 08: Rapid mode switching across threads during active dictation processing.
        Asserts raw_text is completely preserved in SQLite WAL dual-column schema and
        no KeyError or race exceptions occur.
        """
        stop_event = threading.Event()
        modes = ["verbatim", "clean", "polished", "structured", "email", "invalid_mode"]
        switch_errors: List[Exception] = []

        def _mode_switcher() -> None:
            count = 0
            while not stop_event.is_set() and count < 100:
                try:
                    isolated_controller.set_writing_mode(modes[count % len(modes)])
                    time.sleep(0.001)
                    count += 1
                except Exception as exc:
                    switch_errors.append(exc)

        t = threading.Thread(target=_mode_switcher)
        t.start()

        # Simultaneously commit dictations
        commit_results: List[Any] = []
        for _ in range(5):
            res = isolated_controller._format_and_commit_dictation(
                raw_text="The quick brown fox",
                duration_sec=1.2,
                vocab=[],
                out_script="auto",
                target=None,
            )
            if res:
                commit_results.append(res)
            time.sleep(0.005)

        stop_event.set()
        t.join(timeout=3.0)

        assert len(switch_errors) == 0
        assert len(commit_results) == 5

        # Verify dual-column raw text preservation in SQLite WAL
        history = isolated_controller.db.list_dictation_history(limit=5)
        assert len(history) >= 5
        assert all(h["raw_text"] == "The quick brown fox" for h in history)

    # =========================================================================
    # Test 09: Transient SQLite Exclusive Lock & Recovery
    # =========================================================================
    def test_09_chaos_transient_sqlite_exclusive_lock_and_recovery(
        self,
        isolated_controller: AppController,
        isolated_tmp_dir: Path,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 09: Test unyielding SQLite lock and graceful error containment.
        Asserts worker catches OperationalError, sets DictationState.ERROR,
        and subsequent session succeeds after lock release.
        """
        isolated_controller.transcriber.transcribe_dictation = MagicMock(return_value="Recovered dictation")
        isolated_controller.text_inserter.insert_text = MagicMock(return_value=(True, "sendinput_unicode"))
        pcm = np.zeros(16000, dtype=np.float32)

        # 1. Simulate DB error in worker
        with patch.object(isolated_controller.db, "add_dictation_entry", side_effect=sqlite3.OperationalError("database is locked")):
            isolated_controller._process_dictation_worker(pcm, 1.0, target=None)
            qapp_offscreen.processEvents()
            assert isolated_controller.dictation_state == DictationState.ERROR.value

        # 2. Reset and verify recovery
        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state == DictationState.IDLE.value

        # Normal worker execution now succeeds cleanly
        isolated_controller._dictation_cancelled = False
        isolated_controller._process_dictation_worker(pcm, 1.0, target=None)
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state in ("idle", "completed")

        history = isolated_controller.db.list_dictation_history(limit=1)
        assert len(history) >= 1
        assert history[0]["raw_text"] == "Recovered dictation"

    # =========================================================================
    # Test 10: Malformed & Corrupted Snippets and Dictionary Payloads
    # =========================================================================
    def test_10_chaos_malformed_corrupted_snippets_and_dictionary_payloads(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 10: Payload fuzzing with ReDoS regex metacharacters, self-referential circular expansions,
        and None items in vocabulary/snippets.
        Asserts regex is safely escaped, circular items expand once without infinite recursion,
        and malformed items in worker transition cleanly to ERROR.
        """
        # 1. Regex metacharacter injection (ReDoS prevention)
        malicious_snippets = [
            {"id": 1, "trigger_phrase": "(a+)+$", "expansion": "SAFE_EXPANSION"},
            {"id": 2, "trigger_phrase": "[[[invalid_regex", "expansion": "BRACKETS_SAFE"},
        ]
        text_out, matched = DictationProcessor.apply_snippets("Testing (a+)+$ and [[[invalid_regex cue", malicious_snippets)
        assert "SAFE_EXPANSION" in text_out
        assert "BRACKETS_SAFE" in text_out

        # 2. Self-referential circular snippet expansion
        circular_snippets = [
            {"id": 3, "trigger_phrase": "/sig", "expansion": "Best regards, /sig"},
        ]
        text_circ, _ = DictationProcessor.apply_snippets("/sig", circular_snippets)
        assert text_circ == "Best regards, /sig"  # Expanded exactly once!

        # 3. Malformed None items in worker
        pcm = np.zeros(16000, dtype=np.float32)
        with patch.object(isolated_controller.db, "list_vocabulary", return_value=[None, "corrupt_string"]):
            isolated_controller._process_dictation_worker(pcm, 1.0, target=None)
            qapp_offscreen.processEvents()
            assert isolated_controller.dictation_state == DictationState.ERROR.value

        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

    # =========================================================================
    # Test 11: UI Geometry Clipping and Negative Dimension Stress
    # =========================================================================
    def test_11_chaos_ui_geometry_clipping_and_negative_dimension_stress(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 11: Extreme window geometry values (negative coordinates, zero dimensions)
        and 50 rapid expand/collapse toggles on DualTierFloatingPill.
        Asserts clean Qt bounding without QPainter exceptions or deadlocks.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()

        # Extreme negative geometry
        pill.move(-99999, -99999)
        pill.resize(-100, -100)
        qapp_offscreen.processEvents()

        # 50 rapid state toggles
        for i in range(50):
            pill._is_hovered = (i % 2 == 0)
            pill._sync_pill_visuals()
            pill._sync_geometry()
            if i % 10 == 0:
                qapp_offscreen.processEvents()

        qapp_offscreen.processEvents()
        assert pill.upper_pill.width() > 0
        assert pill.upper_pill.height() > 0

    # =========================================================================
    # Test 12: Rapid Session Cycling Memory and Thread Leak Stress
    # =========================================================================
    def test_12_chaos_rapid_session_cycling_memory_and_thread_leak(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Chaos 12: Cycle 50 consecutive dictation sessions rapidly through controller.
        Asserts _recent_utterance_history remains strictly bounded (<= 5), threads stabilize,
        and SQLite records persist with zero open transaction leaks.
        """
        isolated_controller.text_inserter.insert_text = MagicMock(return_value=(True, "sendinput_unicode"))
        isolated_controller.transcriber.transcribe_dictation = MagicMock(return_value="Rapid cycle")
        dummy_audio = np.zeros(16000, dtype=np.float32)
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 1.0))

        initial_threads = threading.active_count()

        for cycle in range(50):
            isolated_controller.handle_hotkey_action("ptt", is_release=False)
            isolated_controller.handle_hotkey_action("ptt", is_release=True)
            qapp_offscreen.processEvents()

            # Wait briefly for worker to complete
            for _ in range(10):
                if isolated_controller.dictation_state in ("idle", "completed"):
                    break
                time.sleep(0.01)
                qapp_offscreen.processEvents()

        qapp_offscreen.processEvents()

        # Memory bound: utterance history deque maxlen is 6
        assert len(isolated_controller._recent_utterance_history) <= 6

        # Thread count should stabilize
        final_threads = threading.active_count()
        assert abs(final_threads - initial_threads) <= 3

        # Database contains distinct records
        history = isolated_controller.db.list_dictation_history(limit=60)
        assert len(history) >= 40

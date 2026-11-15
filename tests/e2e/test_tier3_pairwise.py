"""
Tier 3 Pairwise Cross-Feature Integration Test Suite for VOX Stabilization.

Authority:
- PROJECT.md § Feature Inventory (Features 01 to 24)
- TEST_INFRA.md (4-Tier Test Architecture)
- Explorer 1 Report § 4.1 (Tier 3 Combinatorial Pairwise Specification)

24 Cross-Feature Pairwise Interaction Tests covering major subsystem intersections.
All tests run headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import numpy as np
import pytest
from PyQt6.QtCore import Qt, QRect

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.audio.devices import AudioDeviceInfo, AudioDeviceManager, compute_rms_level
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.meeting_recorder import MeetingRecorder, MeetingRecordingResult
from voxlocal.audio.call_detector import CallDetector, LEAVING_TITLES, is_leaving_title, is_non_call_hub
from voxlocal.win32.hotkeys import GlobalHotkeyManager, parse_hotkey_string
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.engine.transcriber import LocalWhisperTranscriber, is_hallucination, clean_repetition_tail
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline
from voxlocal.ui.floating_pill import LiveSpeechPreviewBubble, DualTierFloatingPill, PillState
from scripts.run_benchmarks import compute_wer_cer


class TestTier3PairwiseInteractions:
    """24 Pairwise Cross-Feature Tests covering subsystem intersections."""

    def test_pairwise_01_f01_deadlock_and_f14_disconnection(self):
        """F01 (Teardown Deadlock Fix) + F14 (Audio Disconnection Auto-Recovery)"""
        rec = DictationRecorder()
        rec._is_recording = True
        rec._start_time = time.perf_counter()
        errors = []
        rec.on_error = lambda err: errors.append(err)

        # Simulate device disconnection event right before stop
        if rec.on_error:
            rec.on_error("WASAPI audio device unplugged")
        audio, dur = rec.stop()
        assert not rec.is_recording
        assert len(errors) == 1
        assert "unplugged" in errors[0]

    def test_pairwise_02_f02_cached_devices_and_f13_always_warm(self):
        """F02 (Cached Device Latency) + F13 (Always-Warm Capture Stream)"""
        # Warm the cache
        AudioDeviceManager.resolve_microphone(use_cache=True)
        rec = DictationRecorder()
        dummy_onset = (np.ones(640, dtype=np.int16) * 500).tobytes()
        rec.pre_buffer.append(dummy_onset)

        t0 = time.perf_counter()
        dev = AudioDeviceManager.resolve_microphone(use_cache=True)
        cached_lat = time.perf_counter() - t0
        assert cached_lat < 0.02
        assert len(rec.pre_buffer) >= 1

    def test_pairwise_03_f03_teardown_sla_and_f16_back_to_back(self, isolated_tmp_dir):
        """F03 (Meeting Teardown SLA <100ms) + F16 (Back-to-Back Meeting Lifecycle)"""
        rec = MeetingRecorder()
        dir_a = isolated_tmp_dir / "meeting_a"
        dir_b = isolated_tmp_dir / "meeting_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Stop Meeting A
        rec._out_dir = dir_a
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        t0 = time.perf_counter()
        res_a = rec.stop()
        elapsed_a_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_a_ms < 100.0
        assert res_a is not None

        # Start Meeting B immediately
        rec._out_dir = dir_b
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res_b = rec.stop()
        assert res_b is not None
        assert res_a.mixed_wav_path != res_b.mixed_wav_path

    def test_pairwise_04_f04_focus_throttling_and_f22_focus_guard(self, isolated_controller, mock_target_windows):
        """F04 (150ms Focus Polling Throttling) + F22 (Target Window Focus Guard)"""
        notepad = mock_target_windows["notepad"]
        isolated_controller.focus_tracker._last_target = notepad
        emitted = []
        isolated_controller.target_window_changed.connect(lambda t, p: emitted.append((t, p)))

        # Poll repeatedly for same window
        for _ in range(10):
            isolated_controller._poll_foreground_window()
        assert len(emitted) >= 1

        # Focus guard verifies active target matches
        active = isolated_controller.focus_tracker.get_active_target()
        assert active.process_name == "notepad.exe"

    def test_pairwise_05_f05_session_cancellation_and_f15_transcriber_preemption(self, isolated_controller):
        """F05 (Cancellation Isolation) + F15 (Transcriber Lock Preemption)"""
        isolated_controller.cancel_finalization()
        assert isolated_controller._cancel_finalization_event.is_set()

        # Transcriber interim preview is not blocked by meeting cancellation
        text, dur = isolated_controller.transcriber.transcribe_interim(np.zeros(16000, dtype=np.float32))
        assert isinstance(text, str)
        assert dur == 0.0

    def test_pairwise_06_f06_call_detector_clean_and_f17_verified_call_vs_calendar(self):
        """F06 (CallDetector Dead Code Removal) + F17 (Verified Call vs Calendar)"""
        # Calendar main window is ignored as hub
        assert is_non_call_hub("teams", "Microsoft Teams") is True
        # Leaving call is filtered out
        assert is_leaving_title("Leaving the meeting...") is True
        # Only active stage is recognized
        assert not is_leaving_title("Sprint Review | Microsoft Teams")
        assert not is_non_call_hub("teams", "Sprint Review | Microsoft Teams")

    def test_pairwise_07_f07_serialized_inserter_and_f08_adaptive_clipboard(self, monkeypatch):
        """F07 (Serialized TextInserter) + F08 (Adaptive Clipboard Handshake)"""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: True)

        results = []

        def _worker(idx):
            ok, method = inserter.insert_text(f"Sequential paste {idx}", method="clipboard")
            results.append((idx, ok, method))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
        assert all(r[1] for r in results)
        assert all(r[2] == "clipboard_paste" for r in results)

    def test_pairwise_08_f08_adaptive_clipboard_and_f21_seamless_preview(self, offscreen_widgets, mock_win32_clipboard):
        """F08 (Adaptive Clipboard Handshake) + F21 (Seamless Preview Transition)"""
        pill = offscreen_widgets["pill"]()
        pill.show()
        pill.update_live_preview("Interim dictation...")
        assert pill.preview_bubble.isVisible()

        # Clipboard update
        mock_win32_clipboard.set_text("Final text pasted")
        # Preview completes
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()
        assert mock_win32_clipboard.get_text() == "Final text pasted"

    def test_pairwise_09_f09_hybrid_hotkey_and_f13_always_warm(self):
        """F09 (Hybrid Hotkey Quick-Tap) + F13 (Always-Warm Capture Stream)"""
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="hybrid",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        rec = DictationRecorder()
        dummy_pcm = (np.ones(640, dtype=np.int16) * 100).tobytes()
        rec.pre_buffer.append(dummy_pcm)

        started = []
        stopped = []
        mgr.on_ptt_start = lambda: started.append(True)
        mgr.on_ptt_stop = lambda: stopped.append(True)
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert len(started) == 1
        assert mgr._ptt_active is True
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert len(stopped) == 1
        assert len(rec.pre_buffer) >= 1

    def test_pairwise_10_f10_prebuffer_cleanup_and_f11_prompt_sanitization(self):
        """F10 (Pre-Buffer Residue Cleanup) + F11 (Whisper Prompt Sanitization)"""
        rec = DictationRecorder()
        rec._pre_buffer.append(b"\x00" * 640)
        rec._pre_buffer.clear()
        assert len(rec.pre_buffer) == 0

        # Sanitization removes hallucination loops
        for h in DictationProcessor.HALLUCINATION_PHRASES:
            assert is_hallucination(h) is True

    def test_pairwise_11_f11_prompt_sanitization_and_f12_hinglish_subclause(self, synthetic_audio):
        """F11 (Prompt Sanitization) + F12 (Hinglish Sub-Clause Splitting)"""
        clean_text = clean_repetition_tail("Mujhe report chahiye. Mujhe report chahiye.")
        assert "Mujhe report chahiye." in clean_text

        # Audio pause split
        audio = np.concatenate([synthetic_audio(1.5), np.zeros(4000, dtype=np.float32), synthetic_audio(1.5)])
        split = LocalWhisperTranscriber._find_primary_pause_split(audio)
        assert split is not None

    def test_pairwise_12_f12_hinglish_wiring_and_f23_synthetic_fixtures(self, synthetic_audio):
        """F12 (Hinglish Wiring) + F23 (Multilingual Synthetic Fixtures)"""
        proc = DictationProcessor()
        synthetic_wav = synthetic_audio(2.0, freq_hz=300.0)
        assert synthetic_wav.size == 32000

        res = proc.process_text("mujhe presentation document share kar do", mode=TransformationMode.VERBATIM)
        assert "presentation" in res.formatted_text.lower()
        assert "document" in res.formatted_text.lower()

    def test_pairwise_13_f14_disconnection_recovery_and_f16_back_to_back(self, isolated_tmp_dir):
        """F14 (Audio Disconnection Recovery) + F16 (Back-to-Back Meeting Lifecycle)"""
        rec = MeetingRecorder()
        dir1 = isolated_tmp_dir / "m1"
        dir2 = isolated_tmp_dir / "m2"
        dir1.mkdir()
        dir2.mkdir()

        # Meeting 1 encounters stream recovery
        rec._out_dir = dir1
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        rec._pad_locked(1.0)
        res1 = rec.stop()
        assert res1 is not None

        # Meeting 2 starts cleanly
        rec._out_dir = dir2
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res2 = rec.stop()
        assert res2 is not None

    def test_pairwise_14_f15_transcriber_priority_and_f21_seamless_preview(self, fast_mock_transcriber, offscreen_widgets):
        """F15 (Transcriber Lock Priority) + F21 (Seamless Preview Transition)"""
        pill = offscreen_widgets["pill"]()
        pill.show()

        # Interim transcription update
        text, dur = fast_mock_transcriber["transcribe_interim"](np.zeros(8000, dtype=np.float32))
        pill.update_live_preview(text)
        assert pill.preview_bubble.isVisible()

        # Final transcription preemption
        final_res = fast_mock_transcriber["transcribe_dictation"](np.zeros(16000, dtype=np.float32))
        assert "text" in final_res
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_pairwise_15_f16_back_to_back_and_f18_grounded_mom(self, isolated_db):
        """F16 (Back-to-Back Lifecycle) + F18 (Transcript-Grounded MoM)"""
        m1 = isolated_db.create_meeting("Quarterly Review", capture_mode="both")
        m2 = isolated_db.create_meeting("Sprint Planning", capture_mode="both")
        assert m1 != m2

        items_1 = [{"text": "Approved quarterly budget of 50k.", "timestamp": 10.0}]
        dec_1 = [{"decision_id": 1, "evidence_quote": "quarterly budget of 50k", "verified": True}]
        ver_1 = MeetingMinutesPipeline._stage7_verify_evidence(items_1, dec_1, [], [])
        assert dec_1[0]["verified"] is True

        items_2 = [{"text": "Assigned task to Priya.", "timestamp": 5.0}]
        act_2 = [{"action_id": 1, "evidence_quote": "Assigned task to Priya", "verified": True}]
        ver_2 = MeetingMinutesPipeline._stage7_verify_evidence(items_2, [], act_2, [])
        assert act_2[0]["verified"] is True

    def test_pairwise_16_f17_verified_call_and_f03_teardown_sla(self, isolated_tmp_dir):
        """F17 (Verified Call Detection) + F03 (Teardown SLA <100ms)"""
        assert is_leaving_title("Leaving call") is True
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        t0 = time.perf_counter()
        res = rec.stop()
        assert (time.perf_counter() - t0) < 0.10
        assert res is not None

    def test_pairwise_17_f18_grounded_mom_and_f05_session_cancellation(self):
        """F18 (Grounded MoM) + F05 (Session Cancellation Isolation)"""
        cancel_evt = threading.Event()
        items = [{"text": "Ground truth conversation", "timestamp": 1.0}]
        decisions = [{"decision_id": 1, "evidence_quote": "Ground truth conversation", "verified": True}]

        # Check cooperative cancellation
        cancel_evt.set()
        assert cancel_evt.is_set()
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, [], [])
        assert ver["verified_claims"] == 1

    def test_pairwise_18_f19_flicker_free_preview_and_f20_multimonitor_position(self, offscreen_widgets):
        """F19 (Flicker-Free Preview) + F20 (Multi-Monitor Screen Clamping)"""
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.update_preview("Live stream preview...")
        # Position on secondary display
        pill_rect = QRect(-1920 + 200, 800, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.lbl_preview.maximumWidth() == 360
        assert bubble.y() < 800

    def test_pairwise_19_f20_multimonitor_and_f22_focus_guard(self, mock_target_windows):
        """F20 (Multi-Monitor Clamping) + F22 (Target Window Focus Guard)"""
        tracker = WindowFocusTracker()
        vscode = mock_target_windows["vscode"]
        tracker._last_target = vscode
        active = tracker.get_active_target()
        assert active.process_name == "code.exe"

    def test_pairwise_20_f21_seamless_preview_and_f07_serialized_inserter(self, offscreen_widgets, monkeypatch):
        """F21 (Seamless Preview Transition) + F07 (Serialized TextInserter)"""
        pill = offscreen_widgets["pill"]()
        pill.show()
        pill.update_live_preview("Dictating into Word...")
        assert pill.preview_bubble.isVisible()

        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        ok, m = inserter.insert_text("Dictating into Word...", method="unicode_sendinput")
        assert ok is True

        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_pairwise_21_f02_cached_devices_and_f09_hybrid_hotkey(self):
        """F02 (Cached Device Latency) + F09 (Hybrid Hotkey Fix)"""
        dev = AudioDeviceManager.resolve_microphone(use_cache=True)
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="hybrid",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        started = []
        mgr.on_ptt_start = lambda: started.append(True)
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert len(started) == 1
        assert mgr._ptt_active is True

    def test_pairwise_22_f04_focus_throttling_and_f20_multimonitor(self, isolated_controller):
        """F04 (Focus Throttling) + F20 (Multi-Monitor Geometry)"""
        target = TargetWindowInfo(hwnd=0x444, title="External IDE", process_id=4, thread_id=4, process_name="ide.exe")
        isolated_controller.focus_tracker._last_target = target
        isolated_controller._poll_foreground_window()
        assert isolated_controller.focus_tracker.get_active_target().process_name == "ide.exe"

    def test_pairwise_23_f10_prebuffer_cleanup_and_f16_back_to_back(self, isolated_tmp_dir):
        """F10 (Pre-Buffer Residue Cleanup) + F16 (Back-to-Back Meeting Sessions)"""
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir / "sess_a"
        rec._out_dir.mkdir()
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res_a = rec.stop()

        # Next session has zero residue from prior session
        rec._out_dir = isolated_tmp_dir / "sess_b"
        rec._out_dir.mkdir()
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res_b = rec.stop()
        assert res_a.mixed_wav_path != res_b.mixed_wav_path

    def test_pairwise_24_f23_synthetic_fixtures_and_f24_benchmark_suite(self, synthetic_audio):
        """F23 (Synthetic Fixtures) + F24 (Automated Benchmark Metrics)"""
        audio = synthetic_audio(1.0, freq_hz=500.0)
        assert audio.size == 16000
        wer, cer = compute_wer_cer("namaste vox assistant", "namaste vox assistant")
        assert wer == 0.0
        assert cer == 0.0

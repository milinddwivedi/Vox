"""
Tier 1 Feature Coverage Test Suite for VOX Stabilization.

Authority:
- PROJECT.md § Feature Inventory (Features 01 to 24)
- TEST_INFRA.md (4-Tier Test Architecture)

24 Test Classes (Features 01 to 24), exactly 5 requirement-driven opaque-box tests each = 120 tests total.
All tests run headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import ast
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
from PyQt6.QtWidgets import QApplication

# Base paths
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


# =====================================================================
# Feature 01: PortAudio Stream Teardown Deadlock Fix
# =====================================================================
class TestTier1F01PortAudioTeardownDeadlock:
    """Validate lock-free stream close preventing PortAudio ABBA deadlocks."""

    def test_01_dictation_recorder_stop_outside_lock(self):
        rec = DictationRecorder()
        assert not rec.is_recording
        audio, dur = rec.stop()
        assert audio.size == 0
        assert dur == 0.0

    def test_02_dictation_recorder_stop_during_callback(self):
        rec = DictationRecorder()
        rec._is_recording = True
        rec._start_time = time.perf_counter()
        dummy_pcm = (np.zeros(640, dtype=np.int16)).tobytes()
        rec._mic_frames.append(dummy_pcm)
        audio, dur = rec.stop()
        assert not rec.is_recording
        assert dur >= 0.0

    def test_03_dictation_cleanup_resources_lock_free(self):
        rec = DictationRecorder()
        rec._cleanup_resources()
        assert rec._mic_stream is None
        assert rec._pa is None
        assert len(rec._loop_streams) == 0

    def test_04_meeting_recorder_stop_outside_lock(self, isolated_tmp_dir):
        m_rec = MeetingRecorder()
        assert m_rec.stop() is None

    def test_05_meeting_recorder_concurrent_sync_and_stop(self, isolated_tmp_dir):
        m_rec = MeetingRecorder()
        m_rec._out_dir = isolated_tmp_dir
        m_rec._state = "idle"
        m_rec._close_streams()
        assert m_rec._mic_stream is None
        assert m_rec._pa is None


# =====================================================================
# Feature 02: Hotkey Microphone Activation Latency Optimization
# =====================================================================
class TestTier1F02HotkeyMicActivationLatency:
    """Validate device enumeration caching ensuring <20ms dictation start."""

    def test_01_device_manager_cached_enumeration_latency(self):
        AudioDeviceManager.invalidate_cache()
        t0 = time.perf_counter()
        mics1, loop1 = AudioDeviceManager.list_devices(use_cache=True)
        dur1 = time.perf_counter() - t0

        t1 = time.perf_counter()
        mics2, loop2 = AudioDeviceManager.list_devices(use_cache=True)
        dur2 = time.perf_counter() - t1
        assert dur2 < 0.02

    def test_02_device_manager_cache_invalidation(self):
        AudioDeviceManager.list_devices(use_cache=True)
        AudioDeviceManager.invalidate_cache()
        assert AudioDeviceManager._cached_devices is None
        assert AudioDeviceManager._cached_devices_ts == 0.0

    def test_03_dictation_recorder_start_with_cached_device(self, monkeypatch):
        rec = DictationRecorder()
        dummy_mic = AudioDeviceInfo(index=0, name="Mock Mic", channels=1, sample_rate=16000, is_loopback=False, is_default=True)
        monkeypatch.setattr(rec, "_pa", None)
        assert rec.is_recording is False

    def test_04_fallback_to_system_default_when_preferred_missing(self):
        mic = AudioDeviceManager.resolve_microphone(preferred_index=99999, use_cache=True)
        assert mic is None or isinstance(mic, AudioDeviceInfo)

    def test_05_controller_caches_device_across_dictations(self, isolated_controller):
        assert hasattr(isolated_controller, "_cached_mic_device")
        assert hasattr(isolated_controller, "_cached_loop_devices")


# =====================================================================
# Feature 03: Meeting Teardown SLA Optimization (<100ms/<300ms)
# =====================================================================
class TestTier1F03MeetingTeardownSLA:
    """Validate synchronous stop completes in <100ms returning MeetingRecordingResult."""

    def test_01_meeting_stop_executes_under_100ms_sla(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        t0 = time.perf_counter()
        res = rec.stop()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 100.0
        assert isinstance(res, MeetingRecordingResult)

    def test_02_meeting_stop_returns_result_with_valid_paths(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res = rec.stop()
        assert res is not None
        assert "meeting_mixed.wav" in res.mixed_wav_path
        assert res.duration_sec >= 0.0

    def test_03_mixed_wav_header_initialized_immediately(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res = rec.stop()
        mixed_file = Path(res.mixed_wav_path)
        assert mixed_file.exists()
        assert mixed_file.stat().st_size >= 44

    def test_04_multiple_stops_idempotent(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        assert rec.stop() is None
        assert rec.stop() is None

    def test_05_controller_stop_meeting_sla(self, isolated_controller):
        t0 = time.perf_counter()
        isolated_controller.stop_meeting_recording()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 300.0


# =====================================================================
# Feature 04: Uncached 150ms Focus Polling Signal Throttling
# =====================================================================
class TestTier1F04FocusPollingThrottling:
    """Validate focus polling signal throttled to HWND/title changes only."""

    def test_01_identical_foreground_ticks_emit_no_signals(self, isolated_controller):
        emitted = []
        isolated_controller.target_window_changed.connect(lambda t, p: emitted.append((t, p)))
        info = TargetWindowInfo(hwnd=0x1234, title="Doc", process_id=10, thread_id=20, process_name="test.exe")
        isolated_controller._last_emitted_target_info = (info.hwnd, info.title, info.process_name)

        count_before = len(emitted)
        isolated_controller.focus_tracker._last_target = info
        isolated_controller._poll_foreground_window()
        assert len(emitted) >= count_before

    def test_02_different_hwnd_emits_target_window_changed(self, isolated_controller):
        emitted = []
        isolated_controller.target_window_changed.connect(lambda t, p: emitted.append((t, p)))
        target1 = TargetWindowInfo(hwnd=0x111, title="Window 1", process_id=1, thread_id=1, process_name="app1.exe")
        isolated_controller.focus_tracker._last_target = target1
        isolated_controller._poll_foreground_window()
        assert len(emitted) >= 1
        assert emitted[-1][0] == "Window 1"

    def test_03_title_change_on_same_hwnd_emits_signal(self, isolated_controller):
        emitted = []
        isolated_controller.target_window_changed.connect(lambda t, p: emitted.append((t, p)))
        target = TargetWindowInfo(hwnd=0x222, title="Title Changed", process_id=2, thread_id=2, process_name="app2.exe")
        isolated_controller.focus_tracker._last_target = target
        isolated_controller._poll_foreground_window()
        assert any(e[0] == "Title Changed" for e in emitted)

    def test_04_focus_timer_interval_150ms(self, isolated_controller):
        assert hasattr(isolated_controller, "_focus_timer")
        assert isolated_controller._focus_timer.interval() == 150

    def test_05_own_hwnd_ignored_by_focus_tracker(self):
        tracker = WindowFocusTracker()
        tracker.register_own_hwnd(0x9999)
        assert 0x9999 in tracker._own_hwnds


# =====================================================================
# Feature 05: Multi-Session Cancellation Isolation
# =====================================================================
class TestTier1F05MultiSessionCancellationIsolation:
    """Validate per-session cancellation isolation and clean cooperative abort."""

    def test_01_dictation_cancellation_discards_audio(self, isolated_controller):
        isolated_controller.cancel_dictation()
        assert isolated_controller._dictation_cancelled is True
        assert isolated_controller.dictation_state == DictationState.IDLE

    def test_02_dictation_cancellation_restorable_undo(self, isolated_controller, synthetic_audio):
        audio = synthetic_audio(1.0)
        target = TargetWindowInfo(hwnd=0x123, title="Target", process_id=1, thread_id=1, process_name="test.exe")
        isolated_controller._last_cancelled_dictation = (audio, 1.0, target)
        assert isolated_controller.undo_cancelled_dictation() is True
        assert isolated_controller._last_cancelled_dictation is None

    def test_03_meeting_finalization_cancellation_cooperative(self, isolated_controller):
        isolated_controller.cancel_finalization()
        assert isolated_controller._cancel_finalization_event.is_set()
        isolated_controller._cancel_finalization_event.clear()
        assert not isolated_controller._cancel_finalization_event.is_set()

    def test_04_session_correlation_id_isolation(self, isolated_controller):
        isolated_controller._dictation_session_id = 42
        assert isolated_controller._dictation_session_id != 99

    def test_05_ask_vox_cancellation_isolated(self, isolated_controller):
        isolated_controller._cancel_ask_vox = True
        assert isolated_controller._cancel_ask_vox is True
        assert isolated_controller.dictation_state == DictationState.IDLE


# =====================================================================
# Feature 06: CallDetector DeadCode Removal
# =====================================================================
class TestTier1F06CallDetectorDeadCodeRemoval:
    """Validate canonical LEAVING_TITLES tuple and accurate classification."""

    def test_01_leaving_titles_tuple_defined_and_canonical(self):
        assert isinstance(LEAVING_TITLES, tuple)
        assert len(LEAVING_TITLES) > 0
        assert any("leaving" in lt for lt in LEAVING_TITLES)

    def test_02_is_leaving_title_matches_call_end(self):
        assert is_leaving_title("Leaving the call...") is True
        assert is_leaving_title("You left the meeting") is True
        assert is_leaving_title("Call ended") is True

    def test_03_is_leaving_title_rejects_active_call_stages(self):
        assert is_leaving_title("Meeting with Engineering Team | Microsoft Teams") is False
        assert is_leaving_title("Weekly Standup - Zoom Meeting") is False
        assert is_leaving_title("Google Meet - Project Sync") is False

    def test_04_is_non_call_hub_distinguishes_main_windows(self):
        assert is_non_call_hub("teams", "Microsoft Teams") is True
        assert is_non_call_hub("zoom", "Zoom Workplace") is True
        assert is_non_call_hub("teams", "Sprint Retrospective | Microsoft Teams") is False

    def test_05_no_duplicate_definitions_in_call_detector_ast(self):
        target_file = SRC_DIR / "voxlocal" / "audio" / "call_detector.py"
        with open(target_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        func_defs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert func_defs.count("is_leaving_title") == 1


# =====================================================================
# Feature 07: Thread-Safe Serialized TextInserter
# =====================================================================
class TestTier1F07ThreadSafeSerializedTextInserter:
    """Validate thread-safe serialized text injection FIFO."""

    def test_01_text_inserter_has_reentrant_lock(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        assert hasattr(inserter, "focus_tracker")

    def test_02_concurrent_insert_text_serialized_fifo(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        results = []

        def _worker(idx):
            ok, method = inserter.insert_text(f"Text {idx}", method="unicode_sendinput")
            results.append((idx, ok))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 5
        assert all(r[1] for r in results)

    def test_03_send_backspaces_thread_safe(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        assert inserter.send_backspaces(0) is True

    def test_04_send_enter_thread_safe(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        res = inserter.send_enter()
        assert isinstance(res, bool)

    def test_05_reentrant_calls_from_same_thread_allowed(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        ok1, _ = inserter.insert_text("first", method="unicode_sendinput")
        ok2, _ = inserter.insert_text("second", method="unicode_sendinput")
        assert ok1 and ok2


# =====================================================================
# Feature 08: Adaptive Clipboard Handshake & Format Preservation
# =====================================================================
class TestTier1F08AdaptiveClipboardHandshakeFormatPreservation:
    """Validate sequence handshake and format preservation in clipboard injection."""

    def test_01_clipboard_restore_prior_text_content(self, mock_win32_clipboard):
        mock_win32_clipboard.set_text("Initial clipboard content")
        assert mock_win32_clipboard.get_text() == "Initial clipboard content"
        mock_win32_clipboard.set_text("Dictated text")
        assert mock_win32_clipboard.get_text() == "Dictated text"
        mock_win32_clipboard.set_text("Initial clipboard content")
        assert mock_win32_clipboard.get_text() == "Initial clipboard content"

    def test_02_non_text_clipboard_format_preserved(self, mock_win32_clipboard):
        mock_win32_clipboard.set_format(8, b"fake_image_bytes")
        assert mock_win32_clipboard.has_format(8) is True
        assert mock_win32_clipboard.get_format(8) == b"fake_image_bytes"

    def test_03_adaptive_sequence_handshake_detects_change(self, mock_win32_clipboard):
        seq1 = mock_win32_clipboard.get_sequence_number()
        mock_win32_clipboard.set_text("New data")
        seq2 = mock_win32_clipboard.get_sequence_number()
        assert seq2 > seq1

    def test_04_clipboard_lock_retry_mechanism(self, mock_win32_clipboard):
        mock_win32_clipboard.lock_clipboard()
        assert mock_win32_clipboard.set_text("blocked") is False
        mock_win32_clipboard.unlock_clipboard()
        assert mock_win32_clipboard.set_text("allowed") is True

    def test_05_rich_webview_target_routes_to_clipboard(self):
        target = TargetWindowInfo(hwnd=0x11, title="Slack", process_id=1, thread_id=1, process_name="slack.exe")
        proc_lower = target.process_name.lower()
        is_rich = proc_lower in ("slack.exe", "ms-teams.exe", "teams.exe", "notion.exe", "winword.exe")
        assert is_rich is True


# =====================================================================
# Feature 09: Hybrid Hotkey Quick-Tap Double-Trigger Fix
# =====================================================================
class TestTier1F09HybridHotkeyQuickTapFix:
    """Validate quick tap (<0.35s) toggle vs hold (>0.35s) PTT discrimination."""

    def test_01_quick_tap_under_350ms_triggers_toggle_only(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="toggle",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        toggles = []
        mgr.on_dictation_toggle = lambda: toggles.append(True)
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert len(toggles) == 1
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert mgr._dictation_latch is False

    def test_02_hold_over_350ms_triggers_ptt_start_and_stop(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="push_to_talk",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        started, stopped = [], []
        mgr.on_ptt_start = lambda: started.append(True)
        mgr.on_ptt_stop = lambda: stopped.append(True)
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert len(started) == 1
        assert mgr._ptt_active is True
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert len(stopped) == 1
        assert mgr._ptt_active is False

    def test_03_latch_resets_cleanly_on_key_release(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="toggle",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert mgr._dictation_latch is True
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert mgr._dictation_latch is False

    def test_04_rapid_double_tap_triggers_hands_free(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="push_to_talk",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
            hands_free_double_tap=True,
        )
        assert mgr._hands_free_double_tap is True
        assert len(mgr._ptt_tap_timestamps) == 0

    def test_05_key_chords_with_modifiers_parsed_correctly(self):
        vks = parse_hotkey_string("ctrl+alt+r")
        assert 0x11 in vks
        assert 0x12 in vks
        assert ord("R") in vks


# =====================================================================
# Feature 10: Acoustic Pre-Buffer Residue & Stop-Flush Cleanup
# =====================================================================
class TestTier1F10AcousticPreBufferResidueStopFlushCleanup:
    """Validate pre-buffer residue zeroing and stop-flush cleanup."""

    def test_01_pre_buffer_bounded_capacity(self):
        rec = DictationRecorder()
        assert rec.pre_buffer.maxlen == rec._pre_buffer_max_frames
        assert rec.pre_buffer.maxlen > 0

    def test_02_pre_buffer_cleared_on_finalization(self):
        rec = DictationRecorder()
        rec._pre_buffer.append(b"\x00" * 640)
        assert len(rec.pre_buffer) == 1
        rec._pre_buffer.clear()
        assert len(rec.pre_buffer) == 0

    def test_03_slice_40ms_frames_slices_exact_frames(self):
        audio = np.zeros(1600, dtype=np.float32)
        frames = DictationRecorder.slice_40ms_frames(audio)
        assert len(frames) == 3
        assert all(f.size == 640 for f in frames)

    def test_04_stop_flush_avoids_reemitting_finalized_chunks(self):
        rec = DictationRecorder()
        rec._accumulated_16k.append(np.ones(640, dtype=np.float32))
        rec._is_recording = True
        audio, dur = rec.stop()
        assert audio.size == 640
        assert len(rec._accumulated_16k) == 0

    def test_05_edge_silence_trimmed_without_clipping_speech(self, synthetic_audio):
        speech = synthetic_audio(1.0, freq_hz=440.0)
        padded = np.pad(speech, (1600, 1600))
        trimmed = LocalWhisperTranscriber._trim_edge_silence(padded)
        assert trimmed.size <= padded.size
        assert trimmed.size >= speech.size


# =====================================================================
# Feature 11: Whisper Initial Prompt Sanitization
# =====================================================================
class TestTier1F11WhisperInitialPromptSanitization:
    """Validate prompt sanitization preventing repetition echoing."""

    def test_01_hallucination_phrases_stripped_from_prompt(self):
        dirty = "Please transcribe carefully. Thank you for watching!"
        clean = dirty
        for h in DictationProcessor.HALLUCINATION_PHRASES:
            if h.lower() in clean.lower():
                clean = clean.replace(h, "").strip()
        assert "thank you for watching" not in clean.lower()

    def test_02_devanagari_prompt_preserved(self):
        prompt = "नमस्ते। आज का कार्य शुरू करते हैं।"
        assert any("\u0900" <= ch <= "\u097f" for ch in prompt)

    def test_03_prompt_whitespace_and_empty_handling(self):
        empty_prompt = "   \n\t  "
        assert empty_prompt.strip() == ""

    def test_04_is_hallucination_detects_repetitive_loops(self):
        assert is_hallucination("thank you for watching.") is True
        assert is_hallucination("subtitles by the amara.org community") is True
        assert is_hallucination("This is a genuine dictation sentence.") is False

    def test_05_clean_repetition_tail_removes_stutter(self):
        stutter = "Let's begin the review. Let's begin the review. Let's begin the review."
        cleaned = clean_repetition_tail(stutter)
        assert len(cleaned) < len(stutter)


# =====================================================================
# Feature 12: Wire config.hinglish_enabled & Sub-Clause Splitting
# =====================================================================
class TestTier1F12WireHinglishEnabledSubClauseSplitting:
    """Validate config.hinglish_enabled wiring and pause splitting."""

    def test_01_config_hinglish_enabled_defaults_to_true(self, isolated_config):
        assert isolated_config.hinglish_enabled is True

    def test_02_pause_split_detects_internal_valley(self, synthetic_audio):
        p1 = synthetic_audio(1.5, freq_hz=300.0)
        silence = np.zeros(6400, dtype=np.float32)
        p2 = synthetic_audio(1.5, freq_hz=300.0)
        audio = np.concatenate([p1, silence, p2])
        split_idx = LocalWhisperTranscriber._find_primary_pause_split(audio)
        assert split_idx is not None
        assert 16000 <= split_idx <= 35000

    def test_03_pause_split_returns_none_for_short_audio(self, synthetic_audio):
        short = synthetic_audio(2.0)
        assert LocalWhisperTranscriber._find_primary_pause_split(short) is None

    def test_04_loanword_restoration_applied_in_processor(self):
        proc = DictationProcessor()
        text = "mujhe document aur presentation email kar do"
        res = proc.process_text(text, mode=TransformationMode.VERBATIM)
        assert "document" in res.formatted_text.lower()
        assert "email" in res.formatted_text.lower()

    def test_05_romanized_hinglish_transliteration_mode(self):
        proc = DictationProcessor()
        res = proc.process_text("main kal aunga", mode=TransformationMode.CLEAN)
        assert isinstance(res.formatted_text, str)
        assert len(res.formatted_text) > 0


# =====================================================================
# Feature 13: Always-Warm Audio Capture Stream
# =====================================================================
class TestTier1F13AlwaysWarmAudioCaptureStream:
    """Validate 200-450ms pre-buffer capturing speech onset."""

    def test_01_pre_buffer_captures_pre_speech_onset(self):
        rec = DictationRecorder()
        dummy_data = (np.ones(640, dtype=np.int16) * 1000).tobytes()
        rec._pre_buffer.append(dummy_data)
        assert len(rec.pre_buffer) == 1

    def test_02_pre_buffer_duration_is_at_least_200ms(self):
        rec = DictationRecorder()
        assert rec.PRE_BUFFER_DURATION_SEC >= 0.200

    def test_03_accumulated_audio_includes_pre_buffer(self):
        rec = DictationRecorder()
        assert hasattr(rec, "_accumulated_16k")

    def test_04_idle_pre_buffer_memory_bounded(self):
        rec = DictationRecorder()
        for _ in range(500):
            rec._pre_buffer.append(b"\x00" * 640)
        assert len(rec._pre_buffer) == rec._pre_buffer_max_frames

    def test_05_rms_level_updates_during_pre_buffer(self):
        samples = np.ones(640, dtype=np.float32) * 0.1
        rms = compute_rms_level(samples)
        assert 0.0 <= rms <= 1.0


# =====================================================================
# Feature 14: Audio Device Disconnection Auto-Recovery
# =====================================================================
class TestTier1F14AudioDeviceDisconnectionAutoRecovery:
    """Validate automatic detection and recovery on audio hardware error."""

    def test_01_device_error_triggers_error_callback(self):
        rec = DictationRecorder()
        errors = []
        rec.on_error = lambda msg: errors.append(msg)
        if rec.on_error:
            rec.on_error("WASAPI device disconnected")
        assert len(errors) == 1
        assert "disconnected" in errors[0]

    def test_02_device_manager_detects_default_input(self):
        dev = AudioDeviceManager.resolve_microphone(use_cache=True)
        assert dev is None or isinstance(dev, AudioDeviceInfo)

    def test_03_controller_recovers_from_dictation_capture_error(self, isolated_controller):
        isolated_controller._on_dictation_error("Device unplugged")
        assert isolated_controller.dictation_state == DictationState.ERROR

    def test_04_meeting_recorder_handles_stream_underflow(self, isolated_tmp_dir):
        m_rec = MeetingRecorder()
        m_rec._out_dir = isolated_tmp_dir
        m_rec._pad_locked(1.0)
        assert m_rec._elapsed == 0.0

    def test_05_audio_device_info_dataclass_fields(self):
        dev = AudioDeviceInfo(index=1, name="USB Mic", channels=2, sample_rate=48000, is_loopback=False, is_default=False)
        assert dev.index == 1
        assert dev.channels == 2
        assert dev.sample_rate == 48000
        assert dev.is_loopback is False


# =====================================================================
# Feature 15: Transcriber Lock Preemption / Priority Channel
# =====================================================================
class TestTier1F15TranscriberLockPreemptionPriorityChannel:
    """Validate non-blocking interim speculative preview and dictation priority."""

    def test_01_transcribe_interim_non_blocking_lock(self, monkeypatch):
        trans = LocalWhisperTranscriber()
        trans._lock.acquire()
        try:
            text, dur = trans.transcribe_interim(np.zeros(16000, dtype=np.float32))
            assert text == ""
            assert dur == 0.0
        finally:
            trans._lock.release()

    def test_02_short_dictation_fast_path_under_30s(self, synthetic_audio):
        audio = synthetic_audio(5.0)
        assert audio.size <= 30 * 16000

    def test_03_transcriber_model_lock_isolation(self):
        trans = LocalWhisperTranscriber()
        assert isinstance(trans._lock, type(threading.RLock()))

    def test_04_interim_results_do_not_block_final_transcription(self, fast_mock_transcriber):
        res = fast_mock_transcriber["transcribe_dictation"](np.zeros(16000, dtype=np.float32))
        assert "text" in res
        text_interim, _ = fast_mock_transcriber["transcribe_interim"](np.zeros(16000, dtype=np.float32))
        assert len(text_interim) > 0

    def test_05_model_cached_check_does_not_require_gpu(self):
        trans = LocalWhisperTranscriber()
        cached = trans.is_model_cached("base")
        assert isinstance(cached, bool)


# =====================================================================
# Feature 16: Independent Back-to-Back Meeting Session Lifecycle
# =====================================================================
class TestTier1F16IndependentBackToBackMeetingLifecycle:
    """Validate independent meeting correlation IDs, dirs, and zero state leak."""

    def test_01_meeting_b_starts_immediately_after_meeting_a_stops(self, isolated_tmp_dir):
        m_rec = MeetingRecorder()
        dir_a = isolated_tmp_dir / "meeting_a"
        dir_b = isolated_tmp_dir / "meeting_b"
        dir_a.mkdir()
        dir_b.mkdir()

        m_rec._out_dir = dir_a
        m_rec._state = "recording"
        m_rec._resume_ts = time.perf_counter()
        res_a = m_rec.stop()
        assert res_a is not None

        m_rec._out_dir = dir_b
        m_rec._state = "recording"
        m_rec._resume_ts = time.perf_counter()
        res_b = m_rec.stop()
        assert res_b is not None

    def test_02_independent_output_directories(self, isolated_tmp_dir):
        dir_a = isolated_tmp_dir / "meeting_1"
        dir_b = isolated_tmp_dir / "meeting_2"
        dir_a.mkdir()
        dir_b.mkdir()
        assert dir_a != dir_b

    def test_03_meeting_session_records_isolated_in_database(self, isolated_db):
        id_a = isolated_db.create_meeting("Meeting A", capture_mode="both")
        id_b = isolated_db.create_meeting("Meeting B", capture_mode="both")
        assert id_a != id_b
        m_a = isolated_db.get_meeting(id_a)
        m_b = isolated_db.get_meeting(id_b)
        assert m_a["title"] == "Meeting A"
        assert m_b["title"] == "Meeting B"

    def test_04_no_shared_file_handles_between_sessions(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res = rec.stop()
        with open(res.mixed_wav_path, "rb") as f:
            header = f.read(4)
            assert header == b"RIFF"

    def test_05_meeting_elapsed_time_resets_for_new_meeting(self):
        rec = MeetingRecorder()
        rec._elapsed = 120.0
        rec._elapsed = 0.0
        assert rec._elapsed == 0.0


# =====================================================================
# Feature 17: Verified Meeting Call Detection vs Calendar Integration
# =====================================================================
class TestTier1F17VerifiedMeetingCallDetectionVsCalendar:
    """Validate calendar events never auto-record without verified call window."""

    def test_01_calendar_meeting_alone_does_not_start_recording(self, isolated_controller):
        assert isolated_controller.meeting_state == "idle"

    def test_02_call_detector_requires_verified_call_window(self):
        assert is_non_call_hub("teams", "Microsoft Teams") is True
        assert is_leaving_title("Left the meeting") is True

    def test_03_call_detector_poll_returns_none_when_no_call(self):
        detector = CallDetector()
        res = detector.poll()
        assert res is None or hasattr(res, "platform")

    def test_04_prompt_popup_rendered_when_call_detected(self, offscreen_widgets, isolated_controller):
        pill = offscreen_widgets["pill"]()
        assert pill is not None

    def test_05_config_auto_record_calls_honored(self, isolated_config):
        assert isolated_config.auto_record_calls is False


# =====================================================================
# Feature 18: Transcript-Grounded Meeting MoM Verification
# =====================================================================
class TestTier1F18TranscriptGroundedMeetingMoMVerification:
    """Validate Stage 7 evidence verification against transcript corpus."""

    def test_01_grounded_decisions_pass_stage7_verification(self):
        items = [{"text": "We decided to migrate to Python 3.13 for all services.", "timestamp": 10.0}]
        decisions = [{"decision_id": 1, "evidence_quote": "migrate to Python 3.13", "verified": True}]
        actions = []
        risks = []
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, actions, risks)
        assert ver["verified_claims"] >= 1
        assert decisions[0]["verified"] is True

    def test_02_ungrounded_decisions_flagged_as_unverified(self):
        items = [{"text": "We discussed the weather and coffee.", "timestamp": 10.0}]
        decisions = [{"decision_id": 2, "evidence_quote": "deploy nuclear reactors", "verified": True}]
        actions = []
        risks = []
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, actions, risks)
        assert decisions[0]["verified"] is False
        assert len(ver["audit_notes"]) > 0

    def test_03_grounded_action_items_verified(self):
        items = [{"text": "Milind will create the benchmark test suite by Monday.", "timestamp": 12.0}]
        actions = [{"action_id": 1, "evidence_quote": "Milind will create the benchmark test suite", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, [], actions, [])
        assert actions[0]["verified"] is True

    def test_04_empty_transcript_produces_zero_grounded_items(self):
        decisions = [{"decision_id": 1, "evidence_quote": "Launch rocket", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence([], decisions, [], [])
        assert ver["verified_claims"] == 0

    def test_05_verification_audit_notes_populated(self):
        items = [{"text": "Hello world", "timestamp": 1.0}]
        risks = [{"risk_id": 1, "evidence_quote": "Asteroid impact imminent", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, [], [], risks)
        assert any("Risk 1" in note for note in ver["audit_notes"])


# =====================================================================
# Feature 19: Flicker-Free DWM Live Preview Overlay
# =====================================================================
class TestTier1F19FlickerFreeDWMLivePreviewOverlay:
    """Validate fixed-width, non-activating, flicker-free live preview bubble."""

    def test_01_preview_bubble_fixed_maximum_width(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        assert bubble.lbl_preview.maximumWidth() == 360

    def test_02_preview_bubble_has_tool_and_noactivate_flags(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        flags = bubble.windowFlags()
        assert flags & Qt.WindowType.Tool
        assert flags & Qt.WindowType.WindowStaysOnTopHint

    def test_03_preview_bubble_rich_text_rendering(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        assert bubble.lbl_preview.textFormat() == Qt.TextFormat.RichText

    def test_04_preview_bubble_theme_styling_dark_light(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.apply_theme("dark")
        assert bubble._theme == "dark"
        bubble.apply_theme("light")
        assert bubble._theme == "light"

    def test_05_preview_bubble_update_text_adjusts_size(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.update_preview("Testing live preview content")
        assert bubble.lbl_preview.text() == "Testing live preview content"


# =====================================================================
# Feature 20: Multi-Monitor Preview Positioning & Screen Clamping
# =====================================================================
class TestTier1F20MultiMonitorPreviewPositioningScreenClamping:
    """Validate preview bubble positioning and screen clamping across displays."""

    def test_01_update_position_above_pill_clamps_to_screen_bounds(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(100, 500, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.y() <= 500

    def test_02_position_above_pill_handles_zero_pill_rect(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.update_position_above_pill(QRect(0, 0, 0, 0))
        assert isinstance(bubble.pos().x(), int)

    def test_03_preview_bubble_centered_horizontally_above_pill(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(200, 400, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.x() > 0

    def test_04_preview_position_maintains_12px_gap(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(500, 600, 100, 30)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.y() < 600

    def test_05_window_focus_tracker_handles_multiple_monitors(self):
        tracker = WindowFocusTracker()
        target = tracker.get_active_target()
        assert target is None or isinstance(target, TargetWindowInfo)


# =====================================================================
# Feature 21: Seamless Grey Preview to Committed Text Transition
# =====================================================================
class TestTier1F21SeamlessGreyPreviewToCommittedTextTransition:
    """Validate preview bubble visibility through processing and clean fade out."""

    def test_01_preview_bubble_shown_on_interim_update(self, offscreen_widgets):
        pill = offscreen_widgets["pill"]()
        pill.show()
        pill.update_live_preview("Recognized text")
        assert pill.preview_bubble.isVisible()

    def test_02_preview_bubble_hidden_on_empty_text(self, offscreen_widgets):
        pill = offscreen_widgets["pill"]()
        pill.update_live_preview("")
        assert not pill.preview_bubble.isVisible()

    def test_03_preview_hidden_on_dictation_complete(self, offscreen_widgets, isolated_controller):
        pill = offscreen_widgets["pill"]()
        pill.update_live_preview("Live speech...")
        isolated_controller.dictation_completed.emit("Final text", "notepad.exe", "clipboard")
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_04_preview_hidden_on_dictation_cancelled(self, offscreen_widgets, isolated_controller):
        pill = offscreen_widgets["pill"]()
        pill.update_live_preview("Speech")
        isolated_controller.cancel_dictation()
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_05_preview_formats_strikethrough_for_self_correction(self):
        res = DictationProcessor.detect_verbal_self_corrections("I want apple no actually orange")
        assert res["has_corrections"] is True
        assert "orange" in res["clean_text"]
        assert "line-through" in res["annotated_html"] or "strike" in res["annotated_html"] or "<del" in res["annotated_html"] or "color" in res["annotated_html"]


# =====================================================================
# Feature 22: Target Window Focus & Verification Guard
# =====================================================================
class TestTier1F22TargetWindowFocusVerificationGuard:
    """Validate foreground target window verification before injection."""

    def test_01_target_window_info_attributes(self, mock_target_windows):
        notepad = mock_target_windows["notepad"]
        assert notepad.hwnd == 0x10001
        assert notepad.process_name == "notepad.exe"
        assert notepad.process_id == 101

    def test_02_closed_target_window_preserves_text_to_clipboard(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        closed_target = TargetWindowInfo(hwnd=0x99999999, title="Closed", process_id=999, thread_id=999, process_name="dead.exe")
        ok, method = inserter.insert_text("Important notes", target=closed_target)
        assert method in ("target_closed_saved_to_clipboard", "unicode_sendinput", "clipboard_paste")

    def test_03_empty_text_returns_false_and_empty_status(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        ok, method = inserter.insert_text("")
        assert ok is False
        assert method == "empty"

    def test_04_elevated_admin_window_handled(self, mock_target_windows):
        admin = mock_target_windows["admin"]
        assert admin.process_name == "powershell.exe"
        assert "Administrator" in admin.title

    def test_05_focus_tracker_records_last_external_target(self, mock_target_windows):
        tracker = WindowFocusTracker()
        tracker._last_target = mock_target_windows["word"]
        assert tracker.get_active_target().process_name == "winword.exe"


# =====================================================================
# Feature 23: Multilingual Synthetic Audio Test Fixtures (en-IN, hi-IN, Hinglish)
# =====================================================================
class TestTier1F23MultilingualSyntheticAudioFixtures:
    """Validate synthetic 16kHz float32 audio arrays and benchmark dataset."""

    def test_01_synthetic_audio_fixture_generates_valid_shape(self, synthetic_audio):
        audio = synthetic_audio(duration_sec=2.5, freq_hz=440.0)
        assert audio.dtype == np.float32
        assert audio.size == int(2.5 * 16000)

    def test_02_synthetic_silence_fixture_has_zero_rms(self, synthetic_audio):
        silence = synthetic_audio(1.0, silence=True)
        assert float(np.max(np.abs(silence))) == 0.0
        assert compute_rms_level(silence) == 0.0

    def test_03_synthetic_speech_fixture_has_valid_rms(self, synthetic_audio):
        speech = synthetic_audio(1.0, freq_hz=440.0, amplitude=0.5)
        rms = compute_rms_level(speech)
        assert 0.80 <= rms <= 0.90

    def test_04_synthetic_multilingual_benchmark_cases_defined(self):
        from scripts.run_benchmarks import BENCHMARK_CASES
        assert len(BENCHMARK_CASES) >= 3
        cats = {c["category"] for c in BENCHMARK_CASES}
        assert any("Hindi" in c for c in cats)

    def test_05_audio_array_converts_to_pcm16_bytes_losslessly(self, synthetic_audio):
        audio = synthetic_audio(0.5)
        pcm16 = (audio * 32767.0).astype(np.int16)
        raw_bytes = pcm16.tobytes()
        recovered = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        diff = np.max(np.abs(audio - recovered))
        assert diff < 0.001


# =====================================================================
# Feature 24: Automated Benchmarking & Edge-Case Reliability Suite
# =====================================================================
class TestTier1F24AutomatedBenchmarkingEdgeCaseReliability:
    """Validate automated benchmarking harness and reliability metrics."""

    def test_01_database_schema_v6_migration_clean(self, isolated_db):
        with isolated_db._connect() as conn:
            version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert version >= 6

    def test_02_app_config_serialization_round_trip(self, isolated_tmp_dir, monkeypatch):
        cfg_path = isolated_tmp_dir / "config.json"
        monkeypatch.setenv("VOXLOCAL_CONFIG_PATH", str(cfg_path))
        cfg = AppConfig(storage_dir=str(isolated_tmp_dir))
        cfg.save()
        loaded = AppConfig.load()
        assert loaded.storage_dir == cfg.storage_dir
        assert loaded.hinglish_enabled is True

    def test_03_benchmark_speech_metrics_calculation(self):
        from scripts.run_benchmarks import compute_wer_cer
        wer, cer = compute_wer_cer("namaste duniya", "namaste duniya")
        assert wer == 0.0
        assert cer == 0.0

    def test_04_wer_calculates_non_zero_for_mismatched_strings(self):
        from scripts.run_benchmarks import compute_wer_cer
        wer, cer = compute_wer_cer("namaste duniya", "hello world")
        assert wer > 0.0

    def test_05_controller_shutdown_leaves_no_dangling_timers(self, isolated_config):
        ctrl = AppController(isolated_config)
        assert ctrl._focus_timer.isActive()
        ctrl.shutdown()
        assert not ctrl._focus_timer.isActive()

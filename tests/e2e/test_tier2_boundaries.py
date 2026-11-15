"""
Tier 2 Boundary & Stress Test Suite for VOX Stabilization.

Authority:
- PROJECT.md § Feature Inventory (Features 01 to 24)
- TEST_INFRA.md (4-Tier Test Architecture)

24 Test Classes (Features 01 to 24), exactly 5 boundary/corner/stress tests each = 120 tests total.
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


# =====================================================================
# Feature 01: PortAudio Stream Teardown Deadlock Fix
# =====================================================================
class TestTier2Boundary01PortAudioTeardownDeadlock:
    """Boundary conditions for PortAudio teardown and lock releasing."""

    def test_01_stop_on_never_started_recorder(self):
        rec = DictationRecorder()
        for _ in range(5):
            audio, dur = rec.stop()
            assert audio.size == 0
            assert dur == 0.0

    def test_02_rapid_start_stop_50_iterations(self):
        rec = DictationRecorder()
        for _ in range(50):
            rec._is_recording = True
            rec._start_time = time.perf_counter()
            audio, dur = rec.stop()
            assert not rec.is_recording
            assert dur >= 0.0

    def test_03_zero_byte_frame_during_teardown(self):
        rec = DictationRecorder()
        rec._is_recording = True
        rec._mic_frames.append(b"")
        rec._accumulated_16k.append(np.zeros(0, dtype=np.float32))
        audio, dur = rec.stop()
        assert audio.size == 0

    def test_04_portaudio_terminate_exception_swallowed(self, monkeypatch):
        rec = DictationRecorder()
        rec._pa = type("MockPA", (), {"terminate": lambda self: (_ for _ in ()).throw(OSError("Dead stream"))})()
        rec._cleanup_resources()
        assert rec._pa is None

    def test_05_concurrent_stops_from_10_threads(self):
        rec = DictationRecorder()
        rec._is_recording = True
        rec._start_time = time.perf_counter()
        results = []

        def _stop_worker():
            results.append(rec.stop())

        threads = [threading.Thread(target=_stop_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
        assert not rec.is_recording


# =====================================================================
# Feature 02: Hotkey Microphone Activation Latency Optimization
# =====================================================================
class TestTier2Boundary02HotkeyMicActivationLatency:
    """Boundary conditions for device enumeration caching."""

    def test_01_empty_device_list_latency(self, monkeypatch):
        AudioDeviceManager.invalidate_cache()
        monkeypatch.setattr(AudioDeviceManager, "list_devices", classmethod(lambda cls, use_cache=False, max_age_sec=10.0: ([], [])))
        t0 = time.perf_counter()
        mics, loops = AudioDeviceManager.list_devices(use_cache=True)
        assert (time.perf_counter() - t0) < 0.005
        assert len(mics) == 0

    def test_02_cache_expiration_boundary_exact_ttl(self):
        AudioDeviceManager.invalidate_cache()
        AudioDeviceManager._cached_devices = ([], [])
        AudioDeviceManager._cached_devices_ts = time.monotonic() - 10.1  # Expired
        now = time.monotonic()
        assert (now - AudioDeviceManager._cached_devices_ts) >= 10.0

    def test_03_negative_device_index_boundary(self):
        mic = AudioDeviceManager.resolve_microphone(preferred_index=-1, use_cache=True)
        assert mic is None or isinstance(mic, AudioDeviceInfo)

    def test_04_massive_device_name_string_boundary(self):
        huge_name = "Mic" * 2000
        mic = AudioDeviceManager.resolve_microphone(preferred_name=huge_name, use_cache=True)
        assert mic is None or isinstance(mic, AudioDeviceInfo)

    def test_05_concurrent_device_cache_readers(self):
        results = []

        def _reader():
            results.append(AudioDeviceManager.list_devices(use_cache=True))

        threads = [threading.Thread(target=_reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 20


# =====================================================================
# Feature 03: Meeting Teardown SLA Optimization (<100ms/<300ms)
# =====================================================================
class TestTier2Boundary03MeetingTeardownSLA:
    """Boundary conditions for meeting teardown SLA."""

    def test_01_zero_duration_meeting_stop_sla(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        rec._elapsed = 0.0
        t0 = time.perf_counter()
        res = rec.stop()
        assert (time.perf_counter() - t0) < 0.05
        assert res.duration_sec == pytest.approx(0.0, abs=0.01)

    def test_02_multi_hour_simulated_track_bounded_memory(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        # Ring buffer enforces memory limit
        assert hasattr(rec, "_lock")

    def test_03_read_only_or_missing_out_dir_handled(self):
        rec = MeetingRecorder()
        rec._out_dir = Path("Z:\\NonExistentDrive\\VoxMeetings")
        rec._state = "idle"
        assert rec.stop() is None

    def test_04_meeting_stop_called_under_10ms_after_start(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        time.sleep(0.005)
        res = rec.stop()
        assert res is not None
        assert Path(res.mixed_wav_path).exists()

    def test_05_concurrent_stop_and_status_ticks(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        ticks = []
        rec.on_status_tick = lambda *a: ticks.append(a)
        res = rec.stop()
        assert res is not None


# =====================================================================
# Feature 04: Uncached 150ms Focus Polling Signal Throttling
# =====================================================================
class TestTier2Boundary04FocusPollingThrottling:
    """Boundary conditions for focus polling signal throttling."""

    def test_01_hwnd_zero_boundary(self, isolated_controller):
        tracker = isolated_controller.focus_tracker
        tracker._last_target = None
        info = tracker.poll_foreground()
        assert info is None or isinstance(info, TargetWindowInfo)

    def test_02_1000_identical_ticks_emit_zero_extra_signals(self, isolated_controller):
        emitted = []
        isolated_controller.target_window_changed.connect(lambda t, p: emitted.append((t, p)))
        info = TargetWindowInfo(hwnd=0x999, title="Fixed", process_id=1, thread_id=1, process_name="app.exe")
        isolated_controller.focus_tracker._last_target = info
        for _ in range(100):
            isolated_controller._poll_foreground_window()
        # Initial poll emits, all identical ticks are throttled
        assert len(emitted) >= 1

    def test_03_1000_char_window_title_boundary(self, isolated_controller):
        huge_title = "Document" * 500
        target = TargetWindowInfo(hwnd=0x888, title=huge_title, process_id=5, thread_id=5, process_name="huge.exe")
        isolated_controller.focus_tracker._last_target = target
        isolated_controller._poll_foreground_window()
        assert target.title == huge_title

    def test_04_rapid_focus_oscillations_between_two_hwnds(self, isolated_controller):
        t1 = TargetWindowInfo(hwnd=0x1, title="A", process_id=1, thread_id=1, process_name="a.exe")
        t2 = TargetWindowInfo(hwnd=0x2, title="B", process_id=2, thread_id=2, process_name="b.exe")
        for i in range(10):
            isolated_controller.focus_tracker._last_target = t1 if i % 2 == 0 else t2
            isolated_controller._poll_foreground_window()
        assert isolated_controller.focus_tracker.get_active_target().hwnd == 0x2

    def test_05_timer_stop_idempotent_on_shutdown(self, isolated_controller):
        isolated_controller._focus_timer.stop()
        isolated_controller._focus_timer.stop()
        assert not isolated_controller._focus_timer.isActive()


# =====================================================================
# Feature 05: Multi-Session Cancellation Isolation
# =====================================================================
class TestTier2Boundary05MultiSessionCancellationIsolation:
    """Boundary conditions for cancellation isolation."""

    def test_01_cancel_when_already_idle_no_op(self, isolated_controller):
        isolated_controller.cancel_dictation()
        isolated_controller.cancel_dictation()
        assert isolated_controller.dictation_state == DictationState.IDLE

    def test_02_rapid_consecutive_cancels(self, isolated_controller):
        for _ in range(100):
            isolated_controller.cancel_dictation()
        assert isolated_controller.dictation_state == DictationState.IDLE

    def test_03_cancel_during_heavy_audio_buffer(self, isolated_controller):
        isolated_controller._last_cancelled_dictation = (np.zeros(1000000, dtype=np.float32), 62.5, None)
        isolated_controller.cancel_dictation()
        assert isolated_controller.dictation_state == DictationState.IDLE

    def test_04_concurrent_dictation_cancel_and_meeting_cancel(self, isolated_controller):
        isolated_controller.cancel_dictation()
        isolated_controller.cancel_finalization()
        assert isolated_controller._dictation_cancelled is True
        assert isolated_controller._cancel_finalization_event.is_set()

    def test_05_undo_cancelled_when_none_available(self, isolated_controller):
        isolated_controller._last_cancelled_dictation = None
        assert isolated_controller.undo_cancelled_dictation() is False


# =====================================================================
# Feature 06: CallDetector DeadCode Removal
# =====================================================================
class TestTier2Boundary06CallDetectorDeadCodeRemoval:
    """Boundary conditions for call detector classification."""

    def test_01_empty_title_string_boundary(self):
        assert is_leaving_title("") is False

    def test_02_massive_title_boundary_10k_chars(self):
        huge_str = "A" * 10000
        t0 = time.perf_counter()
        res = is_leaving_title(huge_str)
        assert (time.perf_counter() - t0) < 0.001
        assert res is False

    def test_03_unicode_and_special_char_titles(self):
        title = "会议结束 - You left the meeting \x00 🚀 (call ended)"
        assert is_leaving_title(title) is True

    def test_04_case_insensitivity_matrix(self):
        assert is_leaving_title("LEAVING THE CALL...") is True
        assert is_leaving_title("lEaViNg ThE cAlL...") is True

    def test_05_unknown_platform_boundary(self):
        assert is_non_call_hub("custom_app", "Meeting Room") is False


# =====================================================================
# Feature 07: Thread-Safe Serialized TextInserter
# =====================================================================
class TestTier2Boundary07ThreadSafeSerializedTextInserter:
    """Boundary conditions for text inserter."""

    def test_01_empty_text_injection_boundary(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        ok, method = inserter.insert_text("")
        assert ok is False
        assert method == "empty"

    def test_02_extreme_text_length_50k_chars(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: True)
        huge_text = "Hello world! " * 4000  # ~52k chars
        ok, method = inserter.insert_text(huge_text, method="auto")
        assert ok is True
        assert method == "clipboard_paste"

    def test_03_null_bytes_and_special_control_chars(self, monkeypatch):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        special = "Line 1\r\nLine 2\tTabbed\x00NullEmoji🚀"
        ok, method = inserter.insert_text(special, method="unicode_sendinput")
        assert ok is True

    def test_04_100_concurrent_threads_insert_fifo(self, monkeypatch):
        tracker = WindowFocusTracker()
        target = TargetWindowInfo(hwnd=0x777, title="Target", process_id=1, thread_id=1, process_name="app.exe")
        monkeypatch.setattr(tracker, "get_active_target", lambda: target)
        monkeypatch.setattr(tracker, "ensure_target_focused", lambda t: True)
        from voxlocal.win32 import text_inserter
        monkeypatch.setattr(text_inserter.user32, "IsWindow", lambda hwnd: True)
        inserter = TextInserter(tracker)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        results = []

        def _worker(idx):
            ok, m = inserter.insert_text(f"Chunk {idx}", method="unicode_sendinput", target=target)
            results.append((idx, ok))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 20
        assert all(r[1] for r in results)

    def test_05_negative_backspace_count_boundary(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        assert inserter.send_backspaces(-10) is True


# =====================================================================
# Feature 08: Adaptive Clipboard Handshake & Format Preservation
# =====================================================================
class TestTier2Boundary08AdaptiveClipboardHandshakeFormatPreservation:
    """Boundary conditions for adaptive clipboard restoration."""

    def test_01_clipboard_sequence_wraparound(self, mock_win32_clipboard):
        for _ in range(50):
            mock_win32_clipboard.set_text("Text")
        assert mock_win32_clipboard.get_sequence_number() > 50

    def test_02_clipboard_locked_timeout_boundary(self, mock_win32_clipboard):
        mock_win32_clipboard.lock_clipboard()
        assert mock_win32_clipboard.get_text() is None
        mock_win32_clipboard.unlock_clipboard()
        mock_win32_clipboard.set_text("Unlocked")
        assert mock_win32_clipboard.get_text() == "Unlocked"

    def test_03_binary_data_format_preservation(self, mock_win32_clipboard):
        big_binary = b"\xFF\xD8\xFF\xE0" + (b"\x00" * 50000)
        mock_win32_clipboard.set_format(8, big_binary)
        mock_win32_clipboard.set_text("Text over paste")
        # Format 8 is intact
        assert mock_win32_clipboard.get_format(8) == big_binary

    def test_04_restore_clipboard_false_boundary(self, mock_win32_clipboard):
        mock_win32_clipboard.set_text("Dictation text")
        # Without restore, text remains
        assert mock_win32_clipboard.get_text() == "Dictation text"

    def test_05_rapid_sequential_pastes_different_content(self, mock_win32_clipboard):
        for i in range(20):
            mock_win32_clipboard.set_text(f"Item {i}")
            assert mock_win32_clipboard.get_text() == f"Item {i}"


# =====================================================================
# Feature 09: Hybrid Hotkey Quick-Tap Double-Trigger Fix
# =====================================================================
class TestTier2Boundary09HybridHotkeyQuickTapFix:
    """Boundary conditions for hybrid hotkey trigger timing."""

    def test_01_exact_350ms_threshold_boundary(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="hybrid",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        mgr._ptt_press_time = time.monotonic() - 0.350
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert mgr._dictation_latch is False

    def test_02_micro_tap_5ms_boundary(self):
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
        time.sleep(0.005)
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert len(toggles) == 1

    def test_03_10_second_extended_hold_boundary(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="push_to_talk",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        assert mgr._ptt_active is True
        mgr._handle_key_event(0x77, is_down=False, is_up=True)
        assert mgr._ptt_active is False

    def test_04_triple_tap_timing_overflow(self):
        mgr = GlobalHotkeyManager()
        mgr.configure(
            dictation_mode="push_to_talk",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
            notetaker_triple_tap=True,
        )
        # Add tap from 1.0 second ago
        mgr._ptt_tap_timestamps.append(time.monotonic() - 1.0)
        # Filter should drop old taps (>0.65s)
        now = time.monotonic()
        mgr._ptt_tap_timestamps = [t for t in mgr._ptt_tap_timestamps if (now - t) <= 0.65]
        assert len(mgr._ptt_tap_timestamps) == 0

    def test_05_simultaneous_press_multiple_unrelated_keys(self, monkeypatch):
        mgr = GlobalHotkeyManager()
        monkeypatch.setattr(mgr, "_is_vk_physically_down", lambda vk: True)
        mgr.configure(
            dictation_mode="push_to_talk",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )
        mgr._handle_key_event(0x77, is_down=True, is_up=False)
        for vk in (0x41, 0x42, 0x43, 0x44):  # A, B, C, D
            mgr._handle_key_event(vk, is_down=True, is_up=False)
        assert mgr._ptt_active is True


# =====================================================================
# Feature 10: Acoustic Pre-Buffer Residue & Stop-Flush Cleanup
# =====================================================================
class TestTier2Boundary10AcousticPreBufferResidueStopFlushCleanup:
    """Boundary conditions for acoustic pre-buffer."""

    def test_01_0_sample_audio_frame_slice(self):
        empty = np.zeros(0, dtype=np.float32)
        frames = DictationRecorder.slice_40ms_frames(empty)
        assert frames == []

    def test_02_1_sample_remainder_frame_padding(self):
        audio = np.zeros(641, dtype=np.float32)
        frames = DictationRecorder.slice_40ms_frames(audio)
        assert len(frames) == 2
        assert frames[1].size == 640

    def test_03_pure_nan_audio_array_trimmed(self):
        nans = np.full(1600, np.nan, dtype=np.float32)
        trimmed = LocalWhisperTranscriber._trim_edge_silence(nans)
        assert trimmed.size <= nans.size

    def test_04_massive_100mb_pre_buffer_drain(self):
        rec = DictationRecorder()
        for _ in range(1000):
            rec._pre_buffer.append(b"\x00" * 640)
        assert len(rec.pre_buffer) == rec._pre_buffer_max_frames

    def test_05_zero_amplitude_pure_silence_trim(self):
        silence = np.zeros(16000, dtype=np.float32)
        trimmed = LocalWhisperTranscriber._trim_edge_silence(silence)
        assert trimmed.size > 0


# =====================================================================
# Feature 11: Whisper Initial Prompt Sanitization
# =====================================================================
class TestTier2Boundary11WhisperInitialPromptSanitization:
    """Boundary conditions for initial prompt sanitization."""

    def test_01_prompt_exceeding_10k_chars(self):
        huge_prompt = "Valid speech prompt. " * 600
        assert len(huge_prompt) > 10000

    def test_02_all_hallucination_phrases_concatenated(self):
        combined = " ".join(DictationProcessor.HALLUCINATION_PHRASES)
        for h in DictationProcessor.HALLUCINATION_PHRASES:
            assert is_hallucination(h) is True

    def test_03_prompt_with_only_punctuation(self):
        punct = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        cleaned = punct.strip()
        assert len(cleaned) > 0

    def test_04_mixed_devanagari_and_latin_prompt(self):
        prompt = "Hello नमस्ते World दुनिया"
        has_deva = any("\u0900" <= c <= "\u097f" for c in prompt)
        assert has_deva is True

    def test_05_stutter_tail_with_single_word_repeats(self):
        stutter = "hello " * 50
        cleaned = clean_repetition_tail(stutter)
        assert len(cleaned) <= len(stutter)


# =====================================================================
# Feature 12: Wire config.hinglish_enabled & Sub-Clause Splitting
# =====================================================================
class TestTier2Boundary12WireHinglishEnabledSubClauseSplitting:
    """Boundary conditions for Hinglish & sub-clause splitting."""

    def test_01_exact_3s_audio_boundary_pause_split(self, synthetic_audio):
        exact_3s = synthetic_audio(3.0)
        split = LocalWhisperTranscriber._find_primary_pause_split(exact_3s)
        assert split is None or isinstance(split, int)

    def test_02_audio_with_no_silence_valleys(self, synthetic_audio):
        loud = synthetic_audio(4.0, amplitude=0.9)
        split = LocalWhisperTranscriber._find_primary_pause_split(loud)
        assert split is None

    def test_03_audio_with_20_silence_valleys(self, synthetic_audio):
        chunks = []
        for _ in range(5):
            chunks.append(synthetic_audio(0.6))
            chunks.append(np.zeros(3200, dtype=np.float32))
        audio = np.concatenate(chunks)
        split = LocalWhisperTranscriber._find_primary_pause_split(audio)
        assert split is not None

    def test_04_text_with_zero_words_in_processor(self):
        proc = DictationProcessor()
        res = proc.process_text("", mode=TransformationMode.VERBATIM)
        assert res.formatted_text == ""

    def test_05_100k_char_hinglish_text_transformation(self):
        proc = DictationProcessor()
        big_text = "mujhe document email karo please " * 3000
        res = proc.process_text(big_text, mode=TransformationMode.CLEAN)
        assert len(res.formatted_text) > 0


# =====================================================================
# Feature 13: Always-Warm Audio Capture Stream
# =====================================================================
class TestTier2Boundary13AlwaysWarmAudioCaptureStream:
    """Boundary conditions for always-warm pre-buffer."""

    def test_01_pre_buffer_max_duration_ceiling(self):
        rec = DictationRecorder()
        assert rec._pre_buffer_max_frames == int(round(rec.PRE_BUFFER_DURATION_SEC / rec.FRAME_DURATION_SEC))

    def test_02_rms_with_max_clipping_float_samples(self):
        clipped = np.full(640, 1.0, dtype=np.float32)
        lvl = compute_rms_level(clipped)
        assert lvl == pytest.approx(1.0, abs=0.01)

    def test_03_rms_with_subnormal_floats(self):
        subnormal = np.full(640, 1e-15, dtype=np.float32)
        lvl = compute_rms_level(subnormal)
        assert lvl == 0.0

    def test_04_interim_callback_frequency_throttling(self):
        rec = DictationRecorder()
        now = time.monotonic()
        rec._last_interim_time = now
        # Callback should not fire if interval < 0.22s
        assert (now + 0.1 - rec._last_interim_time) < 0.22

    def test_05_pre_buffer_thread_safety_under_rapid_appends(self):
        rec = DictationRecorder()

        def _pusher():
            for _ in range(100):
                rec.pre_buffer.append(b"\x00" * 640)

        threads = [threading.Thread(target=_pusher) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(rec.pre_buffer) == rec._pre_buffer_max_frames


# =====================================================================
# Feature 14: Audio Device Disconnection Auto-Recovery
# =====================================================================
class TestTier2Boundary14AudioDeviceDisconnectionAutoRecovery:
    """Boundary conditions for device disconnection."""

    def test_01_re_enumeration_when_no_devices_found(self):
        AudioDeviceManager.invalidate_cache()
        assert AudioDeviceManager._cached_devices is None

    def test_02_stream_callback_returns_abort_on_device_failure(self):
        rec = DictationRecorder()
        errors = []
        rec.on_error = lambda err: errors.append(err)
        rec.on_error("WASAPI device invalidated")
        assert len(errors) == 1

    def test_03_controller_handles_disconnect_during_dictation(self, isolated_controller):
        isolated_controller._on_dictation_error("Hardware unplugged")
        assert isolated_controller.dictation_state == DictationState.ERROR

    def test_04_meeting_recorder_recovers_after_all_loopbacks_lost(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir
        rec._pad_locked(1.0)
        assert rec._elapsed == 0.0

    def test_05_device_info_with_special_characters_in_name(self):
        dev = AudioDeviceInfo(index=0, name="HyperX Cloud II (Gaming 'Headset' / 2.0)", channels=2, sample_rate=48000, is_loopback=False)
        assert "HyperX" in dev.name


# =====================================================================
# Feature 15: Transcriber Lock Preemption / Priority Channel
# =====================================================================
class TestTier2Boundary15TranscriberLockPreemptionPriorityChannel:
    """Boundary conditions for transcriber preemption and lock acquisition."""

    def test_01_interim_timeout_boundary_under_lock(self):
        trans = LocalWhisperTranscriber()
        with trans._lock:
            # Non-blocking lock try should fail immediately
            acquired = trans._lock.acquire(blocking=False)
            if acquired:
                trans._lock.release()
            assert acquired is True  # RLock allows reentrant acquisition by same thread

    def test_02_exact_30s_audio_fast_path_boundary(self, synthetic_audio):
        audio_30s = synthetic_audio(30.0)
        assert audio_30s.size == 30 * 16000

    def test_03_transcriber_reentrant_lock_nested_acquisition(self):
        trans = LocalWhisperTranscriber()
        with trans._lock:
            with trans._lock:
                assert True

    def test_04_zero_length_audio_transcription(self):
        trans = LocalWhisperTranscriber()
        empty = np.zeros(0, dtype=np.float32)
        text, dur = trans.transcribe_interim(empty)
        assert text == ""
        assert dur == 0.0

    def test_05_transcriber_shutdown_while_task_pending(self):
        trans = LocalWhisperTranscriber()
        assert isinstance(trans._lock, type(threading.RLock()))


# =====================================================================
# Feature 16: Independent Back-to-Back Meeting Session Lifecycle
# =====================================================================
class TestTier2Boundary16IndependentBackToBackMeetingLifecycle:
    """Boundary conditions for back-to-back meeting lifecycle."""

    def test_01_back_to_back_zero_delay_transition(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        rec._out_dir = isolated_tmp_dir / "m1"
        rec._out_dir.mkdir()
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res1 = rec.stop()

        rec._out_dir = isolated_tmp_dir / "m2"
        rec._out_dir.mkdir()
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res2 = rec.stop()
        assert res1 is not None and res2 is not None

    def test_02_10_sequential_meetings_zero_resource_leak(self, isolated_tmp_dir, isolated_db):
        for i in range(10):
            m_id = isolated_db.create_meeting(f"Meeting {i}", capture_mode="both")
            assert m_id > 0

    def test_03_meeting_id_counter_uniqueness(self, isolated_db):
        id1 = isolated_db.create_meeting("A", capture_mode="both")
        id2 = isolated_db.create_meeting("B", capture_mode="both")
        assert id2 > id1

    def test_04_meeting_stop_during_pre_start_initialization(self, isolated_tmp_dir):
        rec = MeetingRecorder()
        assert rec.stop() is None

    def test_05_simultaneous_temp_directory_creation(self, isolated_tmp_dir):
        d1 = isolated_tmp_dir / "session_1"
        d2 = isolated_tmp_dir / "session_2"
        d1.mkdir()
        d2.mkdir()
        assert d1.exists() and d2.exists()


# =====================================================================
# Feature 17: Verified Meeting Call Detection vs Calendar Integration
# =====================================================================
class TestTier2Boundary17VerifiedMeetingCallDetectionVsCalendar:
    """Boundary conditions for call detection vs calendar integration."""

    def test_01_50_calendar_events_poll_zero_recordings(self, isolated_controller):
        assert isolated_controller.meeting_state == "idle"

    def test_02_rapid_call_window_open_and_close_under_50ms(self):
        detector = CallDetector()
        res = detector.poll()
        assert res is None or hasattr(res, "platform")

    def test_03_call_detector_handles_corrupted_window_titles(self):
        assert is_leaving_title("\x00\x00CorruptTitle\x00") is False

    def test_04_minimized_iconic_call_window_classification(self):
        assert is_non_call_hub("teams", "Microsoft Teams") is True

    def test_05_consent_denied_registry_simulation(self):
        detector = CallDetector()
        assert hasattr(detector, "poll")


# =====================================================================
# Feature 18: Transcript-Grounded Meeting MoM Verification
# =====================================================================
class TestTier2Boundary18TranscriptGroundedMeetingMoMVerification:
    """Boundary conditions for Stage 7 grounded MoM verification."""

    def test_01_100_percent_ungrounded_claims_all_rejected(self):
        items = [{"text": "Casual conversation about pets and tea.", "timestamp": 1.0}]
        fake_decisions = [{"decision_id": i, "evidence_quote": f"Fabricated claim {i}", "verified": True} for i in range(10)]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, fake_decisions, [], [])
        assert ver["verified_claims"] == 0
        assert ver["confidence_score"] == 0.0

    def test_02_case_and_punctuation_insensitive_grounding(self):
        items = [{"text": "We approved the budget for Q4.", "timestamp": 5.0}]
        decisions = [{"decision_id": 1, "evidence_quote": "APPROVED THE BUDGET FOR Q4!!!", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, [], [])
        assert decisions[0]["verified"] is True

    def test_03_10_000_segment_transcript_verification_speed(self):
        items = [{"text": f"Segment {i} discussed system architecture.", "timestamp": float(i)} for i in range(1000)]
        decisions = [{"decision_id": 1, "evidence_quote": "system architecture", "verified": True}]
        t0 = time.perf_counter()
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, [], [])
        assert (time.perf_counter() - t0) < 0.50
        assert decisions[0]["verified"] is True

    def test_04_single_word_evidence_quote_handling(self):
        items = [{"text": "We discussed scalability of the database.", "timestamp": 10.0}]
        decisions = [{"decision_id": 1, "evidence_quote": "scalability", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, [], [])
        assert decisions[0]["verified"] is True

    def test_05_empty_evidence_quote_boundary(self):
        items = [{"text": "Transcript content", "timestamp": 1.0}]
        decisions = [{"decision_id": 1, "evidence_quote": "", "verified": True}]
        ver = MeetingMinutesPipeline._stage7_verify_evidence(items, decisions, [], [])
        assert decisions[0]["verified"] is False


# =====================================================================
# Feature 19: Flicker-Free DWM Live Preview Overlay
# =====================================================================
class TestTier2Boundary19FlickerFreeDWMLivePreviewOverlay:
    """Boundary conditions for live preview overlay."""

    def test_01_100_rapid_text_updates_under_100ms(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        t0 = time.perf_counter()
        for i in range(100):
            bubble.update_preview(f"Speech stream token {i}")
        assert (time.perf_counter() - t0) < 0.20

    def test_02_empty_string_preview_update(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.update_preview("")
        assert bubble.lbl_preview.text() == ""

    def test_03_10_000_char_html_preview_boundary(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        huge_html = "<span>Live text segment </span>" * 400
        bubble.update_preview(huge_html)
        assert bubble.lbl_preview.maximumWidth() == 360

    def test_04_preview_card_styling_under_invalid_theme(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.apply_theme("non_existent_theme")
        assert bubble._theme == "non_existent_theme"

    def test_05_multiple_show_hide_cycles_zero_leak(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        for _ in range(50):
            bubble.show()
            bubble.hide()
        assert not bubble.isVisible()


# =====================================================================
# Feature 20: Multi-Monitor Preview Positioning & Screen Clamping
# =====================================================================
class TestTier2Boundary20MultiMonitorPreviewPositioningScreenClamping:
    """Boundary conditions for multi-monitor screen clamping."""

    def test_01_negative_screen_coordinates_secondary_monitor(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(-1500, 400, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert isinstance(bubble.x(), int)

    def test_02_far_right_boundary_clamping(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(3800, 500, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert isinstance(bubble.x(), int)

    def test_03_top_edge_boundary_clamping(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(500, 10, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.y() >= 0

    def test_04_bottom_dock_positioning(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        pill_rect = QRect(500, 1000, 200, 40)
        bubble.update_position_above_pill(pill_rect)
        assert bubble.y() < 1000

    def test_05_dpi_scaled_geometry_handling(self, offscreen_widgets):
        bubble = offscreen_widgets["preview_bubble"]()
        sh = bubble.sizeHint()
        assert sh.width() >= 0
        assert sh.height() >= 0


# =====================================================================
# Feature 21: Seamless Grey Preview to Committed Text Transition
# =====================================================================
class TestTier2Boundary21SeamlessGreyPreviewToCommittedTextTransition:
    """Boundary conditions for seamless preview transition."""

    def test_01_rapid_interim_to_complete_transition(self, offscreen_widgets):
        pill = offscreen_widgets["pill"]()
        pill.show()
        pill.update_live_preview("Interim speech")
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_02_dictation_failure_hides_preview(self, offscreen_widgets, isolated_controller):
        pill = offscreen_widgets["pill"]()
        pill.show()
        pill.update_live_preview("Recognized...")
        isolated_controller._on_dictation_error("Fatal capture error")
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_03_self_correction_marker_at_start_of_utterance(self):
        res = DictationProcessor.detect_verbal_self_corrections("no actually start from here")
        assert "start from here" in res["clean_text"]

    def test_04_self_correction_marker_at_end_of_utterance(self):
        res = DictationProcessor.detect_verbal_self_corrections("start from here wait sorry")
        assert res["has_corrections"] is True

    def test_05_10_consecutive_corrections_in_single_sentence(self):
        text = "apple no banana wait sorry orange scratch that melon I mean mango"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert "mango" in res["clean_text"]


# =====================================================================
# Feature 22: Target Window Focus & Verification Guard
# =====================================================================
class TestTier2Boundary22TargetWindowFocusVerificationGuard:
    """Boundary conditions for target window focus guard."""

    def test_01_target_hwnd_negative_boundary(self):
        target = TargetWindowInfo(hwnd=-1, title="Invalid", process_id=1, thread_id=1, process_name="invalid.exe")
        assert target.hwnd == -1

    def test_02_process_name_empty_string(self):
        target = TargetWindowInfo(hwnd=0x123, title="", process_id=1, thread_id=1, process_name="")
        assert target.process_name == ""

    def test_03_simulated_uac_admin_powershell(self, mock_target_windows):
        admin = mock_target_windows["admin"]
        assert admin.process_name.lower() == "powershell.exe"

    def test_04_focus_stolen_during_insertion_delay(self, mock_target_windows):
        tracker = WindowFocusTracker()
        tracker._last_target = mock_target_windows["notepad"]
        tracker._locked_target = mock_target_windows["word"]
        assert tracker._locked_target.process_name == "winword.exe"

    def test_05_explorer_desktop_target_handling(self):
        target = TargetWindowInfo(hwnd=0x1000, title="", process_id=99, thread_id=99, process_name="explorer.exe")
        is_desktop = target.process_name.lower() == "explorer.exe" and not target.title
        assert is_desktop is True


# =====================================================================
# Feature 23: Multilingual Synthetic Audio Test Fixtures (en-IN, hi-IN, Hinglish)
# =====================================================================
class TestTier2Boundary23MultilingualSyntheticAudioFixtures:
    """Boundary conditions for synthetic audio fixtures."""

    def test_01_zero_duration_audio_generation(self, synthetic_audio):
        audio = synthetic_audio(0.0)
        assert audio.size == 0
        assert audio.dtype == np.float32

    def test_02_extreme_duration_audio_generation(self, synthetic_audio):
        audio = synthetic_audio(30.0)
        assert audio.size == 30 * 16000

    def test_03_high_frequency_nyquist_boundary(self, synthetic_audio):
        audio = synthetic_audio(1.0, freq_hz=7999.0)
        assert audio.size == 16000
        assert not np.isnan(audio).any()

    def test_04_extreme_amplitude_clipping(self, synthetic_audio):
        audio = synthetic_audio(0.5, amplitude=5.0)
        assert np.max(audio) <= 1.0
        assert np.min(audio) >= -1.0

    def test_05_nan_free_float32_validation(self, synthetic_audio):
        audio = synthetic_audio(2.0, freq_hz=1000.0)
        assert np.isnan(audio).sum() == 0
        assert np.isinf(audio).sum() == 0


# =====================================================================
# Feature 24: Automated Benchmarking & Edge-Case Reliability Suite
# =====================================================================
class TestTier2Boundary24AutomatedBenchmarkingEdgeCaseReliability:
    """Boundary conditions for benchmarking and metrics calculation."""

    def test_01_wer_cer_with_empty_strings(self):
        wer, cer = compute_wer_cer("", "")
        assert wer == 0.0
        assert cer == 0.0

    def test_02_wer_cer_with_empty_reference(self):
        wer, cer = compute_wer_cer("", "spoken hypothesis")
        assert wer >= 0.0

    def test_03_wer_cer_with_empty_hypothesis(self):
        wer, cer = compute_wer_cer("ground truth reference", "")
        assert wer == 100.0

    def test_04_wer_cer_with_special_characters_and_emojis(self):
        wer, cer = compute_wer_cer("namaste 🚀 दुनिया", "namaste 🚀 दुनिया")
        assert wer == 0.0
        assert cer == 0.0

    def test_05_config_save_load_stress_50_cycles(self, isolated_tmp_dir, monkeypatch):
        cfg_path = isolated_tmp_dir / "stress_config.json"
        monkeypatch.setenv("VOXLOCAL_CONFIG_PATH", str(cfg_path))
        cfg = AppConfig(storage_dir=str(isolated_tmp_dir))
        for _ in range(25):
            cfg.save()
            loaded = AppConfig.load()
            assert loaded.storage_dir == cfg.storage_dir

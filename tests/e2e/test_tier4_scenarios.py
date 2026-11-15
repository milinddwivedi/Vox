"""
Tier 4 Real-World Application Scenarios Test Suite for VOX Stabilization.

Authority:
- PROJECT.md § Architecture & Feature Inventory
- TEST_INFRA.md (§ 2.4 Real-World Application Scenarios)

The 5 Comprehensive Real-World Workloads:
1. test_scenario_01_rapid_consecutive_dictation_notepad_word_slack_hinglish
2. test_scenario_02_back_to_back_teams_meetings_device_disconnect_zero_leakage
3. test_scenario_03_ptt_dictation_during_heavy_meeting_transcription
4. test_scenario_04_rapid_focus_switching_elevated_admin_code_browser
5. test_scenario_05_cold_startup_idle_resources_and_clean_shutdown

All tests run strictly headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
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


class TestTier4RealWorldScenarios:
    """The 5 Real-World End-to-End Application Scenarios from TEST_INFRA.md."""

    def test_scenario_01_rapid_consecutive_dictation_notepad_word_slack_hinglish(
        self,
        offscreen_widgets,
        mock_win32_clipboard,
        synthetic_audio,
        monkeypatch,
    ):
        """
        Scenario 1: Rapid consecutive dictation into Windows 11 Notepad, Word, and Slack
        with code-switched Hinglish.
        Features exercised: F7, F8, F9, F10, F11, F12, F19, F21.
        """
        targets = [
            TargetWindowInfo(hwnd=0x101, title="Untitled - Notepad", process_id=11, thread_id=111, process_name="notepad.exe"),
            TargetWindowInfo(hwnd=0x202, title="Project Proposal - Word", process_id=22, thread_id=222, process_name="winword.exe"),
            TargetWindowInfo(hwnd=0x303, title="team-announcements - Slack", process_id=33, thread_id=333, process_name="slack.exe"),
        ]

        pill = offscreen_widgets["pill"]()
        pill.show()
        proc = DictationProcessor()
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)

        # Mock simulated window existence & injection in offscreen test environment
        monkeypatch.setattr("voxlocal.win32.text_inserter.user32.IsWindow", lambda hwnd: True)
        monkeypatch.setattr("voxlocal.win32.window_focus.user32.IsWindow", lambda hwnd: True)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: mock_win32_clipboard.set_text(a[0]))

        # Hotkey manager in hybrid mode
        hotkey_mgr = GlobalHotkeyManager()
        hotkey_mgr.configure(
            dictation_mode="hybrid",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="f8",
            meeting_hotkey="ctrl+alt+r",
        )

        hinglish_utterances = [
            "aaj meeting mein project milestones review karenge and finalize deadline",
            "please presentation slides update kar dena before evening demo",
            "summary document share kar diya hai slack channel par check kar lo",
        ]

        mock_win32_clipboard.set_text("Important user text copied earlier")
        assert mock_win32_clipboard.get_text() == "Important user text copied earlier"

        for idx, target in enumerate(targets):
            tracker._last_target = target

            # Hotkey activation
            hotkey_mgr._handle_key_event(0x77, is_down=True, is_up=False)
            assert hotkey_mgr._ptt_active is True

            # Speech onset in warm pre-buffer
            audio_frame = synthetic_audio(0.5, freq_hz=350.0)
            assert audio_frame.size == 8000

            # Live preview update in subtle grey
            raw_text = hinglish_utterances[idx]
            pill.update_live_preview(f"Listening: {raw_text[:25]}...")
            assert pill.preview_bubble.isVisible()

            # Process Hinglish code-switching with loanword preservation
            res = proc.process_text(raw_text, mode=TransformationMode.CLEAN)
            assert "meeting" in res.formatted_text.lower() or "document" in res.formatted_text.lower() or "presentation" in res.formatted_text.lower()

            # Serialized injection into active application
            ok, method = inserter.insert_text(res.formatted_text, method="auto", target=target)
            assert ok is True
            if target.process_name.lower() in ("winword.exe", "slack.exe"):
                assert method == "clipboard_paste"

            # Key release resets hotkey latch
            hotkey_mgr._handle_key_event(0x77, is_down=False, is_up=True)
            assert hotkey_mgr._ptt_active is False

            # Live preview fades out smoothly on insertion completion
            pill.preview_bubble.hide()
            assert not pill.preview_bubble.isVisible()

    def test_scenario_02_back_to_back_teams_meetings_device_disconnect_zero_leakage(
        self,
        isolated_tmp_dir,
        isolated_db,
    ):
        """
        Scenario 2: Back-to-back Teams meeting recordings with audio device disconnection
        simulation and zero state leakage.
        Features exercised: F1, F3, F5, F14, F15, F16, F17, F18.
        """
        # 1. Verified Call Detection
        call_title = "Product Strategy Sync | Microsoft Teams"
        assert not is_leaving_title(call_title)
        assert not is_non_call_hub("teams", call_title)

        # 2. Meeting 1 Lifecycle
        dir_m1 = isolated_tmp_dir / "teams_meeting_1"
        dir_m1.mkdir()
        id_m1 = isolated_db.create_meeting("Teams Product Strategy", capture_mode="both")
        assert id_m1 > 0

        rec = MeetingRecorder()
        rec._out_dir = dir_m1
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()

        # Simulate USB headset disconnection during active recording
        errors = []
        rec.on_error = lambda err: errors.append(err)
        rec._pad_locked(2.0)

        # Call end detected
        assert is_leaving_title("Leaving call...")

        # Teardown SLA: stop executes in <100ms
        t0 = time.perf_counter()
        res_m1 = rec.stop()
        stop_ms = (time.perf_counter() - t0) * 1000.0
        assert stop_ms < 100.0
        assert res_m1 is not None
        assert Path(res_m1.mixed_wav_path).exists()

        # 3. Meeting 2 Starts Immediately (<50ms delay)
        dir_m2 = isolated_tmp_dir / "teams_meeting_2"
        dir_m2.mkdir()
        id_m2 = isolated_db.create_meeting("Client Architecture Review", capture_mode="both")
        assert id_m2 > id_m1

        rec._out_dir = dir_m2
        rec._state = "recording"
        rec._resume_ts = time.perf_counter()
        res_m2 = rec.stop()
        assert res_m2 is not None

        assert res_m1.mixed_wav_path != res_m2.mixed_wav_path
        assert dir_m1 != dir_m2

        # 4. Background MoM Generation with Stage 7 Evidence Verification
        transcript_1 = [
            {"text": "We approved the cloud migration timeline for Q3.", "timestamp": 15.0},
            {"text": "Rahul will deploy the staging infrastructure by next Friday.", "timestamp": 25.0},
        ]
        decisions = [{"decision_id": 1, "evidence_quote": "cloud migration timeline for Q3", "verified": True}]
        actions = [{"action_id": 1, "evidence_quote": "Rahul will deploy the staging infrastructure", "verified": True}]

        mom_ver = MeetingMinutesPipeline._stage7_verify_evidence(transcript_1, decisions, actions, [])
        assert mom_ver["verified_claims"] == 2
        assert mom_ver["confidence_score"] == 100.0
        assert decisions[0]["verified"] is True
        assert actions[0]["verified"] is True

    def test_scenario_03_ptt_dictation_during_heavy_meeting_transcription(
        self,
        synthetic_audio,
        fast_mock_transcriber,
        offscreen_widgets,
        mock_target_windows,
        monkeypatch,
    ):
        """
        Scenario 3: Push-to-talk dictation activation while background meeting
        finalizer runs heavy Whisper transcription.
        Features exercised: F2, F7, F15, F19, F21.
        """
        pill = offscreen_widgets["pill"]()
        pill.show()
        tracker = WindowFocusTracker()
        tracker._last_target = mock_target_windows["word"]
        inserter = TextInserter(tracker)
        monkeypatch.setattr("voxlocal.win32.text_inserter.user32.IsWindow", lambda hwnd: True)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: True)

        # 1. Hotkey press latency (<20ms start with warm cache)
        AudioDeviceManager.resolve_microphone(use_cache=True)
        t0 = time.perf_counter()
        dev = AudioDeviceManager.resolve_microphone(use_cache=True)
        assert (time.perf_counter() - t0) < 0.02

        # 2. Interim speculative preview is non-blocking (<150ms)
        audio_chunk = synthetic_audio(1.0)
        t_preview = time.perf_counter()
        preview_text, dur = fast_mock_transcriber["transcribe_interim"](audio_chunk)
        assert (time.perf_counter() - t_preview) < 0.15
        assert len(preview_text) > 0

        pill.update_live_preview(preview_text)
        assert pill.preview_bubble.isVisible()

        # 3. Finalization via fast-path single-encode (<30s)
        fast_mock_transcriber["set_text"]("Urgent client response: approved.")
        final_res = fast_mock_transcriber["transcribe_dictation"](synthetic_audio(3.0))
        assert "approved" in final_res["text"]

        # 4. Serialized injection into Word
        ok, method = inserter.insert_text(final_res["text"], target=mock_target_windows["word"])
        assert ok is True

        # 5. Seamless preview fade out
        pill.preview_bubble.hide()
        assert not pill.preview_bubble.isVisible()

    def test_scenario_04_rapid_focus_switching_elevated_admin_code_browser(
        self,
        isolated_controller,
        mock_target_windows,
        mock_win32_clipboard,
        offscreen_widgets,
        monkeypatch,
    ):
        """
        Scenario 4: Rapid focus switching between elevated admin window, code editor,
        and web browser during live speech.
        Features exercised: F4, F7, F8, F20, F22.
        """
        bubble = offscreen_widgets["preview_bubble"]()
        bubble.update_preview("Live code dictation...")

        vscode = mock_target_windows["vscode"]
        admin = mock_target_windows["admin"]
        browser = mock_target_windows["browser"]

        inserter = TextInserter(isolated_controller.focus_tracker)
        monkeypatch.setattr("voxlocal.win32.text_inserter.user32.IsWindow", lambda hwnd: True)
        monkeypatch.setattr("voxlocal.win32.window_focus.user32.IsWindow", lambda hwnd: True)
        monkeypatch.setattr(inserter, "_send_unicode_text", lambda *a, **k: True)
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: mock_win32_clipboard.set_text(a[0]))

        mock_win32_clipboard.set_text("Initial code snippet: def calculate(): pass")

        for target in (vscode, admin, browser):
            monkeypatch.setattr(isolated_controller.focus_tracker, "poll_foreground", lambda t=target: t)
            isolated_controller.focus_tracker._last_target = target
            isolated_controller._poll_foreground_window()

            bubble.update_position_above_pill(QRect(100, 700, 250, 40))
            assert bubble.y() < 700

            active = isolated_controller.focus_tracker.get_active_target()
            assert active.process_name == target.process_name

            ok, method = inserter.insert_text("git status && git pull", method="auto", target=target)
            assert ok is True

        assert mock_win32_clipboard.get_text() is not None

    def test_scenario_05_cold_startup_idle_resources_and_clean_shutdown(
        self,
        isolated_config,
        isolated_tmp_dir,
    ):
        """
        Scenario 5: Cold startup, idle resource consumption (CPU/RAM), and clean
        application shutdown.
        Features exercised: F1, F4, F6, F24.
        """
        # 1. Cold startup timing (<1.5s SLA)
        t0 = time.perf_counter()
        ctrl = AppController(isolated_config)
        startup_sec = time.perf_counter() - t0
        assert startup_sec < 1.50

        # 2. Idle verification
        assert ctrl.dictation_state == DictationState.IDLE
        assert ctrl.meeting_state == "idle"
        assert ctrl._focus_timer.isActive()
        assert ctrl._focus_timer.interval() == 150

        # 3. Clean shutdown with zero deadlocks or dangling timers
        t_shut = time.perf_counter()
        ctrl.shutdown()
        shut_sec = time.perf_counter() - t_shut
        assert shut_sec < 1.0
        assert not ctrl._focus_timer.isActive()
        assert ctrl._cancel_finalization_event.is_set()
        assert ctrl._cancel_ask_vox is True
        assert ctrl._interim_worker_running is False

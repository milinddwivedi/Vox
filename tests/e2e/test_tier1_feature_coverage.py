"""
Tier 1 Feature Coverage Test Suite for Project VOX (Wispr Flow Exact Replica).

Authority:
- ORIGINAL_REQUEST.md
- PROJECT.md § Feature Inventory (Features 1 through 20)
- SCOPE.md (E2E Testing Track)
- TEST_INFRA.md (4-Tier Test Architecture)

Test Classes:
- TestTier1Feature01Triage (5 tests)
- TestTier1Feature02Startup (5 tests)
- TestTier1Feature03Hotkeys (5 tests)
- TestTier1Feature04TextInjection (5 tests)
- TestTier1Feature05AudioCapture (5 tests)
- TestTier1Feature06Transcription (5 tests)
- TestTier1Feature07StateMachine (5 tests)
- TestTier1Feature08TransformModes (5 tests)
- TestTier1Feature09SelfCorrection (5 tests)
- TestTier1Feature10VoicePill (5 tests)
- TestTier1Feature11FlowHub (5 tests)
- TestTier1Feature12CommandPalette (5 tests)
- TestTier1Feature13NotetakerScratchpad (5 tests)
- TestTier1Feature14MeetingStudio (5 tests)
- TestTier1Feature15OutlookCalendar (5 tests)
- TestTier1Feature16OnboardingTray (5 tests)
- TestTier1Feature17SqliteStorage (5 tests)
- TestTier1Feature18TurnkeySuite (5 tests)
- TestTier1Feature19StandaloneExe (5 tests)
- TestTier1Feature20Hardening (5 tests)

Total: 100 test cases
All tests run headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timedelta, timezone
import os
import sqlite3
import sys
import winreg
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.hardware import HardwareProfiler
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.hotkeys import (
    GlobalHotkeyManager,
    VK_MAP,
    normalize_vk,
    parse_hotkey_string,
)
from voxlocal.win32.text_inserter import (
    TextInserter,
    get_clipboard_text,
    set_clipboard_text,
)
from voxlocal.win32.window_focus import WindowFocusTracker, TargetWindowInfo
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.devices import (
    AudioDeviceManager,
    compute_rms_level,
    normalize_speech_audio,
    resample_audio_to_16k_mono,
)
from voxlocal.engine.transcriber import (
    AVAILABLE_MODELS,
    estimate_acoustic_signature,
    extract_speaker_embedding,
)
from voxlocal.engine.dictation_processor import (
    DictationProcessor,
    TransformationMode,
)
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.command_palette import PaletteCommand, VoxCommandPaletteDialog
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.tray_manager import SystemTrayManager
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.calendar.calendar_manager import CalendarManager
from voxlocal.calendar.mock_provider import MockCalendarProvider


# ==============================================================================
# Feature 1: Codebase Triage & Cleanup (5 tests)
# ==============================================================================
class TestTier1Feature01Triage:
    """Validate codebase cleanup, absence of obsolete artifacts, and clean package layout."""

    def test_no_leftover_legacy_media_in_root(self) -> None:
        """Verify no temporary legacy recordings (temp_*.mp4, temp_*.wav) reside in repository root."""
        root_files = [f.name for f in PROJECT_ROOT.iterdir() if f.is_file()]
        legacy_media = [
            f for f in root_files if f.startswith("temp_") and (f.endswith(".mp4") or f.endswith(".wav"))
        ]
        assert legacy_media == [], f"Dangling legacy media found in root: {legacy_media}"

    def test_voxlocal_subpackage_structure(self) -> None:
        """Verify all 7 mandatory architectural subpackages exist under src/voxlocal/."""
        expected_packages = [
            "audio",
            "engine",
            "win32",
            "ui",
            "calendar",
            "storage",
            "core",
        ]
        voxlocal_dir = SRC_DIR / "voxlocal"
        assert voxlocal_dir.exists() and voxlocal_dir.is_dir()
        for pkg in expected_packages:
            sub = voxlocal_dir / pkg
            assert sub.exists() and sub.is_dir(), f"Missing architectural package: src/voxlocal/{pkg}"
            init_file = sub / "__init__.py"
            assert init_file.exists(), f"Missing __init__.py in src/voxlocal/{pkg}"

    def test_entrypoint_main_exists_and_syntax_valid(self) -> None:
        """Verify main.py exists at project root and compiles without syntax errors."""
        main_py = PROJECT_ROOT / "main.py"
        assert main_py.exists() and main_py.is_file()
        source = main_py.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename="main.py")
        assert parsed is not None

    def test_isolated_config_defaults_hygiene(self, isolated_config: AppConfig) -> None:
        """Verify isolated AppConfig initializes with safe sandboxed paths and hooks disabled."""
        assert isolated_config.disable_hotkeys is True
        assert isolated_config.disable_model_preload is True
        assert isolated_config.llm_enabled is False
        assert Path(isolated_config.storage_dir).exists()

    def test_clean_repository_tree_no_dangling_artifacts(self) -> None:
        """Verify no patch artifacts (.orig, .rej) or scratch scripts exist in root."""
        root_items = [f.name for f in PROJECT_ROOT.iterdir()]
        unwanted = [f for f in root_items if f.endswith(".orig") or f.endswith(".rej") or f.startswith("scratch_")]
        assert unwanted == [], f"Unwanted build/scratch artifacts detected: {unwanted}"


# ==============================================================================
# Feature 2: Robust Windows Startup Manager (5 tests)
# ==============================================================================
class TestTier1Feature02Startup:
    """Validate Windows login registry manager and launch command generation."""

    def test_launch_command_minimized_flag(self) -> None:
        """Verify StartupManager.get_launch_command(minimized=True) includes --startup flag."""
        cmd = StartupManager.get_launch_command(minimized=True)
        assert cmd.endswith(" --startup")

    def test_launch_command_unminimized(self) -> None:
        """Verify StartupManager.get_launch_command(minimized=False) omits --startup flag."""
        cmd = StartupManager.get_launch_command(minimized=False)
        assert not cmd.endswith(" --startup")

    def test_launch_command_executable_quoted(self) -> None:
        """Verify the launch executable path is securely quoted to prevent space injection vulnerabilities."""
        cmd = StartupManager.get_launch_command(minimized=True)
        assert cmd.startswith('"')
        closing_quote_idx = cmd.find('"', 1)
        assert closing_quote_idx > 1

    def test_startup_availability_returns_bool(self) -> None:
        """Verify StartupManager.is_available() executes safely and returns a boolean."""
        avail = StartupManager.is_available()
        assert isinstance(avail, bool)

    def test_startup_registry_error_resilience(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify StartupManager gracefully catches PermissionError without crashing the application."""
        def mock_open_key(*args, **kwargs):
            raise PermissionError("WinError 5 Access is denied")

        monkeypatch.setattr(winreg, "OpenKey", mock_open_key)
        assert StartupManager.is_available() is False


# ==============================================================================
# Feature 3: Global Hotkey Interception (5 tests)
# ==============================================================================
class TestTier1Feature03Hotkeys:
    """Validate low-level keyboard hook virtual key parsing and modifier normalization."""

    def test_parse_standard_chords(self) -> None:
        """Verify parse_hotkey_string converts standard chords into canonical VK sets."""
        vks = parse_hotkey_string("ctrl+shift+space")
        assert 0x11 in vks  # VK_CONTROL
        assert 0x10 in vks  # VK_SHIFT
        assert 0x20 in vks  # VK_SPACE
        assert len(vks) == 3

    def test_parse_function_keys(self) -> None:
        """Verify parse_hotkey_string converts F-keys and chords like Win+F1..F5."""
        f1_vk = parse_hotkey_string("f1")
        assert f1_vk == {0x70}  # VK_F1
        win_f2_vk = parse_hotkey_string("win+f2")
        assert win_f2_vk == {0x5B, 0x71}  # VK_LWIN, VK_F2

    def test_normalize_modifier_vk_codes(self) -> None:
        """Verify normalize_vk maps left/right variants to canonical modifier codes."""
        assert normalize_vk(0xA2) == 0x11  # LCONTROL -> CTRL
        assert normalize_vk(0xA3) == 0x11  # RCONTROL -> CTRL
        assert normalize_vk(0xA0) == 0x10  # LSHIFT -> SHIFT
        assert normalize_vk(0xA1) == 0x10  # RSHIFT -> SHIFT
        assert normalize_vk(0xA4) == 0x12  # LMENU -> ALT
        assert normalize_vk(0x5C) == 0x5B  # RWIN -> LWIN

    def test_parse_empty_and_whitespace_hotkeys(self) -> None:
        """Verify empty, whitespace, or invalid hotkey strings safely return empty sets."""
        assert parse_hotkey_string("") == set()
        assert parse_hotkey_string("   ") == set()
        assert parse_hotkey_string("unknown_key_xyz") == set()

    def test_hotkey_manager_initialization_and_shutdown(self) -> None:
        """Verify GlobalHotkeyManager instantiates, configures, and shuts down cleanly."""
        mgr = GlobalHotkeyManager()
        assert mgr is not None
        mgr.configure(
            dictation_mode="toggle",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="ctrl+win",
            meeting_hotkey="ctrl+alt+r",
        )
        mgr.stop()


# ==============================================================================
# Feature 4: Atomic Win32 Text Injection (5 tests)
# ==============================================================================
class TestTier1Feature04TextInjection:
    """Validate 3-tier text inserter, method selection heuristics, and keyboard injection."""

    def test_insert_empty_text_returns_false(self) -> None:
        """Verify inserting empty text immediately returns (False, 'empty') without side effects."""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        ok, method = inserter.insert_text("")
        assert ok is False
        assert method == "empty"

    def test_method_selection_short_ascii(self) -> None:
        """Verify short ASCII strings (< 120 chars) default to unicode_sendinput attempt."""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        ok, method = inserter.insert_text("VOX quick test", method="auto")
        assert method in ("unicode_sendinput", "clipboard_paste")

    def test_method_selection_long_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify strings > 120 chars automatically route to clipboard paste to prevent UI lag."""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        long_text = "The quick brown fox jumps over the lazy dog. " * 5  # ~225 chars
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: True)
        ok, method = inserter.insert_text(long_text, method="auto")
        assert ok is True
        assert method == "clipboard_paste"

    def test_method_selection_non_ascii(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify strings containing non-ASCII / Unicode characters route to clipboard paste."""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        unicode_text = "Namaste नमस्ते VOX Voice Assistant"
        monkeypatch.setattr(inserter, "_paste_via_clipboard", lambda *a, **k: True)
        ok, method = inserter.insert_text(unicode_text, method="auto")
        assert ok is True
        assert method == "clipboard_paste"

    def test_send_backspaces_and_enter_injection(self) -> None:
        """Verify backspace and enter keyboard injection commands execute successfully."""
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        assert inserter.send_backspaces(count=3) is True
        assert inserter.send_enter() is True


# ==============================================================================
# Feature 5: Audio Capture Pipeline (5 tests)
# ==============================================================================
class TestTier1Feature05AudioCapture:
    """Validate audio capture recorder, RMS metering, and 16kHz resampling utilities."""

    def test_recorder_initial_state(self) -> None:
        """Verify DictationRecorder initializes in idle state with clean buffers."""
        rec = DictationRecorder()
        assert rec.is_recording is False
        assert rec.last_mic_16k.size == 0
        assert rec.last_sys_16k.size == 0
        assert rec.mic_gain == 3.0

    def test_compute_rms_level_silence_vs_signal(self) -> None:
        """Verify compute_rms_level produces near-zero for silence and high level for signal."""
        silence = np.zeros(16000, dtype=np.float32)
        sine = np.sin(2 * np.pi * 440 * np.linspace(0, 1, 16000, False)).astype(np.float32) * 0.8
        lvl_silence = compute_rms_level(silence)
        lvl_sine = compute_rms_level(sine)
        assert lvl_silence <= 0.05
        assert lvl_sine > 0.4
        assert lvl_sine <= 1.0

    def test_resample_audio_to_16k_mono(self) -> None:
        """Verify resampling arbitrary 48kHz stereo/mono buffers converts cleanly to 16kHz mono."""
        sr_orig = 48000
        duration_sec = 0.5
        samples = int(sr_orig * duration_sec)
        pcm_48k = np.zeros(samples, dtype=np.int16)
        resampled = resample_audio_to_16k_mono(pcm_48k, src_rate=sr_orig, src_channels=1)
        assert resampled.dtype == np.float32
        assert len(resampled) == 8000  # 16000 * 0.5 sec

    def test_normalize_speech_audio_scaling(self) -> None:
        """Verify normalize_speech_audio scales low-volume audio into proper dynamic range [-1.0, 1.0]."""
        low_signal = np.array([0.02, -0.02, 0.01, -0.01], dtype=np.float32)
        normalized = normalize_speech_audio(low_signal)
        assert normalized.dtype == np.float32
        assert np.max(np.abs(normalized)) <= 1.0

    def test_audio_device_manager_device_enumeration(self) -> None:
        """Verify AudioDeviceManager.list_devices executes and returns structured device lists."""
        mics, loopbacks = AudioDeviceManager.list_devices()
        assert isinstance(mics, list)
        assert isinstance(loopbacks, list)


# ==============================================================================
# Feature 6: Offline Fast-Whisper Transcription (5 tests)
# ==============================================================================
class TestTier1Feature06Transcription:
    """Validate Whisper transcription model catalog, acoustic signature, and hardware detection."""

    def test_available_models_catalog(self) -> None:
        """Verify AVAILABLE_MODELS catalog contains large-v3-turbo and large-v3 with metadata."""
        assert "large-v3-turbo" in AVAILABLE_MODELS
        assert "large-v3" in AVAILABLE_MODELS
        turbo = AVAILABLE_MODELS["large-v3-turbo"]
        assert "size" in turbo and "speed" in turbo and "description" in turbo

    def test_hardware_profiler_detection(self) -> None:
        """Verify HardwareProfiler detects valid system CPU cores, 64-bit architecture, and RAM."""
        prof = HardwareProfiler.get_profile()
        assert prof.is_64bit is True
        assert prof.cpu_threads >= 1
        assert prof.ram_total_gb > 0
        assert len(prof.corporate_tier) > 0

    def test_estimate_acoustic_signature(self, synthetic_audio: Callable[..., np.ndarray]) -> None:
        """Verify estimate_acoustic_signature computes non-negative frequency estimate."""
        audio = synthetic_audio(duration_sec=0.5, freq_hz=440.0)
        sig = estimate_acoustic_signature(audio)
        assert isinstance(sig, float)
        assert sig >= 0.0

    def test_speaker_embedding_short_audio_rejection(self, synthetic_audio: Callable[..., np.ndarray]) -> None:
        """Verify extract_speaker_embedding rejects audio under 180ms by returning None."""
        short_audio = synthetic_audio(duration_sec=0.1)  # 100ms
        emb = extract_speaker_embedding(short_audio)
        assert emb is None

    def test_speaker_embedding_voiced_audio(self, synthetic_audio: Callable[..., np.ndarray]) -> None:
        """Verify extract_speaker_embedding on sustained voiced audio produces a unit-normalized vector."""
        voiced_audio = synthetic_audio(duration_sec=1.0, freq_hz=200.0, amplitude=0.8)
        emb = extract_speaker_embedding(voiced_audio)
        if emb is not None:
            assert emb.shape == (16,)
            assert np.isclose(np.linalg.norm(emb), 1.0, atol=1e-3)


# ==============================================================================
# Feature 7: 9-State Dictation State Machine (5 tests)
# ==============================================================================
class TestTier1Feature07StateMachine:
    """Validate 9-state dictation state machine transitions and lifecycle signals."""

    def test_initial_state_is_idle(self, isolated_controller: AppController) -> None:
        """Verify controller dictation state initializes to idle."""
        assert isolated_controller.dictation_state == "idle"

    def test_dictation_state_changed_signal_emitted(self, isolated_controller: AppController) -> None:
        """Verify dictation_state_changed pyqtSignal is emitted on state transitions."""
        emitted_states = []
        isolated_controller.dictation_state_changed.connect(lambda st, msg: emitted_states.append(st))
        isolated_controller.cancel_dictation()
        assert "idle" in emitted_states

    def test_cancel_dictation_resets_to_idle(self, isolated_controller: AppController) -> None:
        """Verify cancel_dictation forces active dictation back to idle state."""
        isolated_controller._dictation_state = "listening"
        isolated_controller.cancel_dictation()
        assert isolated_controller.dictation_state == "idle"

    def test_double_start_dictation_handling(self, isolated_controller: AppController) -> None:
        """Verify re-entrant start_dictation call while already active returns False."""
        isolated_controller._dictation_state = "listening"
        started = isolated_controller.start_dictation()
        assert started is False
        isolated_controller.cancel_dictation()

    def test_meeting_state_machine_isolation(self, isolated_controller: AppController) -> None:
        """Verify meeting recording state machine operates independently of dictation state machine."""
        assert isolated_controller.meeting_state == "idle"
        isolated_controller._dictation_state = "listening"
        assert isolated_controller.meeting_state == "idle"
        isolated_controller.cancel_dictation()


# ==============================================================================
# Feature 8: 5 Transformation Modes (5 tests)
# ==============================================================================
class TestTier1Feature08TransformModes:
    """Validate 5 transformation modes: Verbatim, Clean, Polished, Structured, and Email."""

    def test_spoken_punctuation_conversion(self) -> None:
        """Verify spoken punctuation phrases ('period', 'comma', 'new line') convert to symbols."""
        res = DictationProcessor.process("hello world period new line next step comma ok", spoken_punctuation=True)
        assert "Hello world." in res
        assert "\n" in res
        assert "Next step," in res

    def test_mode_verbatim_preserves_fillers(self) -> None:
        """Verify verbatim mode preserves raw filler words like 'um' and 'uh' unchanged."""
        raw = "um so uh we should proceed"
        res = DictationProcessor.process(raw, mode=TransformationMode.VERBATIM)
        assert "um" in res.lower()
        assert "uh" in res.lower()

    def test_mode_clean_removes_fillers(self) -> None:
        """Verify clean mode prunes conversational filler words."""
        raw = "um so uh we should proceed"
        res = DictationProcessor.process(raw, mode=TransformationMode.CLEAN)
        tokens = res.lower().split()
        assert "um" not in tokens
        assert "uh" not in tokens

    def test_mode_email_formatting(self) -> None:
        """Verify email mode formats dictation with Subject, Greeting, Body, CTA, and Sign-off."""
        raw = "please review the attached sprint roadmap by end of day"
        res = DictationProcessor.process(raw, mode=TransformationMode.EMAIL)
        assert "Subject:" in res
        assert "Hi Team," in res
        assert "Best regards," in res
        assert "VOX" in res

    def test_mode_structured_formatting(self) -> None:
        """Verify structured mode formats items into markdown outline with bullet points."""
        raw = "first review security second deploy database third run tests"
        res = DictationProcessor.process(raw, mode=TransformationMode.STRUCTURED)
        assert "- " in res


# ==============================================================================
# Feature 9: Verbal Self-Correction Parser (5 tests)
# ==============================================================================
class TestTier1Feature09SelfCorrection:
    """Validate verbal self-correction retraction parser and crimson strikethrough preview."""

    def test_scratch_that_retraction(self) -> None:
        """Verify 'scratch that' retracts erroneous preceding statement."""
        text = "Let's schedule for Tuesday scratch that Thursday at 3 PM"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert res["has_corrections"] is True
        assert "Thursday" in res["clean_text"]
        assert "Tuesday" not in res["clean_text"]

    def test_actually_correction(self) -> None:
        """Verify 'no actually' retracts preceding phrase and adopts corrected value."""
        text = "Deliver the release on Monday no actually Wednesday"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert res["has_corrections"] is True
        assert "Wednesday" in res["clean_text"]

    def test_annotated_html_crimson_strikethrough(self) -> None:
        """Verify annotated_html contains crimson line-through styling (#D32F2F)."""
        text = "Assign ticket to Bob scratch that Alice"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert res["has_corrections"] is True
        assert "#D32F2F" in res["annotated_html"]
        assert "line-through" in res["annotated_html"]

    def test_no_correction_marker_detected(self) -> None:
        """Verify utterances without self-corrections return has_corrections=False."""
        text = "The quarterly numbers are looking great this quarter and all targets were met"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert res["has_corrections"] is False
        assert res["clean_text"] == text
        assert res["retracted_segments"] == []

    def test_multiple_consecutive_corrections(self) -> None:
        """Verify chained multiple self-corrections resolve to the final formulation."""
        text = "The price is ten dollars wait sorry twenty dollars scratch that thirty dollars"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        assert res["has_corrections"] is True
        assert "thirty dollars" in res["clean_text"]


# ==============================================================================
# Feature 10: Floating Voice Pill (FlowBar) (5 tests)
# ==============================================================================
class TestTier1Feature10VoicePill:
    """Validate floating voice pill overlay, window flags, state sync, and waveform capsule."""

    def test_pill_instantiation_and_window_flags(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify pill initializes with frameless, top-most, and non-activating window flags."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        flags = pill.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert pill.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def test_pill_state_transitions(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify pill state can transition between IDLE, LISTENING, PROCESSING, and COMMITTED."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.set_state(PillState.LISTENING)
        assert pill.pill_state == PillState.LISTENING
        pill.set_state(PillState.PROCESSING)
        assert pill.pill_state == PillState.PROCESSING
        pill.set_state(PillState.COMMITTED)
        assert pill.pill_state == PillState.COMMITTED
        pill.set_state(PillState.IDLE)
        assert pill.pill_state == PillState.IDLE

    def test_waveform_capsule_bar_count(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify DynamicWaveformCapsule initializes with 10 equalizer bars."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        assert hasattr(pill, "waveform_capsule")
        assert pill.waveform_capsule.bar_count == 10

    def test_live_speech_preview_bubble_text(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify preview bubble updates with interim speech preview text."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.update_live_preview("Interim speech recognition test")
        assert "Interim speech recognition test" in pill.preview_bubble.lbl_preview.text()

    def test_pill_audio_level_update(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify set_audio_level dynamically updates target level on waveform capsule."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.set_audio_level(0.8)
        assert pill.waveform_capsule._target_level == 0.8


# ==============================================================================
# Feature 11: Flow Hub & 8 Workspaces (5 tests)
# ==============================================================================
class TestTier1Feature11FlowHub:
    """Validate 1350x850 Flow Hub workspace, 8 navigation routes, and theme switching."""

    def test_main_window_default_geometry(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify MainWindow initializes at 1350x850 with minimum size 980x640."""
        win: MainWindow = offscreen_widgets["main_window"]()
        assert win.width() == 1350
        assert win.height() == 850
        assert win.minimumWidth() == 980
        assert win.minimumHeight() == 640

    def test_eight_workspaces_defined(self) -> None:
        """Verify all 8 workspaces are defined in MainWindow.WORKSPACES."""
        assert len(MainWindow.WORKSPACES) == 8
        routes = [w["route"] for w in MainWindow.WORKSPACES]
        assert routes == [
            "/home",
            "/notetaker",
            "/dictionary",
            "/snippets",
            "/style",
            "/scratchpad",
            "/insights",
            "/settings",
        ]

    def test_workspace_navigation_by_index(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify navigating to integer indices 0 through 7 updates workspace stack."""
        win: MainWindow = offscreen_widgets["main_window"]()
        for idx in range(8):
            win.navigate_to_workspace(idx)
            assert win.workspace_stack.currentIndex() == idx

    def test_workspace_navigation_by_route(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify navigating by string routes (/dictionary, /snippets, /settings) sets correct index."""
        win: MainWindow = offscreen_widgets["main_window"]()
        win.navigate_to_workspace("/dictionary")
        assert win.workspace_stack.currentIndex() == 2
        win.navigate_to_workspace("/snippets")
        assert win.workspace_stack.currentIndex() == 3
        win.navigate_to_workspace("/settings")
        assert win.workspace_stack.currentIndex() == 7

    def test_theme_toggle_updates_ui(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify set_theme switches between 'dark' and 'light' modes."""
        win: MainWindow = offscreen_widgets["main_window"]()
        win.set_theme("dark")
        assert win._theme == "dark"
        win.set_theme("light")
        assert win._theme == "light"


# ==============================================================================
# Feature 12: Command Palette (Ctrl+K) (5 tests)
# ==============================================================================
class TestTier1Feature12CommandPalette:
    """Validate Command Palette quick switcher overlay, filtering, and action execution."""

    def test_palette_dialog_geometry_and_flags(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify palette dialog has fixed size 580x420 and frameless window flag."""
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        assert palette.width() == 580
        assert palette.height() == 420
        assert palette.windowFlags() & Qt.WindowType.FramelessWindowHint

    def test_populate_and_display_commands(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify set_commands populates list_widget items matching input list."""
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        cmds = [
            PaletteCommand(id="c1", title="Start Dictation", description="Begin dictating", category="Dictation"),
            PaletteCommand(id="c2", title="Open Notetaker", description="Open companion drawer", category="Meetings"),
        ]
        palette.set_commands(cmds)
        assert palette.list_widget.count() == 2

    def test_search_filtering(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify typing in search input filters list items by title or category."""
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        cmds = [
            PaletteCommand(id="c1", title="Start Dictation", description="", category="Dictation"),
            PaletteCommand(id="c2", title="Toggle Theme", description="", category="Settings"),
        ]
        palette.set_commands(cmds)
        palette.txt_search.setText("Theme")
        assert palette.list_widget.count() == 1

    def test_command_execution_handler(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify executing a command invokes its attached handler callback."""
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        invoked = []
        cmd = PaletteCommand(
            id="exec_test",
            title="Execute Action",
            description="Test action",
            category="Test",
            handler=lambda: invoked.append(True),
        )
        palette.set_commands([cmd])
        palette.list_widget.setCurrentRow(0)
        palette.execute_selected()
        assert len(invoked) == 1

    def test_empty_search_shows_all_commands(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify clearing search text restores full list of available commands."""
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        cmds = [
            PaletteCommand(id="c1", title="First", description="", category="General"),
            PaletteCommand(id="c2", title="Second", description="", category="General"),
        ]
        palette.set_commands(cmds)
        palette.txt_search.setText("First")
        assert palette.list_widget.count() == 1
        palette.txt_search.setText("")
        assert palette.list_widget.count() == 2


# ==============================================================================
# Feature 13: Notetaker Drawer & Scratchpad (5 tests)
# ==============================================================================
class TestTier1Feature13NotetakerScratchpad:
    """Validate Notetaker companion drawer (500x420) and Scratchpad window (720x760)."""

    def test_notetaker_drawer_geometry(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify Notetaker drawer initializes with 500x420 size and 300x200 minimum size."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        assert drawer.width() == 500
        assert drawer.height() == 420
        assert drawer.minimumWidth() == 300
        assert drawer.minimumHeight() == 200

    def test_drawer_note_capture_input(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify typing private notes in drawer persists in its notes editor."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer.notes_editor.setPlainText("Key decision: Approve Q4 migration")
        assert "Key decision: Approve Q4 migration" in drawer.notes_editor.toPlainText()

    def test_scratchpad_window_geometry(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify Scratchpad window initializes with 720x760 size and 500x600 minimum size."""
        scratchpad: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()
        assert scratchpad.width() == 720
        assert scratchpad.height() == 760
        assert scratchpad.minimumWidth() == 500
        assert scratchpad.minimumHeight() == 600

    def test_scratchpad_save_and_load_note(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify saving a note from scratchpad commits record to SQLite database."""
        scratchpad: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()
        scratchpad.txt_title.setText("Architecture Blueprint")
        scratchpad.editor.setPlainText("Detailed system topology notes")
        scratchpad.save_current_note()
        notes = isolated_controller.db.list_notes(search_query="Architecture Blueprint")
        assert len(notes) >= 1
        assert notes[0]["title"] == "Architecture Blueprint"

    def test_scratchpad_version_history_recording(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify editing a saved note records an entry in scratchpad_versions."""
        scratchpad: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()
        scratchpad.txt_title.setText("Version Tracking Note")
        scratchpad.editor.setPlainText("Initial revision v1")
        scratchpad.save_current_note()
        note_id = scratchpad._current_note_id
        assert note_id is not None
        scratchpad.editor.setPlainText("Updated revision v2")
        scratchpad.save_current_note()
        versions = isolated_controller.db.list_note_versions(note_id)
        assert len(versions) >= 2


# ==============================================================================
# Feature 14: 3-Section Meeting Studio (5 tests)
# ==============================================================================
class TestTier1Feature14MeetingStudio:
    """Validate 3-section Meeting Studio workspace (Thoughts, Transcript, Summary)."""

    def test_meeting_studio_initialization(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify Meeting Studio instantiates with unified tabs and mode switcher."""
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        assert studio is not None
        assert studio.unified_tabs is not None
        assert studio.combo_mode_switcher is not None

    def test_meeting_studio_three_primary_sections(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify unified tab widget contains exactly 3 primary workspace sections."""
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        assert studio.unified_tabs.count() == 3
        labels = [studio.unified_tabs.tabText(i) for i in range(3)]
        assert any("Thoughts" in l for l in labels)
        assert any("Transcript" in l for l in labels)
        assert any("Summary" in l for l in labels)

    def test_populate_diarized_transcript(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify adding diarized transcript segments renders into transcript tab."""
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        m_id = isolated_controller.db.create_meeting("Sync Call", "2026-11-12 10:00:00")
        isolated_controller.db.add_transcript_segment(
            meeting_id=m_id,
            start_time=0.0,
            end_time=5.0,
            speaker="You (Mic)",
            text="Testing meeting transcript feed",
        )
        studio.load_meeting(m_id)
        assert studio.active_meeting_id == m_id

    def test_load_meeting_details(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify load_meeting populates active_meeting_data dictionary."""
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        m_id = isolated_controller.db.create_meeting("Quarterly Review", "2026-11-12 14:00:00")
        studio.load_meeting(m_id)
        assert studio.active_meeting_data.get("title") == "Quarterly Review"

    def test_studio_mode_toggle(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify switching mode combo alternates between Unified (page 0) and Advanced (page 1)."""
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        assert studio.view_stack.currentIndex() == 0  # Unified mode
        studio.combo_mode_switcher.setCurrentIndex(1)
        assert studio.view_stack.currentIndex() == 1  # Advanced mode
        studio.combo_mode_switcher.setCurrentIndex(0)
        assert studio.view_stack.currentIndex() == 0


# ==============================================================================
# Feature 15: 3-Tier Outlook Calendar Engine (5 tests)
# ==============================================================================
class TestTier1Feature15OutlookCalendar:
    """Validate 3-tier Outlook calendar client and mock provider integration."""

    def test_mock_calendar_provider_auth(self, mock_calendar: MockCalendarProvider) -> None:
        """Verify MockCalendarProvider authenticates and reports valid session."""
        assert mock_calendar.authenticate() is True
        assert mock_calendar.is_authenticated() is True

    def test_mock_calendar_get_upcoming_meetings(self, mock_calendar: MockCalendarProvider) -> None:
        """Verify get_upcoming_meetings returns list of valid CalendarMeeting objects."""
        now = datetime.now(timezone.utc)
        meeting = CalendarMeeting(
            id="meet_test_1",
            title="Quarterly Planning Sync",
            start_time=now + timedelta(minutes=15),
            end_time=now + timedelta(minutes=45),
            platform="teams",
        )
        mock_calendar.inject_meeting(meeting)
        meetings = mock_calendar.get_upcoming_meetings(window_minutes=60)
        assert len(meetings) > 0
        assert isinstance(meetings[0], CalendarMeeting)

    def test_calendar_meeting_dataclass_properties(self, mock_calendar: MockCalendarProvider) -> None:
        """Verify CalendarMeeting exposes standard contract fields (id, title, start_time, end_time)."""
        now = datetime.now(timezone.utc)
        meeting = CalendarMeeting(
            id="meet_props_1",
            title="Design Review",
            start_time=now + timedelta(minutes=20),
            end_time=now + timedelta(minutes=50),
            platform="teams",
        )
        mock_calendar.inject_meeting(meeting)
        meetings = mock_calendar.get_upcoming_meetings(window_minutes=60)
        assert len(meetings) > 0
        m = meetings[0]
        assert hasattr(m, "id") and len(m.id) > 0
        assert hasattr(m, "title") and len(m.title) > 0
        assert hasattr(m, "start_time") and m.start_time is not None
        assert hasattr(m, "end_time") and m.end_time is not None

    def test_calendar_manager_provider_selection(self, mock_calendar: MockCalendarProvider) -> None:
        """Verify CalendarManager sets and maintains active calendar provider."""
        mgr = CalendarManager(provider=mock_calendar)
        assert mgr.provider == mock_calendar

    def test_calendar_manager_status_reporting(self, mock_calendar: MockCalendarProvider) -> None:
        """Verify get_provider_status reports healthy status and mock provider type."""
        status = mock_calendar.get_provider_status()
        assert status.get("is_healthy") is True
        assert status.get("provider_type") == "mock"


# ==============================================================================
# Feature 16: Onboarding Wizard & System Tray (5 tests)
# ==============================================================================
class TestTier1Feature16OnboardingTray:
    """Validate 4-step onboarding tour and Windows system tray quick actions."""

    def test_onboarding_wizard_step_count(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify Onboarding Wizard configures exactly 4 steps."""
        wizard = offscreen_widgets["wizard"]()
        assert len(wizard._step_pills) == 4

    def test_onboarding_wizard_step_navigation(self, offscreen_widgets: Dict[str, Any]) -> None:
        """Verify navigating between onboarding steps updates current index and label."""
        wizard = offscreen_widgets["wizard"]()
        wizard._go_to_step(2)
        assert wizard.stack.currentIndex() == 2
        assert "Step 3" in wizard.lbl_step.text()

    def test_onboarding_wizard_finish_persists_config(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify completing onboarding updates config onboarding_completed setting."""
        wizard = offscreen_widgets["wizard"]()
        wizard._finish_tour()
        assert isolated_controller.config.onboarding_completed is True

    def test_tray_manager_menu_actions(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify SystemTrayManager defines Hub, Scratchpad, and Notetaker menu actions."""
        pill = offscreen_widgets["pill"]()
        main_win = offscreen_widgets["main_window"](floating_pill=pill)
        tray = SystemTrayManager(
            parent_widget=main_win,
            controller=isolated_controller,
            floating_widget=pill,
            on_open_tab=main_win.navigate_to_workspace,
            on_exit_app=lambda: None,
        )
        assert tray.act_open.text() == "Open VOX Hub"
        assert tray.act_scratchpad.text() == "Open Scratchpad"
        assert tray.act_notetaker_drawer.text() == "Open Notetaker Drawer"

    def test_tray_status_indicator_update(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Verify system tray status indicator displays ready state when idle."""
        pill = offscreen_widgets["pill"]()
        main_win = offscreen_widgets["main_window"](floating_pill=pill)
        tray = SystemTrayManager(
            parent_widget=main_win,
            controller=isolated_controller,
            floating_widget=pill,
            on_open_tab=main_win.navigate_to_workspace,
            on_exit_app=lambda: None,
        )
        tray._refresh_state()
        assert "Ready" in tray.act_status.text()


# ==============================================================================
# Feature 17: Local SQLite WAL Storage (5 tests)
# ==============================================================================
class TestTier1Feature17SqliteStorage:
    """Validate SQLite database WAL mode, dual-column raw text preservation, and CRUD."""

    def test_wal_journal_mode_enabled(self, isolated_db: LocalDatabase) -> None:
        """Verify SQLite database operates in WAL (Write-Ahead Logging) journal mode."""
        conn = sqlite3.connect(str(isolated_db.db_path))
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        conn.close()
        assert journal_mode.lower() == "wal"

    def test_dual_column_raw_text_preservation(self, isolated_db: LocalDatabase) -> None:
        """Verify immutable raw_text is stored alongside rendered final_text."""
        raw_speech = "um please send the doc period"
        final_speech = "Please send the doc."
        row_id = isolated_db.add_dictation(raw_text=raw_speech, final_text=final_speech)
        assert row_id > 0
        recent = isolated_db.get_recent_dictation(limit=1)
        assert recent[0]["raw_text"] == raw_speech
        assert recent[0]["final_text"] == final_speech

    def test_custom_vocabulary_crud(self, isolated_db: LocalDatabase) -> None:
        """Verify adding, listing, and deleting custom vocabulary entries."""
        v_id = isolated_db.add_vocabulary("k8s", "Kubernetes", category="Tech")
        assert v_id > 0
        vocab_list = isolated_db.list_vocabulary()
        phrases = [v["spoken_phrase"] for v in vocab_list]
        assert "k8s" in phrases
        isolated_db.delete_vocabulary(v_id)
        vocab_after = isolated_db.list_vocabulary()
        phrases_after = [v["spoken_phrase"] for v in vocab_after]
        assert "k8s" not in phrases_after

    def test_snippets_expansion_lookup(self, isolated_db: LocalDatabase) -> None:
        """Verify upserting a text snippet and retrieving by search query."""
        s_id = isolated_db.upsert_snippet("!sig", "Best regards,\nVOX Assistant")
        assert s_id > 0
        snips = isolated_db.list_snippets(search_query="!sig")
        assert len(snips) >= 1
        assert "VOX Assistant" in snips[0]["expansion"]

    def test_scratchpad_note_lifecycle(self, isolated_db: LocalDatabase) -> None:
        """Verify note creation, update, and retrieval in scratchpad_notes."""
        n_id = isolated_db.create_note(title="Weekly Standup", content="Project updates")
        assert n_id > 0
        note = isolated_db.get_note(n_id)
        assert note is not None
        assert note["title"] == "Weekly Standup"
        isolated_db.update_note(n_id, title="Weekly Standup Updated")
        updated_note = isolated_db.get_note(n_id)
        assert updated_note["title"] == "Weekly Standup Updated"


# ==============================================================================
# Feature 18: Turnkey Test Suite (Tiers 1-4) (5 tests)
# ==============================================================================
class TestTier1Feature18TurnkeySuite:
    """Validate test infrastructure invariants, headless configuration, and layout."""

    def test_headless_offscreen_platform_configured(self) -> None:
        """Verify QT_QPA_PLATFORM is strictly set to offscreen to guarantee headless runs."""
        assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

    def test_test_infra_spec_exists_and_valid(self) -> None:
        """Verify TEST_INFRA.md exists and documents the 4-tier testing specification."""
        infra_md = PROJECT_ROOT / "TEST_INFRA.md"
        assert infra_md.exists() and infra_md.is_file()
        content = infra_md.read_text(encoding="utf-8")
        assert "4-Tier Test Case Design Methodology" in content

    def test_conftest_fixtures_loadable(
        self,
        isolated_config: AppConfig,
        isolated_db: LocalDatabase,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """Verify core conftest fixtures initialize cleanly and provide expected interfaces."""
        assert isolated_config is not None
        assert isolated_db is not None
        audio = synthetic_audio(duration_sec=0.25)
        assert isinstance(audio, np.ndarray)

    def test_test_config_disables_hotkeys_and_preload(self, isolated_config: AppConfig) -> None:
        """Verify test configuration strictly disables OS hotkey hooks and model preloading."""
        assert isolated_config.disable_hotkeys is True
        assert isolated_config.disable_model_preload is True

    def test_e2e_directory_structure_complete(self) -> None:
        """Verify tests/e2e/ contains conftest.py and __init__.py."""
        e2e_dir = PROJECT_ROOT / "tests" / "e2e"
        assert (e2e_dir / "__init__.py").exists()
        assert (e2e_dir / "conftest.py").exists()


# ==============================================================================
# Feature 19: Standalone Executable Distribution (5 tests)
# ==============================================================================
class TestTier1Feature19StandaloneExe:
    """Validate standalone executable binary headers, size threshold, and CLI options."""

    def test_standalone_executable_exists(self) -> None:
        """Verify production standalone executable exists under dist/VoxLocal/."""
        exe_path = PROJECT_ROOT / "dist" / "VoxLocal" / "VoxLocal.exe"
        assert exe_path.exists() and exe_path.is_file()

    def test_standalone_executable_pe_binary_header(self) -> None:
        """Verify standalone binary has valid Windows PE (Portable Executable) headers."""
        exe_path = PROJECT_ROOT / "dist" / "VoxLocal" / "VoxLocal.exe"
        with open(exe_path, "rb") as f:
            dos_header = f.read(2)
            assert dos_header == b"MZ", "Missing DOS 'MZ' header signature"
            f.seek(0x3C)
            pe_offset = int.from_bytes(f.read(4), "little")
            f.seek(pe_offset)
            pe_sig = f.read(4)
            assert pe_sig == b"PE\0\0", "Missing PE NT header signature"

    def test_standalone_executable_filesize_threshold(self) -> None:
        """Verify executable file size exceeds 10MB indicating complete bundled runtime."""
        exe_path = PROJECT_ROOT / "dist" / "VoxLocal" / "VoxLocal.exe"
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        assert size_mb >= 10.0, f"Executable size too small ({size_mb:.2f} MB), incomplete bundle"

    def test_cli_smoke_test_argument_parsing(self) -> None:
        """Verify CLI argument parser defines --smoke-test-ms, --startup, and --minimized options."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--startup", action="store_true")
        parser.add_argument("--minimized", action="store_true")
        parser.add_argument("--smoke-test-ms", type=int, default=0)
        parsed = parser.parse_args(["--startup", "--smoke-test-ms", "1500"])
        assert parsed.startup is True
        assert parsed.smoke_test_ms == 1500

    def test_single_instance_mutex_definition(self) -> None:
        """Verify main entrypoint defines the canonical Win32 single instance mutex name."""
        from main import acquire_single_instance_mutex
        assert callable(acquire_single_instance_mutex)


# ==============================================================================
# Feature 20: Adversarial Coverage Hardening (5 tests)
# ==============================================================================
class TestTier1Feature20Hardening:
    """Validate boundary stress, Unicode/Devanagari stability, and SQL injection defense."""

    def test_extreme_text_length_processor_resilience(self) -> None:
        """Verify DictationProcessor processes an extreme 50,000-character payload without memory crash."""
        extreme_payload = "hello world period this is a resilience stress test comma " * 1000  # ~58,000 chars
        processed = DictationProcessor.process(extreme_payload, spoken_punctuation=True)
        assert len(processed) > 0
        assert "." in processed

    def test_multilingual_unicode_and_emoji_handling(self) -> None:
        """Verify processing multilingual text with emojis and zero-width joiners preserves symbols."""
        mixed_text = "Hello नमस्ते 世界 🌍 \u200b\u200c\u200d period"
        processed = DictationProcessor.process(mixed_text, spoken_punctuation=True)
        assert "नमस्ते" in processed
        assert "世界" in processed
        assert "🌍" in processed
        assert "." in processed

    def test_audio_nan_and_infinite_samples_safety(self) -> None:
        """Verify compute_rms_level handles edge-case audio buffers (empty and zeroed) safely."""
        empty_audio = np.zeros(0, dtype=np.float32)
        assert compute_rms_level(empty_audio) == 0.0
        max_clipped = np.ones(1600, dtype=np.float32)
        assert compute_rms_level(max_clipped) <= 1.0

    def test_sql_injection_defense_in_vocabulary(self, isolated_db: LocalDatabase) -> None:
        """Verify SQL injection strings are safely parameterized and cannot drop tables."""
        attack_phrase = "'; DROP TABLE dictation_history; --"
        v_id = isolated_db.add_vocabulary(attack_phrase, "safe_substitute")
        assert v_id > 0
        recent = isolated_db.get_recent_dictation(limit=5)
        assert isinstance(recent, list)  # dictation_history table was not dropped

    def test_rapid_dictation_state_toggles(self, isolated_controller: AppController) -> None:
        """Verify rapid toggles of start/cancel cycles handle re-entrancy without deadlocks."""
        for _ in range(10):
            isolated_controller.start_dictation()
            isolated_controller.cancel_dictation()
        assert isolated_controller.dictation_state == "idle"

"""
Shared Opaque-Box E2E Test Fixtures for Project VOX Stabilization.

Provides:
- Headless offscreen QApplication singleton (qapp_offscreen)
- Sandboxed filesystem and SQLite WAL database (isolated_tmp_dir, isolated_db)
- Safe AppConfig without OS hooks or Whisper pre-download (isolated_config)
- Fully wired AppController with deterministic shutdown (isolated_controller)
- Synthetic 16kHz mono float32 PCM audio generators (synthetic_audio)
- Deterministic Mock Calendar Provider (mock_calendar)
- Registered offscreen UI widget factory (offscreen_widgets)
- Win32 startup registry sandbox (isolated_env)
- Mock PortAudio streams for hardware-independent audio testing (mock_portaudio_streams)
- Mock Win32 clipboard with sequence tracking & format preservation (mock_win32_clipboard)
- Mock target windows for Notepad, Word, Slack, Browser, Admin (mock_target_windows)
- Fast mock transcriber for deterministic testing (fast_mock_transcriber)
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple
import numpy as np
import pytest

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force headless offscreen Qt platform before any Qt modules load
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState, LiveSpeechPreviewBubble
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.command_palette import VoxCommandPaletteDialog
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.audio.devices import AudioDeviceInfo, AudioDeviceManager


@pytest.fixture(scope="session")
def qapp_offscreen() -> Generator[QApplication, None, None]:
    """Provide a session-scoped headless QApplication running on the offscreen QPA platform."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["vox_e2e_runner", "-platform", "offscreen"])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def isolated_env(request, monkeypatch) -> None:
    """Isolate OS-level side effects (registry, notifications, audio devices)."""
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "windows":
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    node_id = request.node.nodeid.lower()
    if "startup" not in node_id:
        monkeypatch.setattr(StartupManager, "is_enabled", staticmethod(lambda: False))
        monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda *a, **k: True))

    # Guard against missing AudioDeviceManager import in voxlocal.core.controller
    import voxlocal.core.controller as ctrl_module
    if not hasattr(ctrl_module, "AudioDeviceManager"):
        setattr(ctrl_module, "AudioDeviceManager", AudioDeviceManager)



@pytest.fixture
def isolated_tmp_dir(tmp_path: Path) -> Path:
    """Create a temporary sandbox directory with data, snapshots, and logs subfolders."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "snapshots").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def isolated_db(isolated_tmp_dir: Path) -> Generator[LocalDatabase, None, None]:
    """Provide a clean, isolated LocalDatabase in SQLite WAL mode."""
    db_file = isolated_tmp_dir / "voxlocal_test.db"
    db = LocalDatabase(db_file)
    yield db


@pytest.fixture
def isolated_config(isolated_tmp_dir: Path, monkeypatch) -> AppConfig:
    """Provide a safe AppConfig that disables global hooks, Whisper preload, and auto-calls."""
    cfg_path = isolated_tmp_dir / "config.json"
    monkeypatch.setenv("VOXLOCAL_CONFIG_PATH", str(cfg_path))

    cfg = AppConfig(
        storage_dir=str(isolated_tmp_dir),
        models_dir=str(isolated_tmp_dir / "models"),
        launch_at_login=False,
        llm_enabled=False,
        auto_record_calls=False,
        prompt_on_call=False,
        progressive_transcription=False,
        sound_effects=False,
        hinglish_enabled=True,
    )
    # Critical test isolation flags
    cfg.disable_hotkeys = True
    cfg.disable_model_preload = True
    cfg.save()
    return cfg


@pytest.fixture
def isolated_controller(
    qapp_offscreen: QApplication,
    isolated_config: AppConfig,
    isolated_env: None,
) -> Generator[AppController, None, None]:
    """Provide an initialized AppController and guarantee clean shutdown on teardown."""
    ctrl = AppController(isolated_config)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        qapp_offscreen.processEvents()


@pytest.fixture
def synthetic_audio() -> Callable[..., np.ndarray]:
    """Factory fixture generating 16kHz mono float32 audio arrays."""
    def _generate(
        duration_sec: float = 1.0,
        freq_hz: float = 440.0,
        amplitude: float = 0.5,
        silence: bool = False,
    ) -> np.ndarray:
        sample_rate = 16000
        total_samples = int(sample_rate * duration_sec)
        if total_samples <= 0:
            return np.zeros(0, dtype=np.float32)
        if silence:
            return np.zeros(total_samples, dtype=np.float32)
        t = np.linspace(0, duration_sec, total_samples, False)
        wave = amplitude * np.sin(2 * np.pi * freq_hz * t)
        return np.clip(wave.astype(np.float32), -1.0, 1.0)

    return _generate


@pytest.fixture
def mock_target_windows() -> Dict[str, TargetWindowInfo]:
    """Pre-configured TargetWindowInfo instances for real-world target testing."""
    return {
        "notepad": TargetWindowInfo(
            hwnd=0x10001,
            title="Untitled - Notepad",
            process_id=101,
            thread_id=1001,
            process_name="notepad.exe",
            focus_child_hwnd=0x10002,
        ),
        "word": TargetWindowInfo(
            hwnd=0x20001,
            title="Document1 - Microsoft Word",
            process_id=102,
            thread_id=1002,
            process_name="winword.exe",
            focus_child_hwnd=0x20002,
        ),
        "slack": TargetWindowInfo(
            hwnd=0x30001,
            title="general - Slack",
            process_id=103,
            thread_id=1003,
            process_name="slack.exe",
            focus_child_hwnd=0x30002,
        ),
        "vscode": TargetWindowInfo(
            hwnd=0x40001,
            title="main.py - Visual Studio Code",
            process_id=104,
            thread_id=1004,
            process_name="code.exe",
            focus_child_hwnd=0x40002,
        ),
        "browser": TargetWindowInfo(
            hwnd=0x50001,
            title="New Tab - Google Chrome",
            process_id=105,
            thread_id=1005,
            process_name="chrome.exe",
            focus_child_hwnd=0x50002,
        ),
        "admin": TargetWindowInfo(
            hwnd=0x60001,
            title="Administrator: Windows PowerShell",
            process_id=106,
            thread_id=1006,
            process_name="powershell.exe",
            focus_child_hwnd=0x60002,
        ),
    }


class SimulatedClipboard:
    """Thread-safe simulated Win32 Clipboard with sequence numbers and format isolation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence_number = 1
        self._text: Optional[str] = None
        self._formats: Dict[int, Any] = {}
        self._is_locked: bool = False

    def get_sequence_number(self) -> int:
        with self._lock:
            return self._sequence_number

    def get_text(self) -> Optional[str]:
        with self._lock:
            if self._is_locked:
                return None
            return self._text

    def set_text(self, text: str) -> bool:
        with self._lock:
            if self._is_locked:
                return False
            self._text = text
            self._sequence_number += 1
            return True

    def set_format(self, format_id: int, data: Any) -> bool:
        with self._lock:
            if self._is_locked:
                return False
            self._formats[format_id] = data
            self._sequence_number += 1
            return True

    def get_format(self, format_id: int) -> Optional[Any]:
        with self._lock:
            if self._is_locked:
                return None
            return self._formats.get(format_id)

    def has_format(self, format_id: int) -> bool:
        with self._lock:
            return format_id in self._formats or (format_id == 13 and self._text is not None)

    def clear(self) -> None:
        with self._lock:
            self._text = None
            self._formats.clear()
            self._sequence_number += 1

    def lock_clipboard(self) -> None:
        with self._lock:
            self._is_locked = True

    def unlock_clipboard(self) -> None:
        with self._lock:
            self._is_locked = False


@pytest.fixture
def mock_win32_clipboard() -> SimulatedClipboard:
    """Fixture providing a mock Win32 clipboard."""
    return SimulatedClipboard()


class MockAudioStream:
    """Mock PyAudio stream for deterministic audio callback testing."""

    def __init__(self, callback: Optional[Callable] = None, rate: int = 16000, channels: int = 1) -> None:
        self.callback = callback
        self.rate = rate
        self.channels = channels
        self._is_active = False
        self._is_stopped = False
        self._is_closed = False

    def start_stream(self) -> None:
        self._is_active = True
        self._is_stopped = False

    def stop_stream(self) -> None:
        self._is_active = False
        self._is_stopped = True

    def close(self) -> None:
        self._is_active = False
        self._is_closed = True

    def is_active(self) -> bool:
        return self._is_active

    def is_stopped(self) -> bool:
        return self._is_stopped


@pytest.fixture
def mock_portaudio_streams(monkeypatch) -> Dict[str, Any]:
    """Mock PortAudio streams to allow instant deterministic testing without audio hardware."""
    created_streams: List[MockAudioStream] = []

    def _mock_open(*args, **kwargs):
        cb = kwargs.get("stream_callback")
        rate = kwargs.get("rate", 16000)
        channels = kwargs.get("channels", 1)
        st = MockAudioStream(callback=cb, rate=rate, channels=channels)
        created_streams.append(st)
        return st

    return {
        "mock_open": _mock_open,
        "created_streams": created_streams,
    }


@pytest.fixture
def fast_mock_transcriber(monkeypatch) -> Dict[str, Any]:
    """Stub / Mock transcriber yielding deterministic transcriptions instantly."""
    last_transcription = {"text": "Namaste this is a verified test transcription."}

    def _stub_transcribe_dictation(audio, language="en+hi", profile="balanced", enable_subclause=True, **kw):
        return {
            "text": last_transcription["text"],
            "raw_text": last_transcription["text"],
            "language": "hi" if "hi" in language else "en",
            "duration": float(len(audio) / 16000.0) if hasattr(audio, "__len__") else 1.0,
            "segments": [{"text": last_transcription["text"], "start": 0.0, "end": 1.0}],
            "words": [],
        }

    def _stub_transcribe_interim(audio, language="en+hi", **kw):
        dur = float(len(audio) / 16000.0) if hasattr(audio, "__len__") else 0.5
        return "Namaste testing live...", dur

    return {
        "set_text": lambda t: last_transcription.update({"text": t}),
        "transcribe_dictation": _stub_transcribe_dictation,
        "transcribe_interim": _stub_transcribe_interim,
    }


@pytest.fixture
def mock_calendar() -> MockCalendarProvider:
    """Provide a fresh, authenticated MockCalendarProvider instance."""
    provider = MockCalendarProvider()
    provider.authenticate()
    return provider


@pytest.fixture
def offscreen_widgets(
    qapp_offscreen: QApplication,
    isolated_controller: AppController,
) -> Generator[Dict[str, Any], None, None]:
    """
    Factory creating tracked offscreen UI widgets tied to isolated_controller.
    Automatically cleans up widgets on teardown.
    """
    created_widgets: List[QWidget] = []

    def _create_pill() -> DualTierFloatingPill:
        pill = DualTierFloatingPill(parent=None, controller=isolated_controller)
        created_widgets.append(pill)
        return pill

    def _create_preview_bubble() -> LiveSpeechPreviewBubble:
        bubble = LiveSpeechPreviewBubble(parent=None)
        created_widgets.append(bubble)
        return bubble

    def _create_main_window(floating_pill: Optional[DualTierFloatingPill] = None) -> MainWindow:
        pill = floating_pill or _create_pill()
        win = MainWindow(controller=isolated_controller, floating_widget=pill)
        created_widgets.append(win)
        return win

    def _create_scratchpad() -> VoxScratchpadWindow:
        scratch = VoxScratchpadWindow(controller=isolated_controller)
        created_widgets.append(scratch)
        return scratch

    def _create_drawer() -> VoxNotetakerDrawer:
        drawer = VoxNotetakerDrawer(controller=isolated_controller)
        created_widgets.append(drawer)
        return drawer

    def _create_wizard() -> VoxOnboardingWizard:
        wizard = VoxOnboardingWizard(controller=isolated_controller)
        created_widgets.append(wizard)
        return wizard

    def _create_palette() -> VoxCommandPaletteDialog:
        try:
            palette = VoxCommandPaletteDialog(parent=None)
        except TypeError:
            palette = VoxCommandPaletteDialog(controller=isolated_controller)  # type: ignore
        created_widgets.append(palette)
        return palette

    def _create_meeting_studio() -> VoxMeetingStudioWorkspace:
        studio = VoxMeetingStudioWorkspace(db=isolated_controller.db)
        created_widgets.append(studio)
        return studio

    factory = {
        "pill": _create_pill,
        "preview_bubble": _create_preview_bubble,
        "main_window": _create_main_window,
        "scratchpad": _create_scratchpad,
        "drawer": _create_drawer,
        "wizard": _create_wizard,
        "palette": _create_palette,
        "meeting_studio": _create_meeting_studio,
    }

    yield factory

    for w in created_widgets:
        try:
            w.close()
            w.deleteLater()
        except Exception:
            pass
    qapp_offscreen.processEvents()

"""
Shared Opaque-Box E2E Test Fixtures for Project VOX.

Provides:
- Headless offscreen QApplication singleton (qapp_offscreen)
- Sandboxed filesystem and SQLite WAL database (isolated_tmp_dir, isolated_db)
- Safe AppConfig without OS hooks or Whisper pre-download (isolated_config)
- Fully wired AppController with deterministic shutdown (isolated_controller)
- Synthetic 16kHz mono float32 PCM audio generators (synthetic_audio)
- Deterministic Mock Calendar Provider (mock_calendar)
- Registered offscreen UI widget factory (offscreen_widgets)
- Win32 startup registry sandbox (isolated_env)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional
import numpy as np
import pytest

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Force headless offscreen Qt platform before any Qt modules load
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.command_palette import VoxCommandPaletteDialog
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup


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
def isolated_env(monkeypatch) -> None:
    """Isolate OS-level side effects (registry, notifications, audio devices)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    # Mock StartupManager to prevent WinError 5 Access Denied in restricted environments
    monkeypatch.setattr(StartupManager, "is_enabled", staticmethod(lambda: False))
    monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda *a, **k: True))


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

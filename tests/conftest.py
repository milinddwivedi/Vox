"""
Global pytest teardown safeguards for Windows Qt / Win32 / SQLite test isolation.

Legacy test modules sometimes construct AppController, GlobalHotkeyManager,
LocalDatabase, or QWidgets without calling shutdown()/close(). Cleaning them up
after each test prevents cross-test access violations and Windows file-lock
PermissionError failures on temporary databases.
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VOX_DISABLE_TEST_PRELOAD", "1")

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="session", autouse=True)
def _ensure_session_qapplication():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest"])
    yield app


@pytest.fixture(autouse=True)
def _cleanup_leaked_vox_resources():
    yield

    # 1. Shut down any AppController instances not already stopped by the test
    try:
        from voxlocal.core.controller import _ACTIVE_CONTROLLERS

        for ctrl in list(_ACTIVE_CONTROLLERS):
            if not getattr(ctrl, "_shutdown_done", False):
                try:
                    ctrl.shutdown()
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Unhook any stray Win32 WH_KEYBOARD_LL hooks and WinEvent listeners
    try:
        from voxlocal.win32.hotkeys import _cleanup_all_hotkeys

        _cleanup_all_hotkeys()
    except Exception:
        pass
    try:
        from voxlocal.audio.call_detector import _cleanup_all_winevent_hooks

        _cleanup_all_winevent_hooks()
    except Exception:
        pass

    # 3. Close any stray LocalDatabase / UsageMeteringEngine SQLite handles
    try:
        from voxlocal.storage.database import _ACTIVE_DATABASES

        for db in list(_ACTIVE_DATABASES):
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        pass

    # 4. Close stray top-level Qt widgets and flush DeferredDelete events
    try:
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for w in list(app.topLevelWidgets()):
                try:
                    if hasattr(w, "prepare_force_quit"):
                        w.prepare_force_quit()
                    w.close()
                    w.deleteLater()
                except Exception:
                    pass
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    except Exception:
        pass

    gc.collect()

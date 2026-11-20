"""
VoxLocal — Local-First AI Voice Assistant for Windows.
Main Application Entry Point.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.machinery
import os
from pathlib import Path
import sys

# Ensure 'src' is on sys.path (and prioritized ahead of PyiFrozenImporter when frozen)
ROOT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) in sys.path:
    sys.path.remove(str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR))
if getattr(sys, "frozen", False) and (SRC_DIR / "voxlocal").exists():
    if not sys.meta_path or sys.meta_path[0] is not importlib.machinery.PathFinder:
        sys.meta_path.insert(0, importlib.machinery.PathFinder)

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if "--print-version" not in sys.argv:
    from voxlocal.core.config import AppConfig

if "--print-version" not in sys.argv and "--hardware-diagnostics" not in sys.argv:
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from voxlocal.core.controller import AppController
    from voxlocal.core.logger import get_logger, setup_logging
    from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup
    from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
    from voxlocal.ui.main_window import MainWindow
    from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
    from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
    from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
    from voxlocal.ui.theme import create_app_icon
    from voxlocal.ui.tray_manager import SystemTrayManager

ERROR_ALREADY_EXISTS = 183


def acquire_single_instance_mutex():
    """
    Create a Win32 named mutex to prevent duplicate background instances and shortcut conflicts.
    Returns `(mutex_handle, already_running)`.
    """
    kernel32 = ctypes.windll.kernel32
    mutex_name = "Local\\VoxLocal_AI_Voice_Assistant_Mutex_v1"
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    last_err = kernel32.GetLastError()
    return handle, (last_err == ERROR_ALREADY_EXISTS)


def set_windows_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VoxLocal.AIVoiceAssistant.Desktop.1"
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="VoxLocal — Local-First AI Voice Assistant")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Launched at Windows login (respects start-minimized setting)",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized in system tray with floating widget",
    )
    parser.add_argument(
        "--smoke-test-ms",
        type=int,
        default=0,
        help="Run full GUI initialization and exit automatically after N milliseconds (for automated testing)",
    )
    parser.add_argument(
        "--auth-url",
        type=str,
        default="",
        help="Deep link authorization payload or redirect URL (from voxlocal:// protocol)",
    )
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="Print the application version and exit immediately",
    )
    parser.add_argument(
        "--hardware-diagnostics",
        action="store_true",
        help="Print portable JSON hardware & adaptive ASR tier diagnostics and exit",
    )
    parser.add_argument(
        "--diagnostics-out",
        type=str,
        default="",
        help="Optional output file path when running with --hardware-diagnostics",
    )
    args = parser.parse_args()

    if getattr(args, "print_version", False):
        from voxlocal import __version__

        print(__version__)
        return 0

    if getattr(args, "hardware_diagnostics", False):
        import json
        from voxlocal.core.hardware import HardwareProfiler

        cfg = AppConfig.load()
        diag = HardwareProfiler.run_hardware_diagnostics(run_short_benchmark=False, config=cfg)
        rendered = json.dumps(diag, indent=2, ensure_ascii=False)
        out_path = getattr(args, "diagnostics_out", "") or ""
        if out_path:
            out_p = Path(out_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    setup_logging()
    logger = get_logger("main")
    set_windows_app_id()

    # Install top-level crash prevention and exception telemetry for Qt
    def _global_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.error(
            "Unhandled exception caught by global hook (preventing fatal abort):",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = _global_exception_handler

    def _qt_message_handler(mode: QtMsgType, context, message: str):
        msg_clean = (message or "").strip()
        loc = f" ({context.file}:{context.line})" if context.file else ""
        if mode == QtMsgType.QtWarningMsg:
            logger.warning("[Qt Warning] %s%s", msg_clean, loc)
        elif mode == QtMsgType.QtCriticalMsg:
            logger.error("[Qt Critical] %s%s", msg_clean, loc)
        elif mode == QtMsgType.QtFatalMsg:
            logger.critical("[Qt Fatal] %s%s", msg_clean, loc)
        else:
            logger.debug("[Qt] %s%s", msg_clean, loc)

    qInstallMessageHandler(_qt_message_handler)

    mutex_handle, already_running = acquire_single_instance_mutex()
    app = QApplication(sys.argv)
    app.setApplicationName("VoxLocal")
    app.setOrganizationName("VoxLocal")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(create_app_icon("idle"))

    if already_running and args.smoke_test_ms <= 0:
        logger.info("Another instance of VoxLocal is already running; bringing it to the front...")
        if getattr(args, "auth_url", None):
            try:
                import urllib.parse
                import urllib.request
                auth_val = getattr(args, "auth_url", "")
                query_str = urllib.parse.urlsplit(auth_val).query or auth_val
                sync_url = f"http://127.0.0.1:8400/sync-session?{query_str}"
                urllib.request.urlopen(sync_url, timeout=1.5)
            except Exception as fwd_exc:
                logger.debug("Could not forward auth-url to running instance: %s", fwd_exc)
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        hwnd = user32.FindWindowW(None, "VoxLocal - Local AI Voice Assistant")
        if not hwnd:
            hwnd = user32.FindWindowW(None, "VoxLocal — Local AI Voice Assistant")
        if not hwnd:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            found = [0]

            def _enum_win(h, _):
                length = user32.GetWindowTextLengthW(h)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(h, buf, length + 1)
                    if "VoxLocal" in buf.value:
                        found[0] = h
                        return False
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_win), 0)
            hwnd = found[0]

        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        return 0

    config = AppConfig.load()
    controller = AppController(config=config)
    if getattr(args, "auth_url", None):
        controller.complete_login_from_url(args.auth_url)

    main_win_ref = []
    tray_ref = []

    def open_dashboard_tab(tab_idx: int = 0, maximize: bool = False) -> None:
        if main_win_ref:
            main_win_ref[0].open_and_focus_tab(tab_idx, maximize=maximize)

    def exit_application() -> None:
        logger.info("User requested complete application exit.")
        if main_win_ref:
            main_win_ref[0].prepare_force_quit()
            main_win_ref[0].close()
        if tray_ref:
            tray_ref[0].hide()
        floating.hide()
        controller.shutdown()
        app.quit()

    def handle_finalize() -> None:
        """Finalize and insert transformed text into currently focused external application."""
        if hasattr(controller, "finalize_active_dictation"):
            controller.finalize_active_dictation()
        elif controller.is_hands_free_active:
            controller.stop_hands_free_typing()
        elif controller.dictation_state in ("starting", "listening", "speaking", "processing"):
            controller.stop_dictation_and_insert()

    def handle_cancel() -> None:
        """Cancel dictation session and discard provisional audio buffer."""
        if hasattr(controller, "cancel_dictation"):
            controller.cancel_dictation()
        elif controller.is_hands_free_active:
            controller.stop_hands_free_typing()

    floating = DualTierFloatingPill(
        parent=None,
        theme=getattr(controller, "theme", config.theme),
        on_finalize=handle_finalize,
        on_cancel=handle_cancel,
        controller=controller,
        on_open_dashboard=open_dashboard_tab,
        on_exit_app=exit_application,
    )
    controller.floating_pill = floating
    main_window = MainWindow(
        controller=controller,
        floating_widget=floating,
        on_exit_app=exit_application,
    )
    main_win_ref.append(main_window)

    # Secondary Wispr-parity floating surfaces & onboarding tour
    scratchpad_win = VoxScratchpadWindow(
        controller=controller,
        on_open_hub_scratchpad=lambda: open_dashboard_tab("scratchpad"),
    )
    notetaker_drawer = VoxNotetakerDrawer(
        controller=controller,
        on_open_meeting_studio=lambda _mid=None: open_dashboard_tab("notetaker"),
    )

    def _on_drawer_seek(meeting_id: int, sec: float) -> None:
        open_dashboard_tab("notetaker")
        if meeting_id and hasattr(main_window, "notetaker_page"):
            main_window.notetaker_page.select_meeting(int(meeting_id))
            main_window.notetaker_page._seek_to_seconds(float(sec))

    notetaker_drawer.seek_requested.connect(_on_drawer_seek)
    cal_prompt = VoxMeetingPromptPopup(theme=getattr(controller, "theme", "light"))
    onboarding_ref: list[VoxOnboardingWizard] = []

    def _toggle_or_show_scratchpad() -> None:
        if scratchpad_win.isVisible() and scratchpad_win.isActiveWindow():
            scratchpad_win.hide()
        else:
            scratchpad_win.load_initial_note()
            scratchpad_win.show()
            scratchpad_win.raise_()
            scratchpad_win.activateWindow()

    def _open_notetaker_drawer() -> None:
        notetaker_drawer.show()
        notetaker_drawer.raise_()
        notetaker_drawer.activateWindow()

    def _launch_onboarding_tour() -> None:
        if not onboarding_ref:
            wiz = VoxOnboardingWizard(controller=controller)
            controller.theme_changed.connect(wiz.apply_theme)
            onboarding_ref.append(wiz)
        wiz = onboarding_ref[0]
        wiz.show()
        wiz.raise_()
        wiz.activateWindow()

    if hasattr(controller, "open_scratchpad_requested"):
        controller.open_scratchpad_requested.connect(_toggle_or_show_scratchpad)
    if hasattr(controller, "open_notetaker_drawer_requested"):
        controller.open_notetaker_drawer_requested.connect(_open_notetaker_drawer)
    controller.theme_changed.connect(scratchpad_win.apply_theme)
    controller.theme_changed.connect(notetaker_drawer.apply_theme)
    controller.theme_changed.connect(cal_prompt.apply_theme)
    controller.meeting_state_changed.connect(
        lambda st: _open_notetaker_drawer()
        if st == "recording" and getattr(config, "notetaker_notepad_on_start", "open") == "open"
        else None
    )
    from PyQt6.QtCore import Qt
    controller.meeting_saved_and_transcribed.connect(
        main_window.handle_meeting_saved_and_transcribed,
        Qt.ConnectionType.QueuedConnection,
    )
    cal_prompt.start_capture_requested.connect(
        lambda mtg: controller.start_meeting_recording(title=getattr(mtg, "title", "Scheduled Meeting"))
    )
    main_window.replay_tour_requested.connect(_launch_onboarding_tour)

    tray = SystemTrayManager(
        parent_widget=main_window,
        controller=controller,
        floating_widget=floating,
        on_open_tab=open_dashboard_tab,
        on_exit_app=exit_application,
        on_open_scratchpad=_toggle_or_show_scratchpad,
        on_open_notetaker=_open_notetaker_drawer,
    )
    tray_ref.append(tray)

    # Show Floating Widget if enabled
    if config.show_floating_widget:
        floating.show()
        main_window._update_float_btn_text()

    # Show Main Window unless started with --minimized or (--startup and start_minimized_to_tray)
    start_hidden = args.minimized or (args.startup and config.start_minimized_to_tray)
    if not start_hidden:
        main_window.show()
        if args.smoke_test_ms <= 0 and not getattr(config, "onboarding_completed", False):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(350, _launch_onboarding_tour)

    if args.smoke_test_ms > 0:
        from PyQt6.QtCore import QTimer

        logger.info("Running smoke test for %d ms...", args.smoke_test_ms)
        QTimer.singleShot(args.smoke_test_ms, exit_application)

    exit_code = app.exec()
    if mutex_handle:
        ctypes.windll.kernel32.CloseHandle(mutex_handle)
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())

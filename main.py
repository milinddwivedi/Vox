"""
VoxLocal — Local-First AI Voice Assistant for Windows.
Main Application Entry Point.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.machinery
import multiprocessing
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


def _run_hardware_diagnostics(out_path: str) -> int:
    import json
    from voxlocal.core.config import AppConfig
    from voxlocal.core.hardware import HardwareProfiler

    cfg = AppConfig.load()
    diag = HardwareProfiler.run_hardware_diagnostics(run_short_benchmark=False, config=cfg)
    rendered = json.dumps(diag, indent=2, ensure_ascii=False)
    if out_path:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _run_health_check(verify_hashes: bool = False) -> int:
    import json
    from voxlocal.core.config import AppConfig
    from voxlocal.core.first_run_health import run_first_run_health_check

    cfg = AppConfig.load()
    report = run_first_run_health_check(config=cfg, verify_hashes=verify_hashes)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("runtime_ok") and report.get("manifest_compatible") else 2


def _run_verify_runtime(expected_version: str = "") -> int:
    import importlib
    import json
    from voxlocal import __file__ as vox_init_file, __version__

    checks: dict[str, bool] = {}
    errors: list[str] = []
    required_modules = (
        "voxlocal.core.controller",
        "voxlocal.engine.meeting_asr_pipeline",
        "voxlocal.engine.meeting_model_manager",
        "voxlocal.engine.llama_server_engine",
        "voxlocal.engine.knowledge_store",
        "voxlocal.engine.llm_engine",
        "voxlocal.engine.meeting_intelligence",
        "onnxruntime",
        "tokenizers",
        "sherpa_onnx",
        "faster_whisper",
    )
    for mod in required_modules:
        try:
            importlib.import_module(mod)
            checks[mod] = True
        except Exception as exc:
            checks[mod] = False
            errors.append(f"{mod}: {type(exc).__name__}: {exc}")

    exp = (expected_version or "").strip()
    version_match = (not exp) or (str(__version__).strip() == exp)
    if not version_match:
        errors.append(f"version_mismatch: running={__version__} expected={exp}")

    ok = version_match and all(checks.values())
    payload = {
        "ok": ok,
        "version": str(__version__),
        "expected_version": exp,
        "version_match": version_match,
        "frozen": bool(getattr(sys, "frozen", False)),
        "root_dir": str(ROOT_DIR),
        "voxlocal_file": str(vox_init_file),
        "modules": checks,
        "errors": errors,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if ok else 2


def _focus_existing_instance(auth_url: str = "") -> None:
    import urllib.parse
    import urllib.request

    if auth_url:
        query_str = urllib.parse.urlsplit(auth_url).query or auth_url
        for port in (8400, 3000):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/sync-session?{query_str}", timeout=1.0)
                break
            except Exception:
                pass

    loopback_focused = False
    for port in (8400, 3000):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/focus-window", timeout=1.0) as resp:
                if resp.status == 200:
                    loopback_focused = True
                    break
        except Exception:
            pass

    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        hwnd = 0
        for exact_title in ("VOX", "VoxLocal - Local AI Voice Assistant", "VoxLocal — Local AI Voice Assistant"):
            hwnd = user32.FindWindowW(None, exact_title)
            if hwnd:
                break
        if not hwnd and not loopback_focused:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            found = [0]

            def _enum_win(h, _):
                if user32.IsWindowVisible(h):
                    length = user32.GetWindowTextLengthW(h)
                    if 0 < length <= 120:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(h, buf, length + 1)
                        if buf.value == "VOX" or "VoxLocal" in buf.value:
                            found[0] = h
                            return False
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_win), 0)
            hwnd = found[0]

        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
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
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run first-run runtime & canonical model readiness health check and exit",
    )
    parser.add_argument(
        "--verify-model-hashes",
        action="store_true",
        help="Include full SHA-256 verification when running --health-check",
    )
    parser.add_argument(
        "--verify-runtime",
        action="store_true",
        help="Verify installed version and runtime modules without starting the GUI or modifying user data",
    )
    parser.add_argument(
        "--expected-version",
        type=str,
        default="",
        help="Expected application version string for --verify-runtime",
    )
    parser.add_argument(
        "--postcall-worker",
        type=str,
        default="",
        help="Internal worker mode: execute post-call ASR & diarization job JSON in isolated subprocess",
    )
    args = parser.parse_args()

    if getattr(args, "postcall_worker", ""):
        try:
            from voxlocal.engine.postcall_worker import run_postcall_worker_cli
            return int(run_postcall_worker_cli(args.postcall_worker))
        except Exception as exc:
            import traceback as _tb
            print(f"Fatal post-call worker error: {exc}\n{_tb.format_exc()}", file=sys.stderr)
            return 1

    if getattr(args, "print_version", False):
        from voxlocal import __version__

        print(__version__)
        return 0

    if getattr(args, "verify_runtime", False):
        return _run_verify_runtime(getattr(args, "expected_version", "") or "")

    if getattr(args, "health_check", False):
        return _run_health_check(verify_hashes=bool(getattr(args, "verify_model_hashes", False)))

    if getattr(args, "hardware_diagnostics", False):
        return _run_hardware_diagnostics(getattr(args, "diagnostics_out", "") or "")

    mutex_handle, already_running = acquire_single_instance_mutex()
    if already_running and args.smoke_test_ms <= 0:
        _focus_existing_instance(getattr(args, "auth_url", "") or "")
        if mutex_handle:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
        return 0

    from PyQt6.QtCore import Qt, QtMsgType, QTimer, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from voxlocal.core.config import AppConfig
    from voxlocal.core.controller import AppController
    from voxlocal.core.diagnostics import (
        UIEventLoopWatchdog,
        install_crash_diagnostics,
        shutdown_crash_diagnostics,
    )
    from voxlocal.core.logger import get_logger, setup_logging
    from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup
    from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
    from voxlocal.ui.main_window import MainWindow
    from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
    from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
    from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
    from voxlocal.ui.theme import create_app_icon, ensure_google_fonts_loaded, get_ui_font
    from voxlocal.ui.tray_manager import SystemTrayManager

    setup_logging()
    logger = get_logger("main")
    install_crash_diagnostics()
    set_windows_app_id()

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

    app = QApplication(sys.argv)
    ensure_google_fonts_loaded()
    app.setFont(get_ui_font(13, 400))
    app.setApplicationName("VoxLocal")
    app.setOrganizationName("VoxLocal")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(create_app_icon("idle"))

    watchdog = UIEventLoopWatchdog(app, heartbeat_interval_ms=250, stall_threshold_ms=1500.0)
    watchdog.start()

    config = AppConfig.load()
    controller = AppController(config=config)
    controller.ui_watchdog = watchdog
    if getattr(args, "auth_url", None):
        controller.complete_login_from_url(args.auth_url)

    main_win_ref = []
    tray_ref = []

    def open_dashboard_tab(tab_idx: int = 0, maximize: bool = False) -> None:
        if main_win_ref:
            main_win_ref[0].open_and_focus_tab(tab_idx, maximize=maximize)

    def exit_application() -> None:
        logger.info("User requested complete application exit.")
        try:
            watchdog.stop()
        except Exception:
            pass
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
    if hasattr(controller, "focus_main_window_requested"):
        controller.focus_main_window_requested.connect(
            lambda: open_dashboard_tab(0),
            Qt.ConnectionType.QueuedConnection,
        )

    start_hidden = args.minimized or (args.startup and config.start_minimized_to_tray)
    if not start_hidden:
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        app.processEvents()

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

    # Schedule onboarding tour on first run and optional smoke test exit
    if not start_hidden and args.smoke_test_ms <= 0 and not getattr(config, "onboarding_completed", False):
        QTimer.singleShot(350, _launch_onboarding_tour)

    if args.smoke_test_ms > 0:
        logger.info("Running smoke test for %d ms...", args.smoke_test_ms)
        QTimer.singleShot(args.smoke_test_ms, exit_application)

    try:
        exit_code = app.exec()
    finally:
        try:
            watchdog.stop()
        except Exception:
            pass
        shutdown_crash_diagnostics()
        if mutex_handle:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
    return int(exit_code)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

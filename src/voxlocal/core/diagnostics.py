"""
Centralized crash diagnostics, unhandled exception capture, fault handler,
and main-thread UI event-loop stall watchdog for VOX.

Privacy & Safety:
- Never writes raw audio samples or full transcripts into crash reports.
- Atomic, Windows-safe JSON crash report writes inside data/logs/crash_reports/.
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer

from voxlocal.core.logger import get_app_root, get_logger, get_system_resource_snapshot, get_user_data_root

logger = get_logger("core.diagnostics")

_crash_lock = threading.Lock()
_unhandled_exception_count = 0
_recent_crash_reports: Deque[str] = deque(maxlen=50)
_faulthandler_file: Optional[Any] = None
_crash_dir: Optional[Path] = None
_orig_sys_excepthook = sys.excepthook
_orig_threading_excepthook = getattr(threading, "excepthook", None)
_hooks_installed = False


def get_unhandled_exception_count() -> int:
    with _crash_lock:
        return _unhandled_exception_count


def get_recent_crash_reports() -> List[str]:
    with _crash_lock:
        return list(_recent_crash_reports)


def reset_crash_counters() -> None:
    global _unhandled_exception_count
    with _crash_lock:
        _unhandled_exception_count = 0
        _recent_crash_reports.clear()


def _write_sanitized_crash_report(
    source: str,
    exc_type: Any,
    exc_value: Any,
    exc_tb: Any,
    thread_name: str = "MainThread",
) -> Optional[Path]:
    global _unhandled_exception_count
    with _crash_lock:
        _unhandled_exception_count += 1
        target_dir = _crash_dir or (get_user_data_root() / "logs" / "crash_reports")

    try:
        os.makedirs(target_dir, exist_ok=True)
        ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_thread = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(thread_name))[:32]
        out_path = target_dir / f"crash_{ts_slug}_{safe_thread}_{uuid.uuid4().hex[:6]}.json"
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "thread_name": str(thread_name),
            "exception_type": getattr(exc_type, "__name__", str(exc_type)),
            "exception_message": str(exc_value)[:500],
            "traceback": "".join(tb_lines)[-6000:],
            "resources": get_system_resource_snapshot(),
        }
        tmp_path = out_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp_path, out_path)
        with _crash_lock:
            _recent_crash_reports.append(str(out_path))
        return out_path
    except Exception:
        return None


def install_crash_diagnostics(log_dir: Optional[Path] = None) -> Path:
    """Install faulthandler, sys.excepthook, and threading.excepthook safely (idempotent)."""
    global _faulthandler_file, _crash_dir, _hooks_installed

    base_log = Path(log_dir) if log_dir else (get_user_data_root() / "logs")
    crash_dir = base_log / "crash_reports"
    os.makedirs(crash_dir, exist_ok=True)

    with _crash_lock:
        _crash_dir = crash_dir
        if _faulthandler_file is None:
            try:
                fh_path = crash_dir / "native_fault.log"
                _faulthandler_file = open(fh_path, "a", encoding="utf-8", errors="replace")
                faulthandler.enable(file=_faulthandler_file, all_threads=True)
            except Exception as exc:
                logger.debug("faulthandler setup skipped: %s", exc)

    if _hooks_installed:
        return crash_dir

    def _sys_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _orig_sys_excepthook(exc_type, exc_value, exc_tb)
            return
        report_p = _write_sanitized_crash_report("sys.excepthook", exc_type, exc_value, exc_tb, "MainThread")
        logger.error(
            "Unhandled main-thread exception captured (report=%s):",
            report_p.name if report_p else "none",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    def _thread_hook(args: Any) -> None:
        exc_type = getattr(args, "exc_type", Exception)
        exc_value = getattr(args, "exc_value", None)
        exc_tb = getattr(args, "exc_traceback", None)
        if isinstance(exc_type, type) and issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        th = getattr(args, "thread", None)
        th_name = getattr(th, "name", "WorkerThread") if th is not None else "WorkerThread"
        report_p = _write_sanitized_crash_report("threading.excepthook", exc_type, exc_value, exc_tb, th_name)
        logger.error(
            "Unhandled worker-thread exception in '%s' captured (report=%s):",
            th_name,
            report_p.name if report_p else "none",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _sys_hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_hook
    _hooks_installed = True
    return crash_dir


def shutdown_crash_diagnostics() -> None:
    """Flush and close native fault handler file stream cleanly on app exit."""
    global _faulthandler_file
    with _crash_lock:
        try:
            faulthandler.disable()
        except Exception:
            pass
        if _faulthandler_file is not None:
            try:
                _faulthandler_file.flush()
                _faulthandler_file.close()
            except Exception:
                pass
            _faulthandler_file = None


@dataclass
class UIStallEvent:
    timestamp_utc: str
    duration_ms: float
    stack_summary: str


class UIEventLoopWatchdog(QObject):
    """
    Lightweight UI thread heartbeat watchdog.
    Detects event-loop freezes > stall_threshold_ms and dumps the main thread's Python stack
    to diagnose blocking calls without impacting steady-state UI latency.
    """

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        heartbeat_interval_ms: int = 250,
        stall_threshold_ms: float = 1500.0,
        max_events: int = 100,
    ) -> None:
        super().__init__(parent)
        self._heartbeat_interval_ms = max(50, int(heartbeat_interval_ms))
        self._stall_threshold_ms = max(100.0, float(stall_threshold_ms))
        self._main_ident = threading.get_ident()
        self._lock = threading.Lock()
        self._last_tick_mono = time.monotonic()
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stall_count = 0
        self._max_stall_ms = 0.0
        self._in_stall = False
        self._events: Deque[UIStallEvent] = deque(maxlen=max_events)

        self._timer = QTimer(self)
        self._timer.setInterval(self._heartbeat_interval_ms)
        self._timer.timeout.connect(self._on_heartbeat)

    def _on_heartbeat(self) -> None:
        now = time.monotonic()
        with self._lock:
            gap_ms = (now - self._last_tick_mono) * 1000.0
            self._last_tick_mono = now
            if gap_ms > self._max_stall_ms:
                self._max_stall_ms = gap_ms
            if self._in_stall:
                self._in_stall = False
                logger.info("[UI-STALL] Main UI event loop recovered after %.0fms stall.", gap_ms)

    def _capture_main_stack(self) -> str:
        try:
            frame = sys._current_frames().get(self._main_ident)
            if frame is None:
                return ""
            lines = traceback.format_stack(frame, limit=18)
            return "".join(lines)[-2000:]
        except Exception:
            return ""

    def _monitor_loop(self) -> None:
        poll_sec = min(0.10, max(0.03, self._stall_threshold_ms / 4000.0))
        while not self._stop_event.wait(poll_sec):
            now = time.monotonic()
            with self._lock:
                elapsed_ms = (now - self._last_tick_mono) * 1000.0
                if elapsed_ms > self._max_stall_ms:
                    self._max_stall_ms = elapsed_ms
                already_reported = self._in_stall
                if elapsed_ms >= self._stall_threshold_ms and not already_reported:
                    self._in_stall = True
                    self._stall_count += 1
                    stack = self._capture_main_stack()
                    ev = UIStallEvent(
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        duration_ms=round(elapsed_ms, 1),
                        stack_summary=stack,
                    )
                    self._events.append(ev)
                else:
                    ev = None
            if ev is not None:
                logger.warning(
                    "[UI-STALL] Main UI thread unresponsive for %.0fms\n%s",
                    ev.duration_ms,
                    ev.stack_summary,
                )

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._last_tick_mono = time.monotonic()
            self._stop_event.clear()
        self._timer.start()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="UIEventLoopWatchdog",
            daemon=True,
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._timer.isActive():
                self._timer.stop()
        except Exception:
            pass
        th = self._monitor_thread
        if th is not None and th.is_alive():
            try:
                th.join(timeout=0.5)
            except Exception:
                pass
        with self._lock:
            self._running = False
            self._monitor_thread = None

    def reset_stats(self) -> None:
        with self._lock:
            self._stall_count = 0
            self._max_stall_ms = 0.0
            self._in_stall = False
            self._events.clear()
            self._last_tick_mono = time.monotonic()

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "stall_threshold_ms": self._stall_threshold_ms,
                "stall_count": self._stall_count,
                "max_stall_ms": round(self._max_stall_ms, 2),
                "events": [asdict(e) for e in self._events],
            }

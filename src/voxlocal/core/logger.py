"""
Centralized diagnostics, structured correlation-ID logging, Windows process resource
telemetry, and rotating file logger with in-memory log buffer for UI inspection.

Privacy & Safety:
- Never logs raw audio samples or full transcripts at INFO/WARNING/ERROR levels.
- Attaches correlation IDs (session_id / op_id) and feature context across threads.
"""

from __future__ import annotations

import collections
import contextlib
import ctypes
from ctypes import wintypes
import logging
import os
import shutil
import sys
import threading
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Tuple


class WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """
    RotatingFileHandler with Windows file-locking guard against [WinError 32].
    Guarantees that log emissions never crash or spam unhandled exceptions if another
    thread or process temporarily holds a lock on the active log file during rollover.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # File is locked by an active handle on Windows; defer rollover safely
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) == 32:
                pass
            else:
                raise


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_kernel32 = getattr(ctypes, "windll", None) and ctypes.windll.kernel32
_psapi = getattr(ctypes, "windll", None) and getattr(ctypes.windll, "psapi", None)

if _kernel32:
    try:
        _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        _kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        _kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    except Exception:
        pass

if _psapi:
    try:
        _psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
            wintypes.DWORD,
        ]
        _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    except Exception:
        pass


_context_local = threading.local()
_global_session_lock = threading.Lock()
_active_contexts: Dict[str, str] = {
    "voice": "idle",
    "meeting": "idle",
    "screen": "idle",
}


def generate_correlation_id(prefix: str = "op") -> str:
    """Generate a compact, unique correlation ID for a session or operation."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def set_operation_context(feature: str, corr_id: str) -> None:
    """Bind a correlation ID to the current thread and update active feature tracking."""
    _context_local.feature = feature
    _context_local.corr_id = corr_id
    with _global_session_lock:
        if feature in _active_contexts:
            _active_contexts[feature] = corr_id


def clear_operation_context(feature: Optional[str] = None) -> None:
    """Clear correlation context on current thread."""
    _context_local.feature = None
    _context_local.corr_id = None
    if feature:
        with _global_session_lock:
            if feature in _active_contexts:
                _active_contexts[feature] = "idle"


@contextlib.contextmanager
def operation_context(feature: str, corr_id: Optional[str] = None) -> Iterator[str]:
    """Context manager for scoped operation logging with automatic cleanup."""
    cid = corr_id or generate_correlation_id(feature[:4])
    prev_feat = getattr(_context_local, "feature", None)
    prev_cid = getattr(_context_local, "corr_id", None)
    set_operation_context(feature, cid)
    try:
        yield cid
    finally:
        _context_local.feature = prev_feat
        _context_local.corr_id = prev_cid


class StructuredCorrelationFilter(logging.Filter):
    """Injects correlation ID (`corr_id`) into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        cid = getattr(_context_local, "corr_id", None)
        feat = getattr(_context_local, "feature", None)
        if cid and feat:
            record.corr_tag = f"{feat}:{cid}"
        elif cid:
            record.corr_tag = str(cid)
        else:
            record.corr_tag = "system"
        return True


def get_system_resource_snapshot(storage_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Collect lightweight Windows process metrics:
    - working_set_mb: Physical RAM in use by process
    - private_mb: Commit / private working bytes
    - handle_count: Open OS kernel handles
    - thread_count: Active Python threads
    - free_disk_mb: Available disk space on target drive
    """
    working_set_mb = 0.0
    private_mb = 0.0
    peak_working_set_mb = 0.0
    handle_count = 0

    if _kernel32:
        try:
            hproc = _kernel32.GetCurrentProcess()
            dw_count = wintypes.DWORD(0)
            if _kernel32.GetProcessHandleCount(hproc, ctypes.byref(dw_count)):
                handle_count = int(dw_count.value)
            if _psapi:
                pmc = PROCESS_MEMORY_COUNTERS_EX()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
                if _psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                    working_set_mb = round(pmc.WorkingSetSize / (1024.0 * 1024.0), 2)
                    private_mb = round(pmc.PrivateUsage / (1024.0 * 1024.0), 2)
                    peak_working_set_mb = round(pmc.PeakWorkingSetSize / (1024.0 * 1024.0), 2)
        except Exception:
            pass

    free_disk_mb = -1.0
    try:
        check_dir = storage_path if (storage_path and storage_path.exists()) else get_app_root()
        usage = shutil.disk_usage(str(check_dir))
        free_disk_mb = round(usage.free / (1024.0 * 1024.0), 1)
    except Exception:
        pass

    return {
        "working_set_mb": working_set_mb,
        "rss_mb": working_set_mb,
        "private_mb": private_mb,
        "peak_working_set_mb": peak_working_set_mb,
        "handle_count": handle_count,
        "handles": handle_count,
        "thread_count": threading.active_count(),
        "free_disk_mb": free_disk_mb,
    }


def check_disk_space_mb(target_dir: Optional[Path] = None, min_free_mb: float = 100.0) -> Tuple[bool, float, str]:
    """Verify at least `min_free_mb` megabytes of disk space are available."""
    try:
        check_path = target_dir if (target_dir and target_dir.exists()) else get_app_root()
        usage = shutil.disk_usage(str(check_path))
        free_mb = round(usage.free / (1024.0 * 1024.0), 1)
        if free_mb < min_free_mb:
            return (
                False,
                free_mb,
                f"Insufficient disk space ({free_mb:.1f} MB free, {min_free_mb:.1f} MB required).",
            )
        return True, free_mb, f"Disk space OK ({free_mb:.1f} MB free)."
    except Exception:
        return True, -1.0, "Disk space check bypassed."


class InMemoryLogHandler(logging.Handler):
    """Stores recent log lines in a bounded ring buffer and notifies optional listeners."""

    def __init__(self, maxlen: int = 500) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self.buffer: Deque[str] = collections.deque(maxlen=maxlen)
        self._listeners: List[Callable[[str], None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self.buffer.append(msg)
                listeners = list(self._listeners)
            for listener in listeners:
                try:
                    listener(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def add_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def get_recent_logs(self) -> List[str]:
        with self._lock:
            return list(self.buffer)


_memory_handler: InMemoryLogHandler | None = None
_initialized = False


def get_app_root() -> Path:
    """Return the root directory of the application (handles frozen PyInstaller builds too)."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (
            exe_dir.name.lower() == "voxlocal"
            and exe_dir.parent.name.lower() == "dist"
            and (exe_dir.parent.parent / "data").exists()
        ):
            return exe_dir.parent.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent.parent.parent


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure application-wide logging to file, console, and in-memory buffer."""
    global _memory_handler, _initialized
    logger = logging.getLogger("voxlocal")
    if _initialized:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    if log_dir is None:
        log_dir = get_app_root() / "data" / "logs"
    os.makedirs(log_dir, exist_ok=True)

    corr_filter = StructuredCorrelationFilter()
    logger.addFilter(corr_filter)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | [%(corr_tag)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Rotating file handler (5 MB x 3 backups) with Windows file lock safety
    log_file = log_dir / "voxlocal.log"
    file_handler = WindowsSafeRotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(corr_filter)
    logger.addHandler(file_handler)

    # 2. Console handler (if stdout is available)
    if sys.stdout is not None:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(corr_filter)
        logger.addHandler(console_handler)

    # 3. In-memory ring handler for Diagnostics UI
    _memory_handler = InMemoryLogHandler(maxlen=600)
    _memory_handler.setFormatter(formatter)
    _memory_handler.addFilter(corr_filter)
    logger.addHandler(_memory_handler)

    _initialized = True
    snap = get_system_resource_snapshot(log_dir)
    logger.info(
        "VoxLocal logging initialized at %s (RAM=%.1fMB, handles=%d, threads=%d, free_disk=%.0fMB)",
        log_file,
        snap["working_set_mb"],
        snap["handle_count"],
        snap["thread_count"],
        snap["free_disk_mb"],
    )
    return logger


def get_logger(name: str = "voxlocal") -> logging.Logger:
    if not _initialized:
        setup_logging()
    if name == "voxlocal" or name.startswith("voxlocal."):
        return logging.getLogger(name)
    return logging.getLogger(f"voxlocal.{name}")


def get_memory_log_handler() -> InMemoryLogHandler | None:
    if not _initialized:
        setup_logging()
    return _memory_handler


def get_recent_logs(limit: int = 100) -> List[str]:
    handler = get_memory_log_handler()
    if handler is None:
        return []
    logs = handler.get_recent_logs()
    return logs[-limit:] if limit > 0 else logs

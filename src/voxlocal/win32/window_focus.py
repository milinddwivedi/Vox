"""
Win32 Window Focus & Non-Activating Floating Window Management.
Ensures the Floating Widget never steals focus from external applications
and tracks the active target window (HWND, title, process name) for seamless voice typing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from dataclasses import dataclass
from typing import List, Optional, Set

from voxlocal.core.logger import get_logger

logger = get_logger("win32.focus")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 Extended Window Styles
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1

WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WA_ACTIVE = 1

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Configure 64-bit safe function signatures
if ctypes.sizeof(ctypes.c_void_p) == 8:
    GetWindowLongPtrW = user32.GetWindowLongPtrW
    SetWindowLongPtrW = user32.SetWindowLongPtrW
    LONG_PTR = ctypes.c_longlong
else:
    GetWindowLongPtrW = user32.GetWindowLongW
    SetWindowLongPtrW = user32.SetWindowLongW
    LONG_PTR = ctypes.c_long

GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = LONG_PTR

SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
SetWindowLongPtrW.restype = LONG_PTR

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL


@dataclass
class TargetWindowInfo:
    hwnd: int
    title: str
    process_id: int
    thread_id: int
    process_name: str
    focus_child_hwnd: int = 0


def apply_non_activating_floating_style(hwnd: int) -> None:
    """
    Apply WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW to a window handle.
    Clicking a WS_EX_NOACTIVATE window delivers mouse events without stealing
    keyboard focus from the currently active target application.
    """
    try:
        current_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        new_style = current_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        logger.debug("Applied WS_EX_NOACTIVATE | WS_EX_TOPMOST to HWND=0x%X", hwnd)
    except Exception as exc:
        logger.error("Failed applying non-activating style to HWND=0x%X: %s", hwnd, exc)


def get_window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_process_name_from_pid(pid: int) -> str:
    if not pid:
        return ""
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h_proc:
        return ""
    try:
        buf_len = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(buf_len)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h_proc)


def get_focused_child_hwnd(top_hwnd: int, thread_id: int) -> int:
    """Resolve the active child input control HWND inside a top-level window."""
    if thread_id:
        gui = GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui)):
            if gui.hwndFocus:
                return int(gui.hwndFocus)
            if gui.hwndCaret:
                return int(gui.hwndCaret)

    # Also check if top_hwnd has a known Edit / RichEdit child window (e.g. Notepad)
    children: List[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _enum_child(ch_hwnd: int, _lparam: int) -> bool:
        cls_name = get_window_class_name(ch_hwnd).lower()
        if any(k in cls_name for k in ("edit", "richedit", "scintilla", "chrome_renderwidgethosthwnd")):
            children.append(int(ch_hwnd))
        return True

    user32.EnumChildWindows(top_hwnd, WNDENUMPROC(_enum_child), 0)
    if children:
        return children[0]
    return int(top_hwnd)


def get_window_info(hwnd: int) -> Optional[TargetWindowInfo]:
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD(0)
    thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title = get_window_title(hwnd)
    proc_name = get_process_name_from_pid(pid.value)
    focus_child = get_focused_child_hwnd(int(hwnd), int(thread_id))
    return TargetWindowInfo(
        hwnd=int(hwnd),
        title=title,
        process_id=int(pid.value),
        thread_id=int(thread_id),
        process_name=proc_name,
        focus_child_hwnd=focus_child,
    )


class WindowFocusTracker:
    """
    Continuously tracks the most recent external foreground window so that
    voice typing always knows where to insert text, even if the user clicked
    a system tray menu or floating control.
    """

    def __init__(self) -> None:
        self._own_pid: int = os.getpid()
        self._own_hwnds: Set[int] = set()
        self._last_target: Optional[TargetWindowInfo] = None
        self._locked_target: Optional[TargetWindowInfo] = None

    def register_own_hwnd(self, hwnd: int) -> None:
        if hwnd:
            self._own_hwnds.add(int(hwnd))

    def is_foreground_own_process(self) -> bool:
        """Return True if the current Windows foreground window belongs to this process."""
        try:
            fg = user32.GetForegroundWindow()
            hwnd = int(fg) if fg else 0
            if not hwnd:
                return False
            if hwnd in self._own_hwnds:
                return True
            info = get_window_info(hwnd)
            return bool(info and info.process_id == self._own_pid)
        except Exception:
            return False

    def poll_foreground(self) -> Optional[TargetWindowInfo]:
        """Check current foreground window; if it belongs to another process, record it."""
        fg = user32.GetForegroundWindow()
        hwnd = int(fg) if fg else 0
        if hwnd and hwnd not in self._own_hwnds:
            info = get_window_info(hwnd)
            if info and info.process_id != self._own_pid:
                if not (info.process_name.lower() == "explorer.exe" and not info.title):
                    self._last_target = info
                    return info

        # Fallback if GetForegroundWindow() is 0 (e.g. secondary desktop or between activations):
        # Find topmost visible external application window in Z-order
        if self._last_target and user32.IsWindow(self._last_target.hwnd):
            return self._last_target

        found: List[TargetWindowInfo] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_top(h: int, _lp: int) -> bool:
            if not user32.IsWindowVisible(h) or h in self._own_hwnds:
                return True
            info = get_window_info(h)
            if info and info.process_id != self._own_pid and info.title:
                if info.process_name.lower() not in ("textinputhost.exe", "shellexperiencehost.exe"):
                    found.append(info)
                    return False  # Stop at top-most visible titled window
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_top), 0)
        if found:
            self._last_target = found[0]
        return self._last_target

    def lock_current_target(self) -> Optional[TargetWindowInfo]:
        """Called when dictation starts to snapshot the active target window."""
        self.poll_foreground()
        self._locked_target = self._last_target
        if self._locked_target:
            logger.info(
                "Locked dictation target window: '%s' (%s, HWND=0x%X)",
                self._locked_target.title,
                self._locked_target.process_name,
                self._locked_target.hwnd,
            )
        return self._locked_target

    def get_active_target(self) -> Optional[TargetWindowInfo]:
        """Return the currently locked target (or latest valid external window)."""
        if self._locked_target and user32.IsWindow(self._locked_target.hwnd):
            # Refresh focus_child_hwnd in case user clicked a different field inside the same window
            self._locked_target.focus_child_hwnd = get_focused_child_hwnd(
                self._locked_target.hwnd, self._locked_target.thread_id
            )
            return self._locked_target
        self.poll_foreground()
        return self._last_target

    def clear_locked_target(self) -> None:
        self._locked_target = None

    def is_own_hwnd(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if hwnd in self._own_hwnds:
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._own_pid

    def ensure_target_focused(self, target: Optional[TargetWindowInfo] = None) -> bool:
        """
        Ensure the target window has foreground focus before sending keystrokes.
        If the current foreground window is ALREADY an external application (like Antigravity or Windows Search),
        we DO NOT switch focus, preventing light-dismiss popups from being closed.
        Only if the current foreground window belongs to VoxLocal (e.g. user clicked our UI) do we bring target to top.
        """
        fg = user32.GetForegroundWindow()
        current_fg = int(fg) if fg else 0
        if current_fg and not self.is_own_hwnd(current_fg):
            # Already focused in an external user application (Antigravity, Windows Search, Word, etc.)
            return True

        target = target or self.get_active_target()
        if not target or not user32.IsWindow(target.hwnd):
            return False

        if current_fg == target.hwnd:
            return True

        try:
            fg_pid = wintypes.DWORD(0)
            fg_thread = user32.GetWindowThreadProcessId(current_fg, ctypes.byref(fg_pid)) if current_fg else 0
            cur_thread = kernel32.GetCurrentThreadId()

            attached_fg = False
            attached_target = False
            if fg_thread and fg_thread != cur_thread:
                attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
            if target.thread_id and target.thread_id != cur_thread:
                attached_target = bool(user32.AttachThreadInput(cur_thread, target.thread_id, True))

            try:
                user32.BringWindowToTop(target.hwnd)
                user32.SetForegroundWindow(target.hwnd)
            finally:
                if attached_target:
                    user32.AttachThreadInput(cur_thread, target.thread_id, False)
                if attached_fg:
                    user32.AttachThreadInput(cur_thread, fg_thread, False)

            # Also ensure window activation state is active if SetForegroundWindow is restricted
            now_fg = user32.GetForegroundWindow()
            if (int(now_fg) if now_fg else 0) != target.hwnd:
                user32.SendMessageW(target.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
                focus_hwnd = target.focus_child_hwnd or target.hwnd
                user32.SendMessageW(focus_hwnd, WM_SETFOCUS, 0, 0)
            return True
        except Exception as exc:
            logger.warning("Failed restoring focus to HWND=0x%X: %s", target.hwnd, exc)
            return False

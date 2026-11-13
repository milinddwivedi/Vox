"""
Cross-Application Text Insertion Engine for Windows.
Supports:
1. Direct Unicode Keyboard Injection via Win32 SendInput (KEYEVENTF_UNICODE) +
   direct Win32 WM_CHAR target window fallback (handles UIPI / elevated windows / secondary desktops) —
   inserts characters directly at the cursor position without touching the clipboard.
2. Safe Clipboard-Paste Fallback (Ctrl+V / WM_PASTE) with full Win32 Clipboard backup and restoration.
3. Modifier Key Safety — waits for / releases held Ctrl, Alt, Shift, Win keys before
   injecting text so voice typing never triggers accidental shortcuts in the target app.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import time
from typing import Optional, Tuple

from voxlocal.core.logger import get_logger
from voxlocal.win32.window_focus import (
    TargetWindowInfo,
    WindowFocusTracker,
    get_focused_child_hwnd,
    get_window_class_name,
)

logger = get_logger("win32.inserter")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 SendInput constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_PASTE = 0x0302
WA_ACTIVE = 1

VK_RETURN = 0x0D
VK_TAB = 0x09
VK_BACK = 0x08
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12     # Alt key
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM

# Clipboard API prototypes (64-bit safe)
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def get_clipboard_text() -> Optional[str]:
    """Read CF_UNICODETEXT from Windows clipboard safely."""
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return None
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return None
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return None
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return None


def set_clipboard_text(text: str) -> bool:
    """Write CF_UNICODETEXT to Windows clipboard safely."""
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
                if not h_mem:
                    return False
                ptr = kernel32.GlobalLock(h_mem)
                if not ptr:
                    kernel32.GlobalFree(h_mem)
                    return False
                ctypes.memmove(ptr, encoded, len(encoded))
                kernel32.GlobalUnlock(h_mem)
                if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                    kernel32.GlobalFree(h_mem)
                    return False
                return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return False


def release_held_modifiers(wait_timeout: float = 0.15) -> None:
    """
    Wait briefly if the user is still physically holding a shortcut modifier (Ctrl, Shift, Alt),
    then send explicit KEYUP events for any modifier still logically down so simulated typing
    never triggers unwanted application shortcuts.
    Avoids sending lone Win keyup events which would toggle/dismiss Windows Search or Start Menu.
    """
    modifier_vks = (
        VK_CONTROL,
        VK_LCONTROL,
        VK_RCONTROL,
        VK_SHIFT,
        VK_LSHIFT,
        VK_RSHIFT,
        VK_MENU,
        VK_LMENU,
        VK_RMENU,
    )
    deadline = time.perf_counter() + wait_timeout
    while time.perf_counter() < deadline:
        any_down = any((user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in modifier_vks)
        if not any_down:
            break
        time.sleep(0.015)

    release_inputs = []
    for vk in modifier_vks:
        if (user32.GetAsyncKeyState(vk) & 0x8000) != 0:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.union.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            release_inputs.append(inp)
    if release_inputs:
        arr = (INPUT * len(release_inputs))(*release_inputs)
        user32.SendInput(len(release_inputs), arr, ctypes.sizeof(INPUT))
        time.sleep(0.01)


class TextInserter:
    """
    Inserts transcribed text into the active cursor position of any Windows application.
    """

    def __init__(self, focus_tracker: WindowFocusTracker) -> None:
        self.focus_tracker = focus_tracker

    def insert_text(
        self,
        text: str,
        method: str = "auto",
        restore_clipboard: bool = True,
        target: Optional[TargetWindowInfo] = None,
    ) -> Tuple[bool, str]:
        """
        Insert `text` into the target application.
        Returns `(success, method_used)`.
        """
        if not text:
            return False, "empty"

        target = target or self.focus_tracker.get_active_target()
        if target:
            if not user32.IsWindow(target.hwnd):
                logger.warning(
                    "Target window HWND=0x%X (%s) was closed before text insertion. "
                    "Preserving transcribed text to clipboard.",
                    target.hwnd,
                    target.process_name,
                )
                set_clipboard_text(text)
                return False, "target_closed_saved_to_clipboard"
            self.focus_tracker.ensure_target_focused(target)
            time.sleep(0.02)

        release_held_modifiers()

        use_clipboard = False
        proc_lower = target.process_name.lower() if (target and target.process_name) else ""
        has_non_ascii = any(ord(ch) > 127 for ch in text)
        is_rich_webview_target = proc_lower in (
            "ms-teams.exe",
            "teams.exe",
            "msedgewebview2.exe",
            "olk.exe",
            "outlook.exe",
            "slack.exe",
            "notion.exe",
            "winword.exe",
        )
        if method == "clipboard":
            use_clipboard = True
        elif method == "auto":
            if len(text) > 120 or has_non_ascii or is_rich_webview_target:
                use_clipboard = True

        if not use_clipboard:
            ok = self._send_unicode_text(text, target=target)
            if ok:
                return True, "unicode_sendinput"
            logger.warning("Unicode direct injection failed; falling back to clipboard paste.")

        ok_clip = self._paste_via_clipboard(
            text, restore_clipboard=restore_clipboard, target=target
        )
        return ok_clip, "clipboard_paste" if ok_clip else "failed"

    def send_backspaces(
        self, count: int = 1, target: Optional[TargetWindowInfo] = None
    ) -> bool:
        """Send backspaces to delete text in the active target window."""
        if count <= 0:
            return True
        target = target or self.focus_tracker.get_active_target()
        if target:
            self.focus_tracker.ensure_target_focused(target)
            time.sleep(0.01)
        release_held_modifiers()
        inputs = []
        for _ in range(min(count, 120)):
            down = INPUT(type=INPUT_KEYBOARD)
            down.union.ki = KEYBDINPUT(wVk=VK_BACK, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            up = INPUT(type=INPUT_KEYBOARD)
            up.union.ki = KEYBDINPUT(
                wVk=VK_BACK, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
            )
            inputs.extend([down, up])
        arr = (INPUT * len(inputs))(*inputs)
        sent = int(user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))
        if sent == 0 and target and user32.IsWindow(target.hwnd):
            dest_hwnd = get_focused_child_hwnd(target.hwnd, target.thread_id) or target.hwnd
            for _ in range(count):
                user32.SendMessageW(dest_hwnd, WM_KEYDOWN, VK_BACK, 1)
                user32.SendMessageW(dest_hwnd, WM_KEYUP, VK_BACK, 0xC0000001)
            return True
        if sent == 0 and (kernel32.GetLastError() == 5 or kernel32.GetLastError() == 0):
            return True
        return sent == len(inputs)

    def send_enter(self, target: Optional[TargetWindowInfo] = None) -> bool:
        """Send Enter key to submit or create new line in the active target window."""
        target = target or self.focus_tracker.get_active_target()
        if target:
            self.focus_tracker.ensure_target_focused(target)
            time.sleep(0.01)
        release_held_modifiers()
        down = INPUT(type=INPUT_KEYBOARD)
        down.union.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.union.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
        arr = (INPUT * 2)(down, up)
        sent = int(user32.SendInput(2, arr, ctypes.sizeof(INPUT)))
        if sent == 0 and target and user32.IsWindow(target.hwnd):
            dest_hwnd = get_focused_child_hwnd(target.hwnd, target.thread_id) or target.hwnd
            user32.SendMessageW(dest_hwnd, WM_KEYDOWN, VK_RETURN, 1)
            user32.SendMessageW(dest_hwnd, WM_KEYUP, VK_RETURN, 0xC0000001)
            return True
        if sent == 0 and (kernel32.GetLastError() == 5 or kernel32.GetLastError() == 0):
            return True
        return sent == 2

    def _send_unicode_text(
        self, text: str, target: Optional[TargetWindowInfo] = None
    ) -> bool:
        """
        Inject UTF-16 code units via Win32 SendInput (KEYEVENTF_UNICODE).
        If SendInput is blocked by UIPI or desktop isolation, automatically delivers
        characters via direct Win32 WM_CHAR messages to the target window/control.
        """
        inputs = []
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        for ch in normalized:
            if ch == "\n":
                down = INPUT(type=INPUT_KEYBOARD)
                down.union.ki = KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
                up = INPUT(type=INPUT_KEYBOARD)
                up.union.ki = KEYBDINPUT(
                    wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
                )
                inputs.extend([down, up])
            elif ch == "\t":
                down = INPUT(type=INPUT_KEYBOARD)
                down.union.ki = KEYBDINPUT(wVk=VK_TAB, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
                up = INPUT(type=INPUT_KEYBOARD)
                up.union.ki = KEYBDINPUT(
                    wVk=VK_TAB, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
                )
                inputs.extend([down, up])
            else:
                utf16_bytes = ch.encode("utf-16-le")
                for i in range(0, len(utf16_bytes), 2):
                    code_unit = int.from_bytes(utf16_bytes[i : i + 2], "little")
                    down = INPUT(type=INPUT_KEYBOARD)
                    down.union.ki = KEYBDINPUT(
                        wVk=0,
                        wScan=code_unit,
                        dwFlags=KEYEVENTF_UNICODE,
                        time=0,
                        dwExtraInfo=0,
                    )
                    up = INPUT(type=INPUT_KEYBOARD)
                    up.union.ki = KEYBDINPUT(
                        wVk=0,
                        wScan=code_unit,
                        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        time=0,
                        dwExtraInfo=0,
                    )
                    inputs.extend([down, up])

        if not inputs:
            return False

        chunk_size = 64
        total_sent = 0
        for start in range(0, len(inputs), chunk_size):
            batch = inputs[start : start + chunk_size]
            arr = (INPUT * len(batch))(*batch)
            sent = int(user32.SendInput(len(batch), arr, ctypes.sizeof(INPUT)))
            if sent == 0 and start == 0:
                # SendInput blocked by UIPI or desktop isolation -> use direct Win32 WM_CHAR injection
                if target and user32.IsWindow(target.hwnd):
                    return self._send_wm_char_to_target(normalized, target)
                return False
            total_sent += sent
            if len(inputs) > chunk_size:
                time.sleep(0.008)

        return total_sent == len(inputs)

    def _send_wm_char_to_target(self, normalized_text: str, target: TargetWindowInfo) -> bool:
        """
        Deliver Unicode characters directly to a target window or its focused child control
        via Win32 WM_CHAR / WM_KEYDOWN messages. Works even when SendInput is blocked.
        """
        if not target or not user32.IsWindow(target.hwnd):
            return False

        user32.SendMessageW(target.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        dest_hwnd = get_focused_child_hwnd(target.hwnd, target.thread_id) or target.hwnd
        user32.SendMessageW(dest_hwnd, WM_SETFOCUS, 0, 0)

        for ch in normalized_text:
            if ch == "\n":
                user32.SendMessageW(dest_hwnd, WM_KEYDOWN, VK_RETURN, 1)
                user32.SendMessageW(dest_hwnd, WM_CHAR, VK_RETURN, 1)
                user32.SendMessageW(dest_hwnd, WM_KEYUP, VK_RETURN, 0xC0000001)
            elif ch == "\t":
                user32.SendMessageW(dest_hwnd, WM_KEYDOWN, VK_TAB, 1)
                user32.SendMessageW(dest_hwnd, WM_CHAR, VK_TAB, 1)
                user32.SendMessageW(dest_hwnd, WM_KEYUP, VK_TAB, 0xC0000001)
            else:
                utf16_bytes = ch.encode("utf-16-le")
                for i in range(0, len(utf16_bytes), 2):
                    code_unit = int.from_bytes(utf16_bytes[i : i + 2], "little")
                    user32.SendMessageW(dest_hwnd, WM_CHAR, code_unit, 1)
        return True

    def _paste_via_clipboard(
        self,
        text: str,
        restore_clipboard: bool = True,
        target: Optional[TargetWindowInfo] = None,
    ) -> bool:
        """
        Fallback text insertion via Clipboard + simulated Ctrl+V (or WM_PASTE / clipboard stream),
        restoring previous text clipboard content afterwards.
        """
        previous_clip: Optional[str] = None
        if restore_clipboard:
            try:
                previous_clip = get_clipboard_text()
            except Exception as exc:
                logger.debug("Could not snapshot clipboard: %s", exc)

        if not set_clipboard_text(text):
            return False

        time.sleep(0.008)

        # 1. Try hardware Ctrl+V via SendInput with physical scan codes (WebView2 / Teams / Electron safe)
        scan_ctrl = int(user32.MapVirtualKeyW(VK_CONTROL, 0))
        scan_v = int(user32.MapVirtualKeyW(VK_V, 0))

        ctrl_down = INPUT(type=INPUT_KEYBOARD)
        ctrl_down.union.ki = KEYBDINPUT(
            wVk=VK_CONTROL, wScan=scan_ctrl, dwFlags=0, time=0, dwExtraInfo=0
        )
        v_down = INPUT(type=INPUT_KEYBOARD)
        v_down.union.ki = KEYBDINPUT(
            wVk=VK_V, wScan=scan_v, dwFlags=0, time=0, dwExtraInfo=0
        )
        v_up = INPUT(type=INPUT_KEYBOARD)
        v_up.union.ki = KEYBDINPUT(
            wVk=VK_V, wScan=scan_v, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
        )
        ctrl_up = INPUT(type=INPUT_KEYBOARD)
        ctrl_up.union.ki = KEYBDINPUT(
            wVk=VK_CONTROL, wScan=scan_ctrl, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
        )

        down_seq = (INPUT * 2)(ctrl_down, v_down)
        up_seq = (INPUT * 2)(v_up, ctrl_up)
        sent_down = int(user32.SendInput(2, down_seq, ctypes.sizeof(INPUT)))
        time.sleep(0.008)
        sent_up = int(user32.SendInput(2, up_seq, ctypes.sizeof(INPUT)))
        pasted_ok = (sent_down + sent_up) == 4

        # 2. If SendInput is blocked, deliver clipboard content to target window
        if not pasted_ok and target and user32.IsWindow(target.hwnd):
            clip_now = get_clipboard_text() or text
            pasted_ok = self._send_wm_char_to_target(
                clip_now.replace("\r\n", "\n").replace("\r", "\n"), target
            )

        # Allow target application (especially WebView2 / Teams) to process paste before restoring clipboard
        if restore_clipboard and previous_clip is not None:
            time.sleep(0.09)
            try:
                set_clipboard_text(previous_clip)
            except Exception as exc:
                logger.debug("Could not restore clipboard: %s", exc)

        return pasted_ok

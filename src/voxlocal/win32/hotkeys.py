"""
Native Win32 Global Hotkey & Push-to-Talk Manager.
Uses a dedicated Win32 message-pump thread with WH_KEYBOARD_LL (Low-Level Keyboard Hook)
so that:
1. Both Toggle-to-Talk (key-down trigger) and Push-to-Talk (hold to record, release to transcribe)
   work globally across all Windows applications without requiring Administrator privileges.
2. Modifier-only chords (e.g. Ctrl+Win, Ctrl+Alt) and standard chords (e.g. Ctrl+Shift+Space,
   Ctrl+Shift+R, F8, F9) are supported seamlessly.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from voxlocal.core.logger import get_logger

logger = get_logger("win32.hotkeys")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

# Virtual Key Code lookup table
VK_MAP: Dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "lctrl": 0xA2,
    "rctrl": 0xA3,
    "shift": 0x10,
    "lshift": 0xA0,
    "rshift": 0xA1,
    "alt": 0x12,
    "lalt": 0xA4,
    "ralt": 0xA5,
    "win": 0x5B,
    "lwin": 0x5B,
    "rwin": 0x5C,
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "pause": 0x13,
    "fn": 0x77,  # Default hardware Function Key alias to VK_F8 (0x77)
    "fn key": 0x77,
    "function": 0x77,
    "function key": 0x77,
    "`": 0xC0,
    "tilde": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}

# Add A-Z (0x41 - 0x5A) and 0-9 (0x30 - 0x39) and F1-F24 (0x70 - 0x87)
for _c in range(ord("a"), ord("z") + 1):
    VK_MAP[chr(_c)] = _c - 32
for _d in range(ord("0"), ord("9") + 1):
    VK_MAP[chr(_d)] = _d
for _f in range(1, 25):
    VK_MAP[f"f{_f}"] = 0x70 + (_f - 1)


def normalize_vk(vk: int) -> int:
    """Normalize left/right modifier virtual key codes into canonical group codes where helpful."""
    if vk in (0xA2, 0xA3):  # LCONTROL, RCONTROL
        return 0x11
    if vk in (0xA0, 0xA1):  # LSHIFT, RSHIFT
        return 0x10
    if vk in (0xA4, 0xA5):  # LMENU, RMENU
        return 0x12
    if vk in (0x5B, 0x5C):  # LWIN, RWIN
        return 0x5B
    return vk


def parse_hotkey_string(hotkey_str: str) -> Set[int]:
    """Parse a human-readable hotkey like 'ctrl+shift+space' into a set of canonical VK codes."""
    if not hotkey_str or not hotkey_str.strip():
        return set()
    parts = [p.strip().lower() for p in hotkey_str.replace(" ", "").split("+") if p.strip()]
    # Special case if 'space' was stripped by replace(" ", "") when written as 'ctrl + shift + space'
    raw_parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    vks: Set[int] = set()
    for part in raw_parts:
        vk = VK_MAP.get(part)
        if vk is not None:
            vks.add(normalize_vk(vk))
    return vks


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


class GlobalHotkeyManager:
    """
    Manages global keyboard shortcuts for VOX:
    - Voice Typing Toggle (`dictation_hotkey`, e.g. Ctrl+Shift+Space)
    - Voice Typing Push-to-Talk (`ptt_hotkey`, e.g. Ctrl+Win, hold vs release)
    - Double-tap PTT chord -> Hands-Free Voice Typing toggle
    - Triple-tap PTT chord (optional) -> Notetaker toggle
    - Esc while dictating -> Cancel active dictation cleanly
    - Meeting Notetaker Toggle (`meeting_hotkey`)
    - Secondary utility hotkeys: Scratchpad, Transform/Polish, Paste Last, Copy Last
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pressed_vks: Set[int] = set()

        self._enable_voice_typing_hotkey: bool = True
        self._dictation_mode: str = "toggle"  # "toggle" or "push_to_talk"
        self._dictation_vks: Set[int] = parse_hotkey_string("ctrl+shift+space")
        self._ptt_vks: Set[int] = parse_hotkey_string("ctrl+win")
        self._meeting_vks: Set[int] = parse_hotkey_string("ctrl+alt+r")

        self._hands_free_double_tap: bool = True
        self._notetaker_triple_tap: bool = False
        self._scratchpad_vks: Set[int] = set()
        self._transform_vks: Set[int] = set()
        self._paste_last_vks: Set[int] = set()
        self._copy_last_vks: Set[int] = set()

        self._dictation_latch: bool = False
        self._ptt_active: bool = False
        self._meeting_latch: bool = False
        self._utility_latches: Dict[str, bool] = {}

        # Tap timing for double/triple tap detection on PTT chord
        self._ptt_press_time: float = 0.0
        self._ptt_had_other_key: bool = False
        self._ptt_tap_timestamps: list[float] = []

        self.on_dictation_toggle: Optional[Callable[[], None]] = None
        self.on_hands_free_toggle: Optional[Callable[[], None]] = None
        self.on_ptt_start: Optional[Callable[[], None]] = None
        self.on_ptt_stop: Optional[Callable[[], None]] = None
        self.on_cancel_dictation: Optional[Callable[[], None]] = None
        self.on_meeting_toggle: Optional[Callable[[], None]] = None
        self.on_mode_switch: Optional[Callable[[str], None]] = None
        self.on_open_scratchpad: Optional[Callable[[], None]] = None
        self.on_transform_last: Optional[Callable[[], None]] = None
        self.on_paste_last: Optional[Callable[[], None]] = None
        self.on_copy_last: Optional[Callable[[], None]] = None
        self.is_dictation_active_fn: Optional[Callable[[], bool]] = None

        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._hook_handle: Optional[int] = None
        self._hook_proc_ref: Optional[HOOKPROC] = None
        self._running = False

    def configure(
        self,
        dictation_mode: str,
        dictation_hotkey: str,
        ptt_hotkey: str,
        meeting_hotkey: str,
        enable_dictation_hotkey: bool = True,
        hands_free_double_tap: bool = True,
        notetaker_triple_tap: bool = False,
        scratchpad_hotkey: str = "",
        transform_hotkey: str = "",
        paste_last_hotkey: str = "",
        copy_last_hotkey: str = "",
    ) -> Tuple[bool, str]:
        """Update active shortcut bindings dynamically without restarting the hook."""
        with self._lock:
            self._enable_voice_typing_hotkey = bool(enable_dictation_hotkey)
            d_vks = parse_hotkey_string(dictation_hotkey)
            p_vks = parse_hotkey_string(ptt_hotkey)
            m_vks = parse_hotkey_string(meeting_hotkey)

            if not d_vks:
                return False, f"Invalid voice typing shortcut: '{dictation_hotkey}'"
            if not p_vks:
                return False, f"Invalid push-to-talk shortcut: '{ptt_hotkey}'"
            if not m_vks:
                return False, f"Invalid meeting shortcut: '{meeting_hotkey}'"

            if d_vks == m_vks:
                return False, "Voice typing and meeting shortcuts cannot be identical."

            self._dictation_mode = dictation_mode
            self._dictation_vks = d_vks
            self._ptt_vks = p_vks
            self._meeting_vks = m_vks
            self._hands_free_double_tap = bool(hands_free_double_tap)
            self._notetaker_triple_tap = bool(notetaker_triple_tap)
            self._scratchpad_vks = parse_hotkey_string(scratchpad_hotkey) if scratchpad_hotkey else set()
            self._transform_vks = parse_hotkey_string(transform_hotkey) if transform_hotkey else set()
            self._paste_last_vks = parse_hotkey_string(paste_last_hotkey) if paste_last_hotkey else set()
            self._copy_last_vks = parse_hotkey_string(copy_last_hotkey) if copy_last_hotkey else set()

            self._pressed_vks.clear()
            self._dictation_latch = False
            self._ptt_active = False
            self._meeting_latch = False
            self._utility_latches.clear()
            self._ptt_tap_timestamps.clear()

            logger.info(
                "Configured hotkeys: mode=%s, enabled=%s, dictation='%s', ptt='%s', meeting='%s', double_tap=%s",
                dictation_mode,
                enable_dictation_hotkey,
                dictation_hotkey,
                ptt_hotkey,
                meeting_hotkey,
                hands_free_double_tap,
            )
            return True, "Shortcuts updated."

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._hook_loop, name="Win32GlobalHotkeyThread", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None
        self._thread_id = 0

    def __del__(self) -> None:
        try:
            if getattr(self, "_running", False):
                self.stop()
        except Exception:
            pass

    def _is_vk_physically_down(self, canonical_vk: int) -> bool:
        """Verify with GetAsyncKeyState so stuck keys never happen if focus changed."""
        if canonical_vk == 0x11:  # Ctrl
            return bool(
                (user32.GetAsyncKeyState(0x11) & 0x8000)
                or (user32.GetAsyncKeyState(0xA2) & 0x8000)
                or (user32.GetAsyncKeyState(0xA3) & 0x8000)
            )
        if canonical_vk == 0x10:  # Shift
            return bool(
                (user32.GetAsyncKeyState(0x10) & 0x8000)
                or (user32.GetAsyncKeyState(0xA0) & 0x8000)
                or (user32.GetAsyncKeyState(0xA1) & 0x8000)
            )
        if canonical_vk == 0x12:  # Alt
            return bool(
                (user32.GetAsyncKeyState(0x12) & 0x8000)
                or (user32.GetAsyncKeyState(0xA4) & 0x8000)
                or (user32.GetAsyncKeyState(0xA5) & 0x8000)
            )
        if canonical_vk == 0x5B:  # Win
            return bool(
                (user32.GetAsyncKeyState(0x5B) & 0x8000)
                or (user32.GetAsyncKeyState(0x5C) & 0x8000)
            )
        return bool(user32.GetAsyncKeyState(canonical_vk) & 0x8000)

    def _prune_stale_keys(self) -> None:
        stale = {vk for vk in self._pressed_vks if not self._is_vk_physically_down(vk)}
        if stale:
            self._pressed_vks -= stale

    def _hook_loop(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())

        def low_level_handler(n_code: int, w_param: int, l_param: int) -> int:
            hook_handle = self._hook_handle
            if not self._running:
                return int(user32.CallNextHookEx(hook_handle, n_code, w_param, l_param))
            if n_code >= 0 and l_param:
                try:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    # Ignore injected events (LLKHF_INJECTED = 0x10) so our own SendInput never triggers hotkeys
                    if (kb.flags & 0x10) == 0:
                        vk = normalize_vk(int(kb.vkCode))
                        is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                        if self._handle_key_event(vk, is_down, is_up):
                            return 1
                except Exception as exc:
                    logger.error("Error in low-level keyboard hook: %s", exc)
            return int(user32.CallNextHookEx(hook_handle, n_code, w_param, l_param))

        self._hook_proc_ref = HOOKPROC(low_level_handler)
        self._hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_ref, None, 0)
        if not self._hook_handle:
            logger.error("Failed to install WH_KEYBOARD_LL hook (err=%d)", ctypes.get_last_error())
            return

        logger.info("Installed Win32 global keyboard hook (thread_id=%d)", self._thread_id)
        msg = wintypes.MSG()
        try:
            while self._running:
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_handle:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None
            logger.info("Uninstalled Win32 global keyboard hook.")

    def _fire_utility_shortcuts_down(self) -> None:
        utility_bindings = [
            ("scratchpad", self._scratchpad_vks, self.on_open_scratchpad),
            ("transform", self._transform_vks, self.on_transform_last),
            ("paste_last", self._paste_last_vks, self.on_paste_last),
            ("copy_last", self._copy_last_vks, self.on_copy_last),
        ]
        for key_name, chord, cb in utility_bindings:
            if chord and chord.issubset(self._pressed_vks) and cb:
                if not self._utility_latches.get(key_name, False):
                    self._utility_latches[key_name] = True
                    threading.Thread(target=cb, daemon=True).start()

    def _release_utility_latches(self) -> None:
        chord_map = {
            "scratchpad": self._scratchpad_vks,
            "transform": self._transform_vks,
            "paste_last": self._paste_last_vks,
            "copy_last": self._copy_last_vks,
        }
        for key_name, chord in chord_map.items():
            if self._utility_latches.get(key_name) and not chord.issubset(self._pressed_vks):
                self._utility_latches[key_name] = False

    def _handle_key_event(self, vk: int, is_down: bool, is_up: bool) -> None:
        with self._lock:
            # Physical watchdog: if PTT was active, verify keys are still physically down
            if self._ptt_active:
                active_ptt_chord = self._ptt_vks if self._ptt_vks.issubset(self._pressed_vks) else self._dictation_vks
                if active_ptt_chord and not all(self._is_vk_physically_down(k) for k in active_ptt_chord):
                    self._ptt_active = False
                    if self.on_ptt_stop:
                        threading.Thread(target=self.on_ptt_stop, daemon=True).start()

            if is_down:
                self._prune_stale_keys()
                was_ptt_down = bool(self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks))
                self._pressed_vks.add(vk)

                # Track if another non-PTT key was pressed while holding PTT
                if was_ptt_down and vk not in self._ptt_vks:
                    self._ptt_had_other_key = True

                # Esc cancels active dictation cleanly without hijacking idle apps
                if vk == 0x1B:
                    is_active_now = self._ptt_active or bool(self.is_dictation_active_fn and self.is_dictation_active_fn())
                    if is_active_now and self.on_cancel_dictation:
                        self._ptt_active = False
                        threading.Thread(target=self.on_cancel_dictation, daemon=True).start()
                        return

                if self._enable_voice_typing_hotkey:
                    ptt_now_down = bool(self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks))
                    dict_now_down = bool(self._dictation_vks and self._dictation_vks.issubset(self._pressed_vks))

                    if self._dictation_mode == "toggle":
                        # Toggle Mode: Pressing either the configured dictation key or PTT key switches dictation on/off
                        is_trigger_down = (ptt_now_down and not was_ptt_down) or (dict_now_down and not self._dictation_latch)
                        if is_trigger_down and not self._dictation_latch:
                            self._dictation_latch = True
                            if self.on_dictation_toggle:
                                threading.Thread(target=self.on_dictation_toggle, daemon=True).start()
                    elif self._dictation_mode == "push_to_talk":
                        # Pure Push-to-Talk: Holding the key captures speech, releasing finalizes and inserts
                        is_ptt_pressed = (ptt_now_down and not was_ptt_down) or (dict_now_down and not self._ptt_active)
                        if is_ptt_pressed:
                            self._ptt_press_time = time.monotonic()
                            self._ptt_had_other_key = False
                            if not self._ptt_active:
                                self._ptt_active = True
                                if self.on_ptt_start:
                                    threading.Thread(target=self.on_ptt_start, daemon=True).start()
                    else:
                        # Hybrid Mode: Hold to Speak (>0.35s) OR Quick Tap (<0.35s) to Toggle
                        is_ptt_pressed = (ptt_now_down and not was_ptt_down) or (dict_now_down and not self._ptt_active)
                        if is_ptt_pressed:
                            self._ptt_press_time = time.monotonic()
                            self._ptt_had_other_key = False
                            if not self._ptt_active:
                                self._ptt_active = True
                                if self.on_ptt_start:
                                    threading.Thread(target=self.on_ptt_start, daemon=True).start()

                # Meeting Recording Toggle
                if self._meeting_vks and self._meeting_vks.issubset(self._pressed_vks):
                    if not self._meeting_latch:
                        self._meeting_latch = True
                        if self.on_meeting_toggle:
                            threading.Thread(target=self.on_meeting_toggle, daemon=True).start()

                # Secondary utility shortcuts (Scratchpad, Transform, Paste Last, Copy Last)
                self._fire_utility_shortcuts_down()

                # Mode switching shortcuts (Win+F1 through Win+F5)
                is_win_down = (0x5B in self._pressed_vks) or self._is_vk_physically_down(0x5B)
                if is_win_down and self.on_mode_switch:
                    mode_map = {
                        0x70: "verbatim",
                        0x71: "clean",
                        0x72: "polished",
                        0x73: "structured",
                        0x74: "email",
                    }
                    if vk in mode_map:
                        threading.Thread(target=self.on_mode_switch, args=(mode_map[vk],), daemon=True).start()

            elif is_up:
                was_ptt_down = bool(self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks))
                self._pressed_vks.discard(vk)
                still_ptt_down = bool(self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks))

                # Detect crisp double-tap / triple-tap on PTT chord
                if self._enable_voice_typing_hotkey and was_ptt_down and not still_ptt_down and not self._ptt_had_other_key:
                    now = time.monotonic()
                    hold_dur = now - self._ptt_press_time
                    if hold_dur <= 0.35:
                        self._ptt_tap_timestamps = [t for t in self._ptt_tap_timestamps if (now - t) <= 0.65]
                        self._ptt_tap_timestamps.append(now)
                        if self._notetaker_triple_tap and len(self._ptt_tap_timestamps) >= 3:
                            self._ptt_tap_timestamps.clear()
                            if self.on_meeting_toggle:
                                threading.Thread(target=self.on_meeting_toggle, daemon=True).start()
                        elif self._hands_free_double_tap and len(self._ptt_tap_timestamps) >= 2:
                            if not self._notetaker_triple_tap:
                                self._ptt_tap_timestamps.clear()
                            cb = self.on_hands_free_toggle or self.on_dictation_toggle
                            if cb:
                                threading.Thread(target=cb, daemon=True).start()

                if self._dictation_latch and not self._dictation_vks.issubset(self._pressed_vks) and not (self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks)):
                    self._dictation_latch = False

                if self._meeting_latch and not self._meeting_vks.issubset(self._pressed_vks):
                    self._meeting_latch = False

                self._release_utility_latches()

                if self._ptt_active:
                    chord1 = self._ptt_vks and self._ptt_vks.issubset(self._pressed_vks)
                    chord2 = (self._dictation_mode in ("push_to_talk", "hybrid")) and bool(self._dictation_vks and self._dictation_vks.issubset(self._pressed_vks))
                    if not chord1 and not chord2:
                        self._ptt_active = False
                        if self.on_ptt_stop:
                            threading.Thread(target=self.on_ptt_stop, daemon=True).start()

            is_standalone_ptt = bool(
                self._enable_voice_typing_hotkey
                and (
                    (len(self._ptt_vks) == 1 and vk in self._ptt_vks)
                    or (len(self._dictation_vks) == 1 and vk in self._dictation_vks)
                )
                and (0x70 <= vk <= 0x87 or vk in (0x13, 0x14, 0xC0))
            )
            return is_standalone_ptt

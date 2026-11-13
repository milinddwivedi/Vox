"""
Ultra-Lightweight Meeting Screen Recorder for Windows with Premium Cursor & Interaction Effects.
Captures meeting presentations, slides, webcams, and shared screens in 1080p with:
- Crisp mouse pointer capture (DrawIconEx + fallback pointer)
- Animated fading click ripple animations (Champagne Gold left-click, Sapphire right-click)
- Directional scroll indicator animations (Up, Down, Left, Right)
- High-efficiency H.264 video encoding at 5 FPS (~10MB/hour)
- Synchronized Audio-Video muxing (AAC audio from meeting_mixed.wav)
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import time
from typing import Dict, List, Optional, Tuple

import av
import numpy as np

from voxlocal.core.logger import get_logger

logger = get_logger("video.screen")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Win32 GDI & User32 Prototypes
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HICON),
        ("ptScreenPos", POINT),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]


def get_system_fps_options() -> List[Tuple[str, int]]:
    """
    Returns framerate options tailored to the primary monitor's hardware refresh rate.
    Supports high-refresh rate displays up to native limit (e.g. 144Hz, 165Hz, 240Hz).
    """
    native_hz = 60
    try:
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen:
            rate = screen.refreshRate()
            if rate and rate > 30:
                native_hz = int(round(rate))
    except Exception:
        native_hz = 60

    options: List[Tuple[str, int]] = []
    if native_hz > 60:
        options.append((f"{native_hz} FPS (Native Display)", native_hz))
    if native_hz >= 120 and native_hz != 120:
        options.append(("120 FPS", 120))

    options.extend([
        ("60 FPS", 60),
        ("30 FPS", 30),
        ("24 FPS", 24),
        ("15 FPS (Recommended)", 15),
        ("10 FPS", 10),
    ])
    return options


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wintypes.BOOL

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.Polygon.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_int]
gdi32.Polygon.restype = wintypes.BOOL

user32.DrawIconEx.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HICON,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.HBRUSH,
    wintypes.UINT,
]
user32.DrawIconEx.restype = wintypes.BOOL

gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN

gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH

gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ

gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.restype = wintypes.BOOL

gdi32.RoundRect.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.RoundRect.restype = wintypes.BOOL

user32.DrawTextW.argtypes = [
    wintypes.HDC,
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.UINT,
]
user32.DrawTextW.restype = ctypes.c_int

gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int

gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetTextColor.restype = wintypes.COLORREF

gdi32.StretchBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.StretchBlt.restype = wintypes.BOOL

gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetStretchBltMode.restype = ctypes.c_int

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002

user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

SRCCOPY = 0x00CC0020
BI_RGB = 0
CURSOR_SHOWING = 0x00000001
DI_NORMAL = 0x0003
NULL_BRUSH = 5
TRANSPARENT = 1
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class ScreenRecorder:
    """
    Lightweight meeting screen video recorder with cursor and interaction animations.
    Captures presentations, slides, and shared windows cleanly with low CPU/disk footprint.
    """

    def __init__(self, fps: int = 15, crf: int = 26, target_hwnd: Optional[int] = None) -> None:
        self.fps = max(5, min(60, int(fps)))
        self.crf = max(18, min(36, int(crf)))
        self.target_hwnd: Optional[int] = target_hwnd
        self._is_window_capture: bool = False
        self._capture_x: int = 0
        self._capture_y: int = 0

        self._lock = threading.RLock()
        self._state = "idle"  # idle | recording | paused
        self._output_path: Optional[Path] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()

        self._width = 0
        self._height = 0
        self._total_frames = 0
        self._start_time = 0.0

        # Animation state lists: (x, y, start_ts, kind)
        self._active_ripples: List[Tuple[int, int, float, str]] = []
        self._active_scrolls: List[Tuple[int, int, float, str]] = []
        self._anim_lock = threading.Lock()

        # Low-level mouse hook handle & thread
        self._mouse_hook = None
        self._hook_thread: Optional[threading.Thread] = None
        self._hook_stop_evt = threading.Event()
        self._hook_callback_c = None

        # Live Annotation Engine (composited off-UI thread directly into bgra_buf)
        self._annotation_engine: Optional[Any] = None
        self._annotation_lock = threading.Lock()

    def set_annotation_engine(self, engine: Optional[Any]) -> None:
        """Attach or detach a shared AnnotationEngine for live frame compositing."""
        with self._annotation_lock:
            self._annotation_engine = engine

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._state in ("recording", "paused")

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    QUALITY_PRESETS = {
        "compact": (15, 32),    # 15 FPS, CRF 32 (~20-30 MB/hr)
        "balanced": (30, 26),   # 30 FPS, CRF 26 (~80-120 MB/hr)
        "high": (60, 20),       # 60 FPS, CRF 20 (~250-400 MB/hr)
    }

    def start(
        self,
        output_mp4_path: Path,
        target_hwnd: Optional[int] = None,
        fps: Optional[int] = None,
        resolution: str = "native",
        crf: Optional[int] = None,
        preset: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Start capturing PC screen or meeting window to `output_mp4_path`."""
        with self._lock:
            if self._state != "idle":
                return False, "Screen recorder is already active."

            if target_hwnd is not None:
                self.target_hwnd = target_hwnd

            if preset and preset.lower() in self.QUALITY_PRESETS:
                p_fps, p_crf = self.QUALITY_PRESETS[preset.lower()]
                self.fps = p_fps
                self.crf = p_crf

            if fps is not None:
                self.fps = max(5, min(60, int(fps)))
            if crf is not None:
                self.crf = max(18, min(36, int(crf)))

            self._output_path = Path(output_mp4_path)
            os.makedirs(self._output_path.parent, exist_ok=True)

            win_rect = None
            if self.target_hwnd and user32.IsWindow(self.target_hwnd):
                if user32.IsIconic(self.target_hwnd):
                    user32.ShowWindow(self.target_hwnd, 9)  # SW_RESTORE
                    time.sleep(0.05)
                try:
                    user32.SetForegroundWindow(self.target_hwnd)
                except Exception:
                    pass

                r = RECT()
                dwmapi = getattr(ctypes.windll, "dwmapi", None)
                got_bounds = False
                if dwmapi:
                    try:
                        res = dwmapi.DwmGetWindowAttribute(
                            wintypes.HWND(self.target_hwnd),
                            wintypes.DWORD(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
                            ctypes.byref(r),
                            ctypes.sizeof(r),
                        )
                        if res == 0:
                            got_bounds = True
                    except Exception:
                        pass
                if not got_bounds:
                    user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))

                w = r.right - r.left
                h = r.bottom - r.top
                if w >= 128 and h >= 128:
                    win_rect = (r.left, r.top, w, h)

            if win_rect:
                self._capture_x = max(0, win_rect[0])
                self._capture_y = max(0, win_rect[1])
                self._width = win_rect[2]
                self._height = win_rect[3]
                self._is_window_capture = True
            else:
                self._capture_x = 0
                self._capture_y = 0
                self._width = int(user32.GetSystemMetrics(0))
                self._height = int(user32.GetSystemMetrics(1))
                self._is_window_capture = False

            # Resolution scaling if requested (e.g., 1080p, 720p)
            res_lower = (resolution or "native").lower()
            if "1080" in res_lower and (self._width > 1920 or self._height > 1080):
                ratio = min(1920 / self._width, 1080 / self._height)
                self._width = int(round(self._width * ratio))
                self._height = int(round(self._height * ratio))
            elif "720" in res_lower and (self._width > 1280 or self._height > 720):
                ratio = min(1280 / self._width, 720 / self._height)
                self._width = int(round(self._width * ratio))
                self._height = int(round(self._height * ratio))

            # H.264 requires even dimensions
            if self._width % 2 != 0:
                self._width -= 1
            if self._height % 2 != 0:
                self._height -= 1
            self._width = max(128, self._width)
            self._height = max(128, self._height)

            self._stop_evt.clear()
            self._pause_evt.clear()
            self._total_frames = 0
            self._elapsed = 0.0
            self._resume_ts = time.perf_counter()
            self._last_pts = -1
            self._start_time = time.perf_counter()
            self._state = "recording"

            with self._anim_lock:
                self._active_ripples.clear()
                self._active_scrolls.clear()

            # Start low-level mouse hook for clicks and scroll tracking
            self._start_mouse_hook()

            self._thread = threading.Thread(
                target=self._capture_worker,
                name="ScreenCaptureWorker",
                daemon=True,
            )
            self._thread.start()

            logger.info(
                "Screen recorder started: %s (%dx%d @ %d fps, CRF=%d)",
                self._output_path.name,
                self._width,
                self._height,
                self.fps,
                self.crf,
            )
            return True, f"Screen recording active ({self._width}x{self._height} @ {self.fps}fps)"

    def pause(self) -> bool:
        with self._lock:
            if self._state != "recording":
                return False
            self._elapsed += time.perf_counter() - self._resume_ts
            self._state = "paused"
            self._pause_evt.set()
            logger.info("Screen recorder paused")
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._state != "paused":
                return False
            self._resume_ts = time.perf_counter()
            self._state = "recording"
            self._pause_evt.clear()
            logger.info("Screen recorder resumed")
            return True

    def stop(self) -> Optional[str]:
        """Stop screen recording and cleanly finalize the MP4 file."""
        with self._lock:
            if self._state == "idle":
                return None
            self._state = "idle"
            self._stop_evt.set()
            self._pause_evt.clear()

        self._stop_mouse_hook()

        if self._thread:
            self._thread.join(timeout=4.0)
            self._thread = None

        if self._output_path and self._output_path.exists():
            size_mb = os.path.getsize(self._output_path) / (1024 * 1024)
            logger.info(
                "Screen recording finalized: %s (%.2f MB, %d frames)",
                self._output_path.name,
                size_mb,
                self._total_frames,
            )
            return str(self._output_path)
        return None

    # ------------------------------------------------------------------
    # Mouse Hook & Interaction Effects (Clicks & Scrolls)
    # ------------------------------------------------------------------
    def _start_mouse_hook(self) -> None:
        self._hook_stop_evt.clear()
        self._hook_thread_id = None

        def _hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and not self._pause_evt.is_set():
                try:
                    if lParam:
                        data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        now = time.perf_counter()
                        x, y = int(data.pt.x), int(data.pt.y)

                        if wParam == 0x0201:  # WM_LBUTTONDOWN
                            with self._anim_lock:
                                self._active_ripples.append((x, y, now, "left"))
                                if len(self._active_ripples) > 12:
                                    self._active_ripples.pop(0)

                        elif wParam == 0x0204:  # WM_RBUTTONDOWN
                            with self._anim_lock:
                                self._active_ripples.append((x, y, now, "right"))
                                if len(self._active_ripples) > 12:
                                    self._active_ripples.pop(0)

                        elif wParam == 0x020A:  # WM_MOUSEWHEEL
                            delta = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                            direction = "up" if delta > 0 else "down"
                            with self._anim_lock:
                                self._active_scrolls.append((x, y, now, direction))
                                if len(self._active_scrolls) > 8:
                                    self._active_scrolls.pop(0)

                        elif wParam == 0x020E:  # WM_MOUSEHWHEEL
                            delta = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                            direction = "right" if delta > 0 else "left"
                            with self._anim_lock:
                                self._active_scrolls.append((x, y, now, direction))
                                if len(self._active_scrolls) > 8:
                                    self._active_scrolls.pop(0)
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_callback_c = HOOKPROC(_hook_proc)

        def _run_hook():
            self._hook_thread_id = kernel32.GetCurrentThreadId()
            hdesk = None
            orig_desk = None
            try:
                orig_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk and not user32.SetThreadDesktop(hdesk):
                    user32.CloseDesktop(hdesk)
                    hdesk = None
            except Exception:
                hdesk = None

            hook = user32.SetWindowsHookExW(14, self._hook_callback_c, 0, 0)
            self._mouse_hook = hook
            msg = wintypes.MSG()
            while not self._hook_stop_evt.is_set():
                if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                    if msg.message == 0x0012:  # WM_QUIT
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.002)

            if hook:
                user32.UnhookWindowsHookEx(hook)
                self._mouse_hook = None
            if hdesk:
                try:
                    if orig_desk:
                        user32.SetThreadDesktop(orig_desk)
                    user32.CloseDesktop(hdesk)
                except Exception:
                    pass

        self._hook_thread = threading.Thread(target=_run_hook, name="ScreenRecorderMouseHook", daemon=True)
        self._hook_thread.start()

    def _stop_mouse_hook(self) -> None:
        self._hook_stop_evt.set()
        if getattr(self, "_hook_thread_id", None):
            user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)
        if self._hook_thread:
            self._hook_thread.join(timeout=1.0)
            self._hook_thread = None

    # ------------------------------------------------------------------
    # Frame Capture & Render Loop
    # ------------------------------------------------------------------
    def _capture_worker(self) -> None:
        """Background frame grabber and H.264 compressor with cursor overlay and click ripples."""
        hdesk = None
        orig_desk = None
        try:
            orig_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk and not user32.SetThreadDesktop(hdesk):
                user32.CloseDesktop(hdesk)
                hdesk = None
        except Exception:
            hdesk = None

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, self._width, self._height)
        gdi32.SelectObject(hdc_mem, hbm)

        bmi = ctypes.create_string_buffer(40)
        ctypes.c_uint32.from_buffer(bmi, 0).value = 40
        ctypes.c_int32.from_buffer(bmi, 4).value = self._width
        ctypes.c_int32.from_buffer(bmi, 8).value = -self._height  # top-down DIB
        ctypes.c_uint16.from_buffer(bmi, 12).value = 1
        ctypes.c_uint16.from_buffer(bmi, 14).value = 32           # 32-bit BGRA
        ctypes.c_uint32.from_buffer(bmi, 16).value = BI_RGB

        bgra_buf = np.empty((self._height, self._width, 4), dtype=np.uint8)

        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)

        container = None
        stream = None
        frame_interval = 1.0 / float(self.fps)

        try:
            container = av.open(str(self._output_path), mode="w")
            stream = container.add_stream("libx264", rate=self.fps)
            stream.width = self._width
            stream.height = self._height
            stream.pix_fmt = "yuv420p"
            stream.options = {
                "crf": str(self.crf),
                "preset": "ultrafast",
                "tune": "zerolatency",
            }

            next_tick = time.perf_counter()
            last_pts = -1

            while not self._stop_evt.is_set():
                now = time.perf_counter()
                if now < next_tick:
                    sleep_dur = max(0.0005, next_tick - now)
                    if self._stop_evt.wait(sleep_dur):
                        break
                    continue

                next_tick += frame_interval
                if now - next_tick > 0.5:
                    next_tick = now + frame_interval

                if self._pause_evt.is_set():
                    time.sleep(0.02)
                    continue

                # Dynamically track window origin & bounds if capturing specific call window
                cur_x = self._capture_x
                cur_y = self._capture_y
                src_w = self._width
                src_h = self._height

                if self._is_window_capture and self.target_hwnd and user32.IsWindow(self.target_hwnd):
                    r = RECT()
                    has_bounds = False
                    dwmapi = getattr(ctypes.windll, "dwmapi", None)
                    if dwmapi:
                        try:
                            res = dwmapi.DwmGetWindowAttribute(
                                wintypes.HWND(self.target_hwnd),
                                wintypes.DWORD(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
                                ctypes.byref(r),
                                ctypes.sizeof(r),
                            )
                            if res == 0:
                                has_bounds = True
                        except Exception:
                            has_bounds = False
                    if not has_bounds:
                        user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))

                    w_cand = r.right - r.left
                    h_cand = r.bottom - r.top
                    if w_cand >= 64 and h_cand >= 64:
                        cur_x = max(0, r.left)
                        cur_y = max(0, r.top)
                        src_w = w_cand
                        src_h = h_cand

                # 1. Grab screen/window frame
                if self._is_window_capture and self.target_hwnd and user32.IsWindow(self.target_hwnd):
                    # Isolated window capture via PrintWindow(PW_RENDERFULLCONTENT)
                    pw_ok = user32.PrintWindow(self.target_hwnd, hdc_mem, PW_RENDERFULLCONTENT)
                    if not pw_ok:
                        user32.PrintWindow(self.target_hwnd, hdc_mem, 0)
                elif src_w == self._width and src_h == self._height:
                    gdi32.BitBlt(hdc_mem, 0, 0, self._width, self._height, hdc_screen, cur_x, cur_y, SRCCOPY)
                else:
                    gdi32.SetStretchBltMode(hdc_mem, 3)  # COLORONCOLOR
                    gdi32.StretchBlt(hdc_mem, 0, 0, self._width, self._height, hdc_screen, cur_x, cur_y, src_w, src_h, SRCCOPY)

                # 2. Render Animated Click Ripples (fading expanding circular waves)
                self._render_ripples(hdc_mem, now, offset_x=cur_x, offset_y=cur_y)

                # 3. Render Animated Scroll Badges (fading directional indicators)
                self._render_scrolls(hdc_mem, now, offset_x=cur_x, offset_y=cur_y)

                # 4. Render Windows Mouse Cursor
                self._render_cursor(hdc_mem, ci, offset_x=cur_x, offset_y=cur_y)

                # 5. Extract frame pixels directly into 32-bit BGRA buffer
                lines = gdi32.GetDIBits(
                    hdc_mem, hbm, 0, self._height, ctypes.c_void_p(bgra_buf.ctypes.data), ctypes.byref(bmi), 0
                )
                if lines != self._height:
                    continue

                # 5b. Composite Live Vector Annotations & Physical Blur Redactions
                with self._annotation_lock:
                    ann_engine = self._annotation_engine
                if ann_engine is not None and getattr(ann_engine, "is_visible", lambda: True)():
                    try:
                        scale_w = float(self._width) / float(max(1, src_w)) if src_w != self._width else 1.0
                        scale_h = float(self._height) / float(max(1, src_h)) if src_h != self._height else 1.0
                        ann_engine.render_to_bgra(
                            bgra_buf,
                            offset_x=cur_x,
                            offset_y=cur_y,
                            scale_x=scale_w,
                            scale_y=scale_h,
                        )
                    except Exception:
                        pass

                # 6. Pass BGRA directly to PyAV with wall-clock PTS synchronization
                active_elapsed = self._elapsed + (now - self._resume_ts)
                target_pts = int(round(active_elapsed * self.fps))
                if target_pts <= last_pts:
                    target_pts = last_pts + 1
                last_pts = target_pts

                frame = av.VideoFrame.from_ndarray(bgra_buf, format="bgra")
                frame.pts = target_pts
                for packet in stream.encode(frame):
                    container.mux(packet)
                self._total_frames += 1

            # Flush remaining packets
            if stream and container:
                for packet in stream.encode():
                    container.mux(packet)

        except Exception as exc:
            logger.error("Screen recording worker error: %s", exc)
        finally:
            if container:
                try:
                    container.close()
                except Exception:
                    pass
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            if hdesk:
                try:
                    if orig_desk:
                        user32.SetThreadDesktop(orig_desk)
                    user32.CloseDesktop(hdesk)
                except Exception:
                    pass

    def _render_cursor(self, hdc_mem, ci: CURSORINFO, offset_x: int = 0, offset_y: int = 0) -> None:
        """Render live mouse pointer icon onto the memory DC."""
        try:
            if user32.GetCursorInfo(ctypes.byref(ci)) and (ci.flags & CURSOR_SHOWING):
                cx = int(ci.ptScreenPos.x) - offset_x
                cy = int(ci.ptScreenPos.y) - offset_y
                if 0 <= cx < self._width and 0 <= cy < self._height:
                    drawn = user32.DrawIconEx(
                        hdc_mem,
                        cx,
                        cy,
                        ci.hCursor,
                        0,
                        0,
                        0,
                        None,
                        DI_NORMAL,
                    )
                    if not drawn:
                        self._draw_fallback_cursor(hdc_mem, cx, cy)
        except Exception:
            pass

    def _draw_fallback_cursor(self, hdc_mem, x: int, y: int) -> None:
        """Draw a sleek, sharp pointer if DrawIconEx is restricted."""
        pen = gdi32.CreatePen(0, 2, 0x00201005)  # dark border
        brush = gdi32.CreateSolidBrush(0x00FFFFFF)  # white fill
        old_pen = gdi32.SelectObject(hdc_mem, pen)
        old_brush = gdi32.SelectObject(hdc_mem, brush)
        pts = (POINT * 4)(
            POINT(x, y),
            POINT(x + 14, y + 14),
            POINT(x + 6, y + 14),
            POINT(x, y + 19),
        )
        ctypes.windll.gdi32.Polygon(hdc_mem, ctypes.byref(pts), 4)
        gdi32.SelectObject(hdc_mem, old_pen)
        gdi32.SelectObject(hdc_mem, old_brush)
        gdi32.DeleteObject(pen)
        gdi32.DeleteObject(brush)

    def _render_ripples(self, hdc_mem, now: float, offset_x: int = 0, offset_y: int = 0) -> None:
        """Draw smooth, expanding faded rings at mouse click locations."""
        with self._anim_lock:
            remaining = []
            old_brush = gdi32.SelectObject(hdc_mem, gdi32.GetStockObject(NULL_BRUSH))
            for x, y, start_t, kind in self._active_ripples:
                dt = now - start_t
                if dt < 0.45:
                    remaining.append((x, y, start_t, kind))
                    rx = x - offset_x
                    ry = y - offset_y
                    if -50 <= rx <= self._width + 50 and -50 <= ry <= self._height + 50:
                        progress = dt / 0.45
                        radius = 8 + int(24 * progress)
                        alpha = 1.0 - progress
                        pen_w = max(1, int(3 * alpha))

                        # Champagne Gold for left-click, Sapphire for right-click
                        if kind == "left":
                            color = (int(240 * alpha)) | (int(190 * alpha) << 8) | (int(75 * alpha) << 16)
                        else:
                            color = (int(60 * alpha)) | (int(190 * alpha) << 8) | (int(245 * alpha) << 16)

                        pen = gdi32.CreatePen(0, pen_w, color)
                        old_pen = gdi32.SelectObject(hdc_mem, pen)
                        gdi32.Ellipse(hdc_mem, rx - radius, ry - radius, rx + radius, ry + radius)
                        gdi32.SelectObject(hdc_mem, old_pen)
                        gdi32.DeleteObject(pen)

            gdi32.SelectObject(hdc_mem, old_brush)
            self._active_ripples = remaining

    def _render_scrolls(self, hdc_mem, now: float, offset_x: int = 0, offset_y: int = 0) -> None:
        """Draw a sleek, translucent directional scroll badge near the cursor."""
        with self._anim_lock:
            remaining = []
            symbols = {"up": "▲", "down": "▼", "left": "◀", "right": "▶"}

            for x, y, start_t, direction in self._active_scrolls:
                dt = now - start_t
                if dt < 0.55:
                    remaining.append((x, y, start_t, direction))
                    progress = dt / 0.55
                    alpha = 1.0 - progress

                    # Badge dimensions offset from cursor
                    bx, by = (x - offset_x) + 20, (y - offset_y) - 10
                    if 0 <= bx <= self._width and 0 <= by <= self._height:
                        bw, bh = 22, 22
                        border_color = (int(215 * alpha)) | (int(175 * alpha) << 8) | (int(90 * alpha) << 16)
                        bg_color = (int(35 * alpha)) | (int(30 * alpha) << 8) | (int(25 * alpha) << 16)

                        pen = gdi32.CreatePen(0, 1, border_color)
                        brush = gdi32.CreateSolidBrush(bg_color)
                        old_pen = gdi32.SelectObject(hdc_mem, pen)
                        old_brush = gdi32.SelectObject(hdc_mem, brush)

                        gdi32.RoundRect(hdc_mem, bx, by, bx + bw, by + bh, 6, 6)

                        # Text symbol
                        symbol = symbols.get(direction, "▼")
                        rect = RECT(bx, by, bx + bw, by + bh)
                        gdi32.SetBkMode(hdc_mem, TRANSPARENT)
                        text_color = (int(245 * alpha)) | (int(225 * alpha) << 8) | (int(185 * alpha) << 16)
                        gdi32.SetTextColor(hdc_mem, text_color)
                        user32.DrawTextW(hdc_mem, symbol, len(symbol), ctypes.byref(rect), DT_CENTER | DT_VCENTER | DT_SINGLELINE)

                        gdi32.SelectObject(hdc_mem, old_pen)
                        gdi32.SelectObject(hdc_mem, old_brush)
                        gdi32.DeleteObject(pen)
                        gdi32.DeleteObject(brush)

            self._active_scrolls = remaining

    # ------------------------------------------------------------------
    # High-Performance Zero-Loss Audio-Video Multiplexer
    # ------------------------------------------------------------------
    @staticmethod
    def mux_audio_into_video(video_mp4_path: Path, audio_wav_path: Path) -> bool:
        """
        Merge meeting_mixed.wav into meeting_screen.mp4 using stream-copy remuxing (zero re-encoding).
        Interleaves H.264 video packets and AAC audio packets chronologically for guaranteed
        lip-sync, smooth playback, and rich, loud microphone audio across all players.
        """
        import gc

        if not video_mp4_path.exists() or not audio_wav_path.exists():
            return False

        temp_out = video_mp4_path.with_name("temp_muxed_" + video_mp4_path.name)
        if temp_out.exists():
            try:
                temp_out.unlink()
            except Exception:
                pass

        in_v = None
        in_a = None
        out_c = None
        try:
            in_v = av.open(str(video_mp4_path))
            in_a = av.open(str(audio_wav_path))
            out_c = av.open(str(temp_out), mode="w", format="mp4")

            v_in_stream = in_v.streams.video[0]
            out_v = out_c.add_stream_from_template(v_in_stream)
            out_a = out_c.add_stream("aac", rate=48000, layout="mono")
            try:
                out_a.bit_rate = 256000
            except Exception:
                pass

            resampler = av.AudioResampler(format="fltp", layout="mono", rate=48000)

            # 1. Encode broadcast-grade 48 kHz AAC audio packets
            audio_packets = []
            pts_a = 0
            for a_frame in in_a.decode(audio=0):
                for r_frame in resampler.resample(a_frame):
                    r_frame.pts = pts_a
                    pts_a += r_frame.samples
                    for packet in out_a.encode(r_frame):
                        pts_sec = float(packet.pts * packet.time_base) if packet.pts is not None else 0.0
                        audio_packets.append((pts_sec, packet))

            for r_frame in resampler.resample(None):
                r_frame.pts = pts_a
                pts_a += r_frame.samples
                for packet in out_a.encode(r_frame):
                    pts_sec = float(packet.pts * packet.time_base) if packet.pts is not None else 0.0
                    audio_packets.append((pts_sec, packet))

            for packet in out_a.encode():
                pts_sec = float(packet.pts * packet.time_base) if packet.pts is not None else 0.0
                audio_packets.append((pts_sec, packet))

            # 2. Interleave audio and video packets:
            # Video packets must preserve their natural demuxed DTS stream order (zero re-encoding)
            # while audio packets are injected chronologically based on presentation timestamp.
            a_idx = 0
            for v_pkt in in_v.demux(v_in_stream):
                if v_pkt.dts is None:
                    continue
                v_pkt.stream = out_v
                v_pts_sec = float(v_pkt.pts * v_pkt.time_base) if v_pkt.pts is not None else 0.0

                while a_idx < len(audio_packets) and audio_packets[a_idx][0] <= v_pts_sec:
                    out_c.mux(audio_packets[a_idx][1])
                    a_idx += 1

                out_c.mux(v_pkt)

            # Flush any remaining audio packets
            while a_idx < len(audio_packets):
                out_c.mux(audio_packets[a_idx][1])
                a_idx += 1

            in_v.close()
            in_a.close()
            out_c.close()
            del in_v, in_a, out_c
            in_v = in_a = out_c = None
            gc.collect()

            # Atomically replace video file with muxed file (with retry & fallback if file is open in a media player)
            if temp_out.exists() and temp_out.stat().st_size > 1000:
                replaced = False
                for attempt in range(3):
                    try:
                        os.replace(temp_out, video_mp4_path)
                        replaced = True
                        break
                    except PermissionError:
                        time.sleep(0.3)
                if not replaced:
                    fallback_path = video_mp4_path.with_name(video_mp4_path.stem + "_with_audio.mp4")
                    try:
                        os.replace(temp_out, fallback_path)
                        logger.info("Target locked by player; saved muxed audio video to %s", fallback_path.name)
                        return True
                    except Exception:
                        pass
                else:
                    logger.info("Successfully muxed meeting audio into %s", video_mp4_path.name)
                    return True
        except Exception as exc:
            logger.error("Audio-video muxing error: %s", exc)
        finally:
            if in_v:
                try: in_v.close()
                except Exception: pass
            if in_a:
                try: in_a.close()
                except Exception: pass
            if out_c:
                try: out_c.close()
                except Exception: pass
            del in_v, in_a, out_c
            gc.collect()
            if temp_out.exists():
                try: temp_out.unlink()
                except Exception: pass
        return False

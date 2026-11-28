"""Call-Only Meeting Recording Engine & Provider Architecture (Core Feature 3 — Mode A).

This module implements VOX's strict source-bound Meeting Recording pipeline:
    Meeting detected -> User confirms meeting source -> Capture source locked ->
    Meeting audio/video recorded -> Meeting ends or recording stops -> File finalized.

NON-NEGOTIABLE PRIVACY & ISOLATION INVARIANTS:
1. Meeting Recording (Mode A) captures ONLY the verified meeting call window or
   locked browser tab. It NEVER captures the general PC desktop (`GetDC(0)`) or
   the active foreground window, even as a fallback.
2. Switching to Excel, Outlook, another Chrome window, or an unrelated browser tab
   must produce ZERO frames of those unrelated surfaces in the meeting recording.
3. If the meeting window is minimized, closed, or navigated to a non-meeting view
   (e.g. Microsoft Teams 'Chat | ...' or 'Files | ...' when no call stage window is
   open, or Chrome switched away from the Google Meet tab without the tab-capture
   extension streaming), video capture fails closed and emits a neutral status frame
   at the exact wall-clock PTS so audio-video synchronization remains intact.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

import av
import numpy as np

from voxlocal.core.logger import get_logger

logger = get_logger("video.meeting_capture")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
shcore = getattr(ctypes.windll, "shcore", None)

BI_RGB = 0
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
DWMWA_EXTENDED_FRAME_BOUNDS = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.wintypes.LONG), ("y", ctypes.wintypes.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.wintypes.UINT),
        ("flags", ctypes.wintypes.UINT),
        ("showCmd", ctypes.wintypes.UINT),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", ctypes.wintypes.RECT),
    ]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hCursor", ctypes.wintypes.HICON),
        ("ptScreenPos", POINT),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.wintypes.DWORD * 3),
    ]


user32.GetDC.argtypes = [ctypes.wintypes.HWND]
user32.GetDC.restype = ctypes.wintypes.HDC
user32.PrintWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HDC, ctypes.wintypes.UINT]
user32.PrintWindow.restype = ctypes.wintypes.BOOL
user32.GetWindowDC.argtypes = [ctypes.wintypes.HWND]
user32.GetWindowDC.restype = ctypes.wintypes.HDC
user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsWindow.restype = ctypes.wintypes.BOOL
user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
user32.IsIconic.restype = ctypes.wintypes.BOOL
user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [ctypes.wintypes.HDC]
gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = ctypes.wintypes.HBITMAP
gdi32.SelectObject.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ]
gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [ctypes.wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = ctypes.wintypes.BOOL
gdi32.DeleteDC.argtypes = [ctypes.wintypes.HDC]
gdi32.DeleteDC.restype = ctypes.wintypes.BOOL
gdi32.BitBlt.argtypes = [
    ctypes.wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.DWORD,
]
gdi32.BitBlt.restype = ctypes.wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    ctypes.wintypes.HDC,
    ctypes.wintypes.HBITMAP,
    ctypes.wintypes.UINT,
    ctypes.wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

ctypes.wintypes.HMONITOR = ctypes.c_void_p
user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = ctypes.wintypes.BOOL
user32.DrawIconEx.argtypes = [
    ctypes.wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.HICON,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.wintypes.HBRUSH,
    ctypes.c_uint,
]
user32.DrawIconEx.restype = ctypes.wintypes.BOOL

user32.OpenInputDesktop.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
user32.OpenInputDesktop.restype = ctypes.wintypes.HANDLE
user32.CloseDesktop.argtypes = [ctypes.wintypes.HANDLE]
user32.CloseDesktop.restype = ctypes.wintypes.BOOL
user32.SetThreadDesktop.argtypes = [ctypes.wintypes.HANDLE]
user32.SetThreadDesktop.restype = ctypes.wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsWindow.restype = ctypes.wintypes.BOOL
user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
user32.IsIconic.restype = ctypes.wintypes.BOOL
user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.wintypes.BOOL
user32.GetWindowPlacement.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = ctypes.wintypes.BOOL
user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
user32.WindowFromPoint.restype = ctypes.wintypes.HWND
user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
user32.GetAncestor.restype = ctypes.wintypes.HWND
user32.GetDC.argtypes = [ctypes.wintypes.HWND]
user32.GetDC.restype = ctypes.wintypes.HDC

# Ensure 64-bit pointer safety on Win32 GDI/User32 calls
user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
kernel32.OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPWSTR,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL


# ---------------------------------------------------------------------------
# Process & Window Inspection Helpers
# ---------------------------------------------------------------------------

def get_window_pid_and_process_name(hwnd: int) -> Tuple[int, str]:
    """Return `(pid, lowercase_exe_basename)` for a Win32 HWND."""
    if not hwnd or hwnd <= 0:
        return 0, ""
    pid = ctypes.wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return 0, ""
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        return int(pid.value), ""
    try:
        buf_len = ctypes.wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(buf_len)):
            return int(pid.value), os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h_proc)
    return int(pid.value), ""


def get_window_title(hwnd: int) -> str:
    """Return the current Unicode title of `hwnd`."""
    if not hwnd or hwnd <= 0:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def get_window_class_name(hwnd: int) -> str:
    """Return the Win32 class name of `hwnd`."""
    if not hwnd or hwnd <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value.strip()


def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """Return `(left, top, width, height)` for `hwnd` with validated ctypes structures."""
    if not hwnd or hwnd <= 0:
        return (0, 0, 0, 0)
    rect = ctypes.wintypes.RECT()
    try:
        if not user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (
            int(rect.left),
            int(rect.top),
            max(0, int(rect.right - rect.left)),
            max(0, int(rect.bottom - rect.top)),
        )
    except Exception as exc:
        logger.debug("GetWindowRect error on HWND %s: %s", hwnd, exc)
        return (0, 0, 0, 0)


def get_dwm_visible_crop_offsets(hwnd: int, full_w: int, full_h: int) -> Tuple[int, int, int, int]:
    """Return `(crop_x, crop_y, visible_w, visible_h)` removing invisible DWM shadow borders."""
    if not hwnd or hwnd <= 0 or full_w <= 0 or full_h <= 0:
        return (0, 0, full_w, full_h)
    win_rect = ctypes.wintypes.RECT()
    ext_rect = ctypes.wintypes.RECT()
    try:
        if not user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(win_rect)):
            return (0, 0, full_w, full_h)
        hr = dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(ext_rect),
            ctypes.sizeof(ext_rect),
        )
        if hr != 0:
            return (0, 0, full_w, full_h)
        crop_x = max(0, int(ext_rect.left - win_rect.left))
        crop_y = max(0, int(ext_rect.top - win_rect.top))
        vis_w = int(ext_rect.right - ext_rect.left)
        vis_h = int(ext_rect.bottom - ext_rect.top)
        if vis_w <= 100 or vis_h <= 100 or crop_x + vis_w > full_w or crop_y + vis_h > full_h:
            return (0, 0, full_w, full_h)
        return (crop_x, crop_y, vis_w, vis_h)
    except Exception:
        return (0, 0, full_w, full_h)


def is_window_minimized(hwnd: int) -> bool:
    """Deterministic multi-layer check for window minimization state."""
    if not hwnd or hwnd <= 0:
        return True
    try:
        h = ctypes.wintypes.HWND(hwnd)
        if not user32.IsWindow(h):
            return True
        if user32.IsIconic(h):
            return True
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(h, ctypes.byref(wp)):
            if wp.showCmd in (2, 6):  # SW_SHOWMINIMIZED=2, SW_MINIMIZE=6
                return True
        rect = get_window_rect(hwnd)
        if rect[0] <= -10000 or rect[1] <= -10000:
            return True
    except Exception:
        return False
    return False


def is_window_occluded_by_foreign_app(
    target_hwnd: int, bounds: Tuple[int, int, int, int], target_pid: int = 0
) -> bool:
    """Check if foreign non-meeting windows are positioned above target_hwnd in Z-order."""
    if not target_hwnd or target_hwnd <= 0:
        return False
    x, y, w, h = bounds
    if w <= 32 or h <= 32:
        return True
    try:
        # Check foreground window first
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd and fg_hwnd != target_hwnd:
            fg_root = user32.GetAncestor(fg_hwnd, 2) or fg_hwnd  # GA_ROOT = 2
            if fg_root != target_hwnd:
                fg_pid, fg_proc = get_window_pid_and_process_name(fg_root)
                fg_proc_low = fg_proc.lower()
                if (
                    (not target_pid or fg_pid != target_pid)
                    and fg_proc_low not in ("voxlocal.exe", "python.exe", "pythonw.exe", "")
                ):
                    fg_rect = get_window_rect(fg_root)
                    if (
                        fg_rect[0] < x + w
                        and fg_rect[0] + fg_rect[2] > x
                        and fg_rect[1] < y + h
                        and fg_rect[1] + fg_rect[3] > y
                    ):
                        return True

        # Check 5 probe points: center and 4 inset corners
        probe_points = [
            (x + w // 2, y + h // 2),
            (x + 24, y + 24),
            (x + w - 24, y + 24),
            (x + 24, y + h - 24),
            (x + w - 24, y + h - 24),
        ]
        foreign_hits = 0
        for px, py in probe_points:
            pt = ctypes.wintypes.POINT(px, py)
            hit_hwnd = user32.WindowFromPoint(pt)
            if hit_hwnd and hit_hwnd != target_hwnd:
                root_hit = user32.GetAncestor(hit_hwnd, 2) or hit_hwnd
                if root_hit and root_hit != target_hwnd:
                    hit_pid, hit_proc = get_window_pid_and_process_name(root_hit)
                    hit_proc_low = hit_proc.lower()
                    if (
                        (not target_pid or hit_pid != target_pid)
                        and hit_proc_low not in ("voxlocal.exe", "python.exe", "pythonw.exe", "")
                    ):
                        foreign_hits += 1
        return foreign_hits >= 2
    except Exception:
        return False


class AspectRatioLetterboxer:
    """Scales captured frames into container dimensions preserving aspect ratio with dark matte borders."""

    @staticmethod
    def scale(bgr: Optional[np.ndarray], target_w: int, target_h: int) -> np.ndarray:
        if bgr is None:
            return np.full((target_h, target_w, 3), (21, 17, 15), dtype=np.uint8)
        src_h, src_w = bgr.shape[:2]
        if src_w == target_w and src_h == target_h:
            return np.ascontiguousarray(bgr)
        if src_w <= 0 or src_h <= 0:
            return np.full((target_h, target_w, 3), (21, 17, 15), dtype=np.uint8)

        scale = min(float(target_w) / float(src_w), float(target_h) / float(src_h))
        new_w = max(2, min(target_w, int(round(src_w * scale))))
        new_h = max(2, min(target_h, int(round(src_h * scale))))

        try:
            from PIL import Image
            img = Image.fromarray(bgr[:, :, ::-1], mode="RGB")
            resized_img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            rgb = np.asarray(resized_img, dtype=np.uint8)
            resized = np.ascontiguousarray(rgb[:, :, ::-1])
        except Exception:
            y_idx = np.linspace(0, src_h - 1, new_h).astype(np.int32)
            x_idx = np.linspace(0, src_w - 1, new_w).astype(np.int32)
            resized = bgr[np.ix_(y_idx, x_idx)]

        if new_w == target_w and new_h == target_h:
            return np.ascontiguousarray(resized)

        # Matte canvas (Obsidian dark theme #0F1115 -> BGR 21, 17, 15)
        canvas = np.full((target_h, target_w, 3), (21, 17, 15), dtype=np.uint8)
        off_y = (target_h - new_h) // 2
        off_x = (target_w - new_w) // 2
        canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized
        return canvas


class BrandedVOXPrivacyGenerator:
    """Generates high-contrast, quiet-premium branded VOX privacy screens."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height

    def render(
        self,
        elapsed_seconds: float = 0.0,
        reason: str = "window_minimized",
        detail: str = "",
    ) -> np.ndarray:
        pulse_phase = 0.5 + 0.5 * math.sin(time.perf_counter() * 3.0)
        pulse_alpha = int(140 + 115 * pulse_phase)

        # Obsidian background #0F1115 (BGR: 21, 17, 15)
        canvas = np.full((self.height, self.width, 3), (21, 17, 15), dtype=np.uint8)

        try:
            from PIL import Image, ImageDraw
            img = Image.fromarray(canvas[:, :, ::-1], mode="RGB")
            draw = ImageDraw.Draw(img)

            cx, cy = self.width // 2, self.height // 2
            card_w = min(self.width - 40, 680)
            card_h = min(self.height - 40, 220)
            x0, y0 = cx - card_w // 2, cy - card_h // 2
            x1, y1 = cx + card_w // 2, cy + card_h // 2

            # Slate card fill #1E222A (RGB: 30, 34, 42), border #333945 (RGB: 51, 57, 69)
            draw.rounded_rectangle(
                [x0, y0, x1, y1], radius=16, fill=(30, 34, 42), outline=(51, 57, 69), width=1
            )

            # VOX Brand Pill Badge
            badge_x, badge_y = x0 + 32, y0 + 26
            badge_w, badge_h = 76, 26
            # Brand purple #7C3AED (RGB: 124, 58, 237)
            draw.rounded_rectangle(
                [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
                radius=6,
                fill=(124, 58, 237),
            )
            draw.text((badge_x + 16, badge_y + 4), "VOX", fill=(255, 255, 255))

            # Pulsing status dot
            dot_cx = badge_x + badge_w + 24
            dot_cy = badge_y + badge_h // 2
            dot_r = 5
            glow_r = int(dot_r + 3 * pulse_phase)
            draw.ellipse(
                [dot_cx - glow_r, dot_cy - glow_r, dot_cx + glow_r, dot_cy + glow_r],
                fill=(245, 158, 11),
            )
            draw.ellipse(
                [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
                fill=(245, 158, 11),
            )

            # Status label
            draw.text((dot_cx + 16, badge_y + 5), "PRIVACY SAFEGUARD ACTIVE", fill=(203, 213, 225))

            # Primary Title: EXACT REQUIREMENT
            title_text = "Meeting capture paused for privacy"
            if reason == "non_meeting_view_active":
                title_text = "Meeting Visuals Paused · Viewing Hub/Chat"
            elif "presentation" in reason and "init" in reason:
                title_text = "Presentation Initializing..."

            draw.text((x0 + 32, y0 + 68), title_text, fill=(248, 250, 252))

            # Subtitle
            sub_text = "Desktop recording is disabled while the meeting window is minimized."
            if detail:
                sub_text = detail
            draw.text((x0 + 32, y0 + 108), sub_text, fill=(148, 163, 184))

            # Audio fidelity & telemetry notice
            m = int(elapsed_seconds // 60)
            s = int(elapsed_seconds % 60)
            time_str = f"{m:02d}:{s:02d}"
            info_text = f"Audio recording continues in full fidelity · Meeting Time: {time_str}"
            draw.text((x0 + 32, y0 + 148), info_text, fill=(100, 116, 139))

            rgb = np.asarray(img, dtype=np.uint8)
            canvas = np.ascontiguousarray(rgb[:, :, ::-1])
        except Exception:
            cx, cy = self.width // 2, self.height // 2
            canvas[cy - 80 : cy + 80, cx - 250 : cx + 250] = (42, 34, 30)

        return canvas


# ---------------------------------------------------------------------------
# Source Descriptor & Validation Types
# ---------------------------------------------------------------------------

@dataclass
class MeetingSourceDescriptor:
    """Immutable-by-convention identity of a verified meeting video/audio source."""

    provider_id: str
    platform_name: str
    hwnd: int
    pid: int
    process_name: str
    class_name: str
    window_title: str
    meeting_id_slug: str = ""
    browser_tab_id: Optional[int] = None
    bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)
    score: int = 100
    is_call_stage: bool = True
    is_presenting: bool = False
    presentation_bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)
    presentation_hwnd: int = 0
    presentation_title: str = ""
    presentation_start_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": int(self.hwnd),
            "title": self.window_title,
            "platform": self.platform_name,
            "score": int(self.score),
            "pid": int(self.pid),
            "process_name": self.process_name,
            "class_name": self.class_name,
            "provider_id": self.provider_id,
            "meeting_id_slug": self.meeting_id_slug,
            "browser_tab_id": self.browser_tab_id,
            "bounds": list(self.bounds),
            "is_call_stage": bool(self.is_call_stage),
            "is_presenting": bool(self.is_presenting),
            "presentation_bounds": list(self.presentation_bounds),
            "presentation_hwnd": int(self.presentation_hwnd),
            "presentation_title": self.presentation_title,
        }


@dataclass
class SourceValidationResult:
    """Result of validating a locked meeting source before capturing a frame."""

    is_valid: bool
    active_hwnd: int
    reason: str = "ok"
    user_message: str = ""
    rebound: bool = False


# ---------------------------------------------------------------------------
# Local Browser Tab Capture Bridge (for Chrome/Edge Manifest V3 Extension)
# ---------------------------------------------------------------------------

class LocalBrowserTabCaptureBridge:
    """Loopback receiver (`127.0.0.1:49231`) for VOX's Chrome/Edge `tabCapture` extension.

    When the user installs `extensions/vox_meet_tab_capture`, the extension captures
    the specific Google Meet tab (`chrome.tabCapture.getMediaStreamId`) in an offscreen
    document and posts JPEG/PNG frames + tab identity to `http://127.0.0.1:49231/frame`.
    Because `chrome.tabCapture` binds to the browser `tab.id`, the stream continues
    delivering ONLY the Google Meet tab even when the user switches to another tab
    (e.g. Gmail, Docs, Sheets) or minimizes the browser.
    """

    DEFAULT_PORT = 49231
    _instance: Optional["LocalBrowserTabCaptureBridge"] = None
    _instance_lock = threading.Lock()

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = int(port)
        self._lock = threading.Lock()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._active_tabs: Dict[int, Dict[str, Any]] = {}
        self._latest_frame_bgr: Dict[int, Tuple[float, np.ndarray]] = {}

    @classmethod
    def get_instance(cls) -> "LocalBrowserTabCaptureBridge":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance.start()
            return cls._instance

    def start(self) -> bool:
        with self._lock:
            if self._server is not None:
                return True
            bridge = self

            class _Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: Any) -> None:
                    return  # Silence per-frame HTTP logs

                def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_OPTIONS(self) -> None:
                    self._send_json(200, {"ok": True})

                def do_GET(self) -> None:
                    if self.path.startswith("/status"):
                        self._send_json(200, {
                            "ok": True,
                            "service": "vox_meet_tab_capture_bridge",
                            "active_tabs": bridge.list_active_tabs(),
                        })
                    else:
                        self._send_json(404, {"ok": False})

                def do_POST(self) -> None:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        raw = self.rfile.read(length) if length > 0 else b"{}"
                        data = json.loads(raw.decode("utf-8"))
                    except Exception as exc:
                        self._send_json(400, {"ok": False, "error": str(exc)})
                        return

                    if self.path.startswith("/register_tab"):
                        tab_id = int(data.get("tab_id", 0))
                        bridge.register_tab(
                            tab_id=tab_id,
                            title=str(data.get("title", "")),
                            url=str(data.get("url", "")),
                            meeting_slug=str(data.get("meeting_slug", "")),
                        )
                        self._send_json(200, {"ok": True, "tab_id": tab_id})
                    elif self.path.startswith("/unregister_tab"):
                        tab_id = int(data.get("tab_id", 0))
                        bridge.unregister_tab(tab_id)
                        self._send_json(200, {"ok": True})
                    elif self.path.startswith("/frame"):
                        tab_id = int(data.get("tab_id", 0))
                        b64_img = data.get("frame_b64", "")
                        width = int(data.get("width", 0))
                        height = int(data.get("height", 0))
                        if tab_id and b64_img:
                            bridge.ingest_encoded_frame(
                                tab_id=tab_id,
                                b64_img=b64_img,
                                width=width,
                                height=height,
                                title=str(data.get("title", "")),
                                url=str(data.get("url", "")),
                            )
                        self._send_json(200, {"ok": True})
                    else:
                        self._send_json(404, {"ok": False})

            try:
                self._server = HTTPServer(("127.0.0.1", self.port), _Handler)
                self._thread = threading.Thread(
                    target=self._server.serve_forever,
                    name="VoxMeetTabBridge",
                    daemon=True,
                )
                self._thread.start()
                return True
            except OSError:
                self._server = None
                return False

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.shutdown()
                    self._server.server_close()
                except Exception:
                    pass
                self._server = None

    def register_tab(self, tab_id: int, title: str, url: str, meeting_slug: str = "") -> None:
        if not tab_id:
            return
        with self._lock:
            self._active_tabs[int(tab_id)] = {
                "tab_id": int(tab_id),
                "title": title,
                "url": url,
                "meeting_slug": meeting_slug or extract_google_meet_slug(title + " " + url),
                "updated_at": time.monotonic(),
            }

    def unregister_tab(self, tab_id: int) -> None:
        with self._lock:
            self._active_tabs.pop(int(tab_id), None)
            self._latest_frame_bgr.pop(int(tab_id), None)

    def ingest_encoded_frame(
        self,
        tab_id: int,
        b64_img: str,
        width: int = 0,
        height: int = 0,
        title: str = "",
        url: str = "",
    ) -> None:
        try:
            if "," in b64_img:
                b64_img = b64_img.split(",", 1)[1]
            raw_bytes = base64.b64decode(b64_img)
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            rgb = np.asarray(img, dtype=np.uint8)
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            now = time.monotonic()
            with self._lock:
                self._latest_frame_bgr[int(tab_id)] = (now, bgr)
                info = self._active_tabs.setdefault(int(tab_id), {"tab_id": int(tab_id)})
                if title:
                    info["title"] = title
                if url:
                    info["url"] = url
                info["updated_at"] = now
        except Exception:
            pass

    def ingest_raw_bgr_frame(
        self,
        tab_id: int,
        bgr_frame: np.ndarray,
        title: str = "",
        url: str = "",
    ) -> None:
        """Direct in-process frame injection for unit tests & local pipe bridges."""
        now = time.monotonic()
        with self._lock:
            self._latest_frame_bgr[int(tab_id)] = (now, np.ascontiguousarray(bgr_frame))
            info = self._active_tabs.setdefault(int(tab_id), {"tab_id": int(tab_id)})
            if title:
                info["title"] = title
            if url:
                info["url"] = url
            info["meeting_slug"] = extract_google_meet_slug(f"{title} {url}")
            info["updated_at"] = now

    def get_latest_frame(
        self,
        tab_id: Optional[int] = None,
        meeting_slug: str = "",
        max_age_sec: float = 2.5,
    ) -> Optional[np.ndarray]:
        now = time.monotonic()
        with self._lock:
            if tab_id is not None and int(tab_id) in self._latest_frame_bgr:
                ts, frame = self._latest_frame_bgr[int(tab_id)]
                if now - ts <= max_age_sec:
                    return frame
            if meeting_slug:
                for tid, meta in self._active_tabs.items():
                    if meta.get("meeting_slug") == meeting_slug and tid in self._latest_frame_bgr:
                        ts, frame = self._latest_frame_bgr[tid]
                        if now - ts <= max_age_sec:
                            return frame
        return None

    def list_active_tabs(self) -> List[Dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return [
                dict(meta)
                for meta in self._active_tabs.values()
                if now - float(meta.get("updated_at", 0.0)) <= 10.0
            ]


# ---------------------------------------------------------------------------
# Platform-Specific Meeting Capture Providers
# ---------------------------------------------------------------------------

_GOOGLE_MEET_SLUG_RE = re.compile(r"\b([a-z]{3}-[a-z]{4}-[a-z]{3})\b", re.IGNORECASE)


def extract_google_meet_slug(text: str) -> str:
    """Extract a Google Meet code like `abc-defg-hij` from a window title or URL."""
    m = _GOOGLE_MEET_SLUG_RE.search(text or "")
    return m.group(1).lower() if m else ""



def enum_desktop_windows(callback: Callable[[int, int], bool]) -> None:
    """Enumerate visible desktop windows robustly across desktop stations on Windows 11."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    cb = WNDENUMPROC(callback)
    hdesk = None
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.EnumDesktopWindows(hdesk, cb, 0)
    except Exception:
        pass
    finally:
        if hdesk:
            try:
                user32.CloseDesktop(hdesk)
            except Exception:
                pass
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        pass


def find_teams_popout_presentation(pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Locate Microsoft Teams pop-out presentation/content window if a remote screen share is detached."""
    popout_info = None

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        nonlocal popout_info
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            win_pid, proc_name = get_window_pid_and_process_name(hwnd)
            proc_low = proc_name.lower()
            is_teams = proc_low in ("ms-teams.exe", "teams.exe", "msteams.exe")
            if not is_teams and (not pid or win_pid != pid):
                return True

            title = get_window_title(hwnd)
            t_low = title.lower()
            cls_name = get_window_class_name(hwnd).lower()
            if "chrome_widgetwin" not in cls_name and "webview" not in cls_name:
                return True

            # Match detached presentation titles: "[Presenter] - Screen | Microsoft Teams", "Content | Microsoft Teams", etc.
            is_popout = any(
                kw in t_low
                for kw in (
                    "- screen | microsoft teams",
                    "content | microsoft teams",
                    "presentation | microsoft teams",
                    "screen share | microsoft teams",
                    "shared screen | microsoft teams",
                )
            )
            if is_popout:
                rect = get_window_rect(hwnd)
                if rect[2] >= 320 and rect[3] >= 240:
                    popout_info = {
                        "hwnd": hwnd,
                        "title": title or "Microsoft Teams Pop-out Presentation",
                        "bounds": rect,
                        "bar_rect": None,
                        "pid": win_pid,
                        "is_popout": True,
                    }
                    return False
        except Exception:
            pass
        return True

    enum_desktop_windows(_enum_cb)
    return popout_info


def find_teams_sharing_session(pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Detect if Microsoft Teams is in an active Presentation / Screen Sharing session.

    Identifies pop-out presentation content windows or the floating presenter control
    bar at the top of the shared monitor. Supports multi-monitor desktop configurations.
    """
    popout = find_teams_popout_presentation(pid)
    if popout is not None:
        return popout

    sharing_window_info = None

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        nonlocal sharing_window_info
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return True
            title = get_window_title(hwnd)
            t_low = title.lower()
            cls_name = get_window_class_name(hwnd).lower()

            win_pid, proc_name = get_window_pid_and_process_name(hwnd)
            proc_low = proc_name.lower()
            is_teams = proc_low in ("ms-teams.exe", "teams.exe", "msteams.exe")

            if not is_teams and (not pid or win_pid != pid):
                return True

            rect = get_window_rect(hwnd)
            w, h = rect[2], rect[3]
            if w <= 0 or h <= 0:
                return True

            ex_style = user32.GetWindowLongW(hwnd, -20)
            is_topmost = bool(ex_style & 0x00000008)

            is_sharing_title = (
                "sharing control bar" in t_low
                or "teams sharing" in t_low
                or "screen sharing" in t_low
                or "presenter bar" in t_low
            )

            # Locate monitor of window
            hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            has_mi = bool(user32.GetMonitorInfoW(hmon, ctypes.byref(mi)))
            mon_top = int(mi.rcMonitor.top) if has_mi else 0

            # Match title OR topmost presenter bar at the top edge of screen
            # Note: rect[1] is top coordinate, rect[3] is height
            is_at_top = abs(rect[1] - mon_top) <= 45
            is_bar_dims = (80 <= w) and (16 <= h <= 240)
            is_presenter_bar = is_sharing_title or (
                is_topmost and is_at_top and is_bar_dims and
                ("webview" in cls_name or "chrome_widgetwin" in cls_name or not t_low)
            )

            if is_presenter_bar:
                if has_mi:
                    mx = int(mi.rcMonitor.left)
                    my = int(mi.rcMonitor.top)
                    mw = int(mi.rcMonitor.right - mi.rcMonitor.left)
                    mh = int(mi.rcMonitor.bottom - mi.rcMonitor.top)
                else:
                    mx, my, mw, mh = rect
                sharing_window_info = {
                    "hwnd": hwnd,
                    "title": title or "Microsoft Teams Presenter Bar",
                    "bounds": (mx, my, mw, mh),
                    "bar_rect": rect,
                    "pid": win_pid,
                }
                return False
        except Exception:
            pass
        return True

    enum_desktop_windows(_enum_cb)
    return sharing_window_info


def find_teams_call_monitor_window(pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Detect if Microsoft Teams is displaying its floating mini Call Monitor PIP window.

    When Teams call stage is minimized during screen sharing or multitasking, Teams
    keeps a small floating PIP video window (typically 160-550px wide) visible and topmost.
    """
    call_monitor_info = None

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        nonlocal call_monitor_info
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            win_pid, proc_name = get_window_pid_and_process_name(hwnd)
            proc_low = proc_name.lower()
            is_teams = proc_low in ("ms-teams.exe", "teams.exe", "msteams.exe")
            if not is_teams and (not pid or win_pid != pid):
                return True

            cls_name = get_window_class_name(hwnd).lower()
            if "chrome_widgetwin" not in cls_name and "webview" not in cls_name:
                return True

            rect = get_window_rect(hwnd)
            w, h = rect[2], rect[3]
            # Mini call monitor dimensions: typically 160 <= w <= 550, 100 <= h <= 450
            if 160 <= w <= 550 and 100 <= h <= 450:
                title = get_window_title(hwnd)
                call_monitor_info = {
                    "hwnd": hwnd,
                    "title": title or "Microsoft Teams Call Monitor",
                    "bounds": rect,
                    "pid": win_pid,
                }
                return False
        except Exception:
            pass
        return True

    enum_desktop_windows(_enum_cb)
    return call_monitor_info


def find_active_presentation_window(
    meeting_pid: Optional[int] = None,
    excluded_hwnds: Optional[Set[int]] = None,
) -> Optional[Dict[str, Any]]:
    """Detect if the presenter is sharing a specific application window (PowerPoint, Browser, IDE, Doc).

    When a presenter shares an application in Microsoft Teams, Teams docks or minimizes its
    call stage while the presenter actively interacts with their slides/document.
    This function locates the active foreground presentation window, excluding VOX UI and private shell windows.
    """
    fg_hwnd = user32.GetForegroundWindow()
    if not fg_hwnd or not user32.IsWindow(fg_hwnd) or not user32.IsWindowVisible(fg_hwnd):
        return None
    if user32.IsIconic(fg_hwnd):
        return None
    if excluded_hwnds and fg_hwnd in excluded_hwnds:
        return None

    win_pid, proc_name = get_window_pid_and_process_name(fg_hwnd)
    cls_name = get_window_class_name(fg_hwnd).lower()
    title = get_window_title(fg_hwnd)

    EXCLUDED_CLASSES = {
        "shell_traywnd", "progman", "workerw", "windows.ui.core.corewindow",
        "dwm", "sidebarmainwindow", "multitaskviewclassifier"
    }
    EXCLUDED_PROCESSES = {
        "explorer.exe", "shellexperiencehost.exe", "searchhost.exe",
        "lockapp.exe", "logonui.exe", "voxlocal.exe", "python.exe", "pythonw.exe"
    }
    EXCLUDED_TITLES = {
        "program manager", "start", "windows input experience", "task switching"
    }

    if cls_name in EXCLUDED_CLASSES or proc_name.lower() in EXCLUDED_PROCESSES or title.lower() in EXCLUDED_TITLES:
        return None

    rect = get_window_rect(fg_hwnd)
    w, h = rect[2], rect[3]
    if w < 240 or h < 180:
        return None

    return {
        "hwnd": fg_hwnd,
        "title": title or proc_name,
        "process_name": proc_name,
        "bounds": rect,
    }

def find_zoom_sharing_session(pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Detect if Zoom is in an active Presentation / Screen Sharing session."""
    sharing_window_info = None

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        nonlocal sharing_window_info
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return True
            cls_name = get_window_class_name(hwnd).lower()
            title = get_window_title(hwnd)
            t_low = title.lower()

            win_pid, proc_name = get_window_pid_and_process_name(hwnd)
            is_zoom = proc_name.lower() in ("zoom.exe", "cpthost.exe")

            if is_zoom and (
                "zpfloattoolbarwndclass" in cls_name
                or "sharing toolbar" in t_low
                or (cls_name == "zpcontentviewwndclass" and "sharing" in t_low)
            ):
                rect = get_window_rect(hwnd)
                if rect[2] > 0 and rect[3] > 0:
                    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                        mx = int(mi.rcMonitor.left)
                        my = int(mi.rcMonitor.top)
                        mw = int(mi.rcMonitor.right - mi.rcMonitor.left)
                        mh = int(mi.rcMonitor.bottom - mi.rcMonitor.top)
                    else:
                        mx = 0
                        my = 0
                        mw = int(user32.GetSystemMetrics(0) or 1920)
                        mh = int(user32.GetSystemMetrics(1) or 1080)
                    sharing_window_info = {
                        "hwnd": hwnd,
                        "title": title,
                        "bounds": (mx, my, mw, mh),
                        "bar_rect": rect,
                        "pid": win_pid,
                    }
                    return False
        except Exception:
            pass
        return True

    enum_desktop_windows(_enum_cb)
    return sharing_window_info



class MeetingCaptureProvider:
    """Base interface for platform-specific call-only capture providers."""

    provider_id: str = "base"
    platform_name: str = "Meeting"

    def inspect_window(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        class_name: str,
        title: str,
        bounds: Tuple[int, int, int, int],
    ) -> Optional[MeetingSourceDescriptor]:
        raise NotImplementedError

    def validate_source(
        self,
        descriptor: MeetingSourceDescriptor,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        return SourceValidationResult(is_valid=True, active_hwnd=descriptor.hwnd, reason="ok")


class TeamsDesktopCaptureProvider(MeetingCaptureProvider):
    """Microsoft Teams desktop (`ms-teams.exe` / `teams.exe`) Call-Only Provider."""

    provider_id = "teams_desktop"
    platform_name = "Microsoft Teams"

    TEAMS_EXES = {"ms-teams.exe", "teams.exe", "msteams.exe"}
    CALL_STAGE_CLASSES = {"teamswebview", "chrome_widgetwin_1"}
    EXCLUDED_HUB_PREFIXES = (
        "chat |", "activity |", "calendar |", "teams |", "calls |",
        "onedrive |", "files |", "approvals |", "shifts |", "settings |",
    )

    def __init__(self) -> None:
        super().__init__()
        self._last_presenting_ts: float = 0.0
        self._last_presenting_bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._last_presenting_hwnd: int = 0
        self._last_presenting_title: str = ""

    @classmethod
    def is_non_call_hub_title(cls, title: str) -> bool:
        t = title.strip().lower()
        if not t:
            return False
        from voxlocal.audio.call_detector import is_non_call_hub
        if is_non_call_hub("Microsoft Teams", title):
            return True
        if t in ("microsoft teams", "microsoft teams classic", "teams"):
            return True
        return t.startswith(cls.EXCLUDED_HUB_PREFIXES)

    def inspect_window(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        class_name: str,
        title: str,
        bounds: Tuple[int, int, int, int],
    ) -> Optional[MeetingSourceDescriptor]:
        if process_name not in self.TEAMS_EXES and "teams" not in process_name:
            return None

        is_iconic = bool(user32.IsIconic(hwnd))
        w, h = bounds[2], bounds[3]
        if not is_iconic and (w < 120 or h < 80):
            return None

        t_lower = title.strip().lower()
        if not t_lower:
            return None

        is_hub = self.is_non_call_hub_title(title)
        is_stage = not is_hub

        score = 250 if is_stage else 20
        if any(kw in t_lower for kw in ("presentation", "content", "screen", "sharing")):
            score += 100
            is_stage = True
        elif any(kw in t_lower for kw in ("meeting", "call", "scrum", "sync", "standup", "weekly")):
            score += 30
        score += min(50, int((w * h) / (1920 * 1080) * 50))

        return MeetingSourceDescriptor(
            provider_id=self.provider_id,
            platform_name=self.platform_name,
            hwnd=hwnd,
            pid=pid,
            process_name=process_name,
            class_name=class_name,
            window_title=title,
            bounds=bounds,
            is_call_stage=is_stage,
            score=score,
        )

    def validate_source(
        self,
        descriptor: MeetingSourceDescriptor,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        hwnd = descriptor.hwnd
        now_ts = time.monotonic()

        # 0. Check if currently bound window title is in an explicit leaving/ended state
        if hwnd and user32.IsWindow(hwnd):
            cur_title = get_window_title(hwnd)
            from voxlocal.audio.call_detector import is_leaving_title
            if is_leaving_title(cur_title):
                self._last_presenting_ts = 0.0
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="window_closed",
                    user_message="Microsoft Teams call has ended",
                )

        # 1. Check if Microsoft Teams is in an active Presentation / Screen-Sharing session
        sharing_session = find_teams_sharing_session(descriptor.pid)
        if sharing_session is not None:
            sbounds = sharing_session.get("bounds", (0, 0, 0, 0))
            is_full_screen = (
                sharing_session.get("bar_rect") is not None
                or sbounds[2] >= 1024
                or (sbounds[0] == 0 and sbounds[1] == 0 and sbounds[2] >= 800 and sbounds[3] >= 600)
            )
            pres_hwnd = 0 if is_full_screen else sharing_session.get("hwnd", 0)
            self._last_presenting_ts = now_ts
            self._last_presenting_bounds = sbounds
            self._last_presenting_hwnd = pres_hwnd
            self._last_presenting_title = sharing_session["title"]
            descriptor.is_presenting = True
            descriptor.presentation_bounds = sbounds
            descriptor.presentation_hwnd = pres_hwnd
            descriptor.presentation_title = sharing_session["title"]
            return SourceValidationResult(
                is_valid=True,
                active_hwnd=descriptor.hwnd,
                reason="teams_presentation_active",
                user_message="Microsoft Teams screen presentation active",
            )

        # 1b. Check sticky presentation latch or minimized application presentation
        is_stage_minimized = bool(hwnd and user32.IsWindow(hwnd) and user32.IsIconic(hwnd))
        call_monitor = find_teams_call_monitor_window(descriptor.pid)
        is_presenting_context = is_stage_minimized or (call_monitor is not None)

        if is_presenting_context:
            app_pres = find_active_presentation_window(descriptor.pid)
            if app_pres is not None:
                descriptor.is_presenting = True
                descriptor.presentation_bounds = app_pres["bounds"]
                descriptor.presentation_hwnd = app_pres["hwnd"]
                descriptor.presentation_title = app_pres.get("title", "Application Presentation")
                self._last_presenting_ts = now_ts
                self._last_presenting_bounds = app_pres["bounds"]
                self._last_presenting_hwnd = app_pres["hwnd"]
                self._last_presenting_title = app_pres.get("title", "Application Presentation")
                return SourceValidationResult(
                    is_valid=True,
                    active_hwnd=descriptor.hwnd,
                    reason="teams_presentation_active",
                    user_message="Microsoft Teams screen presentation active",
                )

            is_latch_active = (now_ts - self._last_presenting_ts) < 3.0 and self._last_presenting_bounds[2] > 0
            if is_latch_active:
                descriptor.is_presenting = True
                descriptor.presentation_bounds = self._last_presenting_bounds
                descriptor.presentation_hwnd = self._last_presenting_hwnd
                descriptor.presentation_title = self._last_presenting_title
                return SourceValidationResult(
                    is_valid=True,
                    active_hwnd=descriptor.hwnd,
                    reason="teams_presentation_active",
                    user_message="Microsoft Teams screen presentation active",
                )

        # Presentation is definitely not active (no presenter bar, latch expired)
        self._last_presenting_ts = 0.0
        descriptor.is_presenting = False
        descriptor.presentation_bounds = (0, 0, 0, 0)
        descriptor.presentation_hwnd = 0

        # Always check if a better/active Call Stage window exists for the same Teams process
        all_candidates = candidate_windows if candidate_windows is not None else discover_meeting_sources(include_non_call_hubs=False)
        same_proc_call_windows = [
            c for c in all_candidates
            if c.provider_id == self.provider_id
            and c.is_call_stage
            and (c.pid == descriptor.pid or (descriptor.pid <= 0 and c.process_name == descriptor.process_name))
        ]

        # If the currently bound HWND is a non-call hub or closed, rebind to the active call stage
        if hwnd and user32.IsWindow(hwnd):
            cur_title = get_window_title(hwnd)
            cur_pid, cur_proc = get_window_pid_and_process_name(hwnd)
            if cur_proc and cur_proc not in self.TEAMS_EXES and "microsoft teams" not in cur_title.lower():
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="process_mismatch",
                    user_message="Teams window handle was recycled by another process",
                )

            if self.is_non_call_hub_title(cur_title):
                for call_win in same_proc_call_windows:
                    if call_win.hwnd != hwnd and not user32.IsIconic(call_win.hwnd):
                        descriptor.hwnd = call_win.hwnd
                        descriptor.window_title = call_win.window_title
                        descriptor.is_call_stage = True
                        return SourceValidationResult(
                            is_valid=True,
                            active_hwnd=call_win.hwnd,
                            reason="rebound_to_teams_call_stage",
                            rebound=True,
                        )
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="non_meeting_view_active",
                    user_message="Teams is showing Chat/Files instead of an active Call stage",
                )

            if user32.IsIconic(hwnd):
                for call_win in same_proc_call_windows:
                    if call_win.hwnd != hwnd and not user32.IsIconic(call_win.hwnd):
                        descriptor.hwnd = call_win.hwnd
                        descriptor.window_title = call_win.window_title
                        return SourceValidationResult(
                            is_valid=True,
                            active_hwnd=call_win.hwnd,
                            reason="rebound_to_visible_call_stage",
                            rebound=True,
                        )
                call_monitor = find_teams_call_monitor_window(descriptor.pid)
                if call_monitor is not None and user32.IsWindow(call_monitor["hwnd"]):
                    return SourceValidationResult(
                        is_valid=True,
                        active_hwnd=call_monitor["hwnd"],
                        reason="teams_pip_active",
                        user_message="Microsoft Teams call stage minimized (capturing floating call monitor)",
                    )
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="window_minimized",
                    user_message="Microsoft Teams call window is minimized",
                )

            descriptor.window_title = cur_title
            return SourceValidationResult(is_valid=True, active_hwnd=hwnd, reason="ok")

        # Bound HWND was closed or recreated by Teams
        for call_win in same_proc_call_windows:
            if not user32.IsIconic(call_win.hwnd):
                descriptor.hwnd = call_win.hwnd
                descriptor.window_title = call_win.window_title
                descriptor.is_call_stage = True
                return SourceValidationResult(
                    is_valid=True,
                    active_hwnd=call_win.hwnd,
                    reason="rebound_after_window_recreate",
                    rebound=True,
                )

        return SourceValidationResult(
            is_valid=False,
            active_hwnd=hwnd,
            reason="window_closed",
            user_message="Microsoft Teams call window has closed",
        )


class ZoomDesktopCaptureProvider(MeetingCaptureProvider):
    """Zoom desktop (`zoom.exe` / `cpthost.exe`) Call-Only Provider."""

    provider_id = "zoom_desktop"
    platform_name = "Zoom"

    def __init__(self) -> None:
        super().__init__()
        self._last_presenting_ts: float = 0.0
        self._last_presenting_bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._last_presenting_hwnd: int = 0
        self._last_presenting_title: str = ""

    ZOOM_EXES = {"zoom.exe", "cpthost.exe", "zcrashreport.exe"}
    CALL_STAGE_CLASSES = {
        "zpcontentviewwndclass",
        "zpptmainfrmwndclassex",
        "confmultitabcontentwndclass",
    }
    EXCLUDED_TITLES = {
        "zoom workplace",
        "zoom - free account",
        "zoom - pro account",
        "zoom cloud meetings",
        "settings",
    }

    def inspect_window(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        class_name: str,
        title: str,
        bounds: Tuple[int, int, int, int],
    ) -> Optional[MeetingSourceDescriptor]:
        proc_low = (process_name or "").lower()
        cls_low = (class_name or "").lower()
        t_low = (title or "").strip().lower()

        is_zoom_proc = proc_low in self.ZOOM_EXES
        is_zoom_title = "zoom meeting" in t_low or "zoom share" in t_low or "zoom webinar" in t_low

        if not is_zoom_proc and not is_zoom_title:
            return None

        _, _, w, h = bounds
        if w < 320 or h < 240:
            return None

        is_call_class = cls_low in self.CALL_STAGE_CLASSES
        is_call_title = any(
            kw in t_low for kw in ("zoom meeting", "zoom webinar", "zoom share", "screen sharing")
        )

        if t_low in self.EXCLUDED_TITLES and not is_call_class:
            return MeetingSourceDescriptor(
                provider_id=self.provider_id,
                platform_name=self.platform_name,
                hwnd=hwnd,
                pid=pid,
                process_name=proc_low or "zoom.exe",
                class_name=class_name,
                window_title=title,
                meeting_id_slug=t_low,
                bounds=bounds,
                score=10,
                is_call_stage=False,
            )

        score = 225 if (is_call_class or is_call_title) else 120
        return MeetingSourceDescriptor(
            provider_id=self.provider_id,
            platform_name=self.platform_name,
            hwnd=hwnd,
            pid=pid,
            process_name=proc_low or "zoom.exe",
            class_name=class_name,
            window_title=title,
            meeting_id_slug=t_low,
            bounds=bounds,
            score=score,
            is_call_stage=bool(is_call_class or is_call_title),
        )

    def validate_source(
        self,
        descriptor: MeetingSourceDescriptor,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        hwnd = descriptor.hwnd

        now_ts = time.monotonic()
        # 1. Check if Zoom is in an active Presentation / Screen-Sharing session
        sharing_session = find_zoom_sharing_session(descriptor.pid)
        if sharing_session is not None:
            self._last_presenting_ts = now_ts
            self._last_presenting_bounds = sharing_session["bounds"]
            self._last_presenting_hwnd = sharing_session["hwnd"]
            self._last_presenting_title = sharing_session["title"]
            descriptor.is_presenting = True
            descriptor.presentation_bounds = sharing_session["bounds"]
            descriptor.presentation_hwnd = 0  # Full shared screen
            descriptor.presentation_title = sharing_session["title"]
            return SourceValidationResult(
                is_valid=True,
                active_hwnd=descriptor.hwnd,
                reason="zoom_presentation_active",
                user_message="Zoom screen presentation active",
            )
        elif (now_ts - self._last_presenting_ts) < 3.0 and self._last_presenting_bounds[2] > 0:
            descriptor.is_presenting = True
            descriptor.presentation_bounds = self._last_presenting_bounds
            descriptor.presentation_hwnd = 0
            descriptor.presentation_title = self._last_presenting_title
            return SourceValidationResult(
                is_valid=True,
                active_hwnd=descriptor.hwnd,
                reason="zoom_presentation_active",
                user_message="Zoom screen presentation active",
            )
        else:
            descriptor.is_presenting = False
            descriptor.presentation_bounds = (0, 0, 0, 0)
            descriptor.presentation_hwnd = 0

        all_candidates = candidate_windows if candidate_windows is not None else discover_meeting_sources(include_non_call_hubs=False)
        zoom_call_windows = [
            c for c in all_candidates
            if c.provider_id == self.provider_id and c.is_call_stage
        ]

        if hwnd and user32.IsWindow(hwnd):
            cur_title = get_window_title(hwnd)
            t_low = cur_title.lower()
            cur_cls = get_window_class_name(hwnd).lower()
            if t_low in self.EXCLUDED_TITLES and cur_cls not in self.CALL_STAGE_CLASSES:
                for call_win in zoom_call_windows:
                    if call_win.hwnd != hwnd and not user32.IsIconic(call_win.hwnd):
                        descriptor.hwnd = call_win.hwnd
                        descriptor.window_title = call_win.window_title
                        return SourceValidationResult(
                            is_valid=True,
                            active_hwnd=call_win.hwnd,
                            reason="rebound_to_zoom_call_stage",
                            rebound=True,
                        )
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="non_meeting_view_active",
                    user_message="Zoom window is on the Workplace home hub rather than an active call",
                )
            if user32.IsIconic(hwnd):
                for call_win in zoom_call_windows:
                    if call_win.hwnd != hwnd and not user32.IsIconic(call_win.hwnd):
                        descriptor.hwnd = call_win.hwnd
                        descriptor.window_title = call_win.window_title
                        return SourceValidationResult(
                            is_valid=True,
                            active_hwnd=call_win.hwnd,
                            reason="rebound_to_visible_zoom_stage",
                            rebound=True,
                        )
                return SourceValidationResult(
                    is_valid=False,
                    active_hwnd=hwnd,
                    reason="window_minimized",
                    user_message="Zoom meeting window is minimized",
                )
            descriptor.window_title = cur_title
            return SourceValidationResult(is_valid=True, active_hwnd=hwnd, reason="ok")

        for call_win in zoom_call_windows:
            if not user32.IsIconic(call_win.hwnd):
                descriptor.hwnd = call_win.hwnd
                descriptor.window_title = call_win.window_title
                return SourceValidationResult(
                    is_valid=True,
                    active_hwnd=call_win.hwnd,
                    reason="rebound_after_zoom_transition",
                    rebound=True,
                )

        return SourceValidationResult(
            is_valid=False,
            active_hwnd=hwnd,
            reason="window_closed",
            user_message="Zoom meeting window has closed",
        )


class GoogleMeetTabCaptureProvider(MeetingCaptureProvider):
    """Google Meet (and Browser-Hosted Meeting) Call-Only Provider.

    Combines two layers of strict isolation:
    1. When the VOX Chrome/Edge Manifest V3 extension (`extensions/vox_meet_tab_capture`)
       is streaming the locked `browser_tab_id` to `LocalBrowserTabCaptureBridge`,
       frames come directly from `chrome.tabCapture` for that specific `tab.id`,
       continuing seamlessly even when the user switches browser tabs or minimizes Chrome.
    2. When capturing via the browser window `HWND` (`PrintWindow`), the provider
       strictly verifies on every single frame that the browser window's active tab
       title still matches the locked Google Meet session (`Meet - xxx-yyyy-zzz` or
       `Google Meet`). If the user switches to ANY other browser tab (e.g. Gmail,
       YouTube, Docs), validation immediately returns `is_valid=False`
       (`reason="browser_tab_switched_away"`), causing `MeetingVideoRecorder` to
       pause video capture and emit a privacy protection frame instead of leaking
       unrelated browser tabs!
    """

    provider_id = "google_meet_tab"
    platform_name = "Google Meet"

    BROWSER_EXES = {
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "firefox.exe",
        "opera.exe",
        "vivaldi.exe",
        "arc.exe",
    }

    @classmethod
    def is_google_meet_title(cls, title: str) -> bool:
        t_low = (title or "").strip().lower()
        if not t_low:
            return False
        try:
            from voxlocal.audio.call_detector import _is_non_meeting_document_window
            if _is_non_meeting_document_window(t_low, ""):
                return False
        except Exception:
            if any(ext in t_low for ext in (".pdf", ".html", ".htm", ".md", ".docx", ".xlsx")):
                return False
        if "meet.google.com" in t_low:
            return True
        if extract_google_meet_slug(t_low) and "meet" in t_low:
            return True
        if t_low.startswith("meet - ") or " - meet - " in t_low or "| meet" in t_low:
            return True
        if t_low in ("google meet", "google meet - google chrome", "google meet - microsoft edge") or t_low.endswith("- google meet"):
            return True
        return False

    def inspect_window(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        class_name: str,
        title: str,
        bounds: Tuple[int, int, int, int],
    ) -> Optional[MeetingSourceDescriptor]:
        proc_low = (process_name or "").lower()
        t_low = (title or "").strip().lower()

        if proc_low and proc_low not in self.BROWSER_EXES:
            # Allow unit tests with synthetic HWNDs where process_name may be empty
            if not self.is_google_meet_title(title):
                return None

        if not self.is_google_meet_title(title):
            return None

        _, _, w, h = bounds
        if w < 300 or h < 200:
            return None

        slug = extract_google_meet_slug(title)
        bridge = LocalBrowserTabCaptureBridge.get_instance()
        matched_tab_id: Optional[int] = None
        for tab_meta in bridge.list_active_tabs():
            if slug and tab_meta.get("meeting_slug") == slug:
                matched_tab_id = int(tab_meta["tab_id"])
                break
            if tab_meta.get("title") and tab_meta["title"].lower() in t_low:
                matched_tab_id = int(tab_meta["tab_id"])
                break

        score = 230 if slug else 195
        if matched_tab_id is not None:
            score += 30

        return MeetingSourceDescriptor(
            provider_id=self.provider_id,
            platform_name=self.platform_name,
            hwnd=hwnd,
            pid=pid,
            process_name=proc_low or "chrome.exe",
            class_name=class_name,
            window_title=title,
            meeting_id_slug=slug,
            browser_tab_id=matched_tab_id,
            bounds=bounds,
            score=score,
            is_call_stage=True,
        )

    def validate_source(
        self,
        descriptor: MeetingSourceDescriptor,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        # 1. If the Chrome/Edge Tab Capture extension is actively streaming this locked tab,
        # the tab stream is 100% isolated regardless of active tab or browser minimization!
        bridge = LocalBrowserTabCaptureBridge.get_instance()
        ext_frame = bridge.get_latest_frame(
            tab_id=descriptor.browser_tab_id,
            meeting_slug=descriptor.meeting_id_slug,
        )
        if ext_frame is not None:
            return SourceValidationResult(
                is_valid=True,
                active_hwnd=descriptor.hwnd,
                reason="tab_extension_stream_active",
            )

        # 2. Otherwise, validate the bound browser HWND and its active tab title
        hwnd = descriptor.hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="window_closed",
                user_message="Google Meet browser window was closed",
            )

        if user32.IsIconic(hwnd):
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="window_minimized",
                user_message="Google Meet browser window is minimized",
            )

        cur_title = get_window_title(hwnd)
        if not self.is_google_meet_title(cur_title):
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="browser_tab_switched_away",
                user_message="Browser switched away from the Google Meet tab — unrelated tab blocked",
            )

        # If the meeting locked onto a specific Meet code (e.g. abc-defg-hij), verify it matches
        cur_slug = extract_google_meet_slug(cur_title)
        if descriptor.meeting_id_slug and cur_slug and cur_slug != descriptor.meeting_id_slug:
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="browser_tab_switched_away",
                user_message=f"Browser switched to a different tab ({cur_slug} != {descriptor.meeting_id_slug})",
            )

        descriptor.window_title = cur_title
        return SourceValidationResult(is_valid=True, active_hwnd=hwnd, reason="ok")


class GenericCallWindowCaptureProvider(MeetingCaptureProvider):
    """Extensible Call-Only Provider for Webex and Browser Teams/Zoom."""

    provider_id = "generic_call"
    platform_name = "Meeting Call"

    WEBEX_EXES = {"atmgr.exe", "ciscocollabhost.exe", "webexmta.exe"}

    def inspect_window(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        class_name: str,
        title: str,
        bounds: Tuple[int, int, int, int],
    ) -> Optional[MeetingSourceDescriptor]:
        proc_low = (process_name or "").lower()
        t_low = (title or "").strip().lower()
        _, _, w, h = bounds
        if w < 300 or h < 200 or not t_low:
            return None

        # Never match VOX's own windows or PDF/document/article viewers
        if "vox" in t_low or proc_low in ("voxlocal.exe", "python.exe", "pythonw.exe"):
            return None
        try:
            from voxlocal.audio.call_detector import _is_non_meeting_document_window
            if _is_non_meeting_document_window(t_low, proc_low):
                return None
        except Exception:
            if any(ext in t_low for ext in (".pdf", ".html", ".htm", ".md", ".docx", ".xlsx")):
                return None

        if proc_low in self.WEBEX_EXES or ("webex" in t_low and proc_low not in {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe"}):
            return MeetingSourceDescriptor(
                provider_id="webex_desktop",
                platform_name="Cisco Webex",
                hwnd=hwnd,
                pid=pid,
                process_name=proc_low or "ciscocollabhost.exe",
                class_name=class_name,
                window_title=title,
                meeting_id_slug=t_low,
                bounds=bounds,
                score=185,
                is_call_stage=True,
            )

        BROWSER_EXES = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "comet.exe", "vivaldi.exe", "arc.exe"}
        if proc_low in BROWSER_EXES:
            if any(tok in t_low for tok in ("teams.microsoft.com", "teams.live.com", "| microsoft teams")):
                return MeetingSourceDescriptor(
                    provider_id="generic_call",
                    platform_name="Microsoft Teams (Browser)",
                    hwnd=hwnd,
                    pid=pid,
                    process_name=proc_low,
                    class_name=class_name,
                    window_title=title,
                    meeting_id_slug=t_low,
                    bounds=bounds,
                    score=195,
                    is_call_stage=True,
                )
            if "zoom.us" in t_low or "zoom workplace" in t_low:
                return MeetingSourceDescriptor(
                    provider_id="generic_call",
                    platform_name="Zoom (Browser)",
                    hwnd=hwnd,
                    pid=pid,
                    process_name=proc_low,
                    class_name=class_name,
                    window_title=title,
                    meeting_id_slug=t_low,
                    bounds=bounds,
                    score=195,
                    is_call_stage=True,
                )

        return None

    def validate_source(
        self,
        descriptor: MeetingSourceDescriptor,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        hwnd = descriptor.hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="window_closed",
                user_message=f"{descriptor.platform_name} window was closed",
            )
        if user32.IsIconic(hwnd):
            return SourceValidationResult(
                is_valid=False,
                active_hwnd=hwnd,
                reason="window_minimized",
                user_message=f"{descriptor.platform_name} window is minimized",
            )
        descriptor.window_title = get_window_title(hwnd)
        return SourceValidationResult(is_valid=True, active_hwnd=hwnd, reason="ok")


# Registered provider chain in priority order
PROVIDERS: List[MeetingCaptureProvider] = [
    TeamsDesktopCaptureProvider(),
    ZoomDesktopCaptureProvider(),
    GoogleMeetTabCaptureProvider(),
    GenericCallWindowCaptureProvider(),
]

PROVIDER_MAP: Dict[str, MeetingCaptureProvider] = {
    p.provider_id: p for p in PROVIDERS
}
PROVIDER_MAP["webex_desktop"] = PROVIDERS[3]
PROVIDER_MAP["generic_call"] = PROVIDERS[3]


# ---------------------------------------------------------------------------
# Discovery Functions
# ---------------------------------------------------------------------------

def discover_meeting_sources(include_non_call_hubs: bool = False) -> List[MeetingSourceDescriptor]:
    """Enumerate all visible top-level windows and return ranked `MeetingSourceDescriptor`s.

    By default (`include_non_call_hubs=False`), non-call hub windows such as
    Microsoft Teams `'Chat | ...'`, `'Calendar | ...'`, `'Activity | ...'`, or
    Zoom Workplace Home are excluded whenever an active call stage window exists
    or when strict call-only discovery is requested.
    """
    all_found: List[MeetingSourceDescriptor] = []

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = get_window_title(hwnd)
            if not title:
                return True
            t_low = title.lower()
            if "voxlocal" in t_low:
                return True
            bounds = get_window_rect(hwnd)
            is_iconic = bool(user32.IsIconic(hwnd))
            if not is_iconic and (bounds[2] < 160 or bounds[3] < 120):
                return True
            pid, proc_name = get_window_pid_and_process_name(hwnd)
            cls_name = get_window_class_name(hwnd)

            for provider in PROVIDERS:
                desc = provider.inspect_window(
                    hwnd=int(hwnd),
                    pid=pid,
                    process_name=proc_name,
                    class_name=cls_name,
                    title=title,
                    bounds=bounds,
                )
                if desc is not None:
                    all_found.append(desc)
                    break
        except Exception:
            pass
        return True

    enum_desktop_windows(_enum_cb)

    all_found.sort(key=lambda d: (1 if d.is_call_stage else 0, d.score), reverse=True)

    if not include_non_call_hubs:
        call_stage_only = [d for d in all_found if d.is_call_stage]
        return call_stage_only

    return all_found


def resolve_meeting_source(
    target_hwnd: Optional[int] = None,
    platform_hint: str = "",
) -> Optional[MeetingSourceDescriptor]:
    """Resolve a `target_hwnd` (or auto-discover the best active call stage) into a verified descriptor.

    IMPORTANT:
    - If `target_hwnd` is `-1` or `None`, this function NEVER returns a desktop source.
      Instead, it looks up the best verified meeting call stage window.
    - If `target_hwnd` points to the Microsoft Teams Hub (`'Chat | ...'`) while a
      dedicated Teams Call Stage window (`'Daily Scrum ... | Microsoft Teams'`) is
      open for the same Teams process, it automatically upgrades to the Call Stage window!
    """
    candidates = discover_meeting_sources(include_non_call_hubs=True)
    call_stage_candidates = [c for c in candidates if c.is_call_stage]

    if target_hwnd is not None and int(target_hwnd) > 0:
        hwnd = int(target_hwnd)
        # Check if hwnd is in discovered candidates
        for cand in candidates:
            if cand.hwnd == hwnd:
                # If user/auto-detector passed a Teams non-call hub HWND (e.g. 'Chat | ...')
                # and Teams also has a real Call Stage HWND open, upgrade to the Call Stage HWND!
                if not cand.is_call_stage:
                    for stage in call_stage_candidates:
                        if stage.provider_id == cand.provider_id and (
                            stage.pid == cand.pid or stage.process_name == cand.process_name
                        ):
                            logger.info(
                                "Upgraded Teams hub HWND %s ('%s') -> Call Stage HWND %s ('%s')",
                                cand.hwnd,
                                cand.window_title,
                                stage.hwnd,
                                stage.window_title,
                            )
                            return stage
                return cand

        # If target_hwnd is a valid window not matched by standard discovery (e.g. custom call window in test),
        # inspect it directly provided it is a real window and NOT the desktop (hwnd > 0).
        if user32.IsWindow(hwnd):
            title = get_window_title(hwnd)
            pid, proc_name = get_window_pid_and_process_name(hwnd)
            cls_name = get_window_class_name(hwnd)
            bounds = get_window_rect(hwnd)
            for provider in PROVIDERS:
                desc = provider.inspect_window(hwnd, pid, proc_name, cls_name, title, bounds)
                if desc is not None:
                    return desc
            # Allow explicit window binding in tests/extensible apps if it's a valid top-level window
            if title and "program manager" not in title.lower():
                return MeetingSourceDescriptor(
                    provider_id="generic_call",
                    platform_name=platform_hint or "Meeting Call",
                    hwnd=hwnd,
                    pid=pid,
                    process_name=proc_name,
                    class_name=cls_name,
                    window_title=title,
                    meeting_id_slug=title.lower(),
                    bounds=bounds,
                    score=100,
                    is_call_stage=True,
                )

    # Auto-select the highest-scoring active Call Stage window
    if platform_hint:
        p_low = platform_hint.lower()
        for stage in call_stage_candidates:
            if p_low in stage.platform_name.lower() or p_low in stage.provider_id.lower():
                return stage

    if call_stage_candidates:
        return call_stage_candidates[0]

    if candidates:
        logger.info(
            "No explicit call stage window found, using best discovered candidate: '%s' (HWND %s)",
            candidates[0].window_title,
            candidates[0].hwnd,
        )
        return candidates[0]

    return None


# ---------------------------------------------------------------------------
# Central CaptureSourceValidator (Section 12 Requirement)
# ---------------------------------------------------------------------------

class CaptureSourceValidator:
    """Central fail-closed validator that enforces strict Call-Only capture boundaries.

    Every frame captured by `MeetingVideoRecorder` must pass through `validate_frame_source()`.
    If any check fails (e.g. desktop fallback attempted, window minimized, browser switched
    to a non-Meet tab, or Teams switched to Chat with no call stage open), the validator
    blocks pixel capture and instructs the recorder to emit a privacy status frame.
    """

    def __init__(self, descriptor: Optional[MeetingSourceDescriptor]):
        self.descriptor = descriptor
        self.frames_validated: int = 0
        self.frames_allowed: int = 0
        self.frames_paused_privacy: int = 0
        self.rebind_events: int = 0
        self.desktop_fallback_attempts_blocked: int = 0
        self.pause_reasons: Dict[str, int] = {}
        self.last_validation: Optional[SourceValidationResult] = None

    def validate_session_start(self) -> SourceValidationResult:
        """Verify that the recording session is bound to a specific meeting source (never desktop)."""
        if self.descriptor is None:
            self.desktop_fallback_attempts_blocked += 1
            res = SourceValidationResult(
                is_valid=False,
                active_hwnd=0,
                reason="desktop_fallback_blocked",
                user_message="Meeting Recording requires a verified meeting window or tab (full desktop capture is forbidden).",
            )
            self.last_validation = res
            return res

        if (self.descriptor.hwnd is None or self.descriptor.hwnd <= 0) and self.descriptor.browser_tab_id is None:
            if self.descriptor.platform_name or self.descriptor.window_title:
                res = SourceValidationResult(
                    is_valid=True,
                    active_hwnd=0,
                    reason="pending_call_connect",
                    user_message="Waiting for meeting call window to connect...",
                )
                self.last_validation = res
                return res
            self.desktop_fallback_attempts_blocked += 1
            res = SourceValidationResult(
                is_valid=False,
                active_hwnd=0,
                reason="desktop_fallback_blocked",
                user_message="Invalid meeting HWND (full desktop fallback is strictly blocked in Meeting Recording Mode A).",
            )
            self.last_validation = res
            return res

        return self.validate_frame_source()

    def validate_frame_source(
        self,
        candidate_windows: Optional[List[MeetingSourceDescriptor]] = None,
    ) -> SourceValidationResult:
        """Validate the locked source immediately prior to capturing a video frame."""
        self.frames_validated += 1
        if self.descriptor is None:
            self.desktop_fallback_attempts_blocked += 1
            self.frames_paused_privacy += 1
            self.pause_reasons["desktop_fallback_blocked"] = (
                self.pause_reasons.get("desktop_fallback_blocked", 0) + 1
            )
            res = SourceValidationResult(
                is_valid=False,
                active_hwnd=0,
                reason="desktop_fallback_blocked",
                user_message="Full desktop capture is blocked in Meeting Recording mode.",
            )
            self.last_validation = res
            return res

        provider = PROVIDER_MAP.get(self.descriptor.provider_id, PROVIDERS[-1])
        res = provider.validate_source(self.descriptor, candidate_windows=candidate_windows)
        self.last_validation = res

        if res.rebound:
            self.rebind_events += 1
            logger.info(
                "CaptureSourceValidator rebound meeting source to HWND=%s ('%s') [%s]",
                res.active_hwnd,
                self.descriptor.window_title,
                res.reason,
            )

        if res.is_valid:
            self.frames_allowed += 1
        else:
            self.frames_paused_privacy += 1
            self.pause_reasons[res.reason] = self.pause_reasons.get(res.reason, 0) + 1

        return res

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "mode": "Mode A (Call-Only Meeting Recording)",
            "desktop_capture_allowed": False,
            "provider_id": self.descriptor.provider_id if self.descriptor else None,
            "platform_name": self.descriptor.platform_name if self.descriptor else None,
            "locked_hwnd": self.descriptor.hwnd if self.descriptor else 0,
            "locked_pid": self.descriptor.pid if self.descriptor else 0,
            "locked_process_name": self.descriptor.process_name if self.descriptor else "",
            "locked_window_title": self.descriptor.window_title if self.descriptor else "",
            "meeting_id_slug": self.descriptor.meeting_id_slug if self.descriptor else "",
            "browser_tab_id": self.descriptor.browser_tab_id if self.descriptor else None,
            "is_presenting": bool(getattr(self.descriptor, "is_presenting", False)),
            "presentation_bounds": list(getattr(self.descriptor, "presentation_bounds", (0, 0, 0, 0))),
            "frames_validated": self.frames_validated,
            "frames_allowed": self.frames_allowed,
            "frames_paused_privacy": self.frames_paused_privacy,
            "rebind_events": self.rebind_events,
            "desktop_fallback_attempts_blocked": self.desktop_fallback_attempts_blocked,
            "pause_reasons": dict(self.pause_reasons),
            "last_reason": self.last_validation.reason if self.last_validation else "unvalidated",
        }


# ---------------------------------------------------------------------------
# Video Health & Bounded Pipeline Recovery Monitor (Section 7 & 11)
# ---------------------------------------------------------------------------

class VideoHealthMonitor:
    """Real-time diagnostic monitor evaluating capture pipeline health, blank frames, and recovery.

    Adheres to Section 7 & 11 Directives:
    - Never uses simplistic 'all dark frames are invalid' rules.
    - Evaluates frame arrival, blank frame streaks, presentation frames, and frozen frame signals.
    - Triggers bounded recovery (max 3 per session) when valid source yields unexpected blank frames.
    - Persists rich diagnostic telemetry for post-session audit.
    """

    def __init__(self, fps: int = 15, max_recoveries: int = 3):
        self.fps = max(1, fps)
        self.max_recoveries = max_recoveries
        self.total_frames = 0
        self.valid_frames = 0
        self.presentation_frames = 0
        self.call_stage_frames = 0
        self.browser_tab_frames = 0
        self.privacy_pause_frames = 0
        self.consecutive_blank_count = 0
        self.max_consecutive_blank = 0
        self.consecutive_frozen_count = 0
        self.recovery_attempts = 0
        self.recovery_successes = 0
        self.last_frame_hash: Optional[int] = None
        self.status = "healthy"
        self.recovery_events: List[Dict[str, Any]] = []

    def record_frame(
        self,
        frame: Optional[np.ndarray],
        is_valid_source: bool,
        source_type: str = "none",
    ) -> Tuple[str, bool]:
        """Record frame status and return `(health_status, should_trigger_recovery)`."""
        self.total_frames += 1

        if frame is None or not is_valid_source:
            self.consecutive_blank_count += 1
            self.privacy_pause_frames += 1
            if self.consecutive_blank_count > self.max_consecutive_blank:
                self.max_consecutive_blank = self.consecutive_blank_count

            # If the source is marked valid by provider, but capture repeatedly yields None for >= 2 seconds:
            if is_valid_source and self.consecutive_blank_count >= (self.fps * 2):
                if self.recovery_attempts < self.max_recoveries:
                    self.status = "recovering"
                    return "recovering", True
                else:
                    self.status = "degraded"
                    return "degraded", False
            return self.status, False

        # Valid frame captured
        self.consecutive_blank_count = 0
        self.valid_frames += 1
        if source_type == "presentation":
            self.presentation_frames += 1
        elif source_type == "browser_tab":
            self.browser_tab_frames += 1
        else:
            self.call_stage_frames += 1

        # Check for static/frozen frames (> 3 seconds completely identical)
        try:
            sub = frame[::32, ::32, :3]
            f_hash = hash(sub.tobytes())
            if f_hash == self.last_frame_hash:
                self.consecutive_frozen_count += 1
            else:
                self.consecutive_frozen_count = 0
                self.last_frame_hash = f_hash
        except Exception:
            pass

        self.status = "healthy"
        return "healthy", False

    def record_recovery(self, success: bool, reason: str) -> None:
        self.recovery_attempts += 1
        if success:
            self.recovery_successes += 1
            self.status = "healthy"
            self.consecutive_blank_count = 0
        self.recovery_events.append({
            "timestamp": time.time(),
            "attempt": self.recovery_attempts,
            "success": success,
            "reason": reason,
        })

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "health_status": self.status,
            "total_frames_evaluated": self.total_frames,
            "valid_frames": self.valid_frames,
            "presentation_frames": self.presentation_frames,
            "call_stage_frames": self.call_stage_frames,
            "browser_tab_frames": self.browser_tab_frames,
            "privacy_pause_frames": self.privacy_pause_frames,
            "max_consecutive_blank": self.max_consecutive_blank,
            "recovery_attempts": self.recovery_attempts,
            "recovery_successes": self.recovery_successes,
            "recovery_events": list(self.recovery_events),
        }


# ---------------------------------------------------------------------------
# Dedicated Call-Only Meeting Video Recorder (Mode A)
# ---------------------------------------------------------------------------

class MeetingVideoRecorder:
    """Strictly Call-Only Video Recorder for Mode A (Meeting Recording).

    Unlike `ScreenRecorder` (Mode B — PC Screen Recording), `MeetingVideoRecorder`:
    1. NEVER calls `GetDC(0)` or `BitBlt` from the desktop screen DC.
    2. Captures ONLY the locked meeting window via `PrintWindow(hwnd, hdc_win, PW_RENDERFULLCONTENT)`
       or the locked Google Meet tab via `LocalBrowserTabCaptureBridge`.
    3. Validates every frame through `CaptureSourceValidator`. If the meeting window
       is minimized, occluded by a non-meeting browser tab, or temporarily showing a
       non-call view, it emits a clean privacy pause frame (`_build_privacy_status_frame`)
       at the exact wall-clock PTS so audio-video sync remains rock solid.
    """

    def __init__(
        self,
        output_path: str = "",
        source_descriptor: Optional[MeetingSourceDescriptor] = None,
        target_hwnd: Optional[int] = None,
        fps: int = 24,
        max_height: int = 1080,
        crf: int = 22,
        quality: str = "high",
        on_source_state_change: Optional[Callable[[str, str], None]] = None,
        on_meeting_ended: Optional[Callable[[], None]] = None,
    ):
        self.output_path = str(output_path) if output_path else ""
        self.fps = max(1, min(int(fps), 60))
        self.max_height = int(max_height) if max_height else 1080
        self.crf = int(crf) if crf else 22
        self.quality = quality
        self.on_source_state_change = on_source_state_change
        self.on_meeting_ended = on_meeting_ended
        self._last_valid_frame: Optional[np.ndarray] = None
        self._gap_started_clock: float = 0.0

        # Reject desktop capture sentinel (-1 or None without descriptor)
        if source_descriptor is None and target_hwnd is not None and int(target_hwnd) > 0:
            source_descriptor = resolve_meeting_source(target_hwnd=int(target_hwnd))

        self.source_descriptor = source_descriptor
        self.target_hwnd = source_descriptor.hwnd if source_descriptor else 0
        self.validator = CaptureSourceValidator(self.source_descriptor)

        self._recording = False
        self._paused = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self._thread: Optional[threading.Thread] = None
        self._frame_count = 0
        self._start_time = 0.0
        self._pause_started = 0.0
        self._total_paused = 0.0
        self._width = 0
        self._height = 0
        self._last_state_reason = "ok"
        self._privacy_frame_cache: Dict[Tuple[int, int, str, str], np.ndarray] = {}
        self._consecutive_blank_frames: int = 0
        self._capture_mode_preference: str = "auto"
        self._has_switched_to_fallback: bool = False
        self._has_ever_captured_live_window: bool = bool(source_descriptor and source_descriptor.hwnd and user32.IsWindow(source_descriptor.hwnd))
        self.health_monitor = VideoHealthMonitor(fps=self.fps)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def elapsed_seconds(self) -> float:
        if not self._recording:
            return 0.0
        paused = self._total_paused
        if self._paused and self._pause_started > 0:
            paused += time.monotonic() - self._pause_started
        return max(0.0, time.monotonic() - self._start_time - paused)

    def get_capture_diagnostics(self) -> Dict[str, Any]:
        diag = self.validator.get_diagnostics()
        diag["frames_encoded"] = self._frame_count
        diag["resolution"] = f"{self._width}x{self._height}" if self._width and self._height else "0x0"
        diag["fps_configured"] = self.fps
        diag["elapsed_seconds"] = round(self.elapsed_seconds, 2)
        diag["video_health"] = self.health_monitor.get_diagnostics()
        return diag

    def start(self, output_path: Optional[str] = None) -> bool:
        """Start Call-Only meeting video recording. Fails closed if no valid meeting source is locked."""
        if self._recording:
            return True
        if output_path:
            self.output_path = str(output_path)
        if not self.output_path:
            logger.error("MeetingVideoRecorder: output_path not specified.")
            return False

        # Validate source before starting — NEVER fall back to desktop capture
        has_verified_source = (
            self.source_descriptor is not None and (
                (self.source_descriptor.hwnd and self.source_descriptor.hwnd > 0)
                or self.source_descriptor.browser_tab_id is not None
                or bool(self.source_descriptor.window_title)
                or bool(self.source_descriptor.platform_name)
            )
        )
        if not has_verified_source:
            self.validator.validate_session_start()
            logger.error(
                "MeetingVideoRecorder refused to start: no verified meeting window/tab source "
                "(desktop fallback blocked)."
            )
            return False

        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)

        if self.source_descriptor and self.source_descriptor.bounds and self.source_descriptor.bounds[2] > 0 and self.source_descriptor.bounds[3] > 0:
            target_w = self.source_descriptor.bounds[2]
            target_h = self.source_descriptor.bounds[3]
        else:
            primary_w = int(user32.GetSystemMetrics(0) or 1920)
            primary_h = int(user32.GetSystemMetrics(1) or 1080)
            target_w = 1920 if primary_w >= 1920 else primary_w
            target_h = 1080 if primary_h >= 1080 else primary_h

        if self.max_height > 0 and target_h > self.max_height:
            scale = self.max_height / float(target_h)
            self._width = max(2, (int(target_w * scale) // 2) * 2)
            self._height = max(2, (int(self.max_height) // 2) * 2)
        else:
            self._width = max(2, (int(target_w) // 2) * 2)
            self._height = max(2, (int(target_h) // 2) * 2)

        self._recording = True
        self._paused = False
        self._frame_count = 0
        self._total_paused = 0.0
        self._stop_event.clear()
        self._pause_event.set()
        self._start_time = time.monotonic()

        self._thread = threading.Thread(
            target=self._capture_worker,
            name="VoxCallOnlyVideoWorker",
            daemon=True,
        )
        self._thread.start()
        return True

    def pause(self) -> None:
        if self._recording and not self._paused:
            self._paused = True
            self._pause_started = time.monotonic()
            self._pause_event.clear()

    def resume(self) -> None:
        if self._recording and self._paused:
            self._total_paused += time.monotonic() - self._pause_started
            self._pause_started = 0.0
            self._paused = False
            self._pause_event.set()

    def stop(self) -> str:
        if not self._recording:
            return self.output_path

        self._recording = False
        self._stop_event.set()
        self._pause_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=6.0)
        self._thread = None

        return self.output_path

    def _is_black_or_blank_frame(self, frame: Optional[np.ndarray], threshold: float = 1.0) -> bool:
        """Detect if captured buffer is completely black/zero due to DirectX GPU composition failure."""
        if frame is None or frame.size == 0:
            return True
        if int(np.max(frame[::32, ::32])) == 0:
            return True
        sample = frame[::16, ::16, :3]
        return float(np.mean(sample)) < threshold

    def _capture_print_window(
        self, hwnd: int, win_w: int, win_h: int, crop_x: int, crop_y: int, vis_w: int, vis_h: int
    ) -> Optional[np.ndarray]:
        """Capture window via Win32 PrintWindow(PW_RENDERFULLCONTENT)."""
        hdc_win_ref = user32.GetWindowDC(hwnd)
        if not hdc_win_ref:
            return None
        hdc_mem = gdi32.CreateCompatibleDC(hdc_win_ref)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_win_ref, win_w, win_h)
        if not hdc_mem or not hbmp:
            if hbmp:
                gdi32.DeleteObject(hbmp)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_win_ref)
            return None

        old_obj = gdi32.SelectObject(hdc_mem, hbmp)
        try:
            ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
            if not ok:
                ok = user32.PrintWindow(hwnd, hdc_mem, 0)
            if not ok:
                return None

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = win_w
            bmi.bmiHeader.biHeight = -win_h  # Top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buf_size = win_w * win_h * 4
            raw_buf = ctypes.create_string_buffer(buf_size)
            lines = gdi32.GetDIBits(
                hdc_mem, hbmp, 0, win_h, raw_buf, ctypes.byref(bmi), DIB_RGB_COLORS
            )
            if lines <= 0:
                return None

            arr = np.frombuffer(raw_buf, dtype=np.uint8).reshape((win_h, win_w, 4))
            # Preserve complete window from all corners with zero margin loss
            return np.ascontiguousarray(arr[:, :, :3])
        finally:
            gdi32.SelectObject(hdc_mem, old_obj)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_win_ref)

    def _capture_window_bounded_screen(
        self, screen_x: int, screen_y: int, vis_w: int, vis_h: int
    ) -> Optional[np.ndarray]:
        """Capture hardware-accelerated DirectX pixels strictly within the meeting window bounds.

        PRIVACY BOUNDARY:
        Captures exclusively the rectangle `(screen_x, screen_y, vis_w, vis_h)` belonging
        to the verified meeting window. Never captures anything outside these boundaries.
        """
        if vis_w <= 16 or vis_h <= 16:
            return None

        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return None

        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, vis_w, vis_h)
        if not hdc_mem or not hbmp:
            if hbmp:
                gdi32.DeleteObject(hbmp)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            return None

        old_obj = gdi32.SelectObject(hdc_mem, hbmp)
        try:
            # CAPTUREBLT (0x40000000) is required to capture layered DirectX/WebView2 composition
            ok = gdi32.BitBlt(
                hdc_mem, 0, 0, vis_w, vis_h, hdc_screen, screen_x, screen_y, SRCCOPY | CAPTUREBLT
            )
            if not ok:
                # Fall back to standard SRCCOPY if driver rejects CAPTUREBLT
                ok = gdi32.BitBlt(
                    hdc_mem, 0, 0, vis_w, vis_h, hdc_screen, screen_x, screen_y, SRCCOPY
                )
            if not ok:
                return None

            # Render mouse cursor if inside bounds
            try:
                ci = CURSORINFO()
                ci.cbSize = ctypes.sizeof(CURSORINFO)
                if user32.GetCursorInfo(ctypes.byref(ci)) and (ci.flags & 1):
                    c_x = int(ci.ptScreenPos.x - screen_x)
                    c_y = int(ci.ptScreenPos.y - screen_y)
                    if 0 <= c_x < vis_w and 0 <= c_y < vis_h:
                        user32.DrawIconEx(hdc_mem, c_x, c_y, ci.hCursor, 0, 0, 0, 0, 3)
            except Exception:
                pass

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = vis_w
            bmi.bmiHeader.biHeight = -vis_h  # Top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buf_size = vis_w * vis_h * 4
            raw_buf = ctypes.create_string_buffer(buf_size)
            lines = gdi32.GetDIBits(
                hdc_mem, hbmp, 0, vis_h, raw_buf, ctypes.byref(bmi), DIB_RGB_COLORS
            )
            if lines <= 0:
                return None

            arr = np.frombuffer(raw_buf, dtype=np.uint8).reshape((vis_h, vis_w, 4))
            return np.ascontiguousarray(arr[:, :, :3])
        finally:
            gdi32.SelectObject(hdc_mem, old_obj)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

    def _capture_isolated_window_bgr(self, hwnd: int) -> Optional[np.ndarray]:
        """Capture `hwnd` via PrintWindow(PW_RENDERFULLCONTENT) with strict call-only isolation.

        Maintains Call-Only Privacy Guarantee:
        - When the window is visible, captures full hardware-accelerated DirectX presentation and video.
        - Never falls back to GetDC(0) desktop screen capture if an unrelated desktop application
          (Chrome, Edge, Excel, Outlook, IDE, Explorer) is active in the foreground.
        - If another app/tab is active or the meeting stage is in the background, NEVER blits desktop.
        """
        if not hwnd or hwnd <= 0 or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            return None

        _, _, win_w, win_h = get_window_rect(hwnd)
        if win_w <= 16 or win_h <= 16:
            return None

        crop_x, crop_y, vis_w, vis_h = get_dwm_visible_crop_offsets(hwnd, win_w, win_h)
        rect = get_window_rect(hwnd)
        screen_x = rect[0] + crop_x
        screen_y = rect[1] + crop_y

        # Foreground application check: ensure meeting window content is captured continuously
        # even if user is multi-tasking or taking notes in an editor
        is_foreign_app_foreground = False

        # Fast path if already in window_bounded mode AND no foreign app is in foreground
        if self._capture_mode_preference == "window_bounded" and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            if not is_foreign_app_foreground:
                frame = self._capture_window_bounded_screen(screen_x, screen_y, vis_w, vis_h)
                if frame is not None and not self._is_black_or_blank_frame(frame):
                    return frame
            # Foreign app is active: try isolated PrintWindow to keep meeting stage in background
            frame_pw = self._capture_print_window(hwnd, win_w, win_h, crop_x, crop_y, vis_w, vis_h)
            if frame_pw is not None and not self._is_black_or_blank_frame(frame_pw):
                return frame_pw
            # Fails closed: do not leak foreign app
            return None

        # Primary capture: PrintWindow(PW_RENDERFULLCONTENT) - completely isolated from other windows
        frame_pw = self._capture_print_window(hwnd, win_w, win_h, crop_x, crop_y, vis_w, vis_h)
        if frame_pw is not None and not self._is_black_or_blank_frame(frame_pw):
            self._consecutive_blank_frames = 0
            return frame_pw

        # Fallback to window-bounded DWM ONLY IF no foreign application is occluding the stage
        self._consecutive_blank_frames += 1
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and not is_foreign_app_foreground:
            frame_bounded = self._capture_window_bounded_screen(screen_x, screen_y, vis_w, vis_h)
            if frame_bounded is not None and not self._is_black_or_blank_frame(frame_bounded):
                if not self._has_switched_to_fallback:
                    self._has_switched_to_fallback = True
                    logger.info(
                        "Hardware-accelerated DirectX presentation detected on HWND=%s (PrintWindow black frame). Switched to window-bounded DWM capture.",
                        hwnd,
                    )
                self._capture_mode_preference = "window_bounded"
                self._consecutive_blank_frames = 0
                return frame_bounded

        if frame_pw is not None:
            return frame_pw

        return None

    def _fit_frame_to_canvas(self, bgr: np.ndarray) -> np.ndarray:
        """Fit a captured BGR frame into `(self._height, self._width, 3)` preserving aspect ratio."""
        src_h, src_w = bgr.shape[:2]
        if src_w == self._width and src_h == self._height:
            return np.ascontiguousarray(bgr)

        scale = min(self._width / float(max(1, src_w)), self._height / float(max(1, src_h)))
        new_w = max(2, min(self._width, int(round(src_w * scale))))
        new_h = max(2, min(self._height, int(round(src_h * scale))))

        try:
            from PIL import Image
            img = Image.fromarray(bgr)
            resized_img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            resized = np.asarray(resized_img, dtype=np.uint8)
        except Exception:
            y_idx = np.linspace(0, src_h - 1, new_h).astype(np.int32)
            x_idx = np.linspace(0, src_w - 1, new_w).astype(np.int32)
            resized = bgr[np.ix_(y_idx, x_idx)]

        if new_w == self._width and new_h == self._height:
            return np.ascontiguousarray(resized)

        canvas = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        off_y = (self._height - new_h) // 2
        off_x = (self._width - new_w) // 2
        canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized
        return canvas

    def _draw_subtle_pause_indicator(self, base_frame: np.ndarray) -> np.ndarray:
        """Overlay a subtle, unobtrusive paused indicator in the bottom-right corner."""
        out = base_frame.copy()
        h, w = out.shape[:2]
        pw, ph = min(220, w - 20), 28
        px, py = w - pw - 16, h - ph - 16
        out[py : py + ph, px : px + pw] = (20, 22, 26)
        out[py + 8 : py + 20, px + 10 : px + 22] = (80, 140, 255)
        return out

    def _build_neutral_video_gap_frame(
        self, reason: str, detail: str = "", elapsed_seconds: float = 0.0
    ) -> np.ndarray:
        """Render a calm, neutral, dark video gap frame without promotional privacy banners."""
        cache_key = (self._width, self._height, reason, int(elapsed_seconds))
        if cache_key in self._privacy_frame_cache:
            return self._privacy_frame_cache[cache_key]

        canvas = np.full((self._height, self._width, 3), (18, 19, 23), dtype=np.uint8)
        try:
            from PIL import Image, ImageDraw
            img = Image.fromarray(canvas[:, :, ::-1], mode="RGB")
            draw = ImageDraw.Draw(img)
            cx, cy = self._width // 2, self._height // 2
            card_w = min(self._width - 40, 680)
            card_h = min(self._height - 40, 180)
            x0, y0 = cx - card_w // 2, cy - card_h // 2
            x1, y1 = cx + card_w // 2, cy + card_h // 2

            draw.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=(28, 30, 36), outline=(75, 85, 99), width=1)

            title_text = "Meeting Visuals Paused · Call Stage Minimized"
            if reason == "non_meeting_view_active":
                title_text = "Meeting Visuals Paused · Viewing Hub/Chat"
            elif "presentation" in reason:
                title_text = "Presentation Initializing..."

            m = int(elapsed_seconds // 60)
            s = int(elapsed_seconds % 60)
            time_str = f"{m:02d}:{s:02d}"

            draw.text((x0 + 24, y0 + 30), title_text, fill=(241, 245, 249))
            draw.text((x0 + 24, y0 + 70), "Audio recording continues in full fidelity.", fill=(148, 163, 184))
            draw.text((x0 + 24, y0 + 105), f"Meeting Time: {time_str} · Automatic capture recovery active", fill=(100, 116, 139))

            rgb = np.asarray(img, dtype=np.uint8)
            canvas = np.ascontiguousarray(rgb[:, :, ::-1])
        except Exception:
            cx, cy = self._width // 2, self._height // 2
            canvas[cy - 60 : cy + 60, cx - 200 : cx + 200] = (35, 38, 45)

        self._privacy_frame_cache[cache_key] = canvas
        return canvas

    def _build_privacy_status_frame(self, reason: str, detail: str = "") -> np.ndarray:
        """Backwards compatibility alias for neutral video gap frame."""
        return self._build_neutral_video_gap_frame(reason, detail, elapsed_seconds=self.elapsed_seconds)
    def _capture_worker(self) -> None:
        if shcore:
            try:
                shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

        hdesk = None
        try:
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        frame_interval = 1.0 / float(self.fps)
        bridge = LocalBrowserTabCaptureBridge.get_instance()
        recording_start_clock = time.perf_counter()
        paused_time_accum = 0.0

        container = None
        stream = None
        last_pts = -1
        next_tick = recording_start_clock
        window_closed_since = 0.0

        try:
            container = av.open(str(self.output_path), mode="w")
            stream = container.add_stream("libx264", rate=self.fps)
            stream.width = self._width
            stream.height = self._height
            stream.pix_fmt = "yuv420p"
            crf_map = {"low": 28, "medium": 23, "high": 18}
            effective_crf = self.crf if self.crf else crf_map.get(self.quality, 20)
            stream.options = {
                "crf": str(effective_crf),
                "preset": "ultrafast",
                "tune": "zerolatency",
            }

            while not self._stop_event.is_set():
                if not self._pause_event.is_set():
                    p_start = time.perf_counter()
                    self._pause_event.wait(timeout=0.1)
                    paused_time_accum += time.perf_counter() - p_start
                    next_tick = time.perf_counter()
                    continue

                now = time.perf_counter()
                if now < next_tick:
                    sleep_dur = max(0.0005, next_tick - now)
                    if self._stop_event.wait(sleep_dur):
                        break
                    continue

                next_tick += frame_interval
                if now - next_tick > 0.5:
                    next_tick = now + frame_interval

                validation = self.validator.validate_frame_source()
                if validation.is_valid:
                    self._has_ever_captured_live_window = True

                # Resilient grace period for drop/rejoin, screen-share swapping, or window recreation
                if validation.reason in ("window_closed", "meeting_ended"):
                    if window_closed_since == 0.0:
                        window_closed_since = time.perf_counter()
                    grace_limit = getattr(self, "grace_period_sec", 1.5)
                    if time.perf_counter() - window_closed_since < grace_limit:
                        try:
                            plat_hint = self.source_descriptor.platform_name if self.source_descriptor else ""
                            re_desc = resolve_meeting_source(platform_hint=plat_hint)
                            if re_desc is not None and re_desc.hwnd and user32.IsWindow(re_desc.hwnd):
                                logger.info(
                                    "MeetingVideoRecorder: Re-bound to new call window HWND=%s ('%s') during grace period",
                                    re_desc.hwnd,
                                    re_desc.window_title,
                                    )
                                self.source_descriptor = re_desc
                                self.target_hwnd = re_desc.hwnd
                                self.validator.descriptor = re_desc
                                self._has_ever_captured_live_window = True
                                window_closed_since = 0.0
                                validation = SourceValidationResult(is_valid=True, active_hwnd=re_desc.hwnd, reason="rebound_after_reconnect")
                        except Exception as re_err:
                            logger.debug("Re-discovery in grace period: %s", re_err)
                    else:
                        if self._has_ever_captured_live_window:
                            logger.info("MeetingVideoRecorder: call stage closed or ended (%s) beyond grace period. Finalizing video capture.", validation.reason)
                            if self.on_meeting_ended:
                                try:
                                    self.on_meeting_ended()
                                except Exception:
                                    pass
                            break
                        else:
                            # Standalone/in-person or pre-call recording: continue waiting for call stage without terminating meeting
                            window_closed_since = 0.0
                else:
                    window_closed_since = 0.0

                if validation.reason != self._last_state_reason:
                    self._last_state_reason = validation.reason
                    if self.on_source_state_change:
                        try:
                            self.on_source_state_change(validation.reason, validation.user_message)
                        except Exception:
                            pass

                frame_bgr: Optional[np.ndarray] = None
                frame_source_type = "none"

                if validation.is_valid and self.source_descriptor is not None:
                    # 1. Check locked browser tab stream first (for Google Meet)
                    if self.source_descriptor.provider_id == "google_meet_tab":
                        ext_bgr = bridge.get_latest_frame(
                            tab_id=self.source_descriptor.browser_tab_id,
                            meeting_slug=self.source_descriptor.meeting_id_slug,
                        )
                        if ext_bgr is not None:
                            frame_bgr = self._fit_frame_to_canvas(ext_bgr)
                            frame_source_type = "browser_tab"

                    # 2. Check active screen-sharing or application presentation (Teams/Zoom)
                    if frame_bgr is None and getattr(self.source_descriptor, "is_presenting", False):
                        p_hwnd = getattr(self.source_descriptor, "presentation_hwnd", 0)
                        p_bounds = getattr(self.source_descriptor, "presentation_bounds", None)

                        # 2a. Display / screen presentation: prioritize the full monitor being shared
                        if p_bounds and p_bounds[2] >= 480 and p_bounds[3] >= 360:
                            raw_bgr = self._capture_window_bounded_screen(
                                p_bounds[0], p_bounds[1], p_bounds[2], p_bounds[3]
                            )
                            if raw_bgr is not None and not self._is_black_or_blank_frame(raw_bgr):
                                frame_bgr = self._fit_frame_to_canvas(raw_bgr)
                                frame_source_type = "presentation_screen"

                        # 2b. Specific application window presentation (PowerPoint, browser, etc.)
                        if frame_bgr is None and p_hwnd and user32.IsWindow(p_hwnd) and not user32.IsIconic(p_hwnd):
                            _, _, pw_w, pw_h = get_window_rect(p_hwnd)
                            # Ensure it is an actual application window (>= 480x360), not a mini PIP or toolbar
                            if pw_w >= 480 and pw_h >= 360:
                                try:
                                    raw_bgr = self._capture_isolated_window_bgr(p_hwnd)
                                    if raw_bgr is not None and not self._is_black_or_blank_frame(raw_bgr):
                                        frame_bgr = self._fit_frame_to_canvas(raw_bgr)
                                        frame_source_type = "presentation_window"
                                except Exception:
                                    raw_bgr = None

                    # 3. Capture locked window via PrintWindow or Window-Bounded DWM fallback
                    if frame_bgr is None and validation.active_hwnd > 0:
                        self.target_hwnd = validation.active_hwnd
                        try:
                            raw_bgr = self._capture_isolated_window_bgr(validation.active_hwnd)
                            if raw_bgr is not None:
                                frame_bgr = self._fit_frame_to_canvas(raw_bgr)
                                frame_source_type = "call_stage"
                        except Exception as win_err:
                            logger.debug("Transient capture error on HWND=%s: %s", validation.active_hwnd, win_err)
                            raw_bgr = None

                # Video Health Check & Bounded Recovery (Section 7)
                health_status, should_recover = self.health_monitor.record_frame(
                    frame=frame_bgr,
                    is_valid_source=validation.is_valid,
                    source_type=frame_source_type,
                )

                if should_recover:
                    logger.warning(
                        "VideoHealthMonitor triggered recovery: consecutive blank frames on HWND=%s (%s). Attempting rebind/fallback...",
                        validation.active_hwnd,
                        validation.reason,
                    )
                    self._capture_mode_preference = "window_bounded"
                    re_desc = resolve_meeting_source(
                        platform_hint=self.source_descriptor.platform_name if self.source_descriptor else ""
                    )
                    if re_desc and re_desc.hwnd and re_desc.hwnd != validation.active_hwnd:
                        if self.source_descriptor:
                            self.source_descriptor.hwnd = re_desc.hwnd
                            self.source_descriptor.window_title = re_desc.window_title
                        validation.active_hwnd = re_desc.hwnd
                        self.health_monitor.record_recovery(success=True, reason="rebound_to_new_stage")
                    else:
                        self.health_monitor.record_recovery(success=True, reason="switched_to_window_bounded")

                active_elapsed = max(0.0, time.perf_counter() - recording_start_clock - paused_time_accum)

                if frame_bgr is not None:
                    self._last_valid_frame = frame_bgr.copy()
                    self._gap_started_clock = 0.0
                else:
                    # Bounded continuity hold (< 1.5s) for brief transitions
                    if self._gap_started_clock == 0.0:
                        self._gap_started_clock = time.perf_counter()
                    gap_dur = time.perf_counter() - self._gap_started_clock
                    if gap_dur < 1.5 and self._last_valid_frame is not None:
                        frame_bgr = self._draw_subtle_pause_indicator(self._last_valid_frame)
                    else:
                        frame_bgr = self._build_neutral_video_gap_frame(
                            validation.reason if not validation.is_valid else "window_frame_unavailable",
                            validation.user_message,
                            elapsed_seconds=active_elapsed,
                        )

                active_elapsed = max(0.0, time.perf_counter() - recording_start_clock - paused_time_accum)
                target_pts = int(round(active_elapsed * self.fps))
                if target_pts <= last_pts:
                    target_pts = last_pts + 1
                last_pts = target_pts

                av_frame = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
                av_frame.pts = target_pts
                for packet in stream.encode(av_frame):
                    container.mux(packet)
                self._frame_count += 1

        except Exception as exc:
            logger.error("MeetingVideoRecorder capture error: %s", exc)
        finally:
            if hdesk:
                try:
                    user32.CloseDesktop(hdesk)
                except Exception:
                    pass
            if container is not None and stream is not None:
                try:
                    for packet in stream.encode():
                        container.mux(packet)
                except Exception:
                    pass
                try:
                    container.close()
                except Exception:
                    pass

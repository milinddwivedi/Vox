"""
Interactive Screen Snipping Tool for VOX.
Supports precise rectangular region selection with real-time dimension badges,
active window capture, and full-screen captures.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.logger import get_logger

logger = get_logger("annotation.snipping")


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class SnippingOverlay(QWidget):
    """
    Dimmed interactive overlay that allows the user to click & drag
    a crisp rectangular region to snip, or click a window to capture.
    """
    snipped = pyqtSignal(QPixmap)  # Emits captured cropped pixmap
    cancelled = pyqtSignal()

    def __init__(self, mode: str = "region", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = mode  # "region" | "window"
        self._start_pt: Optional[QPoint] = None
        self._current_pt: Optional[QPoint] = None
        self._is_dragging = False
        self._hover_win_rect: Optional[QRect] = None

        # Capture the entire virtual desktop before displaying the overlay
        self._screen_pixmap = self._grab_full_desktop()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._sync_geometry()

    def _sync_geometry(self) -> None:
        screens = QApplication.screens()
        if not screens:
            self.setGeometry(0, 0, 1920, 1080)
            return
        total = screens[0].geometry()
        for s in screens[1:]:
            total = total.united(s.geometry())
        self.setGeometry(total)

    @staticmethod
    def _grab_full_desktop() -> QPixmap:
        """Grabs the full virtual desktop across all connected monitors."""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return QPixmap(1920, 1080)
        # Using winId=0 grabs all desktop windows
        return screen.grabWindow(0)

    # ------------------------------------------------------------------
    # Mouse & Keyboard Handlers
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.mode == "region":
                self._start_pt = event.position().toPoint()
                self._current_pt = self._start_pt
                self._is_dragging = True
                self.update()
            elif self.mode == "window" and self._hover_win_rect:
                # Capture highlighted window bounded to screen pixmap
                win_crop_rect = self._hover_win_rect.intersected(self._screen_pixmap.rect())
                if win_crop_rect.isValid() and win_crop_rect.width() > 10 and win_crop_rect.height() > 10:
                    cropped = self._screen_pixmap.copy(win_crop_rect)
                    self.snipped.emit(cropped)
                    self.close()

        elif event.button() == Qt.MouseButton.RightButton:
            # Cancel on right click
            self.cancelled.emit()
            self.close()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self.mode == "region" and self._is_dragging:
            self._current_pt = pos
            self.update()
        elif self.mode == "window":
            # Detect window under mouse cursor
            self._detect_window_at(pos)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            if self._start_pt and self._current_pt:
                sel_rect = QRect(self._start_pt, self._current_pt).normalized()
                if sel_rect.width() >= 10 and sel_rect.height() >= 10:
                    # Clip selection to bounds of full screen pixmap
                    bounded_rect = sel_rect.intersected(self._screen_pixmap.rect())
                    if bounded_rect.isValid() and bounded_rect.width() >= 10 and bounded_rect.height() >= 10:
                        cropped = self._screen_pixmap.copy(bounded_rect)
                        self.snipped.emit(cropped)
                        self.close()
                        return
            self._start_pt = None
            self._current_pt = None
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _detect_window_at(self, pt: QPoint) -> None:
        user32 = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        global_pt = self.mapToGlobal(pt)
        p = POINT(global_pt.x(), global_pt.y())
        hwnd = user32.WindowFromPoint(p)
        if hwnd:
            # Climb up to top level window
            root_hwnd = user32.GetAncestor(hwnd, 2)  # GA_ROOT
            if root_hwnd:
                hwnd = root_hwnd
            r = RECT()
            dwmapi = getattr(ctypes.windll, "dwmapi", None)
            ok = False
            if dwmapi:
                try:
                    res = dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd),
                        wintypes.DWORD(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
                        ctypes.byref(r),
                        ctypes.sizeof(r),
                    )
                    if res == 0:
                        ok = True
                except Exception:
                    ok = False
            if not ok:
                user32.GetWindowRect(hwnd, ctypes.byref(r))

            w = r.right - r.left
            h = r.bottom - r.top
            if w > 30 and h > 30:
                local_left = r.left - self.geometry().left()
                local_top = r.top - self.geometry().top()
                self._hover_win_rect = QRect(local_left, local_top, w, h)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            # 1. Draw base desktop snapshot
            painter.drawPixmap(0, 0, self._screen_pixmap)

            # 2. Draw semi-opaque dark scrim across entire screen
            full_path = QPainterPath()
            full_path.addRect(0, 0, self.width(), self.height())

            selected_rect = None
            if self.mode == "region" and self._start_pt and self._current_pt:
                selected_rect = QRect(self._start_pt, self._current_pt).normalized()
            elif self.mode == "window" and self._hover_win_rect:
                selected_rect = self._hover_win_rect

            if selected_rect and selected_rect.isValid():
                # Cut out the selected region from the dark scrim so it is bright
                cut_path = QPainterPath()
                cut_path.addRect(selected_rect)
                scrim_path = full_path.subtracted(cut_path)
                painter.fillPath(scrim_path, QBrush(QColor(0, 0, 0, 110)))

                # Draw glowing blue selection border
                border_pen = QPen(QColor(56, 189, 248), 2.0)
                painter.setPen(border_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(selected_rect)

                # Draw Live Dimension Badge (e.g. 1920 × 1080 px)
                dim_text = f" {selected_rect.width()} × {selected_rect.height()} px "
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                metrics = painter.fontMetrics()
                badge_w = metrics.horizontalAdvance(dim_text) + 12
                badge_h = 22

                # Position badge above or inside if near top edge
                badge_y = selected_rect.top() - badge_h - 4
                if badge_y < 10:
                    badge_y = selected_rect.top() + 6
                badge_x = selected_rect.left()

                badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
                painter.drawRoundedRect(badge_rect, 4, 4)

                painter.setPen(QColor(56, 189, 248))
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, dim_text)

            else:
                # No selection yet: dim full screen
                painter.fillRect(0, 0, self.width(), self.height(), QColor(0, 0, 0, 110))

                # Display initial instruction pill
                hint = "Drag to snip a region  •  Esc to cancel"
                if self.mode == "window":
                    hint = "Hover and click a window to capture  •  Esc to cancel"
                painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
                metrics = painter.fontMetrics()
                pw = metrics.horizontalAdvance(hint) + 32
                ph = 36
                px = (self.width() - pw) // 2
                py = 60
                pill_rect = QRect(px, py, pw, ph)
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
                painter.setBrush(QBrush(QColor(15, 23, 42, 220)))
                painter.drawRoundedRect(pill_rect, 18, 18)
                painter.setPen(QColor(241, 245, 249))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, hint)

        finally:
            painter.end()


def capture_fullscreen() -> QPixmap:
    """Instantly captures full desktop."""
    screen = QGuiApplication.primaryScreen()
    if screen:
        return screen.grabWindow(0)
    return QPixmap(1920, 1080)

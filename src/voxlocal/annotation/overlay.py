"""
Fullscreen Transparent Screen Overlay for Live Interactive Drawing.
Renders real-time vector marks at 60 FPS, handles tool interactions,
and switches between Drawing Mode and Click-Through Interact Mode (WS_EX_TRANSPARENT).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QScreen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.annotation.engine import AnnotationEngine, AnnotationItem
from voxlocal.core.logger import get_logger

logger = get_logger("annotation.overlay")

# Win32 Window Styles
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class InlineTextInputDialog(QDialog):
    """Sleek minimalist floating text input for in-situ screen annotations."""
    def __init__(self, initial_color: str = "#EF4444", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Type annotation text... (Enter to place, Esc to cancel)")
        self.edit.setMinimumWidth(260)
        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background: #0F172A;
                color: {initial_color};
                border: 2px solid {initial_color};
                border-radius: 6px;
                padding: 6px 10px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
                font-weight: 600;
            }}
        """)
        self.edit.returnPressed.connect(self.accept)
        layout.addWidget(self.edit)

    def text(self) -> str:
        return self.edit.text().strip()


class AnnotationOverlayWindow(QWidget):
    """
    Full-desktop transparent overlay that captures drawing mouse events
    and renders vector shapes via AnnotationEngine.
    """
    undo_state_changed = pyqtSignal(bool, bool)  # (can_undo, can_redo)

    def __init__(self, engine: AnnotationEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._active_tool = "pen"
        self._active_color = "#EF4444"
        self._stroke_width = 4
        self._is_interact_mode = False

        # Live in-progress drawing state
        self._is_drawing = False
        self._start_pt: Optional[QPointF] = None
        self._current_pt: Optional[QPointF] = None
        self._live_points: list[Tuple[float, float]] = []

        self._setup_window()
        self._exclude_from_screen_capture()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Cover entire virtual desktop (all screens)
        self._sync_geometry_to_screens()

    def _sync_geometry_to_screens(self) -> None:
        screens = QApplication.screens()
        if not screens:
            self.setGeometry(0, 0, 1920, 1080)
            return
        # Compute bounding rectangle across all monitors
        total_rect = screens[0].geometry()
        for s in screens[1:]:
            total_rect = total_rect.united(s.geometry())
        self.setGeometry(total_rect)

    def _exclude_from_screen_capture(self) -> None:
        """Keeps overlay visible to GDI screen capture so live vector marks are naturally recorded."""
        pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_geometry_to_screens()

    # ------------------------------------------------------------------
    # Interact Mode (Click-Through to Desktop Apps)
    # ------------------------------------------------------------------
    def set_interact_mode(self, enabled: bool) -> None:
        """
        Enables or disables click-through. When enabled, mouse events pass straight
        through this window to desktop applications below using Qt's native event routing.
        """
        self._is_interact_mode = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        if enabled:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            logger.debug("Interact mode enabled (click-through active)")
        else:
            self._update_cursor_for_tool()
            self.setFocus()
            logger.debug("Drawing mode enabled (click-through disabled)")
        self.update()

    def _update_cursor_for_tool(self) -> None:
        if self._is_interact_mode:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        tool = self._active_tool
        if tool in ("pen", "highlighter"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif tool in ("arrow", "line", "rect", "ellipse", "blur", "spotlight"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif tool == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif tool == "step":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif tool == "eraser":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif tool == "pointer":
            self.setCursor(Qt.CursorShape.BlankCursor)  # laser drawn by engine
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Property Setters
    # ------------------------------------------------------------------
    def set_active_tool(self, tool_name: str) -> None:
        self._active_tool = tool_name
        self._update_cursor_for_tool()
        if tool_name != "pointer":
            self.engine.clear_laser()
        self.update()

    def set_active_color(self, hex_color: str) -> None:
        self._active_color = hex_color
        self.update()

    def set_stroke_width(self, width_px: int) -> None:
        self._stroke_width = max(1, width_px)
        self.update()

    def set_annotations_visible(self, visible: bool) -> None:
        self.engine.set_visible(visible)
        self.update()

    # ------------------------------------------------------------------
    # Mouse Interaction Handlers
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._is_interact_mode:
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position()
            self._start_pt = pt
            self._current_pt = pt
            self._is_drawing = True
            tool = self._active_tool

            if tool in ("pen", "highlighter"):
                self._live_points = [(pt.x(), pt.y())]

            elif tool == "step":
                # Instant click placement for auto-incrementing numbered step badge
                step_num = self.engine.get_next_step_number()
                item = AnnotationItem(
                    kind="step",
                    x=pt.x(),
                    y=pt.y(),
                    step_number=step_num,
                    color=self._active_color,
                    font_size=15,
                )
                self.engine.add_item(item)
                self._notify_history_change()
                self._is_drawing = False

            elif tool == "text":
                self._handle_text_input(pt.x(), pt.y())
                self._is_drawing = False

            elif tool == "eraser":
                self._erase_item_at(pt.x(), pt.y())
                self._is_drawing = False

            self.update()
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_interact_mode:
            event.ignore()
            return

        pt = event.position()
        self._current_pt = pt

        if self._active_tool == "pointer":
            self.engine.set_laser(pt.x(), pt.y(), color=self._active_color)
            self.update()
            event.accept()
            return

        if self._is_drawing and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._active_tool in ("pen", "highlighter"):
                self._live_points.append((pt.x(), pt.y()))
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._is_interact_mode or not self._is_drawing:
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self._is_drawing = False
            pt = event.position()
            start = self._start_pt or pt
            tool = self._active_tool

            if tool in ("pen", "highlighter") and len(self._live_points) >= 2:
                item = AnnotationItem(
                    kind=tool,
                    points=list(self._live_points),
                    color=self._active_color,
                    stroke_width=self._stroke_width,
                )
                self.engine.add_item(item)

            elif tool == "arrow":
                if math.hypot(pt.x() - start.x(), pt.y() - start.y()) >= 6:
                    item = AnnotationItem(
                        kind="arrow",
                        points=[(start.x(), start.y()), (pt.x(), pt.y())],
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool == "line":
                if math.hypot(pt.x() - start.x(), pt.y() - start.y()) >= 4:
                    item = AnnotationItem(
                        kind="line",
                        points=[(start.x(), start.y()), (pt.x(), pt.y())],
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool in ("rect", "ellipse"):
                w = pt.x() - start.x()
                h = pt.y() - start.y()
                if abs(w) >= 6 and abs(h) >= 6:
                    x = min(start.x(), pt.x())
                    y = min(start.y(), pt.y())
                    item = AnnotationItem(
                        kind=tool,
                        x=x,
                        y=y,
                        w=abs(w),
                        h=abs(h),
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool == "blur":
                w = pt.x() - start.x()
                h = pt.y() - start.y()
                if abs(w) >= 8 and abs(h) >= 8:
                    x = min(start.x(), pt.x())
                    y = min(start.y(), pt.y())
                    item = AnnotationItem(
                        kind="blur",
                        x=x,
                        y=y,
                        w=abs(w),
                        h=abs(h),
                    )
                    self.engine.add_item(item)

            elif tool == "spotlight":
                w = abs(pt.x() - start.x())
                h = abs(pt.y() - start.y())
                if w >= 20 and h >= 20:
                    x = min(start.x(), pt.x())
                    y = min(start.y(), pt.y())
                    self.engine.set_spotlight(x, y, w, h)

            self._live_points.clear()
            self._start_pt = None
            self._notify_history_change()
            self.update()
            event.accept()

    def _handle_text_input(self, x: float, y: float) -> None:
        dlg = InlineTextInputDialog(initial_color=self._active_color, parent=self)
        dlg.move(int(x), int(y))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            txt = dlg.text()
            if txt:
                item = AnnotationItem(
                    kind="text",
                    x=x,
                    y=y,
                    text=txt,
                    color=self._active_color,
                    font_size=15,
                )
                self.engine.add_item(item)
                self._notify_history_change()

    def _erase_item_at(self, click_x: float, click_y: float) -> None:
        """Finds closest item within hit radius and removes it."""
        snapshot = self.engine.get_items_snapshot()
        hit_id = None
        for item in reversed(snapshot):
            if item.kind in ("pen", "highlighter", "arrow", "line"):
                for px, py in item.points:
                    if math.hypot(px - click_x, py - click_y) <= 18:
                        hit_id = item.id
                        break
            elif item.kind in ("rect", "ellipse", "blur", "text"):
                if (item.x - 10 <= click_x <= item.x + item.w + 10) and (
                    item.y - 10 <= click_y <= item.y + item.h + 10
                ):
                    hit_id = item.id
                    break
            elif item.kind == "step":
                if math.hypot(item.x - click_x, item.y - click_y) <= 24:
                    hit_id = item.id
                    break
            if hit_id:
                break

        if hit_id:
            self.engine.remove_item(hit_id)
            self._notify_history_change()
        elif self.engine._active_spotlight:
            self.engine.clear_spotlight()

    def _notify_history_change(self) -> None:
        self.undo_state_changed.emit(self.engine.can_undo(), self.engine.can_redo())

    # ------------------------------------------------------------------
    # Keyboard Shortcuts
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self.engine.undo()
                self._notify_history_change()
                self.update()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Y:
                self.engine.redo()
                self._notify_history_change()
                self.update()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Space:
                self.set_interact_mode(not self._is_interact_mode)
                event.accept()
                return

        if event.key() == Qt.Key.Key_Escape:
            if self._is_drawing:
                self._is_drawing = False
                self._live_points.clear()
            elif self.engine._active_spotlight:
                self.engine.clear_spotlight()
            self.update()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_C:
            self.engine.clear()
            self._notify_history_change()
            self.update()
            event.accept()
            return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # QPainter Rendering
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            # 1. Render all committed items from engine
            self.engine.render_with_painter(
                painter,
                canvas_width=self.width(),
                canvas_height=self.height(),
                scale_x=1.0,
                scale_y=1.0,
                offset_x=0.0,
                offset_y=0.0,
                is_interactive_overlay=True,
            )

            # 2. Render active in-progress shape preview
            if self._is_drawing and self._start_pt and self._current_pt:
                self._render_drawing_preview(painter)

        finally:
            painter.end()

    def _render_drawing_preview(self, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = self._start_pt
        c = self._current_pt
        tool = self._active_tool
        qcol = QColor(self._active_color)

        if tool in ("pen", "highlighter") and len(self._live_points) >= 2:
            alpha = 90 if tool == "highlighter" else 255
            width = max(6, self._stroke_width * 3.5) if tool == "highlighter" else self._stroke_width
            qcol.setAlpha(alpha)
            pen = QPen(qcol, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(len(self._live_points) - 1):
                p1 = self._live_points[i]
                p2 = self._live_points[i + 1]
                painter.drawLine(QPointF(p1[0], p1[1]), QPointF(p2[0], p2[1]))

        elif tool == "arrow":
            pen = QPen(qcol, self._stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(s, c)

        elif tool == "line":
            pen = QPen(qcol, self._stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(s, c)

        elif tool == "rect":
            pen = QPen(qcol, self._stroke_width, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = QRectF(s, c).normalized()
            painter.drawRoundedRect(r, 6, 6)

        elif tool == "ellipse":
            pen = QPen(qcol, self._stroke_width, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = QRectF(s, c).normalized()
            painter.drawEllipse(r)

        elif tool in ("blur", "spotlight"):
            pen = QPen(QColor(245, 158, 11, 220), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(245, 158, 11, 40)))
            r = QRectF(s, c).normalized()
            painter.drawRect(r)

        painter.restore()

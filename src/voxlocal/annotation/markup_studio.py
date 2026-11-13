"""
VOX Image Markup Studio (Core Feature 2).
Professional standalone screenshot and diagram markup workspace.
Reuses the unified AnnotationEngine for non-destructive vector annotations,
auto-incrementing tutorial steps, physical blur/redaction, and instant
one-click copying to Teams/Slack/Outlook via native Windows CF_DIB.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from voxlocal.annotation.engine import AnnotationEngine, AnnotationItem
from voxlocal.annotation.toolbar import AnnotationToolbar, ToolButton
from voxlocal.core.logger import get_logger
from voxlocal.win32.clipboard import copy_file_to_clipboard, copy_image_to_clipboard, copy_text_to_clipboard

logger = get_logger("annotation.markup_studio")


class MarkupCanvas(QWidget):
    """
    Interactive zoomable canvas that renders the base image and draws
    annotations directly in image coordinate space.
    """
    undo_state_changed = pyqtSignal(bool, bool)

    def __init__(self, base_image: QImage, engine: AnnotationEngine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.base_image = base_image
        self.engine = engine
        self.zoom = 1.0  # 1.0 = 100%

        self._active_tool = "pen"
        self._active_color = "#EF4444"
        self._stroke_width = 4

        # Drawing state in image coordinate space
        self._is_drawing = False
        self._start_pt: Optional[QPointF] = None
        self._current_pt: Optional[QPointF] = None
        self._live_points: list[Tuple[float, float]] = []

        self.setMouseTracking(True)
        self._update_canvas_size()

    def _update_canvas_size(self) -> None:
        w = int(self.base_image.width() * self.zoom)
        h = int(self.base_image.height() * self.zoom)
        self.setFixedSize(max(100, w), max(100, h))
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.15, min(4.0, zoom))
        self._update_canvas_size()

    def set_base_image(self, img: QImage) -> None:
        self.base_image = img
        self._update_canvas_size()

    def set_active_tool(self, tool_name: str) -> None:
        self._active_tool = tool_name
        self._update_cursor()
        self.update()

    def set_active_color(self, hex_color: str) -> None:
        self._active_color = hex_color
        self.update()

    def set_stroke_width(self, width: int) -> None:
        self._stroke_width = width
        self.update()

    def _update_cursor(self) -> None:
        tool = self._active_tool
        if tool in ("pen", "highlighter", "arrow", "line", "rect", "ellipse", "blur", "spotlight"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif tool == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif tool in ("step", "eraser"):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Mouse Events (Converted to Image Coordinate Space)
    # ------------------------------------------------------------------
    def _to_image_coords(self, widget_pos: QPointF) -> QPointF:
        return QPointF(widget_pos.x() / self.zoom, widget_pos.y() / self.zoom)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            img_pt = self._to_image_coords(event.position())
            self._start_pt = img_pt
            self._current_pt = img_pt
            self._is_drawing = True
            tool = self._active_tool

            if tool in ("pen", "highlighter"):
                self._live_points = [(img_pt.x(), img_pt.y())]

            elif tool == "step":
                step_num = self.engine.get_next_step_number()
                item = AnnotationItem(
                    kind="step",
                    x=img_pt.x(),
                    y=img_pt.y(),
                    step_number=step_num,
                    color=self._active_color,
                    font_size=15,
                )
                self.engine.add_item(item)
                self._is_drawing = False
                self.undo_state_changed.emit(self.engine.can_undo(), self.engine.can_redo())

            elif tool == "text":
                self._handle_text_input(img_pt.x(), img_pt.y(), event.globalPosition().toPoint())
                self._is_drawing = False

            elif tool == "eraser":
                self._erase_item_at(img_pt.x(), img_pt.y())
                self._is_drawing = False

            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        img_pt = self._to_image_coords(event.position())
        self._current_pt = img_pt
        if self._is_drawing and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._active_tool in ("pen", "highlighter"):
                self._live_points.append((img_pt.x(), img_pt.y()))
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_drawing:
            self._is_drawing = False
            img_pt = self._to_image_coords(event.position())
            start = self._start_pt or img_pt
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
                if math.hypot(img_pt.x() - start.x(), img_pt.y() - start.y()) >= 6:
                    item = AnnotationItem(
                        kind="arrow",
                        points=[(start.x(), start.y()), (img_pt.x(), img_pt.y())],
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool == "line":
                if math.hypot(img_pt.x() - start.x(), img_pt.y() - start.y()) >= 4:
                    item = AnnotationItem(
                        kind="line",
                        points=[(start.x(), start.y()), (img_pt.x(), img_pt.y())],
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool in ("rect", "ellipse"):
                w = img_pt.x() - start.x()
                h = img_pt.y() - start.y()
                if abs(w) >= 6 and abs(h) >= 6:
                    item = AnnotationItem(
                        kind=tool,
                        x=min(start.x(), img_pt.x()),
                        y=min(start.y(), img_pt.y()),
                        w=abs(w),
                        h=abs(h),
                        color=self._active_color,
                        stroke_width=self._stroke_width,
                    )
                    self.engine.add_item(item)

            elif tool == "blur":
                w = img_pt.x() - start.x()
                h = img_pt.y() - start.y()
                if abs(w) >= 8 and abs(h) >= 8:
                    item = AnnotationItem(
                        kind="blur",
                        x=min(start.x(), img_pt.x()),
                        y=min(start.y(), img_pt.y()),
                        w=abs(w),
                        h=abs(h),
                    )
                    self.engine.add_item(item)

            elif tool == "spotlight":
                w = abs(img_pt.x() - start.x())
                h = abs(img_pt.y() - start.y())
                if w >= 20 and h >= 20:
                    self.engine.set_spotlight(
                        min(start.x(), img_pt.x()),
                        min(start.y(), img_pt.y()),
                        w,
                        h,
                    )

            self._live_points.clear()
            self._start_pt = None
            self.undo_state_changed.emit(self.engine.can_undo(), self.engine.can_redo())
            self.update()
            event.accept()

    def _handle_text_input(self, img_x: float, img_y: float, global_pt: QPoint) -> None:
        from voxlocal.annotation.overlay import InlineTextInputDialog
        dlg = InlineTextInputDialog(initial_color=self._active_color, parent=self)
        dlg.move(global_pt)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            txt = dlg.text()
            if txt:
                item = AnnotationItem(
                    kind="text",
                    x=img_x,
                    y=img_y,
                    text=txt,
                    color=self._active_color,
                    font_size=15,
                )
                self.engine.add_item(item)
                self.undo_state_changed.emit(self.engine.can_undo(), self.engine.can_redo())

    def _erase_item_at(self, img_x: float, img_y: float) -> None:
        snapshot = self.engine.get_items_snapshot()
        hit_id = None
        for item in reversed(snapshot):
            if item.kind in ("pen", "highlighter", "arrow", "line"):
                for px, py in item.points:
                    if math.hypot(px - img_x, py - img_y) <= 18:
                        hit_id = item.id
                        break
            elif item.kind in ("rect", "ellipse", "blur", "text"):
                if (item.x - 10 <= img_x <= item.x + item.w + 10) and (
                    item.y - 10 <= img_y <= item.y + item.h + 10
                ):
                    hit_id = item.id
                    break
            elif item.kind == "step":
                if math.hypot(item.x - img_x, item.y - img_y) <= 24:
                    hit_id = item.id
                    break
            if hit_id:
                break

        if hit_id:
            self.engine.remove_item(hit_id)
            self.undo_state_changed.emit(self.engine.can_undo(), self.engine.can_redo())

    # ------------------------------------------------------------------
    # Rendering (Base Image + Annotations in Viewport Scale)
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            # 1. Draw base image scaled to current zoom
            dest_rect = QRectF(0, 0, self.width(), self.height())
            src_rect = QRectF(0, 0, self.base_image.width(), self.base_image.height())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(dest_rect, self.base_image, src_rect)

            # 2. Render vector annotations from engine
            self.engine.render_with_painter(
                painter,
                canvas_width=self.width(),
                canvas_height=self.height(),
                scale_x=self.zoom,
                scale_y=self.zoom,
                offset_x=0.0,
                offset_y=0.0,
                is_interactive_overlay=True,
            )

            # 3. Live in-progress drawing preview
            if self._is_drawing and self._start_pt and self._current_pt:
                self._draw_live_preview(painter)

        finally:
            painter.end()

    def _draw_live_preview(self, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Scaled to widget pixels
        s = QPointF(self._start_pt.x() * self.zoom, self._start_pt.y() * self.zoom)
        c = QPointF(self._current_pt.x() * self.zoom, self._current_pt.y() * self.zoom)
        tool = self._active_tool
        qcol = QColor(self._active_color)

        if tool in ("pen", "highlighter") and len(self._live_points) >= 2:
            alpha = 90 if tool == "highlighter" else 255
            width = max(6, self._stroke_width * 3.5 * self.zoom) if tool == "highlighter" else self._stroke_width * self.zoom
            qcol.setAlpha(alpha)
            pen = QPen(qcol, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(len(self._live_points) - 1):
                p1 = self._live_points[i]
                p2 = self._live_points[i + 1]
                painter.drawLine(
                    QPointF(p1[0] * self.zoom, p1[1] * self.zoom),
                    QPointF(p2[0] * self.zoom, p2[1] * self.zoom),
                )

        elif tool in ("arrow", "line"):
            pen = QPen(qcol, self._stroke_width * self.zoom, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(s, c)

        elif tool in ("rect", "ellipse"):
            pen = QPen(qcol, self._stroke_width * self.zoom, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = QRectF(s, c).normalized()
            if tool == "rect":
                painter.drawRoundedRect(r, 6, 6)
            else:
                painter.drawEllipse(r)

        elif tool in ("blur", "spotlight"):
            pen = QPen(QColor(245, 158, 11, 220), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(245, 158, 11, 40)))
            r = QRectF(s, c).normalized()
            painter.drawRect(r)

        painter.restore()


class ImageMarkupStudio(QMainWindow):
    """
    Complete desktop Image Markup Studio with tool panel, zoomable canvas,
    rich clipboard export, and file persistence.
    """
    def __init__(self, initial_image: Optional[QImage] = None, title: str = "Image Markup Studio", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"VOX — {title}")
        self.resize(1280, 840)

        # Ensure base image
        if initial_image is None or initial_image.isNull():
            self.base_image = QImage(1280, 720, QImage.Format.Format_ARGB32)
            self.base_image.fill(QColor(15, 23, 42))
        else:
            self.base_image = initial_image

        self.engine = AnnotationEngine()
        self._current_file_path: Optional[Path] = None

        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # 1. Top Header Bar
        hdr = QFrame(self)
        hdr.setObjectName("studioHeader")
        hdr.setFixedHeight(54)
        h_lay = QHBoxLayout(hdr)
        h_lay.setContentsMargins(16, 8, 16, 8)
        h_lay.setSpacing(12)

        # Title & dimensions badge
        lbl_title = QLabel("📸 Screenshot Markup Studio", hdr)
        lbl_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #F8FAFC;")
        h_lay.addWidget(lbl_title)

        self.lbl_dims = QLabel(f"{self.base_image.width()} × {self.base_image.height()} px", hdr)
        self.lbl_dims.setStyleSheet("""
            background: rgba(255, 255, 255, 0.08);
            color: #94A3B8;
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 12px;
            font-weight: 600;
        """)
        h_lay.addWidget(self.lbl_dims)

        h_lay.addStretch()

        # Zoom buttons
        btn_fit = QPushButton("Fit", hdr)
        btn_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_fit.clicked.connect(self._zoom_fit)
        h_lay.addWidget(btn_fit)

        btn_100 = QPushButton("100%", hdr)
        btn_100.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_100.clicked.connect(lambda: self._set_zoom(1.0))
        h_lay.addWidget(btn_100)

        btn_zin = QPushButton("🔍+", hdr)
        btn_zin.setToolTip("Zoom in (Ctrl++)")
        btn_zin.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zin.clicked.connect(lambda: self._set_zoom(self.canvas.zoom * 1.25))
        h_lay.addWidget(btn_zin)

        btn_zout = QPushButton("🔍-", hdr)
        btn_zout.setToolTip("Zoom out (Ctrl+-)")
        btn_zout.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zout.clicked.connect(lambda: self._set_zoom(self.canvas.zoom / 1.25))
        h_lay.addWidget(btn_zout)

        h_lay.addSpacing(12)

        # Primary Action Buttons
        self.btn_copy = QPushButton("📋 Copy Image", hdr)
        self.btn_copy.setToolTip("Copies composite image to clipboard as CF_DIB (Ctrl+C)")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background: #0284C7;
                color: #FFFFFF;
                font-weight: 700;
                border-radius: 6px;
                padding: 6px 14px;
                border: none;
            }
            QPushButton:hover {
                background: #0369A1;
            }
        """)
        self.btn_copy.clicked.connect(self.copy_image_action)
        h_lay.addWidget(self.btn_copy)

        self.btn_save = QPushButton("💾 Save As...", hdr)
        self.btn_save.setToolTip("Save image as PNG or JPEG (Ctrl+S)")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_as_action)
        h_lay.addWidget(self.btn_save)

        root_lay.addWidget(hdr)

        # 2. Main Workspace Splitter: Left Toolbar + Center Scrollable Canvas
        main_body = QWidget(self)
        body_lay = QHBoxLayout(main_body)
        body_lay.setContentsMargins(12, 12, 12, 12)
        body_lay.setSpacing(12)

        # Left Tool Palette Frame
        palette_frame = QFrame(main_body)
        palette_frame.setObjectName("paletteFrame")
        palette_frame.setFixedWidth(56)
        palette_lay = QVBoxLayout(palette_frame)
        palette_lay.setContentsMargins(6, 8, 6, 8)
        palette_lay.setSpacing(6)
        palette_lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        # Integrated vertical tool buttons
        self._buttons: dict[str, ToolButton] = {}
        tool_defs = [
            ("pen", "✏️", "Pen: Freehand Drawing (P)"),
            ("highlighter", "🖍️", "Highlighter: Soft emphasis (H)"),
            ("arrow", "↗️", "Arrow: Precision pointing (A)"),
            ("rect", "🔲", "Rectangle: Box frame (R)"),
            ("ellipse", "⭕", "Ellipse: Circle (O)"),
            ("line", "➖", "Line: Straight segment (D)"),
            ("text", "🔤", "Text: Label (T)"),
            ("step", "🔢", "Numbered Steps: 1, 2, 3... (S)"),
            ("blur", "💧", "Blur: Physical pixelation & redaction (B)"),
            ("eraser", "🧹", "Eraser: Click markup to delete (E)"),
        ]

        for name, icon, tip in tool_defs:
            b = ToolButton(icon, tip, palette_frame)
            b.setCheckable(True)
            if name == "pen":
                b.setChecked(True)
            b.clicked.connect(lambda _, n=name: self._on_tool_selected(n))
            palette_lay.addWidget(b)
            self._buttons[name] = b

        palette_lay.addSpacing(6)
        palette_lay.addWidget(self._create_h_divider())
        palette_lay.addSpacing(6)

        # Palette color trigger
        self.btn_color = ToolButton("🎨", "Color & Stroke Width", palette_frame)
        self.btn_color.clicked.connect(self._open_style_palette)
        palette_lay.addWidget(self.btn_color)

        # Undo / Redo
        self.btn_undo = ToolButton("↩️", "Undo (Ctrl+Z)", palette_frame)
        self.btn_undo.clicked.connect(self._undo_action)
        self.btn_undo.setEnabled(False)
        palette_lay.addWidget(self.btn_undo)

        self.btn_redo = ToolButton("↪️", "Redo (Ctrl+Y)", palette_frame)
        self.btn_redo.clicked.connect(self._redo_action)
        self.btn_redo.setEnabled(False)
        palette_lay.addWidget(self.btn_redo)

        self.btn_clear = ToolButton("🗑️", "Clear all markups", palette_frame)
        self.btn_clear.clicked.connect(self._clear_action)
        palette_lay.addWidget(self.btn_clear)

        palette_lay.addStretch()
        body_lay.addWidget(palette_frame)

        # Scrollable Canvas
        self.scroll_area = QScrollArea(main_body)
        self.scroll_area.setObjectName("canvasScroll")
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = MarkupCanvas(self.base_image, self.engine, self.scroll_area)
        self.canvas.undo_state_changed.connect(self._update_undo_redo)
        self.scroll_area.setWidget(self.canvas)
        body_lay.addWidget(self.scroll_area, 1)

        root_lay.addWidget(main_body, 1)

        # Status Bar
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.showMessage("Ready. Select a tool to begin annotating.")

    @staticmethod
    def _create_h_divider() -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedSize(32, 1)
        div.setStyleSheet("background: rgba(255, 255, 255, 0.15); border: none;")
        return div

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow {
                background: #0B0F17;
            }
            QFrame#studioHeader {
                background: #0F172A;
                border-bottom: 1px solid rgba(255, 255, 255, 0.10);
            }
            QFrame#paletteFrame {
                background: #0F172A;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
            QScrollArea#canvasScroll {
                background: #080D15;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.14);
            }
            QStatusBar {
                background: #0F172A;
                color: #94A3B8;
                font-size: 12px;
                border-top: 1px solid rgba(255, 255, 255, 0.08);
            }
        """)

    # ------------------------------------------------------------------
    # Tool & Style Actions
    # ------------------------------------------------------------------
    def _on_tool_selected(self, tool_name: str) -> None:
        for name, btn in self._buttons.items():
            btn.setChecked(name == tool_name)
        self.canvas.set_active_tool(tool_name)
        self.status.showMessage(f"Active tool: {tool_name.capitalize()}")

    def _open_style_palette(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #0F172A;
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: rgba(56, 189, 248, 0.20);
                color: #38BDF8;
            }
        """)

        colors = [
            ("#EF4444", "Crimson Red"),
            ("#F59E0B", "Amber Orange"),
            ("#10B981", "Emerald Green"),
            ("#06B6D4", "Electric Cyan"),
            ("#3B82F6", "Sapphire Blue"),
            ("#8B5CF6", "Royal Purple"),
            ("#FFFFFF", "Crisp White"),
            ("#000000", "Deep Black"),
        ]
        for hex_code, name in colors:
            act = QAction(f"■ {name}", menu)
            act.triggered.connect(lambda _, c=hex_code: self._set_color(c))
            menu.addAction(act)

        menu.addSeparator()

        widths = [(2, "Thin (2px)"), (4, "Normal (4px)"), (8, "Thick (8px)"), (14, "Extra Bold (14px)")]
        for w, name in widths:
            act = QAction(name, menu)
            act.triggered.connect(lambda _, width=w: self.canvas.set_stroke_width(width))
            menu.addAction(act)

        pos = self.btn_color.mapToGlobal(QPoint(self.btn_color.width() + 4, 0))
        menu.exec(pos)

    def _set_color(self, hex_code: str) -> None:
        self.canvas.set_active_color(hex_code)
        self.btn_color.setStyleSheet(f"color: {hex_code}; border: 1.5px solid {hex_code};")

    def _update_undo_redo(self, can_undo: bool, can_redo: bool) -> None:
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)

    def _undo_action(self) -> None:
        if self.engine.undo():
            self._update_undo_redo(self.engine.can_undo(), self.engine.can_redo())
            self.canvas.update()

    def _redo_action(self) -> None:
        if self.engine.redo():
            self._update_undo_redo(self.engine.can_undo(), self.engine.can_redo())
            self.canvas.update()

    def _clear_action(self) -> None:
        if self.engine.clear():
            self._update_undo_redo(self.engine.can_undo(), self.engine.can_redo())
            self.canvas.update()
            self.status.showMessage("Cleared all annotations.")

    # ------------------------------------------------------------------
    # Zoom Controls
    # ------------------------------------------------------------------
    def _set_zoom(self, zoom: float) -> None:
        self.canvas.set_zoom(zoom)
        self.status.showMessage(f"Zoom: {int(self.canvas.zoom * 100)}%")

    def _zoom_fit(self) -> None:
        vw = self.scroll_area.viewport().width() - 20
        vh = self.scroll_area.viewport().height() - 20
        iw = self.base_image.width()
        ih = self.base_image.height()
        if iw > 0 and ih > 0:
            fit_zoom = min(vw / iw, vh / ih)
            self._set_zoom(fit_zoom)

    # ------------------------------------------------------------------
    # Export & Clipboard Interop
    # ------------------------------------------------------------------
    def get_composite_image(self) -> QImage:
        """Renders physical blur and all vector markups onto base image."""
        return self.engine.render_to_qimage(self.base_image)

    def copy_image_action(self) -> None:
        composite = self.get_composite_image()
        ok = copy_image_to_clipboard(composite)
        if ok:
            self.status.showMessage("✓ Image with markups copied to clipboard! (Ready to paste in Teams/Slack/Outlook)")
            self.btn_copy.setText("✓ Copied!")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 Copy Image"))
        else:
            QMessageBox.warning(self, "Clipboard Error", "Failed to copy image to clipboard.")

    def save_as_action(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Annotated Screenshot",
            os.path.expanduser("~/Pictures/vox_screenshot.png"),
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg)",
        )
        if file_path:
            composite = self.get_composite_image()
            if composite.save(file_path):
                self._current_file_path = Path(file_path)
                self.status.showMessage(f"Saved: {Path(file_path).name}")
            else:
                QMessageBox.warning(self, "Save Failed", "Could not save image to selected location.")

    # ------------------------------------------------------------------
    # Keyboard Shortcuts
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_C:
                self.copy_image_action()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_S:
                self.save_as_action()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Z:
                self._undo_action()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Y:
                self._redo_action()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Plus or event.key() == Qt.Key.Key_Equal:
                self._set_zoom(self.canvas.zoom * 1.25)
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Minus:
                self._set_zoom(self.canvas.zoom / 1.25)
                event.accept()
                return
            elif event.key() == Qt.Key.Key_0:
                self._zoom_fit()
                event.accept()
                return

        super().keyPressEvent(event)

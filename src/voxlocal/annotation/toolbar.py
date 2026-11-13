"""
Floating Glassmorphic Annotation Toolbar for VOX Screen Recording & Markup Studio.
Features recognizable tool icons, quick color/width selectors, interact mode (click-through),
and automated Windows capture exclusion (WDA_EXCLUDEFROMCAPTURE) so it never taints recordings.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger

logger = get_logger("annotation.toolbar")


class DragGripWidget(QWidget):
    """Visual drag handle with subtle dotted grip texture."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 28)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Drag to move toolbar anywhere on screen")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(148, 163, 184, 160))  # Slate 400

        # Draw 2 columns of 3 grip dots
        for col in (3, 9):
            for row in (6, 14, 22):
                painter.drawEllipse(col, row, 2.5, 2.5)


class ToolButton(QPushButton):
    """Sleek icon tool button with custom hover, checked state, and tooltips."""
    def __init__(self, text: str = "", tooltip: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #E2E8F0;
                font-size: 14px;
                border-radius: 6px;
                border: 1px solid transparent;
                padding: 0px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            QPushButton:checked {
                background: rgba(56, 189, 248, 0.25);
                border: 1.5px solid #38BDF8;
                color: #38BDF8;
                font-weight: bold;
            }
            QPushButton:disabled {
                color: #475569;
                background: transparent;
                border: none;
            }
        """)


class AnnotationToolbar(QWidget):
    """
    Floating annotation pill widget that hovers on top of screen recordings
    or static screenshot markup windows.
    """
    tool_changed = pyqtSignal(str)              # (tool_name)
    color_changed = pyqtSignal(str)             # (hex_color)
    stroke_width_changed = pyqtSignal(int)      # (width_px)
    interact_mode_toggled = pyqtSignal(bool)    # (is_click_through)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    visibility_toggled = pyqtSignal(bool)       # (is_visible)
    screenshot_requested = pyqtSignal(str)      # ("region" | "window" | "fullscreen")
    closed = pyqtSignal()

    PRESET_COLORS = [
        ("#EF4444", "Crimson Red"),
        ("#F59E0B", "Amber Orange"),
        ("#10B981", "Emerald Green"),
        ("#06B6D4", "Electric Cyan"),
        ("#3B82F6", "Sapphire Blue"),
        ("#8B5CF6", "Royal Purple"),
        ("#FFFFFF", "Crisp White"),
        ("#000000", "Deep Black"),
    ]

    STROKE_WIDTHS = [
        (2, "Thin (2px)"),
        (4, "Normal (4px)"),
        (8, "Thick (8px)"),
        (14, "Extra Bold (14px)"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._active_tool = "pen"
        self._active_color = "#EF4444"
        self._active_width = 4
        self._is_interact_mode = False
        self._is_annotations_visible = True
        self._drag_pos: Optional[QPoint] = None

        self._buttons: Dict[str, ToolButton] = {}
        self._setup_ui()
        self._exclude_from_screen_capture()

    def _setup_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.setSpacing(0)

        # Background pill container frame
        container = QFrame(self)
        container.setObjectName("toolbarContainer")
        container.setStyleSheet("""
            QFrame#toolbarContainer {
                background: rgba(15, 23, 42, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 20px;
            }
        """)
        c_layout = QHBoxLayout(container)
        c_layout.setContentsMargins(8, 4, 8, 4)
        c_layout.setSpacing(4)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 1. Drag Grip Handle
        self.drag_grip = DragGripWidget(container)
        c_layout.addWidget(self.drag_grip)
        c_layout.addSpacing(3)

        # 2. Interact Mode Toggle (Click-through to apps beneath)
        self.btn_interact = ToolButton("🖱️", "Interact Mode (Ctrl+Space): Click through to desktop apps", container)
        self.btn_interact.setCheckable(True)
        self.btn_interact.clicked.connect(self._toggle_interact_mode)
        c_layout.addWidget(self.btn_interact)

        c_layout.addWidget(self._create_divider())

        # 3. Exclusive Drawing Tools Group
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        tool_defs = [
            ("pointer", "👆", "Pointer / Laser Dot (L)"),
            ("pen", "✏️", "Pen: Freehand Drawing (P)"),
            ("highlighter", "🖍️", "Highlighter: Semi-transparent emphasis (H)"),
            ("arrow", "↗️", "Arrow: Directional pointing (A)"),
            ("rect", "🔲", "Rectangle: Box / Frame (R)"),
            ("ellipse", "⭕", "Ellipse / Circle (O)"),
            ("line", "➖", "Line: Straight segment (D)"),
            ("text", "🔤", "Text: Label or callout (T)"),
            ("step", "🔢", "Numbered Steps: Auto-incrementing 1, 2, 3... (S)"),
            ("spotlight", "🔦", "Spotlight: Focus area cutout (F)"),
            ("blur", "💧", "Blur: Physical pixelation & redaction (B)"),
            ("eraser", "🧹", "Eraser: Remove clicked markup (E)"),
        ]

        for name, icon, tip in tool_defs:
            btn = ToolButton(icon, tip, container)
            btn.setCheckable(True)
            if name == "pen":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, n=name: self._select_tool(n))
            self.tool_group.addButton(btn)
            self._buttons[name] = btn
            c_layout.addWidget(btn)

        c_layout.addWidget(self._create_divider())

        # 4. Color Swatch & Width Picker Trigger Button
        self.btn_palette = ToolButton("🎨", "Color & Stroke Width", container)
        self.btn_palette.clicked.connect(self._open_style_palette)
        self._update_palette_swatch_indicator()
        c_layout.addWidget(self.btn_palette)

        c_layout.addWidget(self._create_divider())

        # 5. Undo & Redo Actions
        self.btn_undo = ToolButton("↩️", "Undo last drawing (Ctrl+Z)", container)
        self.btn_undo.clicked.connect(self.undo_requested.emit)
        self.btn_undo.setEnabled(False)
        c_layout.addWidget(self.btn_undo)

        self.btn_redo = ToolButton("↪️", "Redo last drawing (Ctrl+Y)", container)
        self.btn_redo.clicked.connect(self.redo_requested.emit)
        self.btn_redo.setEnabled(False)
        c_layout.addWidget(self.btn_redo)

        # 6. Clear All
        self.btn_clear = ToolButton("🗑️", "Clear all annotations (C)", container)
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        c_layout.addWidget(self.btn_clear)

        # 7. Visibility Toggle (Eye)
        self.btn_vis = ToolButton("👁️", "Toggle Annotations Visibility", container)
        self.btn_vis.setCheckable(True)
        self.btn_vis.setChecked(True)
        self.btn_vis.clicked.connect(self._toggle_visibility)
        c_layout.addWidget(self.btn_vis)

        c_layout.addWidget(self._create_divider())

        # 8. Screenshot Quick Snip
        self.btn_snip = ToolButton("📸", "Take Screenshot & Markup Studio", container)
        self.btn_snip.clicked.connect(self._open_screenshot_menu)
        c_layout.addWidget(self.btn_snip)

        # 9. Close / Minimize Toolbar
        self.btn_close = ToolButton("✕", "Close Toolbar", container)
        self.btn_close.clicked.connect(self.close)
        c_layout.addWidget(self.btn_close)

        main_layout.addWidget(container)
        self.adjustSize()

    @staticmethod
    def _create_divider() -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFixedSize(1, 20)
        div.setStyleSheet("background: rgba(255, 255, 255, 0.15); border: none;")
        return div

    # ------------------------------------------------------------------
    # Windows Capture Exclusion (WDA_EXCLUDEFROMCAPTURE = 0x00000011)
    # ------------------------------------------------------------------
    def _exclude_from_screen_capture(self) -> None:
        """
        Instructs Windows DWM to omit this toolbar from all GDI and Desktop Duplication
        screen recordings, ensuring the toolbar never appears in recorded videos.
        """
        try:
            hwnd = int(self.winId())
            if hwnd:
                user32 = ctypes.windll.user32
                user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                user32.SetWindowDisplayAffinity.restype = ctypes.c_int
                WDA_EXCLUDEFROMCAPTURE = 0x00000011
                res = user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE))
                if res != 0:
                    logger.debug("AnnotationToolbar excluded from screen capture (WDA_EXCLUDEFROMCAPTURE).")
                else:
                    logger.debug("SetWindowDisplayAffinity returned 0 (not supported or permissions).")
        except Exception as exc:
            logger.debug("Could not set display affinity on toolbar: %s", exc)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._exclude_from_screen_capture()

    # ------------------------------------------------------------------
    # Tool Selection & States
    # ------------------------------------------------------------------
    def _select_tool(self, tool_name: str) -> None:
        self._active_tool = tool_name
        if self._is_interact_mode:
            self._is_interact_mode = False
            self.btn_interact.setChecked(False)
            self.interact_mode_toggled.emit(False)
        self.tool_changed.emit(tool_name)

    def set_tool(self, tool_name: str) -> None:
        if tool_name in self._buttons:
            self._buttons[tool_name].setChecked(True)
            self._active_tool = tool_name
            self.tool_changed.emit(tool_name)

    def _toggle_interact_mode(self, checked: bool) -> None:
        self._is_interact_mode = checked
        if checked:
            # Deselect drawing tools visually
            btn = self.tool_group.checkedButton()
            if btn:
                self.tool_group.setExclusive(False)
                btn.setChecked(False)
                self.tool_group.setExclusive(True)
        else:
            # Re-select active tool
            if self._active_tool in self._buttons:
                self._buttons[self._active_tool].setChecked(True)
        self.interact_mode_toggled.emit(checked)

    def _toggle_visibility(self, checked: bool) -> None:
        self._is_annotations_visible = checked
        self.btn_vis.setText("👁️" if checked else "🙈")
        self.visibility_toggled.emit(checked)

    def update_history_state(self, can_undo: bool, can_redo: bool) -> None:
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)

    # ------------------------------------------------------------------
    # Style Popover (Color Swatches & Stroke Width)
    # ------------------------------------------------------------------
    def _open_style_palette(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #0F172A;
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 10px;
                padding: 8px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: rgba(56, 189, 248, 0.20);
                color: #38BDF8;
            }
        """)

        # Color Swatches Header
        col_hdr = QAction("🎨 SELECT COLOR", menu)
        col_hdr.setEnabled(False)
        menu.addAction(col_hdr)

        for hex_code, name in self.PRESET_COLORS:
            indicator = "✓ " if hex_code.lower() == self._active_color.lower() else "  "
            act = QAction(f"{indicator}■ {name} ({hex_code})", menu)
            act.triggered.connect(lambda _, c=hex_code: self._set_color(c))
            menu.addAction(act)

        menu.addSeparator()

        # Stroke Widths Header
        w_hdr = QAction("📏 STROKE WIDTH", menu)
        w_hdr.setEnabled(False)
        menu.addAction(w_hdr)

        for w_px, name in self.STROKE_WIDTHS:
            indicator = "✓ " if w_px == self._active_width else "  "
            act = QAction(f"{indicator}{name}", menu)
            act.triggered.connect(lambda _, w=w_px: self._set_stroke_width(w))
            menu.addAction(act)

        btn_pos = self.btn_palette.mapToGlobal(QPoint(0, self.btn_palette.height() + 4))
        menu.exec(btn_pos)

    def _set_color(self, hex_code: str) -> None:
        self._active_color = hex_code
        self._update_palette_swatch_indicator()
        self.color_changed.emit(hex_code)

    def _set_stroke_width(self, width_px: int) -> None:
        self._active_width = width_px
        self.stroke_width_changed.emit(width_px)

    def _update_palette_swatch_indicator(self) -> None:
        self.btn_palette.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {self._active_color};
                font-size: 15px;
                border-radius: 6px;
                border: 1.5px solid {self._active_color};
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.15);
            }}
        """)

    # ------------------------------------------------------------------
    # Screenshot Quick Trigger Menu
    # ------------------------------------------------------------------
    def _open_screenshot_menu(self) -> None:
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

        act_region = QAction("✂️ Snip Region (Drag rectangle)", menu)
        act_region.triggered.connect(lambda: self.screenshot_requested.emit("region"))
        menu.addAction(act_region)

        act_win = QAction("🪟 Capture Active Window", menu)
        act_win.triggered.connect(lambda: self.screenshot_requested.emit("window"))
        menu.addAction(act_win)

        act_full = QAction("🖥️ Capture Full Display", menu)
        act_full.triggered.connect(lambda: self.screenshot_requested.emit("fullscreen"))
        menu.addAction(act_full)

        pos = self.btn_snip.mapToGlobal(QPoint(0, self.btn_snip.height() + 4))
        menu.exec(pos)

    # ------------------------------------------------------------------
    # Dragging Support
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        event.accept()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

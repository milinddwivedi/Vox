"""
High-Performance Annotation Engine for VOX Screen Recording & Markup Studio.
Unified vector model supporting freehand pen, highlighters, directional arrows,
shapes, auto-incrementing numbered tutorial steps, text badges, spotlight focus,
and physical pixelation/blur redaction for sensitive corporate workflows.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QImage,
)

from voxlocal.core.logger import get_logger

logger = get_logger("annotation.engine")


@dataclass
class AnnotationItem:
    """Represents a single vector or effect annotation element."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    kind: str = "pen"  # pointer, pen, highlighter, arrow, rect, ellipse, line, text, step, spotlight, blur
    points: List[Tuple[float, float]] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    color: str = "#EF4444"        # Hex code (e.g. #EF4444, #F59E0B, #10B981, #06B6D4)
    stroke_width: int = 4         # 2, 4, 8, 14
    opacity: float = 1.0          # 0.0 - 1.0
    text: str = ""                # Content for text labels
    step_number: int = 1          # Number for tutorial step markers (1, 2, 3...)
    fill: bool = False            # Whether shape has fill
    fill_color: Optional[str] = None
    font_size: int = 15           # Font size for text and step badges
    created_at: float = field(default_factory=time.time)
    _cached_image: Optional[Any] = field(default=None, repr=False, compare=False)

    def __deepcopy__(self, memo: Dict[Any, Any]) -> AnnotationItem:
        copied = AnnotationItem(
            id=self.id,
            kind=self.kind,
            points=[(float(p[0]), float(p[1])) for p in self.points],
            x=float(self.x),
            y=float(self.y),
            w=float(self.w),
            h=float(self.h),
            color=str(self.color),
            stroke_width=int(self.stroke_width),
            opacity=float(self.opacity),
            text=str(self.text),
            step_number=int(self.step_number),
            fill=bool(self.fill),
            fill_color=self.fill_color,
            font_size=int(self.font_size),
            created_at=float(self.created_at),
        )
        if self._cached_image is not None and hasattr(self._cached_image, "copy"):
            copied._cached_image = self._cached_image.copy()
        memo[id(self)] = copied
        return copied

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "points": self.points,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "color": self.color,
            "stroke_width": self.stroke_width,
            "opacity": self.opacity,
            "text": self.text,
            "step_number": self.step_number,
            "fill": self.fill,
            "fill_color": self.fill_color,
            "font_size": self.font_size,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AnnotationItem:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            kind=data.get("kind", "pen"),
            points=[(float(p[0]), float(p[1])) for p in data.get("points", [])],
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            w=float(data.get("w", 0.0)),
            h=float(data.get("h", 0.0)),
            color=data.get("color", "#EF4444"),
            stroke_width=int(data.get("stroke_width", 4)),
            opacity=float(data.get("opacity", 1.0)),
            text=data.get("text", ""),
            step_number=int(data.get("step_number", 1)),
            fill=bool(data.get("fill", False)),
            fill_color=data.get("fill_color"),
            font_size=int(data.get("font_size", 15)),
            created_at=float(data.get("created_at", time.time())),
        )


class AnnotationEngine:
    """
    Thread-safe annotation manager and high-performance renderer.
    Maintains vector items, undo/redo stacks, auto-incrementing step counters,
    and provides unified rendering across live UI overlays and background video frames.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: List[AnnotationItem] = []
        self._undo_stack: List[List[AnnotationItem]] = []
        self._redo_stack: List[List[AnnotationItem]] = []
        self._next_step_number: int = 1
        self._visible: bool = True

        # Laser pointer and spotlight live transient state
        self._laser_pos: Optional[Tuple[float, float]] = None
        self._laser_color: str = "#EF4444"
        self._active_spotlight: Optional[Tuple[float, float, float, float]] = None

    # ------------------------------------------------------------------
    # Item Management & History (Undo / Redo / Clear)
    # ------------------------------------------------------------------
    def _prerender_item_badge(self, item: AnnotationItem) -> None:
        """Renders text or step badges into an isolated small QImage on the main thread for thread-safe compositing."""
        try:
            if item.kind == "step":
                radius = max(14, int(item.font_size * 0.95))
                size = (radius + 4) * 2
                img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(0)
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                cx, cy = size / 2.0, size / 2.0
                # Drop shadow
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(0, 0, 0, 70)))
                p.drawEllipse(QPointF(cx + 1.5, cy + 2.0), radius, radius)
                # Colored circle
                qcol = QColor(item.color)
                p.setBrush(QBrush(qcol))
                p.setPen(QPen(QColor(255, 255, 255, 230), 2.0))
                p.drawEllipse(QPointF(cx, cy), radius, radius)
                # Number text
                p.setPen(QColor(255, 255, 255, 255))
                p.setFont(QFont("Segoe UI", item.font_size, QFont.Weight.Bold))
                text_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
                p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, str(item.step_number))
                p.end()
                item._cached_image = img
            elif item.kind == "text" and item.text.strip():
                font = QFont("Segoe UI", item.font_size, QFont.Weight.DemiBold)
                from PyQt6.QtGui import QFontMetrics
                fm = QFontMetrics(font)
                tw = max(30, fm.horizontalAdvance(item.text) + 20)
                th = max(20, fm.height() + 8)
                img = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(0)
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                badge_rect = QRectF(1, 1, tw - 2, th - 2)
                p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
                p.setBrush(QBrush(QColor(15, 23, 42, 220)))
                p.drawRoundedRect(badge_rect, 6.0, 6.0)
                p.setFont(font)
                p.setPen(QColor(item.color))
                p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, item.text)
                p.end()
                item._cached_image = img
        except Exception as exc:
            logger.debug("Badge prerender error: %s", exc)
            item._cached_image = None

    def add_item(self, item: AnnotationItem) -> None:
        self._prerender_item_badge(item)
        with self._lock:
            # Push current snapshot to undo stack
            self._undo_stack.append([copy.deepcopy(x) for x in self._items])
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
            self._redo_stack.clear()

            self._items.append(item)
            if item.kind == "step":
                self._next_step_number = max(self._next_step_number, item.step_number + 1)

    def remove_item(self, item_id: str) -> bool:
        with self._lock:
            idx = next((i for i, item in enumerate(self._items) if item.id == item_id), -1)
            if idx != -1:
                self._undo_stack.append([copy.deepcopy(x) for x in self._items])
                self._redo_stack.clear()
                self._items.pop(idx)
                return True
            return False

    def undo(self) -> bool:
        with self._lock:
            if not self._undo_stack:
                return False
            self._redo_stack.append([copy.deepcopy(x) for x in self._items])
            self._items = self._undo_stack.pop()
            # Recalculate next step number based on remaining step items
            step_nums = [it.step_number for it in self._items if it.kind == "step"]
            self._next_step_number = max(step_nums) + 1 if step_nums else 1
            return True

    def redo(self) -> bool:
        with self._lock:
            if not self._redo_stack:
                return False
            self._undo_stack.append([copy.deepcopy(x) for x in self._items])
            self._items = self._redo_stack.pop()
            step_nums = [it.step_number for it in self._items if it.kind == "step"]
            self._next_step_number = max(step_nums) + 1 if step_nums else 1
            return True

    def can_undo(self) -> bool:
        with self._lock:
            return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        with self._lock:
            return len(self._redo_stack) > 0

    def clear(self) -> bool:
        with self._lock:
            if not self._items and not self._active_spotlight:
                return False
            self._undo_stack.append([copy.deepcopy(x) for x in self._items])
            self._redo_stack.clear()
            self._items.clear()
            self._active_spotlight = None
            self._next_step_number = 1
            return True

    @property
    def items_count(self) -> int:
        with self._lock:
            return len(self._items)

    def get_next_step_number(self) -> int:
        with self._lock:
            return self._next_step_number

    def reset_step_counter(self, start_val: int = 1) -> None:
        with self._lock:
            self._next_step_number = max(1, start_val)

    def set_visible(self, visible: bool) -> None:
        with self._lock:
            self._visible = visible

    def is_visible(self) -> bool:
        with self._lock:
            return self._visible

    # ------------------------------------------------------------------
    # Live Pointer & Spotlight Controls
    # ------------------------------------------------------------------
    def set_laser(self, x: float, y: float, color: str = "#EF4444") -> None:
        with self._lock:
            self._laser_pos = (x, y)
            self._laser_color = color

    def clear_laser(self) -> None:
        with self._lock:
            self._laser_pos = None

    def set_spotlight(self, x: float, y: float, w: float, h: float) -> None:
        with self._lock:
            self._active_spotlight = (x, y, w, h)

    def clear_spotlight(self) -> None:
        with self._lock:
            self._active_spotlight = None

    def get_items_snapshot(self) -> List[AnnotationItem]:
        with self._lock:
            return [copy.deepcopy(item) for item in self._items]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "items": [it.to_dict() for it in self._items],
                "next_step_number": self._next_step_number,
                "visible": self._visible,
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._undo_stack.append([copy.deepcopy(x) for x in self._items])
            self._redo_stack.clear()
            self._items = [AnnotationItem.from_dict(d) for d in data.get("items", [])]
            for it in self._items:
                if it.kind in ("step", "text"):
                    self._prerender_item_badge(it)
            self._next_step_number = int(data.get("next_step_number", 1))
            self._visible = bool(data.get("visible", True))

    # ------------------------------------------------------------------
    # Unified QPainter Vector Rendering
    # ------------------------------------------------------------------
    def render_with_painter(
        self,
        painter: QPainter,
        canvas_width: int,
        canvas_height: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        is_interactive_overlay: bool = False,
    ) -> None:
        """
        Draws all active annotation items using QPainter with sub-pixel antialiasing.
        Used by both the on-screen live overlay window and image markup studio canvas.
        """
        with self._lock:
            if not self._visible:
                return
            items_to_render = [copy.deepcopy(item) for item in self._items]
            spotlight = self._active_spotlight
            laser = self._laser_pos
            laser_color = self._laser_color

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 1. Render Active Spotlight (dims background canvas with illuminated cutout)
        if spotlight is not None:
            sx, sy, sw, sh = spotlight
            sx = (sx - offset_x) * scale_x
            sy = (sy - offset_y) * scale_y
            sw *= scale_x
            sh *= scale_y

            # Full canvas path minus spotlight cutout
            full_path = QPainterPath()
            full_path.addRect(0, 0, canvas_width, canvas_height)

            spot_path = QPainterPath()
            spot_path.addEllipse(QRectF(sx, sy, sw, sh))
            scrim_path = full_path.subtracted(spot_path)

            painter.fillPath(scrim_path, QBrush(QColor(0, 0, 0, 170)))

            # Illuminated focus ring
            ring_pen = QPen(QColor(255, 255, 255, 220), 2.5)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(sx, sy, sw, sh))

        # 2. Render Annotation Items
        for item in items_to_render:
            self._draw_single_item(
                painter,
                item,
                scale_x,
                scale_y,
                offset_x,
                offset_y,
                is_interactive_overlay=is_interactive_overlay,
            )

        # 3. Render Laser Pointer Dot
        if laser is not None:
            lx = (laser[0] - offset_x) * scale_x
            ly = (laser[1] - offset_y) * scale_y
            # Outer glow
            painter.setPen(Qt.PenStyle.NoPen)
            qcol = QColor(laser_color)
            qcol.setAlpha(80)
            painter.setBrush(QBrush(qcol))
            painter.drawEllipse(QPointF(lx, ly), 16, 16)
            # Inner bright core
            qcol.setAlpha(255)
            painter.setBrush(QBrush(qcol))
            painter.drawEllipse(QPointF(lx, ly), 6, 6)
            # White highlight center
            painter.setBrush(QBrush(QColor(255, 255, 255, 240)))
            painter.drawEllipse(QPointF(lx, ly), 2, 2)

        painter.restore()

    def _draw_single_item(
        self,
        painter: QPainter,
        item: AnnotationItem,
        scale_x: float,
        scale_y: float,
        offset_x: float,
        offset_y: float,
        is_interactive_overlay: bool = False,
    ) -> None:
        qcol = QColor(item.color)
        alpha_int = int(max(0.0, min(1.0, item.opacity)) * 255)

        # --- PEN (Freehand Vector) ---
        if item.kind == "pen":
            if len(item.points) < 2:
                return
            qcol.setAlpha(alpha_int)
            pen = QPen(
                qcol,
                max(1.0, item.stroke_width * scale_x),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            p0 = item.points[0]
            path.moveTo((p0[0] - offset_x) * scale_x, (p0[1] - offset_y) * scale_y)
            for pt in item.points[1:]:
                path.lineTo((pt[0] - offset_x) * scale_x, (pt[1] - offset_y) * scale_y)
            painter.drawPath(path)

        # --- HIGHLIGHTER (Semi-Transparent Soft Stroke) ---
        elif item.kind == "highlighter":
            if len(item.points) < 2:
                return
            # Highlighters have characteristic translucent tint and thicker stroke
            hl_alpha = int(alpha_int * 0.38)
            qcol.setAlpha(hl_alpha)
            pen = QPen(
                qcol,
                max(6.0, (item.stroke_width * 3.5) * scale_x),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            p0 = item.points[0]
            path.moveTo((p0[0] - offset_x) * scale_x, (p0[1] - offset_y) * scale_y)
            for pt in item.points[1:]:
                path.lineTo((pt[0] - offset_x) * scale_x, (pt[1] - offset_y) * scale_y)
            painter.drawPath(path)

        # --- ARROW (Precision Directional Arrow) ---
        elif item.kind == "arrow":
            if len(item.points) < 2:
                return
            p_start = item.points[0]
            p_end = item.points[-1]
            x1 = (p_start[0] - offset_x) * scale_x
            y1 = (p_start[1] - offset_y) * scale_y
            x2 = (p_end[0] - offset_x) * scale_x
            y2 = (p_end[1] - offset_y) * scale_y

            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length < 4.0:
                return

            qcol.setAlpha(alpha_int)
            pen_width = max(2.0, item.stroke_width * scale_x)
            pen = QPen(
                qcol,
                pen_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # Draw shaft
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Draw arrowhead wings
            angle = math.atan2(dy, dx)
            head_len = max(14.0, pen_width * 3.8)
            wing_angle = math.pi / 6.5  # ~28 degrees

            h1_x = x2 - head_len * math.cos(angle - wing_angle)
            h1_y = y2 - head_len * math.sin(angle - wing_angle)
            h2_x = x2 - head_len * math.cos(angle + wing_angle)
            h2_y = y2 - head_len * math.sin(angle + wing_angle)

            head_path = QPainterPath()
            head_path.moveTo(x2, y2)
            head_path.lineTo(h1_x, h1_y)
            head_path.lineTo(h2_x, h2_y)
            head_path.closeSubpath()

            painter.setBrush(QBrush(qcol))
            painter.drawPath(head_path)

        # --- LINE (Straight Line) ---
        elif item.kind == "line":
            if len(item.points) < 2:
                return
            p_start = item.points[0]
            p_end = item.points[-1]
            x1 = (p_start[0] - offset_x) * scale_x
            y1 = (p_start[1] - offset_y) * scale_y
            x2 = (p_end[0] - offset_x) * scale_x
            y2 = (p_end[1] - offset_y) * scale_y

            qcol.setAlpha(alpha_int)
            pen = QPen(
                qcol,
                max(1.0, item.stroke_width * scale_x),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # --- RECTANGLE (Rounded Box) ---
        elif item.kind == "rect":
            rx = (item.x - offset_x) * scale_x
            ry = (item.y - offset_y) * scale_y
            rw = item.w * scale_x
            rh = item.h * scale_y
            rect = QRectF(rx, ry, rw, rh).normalized()

            qcol.setAlpha(alpha_int)
            pen = QPen(qcol, max(1.0, item.stroke_width * scale_x))
            painter.setPen(pen)

            if item.fill and item.fill_color:
                fcol = QColor(item.fill_color)
                fcol.setAlpha(int(alpha_int * 0.25))
                painter.setBrush(QBrush(fcol))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)

            painter.drawRoundedRect(rect, 6.0, 6.0)

        # --- ELLIPSE (Circle / Oval) ---
        elif item.kind == "ellipse":
            rx = (item.x - offset_x) * scale_x
            ry = (item.y - offset_y) * scale_y
            rw = item.w * scale_x
            rh = item.h * scale_y
            rect = QRectF(rx, ry, rw, rh).normalized()

            qcol.setAlpha(alpha_int)
            pen = QPen(qcol, max(1.0, item.stroke_width * scale_x))
            painter.setPen(pen)

            if item.fill and item.fill_color:
                fcol = QColor(item.fill_color)
                fcol.setAlpha(int(alpha_int * 0.25))
                painter.setBrush(QBrush(fcol))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)

            painter.drawEllipse(rect)

        # --- NUMBERED STEP (Tutorial Badges: 1, 2, 3...) ---
        elif item.kind == "step":
            cx = (item.x - offset_x) * scale_x
            cy = (item.y - offset_y) * scale_y
            radius = max(14.0, (item.font_size * 0.95) * scale_x)

            # Check for thread-safe pre-rendered badge first
            cached_img = getattr(item, "_cached_image", None)
            if cached_img is not None and not cached_img.isNull():
                dest_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
                painter.drawImage(dest_rect, cached_img)
            else:
                # Outer subtle drop shadow ring
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(0, 0, 0, 70)))
                painter.drawEllipse(QPointF(cx + 1.5, cy + 2.0), radius, radius)

                # Solid circular pill
                qcol.setAlpha(alpha_int)
                painter.setBrush(QBrush(qcol))
                pen = QPen(QColor(255, 255, 255, 230), 2.0)
                painter.setPen(pen)
                painter.drawEllipse(QPointF(cx, cy), radius, radius)

                # If on GUI thread, render text and populate cache for worker threads
                if is_interactive_overlay:
                    painter.setPen(QColor(255, 255, 255, 255))
                    font = QFont("Segoe UI", int(item.font_size * scale_x), QFont.Weight.Bold)
                    painter.setFont(font)
                    text_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
                    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, str(item.step_number))

        # --- TEXT LABEL (Legible Dark Pill + Colored Text) ---
        elif item.kind == "text":
            if not item.text.strip():
                return
            tx = (item.x - offset_x) * scale_x
            ty = (item.y - offset_y) * scale_y

            cached_img = getattr(item, "_cached_image", None)
            if cached_img is not None and not cached_img.isNull():
                dest_w = cached_img.width() * scale_x
                dest_h = cached_img.height() * scale_y
                dest_rect = QRectF(tx, ty, dest_w, dest_h)
                painter.drawImage(dest_rect, cached_img)
            elif is_interactive_overlay:
                font = QFont("Segoe UI", int(item.font_size * scale_x), QFont.Weight.DemiBold)
                painter.setFont(font)

                metrics = painter.fontMetrics()
                text_w = metrics.horizontalAdvance(item.text) + 16 * scale_x
                text_h = metrics.height() + 8 * scale_y
                badge_rect = QRectF(tx, ty, text_w, text_h)

                # Dark translucent pill background for guaranteed legibility over any desktop wallpaper
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
                painter.setBrush(QBrush(QColor(15, 23, 42, 220)))
                painter.drawRoundedRect(badge_rect, 6.0, 6.0)

                # Text string
                qcol.setAlpha(alpha_int)
                painter.setPen(qcol)
                painter.drawText(
                    badge_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    item.text,
                )

        # --- BLUR / REDACTION (Overlay Indicator) ---
        elif item.kind == "blur":
            rx = (item.x - offset_x) * scale_x
            ry = (item.y - offset_y) * scale_y
            rw = item.w * scale_x
            rh = item.h * scale_y
            rect = QRectF(rx, ry, rw, rh).normalized()

            if is_interactive_overlay:
                # In live transparent overlay mode, render frosted mosaic preview with badge
                painter.setPen(QPen(QColor(245, 158, 11, 220), 1.5, Qt.PenStyle.DashLine))
                painter.setBrush(QBrush(QColor(245, 158, 11, 40)))
                painter.drawRect(rect)

                # Small Privacy Redaction tag
                tag_rect = QRectF(rect.left(), rect.top() - 18, 90, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(245, 158, 11, 230)))
                painter.drawRoundedRect(tag_rect, 3, 3)
                painter.setPen(QColor(0, 0, 0))
                font = QFont("Segoe UI", 8, QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, "REDACTED")

    # ------------------------------------------------------------------
    # Physical Pixelation & Video BGRA Frame Compositing
    # ------------------------------------------------------------------
    def render_to_bgra(
        self,
        bgra_buf: np.ndarray,
        offset_x: int = 0,
        offset_y: int = 0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> None:
        """
        Composites all active annotations and physical pixelation directly onto
        the Win32 32-bit BGRA video frame buffer prior to PyAV encoding.
        Zero memory copying: paints directly into bgra_buf memory.
        """
        with self._lock:
            if not self._visible:
                return
            items_to_render = [copy.deepcopy(item) for item in self._items]
            spotlight = self._active_spotlight

        if not items_to_render and spotlight is None:
            return

        h, w = bgra_buf.shape[:2]

        # 1. Apply Physical Pixelation for all 'blur' items directly on numpy array
        for item in items_to_render:
            if item.kind == "blur":
                self._apply_numpy_pixelation(
                    bgra_buf,
                    item.x,
                    item.y,
                    item.w,
                    item.h,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    scale_x=scale_x,
                    scale_y=scale_y,
                    block_size=12,
                )

        # 2. Render vector annotations onto bgra_buf via in-place QPainter if non-blur items present
        has_vector_items = any(it.kind != "blur" for it in items_to_render) or (spotlight is not None)
        if has_vector_items:
            try:
                qimg = QImage(
                    bgra_buf.ctypes.data,
                    w,
                    h,
                    w * 4,
                    QImage.Format.Format_ARGB32,
                )
                painter = QPainter(qimg)
                try:
                    self.render_with_painter(
                        painter,
                        canvas_width=w,
                        canvas_height=h,
                        scale_x=scale_x,
                        scale_y=scale_y,
                        offset_x=float(offset_x),
                        offset_y=float(offset_y),
                        is_interactive_overlay=False,
                    )
                finally:
                    painter.end()
            except Exception as render_err:
                logger.debug("render_to_bgra vector render error: %s", render_err)

    @staticmethod
    def _apply_numpy_pixelation(
        buf: np.ndarray,
        x: float,
        y: float,
        w: float,
        h: float,
        offset_x: int = 0,
        offset_y: int = 0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        block_size: int = 12,
    ) -> None:
        """
        Downsamples and nearest-neighbor upsamples the underlying pixels,
        physically obliterating sensitive text/passwords in the recorded buffer.
        """
        img_h, img_w = buf.shape[:2]
        rx = int(round((x - offset_x) * scale_x))
        ry = int(round((y - offset_y) * scale_y))
        rw = int(round(w * scale_x))
        rh = int(round(h * scale_y))

        # Normalize negative dimensions
        if rw < 0:
            rx += rw
            rw = abs(rw)
        if rh < 0:
            ry += rh
            rh = abs(rh)

        x1 = max(0, min(img_w, rx))
        y1 = max(0, min(img_h, ry))
        x2 = max(0, min(img_w, rx + rw))
        y2 = max(0, min(img_h, ry + rh))

        box_w = x2 - x1
        box_h = y2 - y1
        if box_w <= 2 or box_h <= 2:
            return

        sub = buf[y1:y2, x1:x2]
        bs = max(4, block_size)
        # Downsample
        small = sub[::bs, ::bs]
        # Upsample nearest neighbor back
        upsampled = np.repeat(np.repeat(small, bs, axis=0), bs, axis=1)
        # Write back directly
        buf[y1:y2, x1:x2] = upsampled[:box_h, :box_w]

    # ------------------------------------------------------------------
    # Static Image Markup Rendering (for Screenshots)
    # ------------------------------------------------------------------
    def render_to_qimage(self, base_image: QImage) -> QImage:
        """
        Creates a new QImage with the base image, physical blur redactions,
        and all vector markups composited into a production-ready export.
        """
        composite = base_image.copy()
        if composite.format() != QImage.Format.Format_ARGB32:
            composite = composite.convertToFormat(QImage.Format.Format_ARGB32)

        w = composite.width()
        h = composite.height()

        with self._lock:
            items_to_render = [copy.deepcopy(item) for item in self._items]

        # 1. Apply physical pixelation for blur items
        ptr = composite.bits()
        ptr.setsize(h * w * 4)
        arr = np.frombuffer(ptr, np.uint8).reshape((h, w, 4))
        for item in items_to_render:
            if item.kind == "blur":
                self._apply_numpy_pixelation(
                    arr,
                    item.x,
                    item.y,
                    item.w,
                    item.h,
                    offset_x=0,
                    offset_y=0,
                    scale_x=1.0,
                    scale_y=1.0,
                    block_size=12,
                )

        # 2. Paint vector markups
        painter = QPainter(composite)
        try:
            self.render_with_painter(
                painter,
                canvas_width=w,
                canvas_height=h,
                scale_x=1.0,
                scale_y=1.0,
                offset_x=0.0,
                offset_y=0.0,
                is_interactive_overlay=False,
            )
        finally:
            painter.end()

        return composite

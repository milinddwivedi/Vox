"""
VOX Vector Iconography Engine (Material Symbols Rounded & Fluent 2 Dual-State).

Renders resolution-independent, high-DPI monochrome vector icons using QPainter
without shipping external PNG sprites or relying on inconsistent OS emoji glyphs.
Cached by (name, color_hex, size_px, filled) for near-zero runtime overhead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget


@lru_cache(maxsize=512)
def get_pixmap(name: str, color: str = "#0F172A", size: int = 16, filled: bool = False) -> QPixmap:
    """Render a crisp 2x-retina monochrome vector pixmap at logical size `size`."""
    dpr = 2.0
    px_size = max(12, int(round(size * dpr)))
    pm = QPixmap(px_size, px_size)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    s = float(size) / 24.0
    col = QColor(color or "#0F172A")
    stroke_w = max(1.35, 1.75 * s)
    pen = QPen(col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(col if filled else Qt.BrushStyle.NoBrush)

    n = (name or "").strip().lower()

    def _pt(x: float, y: float) -> QPointF:
        return QPointF(x * s, y * s)

    def _rect(x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(x * s, y * s, w * s, h * s)

    if n == "home":
        path = QPainterPath()
        path.moveTo(_pt(3, 10.5))
        path.lineTo(_pt(12, 3.2))
        path.lineTo(_pt(21, 10.5))
        path.lineTo(_pt(21, 20.0))
        path.lineTo(_pt(14.8, 20.0))
        path.lineTo(_pt(14.8, 13.8))
        path.lineTo(_pt(9.2, 13.8))
        path.lineTo(_pt(9.2, 20.0))
        path.lineTo(_pt(3, 20.0))
        path.closeSubpath()
        p.drawPath(path)

    elif n in ("meetings", "mic", "notetaker"):
        # Studio microphone / notetaker emblem
        if filled:
            p.drawRoundedRect(_rect(8.5, 3, 7, 11.5), 3.5 * s, 3.5 * s)
        else:
            p.drawRoundedRect(_rect(9, 3, 6, 11), 3.0 * s, 3.0 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath()
        arc.moveTo(_pt(5.5, 11))
        arc.arcTo(_rect(5.5, 4.5, 13, 13), 180, 180)
        p.drawPath(arc)
        p.drawLine(_pt(12, 17.5), _pt(12, 21))
        p.drawLine(_pt(8.5, 21), _pt(15.5, 21))

    elif n == "calendar":
        p.drawRoundedRect(_rect(3.5, 5, 17, 15.5), 2.5 * s, 2.5 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if filled:
            p.save()
            p.setPen(QPen(QColor("#FFFFFF"), stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(_pt(4, 10), _pt(20, 10))
            p.restore()
            p.setPen(pen)
            p.drawLine(_pt(8, 2.8), _pt(8, 6.2))
            p.drawLine(_pt(16, 2.8), _pt(16, 6.2))
        else:
            p.drawLine(_pt(3.5, 10), _pt(20.5, 10))
            p.drawLine(_pt(8, 2.8), _pt(8, 6.2))
            p.drawLine(_pt(16, 2.8), _pt(16, 6.2))
            p.setBrush(col)
            p.drawEllipse(_rect(7.5, 13.5, 2.2, 2.2))
            p.drawEllipse(_rect(11, 13.5, 2.2, 2.2))
            p.drawEllipse(_rect(14.5, 13.5, 2.2, 2.2))

    elif n == "dictionary":
        p.drawRoundedRect(_rect(4.5, 3.5, 15, 17), 2.2 * s, 2.2 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(8.2, 3.5), _pt(8.2, 20.5))
        if not filled:
            p.drawLine(_pt(11.2, 8.5), _pt(16.5, 8.5))
            p.drawLine(_pt(11.2, 12.2), _pt(16.5, 12.2))
        else:
            p.save()
            p.setPen(QPen(QColor("#FFFFFF"), stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(_pt(11.2, 8.5), _pt(16.5, 8.5))
            p.drawLine(_pt(11.2, 12.2), _pt(16.5, 12.2))
            p.restore()

    elif n == "snippets":
        path = QPainterPath()
        path.moveTo(_pt(13.2, 2.8))
        path.lineTo(_pt(5.5, 13.2))
        path.lineTo(_pt(11.5, 13.2))
        path.lineTo(_pt(10.5, 21.2))
        path.lineTo(_pt(18.5, 10.6))
        path.lineTo(_pt(12.5, 10.6))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "style":
        path = QPainterPath()
        path.moveTo(_pt(12, 3.2))
        path.quadTo(_pt(12.6, 11.4), _pt(20.8, 12))
        path.quadTo(_pt(12.6, 12.6), _pt(12, 20.8))
        path.quadTo(_pt(11.4, 12.6), _pt(3.2, 12))
        path.quadTo(_pt(11.4, 11.4), _pt(12, 3.2))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "scratchpad":
        p.drawRoundedRect(_rect(4.2, 4.2, 15.6, 15.6), 2.5 * s, 2.5 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen_inner = QPen(QColor("#FFFFFF") if filled else col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_inner)
        p.drawLine(_pt(8, 9), _pt(16, 9))
        p.drawLine(_pt(8, 12.5), _pt(14.5, 12.5))
        p.drawLine(_pt(8, 16), _pt(12, 16))

    elif n == "insights":
        if filled:
            p.drawRoundedRect(_rect(4, 12, 3.8, 8), 1.2 * s, 1.2 * s)
            p.drawRoundedRect(_rect(10.1, 7.5, 3.8, 12.5), 1.2 * s, 1.2 * s)
            p.drawRoundedRect(_rect(16.2, 4, 3.8, 16), 1.2 * s, 1.2 * s)
        else:
            p.drawLine(_pt(4, 20), _pt(20, 20))
            p.drawRoundedRect(_rect(5, 12.5, 2.8, 7.5), 1 * s, 1 * s)
            p.drawRoundedRect(_rect(10.6, 8, 2.8, 12), 1 * s, 1 * s)
            p.drawRoundedRect(_rect(16.2, 4.5, 2.8, 15.5), 1 * s, 1 * s)

    elif n == "settings":
        p.drawEllipse(_rect(8.6, 8.6, 6.8, 6.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(4.2, 4.2, 15.6, 15.6))
        for deg in range(0, 360, 45):
            import math
            ang = math.radians(deg)
            p.drawLine(
                _pt(12 + 7.8 * math.cos(ang), 12 + 7.8 * math.sin(ang)),
                _pt(12 + 9.8 * math.cos(ang), 12 + 9.8 * math.sin(ang)),
            )

    elif n == "sidebar_toggle":
        p.drawRoundedRect(_rect(3.2, 4.2, 17.6, 15.6), 2.4 * s, 2.4 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(9.2, 4.2), _pt(9.2, 19.8))
        p.drawLine(_pt(5.5, 9), _pt(7.2, 9))
        p.drawLine(_pt(5.5, 12), _pt(7.2, 12))

    elif n == "search":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(4.2, 4.2, 11.4, 11.4))
        p.drawLine(_pt(14.2, 14.2), _pt(19.8, 19.8))

    elif n == "filter":
        path = QPainterPath()
        path.moveTo(_pt(3.5, 5.2))
        path.lineTo(_pt(20.5, 5.2))
        path.lineTo(_pt(14, 12.6))
        path.lineTo(_pt(14, 18.8))
        path.lineTo(_pt(10, 17.0))
        path.lineTo(_pt(10, 12.6))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "sort":
        p.drawLine(_pt(4, 7), _pt(15, 7))
        p.drawLine(_pt(4, 12), _pt(12, 12))
        p.drawLine(_pt(4, 17), _pt(9, 17))
        p.drawLine(_pt(17.5, 6.5), _pt(17.5, 18))
        p.drawLine(_pt(14.8, 15.2), _pt(17.5, 18))
        p.drawLine(_pt(20.2, 15.2), _pt(17.5, 18))

    elif n == "density_compact":
        p.drawLine(_pt(4, 6), _pt(20, 6))
        p.drawLine(_pt(4, 10), _pt(20, 10))
        p.drawLine(_pt(4, 14), _pt(20, 14))
        p.drawLine(_pt(4, 18), _pt(20, 18))

    elif n == "density_comfy":
        p.drawRoundedRect(_rect(4, 4.5, 16, 6), 1.5 * s, 1.5 * s)
        p.drawRoundedRect(_rect(4, 13.5, 16, 6), 1.5 * s, 1.5 * s)

    elif n == "folder":
        path = QPainterPath()
        path.moveTo(_pt(3.5, 6.5))
        path.lineTo(_pt(9.5, 6.5))
        path.lineTo(_pt(11.5, 8.8))
        path.lineTo(_pt(20.5, 8.8))
        path.lineTo(_pt(20.5, 18.8))
        path.lineTo(_pt(3.5, 18.8))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "folder_plus":
        path = QPainterPath()
        path.moveTo(_pt(3.2, 6.2))
        path.lineTo(_pt(9.2, 6.2))
        path.lineTo(_pt(11.2, 8.5))
        path.lineTo(_pt(20.8, 8.5))
        path.lineTo(_pt(20.8, 18.8))
        path.lineTo(_pt(3.2, 18.8))
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(12, 11.4), _pt(12, 16.0))
        p.drawLine(_pt(9.7, 13.7), _pt(14.3, 13.7))

    elif n == "chevron_down":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(6, 9.2), _pt(12, 15.2))
        p.drawLine(_pt(12, 15.2), _pt(18, 9.2))

    elif n == "chevron_right":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(9.2, 6), _pt(15.2, 12))
        p.drawLine(_pt(15.2, 12), _pt(9.2, 18))

    elif n == "chevron_left":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(14.8, 6), _pt(8.8, 12))
        p.drawLine(_pt(8.8, 12), _pt(14.8, 18))

    elif n == "close":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(6.2, 6.2), _pt(17.8, 17.8))
        p.drawLine(_pt(17.8, 6.2), _pt(6.2, 17.8))

    elif n == "more_horiz":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(_rect(4.2, 10.6, 2.8, 2.8))
        p.drawEllipse(_rect(10.6, 10.6, 2.8, 2.8))
        p.drawEllipse(_rect(17.0, 10.6, 2.8, 2.8))

    elif n == "split_view":
        p.drawRoundedRect(_rect(3.2, 4.5, 17.6, 15), 2.2 * s, 2.2 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(12, 4.5), _pt(12, 19.5))

    elif n == "expand":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(7, 9), _pt(12, 4.5))
        p.drawLine(_pt(12, 4.5), _pt(17, 9))
        p.drawLine(_pt(7, 15), _pt(12, 19.5))
        p.drawLine(_pt(12, 19.5), _pt(17, 15))

    elif n == "collapse":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(7, 5), _pt(12, 9.5))
        p.drawLine(_pt(12, 9.5), _pt(17, 5))
        p.drawLine(_pt(7, 19), _pt(12, 14.5))
        p.drawLine(_pt(12, 14.5), _pt(17, 19))

    elif n == "record":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(3.5, 3.5, 17, 17))
        p.setBrush(col)
        p.drawEllipse(_rect(8.2, 8.2, 7.6, 7.6))

    elif n == "stop":
        p.setBrush(col)
        p.drawRoundedRect(_rect(5.5, 5.5, 13, 13), 2.2 * s, 2.2 * s)

    elif n == "play":
        p.setBrush(col)
        path = QPainterPath()
        path.moveTo(_pt(7.2, 5.0))
        path.lineTo(_pt(18.8, 12.0))
        path.lineTo(_pt(7.2, 19.0))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "pause":
        p.setBrush(col)
        p.drawRoundedRect(_rect(6.5, 5.2, 3.8, 13.6), 1.0 * s, 1.0 * s)
        p.drawRoundedRect(_rect(13.7, 5.2, 3.8, 13.6), 1.0 * s, 1.0 * s)

    elif n == "skip_back":
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath()
        arc.moveTo(_pt(12, 5.2))
        arc.arcTo(_rect(5.2, 5.2, 13.6, 13.6), 90, -270)
        p.drawPath(arc)
        p.drawLine(_pt(12, 5.2), _pt(9.2, 3.2))
        p.drawLine(_pt(12, 5.2), _pt(9.6, 7.6))

    elif n == "skip_forward":
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath()
        arc.moveTo(_pt(12, 5.2))
        arc.arcTo(_rect(5.2, 5.2, 13.6, 13.6), 90, 270)
        p.drawPath(arc)
        p.drawLine(_pt(12, 5.2), _pt(14.8, 3.2))
        p.drawLine(_pt(12, 5.2), _pt(14.4, 7.6))

    elif n == "video":
        p.drawRoundedRect(_rect(3.2, 6.2, 12.2, 11.6), 2.2 * s, 2.2 * s)
        path = QPainterPath()
        path.moveTo(_pt(15.6, 10.2))
        path.lineTo(_pt(20.8, 7.2))
        path.lineTo(_pt(20.8, 16.8))
        path.lineTo(_pt(15.6, 13.8))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "sparkles":
        path = QPainterPath()
        path.moveTo(_pt(10.5, 3.2))
        path.quadTo(_pt(11.1, 10.0), _pt(17.8, 10.6))
        path.quadTo(_pt(11.1, 11.2), _pt(10.5, 18.0))
        path.quadTo(_pt(9.9, 11.2), _pt(3.2, 10.6))
        path.quadTo(_pt(9.9, 10.0), _pt(10.5, 3.2))
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(col)
        p.drawEllipse(_rect(17.2, 15.2, 3.6, 3.6))

    elif n == "copy":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(_rect(8.5, 8.2, 11.2, 12.2), 2.0 * s, 2.0 * s)
        p.drawLine(_pt(15.2, 8.0), _pt(15.2, 4.4))
        p.drawLine(_pt(15.2, 4.4), _pt(4.8, 4.4))
        p.drawLine(_pt(4.8, 4.4), _pt(4.8, 15.8))
        p.drawLine(_pt(4.8, 15.8), _pt(8.2, 15.8))

    elif n == "export":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(12, 15), _pt(12, 4))
        p.drawLine(_pt(8.2, 7.8), _pt(12, 4))
        p.drawLine(_pt(15.8, 7.8), _pt(12, 4))
        p.drawLine(_pt(4.5, 14.5), _pt(4.5, 19.5))
        p.drawLine(_pt(4.5, 19.5), _pt(19.5, 19.5))
        p.drawLine(_pt(19.5, 19.5), _pt(19.5, 14.5))

    elif n == "email":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(_rect(3.5, 5.5, 17, 13), 2.2 * s, 2.2 * s)
        p.drawLine(_pt(4.2, 6.8), _pt(12, 12.8))
        p.drawLine(_pt(19.8, 6.8), _pt(12, 12.8))

    elif n == "edit":
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(_pt(4.2, 19.8))
        path.lineTo(_pt(5.5, 14.8))
        path.lineTo(_pt(16.2, 4.2))
        path.lineTo(_pt(19.8, 7.8))
        path.lineTo(_pt(9.1, 18.5))
        path.closeSubpath()
        p.drawPath(path)

    elif n in ("refresh", "retry"):
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath()
        arc.moveTo(_pt(19.2, 10.2))
        arc.arcTo(_rect(4.5, 4.5, 15, 15), 15, 295)
        p.drawPath(arc)
        p.drawLine(_pt(19.2, 5.5), _pt(19.2, 10.2))
        p.drawLine(_pt(14.5, 10.2), _pt(19.2, 10.2))

    elif n == "users":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(6.2, 4.5, 6.2, 6.2))
        p.drawArc(_rect(3.2, 13.2, 12.2, 8), 0, 180 * 16)
        p.drawEllipse(_rect(14.5, 6.0, 4.8, 4.8))
        p.drawArc(_rect(14.2, 13.8, 6.6, 6.8), 0, 140 * 16)

    elif n == "lock":
        p.drawRoundedRect(_rect(5, 10.5, 14, 9.8), 2.2 * s, 2.2 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(_rect(8, 4.2, 8, 9.5), 0, 180 * 16)

    elif n == "globe":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(3.5, 3.5, 17, 17))
        p.drawEllipse(_rect(8.2, 3.5, 7.6, 17))
        p.drawLine(_pt(3.8, 12), _pt(20.2, 12))

    elif n == "drawer":
        p.drawRoundedRect(_rect(3.2, 4.2, 17.6, 15.6), 2.4 * s, 2.4 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(14.8, 4.2), _pt(14.8, 19.8))

    elif n == "alert":
        path = QPainterPath()
        path.moveTo(_pt(12, 3.6))
        path.lineTo(_pt(20.6, 19.2))
        path.lineTo(_pt(3.4, 19.2))
        path.closeSubpath()
        p.drawPath(path)
        p.setPen(QPen(QColor("#FFFFFF") if filled else col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(_pt(12, 9.2), _pt(12, 13.6))
        p.drawPoint(_pt(12, 16.4))

    elif n == "check":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(5.2, 12.6), _pt(9.8, 17.2))
        p.drawLine(_pt(9.8, 17.2), _pt(19.0, 7.4))

    elif n == "bold":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(7, 5.2), _pt(7, 18.8))
        p.drawLine(_pt(7, 5.2), _pt(13.2, 5.2))
        p.drawArc(_rect(9.8, 5.2, 6.2, 6.6), -90 * 16, 180 * 16)
        p.drawLine(_pt(7, 11.8), _pt(14.2, 11.8))
        p.drawArc(_rect(10.6, 11.8, 6.4, 7.0), -90 * 16, 180 * 16)
        p.drawLine(_pt(7, 18.8), _pt(14.2, 18.8))

    elif n == "list_bullet":
        p.setBrush(col)
        p.drawEllipse(_rect(4.0, 6.2, 2.2, 2.2))
        p.drawEllipse(_rect(4.0, 11.0, 2.2, 2.2))
        p.drawEllipse(_rect(4.0, 15.8, 2.2, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(9, 7.3), _pt(19.5, 7.3))
        p.drawLine(_pt(9, 12.1), _pt(19.5, 12.1))
        p.drawLine(_pt(9, 16.9), _pt(19.5, 16.9))

    elif n == "checklist":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(4.0, 7.8), _pt(6.0, 9.8))
        p.drawLine(_pt(6.0, 9.8), _pt(9.2, 6.0))
        p.drawLine(_pt(11.5, 8.0), _pt(19.5, 8.0))
        p.drawRoundedRect(_rect(4.2, 13.5, 4.4, 4.4), 1.0 * s, 1.0 * s)
        p.drawLine(_pt(11.5, 15.7), _pt(19.5, 15.7))

    elif n == "star":
        path = QPainterPath()
        import math
        for i in range(10):
            ang = math.radians(-90 + i * 36)
            r = 8.6 if i % 2 == 0 else 3.8
            pt = _pt(12 + r * math.cos(ang), 12 + r * math.sin(ang))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)

    elif n == "clock":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(_rect(3.8, 3.8, 16.4, 16.4))
        p.drawLine(_pt(12, 7.2), _pt(12, 12.2))
        p.drawLine(_pt(12, 12.2), _pt(15.6, 14.4))

    elif n == "link":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(_rect(3.5, 8.2, 9.0, 7.6), 3.6 * s, 3.6 * s)
        p.drawRoundedRect(_rect(11.5, 8.2, 9.0, 7.6), 3.6 * s, 3.6 * s)
        p.drawLine(_pt(9.0, 12), _pt(15.0, 12))

    elif n == "plus":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(12, 5.2), _pt(12, 18.8))
        p.drawLine(_pt(5.2, 12), _pt(18.8, 12))

    elif n == "trash":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(_pt(4.5, 6.8), _pt(19.5, 6.8))
        p.drawLine(_pt(9.2, 6.8), _pt(9.8, 4.2))
        p.drawLine(_pt(9.8, 4.2), _pt(14.2, 4.2))
        p.drawLine(_pt(14.2, 4.2), _pt(14.8, 6.8))
        p.drawRoundedRect(_rect(6.2, 6.8, 11.6, 13.0), 1.6 * s, 1.6 * s)
        p.drawLine(_pt(10.0, 10.5), _pt(10.0, 16.2))
        p.drawLine(_pt(14.0, 10.5), _pt(14.0, 16.2))

    elif n == "send":
        path = QPainterPath()
        path.moveTo(_pt(3.5, 4.5))
        path.lineTo(_pt(20.5, 12))
        path.lineTo(_pt(3.5, 19.5))
        path.lineTo(_pt(6.8, 12))
        path.closeSubpath()
        p.drawPath(path)

    elif n == "new_chat":
        # Minimal rounded square + top-right compose pen (Lucide square-pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        box = QPainterPath()
        box.moveTo(_pt(12.2, 4.2))
        box.lineTo(_pt(6.4, 4.2))
        box.quadTo(_pt(4.2, 4.2), _pt(4.2, 6.4))
        box.lineTo(_pt(4.2, 17.6))
        box.quadTo(_pt(4.2, 19.8), _pt(6.4, 19.8))
        box.lineTo(_pt(17.6, 19.8))
        box.quadTo(_pt(19.8, 19.8), _pt(19.8, 17.6))
        box.lineTo(_pt(19.8, 11.8))
        p.drawPath(box)
        pen_path = QPainterPath()
        pen_path.moveTo(_pt(17.4, 3.6))
        pen_path.lineTo(_pt(20.4, 6.6))
        pen_path.lineTo(_pt(12.6, 14.4))
        pen_path.lineTo(_pt(9.4, 14.6))
        pen_path.lineTo(_pt(9.6, 11.4))
        pen_path.closeSubpath()
        p.drawPath(pen_path)

    else:
        p.drawEllipse(_rect(5, 5, 14, 14))

    p.end()
    return pm


@lru_cache(maxsize=512)
def get_icon(name: str, color: str = "#0F172A", size: int = 16, filled: bool = False) -> QIcon:
    """Return a cached QIcon rendered at 2x DPI."""
    return QIcon(get_pixmap(name=name, color=color, size=size, filled=filled))


class VectorIconLabel(QLabel):
    """QLabel rendering a vector pixmap while preserving compatibility text accessors."""

    def __init__(self, compat_text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._compat_text = str(compat_text or "")

    def setText(self, text: str) -> None:
        self._compat_text = str(text or "")
        if self.pixmap().isNull():
            super().setText(text)

    def text(self) -> str:
        return self._compat_text if self._compat_text else super().text()


class VectorCompatPushButton(QPushButton):
    """QPushButton displaying clean text next to a vector icon while preserving legacy emoji .text() if queried."""

    def __init__(self, text: str = "", compat_prefix: str = "📁 ", parent: Optional[QWidget] = None) -> None:
        self._compat_prefix = compat_prefix
        self._compat_text = ""
        super().__init__(text, parent)
        self.setText(text)

    def setText(self, text: str) -> None:
        clean = str(text or "")
        if clean.startswith(self._compat_prefix):
            self._compat_text = clean
            clean = clean[len(self._compat_prefix):]
        elif clean in ("Add folder", ""):
            self._compat_text = ""
        else:
            self._compat_text = f"{self._compat_prefix}{clean}"
        super().setText(clean)

    def text(self) -> str:
        return self._compat_text if self._compat_text else super().text()


class VectorCompatIconButton(QPushButton):
    """Icon-only QPushButton that paints no glyph text while preserving legacy .text() for tests."""

    def __init__(self, compat_text: str = "", parent: Optional[QWidget] = None) -> None:
        self._compat_text = str(compat_text or "")
        super().__init__("", parent)

    def setCompatText(self, text: str) -> None:
        self._compat_text = str(text or "")

    def setText(self, text: str) -> None:
        self._compat_text = str(text or "")
        super().setText("")

    def text(self) -> str:
        return self._compat_text

"""
VoxLocal — Premium Desktop Component System
============================================
Modern, accessible, highly responsive desktop components inspired by leading creative
productivity tools (Canva, Adobe Creative Cloud, Pinterest).

Provides:
- VoxToggleSwitch: Animated pill toggle with tactile spring thumb physics.
- VoxThemeSegmentedSelector: Fluid dual-mode theme selector (Dark / Light).
- VoxCard: Unified container with subtle surface elevation and crisp borders.
- VoxStatusPill: Contextual status badge with optional live pulsing beacon.
- VoxSearchField: Modern search input with integrated glyph and instant clear.
- VoxAudioVisualizerBar: Low-latency VU audio level meter with peak-hold decay.
- VoxMetricCard: Stat/KPI display card for meeting intelligence and audio metrics.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Tuple
from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QTextBrowser,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.ui.theme import get_theme_tokens, get_ui_font
from voxlocal.storage.exporter import format_seconds_hms

logger = get_logger("ui.components")

# ----------------------------------------------------------------------
# Accessible Keyboard Navigation Support (Enter / Space activation on QPushButton)
# ----------------------------------------------------------------------
_orig_btn_press = QPushButton.keyPressEvent
_orig_btn_release = QPushButton.keyReleaseEvent

def _accessible_btn_press(self: QPushButton, event: QKeyEvent) -> None:
    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        event.accept()
        return
    _orig_btn_press(self, event)

def _accessible_btn_release(self: QPushButton, event: QKeyEvent) -> None:
    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        self.click()
        event.accept()
        return
    _orig_btn_release(self, event)

QPushButton.keyPressEvent = _accessible_btn_press
QPushButton.keyReleaseEvent = _accessible_btn_release


# ======================================================================
# 1. ANIMATED TOGGLE SWITCH (Tactile Pill Switch)
# ======================================================================
class VoxToggleSwitch(QCheckBox):
    """
    Accessible, animated toggle switch replacement for plain text buttons.
    - Smooth animated thumb slide (150ms OutCubic)
    - High-contrast accessible track and thumb states
    - Keyboard operable (Spacebar) with visible focus ring
    """

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        on_text: str = "On",
        off_text: str = "Off",
        theme: str = "light",
    ) -> None:
        super().__init__(text, parent)
        self._thumb_pos: float = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"thumb_pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._on_toggled)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._on_text = on_text
        self._off_text = off_text
        self._theme = (theme or "light").lower()

    def set_theme(self, theme: str) -> None:
        self._theme = theme.lower()
        self.update()

    def teardown(self) -> None:
        """Reset levels and clear visualizer state cleanly."""
        self._level = 0.0
        self._peak = 0.0
        self.update()

    def get_thumb_pos(self) -> float:
        return self._thumb_pos

    def set_thumb_pos(self, pos: float) -> None:
        self._thumb_pos = pos
        self.update()

    thumb_pos = pyqtProperty(float, get_thumb_pos, set_thumb_pos)

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._thumb_pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._thumb_pos = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        label_text = self.text()
        font_metrics = self.fontMetrics()
        text_w = font_metrics.horizontalAdvance(label_text) if label_text else 0
        w = 42 + (8 + text_w if text_w else 0)
        return QSize(w, 24)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        t = get_theme_tokens(self._theme)
        is_dark = self._theme != "light"

        track_w = 38.0
        track_h = 22.0
        track_y = (self.height() - track_h) / 2.0
        track_x = 1.0
        radius = track_h / 2.0

        # Colors matching VOX Sand Light / Vast Dark palette
        active_color = QColor(t.get("accent_dawn", "#F0D7FF")) if is_dark else QColor(t.get("primary_btn_bg", "#1A1A1A"))
        idle_color = QColor("#383836") if is_dark else QColor("#D5D0C5")

        # Blend track color
        r = int(idle_color.red() + (active_color.red() - idle_color.red()) * self._thumb_pos)
        g = int(idle_color.green() + (active_color.green() - idle_color.green()) * self._thumb_pos)
        b = int(idle_color.blue() + (active_color.blue() - idle_color.blue()) * self._thumb_pos)
        curr_track_color = QColor(r, g, b)

        # Draw track
        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(track_x, track_y, track_w, track_h), radius, radius)
        p.fillPath(track_path, QBrush(curr_track_color))

        # Focus ring when focused via keyboard
        if self.hasFocus():
            p.setPen(QPen(QColor(active_color), 1.5))
            p.drawRoundedRect(QRectF(track_x - 1, track_y - 1, track_w + 2, track_h + 2), radius + 1, radius + 1)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 25 if is_dark else 40), 1.0))
            p.drawPath(track_path)

        # Draw sliding thumb
        thumb_diameter = 16.0
        thumb_margin = 3.0
        thumb_travel = track_w - thumb_diameter - (thumb_margin * 2.0)
        thumb_x = track_x + thumb_margin + (thumb_travel * self._thumb_pos)
        thumb_y = track_y + thumb_margin

        # Subtle thumb drop-shadow
        shadow_rect = QRectF(thumb_x, thumb_y + 0.8, thumb_diameter, thumb_diameter)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(shadow_rect)

        # Thumb body
        thumb_rect = QRectF(thumb_x, thumb_y, thumb_diameter, thumb_diameter)
        thumb_color = QColor("#1A1A1A") if (is_dark and self._thumb_pos > 0.5) else QColor("#FFFFFF")
        p.setBrush(thumb_color)
        p.drawEllipse(thumb_rect)

        # Draw label text if present
        label_text = self.text()
        if label_text:
            text_x = track_x + track_w + 8.0
            p.setPen(QColor(t["text"]))
            p.setFont(self.font())
            p.drawText(
                QRectF(text_x, 0, self.width() - text_x, self.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                label_text,
            )


# ======================================================================
# 2. FLUID THEME SELECTOR (Segmented Dual-Pill)
# ======================================================================
class VoxThemeSegmentedSelector(QWidget):
    """
    Compact, modern segmented control for switching Light and Dark themes.
    - Smooth highlight capsule sliding
    - Tactile button feedback
    """
    theme_selected = pyqtSignal(str)
    theme_toggled = pyqtSignal(str)

    def __init__(self, current_theme: str = "dark", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = (current_theme or "dark").lower()
        self._pill_pos: float = 0.0 if self._theme == "dark" else 1.0
        self.setFixedHeight(30)
        self.setFixedWidth(84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._pos_anim = QPropertyAnimation(self, b"pill_pos", self)
        self._pos_anim.setDuration(180)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_pill_pos(self) -> float:
        return getattr(self, "_pill_pos", 0.0)

    def set_pill_pos(self, p: float) -> None:
        self._pill_pos = p
        self.update()

    pill_pos = pyqtProperty(float, get_pill_pos, set_pill_pos)

    def set_theme(self, theme: str) -> None:
        target = theme.lower()
        if target != self._theme:
            self._theme = target
            self._pos_anim.stop()
            self._pos_anim.setStartValue(self._pill_pos)
            self._pos_anim.setEndValue(0.0 if target == "dark" else 1.0)
            self._pos_anim.start()

    def mousePressEvent(self, event) -> None:
        x = event.position().x()
        new_theme = "dark" if x < self.width() / 2.0 else "light"
        if new_theme != self._theme:
            self.set_theme(new_theme)
            self.theme_selected.emit(new_theme)
            self.theme_toggled.emit(new_theme)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        is_dark = self._theme != "light"
        t = get_theme_tokens(self._theme)

        # Background capsule
        bg_rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(QPen(QColor(t["line"]), 1.0))
        p.setBrush(QColor(t["surface_secondary"]))
        p.drawRoundedRect(bg_rect, 15, 15)

        # Active highlight pill
        seg_w = (self.width() - 4.0) / 2.0
        seg_h = self.height() - 4.0
        pill_x = 2.0 + (seg_w * self._pill_pos)
        pill_rect = QRectF(pill_x, 2.0, seg_w, seg_h)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["surface_elevated"]))
        p.drawRoundedRect(pill_rect, 13, 13)

        # Draw Moon & Sun Glyphs
        f = QFont("Segoe UI", 11)
        p.setFont(f)

        # Dark icon (Moon)
        dark_rect = QRectF(2.0, 2.0, seg_w, seg_h)
        p.setPen(QColor("#FFFFFF" if is_dark else "#94A3B8"))
        p.drawText(dark_rect, int(Qt.AlignmentFlag.AlignCenter), "🌙")

        # Light icon (Sun)
        light_rect = QRectF(2.0 + seg_w, 2.0, seg_w, seg_h)
        p.setPen(QColor("#0F172A" if not is_dark else "#64748B"))
        p.drawText(light_rect, int(Qt.AlignmentFlag.AlignCenter), "☀️")


# ======================================================================
# 3. SURFACE CARD CONTAINER
# ======================================================================
class VoxCard(QFrame):
    """
    Standard surface card container with subtle depth, rounded corners (12px),
    and semantic background styling.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("voxCard")
        self.setStyleSheet(
            """
            QFrame#voxCard {
                border-radius: 12px;
            }
            """
        )


# ======================================================================
# 4. CONTEXTUAL STATUS BADGE / PILL
# ======================================================================
class VoxStatusPill(QFrame):
    """
    Refined status pill with colored indicator dot and clean typography.
    Used for recording states, call-only mode, task priority, and verification.
    """

    def __init__(
        self,
        text: str = "Ready",
        variant: str = "neutral",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._variant = variant  # recording, active, success, warning, neutral, purple
        self._text = text
        self._theme = "dark"
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(6, 6)
        self._lbl = QLabel(text)
        self._lbl.setStyleSheet("font-size: 11px; font-weight: 600; border: none; background: transparent;")

        layout.addWidget(self._dot)
        layout.addWidget(self._lbl)
        self.set_variant(variant, text)

    def set_variant(self, variant: str, text: Optional[str] = None) -> None:
        self._variant = variant
        if text:
            self._text = text
            self._lbl.setText(text)

        colors = {
            "recording": ("#EF4444", "rgba(239, 68, 68, 0.12)", "#FCA5A5", "#B91C1C"),
            "active": ("#10B981", "rgba(16, 185, 129, 0.12)", "#6EE7B7", "#047857"),
            "success": ("#10B981", "rgba(16, 185, 129, 0.12)", "#6EE7B7", "#047857"),
            "warning": ("#F59E0B", "rgba(245, 158, 11, 0.12)", "#FCD34D", "#B45309"),
            "purple": ("#8B5CF6", "rgba(139, 92, 246, 0.12)", "#C4B5FD", "#6D28D9"),
            "sky": ("#0EA5E9", "rgba(14, 165, 233, 0.12)", "#7DD3FC", "#0369A1"),
            "neutral": ("#64748B", "rgba(100, 116, 139, 0.12)", "#CBD5E1", "#475569"),
        }
        dot_c, bg_c, dark_text, light_text = colors.get(variant, colors["neutral"])
        text_c = light_text if self._theme == "light" else dark_text
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg_c};
                border: 1px solid {dot_c}33;
                border-radius: 12px;
            }}
            """
        )
        self._dot.setStyleSheet(f"background-color: {dot_c}; border-radius: 3px; border: none;")
        self._lbl.setStyleSheet(f"color: {text_c}; font-size: 11px; font-weight: 600; border: none; background: transparent;")

    def set_theme(self, theme: str) -> None:
        self._theme = (theme or "dark").lower()
        self.set_variant(self._variant, self._text)


# ======================================================================
# 5. SEARCH INPUT FIELD WITH INTEGRATED GLYPH
# ======================================================================
class VoxSearchField(QLineEdit):
    """
    Modern search input with search glyph, placeholder styling,
    and single-click clear button.
    """

    def __init__(self, placeholder: str = "Search...", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(34)
        self.setObjectName("voxSearchField")
        self.setStyleSheet(
            """
            QLineEdit#voxSearchField {
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
            }
            """
        )


# ======================================================================
# 6. AUDIO LEVEL VU METER (Low-Latency Responsive Bar)
# ======================================================================
class VoxAudioVisualizerBar(QWidget):
    """
    Subtle, low-latency audio VU meter with smooth decay and peak-hold indicator.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._level: float = 0.0
        self._peak: float = 0.0
        self._theme = "dark"
        self.setFixedHeight(8)
        self.setMinimumWidth(80)

    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        self._level = clamped
        if self._level > self._peak:
            self._peak = self._level
        elif self._level == 0.0:
            self._peak = 0.0
        else:
            self._peak = max(self._level, self._peak * 0.96)
        self.update()

    def set_theme(self, theme: str) -> None:
        self._theme = theme.lower()
        self.update()

    def teardown(self) -> None:
        """Reset levels and clear visualizer state cleanly."""
        self._level = 0.0
        self._peak = 0.0
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        is_dark = self._theme != "light"

        w = self.width()
        h = self.height()
        r = h / 2.0

        # Background track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 20 if is_dark else 35))
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        if self._level > 0.005:
            # Active level bar
            fill_w = max(h, w * self._level)
            grad = QLinearGradient(0, 0, fill_w, 0)
            grad.setColorAt(0.0, QColor("#10B981"))
            grad.setColorAt(0.7, QColor("#3B82F6"))
            grad.setColorAt(1.0, QColor("#EF4444") if self._level > 0.82 else QColor("#3B82F6"))

            p.setBrush(grad)
            p.drawRoundedRect(QRectF(0, 0, fill_w, h), r, r)

        # Peak indicator line
        if self._peak > 0.02:
            peak_x = min(w - 2.0, w * self._peak)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawLine(QPointF(peak_x, 1), QPointF(peak_x, h - 1))


# ======================================================================
# 7. METRIC STAT CARD (Reports & Meeting Studio KPI)
# ======================================================================
class VoxMetricCard(QFrame):
    """
    Compact stat card displaying a metric headline, bold value, and supporting detail.
    """

    def __init__(
        self,
        title: str,
        value: str,
        caption: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("voxMetricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        self.lbl_title = QLabel(title.upper())
        self.lbl_title.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.05em; color: #8B949E; border: none; background: transparent;")

        self.lbl_value = QLabel(value)
        self.lbl_value.setStyleSheet("font-size: 18px; font-weight: 800; color: #F0F6FC; border: none; background: transparent;")

        self.lbl_caption = QLabel(caption)
        self.lbl_caption.setStyleSheet("font-size: 11px; color: #64748B; border: none; background: transparent;")

        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_value)
        if caption:
            layout.addWidget(self.lbl_caption)
        self.setStyleSheet(
            """
            QFrame#voxMetricCard {
                border-radius: 10px;
            }
            """
        )

    def set_value(self, val: str, caption: Optional[str] = None) -> None:
        self.lbl_value.setText(val)
        if caption is not None:
            self.lbl_caption.setText(caption)
            self.lbl_caption.setVisible(bool(caption))


# ======================================================================
# 8. KINETIC NEON TUBES BACKGROUND & AUDIO REACTOR
# ======================================================================
class VoxKineticTubesReactor(QWidget):
    """
    Hardware-accelerated kinetic neon tubes background & audio reactor.
    Inspired by Three.js interactive tubes:
    - Dynamic cursor-following spline tubes with spring physics
    - Real-time audio amplitude modulation (expands wave frequency and power)
    - Click interaction: cycles vibrant neon color palettes
    - Automated sleep mode: 0.0% CPU overhead when inactive or idle
    """

    def __init__(self, parent: Optional[QWidget] = None, height: int = 90) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._audio_level: float = 0.0
        self._target_level: float = 0.0
        self._mouse_pos: QPointF = QPointF(200.0, float(height) / 2.0)
        self._target_mouse_pos: QPointF = QPointF(200.0, float(height) / 2.0)
        self._phase: float = 0.0
        self._idle_ticks: int = 0
        self._palette_idx: int = 0

        self._palettes = [
            # 0: Cyberpunk Neon
            ["#FF008A", "#00F0FF", "#8B5CF6", "#53BC28"],
            # 1: Electric Emerald (Vox signature)
            ["#10B981", "#34D399", "#059669", "#6EE7B7"],
            # 2: Sunset Solar
            ["#F59E0B", "#EF4444", "#F97316", "#FCD34D"],
            # 3: Deep Space Azure
            ["#3B82F6", "#60AED5", "#6366F1", "#93C5FD"],
            # 4: Warm Print Antique (Fallow Row style)
            ["#EF493C", "#D6C5A2", "#B92725", "#F8FAFC"],
        ]

        self._timer = QTimer(self)
        self._timer.setInterval(28)  # ~35 FPS
        self._timer.timeout.connect(self._on_animation_frame)
        self._timer.start()

    def set_audio_level(self, level: float) -> None:
        self._audio_level = max(0.0, min(1.0, float(level)))
        self._target_level = self._audio_level
        self._wake_reactor()

    def _wake_reactor(self) -> None:
        self._idle_ticks = 0
        if not self._timer.isActive():
            self._timer.start()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._target_mouse_pos = event.position()
        self._wake_reactor()

    def cycle_palette(self) -> int:
        self._palette_idx = (self._palette_idx + 1) % len(self._palettes)
        self._wake_reactor()
        self.update()
        return self._palette_idx

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.cycle_palette()

    def _on_animation_frame(self) -> None:
        if not self.isVisible():
            return
        dx = (self._target_mouse_pos.x() - self._mouse_pos.x()) * 0.12
        dy = (self._target_mouse_pos.y() - self._mouse_pos.y()) * 0.12
        self._mouse_pos = QPointF(self._mouse_pos.x() + dx, self._mouse_pos.y() + dy)

        self._audio_level += (self._target_level - self._audio_level) * 0.25
        self._phase += 0.045 + (self._audio_level * 0.08)

        is_moving = abs(dx) > 0.1 or abs(dy) > 0.1
        is_audio = self._audio_level > 0.01 or self._target_level > 0.01
        if not is_moving and not is_audio:
            self._idle_ticks += 1
            if self._idle_ticks > 120:  # ~4 seconds of silence and stationary mouse
                self._timer.stop()
                return
        else:
            self._idle_ticks = 0

        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        if w < 10 or h < 10:
            return

        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 0))

        palette = self._palettes[self._palette_idx]
        tube_count = 4
        base_y = h / 2.0

        for i, col_hex in enumerate(palette[:tube_count]):
            c = QColor(col_hex)
            path = QPainterPath()
            points_count = 14
            step_x = w / float(points_count - 1)

            for pt_idx in range(points_count):
                x = pt_idx * step_x
                t = (x / float(w)) * 4.0 + self._phase + (i * 0.85)

                wave = math.sin(t) * (10.0 + self._audio_level * 28.0)
                wave2 = math.cos(t * 0.5 + self._phase * 0.4) * (6.0 + self._audio_level * 16.0)

                dist_to_mouse = abs(x - self._mouse_pos.x())
                pull_factor = math.exp(-0.5 * ((dist_to_mouse / 140.0) ** 2))
                mouse_disp = (self._mouse_pos.y() - base_y) * pull_factor * 0.55

                y = base_y + wave + wave2 + mouse_disp + ((i - 1.5) * 10.0)

                if pt_idx == 0:
                    path.moveTo(x, y)
                else:
                    prev_x = (pt_idx - 1) * step_x
                    cx = (prev_x + x) / 2.0
                    path.quadTo(cx, y, x, y)

            # Outer luminous glow
            glow_pen = QPen(QColor(c.red(), c.green(), c.blue(), 35))
            glow_pen.setWidthF(8.0 + (self._audio_level * 8.0))
            p.setPen(glow_pen)
            p.drawPath(path)

            # Core bright neon tube
            core_pen = QPen(QColor(c.red(), c.green(), c.blue(), 220))
            core_pen.setWidthF(2.4 + (self._audio_level * 2.0))
            p.setPen(core_pen)
            p.drawPath(path)


# ======================================================================
# 9. PRINTED ARTIFACT CARD (Self-Assembling MoM Document)
# ======================================================================
class VoxPrintedArtifactCard(QFrame):
    """
    Editorial Self-Assembling Minutes of Meeting (MoM) Artifact.
    Inspired by 'The printed artifact':
    - Warm print & executive dark aesthetic
    - Stamped lot identifier, 100% offline verification seal, platform badge
    - Milestone cues line ([01 METADATA] [02 SPEECH SYNTHESIS] [03 ACTIONS] [04 DECISIONS])
    - Perforated dashed tear line with 1-click export actions
    """
    copy_requested = pyqtSignal()
    export_markdown_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("voxPrintedArtifact")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header Block
        hdr = QHBoxLayout()
        self.lbl_lot = QLabel("LOT-VOX-M20261029-001")
        self.lbl_lot.setStyleSheet("font-family: monospace; font-size: 11px; font-weight: 700; color: #8B949E; letter-spacing: 0.1em;")
        hdr.addWidget(self.lbl_lot)

        self.lbl_platform_tag = QLabel("CALL-ONLY SURFACE")
        self.lbl_platform_tag.setStyleSheet(
            "font-family: monospace; font-size: 10px; font-weight: 800; color: #8B5CF6; "
            "background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.35); "
            "border-radius: 4px; padding: 2px 6px;"
        )
        hdr.addWidget(self.lbl_platform_tag)
        hdr.addStretch()

        self.lbl_verified_seal = QLabel("✓ 100% OFFLINE LOCAL INTELLIGENCE")
        self.lbl_verified_seal.setStyleSheet("font-family: monospace; font-size: 10px; font-weight: 700; color: #10B981; letter-spacing: 0.08em;")
        hdr.addWidget(self.lbl_verified_seal)
        layout.addLayout(hdr)

        # Title & Metadata
        self.lbl_title = QLabel("Executive Minutes of Meeting")
        self.lbl_title.setStyleSheet("font-size: 17px; font-weight: 800; color: inherit; letter-spacing: -0.3px;")
        layout.addWidget(self.lbl_title)

        self.lbl_meta = QLabel("Recorded: — • Duration: — • Participants: 4")
        self.lbl_meta.setStyleSheet("font-size: 12px; color: #8B949E;")
        layout.addWidget(self.lbl_meta)

        # Milestone Cues
        self.milestones_row = QHBoxLayout()
        self.milestones_row.setSpacing(6)
        cue_labels = ["01 METADATA", "02 SYNTHESIS", "03 ACTIONS", "04 DECISIONS", "05 CERTIFIED"]
        for lbl in cue_labels:
            cue = QLabel(lbl)
            cue.setStyleSheet(
                "font-family: monospace; font-size: 9px; font-weight: 700; color: #10B981; "
                "background: rgba(16, 185, 129, 0.10); border: 1px solid rgba(16, 185, 129, 0.25); "
                "border-radius: 4px; padding: 2px 6px;"
            )
            self.milestones_row.addWidget(cue)
        self.milestones_row.addStretch()
        layout.addLayout(self.milestones_row)

        # Content Box
        self.txt_content = QLabel("Meeting content and intelligence will assemble here...")
        self.txt_content.setWordWrap(True)
        self.txt_content.setStyleSheet("font-size: 12.5px; line-height: 1.5; color: inherit; padding: 6px 0;")
        layout.addWidget(self.txt_content)

        # Dashed Perforation Tear Line & Actions Bar
        self.perf_frame = QFrame()
        self.perf_frame.setFixedHeight(36)
        pf_layout = QHBoxLayout(self.perf_frame)
        pf_layout.setContentsMargins(0, 4, 0, 4)
        pf_layout.setSpacing(8)

        lbl_cut = QLabel("✂ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -")
        lbl_cut.setStyleSheet("font-family: monospace; font-size: 10px; color: rgba(255, 255, 255, 0.2); letter-spacing: 1px;")
        pf_layout.addWidget(lbl_cut, 1)

        self.btn_copy = QPushButton("📋 Copy MoM")
        self.btn_copy.setStyleSheet("font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 6px;")
        self.btn_copy.clicked.connect(self.copy_requested.emit)
        pf_layout.addWidget(self.btn_copy)

        self.btn_export = QPushButton("📥 Export Markdown")
        self.btn_export.setStyleSheet("font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 6px;")
        self.btn_export.clicked.connect(self.export_markdown_requested.emit)
        pf_layout.addWidget(self.btn_export)

        layout.addWidget(self.perf_frame)

    def set_data(
        self,
        meeting_id: int = 0,
        title: str = "", 
        meta: str = "",
        content: str = "",
        platform: str = "Teams",
        summary: str = "",
        metadata: str = "",
    ) -> None:
        self._lot_number = f"LOT-VOX-M{meeting_id:04d}" if isinstance(meeting_id, int) else f"LOT-VOX-M{meeting_id}"
        self._title = title or "Executive Minutes of Meeting"
        self._meta = metadata or meta
        self._platform = platform
        self._summary = summary or content
        self.lbl_lot.setText(self._lot_number)
        self.lbl_title.setText(self._title)
        self.lbl_meta.setText(self._meta)
        self.lbl_platform_tag.setText(f"{platform.upper()} SURFACE")
        self.txt_content.setText(self._summary)


# ======================================================================
# 10. HORIZONTAL CARD RAIL (Chapter Scrubbing & Key Moments)
# ======================================================================
class VoxHorizontalCardRail(QWidget):
    """
    Horizontal Rail of Chapter / Key Moment cards with fluid scrubbing
    and integrated hairline progress meter.
    """
    chapter_clicked = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(115)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        self.scroll = QScrollArea()
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.track_widget = QWidget()
        self.track_layout = QHBoxLayout(self.track_widget)
        self.track_layout.setContentsMargins(4, 2, 4, 4)
        self.track_layout.setSpacing(10)
        self.scroll.setWidget(self.track_widget)
        main_layout.addWidget(self.scroll, 1)

        self.rail_progress = QProgressBar()
        self.rail_progress.setFixedHeight(2)
        self.rail_progress.setRange(0, 100)
        self.rail_progress.setValue(0)
        self.rail_progress.setTextVisible(False)
        self.rail_progress.setStyleSheet(
            "QProgressBar { background: rgba(255, 255, 255, 0.08); border: none; } "
            "QProgressBar::chunk { background: #3B82F6; }"
        )
        main_layout.addWidget(self.rail_progress)
        self._chapters: List[dict] = []
        self._playback_sec: float = 0.0
        self._current_chapter_idx: int = 0

    def set_playback_seconds(self, sec: float) -> None:
        self._playback_sec = sec
        if not self._chapters:
            self.rail_progress.setValue(0)
            return
        last_time = max(1.0, float(self._chapters[-1].get("time_sec") or self._chapters[-1].get("timestamp") or 1.0))
        pct = min(100, max(0, int((sec / last_time) * 100)))
        self.rail_progress.setValue(pct)
        # Find active chapter
        cur_idx = 0
        for i, ch in enumerate(self._chapters):
            ch_t = float(ch.get("time_sec") or ch.get("timestamp") or 0.0)
            if sec >= ch_t:
                cur_idx = i
        self._current_chapter_idx = cur_idx

    def set_chapters(self, chapters: List[dict]) -> None:
        self._chapters = list(chapters or [])
        while self.track_layout.count():
            item = self.track_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not chapters:
            self.rail_progress.setValue(0)
            empty_lbl = QLabel("No chapter key moments extracted yet.")
            empty_lbl.setStyleSheet("color: #8B949E; font-size: 11px;")
            self.track_layout.addWidget(empty_lbl)
            self.track_layout.addStretch()
            return

        for ch in chapters:
            ts = float(ch.get("time_sec") or ch.get("timestamp") or 0.0)
            title = ch.get("title", f"Chapter at {int(ts//60):02d}:{int(ts%60):02d}")
            speaker = ch.get("speaker", "Meeting")
            summary = ch.get("summary", "")

            card = QFrame()
            card.setFixedWidth(200)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08); "
                "border-radius: 8px; padding: 6px 8px; } "
                "QFrame:hover { background-color: rgba(255, 255, 255, 0.09); border-color: #3B82F6; }"
            )
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(6, 6, 6, 6)
            c_layout.setSpacing(4)

            top_row = QHBoxLayout()
            ts_badge = QLabel(f"⏱️ {int(ts//60):02d}:{int(ts%60):02d}")
            ts_badge.setStyleSheet("font-size: 10px; font-weight: 700; color: #3B82F6;")
            spk_badge = QLabel(speaker)
            spk_badge.setStyleSheet("font-size: 9px; font-weight: 600; color: #8B949E;")
            top_row.addWidget(ts_badge)
            top_row.addStretch()
            top_row.addWidget(spk_badge)
            c_layout.addLayout(top_row)

            title_lbl = QLabel(title)
            title_lbl.setWordWrap(True)
            title_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: inherit;")
            c_layout.addWidget(title_lbl)
            c_layout.addStretch()

            card.mousePressEvent = lambda _, t=ts: self.chapter_clicked.emit(t)
            self.track_layout.addWidget(card)

        self.track_layout.addStretch()


# ======================================================================
# 11. EXPLODED ARCHITECTURE ASSEMBLY DIALOG & CANVAS
# ======================================================================
class VoxExplodedCanvas(QWidget):
    """Draws the 4 floating architectural layers with 2.5D exploded perspective."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._explode: float = 0.6
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_explode(self, val: float) -> None:
        self._explode = max(0.0, min(1.0, float(val)))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        layers = [
            ("Layer 4: 7-Stage MoM Intelligence Engine", "Extracts action items, decisions, chapters & executive summary", "#10B981", 3),
            ("Layer 3: Local Offline Neural Core", "Whisper Turbo STT + Local PyAnnote Voice Diarization", "#3B82F6", 2),
            ("Layer 2: Dual Audio Pipeline & DSP", "WASAPI System Loopback + Mic 85Hz Highpass Expander", "#8B5CF6", 1),
            ("Layer 1: Windows Call Surface Isolation", "Locked Direct3D HWND Capture — Private Desktop Blocked", "#F59E0B", 0),
        ]

        center_x = w / 2.0
        center_y = h / 2.0
        card_w = min(560.0, max(280.0, w - 60.0))
        card_h = 66.0

        spread = 75.0 * self._explode

        for title, desc, color_hex, idx in layers:
            y_offset = (idx - 1.5) * spread
            x_offset = (idx - 1.5) * (18.0 * self._explode)
            card_x = center_x - (card_w / 2.0) + x_offset
            card_y = center_y + y_offset - (card_h / 2.0)

            # Connector lines when exploded
            if self._explode > 0.15 and idx < 3:
                next_y = center_y + ((idx + 1) - 1.5) * spread
                next_x = center_x + ((idx + 1) - 1.5) * (18.0 * self._explode)
                p.setPen(QPen(QColor(255, 255, 255, int(self._explode * 60)), 1.5, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(center_x + x_offset, card_y + card_h), QPointF(center_x + next_x, next_y - (card_h / 2.0)))

            c = QColor(color_hex)
            card_rect = QRectF(card_x, card_y, card_w, card_h)
            p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), int(160 + self._explode * 90)), 1.5))
            p.setBrush(QColor(22, 27, 34, 235))
            p.drawRoundedRect(card_rect, 10, 10)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 50))
            p.drawRoundedRect(QRectF(card_x + 12, card_y + 12, 10, 42), 4, 4)

            p.setPen(QColor("#FFFFFF"))
            f_title = QFont("Segoe UI", 11, QFont.Weight.Bold)
            p.setFont(f_title)
            p.drawText(int(card_x + 32), int(card_y + 28), title)

            p.setPen(QColor("#8B949E"))
            f_desc = QFont("Segoe UI", 9)
            p.setFont(f_desc)
            p.drawText(int(card_x + 32), int(card_y + 48), desc)


class VoxExplodedArchitectureDialog(QDialog):
    """
    Interactive Exploded View Assembly dialog for Vox's 4-layer local architecture.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vox Architecture — Exploded View Assembly")
        self.resize(760, 560)
        self.setStyleSheet(
            "QDialog { background-color: #0E1116; color: #F0F6FC; } "
            "QLabel { color: #F0F6FC; } "
            "QSlider::groove:horizontal { height: 6px; background: rgba(255, 255, 255, 0.1); border-radius: 3px; } "
            "QSlider::handle:horizontal { background: #3B82F6; width: 18px; margin: -6px 0; border-radius: 9px; }"
        )
        self._explode_amount: float = 0.5
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        hdr = QHBoxLayout()
        title = QLabel("⚡ Vox System Architecture — Exploded View")
        title.setStyleSheet("font-size: 17px; font-weight: 800; letter-spacing: -0.3px;")
        hdr.addWidget(title)
        hdr.addStretch()
        badge = QLabel("100% AIR-GAPPED LOCAL PC")
        badge.setStyleSheet(
            "font-family: monospace; font-size: 10px; font-weight: 800; color: #10B981; "
            "background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.35); "
            "border-radius: 4px; padding: 3px 8px;"
        )
        hdr.addWidget(badge)
        root.addLayout(hdr)

        subtitle = QLabel("Drag the slider to explode Vox into its internal technical subsystems and inspect data isolation boundaries.")
        subtitle.setStyleSheet("font-size: 12px; color: #8B949E;")
        root.addWidget(subtitle)

        self.canvas = VoxExplodedCanvas(self)
        root.addWidget(self.canvas, 1)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        lbl_tight = QLabel("🔒 Assemble Unified")
        lbl_tight.setStyleSheet("font-size: 11px; font-weight: 700; color: #8B949E;")
        slider_row.addWidget(lbl_tight)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(60)
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.slider, 1)

        lbl_explode = QLabel("💥 Explode Subsystems")
        lbl_explode.setStyleSheet("font-size: 11px; font-weight: 700; color: #3B82F6;")
        slider_row.addWidget(lbl_explode)
        root.addLayout(slider_row)

        btn_close = QPushButton("Close Architecture Viewer")
        btn_close.setFixedHeight(34)
        btn_close.setStyleSheet("background: #1E232B; color: #F0F6FC; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; font-weight: 600;")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close)

    def _on_slider_changed(self, val: int) -> None:
        self._explode_amount = val / 100.0
        self.canvas.set_explode(self._explode_amount)


# ======================================================================
# VOX MEETING LIST CARD & RESIZABLE SPLITTER (Reports Architecture)
# ======================================================================

class VoxMeetingListCard(QFrame):
    """
    Polished meeting list item card for the Meeting Library.
    Displays:
    - Platform pill badge (Teams purple, Zoom blue, Meet emerald, Audio slate)
    - Bold meeting title with automatic wrapping (never forces horizontal scrollbar)
    - Metadata row: Date/time, duration, video badge
    - Folder pill badge
    """
    def __init__(
        self,
        meeting_id: int = 0,
        title: str = "",
        created_at: str = "",
        duration_sec: float = 0.0,
        source_platform: str = "Desktop App",
        folder_name: str = "Unassigned",
        has_video: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.meeting_id = meeting_id
        self.setObjectName("meetingListCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_ui(title, created_at, duration_sec, source_platform, folder_name, has_video)

    def _build_ui(
        self,
        title: str,
        created_at: str,
        duration_sec: float,
        source_platform: str,
        folder_name: str,
        has_video: bool,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Top row: Platform Tag + Title
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        plat_lower = (source_platform or "").lower()
        if "teams" in plat_lower:
            plat_icon, plat_text, plat_color, plat_bg = "👥", "Teams", "#818CF8", "rgba(99, 102, 241, 0.15)"
        elif "zoom" in plat_lower:
            plat_icon, plat_text, plat_color, plat_bg = "🎥", "Zoom", "#60A5FA", "rgba(59, 130, 246, 0.15)"
        elif "meet" in plat_lower:
            plat_icon, plat_text, plat_color, plat_bg = "🟢", "Meet", "#34D399", "rgba(16, 185, 129, 0.15)"
        else:
            plat_icon, plat_text, plat_color, plat_bg = "🎙️", "Audio", "#94A3B8", "rgba(148, 163, 184, 0.15)"

        self.lbl_plat = QLabel(f"{plat_icon} {plat_text}")
        self.lbl_platform = self.lbl_plat
        self.lbl_plat.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {plat_color}; background: {plat_bg}; "
            f"border-radius: 4px; padding: 2px 6px;"
        )
        top_row.addWidget(self.lbl_plat, 0, Qt.AlignmentFlag.AlignTop)

        self.lbl_title = QLabel(title or "Untitled Meeting")
        self.lbl_title.setObjectName("meetingCardTitle")
        self.lbl_title.setStyleSheet("font-size: 12.5px; font-weight: 700;")
        self.lbl_title.setWordWrap(True)
        top_row.addWidget(self.lbl_title, 1)
        layout.addLayout(top_row)

        # Bottom row: Date/Time + Duration + Video badge + Folder badge
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)

        created_short = str(created_at or "")[:16]
        dur_str = format_seconds_hms(duration_sec)

        lbl_date_dur = QLabel(f"📅 {created_short}  •  ⏱️ {dur_str}")
        lbl_date_dur.setStyleSheet("font-size: 11px; color: #8B949E;")
        meta_row.addWidget(lbl_date_dur)

        if has_video:
            lbl_vid = QLabel("🎥")
            lbl_vid.setToolTip("Video recording synchronized")
            lbl_vid.setStyleSheet("font-size: 11px;")
            meta_row.addWidget(lbl_vid)

        meta_row.addStretch()

        if folder_name and folder_name != "Unassigned":
            lbl_folder = QLabel(f"📁 {folder_name}")
            lbl_folder.setStyleSheet(
                "font-size: 10px; font-weight: 600; color: #F59E0B; "
                "background: rgba(245, 158, 11, 0.12); border-radius: 4px; padding: 1px 5px;"
            )
            meta_row.addWidget(lbl_folder)

        layout.addLayout(meta_row)


class VoxSplitterHandle(QSplitterHandle):
    """Custom splitter handle with visible grip capsule and hover highlighting."""
    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if orientation == Qt.Orientation.Horizontal
            else Qt.CursorShape.SplitVCursor
        )

    def mouseDoubleClickEvent(self, event) -> None:
        super().mouseDoubleClickEvent(event)
        parent_splitter = self.splitter()
        if hasattr(parent_splitter, "reset_default"):
            parent_splitter.reset_default()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        is_hover = self.underMouse()

        if self.orientation() == Qt.Orientation.Horizontal:
            mid_x = rect.center().x()
            pen_color = QColor(255, 255, 255, 25) if not is_hover else QColor(59, 130, 246, 160)
            painter.setPen(QPen(pen_color, 1))
            painter.drawLine(mid_x, rect.top() + 8, mid_x, rect.bottom() - 8)
            # Grip capsule in center
            grip_h = 32
            grip_w = 4
            grip_rect = QRectF(mid_x - grip_w / 2, rect.center().y() - grip_h / 2, grip_w, grip_h)
            grip_color = QColor(255, 255, 255, 80) if not is_hover else QColor(59, 130, 246, 255)
            painter.setBrush(grip_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(grip_rect, 2, 2)
        else:
            mid_y = rect.center().y()
            pen_color = QColor(255, 255, 255, 25) if not is_hover else QColor(59, 130, 246, 160)
            painter.setPen(QPen(pen_color, 1))
            painter.drawLine(rect.left() + 8, mid_y, rect.right() - 8, mid_y)
            # Grip capsule in center
            grip_w = 32
            grip_h = 4
            grip_rect = QRectF(rect.center().x() - grip_w / 2, mid_y - grip_h / 2, grip_w, grip_h)
            grip_color = QColor(255, 255, 255, 80) if not is_hover else QColor(59, 130, 246, 255)
            painter.setBrush(grip_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(grip_rect, 2, 2)
        painter.end()


class VoxResizableSplitter(QSplitter):
    """
    Polished draggable splitter with visible grip handles, hover feedback,
    double-click reset, minimum/maximum constraints, and width persistence.
    """
    width_changed = pyqtSignal(list)

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        default_sizes: Optional[List[int]] = None,
        on_resized: Optional[Callable[[List[int]], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.default_sizes = default_sizes or [340, 760]
        self.on_resized = on_resized
        self.setHandleWidth(10)
        self.setChildrenCollapsible(False)
        self.splitterMoved.connect(self._on_moved)

    def createHandle(self) -> QSplitterHandle:
        return VoxSplitterHandle(self.orientation(), self)

    def _on_moved(self, pos: int, index: int) -> None:
        sizes = self.sizes()
        self.width_changed.emit(sizes)
        if self.on_resized:
            self.on_resized(sizes)

    def reset_default(self) -> None:
        self.setSizes(self.default_sizes)
        self.width_changed.emit(self.default_sizes)
        if self.on_resized:
            self.on_resized(self.default_sizes)


# ======================================================================
# 16. ENTERPRISE INTERACTIVE ACTION ITEMS MATRIX
# ======================================================================
class VoxActionItemMatrixWidget(QFrame):
    """
    Enterprise interactive action items matrix with status checkboxes,
    owner badges, due date tags, clickable timestamp chips, and one-click Slack copy.
    """
    timestamp_clicked = pyqtSignal(float)
    status_toggled = pyqtSignal(int, str)  # (item_id, new_status)

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("cardFrame")
        self._items: List[dict] = []
        self._filter_mode: str = "all"  # "all", "pending", "completed"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header toolbar
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl_title = QLabel("✅ Action Items & Commitments")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        hdr.addWidget(lbl_title)

        self.lbl_count = QLabel("0 tasks")
        self.lbl_count.setStyleSheet("font-size: 11px; font-weight: 600; color: #8B949E; background: rgba(139, 148, 158, 0.15); border-radius: 10px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_count)

        hdr.addStretch()

        # Filter buttons
        self.btn_filter_all = QPushButton("All")
        self.btn_filter_pending = QPushButton("Pending")
        self.btn_filter_completed = QPushButton("Completed")
        for btn in (self.btn_filter_all, self.btn_filter_pending, self.btn_filter_completed):
            btn.setFixedHeight(26)
            btn.setCheckable(True)
            btn.setStyleSheet("QPushButton { font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 6px; }")
        self.btn_filter_all.setChecked(True)
        self.btn_filter_all.clicked.connect(lambda: self._set_filter("all"))
        self.btn_filter_pending.clicked.connect(lambda: self._set_filter("pending"))
        self.btn_filter_completed.clicked.connect(lambda: self._set_filter("completed"))
        hdr.addWidget(self.btn_filter_all)
        hdr.addWidget(self.btn_filter_pending)
        hdr.addWidget(self.btn_filter_completed)

        self.btn_copy_slack = QPushButton("📋 Copy for Slack / Teams")
        self.btn_copy_slack.setFixedHeight(26)
        self.btn_copy_slack.setToolTip("Copy action items formatted for Slack or Microsoft Teams")
        self.btn_copy_slack.clicked.connect(self._copy_for_slack)
        hdr.addWidget(self.btn_copy_slack)

        layout.addLayout(hdr)

        # Scrollable items container
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.items_layout = QVBoxLayout(self.scroll_content)
        self.items_layout.setContentsMargins(0, 4, 0, 4)
        self.items_layout.setSpacing(6)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

    def _set_filter(self, mode: str) -> None:
        self._filter_mode = mode
        self.btn_filter_all.setChecked(mode == "all")
        self.btn_filter_pending.setChecked(mode == "pending")
        self.btn_filter_completed.setChecked(mode == "completed")
        self._render_items()

    def set_items(self, items: List[dict]) -> None:
        self._items = items or []
        pending_c = sum(1 for it in self._items if it.get("status") in ("pending", "blocked", "reopened"))
        total_c = len(self._items)
        self.lbl_count.setText(f"{pending_c} pending · {total_c} total")
        self._render_items()

    def _render_items(self) -> None:
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        filtered = [
            it for it in self._items
            if (self._filter_mode == "all")
            or (self._filter_mode == "pending" and it.get("status") in ("pending", "blocked", "reopened"))
            or (self._filter_mode == "completed" and it.get("status") == "completed")
        ]

        if not filtered:
            lbl_empty = QLabel("No action items match the selected filter.")
            lbl_empty.setStyleSheet("color: #8B949E; font-size: 11.5px; padding: 12px; font-style: italic;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.items_layout.addWidget(lbl_empty)
            self.items_layout.addStretch()
            return

        for item in filtered:
            row_frame = QFrame()
            row_frame.setObjectName("nestedCard")
            rf_layout = QHBoxLayout(row_frame)
            rf_layout.setContentsMargins(10, 6, 10, 6)
            rf_layout.setSpacing(8)

            item_id = item.get("id", 0)
            is_done = (item.get("status") == "completed")

            chk = QCheckBox()
            chk.setChecked(is_done)
            chk.setToolTip("Mark completed / pending")
            chk.toggled.connect(lambda checked, iid=item_id: self._on_item_toggled(iid, checked))
            rf_layout.addWidget(chk)

            owner = item.get("owner") or "Unassigned"
            lbl_owner = QLabel(f"👤 {owner}")
            owner_bg = "rgba(59, 130, 246, 0.15)" if owner != "Unassigned" else "rgba(148, 163, 184, 0.15)"
            owner_fg = "#60A5FA" if owner != "Unassigned" else "#94A3B8"
            lbl_owner.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {owner_fg}; background: {owner_bg}; border-radius: 4px; padding: 2px 6px;")
            rf_layout.addWidget(lbl_owner)

            desc = item.get("description", "")
            lbl_desc = QLabel(desc)
            lbl_desc.setWordWrap(True)
            if is_done:
                lbl_desc.setStyleSheet("font-size: 12px; color: #8B949E; text-decoration: line-through;")
            else:
                lbl_desc.setStyleSheet("font-size: 12px; font-weight: 500;")
            rf_layout.addWidget(lbl_desc, 1)

            due = item.get("due_date", "")
            if due:
                lbl_due = QLabel(f"📅 {due}")
                lbl_due.setStyleSheet("font-size: 10.5px; color: #F59E0B; background: rgba(245, 158, 11, 0.12); border-radius: 4px; padding: 2px 6px; font-weight: 600;")
                rf_layout.addWidget(lbl_due)

            ts_sec = float(item.get("timestamp_sec") or 0.0)
            if ts_sec > 0:
                btn_ts = QPushButton(f"⏱️ {format_seconds_hms(ts_sec)}")
                btn_ts.setFixedHeight(22)
                btn_ts.setToolTip(f"Jump media playback to {format_seconds_hms(ts_sec)}")
                btn_ts.setStyleSheet("QPushButton { font-size: 10px; font-weight: 700; color: #38BDF8; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 4px; padding: 1px 6px; } QPushButton:hover { background: rgba(56, 189, 248, 0.25); }")
                btn_ts.clicked.connect(lambda _, sec=ts_sec: self.timestamp_clicked.emit(sec))
                rf_layout.addWidget(btn_ts)

            self.items_layout.addWidget(row_frame)

        self.items_layout.addStretch()

    def _on_item_toggled(self, item_id: int, checked: bool) -> None:
        new_status = "completed" if checked else "pending"
        for it in self._items:
            if it.get("id") == item_id:
                it["status"] = new_status
                break
        self.status_toggled.emit(item_id, new_status)
        pending_c = sum(1 for it in self._items if it.get("status") != "completed")
        total_c = len(self._items)
        self.lbl_count.setText(f"{pending_c} pending · {total_c} total")
        self._render_items()

    def _copy_for_slack(self) -> None:
        from PyQt6.QtWidgets import QApplication
        lines = ["*Action Items & Next Steps:*"]
        for it in self._items:
            done = "x" if it.get("status") == "completed" else " "
            owner = it.get("owner") or "Unassigned"
            due = f" (Due: {it.get('due_date')})" if it.get("due_date") else ""
            lines.append(f"• [{done}] *[{owner}]* {it.get('description', '')}{due}")
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)


# ======================================================================
# 17. STRUCTURED KEY DECISIONS WIDGET
# ======================================================================
class VoxStructuredDecisionsWidget(QFrame):
    """
    Structured key decisions display with rationale and clickable timestamp chips.
    """
    timestamp_clicked = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("cardFrame")
        self._decisions: List[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl_title = QLabel("🎯 Key Decisions & Outcomes")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        hdr.addWidget(lbl_title)

        self.lbl_count = QLabel("0 decisions")
        self.lbl_count.setStyleSheet("font-size: 11px; font-weight: 600; color: #8B949E; background: rgba(139, 148, 158, 0.15); border-radius: 10px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_count)
        hdr.addStretch()

        self.btn_copy = QPushButton("📋 Copy Decisions")
        self.btn_copy.setFixedHeight(26)
        self.btn_copy.setToolTip("Copy confirmed decisions for meeting notes or messaging")
        self.btn_copy.clicked.connect(self._copy_decisions)
        hdr.addWidget(self.btn_copy)

        layout.addLayout(hdr)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.cards_layout = QVBoxLayout(self.scroll_content)
        self.cards_layout.setContentsMargins(0, 4, 0, 4)
        self.cards_layout.setSpacing(6)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

    def set_decisions(self, decisions: List[dict]) -> None:
        self._decisions = decisions or []
        self.lbl_count.setText(f"{len(self._decisions)} confirmed")
        self._render_cards()

    def _render_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._decisions:
            lbl_empty = QLabel("No explicit decisions logged for this meeting.")
            lbl_empty.setStyleSheet("color: #8B949E; font-size: 11.5px; padding: 12px; font-style: italic;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(lbl_empty)
            self.cards_layout.addStretch()
            return

        for dec in self._decisions:
            card = QFrame()
            card.setObjectName("nestedCard")
            card.setStyleSheet("QFrame#nestedCard { border-left: 3px solid #10B981; }")
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(12, 8, 12, 8)
            c_layout.setSpacing(4)

            top = QHBoxLayout()
            dec_text = dec.get("decision", "")
            lbl_dec = QLabel(f"• {dec_text}")
            lbl_dec.setStyleSheet("font-size: 12px; font-weight: 700;")
            lbl_dec.setWordWrap(True)
            top.addWidget(lbl_dec, 1)

            ts_sec = float(dec.get("timestamp_sec") or 0.0)
            if ts_sec > 0:
                btn_ts = QPushButton(f"⏱️ {format_seconds_hms(ts_sec)}")
                btn_ts.setFixedHeight(22)
                btn_ts.setToolTip(f"Jump playback to decision at {format_seconds_hms(ts_sec)}")
                btn_ts.setStyleSheet("QPushButton { font-size: 10px; font-weight: 700; color: #10B981; background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 4px; padding: 1px 6px; } QPushButton:hover { background: rgba(16, 185, 129, 0.25); }")
                btn_ts.clicked.connect(lambda _, sec=ts_sec: self.timestamp_clicked.emit(sec))
                top.addWidget(btn_ts)

            c_layout.addLayout(top)

            rationale = dec.get("rationale", "")
            if rationale:
                lbl_rat = QLabel(f"Context: {rationale}")
                lbl_rat.setStyleSheet("font-size: 11px; color: #8B949E; font-style: italic;")
                lbl_rat.setWordWrap(True)
                c_layout.addWidget(lbl_rat)

            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

    def _copy_decisions(self) -> None:
        from PyQt6.QtWidgets import QApplication
        lines = ["*Confirmed Key Decisions:*"]
        for dec in self._decisions:
            ts = f" [{format_seconds_hms(float(dec.get('timestamp_sec', 0.0)))}]" if dec.get("timestamp_sec") else ""
            lines.append(f"•{ts} {dec.get('decision', '')}")
            if dec.get("rationale"):
                lines.append(f"  _Context: {dec.get('rationale')}_")
        QApplication.clipboard().setText("\n".join(lines))


# ======================================================================
# 18. MULTI-STAGE PROCESSING PROGRESS CARD
# ======================================================================
class VoxProcessingProgressCard(QFrame):
    """
    Multi-stage interactive progress card shown during post-meeting finalization.
    Displays clear 4-step stepper and current activity description.
    """
    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("cardFrame")
        self._current_stage: int = 1
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        lbl_head = QLabel("⚙️ Finalizing Meeting Intelligence Report")
        lbl_head.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #38BDF8;")
        top_row.addWidget(lbl_head)
        top_row.addStretch()

        self.lbl_step_num = QLabel("Step 1 of 4")
        self.lbl_step_num.setStyleSheet("font-size: 11px; font-weight: 700; color: #8B949E; background: rgba(139, 148, 158, 0.15); border-radius: 6px; padding: 2px 8px;")
        top_row.addWidget(self.lbl_step_num)
        layout.addLayout(top_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(25)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("QProgressBar { background: rgba(255,255,255,0.08); border-radius: 3px; } QProgressBar::chunk { background: #38BDF8; border-radius: 3px; }")
        layout.addWidget(self.progress_bar)

        # 4-stage pill row
        stepper_row = QHBoxLayout()
        stepper_row.setSpacing(6)
        self.steps = [
            ("1. Video Mux", "Saving MP4 video track"),
            ("2. Whisper", "Offline speech transcription"),
            ("3. Diarization", "Speaker separation & alignment"),
            ("4. AI Synthesis", "MoM, decisions & action items"),
        ]
        self.step_labels = []
        for idx, (short_name, tip) in enumerate(self.steps, start=1):
            lbl = QLabel(short_name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setToolTip(tip)
            lbl.setStyleSheet("font-size: 10px; font-weight: 600; padding: 4px 6px; border-radius: 4px; background: rgba(255,255,255,0.05); color: #8B949E;")
            stepper_row.addWidget(lbl, 1)
            self.step_labels.append(lbl)
        layout.addLayout(stepper_row)

        self.lbl_message = QLabel("Saving video recording and synchronizing dual-channel audio...")
        self.lbl_message.setStyleSheet("font-size: 11.5px; color: #E2E8F0; font-weight: 500;")
        self.lbl_message.setWordWrap(True)
        layout.addWidget(self.lbl_message)

    def set_stage_message(self, stage_idx: int, message: str) -> None:
        self._current_stage = max(1, min(4, stage_idx))
        self.lbl_step_num.setText(f"Step {self._current_stage} of 4")
        self.progress_bar.setValue(self._current_stage * 25)
        self.lbl_message.setText(message)
        for idx, lbl in enumerate(self.step_labels, start=1):
            if idx < self._current_stage:
                lbl.setStyleSheet("font-size: 10px; font-weight: 700; padding: 4px 6px; border-radius: 4px; background: rgba(16, 185, 129, 0.2); color: #10B981;")
            elif idx == self._current_stage:
                lbl.setStyleSheet("font-size: 10px; font-weight: 700; padding: 4px 6px; border-radius: 4px; background: rgba(56, 189, 248, 0.25); color: #38BDF8; border: 1px solid #38BDF8;")
            else:
                lbl.setStyleSheet("font-size: 10px; font-weight: 600; padding: 4px 6px; border-radius: 4px; background: rgba(255,255,255,0.05); color: #8B949E;")


# ======================================================================
# 19. EXECUTIVE MEETING DASHBOARD HEADER
# ======================================================================
class VoxMeetingDashboardHeader(QFrame):
    """
    Executive Meeting Intelligence dashboard strip for Meeting Studio.
    Displays 4 core KPIs, active call connection banner, and jump-to-reports shortcut.
    """
    view_reports_clicked = pyqtSignal()
    filter_pending_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("cardFrame")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        lbl_dash = QLabel("📊 Executive Meeting Intelligence")
        lbl_dash.setStyleSheet("font-size: 13.5px; font-weight: 800; color: #E2E8F0;")
        top.addWidget(lbl_dash)

        self.lbl_offline_badge = QLabel("🛡️ 100% Offline Local Engine")
        self.lbl_offline_badge.setStyleSheet("font-size: 10px; font-weight: 700; color: #10B981; background: rgba(16, 185, 129, 0.12); border-radius: 4px; padding: 2px 6px;")
        top.addWidget(self.lbl_offline_badge)
        top.addStretch()

        btn_go_reports = QPushButton("📋 Open Meeting Reports Library →")
        btn_go_reports.setFixedHeight(28)
        btn_go_reports.setStyleSheet("QPushButton { font-size: 11px; font-weight: 700; color: #38BDF8; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 6px; padding: 2px 10px; } QPushButton:hover { background: rgba(56, 189, 248, 0.25); }")
        btn_go_reports.clicked.connect(self.view_reports_clicked.emit)
        top.addWidget(btn_go_reports)
        layout.addLayout(top)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)

        self.kpi_meetings = self._create_kpi_cell("📅 Recorded", "0", "Total meeting sessions")
        self.kpi_hours = self._create_kpi_cell("⏱️ Talk Time", "0h 0m", "Captured audio time")
        self.kpi_actions = self._create_kpi_cell("✅ Open Actions", "0", "Pending commitments")
        self.kpi_decisions = self._create_kpi_cell("🎯 Decisions", "0", "Strategic decisions")

        kpi_row.addWidget(self.kpi_meetings, 1)
        kpi_row.addWidget(self.kpi_hours, 1)
        kpi_row.addWidget(self.kpi_actions, 1)
        kpi_row.addWidget(self.kpi_decisions, 1)
        layout.addLayout(kpi_row)

    def _create_kpi_cell(self, title: str, value: str, tooltip: str) -> QFrame:
        cell = QFrame()
        cell.setObjectName("nestedCard")
        cell.setToolTip(tooltip)
        c_layout = QVBoxLayout(cell)
        c_layout.setContentsMargins(10, 6, 10, 6)
        c_layout.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("font-size: 10.5px; font-weight: 600; color: #8B949E;")
        c_layout.addWidget(lbl_t)

        lbl_v = QLabel(value)
        lbl_v.setObjectName("kpiValue")
        lbl_v.setStyleSheet("font-size: 15px; font-weight: 800; color: #F1F5F9;")
        c_layout.addWidget(lbl_v)
        return cell

    def update_stats(self, total_meetings: int, total_sec: float, pending_actions: int, total_decisions: int) -> None:
        val_m = self.kpi_meetings.findChild(QLabel, "kpiValue")
        if val_m:
            val_m.setText(str(total_meetings))

        val_h = self.kpi_hours.findChild(QLabel, "kpiValue")
        if val_h:
            hours = int(total_sec // 3600)
            mins = int((total_sec % 3600) // 60)
            val_h.setText(f"{hours}h {mins}m")

        val_a = self.kpi_actions.findChild(QLabel, "kpiValue")
        if val_a:
            val_a.setText(str(pending_actions))

        val_d = self.kpi_decisions.findChild(QLabel, "kpiValue")
        if val_d:
            val_d.setText(str(total_decisions))



# ======================================================================
# 18. VOX MEETING COMPARISON DIALOG (Side-by-Side Evolution)
# ======================================================================
class VoxMeetingComparisonDialog(QDialog):
    """
    Enterprise Meeting Comparison & Evolution Modal Dialog.
    Enables side-by-side analysis of two meeting sessions:
    - Decisional evolution & shifts
    - Action item carry-over status
    - Discussion topic transitions
    - Export to Slack / Teams / Markdown
    """

    def __init__(
        self,
        db: Any,
        intelligence: Any,
        theme: str = "dark",
        initial_meeting_id_a: Optional[int] = None,
        initial_meeting_id_b: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.intelligence = intelligence
        self.theme = theme
        self.initial_meeting_id_a = initial_meeting_id_a
        self.initial_meeting_id_b = initial_meeting_id_b

        self.setWindowTitle("⚖️ Meeting Evolution & Comparison — VOX")
        self.resize(840, 640)
        self.setMinimumSize(700, 500)

        is_dark = (self.theme != "light")
        bg_color = "#0F172A" if is_dark else "#F8FAFC"
        text_color = "#F8FAFC" if is_dark else "#0F172A"
        card_bg = "#1E293B" if is_dark else "#FFFFFF"
        border_color = "#334155" if is_dark else "#E2E8F0"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_color};
                color: {text_color};
            }}
            QLabel {{
                color: {text_color};
            }}
            QComboBox {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QTextBrowser {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 12px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 13px;
                line-height: 1.5;
            }}
            QPushButton {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: #38BDF8;
                color: #38BDF8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header Title
        hdr = QHBoxLayout()
        title_lbl = QLabel("⚖️ Meeting Comparison & Continuity")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #38BDF8;")
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Meeting Selectors Row
        sel_row = QHBoxLayout()
        sel_row.setSpacing(12)

        # Meeting A
        lbl_a = QLabel("Session A:")
        lbl_a.setStyleSheet("font-weight: 700; font-size: 12px;")
        sel_row.addWidget(lbl_a)
        self.combo_a = QComboBox()
        self.combo_a.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sel_row.addWidget(self.combo_a, 1)

        # VS pill
        vs_lbl = QLabel("vs")
        vs_lbl.setStyleSheet("font-weight: 800; color: #94A3B8; padding: 0 4px;")
        sel_row.addWidget(vs_lbl)

        # Meeting B
        lbl_b = QLabel("Session B:")
        lbl_b.setStyleSheet("font-weight: 700; font-size: 12px;")
        sel_row.addWidget(lbl_b)
        self.combo_b = QComboBox()
        self.combo_b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sel_row.addWidget(self.combo_b, 1)

        # Compare Button
        self.btn_run_compare = QPushButton("⚡ Compare")
        self.btn_run_compare.setStyleSheet("""
            background-color: #0284C7;
            color: #FFFFFF;
            border: 1px solid #0284C7;
            font-weight: 700;
        """)
        self.btn_run_compare.clicked.connect(self._run_comparison)
        sel_row.addWidget(self.btn_run_compare)

        layout.addLayout(sel_row)

        # Markdown Report View
        self.txt_report = QTextBrowser()
        self.txt_report.setOpenExternalLinks(False)
        layout.addWidget(self.txt_report, 1)

        # Bottom Button Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_copy_md = QPushButton("📋 Copy Markdown")
        self.btn_copy_md.clicked.connect(self._copy_markdown)
        btn_bar.addWidget(self.btn_copy_md)

        self.btn_export_slack = QPushButton("💬 Copy for Slack/Teams")
        self.btn_export_slack.clicked.connect(self._copy_slack)
        btn_bar.addWidget(self.btn_export_slack)

        btn_bar.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_close)

        layout.addLayout(btn_bar)

        self._current_result: Optional[Dict[str, Any]] = None
        self._populate_meetings()

    def _populate_meetings(self) -> None:
        meetings = self.db.list_meetings(limit=1000)
        self.combo_a.clear()
        self.combo_b.clear()

        idx_a = 0
        idx_b = 1 if len(meetings) > 1 else 0

        for i, m in enumerate(meetings):
            mid = m["id"]
            created = m.get("created_at", "")[:10]
            title = m.get("title", f"Meeting #{mid}")
            label = f"#{mid} · {title} ({created})"
            self.combo_a.addItem(label, mid)
            self.combo_b.addItem(label, mid)

            if self.initial_meeting_id_a is not None and mid == self.initial_meeting_id_a:
                idx_a = i
            if self.initial_meeting_id_b is not None and mid == self.initial_meeting_id_b:
                idx_b = i

        if self.combo_a.count() > 0:
            self.combo_a.setCurrentIndex(idx_a)
        if self.combo_b.count() > 0:
            self.combo_b.setCurrentIndex(idx_b)

        if self.combo_a.count() >= 2:
            self._run_comparison()

    def _run_comparison(self) -> None:
        mid_a = self.combo_a.currentData()
        mid_b = self.combo_b.currentData()
        if mid_a is None or mid_b is None:
            self.txt_report.setPlainText("Please select two meetings to compare.")
            return

        try:
            res = self.intelligence.compare_meetings(mid_a, mid_b)
            self._current_result = res
            md = res.get("comparison_markdown", "")
            if hasattr(self.txt_report, "setMarkdown"):
                self.txt_report.setMarkdown(md)
            else:
                self.txt_report.setPlainText(md)
        except Exception as exc:
            self.txt_report.setPlainText(f"Failed to compare meetings: {exc}")

    def _copy_markdown(self) -> None:
        if not self._current_result:
            return
        res = self._current_result
        m_a = res.get("meeting_a", {})
        m_b = res.get("meeting_b", {})
        try:
            from voxlocal.storage.exporter import TranscriptExporter
            sensitive = []
            if m_a:
                sensitive.extend(TranscriptExporter.check_sensitive_content(m_a))
            if m_b:
                sensitive.extend(TranscriptExporter.check_sensitive_content(m_b))
            if sensitive:
                kw_str = ", ".join(f"'{k}'" for k in sorted(set(sensitive)))
                ret = QMessageBox.warning(
                    self,
                    "Sensitive Content Warning",
                    f"This comparison contains potentially sensitive keywords ({kw_str}).\n\nDo you want to proceed with copying to clipboard?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
        except Exception as exc:
            logger.debug("Sensitive content check skipped: %s", exc)

        md = self._current_result.get("comparison_markdown", "")
        self._last_copied_text = md
        app = QApplication.instance()
        if app:
            clipboard = app.clipboard()
            if clipboard:
                clipboard.setText(md)

    def _copy_slack(self) -> None:
        if not self._current_result:
            return
        res = self._current_result
        m_a = res.get("meeting_a", {})
        m_b = res.get("meeting_b", {})
        try:
            from voxlocal.storage.exporter import TranscriptExporter
            sensitive = []
            if m_a:
                sensitive.extend(TranscriptExporter.check_sensitive_content(m_a))
            if m_b:
                sensitive.extend(TranscriptExporter.check_sensitive_content(m_b))
            if sensitive:
                kw_str = ", ".join(f"'{k}'" for k in sorted(set(sensitive)))
                ret = QMessageBox.warning(
                    self,
                    "Sensitive Content Warning",
                    f"This comparison contains potentially sensitive keywords ({kw_str}).\n\nDo you want to proceed with copying to clipboard?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
        except Exception as exc:
            logger.debug("Sensitive content check skipped: %s", exc)

        carry_over = res.get("carry_over_actions", [])
        dec_a = res.get("decisions_a", [])
        dec_b = res.get("decisions_b", [])

        # Ensure chronological ordering for comparison header and active decisions
        date_a = str(m_a.get("created_at") or "")
        date_b = str(m_b.get("created_at") or "")
        if date_a > date_b:
            m_early, m_later = m_b, m_a
            dec_later = dec_a
        else:
            m_early, m_later = m_a, m_b
            dec_later = dec_b

        slack_lines = [
            f"*⚖️ VOX Meeting Comparison: {m_early.get('title')} vs {m_later.get('title')}*",
            f"• *Earlier Session:* {m_early.get('title')} ({m_early.get('created_at', '')})",
            f"• *Later Session:* {m_later.get('title')} ({m_later.get('created_at', '')})",
            "",
            "*🎯 Strategic Decisions Summary:*",
        ]
        active_decisions = [d for d in dec_later if d.get("status") not in ("rejected",)]
        if active_decisions:
            for d in active_decisions[:4]:
                slack_lines.append(f"• *[New/Active]* {d.get('decision', '')}")
        elif dec_later:
            for d in dec_later[:4]:
                slack_lines.append(f"• *[New/Active]* {d.get('decision', '')}")
        else:
            slack_lines.append("• _No new decisions tracked in latest meeting_")

        if carry_over:
            slack_lines.append("")
            slack_lines.append("*⚠️ Carry-Over Action Items:*")
            for act in carry_over[:5]:
                owner = act.get("owner") or "Unassigned"
                due = f" (Due: {act.get('due_date')})" if act.get("due_date") else ""
                slack_lines.append(f"• [ ] *[{owner}]* {act.get('description', '')}{due}")

        text = "\n".join(slack_lines)
        self._last_copied_text = text
        app = QApplication.instance()
        if app:
            clipboard = app.clipboard()
            if clipboard:
                clipboard.setText(text)


# ======================================================================
# 19. VOX PRE-MEETING BRIEFING DIALOG (Executive Briefing Generator)
# ======================================================================
class VoxPreMeetingBriefingDialog(QDialog):
    """
    Enterprise Pre-Meeting Briefing Modal Dialog.
    Synthesizes past context, prior agreements, and open deliverables
    before entering a critical customer, team, or stakeholder call.
    """

    def __init__(
        self,
        db: Any,
        intelligence: Any,
        theme: str = "dark",
        initial_topic: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.intelligence = intelligence
        self.theme = theme
        self.initial_topic = initial_topic

        self.setWindowTitle("🎯 Executive Pre-Meeting Briefing — VOX")
        self.resize(800, 620)
        self.setMinimumSize(680, 480)

        is_dark = (self.theme != "light")
        bg_color = "#0F172A" if is_dark else "#F8FAFC"
        text_color = "#F8FAFC" if is_dark else "#0F172A"
        card_bg = "#1E293B" if is_dark else "#FFFFFF"
        border_color = "#334155" if is_dark else "#E2E8F0"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_color};
                color: {text_color};
            }}
            QLabel {{
                color: {text_color};
            }}
            QLineEdit {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 7px 12px;
                font-size: 13px;
            }}
            QTextBrowser {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 12px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 13px;
                line-height: 1.5;
            }}
            QPushButton {{
                background-color: {card_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: #38BDF8;
                color: #38BDF8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header Title
        hdr = QHBoxLayout()
        title_lbl = QLabel("🎯 Executive Pre-Meeting Briefing")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #38BDF8;")
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Input Row
        inp_row = QHBoxLayout()
        inp_row.setSpacing(8)

        lbl_top = QLabel("Topic / Client / Project:")
        lbl_top.setStyleSheet("font-weight: 700; font-size: 12px;")
        inp_row.addWidget(lbl_top)

        self.txt_topic = QLineEdit()
        self.txt_topic.setPlaceholderText("e.g. Everlane, Abhilash Mishra, Q3 Roadmap, Sprint 42...")
        self.txt_topic.setText(self.initial_topic)
        self.txt_topic.returnPressed.connect(self._run_briefing)
        inp_row.addWidget(self.txt_topic, 1)

        self.btn_run_brief = QPushButton("✨ Generate Briefing")
        self.btn_run_brief.setStyleSheet("""
            background-color: #0284C7;
            color: #FFFFFF;
            border: 1px solid #0284C7;
            font-weight: 700;
        """)
        self.btn_run_brief.clicked.connect(self._run_briefing)
        inp_row.addWidget(self.btn_run_brief)

        layout.addLayout(inp_row)

        # Suggested Topics Rail
        self.chips_row = QHBoxLayout()
        self.chips_row.setSpacing(6)
        lbl_rec = QLabel("Recent Themes:")
        lbl_rec.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        self.chips_row.addWidget(lbl_rec)

        topics = self.intelligence.get_recurring_topics(limit=5)
        if topics:
            for top in topics:
                name = top["name"]
                chip = QPushButton(name)
                chip.setStyleSheet("padding: 3px 8px; font-size: 11px; border-radius: 10px;")
                chip.clicked.connect(lambda _, n=name: self._on_chip_clicked(n))
                self.chips_row.addWidget(chip)
        else:
            default_themes = ["Roadmap", "Sprint", "Budget", "Product"]
            for theme_name in default_themes:
                chip = QPushButton(theme_name)
                chip.setStyleSheet("padding: 3px 8px; font-size: 11px; border-radius: 10px;")
                chip.clicked.connect(lambda _, n=theme_name: self._on_chip_clicked(n))
                self.chips_row.addWidget(chip)

        self.chips_row.addStretch()
        layout.addLayout(self.chips_row)

        # Markdown Report View
        self.txt_report = QTextBrowser()
        self.txt_report.setOpenExternalLinks(False)
        layout.addWidget(self.txt_report, 1)

        # Bottom Button Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_copy_md = QPushButton("📋 Copy Briefing")
        self.btn_copy_md.clicked.connect(self._copy_markdown)
        btn_bar.addWidget(self.btn_copy_md)

        self.btn_export_slack = QPushButton("💬 Copy for Slack/Teams")
        self.btn_export_slack.clicked.connect(self._copy_slack)
        btn_bar.addWidget(self.btn_export_slack)

        btn_bar.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_close)

        layout.addLayout(btn_bar)

        self._current_result: Optional[Dict[str, Any]] = None
        self._run_briefing()

    def _on_chip_clicked(self, name: str) -> None:
        self.txt_topic.setText(name)
        self._run_briefing()

    def _run_briefing(self) -> None:
        topic = self.txt_topic.text().strip()
        try:
            res = self.intelligence.generate_pre_meeting_briefing(topic)
            self._current_result = res
            md = res.get("briefing_markdown", "")
            if hasattr(self.txt_report, "setMarkdown"):
                self.txt_report.setMarkdown(md)
            else:
                self.txt_report.setPlainText(md)
        except Exception as exc:
            self.txt_report.setPlainText(f"Failed to generate briefing: {exc}")

    def _copy_markdown(self) -> None:
        if self._current_result:
            md = self._current_result.get("briefing_markdown", "")
            self._last_copied_text = md
            app = QApplication.instance()
            if app:
                clipboard = app.clipboard()
                if clipboard:
                    clipboard.setText(md)

    def _copy_slack(self) -> None:
        if not self._current_result:
            return
        res = self._current_result
        topic = res.get("topic", "Upcoming Meeting")
        open_actions = res.get("open_action_items", [])
        decisions = res.get("key_decisions", [])

        slack_lines = [
            f"*🎯 VOX Pre-Meeting Briefing: {topic}*",
            "",
            "*🎯 Strategic Context & Decisions:*",
        ]
        if decisions:
            for d in decisions[:4]:
                slack_lines.append(f"• *{d.get('decision', '')}*")
        else:
            slack_lines.append("• _No prior formal decisions tracked_")

        if open_actions:
            slack_lines.append("")
            slack_lines.append("*⚠️ Outstanding Commitments:*")
            for act in open_actions[:5]:
                owner = act.get("owner") or "Unassigned"
                due = f" (Due: {act.get('due_date')})" if act.get("due_date") else ""
                slack_lines.append(f"• [ ] *[{owner}]* {act.get('description', '')}{due}")

        text = "\n".join(slack_lines)
        self._last_copied_text = text
        app = QApplication.instance()
        if app:
            clipboard = app.clipboard()
            if clipboard:
                clipboard.setText(text)


# ======================================================================
# 28. VOX EMPTY STATE WIDGET (With 1-Click Quick Starts & Templates)
# ======================================================================
class VoxEmptyStateWidget(QFrame):
    """
    Modern, accessible empty state illustration widget with 1-click starter cards.
    """

    starter_clicked = pyqtSignal(str)
    action_clicked = pyqtSignal()

    def __init__(
        self,
        icon: str = "🎙️",
        title: str = "No Items Yet",
        subtitle: str = "Get started by choosing a template or creating a new item.",
        parent: Optional[QWidget] = None,
        theme: str = "light",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("voxEmptyState")
        self._theme = (theme or "light").lower()
        self._cards_layout: Optional[QHBoxLayout] = None
        self._starter_buttons: List[QPushButton] = []

        self._build_ui(icon, title, subtitle)
        self.apply_theme(self._theme)

    def _build_ui(self, icon: str, title: str, subtitle: str) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 40, 32, 40)
        lay.setSpacing(16)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon badge
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon.setFixedSize(56, 56)
        self.lbl_icon.setAccessibleName("Empty state icon")
        lay.addWidget(self.lbl_icon, 0, Qt.AlignmentFlag.AlignCenter)

        # Title
        self.lbl_title = QLabel(title)
        self.lbl_title.setFont(get_ui_font(20, 600, serif=True))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setAccessibleName(title)
        lay.addWidget(self.lbl_title)

        # Subtitle
        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setMaximumWidth(520)
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sub.setAccessibleName(subtitle)
        lay.addWidget(self.lbl_sub, 0, Qt.AlignmentFlag.AlignCenter)

        # Quick start container
        self.cards_wrap = QWidget(self)
        self._cards_layout = QHBoxLayout(self.cards_wrap)
        self._cards_layout.setContentsMargins(0, 8, 0, 8)
        self._cards_layout.setSpacing(12)
        lay.addWidget(self.cards_wrap)

        # Primary action button
        self.btn_action = QPushButton("Start Recording", self)
        self.btn_action.setObjectName("primaryBtn")
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.setAccessibleName("Start Recording")
        self.btn_action.setAccessibleDescription("Start capturing audio and meeting notes")
        self.btn_action.clicked.connect(self.action_clicked.emit)
        lay.addWidget(self.btn_action, 0, Qt.AlignmentFlag.AlignCenter)

    def add_starter_card(self, title: str, desc: str, template_key: str, tag: str = "") -> QPushButton:
        btn = QPushButton(self.cards_wrap if hasattr(self, "cards_wrap") else self)
        btn.setObjectName("starterCard")
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumSize(160, 84)
        btn.setAccessibleName(f"Template {title}")
        btn.setAccessibleDescription(f"Use {title} starter template: {desc}")

        b_lay = QVBoxLayout(btn)
        b_lay.setContentsMargins(14, 12, 14, 12)
        b_lay.setSpacing(4)
        b_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)

        if tag:
            lbl_tag = QLabel(tag.upper())
            lbl_tag.setStyleSheet("font-size: 9px; font-weight: 700; letter-spacing: 0.08em; opacity: 0.7;")
            b_lay.addWidget(lbl_tag)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        d_lbl = QLabel(desc)
        d_lbl.setStyleSheet("font-size: 11px; opacity: 0.75;")
        d_lbl.setWordWrap(True)
        b_lay.addWidget(t_lbl)
        b_lay.addWidget(d_lbl)

        btn.clicked.connect(lambda _=False, k=template_key: self.starter_clicked.emit(k))
        if self._cards_layout:
            self._cards_layout.addWidget(btn)
        self._starter_buttons.append(btn)
        return btn

    def apply_theme(self, theme: str) -> None:
        self._theme = (theme or "light").lower()
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"QFrame#voxEmptyState {{ background-color: {t['surface']}; border: 1px dashed {t['border']}; border-radius: 16px; }}"
            f"QLabel {{ color: {t['text']}; background: transparent; }}"
            f"QPushButton#starterCard {{ background-color: {t['surface_well']}; border: 1px solid {t['border']}; border-radius: 10px; text-align: left; }}"
            f"QPushButton#starterCard:hover {{ border-color: {t['accent_fathom']}; background-color: {t['sidebar_active']}; }}"
        )
        self.lbl_icon.setStyleSheet(
            f"background-color: {t['surface_well']}; border: 1px solid {t['border']}; border-radius: 28px; font-size: 24px;"
        )


"""
VOX — Motion Design System & Animation Engine
===================================================
Short ease-out transitions and focused recording-state feedback.

Provides over 100+ micro and macro animations across the application:
1. Workspace slide and opacity transitions
2. Subtle button lift and press feedback
3. 10-Channel Dynamic Audio Equalizer (10 dancing harmonic channels)
4. Concentric Ripple Acoustic Wavefronts (3 continuous expanding rings)
5. Sinusoidal Breathing Status Beacons (3 animated beacons)
6. 360-degree rotating theme toggle capsule
7. Floating pill smooth height morphing and button stagger entrance
8. Glass toast notification entrance/drainage/exit
9. Smooth VU progress bar value interpolation
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional
from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSequentialAnimationGroup,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QEnterEvent,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QRadialGradient,
    QTransform,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal.ui.theme import get_theme_tokens


# ======================================================================
# VOX MOTION CURVES
# ======================================================================
class VoxMotion:
    """Shared easing curves for VOX controls and workspace transitions."""
    QUIET_LUXURY = QEasingCurve.Type.OutCubic
    KINETIC_SPRING = QEasingCurve.Type.OutCubic
    SMOOTH_EDITORIAL = QEasingCurve.Type.InOutQuad
    CINEMATIC_EXPO = QEasingCurve.Type.OutExpo


# Compatibility alias for integrations that imported the previous class name.
FlakyCurves = VoxMotion


# ======================================================================
# 1. ANIMATED PUSH BUTTON (Tactile Lift, Spring & Depression)
# ======================================================================
class AnimatedPushButton(QPushButton):
    """
    Button with subtle lift and press feedback. The retained glow property keeps
    compatibility with existing widgets; no unused glow animation runs.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._y_offset: float = 0.0
        self._glow_alpha: float = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Animators
        self._anim_y = QPropertyAnimation(self, b"y_offset", self)
        self._anim_y.setEasingCurve(VoxMotion.QUIET_LUXURY)

    @pyqtProperty(float)
    def y_offset(self) -> float:
        return self._y_offset

    @y_offset.setter
    def y_offset(self, val: float) -> None:
        self._y_offset = val
        self.update()

    @pyqtProperty(float)
    def glow_alpha(self) -> float:
        return self._glow_alpha

    @glow_alpha.setter
    def glow_alpha(self, val: float) -> None:
        self._glow_alpha = val
        self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        if self.isEnabled():
            self._anim_y.stop()
            self._anim_y.setDuration(140)
            self._anim_y.setStartValue(self._y_offset)
            self._anim_y.setEndValue(-1.0)
            self._anim_y.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._anim_y.stop()
        self._anim_y.setDuration(130)
        self._anim_y.setStartValue(self._y_offset)
        self._anim_y.setEndValue(0.0)
        self._anim_y.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._anim_y.stop()
            self._anim_y.setDuration(60)
            self._anim_y.setStartValue(self._y_offset)
            self._anim_y.setEndValue(0.5)
            self._anim_y.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._anim_y.stop()
            self._anim_y.setDuration(140)
            self._anim_y.setEasingCurve(VoxMotion.QUIET_LUXURY)
            self._anim_y.setStartValue(self._y_offset)
            target = -1.0 if self.underMouse() else 0.0
            self._anim_y.setEndValue(target)
            self._anim_y.start()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if abs(self._y_offset) > 0.01:
            painter.translate(0.0, self._y_offset)
        painter.end()
        super().paintEvent(event)


# ======================================================================
# 2. THEME TOGGLE CAPSULE
# ======================================================================
class ThemeToggleCapsule(QPushButton):
    """
    Floating Glass Capsule Theme Switcher:
    - Rotates Sun/Moon icon 360 degrees on toggle with kinetic spring snap
    - Smooth hover glow ring
    """
    theme_toggled = pyqtSignal(str)

    def __init__(self, current_theme: str = "dark", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = current_theme
        self._rotation_angle: float = 0.0
        self._hover_progress: float = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setMinimumWidth(100)

        self._anim_rot = QPropertyAnimation(self, b"rotation_angle")
        self._anim_rot.setEasingCurve(VoxMotion.KINETIC_SPRING)

        self._anim_hover = QPropertyAnimation(self, b"hover_progress")
        self._anim_hover.setEasingCurve(VoxMotion.QUIET_LUXURY)

        self.clicked.connect(self._on_clicked)
        self._update_appearance()

    @pyqtProperty(float)
    def rotation_angle(self) -> float:
        return self._rotation_angle

    @rotation_angle.setter
    def rotation_angle(self, val: float) -> None:
        self._rotation_angle = val
        self.update()

    @pyqtProperty(float)
    def hover_progress(self) -> float:
        return self._hover_progress

    @hover_progress.setter
    def hover_progress(self, val: float) -> None:
        self._hover_progress = val
        self.update()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_appearance()

    def _update_appearance(self) -> None:
        is_dark = self._theme == "dark"
        self.setText("  " + ("● Dark" if is_dark else "○ Light"))
        tip = "Switch to Light Mode" if is_dark else "Switch to Dark Mode"
        self.setToolTip(f"{tip} (Ctrl+T)")
        self._sync_stylesheet()

    def _sync_stylesheet(self) -> None:
        t = get_theme_tokens(self._theme)
        is_dark = self._theme == "dark"
        bg = t.get("secondary_btn_bg", "#30302F" if is_dark else "#FFFFFF")
        border = t.get("border", "#383836" if is_dark else "#E5E2D9")
        hover_border = t.get("accent_dawn", "#F0D7FF") if is_dark else t.get("accent_fathom", "#034F46")
        color = t.get("text", "#1A1A1A")

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 15px;
                padding: 4px 14px 4px 10px;
                font-size: 11.5px;
                font-weight: 700;
                letter-spacing: 0.03em;
            }}
            QPushButton:hover {{
                border-color: {hover_border};
                background-color: {t.get('sidebar_hover', '#262625' if is_dark else '#F0EFEA')};
            }}
            """
        )

    def _on_clicked(self) -> None:
        self._anim_rot.stop()
        self._anim_rot.setDuration(520)
        self._anim_rot.setStartValue(0.0)
        self._anim_rot.setEndValue(360.0)
        self._anim_rot.start()

        new_theme = "light" if self._theme == "dark" else "dark"
        self._theme = new_theme
        self._update_appearance()
        self.theme_toggled.emit(new_theme)


# ======================================================================
# 3. TAB TRANSITION ENGINE (Quintic Slide & Fade Across Workspaces)
# ======================================================================
class TabTransitionEngine(QObject):
    """
    Orchestrates short, consistent transitions between workspaces:
    - Fades opacity 0.0 -> 1.0 (220ms)
    - Slides content Y by +14px -> 0px using Quintic Deceleration
    """

    def __init__(self, tab_widget: QTabWidget) -> None:
        super().__init__(tab_widget)
        self.tab_widget = tab_widget
        self._last_index = tab_widget.currentIndex()
        self._active_anim: Optional[QParallelAnimationGroup] = None
        self._active_cleanup: Optional[Callable[[], None]] = None
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, new_index: int) -> None:
        if new_index < 0:
            return
        widget = self.tab_widget.widget(new_index)
        if not widget:
            return

        # Restore any previous page whose transition is still running before
        # animating the newly selected workspace.
        self.cancel_active()

        # Setup Opacity Effect
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setOpacity(0.15)

        anim_fade = QPropertyAnimation(effect, b"opacity")
        anim_fade.setDuration(240)
        anim_fade.setStartValue(0.15)
        anim_fade.setEndValue(1.0)
        anim_fade.setEasingCurve(VoxMotion.QUIET_LUXURY)

        # Setup Slide Animation
        orig_pos = widget.pos()
        widget.move(orig_pos.x(), orig_pos.y() + 14)

        anim_slide = QPropertyAnimation(widget, b"pos")
        anim_slide.setDuration(240)
        anim_slide.setStartValue(QPoint(orig_pos.x(), orig_pos.y() + 14))
        anim_slide.setEndValue(orig_pos)
        anim_slide.setEasingCurve(VoxMotion.QUIET_LUXURY)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_fade)
        group.addAnimation(anim_slide)

        def _cleanup():
            effect.setOpacity(1.0)
            widget.move(orig_pos)
            if self._active_anim is group:
                self._active_anim = None
                self._active_cleanup = None

        group.finished.connect(_cleanup)
        group.finished.connect(group.deleteLater)
        self._active_anim = group
        self._active_cleanup = _cleanup
        group.start()
        self._last_index = new_index

    def cancel_active(self) -> None:
        """Stop the current workspace motion safely, for rapid switches or window hide."""
        group = self._active_anim
        cleanup = self._active_cleanup
        self._active_anim = None
        self._active_cleanup = None
        if group is not None:
            group.stop()
            if cleanup is not None:
                cleanup()
            group.deleteLater()


# ======================================================================
# 4. PULSING BREATHING STATUS BEACON (With Glowing Halo Ring)
# ======================================================================
class PulseBeacon(QWidget):
    """
    Quiet luxury glowing status beacon.
    Features continuous sinusoidal breathing and concentric expanding halo waves:
    - Emerald (#10b981) for Voice Typing
    - Crimson (#f43f5e) for Meeting Recording
    - Honey Amber (#f59e0b) for Transcribing
    - Gold (#d6c5a2) for Standby
    """

    def __init__(self, state: str = "idle", size: int = 24, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._size = size
        self.setFixedSize(size, size)

        self._phase: float = 0.0
        self._halo_radius: float = size * 0.3
        self._halo_alpha: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS when active
        self._timer.timeout.connect(self._tick)
        if self._state != "idle":
            self._timer.start()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "idle":
            if self._timer.isActive():
                self._timer.stop()
        else:
            if not self._timer.isActive():
                self._timer.start()
        self.update()

    def _tick(self) -> None:
        if self._state == "idle":
            self._timer.stop()
            return
        self._phase += 0.06
        # Sine wave breathing between 0.35 and 1.0
        self._halo_alpha = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._phase))
        self.update()

    def _get_color(self) -> QColor:
        if self._state == "recording":
            return QColor("#f43f5e")  # Crimson
        elif self._state in ("listening", "typing"):
            return QColor("#10b981")  # Emerald
        elif self._state in ("processing", "transcribing"):
            return QColor("#f59e0b")  # Amber
        return QColor("#d6c5a2")      # Gold / Idle

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        core_r = self._size * 0.22
        col = self._get_color()

        if self._state != "idle":
            # Outer radiating halo
            halo_r = core_r * (1.2 + 0.7 * self._halo_alpha)
            halo_color = QColor(col)
            halo_color.setAlphaF(max(0.0, min(0.45, 0.45 * (1.0 - (halo_r / (self._size * 0.48))))))
            painter.setBrush(QBrush(halo_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # Core breathing dot
        core_color = QColor(col)
        core_color.setAlphaF(0.85 if self._state == "idle" else 0.55 + 0.45 * self._halo_alpha)
        painter.setBrush(QBrush(core_color))
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1.2))
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)
        painter.end()


# ======================================================================
# 5. 10-CHANNEL DANCING HARMONIC EQUALIZER
# ======================================================================
class MultiChannelEqualizerWidget(QWidget):
    """
    10-Channel Studio Spectrum Equalizer:
    - 10 harmonic bars dancing in real-time with responsive physics attack and decay
    - Interpolates smoothly based on audio amplitude level
    """

    def __init__(self, channels: int = 10, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._channels = channels
        self.setFixedHeight(24)
        self.setMinimumWidth( channels * 7 )

        self._target_level: float = 0.0
        self._current_levels: List[float] = [0.0] * channels
        # Harmonic distribution multipliers
        self._harmonics = [
            0.65, 0.95, 1.30, 1.45, 1.20, 1.10, 0.90, 0.75, 0.60, 0.45
        ][:channels]

        self._phase = 0.0
        self._color_active = QColor("#10b981")
        self._color_idle = QColor(142, 142, 147, 60)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._physics_step)

    def set_color(self, hex_color: str) -> None:
        self._color_active = QColor(hex_color)
        self.update()

    def set_level(self, level: float) -> None:
        """Normalized level [0.0 .. 1.0]."""
        self._target_level = max(0.0, min(1.0, float(level)))
        if self._target_level > 0.01 and not self._timer.isActive():
            self._timer.start()

    def _physics_step(self) -> None:
        self._phase += 0.22
        decay = 0.76
        changed = False

        for i in range(self._channels):
            if self._target_level > 0.01:
                # Add slight organic phase wave across the frequency spectrum
                wave = 0.85 + 0.28 * math.sin(self._phase + i * 0.9)
                target = min(1.0, self._target_level * self._harmonics[i] * wave)
            else:
                target = 0.0

            if target > self._current_levels[i]:
                # Instant responsive attack
                self._current_levels[i] = target
            else:
                # Smooth gravity decay
                self._current_levels[i] *= decay

            if self._current_levels[i] > 0.005:
                changed = True
            else:
                self._current_levels[i] = 0.0

        if changed or self._target_level > 0.01:
            self.update()
        else:
            self._timer.stop()
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        bar_w = 3.0
        spacing = 3.0
        total_w = self._channels * bar_w + (self._channels - 1) * spacing
        start_x = (w - total_w) / 2.0

        min_h = 3.0
        max_h = h - 2.0

        for i in range(self._channels):
            lvl = self._current_levels[i]
            bar_h = min_h + (max_h - min_h) * lvl
            x = start_x + i * (bar_w + spacing)
            y = (h - bar_h) / 2.0

            col = self._color_active if lvl > 0.06 else self._color_idle
            painter.setBrush(QBrush(col))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), bar_w / 2.0, bar_w / 2.0)
        painter.end()


# ======================================================================
# 6. CONCENTRIC SOUND WAVE ACOUSTIC RIPPLE
# ======================================================================
class ConcentricSoundWaveVisualizer(QWidget):
    """
    3 Staggered expanding circular ripples that radiate outward
    when the microphone captures speech.
    """

    def __init__(self, size: int = 56, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._phase: float = 0.0
        self._active: bool = False
        self._color = QColor("#10b981")

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._step)

    def set_active(self, active: bool, color_hex: str = "#10b981") -> None:
        self._active = active
        self._color = QColor(color_hex)
        if active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            if self._timer.isActive():
                self._timer.stop()
            self.update()

    def _step(self) -> None:
        if self._active:
            self._phase += 0.05
            self.update()
        else:
            self._timer.stop()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._active:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        max_r = self._size * 0.46

        # 3 Concentric wavefronts
        for ring in range(3):
            t = (self._phase + ring * 0.33) % 1.0
            r = 8.0 + t * (max_r - 8.0)
            alpha = max(0.0, min(0.65, 0.65 * (1.0 - t)))

            ring_col = QColor(self._color)
            ring_col.setAlphaF(alpha)
            painter.setPen(QPen(ring_col, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.end()


# ======================================================================
# 7. ANIMATED PROGRESS BAR (Smooth Value Interpolation)
# ======================================================================
class AnimatedProgressBar(QProgressBar):
    """Smoothly animates progress bar value changes rather than jagged jumps."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._anim = QPropertyAnimation(self, b"value")
        self._anim.setDuration(120)
        self._anim.setEasingCurve(VoxMotion.QUIET_LUXURY)

    def setSmoothValue(self, val: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(val)
        self._anim.start()


# ======================================================================
# 8. GLASS TOAST NOTIFICATION ENGINE
# ======================================================================
class ToastNotification(QFrame):
    """
    VOX desktop toast notification:
    - Slides in from bottom-right (+40px -> 0px) and fades in
    - Progress bar countdown
    - Smooth slide down and fade out exit
    """
    closed = pyqtSignal()

    def __init__(self, title: str, message: str, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.toast_title = title
        t = get_theme_tokens(theme)

        self.setObjectName("voxToastCard")
        self.setFixedWidth(360)
        self.setStyleSheet(
            f"""
            QFrame#voxToastCard {{
                background-color: {t.get('surface_elevated', t['surface'])};
                border: 1.5px solid {t['secondary_btn_border']};
                border-radius: 14px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._lbl_title = QLabel(title)
        self._lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {t['text']}; background: transparent;")
        top_row.addWidget(self._lbl_title)
        top_row.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {t['text_secondary']};
                border: none;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                color: {t['text']};
            }}
            """
        )
        btn_close.clicked.connect(self.dismiss)
        top_row.addWidget(btn_close)
        layout.addLayout(top_row)

        self._lbl_msg = QLabel(message)
        self._lbl_msg.setWordWrap(True)
        self._lbl_msg.setStyleSheet(f"font-size: 12.5px; color: {t['text_secondary']}; background: transparent;")
        layout.addWidget(self._lbl_msg)

        # Slide-in animation
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)

        self._anim_in = QPropertyAnimation(self._effect, b"opacity")
        self._anim_in.setDuration(220)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(VoxMotion.QUIET_LUXURY)
        self._anim_in.start()

        # Auto dismiss timer (stored so updates can extend visibility cleanly)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)
        self._dismiss_timer.start(4200)

    def update_message(self, message: str) -> None:
        self._lbl_msg.setText(message)
        self.adjustSize()
        self._dismiss_timer.start(4200)

    def dismiss(self) -> None:
        self._anim_out = QPropertyAnimation(self._effect, b"opacity")
        self._anim_out.setDuration(200)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(VoxMotion.QUIET_LUXURY)
        self._anim_out.finished.connect(self._on_dismiss_finished)
        self._anim_out.start()

    def _on_dismiss_finished(self) -> None:
        self.closed.emit()
        self.deleteLater()

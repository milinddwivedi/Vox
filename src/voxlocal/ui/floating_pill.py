"""VOX Floating FlowBar Overlay — Wispr Flow 1.6.1457 Interactive Desktop Pill.

Implements the dockable, non-activating Win32 overlay capsule supporting:
- 3 screen-edge docking modes: 'bottom', 'left', 'right'
- Compact resting capsule (40x8 bottom / 8x40 side) expanding smoothly on hover
  (50x30 ready pill + quick Scratchpad button + dark keycap shortcut tooltip)
- Active Push-to-Talk capsule (74x30) & Hands-Free capsule (104x30 with Cancel + 10-bar Waveform + Stop)
- Processing (98x30), Polish Processing (136x30 prismatic Dawn glow), Committed (152x30), Error (91x30)
- Live 10-bar parabolic center-weighted waveform (module 60666 algorithm)
- Optional live speech preview bubble positioned 12px above the pill
- Right-click context menu with live Microphone device picker, Languages, Dock edge, Scratchpad & Hub links
- Strict Win32 focus invariance (WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW) and optional WDA_EXCLUDEFROMCAPTURE
"""

from __future__ import annotations

import ctypes
import enum
import io
import math
import struct
import threading
import wave
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QTimer,
    QVariant,
    QVariantAnimation,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QFont,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedLayout,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.theme import get_floating_pill_stylesheet, get_theme_tokens, get_ui_font
from voxlocal.win32.window_focus import apply_non_activating_floating_style

logger = get_logger("ui.floating_pill")

_CHIME_WAV_CACHE: Dict[str, bytes] = {}


def _build_harmonic_chime_wav(
    freq_a: float, freq_b: float, duration_ms: int = 52, volume: float = 0.14
) -> bytes:
    """Synthesize a warm harmonic audio cue WAV buffer purely in memory."""
    sample_rate = 22050
    num_samples = max(1, int(sample_rate * (duration_ms / 1000.0)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_samples):
            prog = i / float(num_samples)
            env = math.sin(math.pi * prog) ** 0.85
            freq = freq_a + (freq_b - freq_a) * (prog ** 0.7)
            sample = math.sin(2.0 * math.pi * freq * (i / float(sample_rate))) * volume * env
            val = max(-32767, min(32767, int(sample * 32767)))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def play_flow_chime(kind: str, enabled: bool = True) -> None:
    """Play a gentle non-blocking sound effect on Windows ('start', 'stop', 'commit', 'error')."""
    if not enabled:
        return

    def _worker() -> None:
        try:
            import winsound

            if kind not in _CHIME_WAV_CACHE:
                specs = {
                    "start": (523.25, 659.25, 55, 0.14),
                    "stop": (659.25, 493.88, 50, 0.12),
                    "commit": (783.99, 987.77, 45, 0.11),
                    "error": (329.63, 246.94, 70, 0.12),
                }
                fa, fb, ms, vol = specs.get(kind, (587.33, 880.0, 45, 0.11))
                _CHIME_WAV_CACHE[kind] = _build_harmonic_chime_wav(fa, fb, ms, vol)
            winsound.PlaySound(
                _CHIME_WAV_CACHE[kind],
                winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def apply_capture_exclusion(hwnd: int, exclude: bool) -> None:
    """Exclude or include overlay HWND from Windows desktop/screen capture (Windows 10 2004+)."""
    if not hwnd:
        return
    try:
        # WDA_EXCLUDEFROMCAPTURE = 0x11, WDA_NONE = 0x0
        ctypes.windll.user32.SetWindowDisplayAffinity(int(hwnd), 0x00000011 if exclude else 0x00000000)
    except Exception:
        pass


class PillState(str, enum.Enum):
    IDLE = "idle"
    RESTING = "resting"
    READY = "ready"
    LISTENING = "listening"
    PROCESSING = "processing"
    POLISH_PROCESSING = "polish_processing"
    COMMITTED = "committed"
    ERROR = "error"
    MINI_MEETING = "mini_meeting"
    CARD_NOTIFICATION = "card_notification"
    ACTIVE_RECORDING = "active_recording"
    L4_PREVIEW = "l4_preview"


# Alias matching PROJECT.md Interface Contract 3
FlowBarState = PillState

DIMENSION_SPECS: Dict[str, Tuple[int, int]] = {
    "RESTING": (30, 6),
    "MINI_MEETING": (416, 48),
    "CARD_NOTIFICATION": (416, 80),
    "ACTIVE_RECORDING": (120, 36),
    "L4_PREVIEW": (416, 322),
}

MORPH_DURATION_MS = 420
FADE_OUT_MS = 80
FADE_IN_DELAY_MS = 100
EMERALD_HEX = "#10B981"
EMERALD_RGB = (16, 185, 129)


def resolve_platform_badge(platform_name: str) -> str:
    """Resolve normalized platform badge key for Teams, Zoom, Meet, Slack, or generic."""
    p = (platform_name or "").lower().strip()
    if p in ("generic_meeting", "generic") or not p:
        return "generic_meeting"
    if "teams" in p:
        return "teams"
    elif "zoom" in p:
        return "zoom"
    elif "meet" in p and not p.endswith("meeting"):
        return "meet"
    elif "slack" in p:
        return "slack"
    return "generic_meeting"


_get_platform_badge = resolve_platform_badge


def normalize_pill_state(state: PillState | str) -> PillState:
    """Safely map string or enum member to authoritative PillState."""
    if isinstance(state, PillState):
        return state
    s = str(state).strip().upper()
    mapping = {
        "IDLE": PillState.IDLE,
        "RESTING": PillState.RESTING,
        "READY": PillState.READY,
        "LISTENING": PillState.LISTENING,
        "PROCESSING": PillState.PROCESSING,
        "POLISH_PROCESSING": PillState.POLISH_PROCESSING,
        "COMMITTED": PillState.COMMITTED,
        "ERROR": PillState.ERROR,
        "MINI": PillState.MINI_MEETING,
        "MINI_MEETING": PillState.MINI_MEETING,
        "CARD": PillState.CARD_NOTIFICATION,
        "CARD_NOTIFICATION": PillState.CARD_NOTIFICATION,
        "ACTIVE": PillState.ACTIVE_RECORDING,
        "ACTIVE_RECORDING": PillState.ACTIVE_RECORDING,
        "L4": PillState.L4_PREVIEW,
        "L4_PREVIEW": PillState.L4_PREVIEW,
    }
    if s in mapping:
        return mapping[s]
    try:
        return PillState(str(state).lower())
    except ValueError:
        return PillState.IDLE


class DynamicWaveformCapsule(QWidget):
    """
    10-bar parabolic center-weighted live waveform matching Wispr Flow CSS module 60666.
    Bars are 2px wide with smooth spring smoothing and per-mode tinting.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, bar_count: int = 10) -> None:
        super().__init__(parent)
        self.bar_count = max(6, int(bar_count))
        self._target_level: float = 0.0
        self._smooth_level: float = 0.0
        self._bars: List[float] = [0.08] * self.bar_count
        self._is_processing: bool = False
        self._is_error: bool = False
        self._is_committed: bool = False
        self._tint_mode: str = "speech"  # "speech" | "command" | "instruct" | "dim"
        self._phase: float = 0.0
        self.setFixedSize(38, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

    def set_level(self, rms_level: float) -> None:
        self._target_level = max(0.0, min(1.0, float(rms_level)))

    def set_processing(self, processing: bool) -> None:
        self._is_processing = bool(processing)
        if processing:
            self._is_error = False
            self._is_committed = False
        self.update()

    def set_visual_mode(self, mode: str) -> None:
        """Set visual bar color mode: 'speech', 'command', 'instruct', or 'dim'."""
        self._tint_mode = mode
        self.update()

    def _tick(self) -> None:
        self._phase += 0.24
        self._smooth_level += (self._target_level - self._smooth_level) * 0.35
        center = (self.bar_count - 1) / 2.0
        bulge = 1.65
        for i in range(self.bar_count):
            dist = abs(center - i)
            parabolic_scale = max(0.12, 1.0 - (dist ** 2) * (bulge / 48.0))
            if self._is_processing:
                wave_val = 0.25 + 0.55 * (0.5 + 0.5 * math.sin(self._phase * 1.4 - i * 0.55))
                target_h = wave_val * parabolic_scale
            elif self._smooth_level > 0.02:
                jitter = 0.78 + 0.22 * math.sin(self._phase * 1.9 + i * 1.15)
                target_h = min(1.0, (0.14 + self._smooth_level * 1.65 * jitter) * parabolic_scale)
            else:
                idle_wave = 0.14 + 0.06 * math.sin(self._phase * 0.65 + i * 0.6)
                target_h = idle_wave * parabolic_scale
            self._bars[i] += (target_h - self._bars[i]) * 0.38
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cy = h / 2.0

        if self._tint_mode == "command":
            color = QColor("#FFA946")
        elif self._tint_mode == "instruct":
            color = QColor("#FFBCF2")
        elif self._tint_mode == "dim":
            color = QColor(255, 255, 255, 102)
        else:
            color = QColor("#FFFFFF")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))

        bar_w = 2.0
        step = (w - bar_w * self.bar_count) / max(1, self.bar_count - 1)
        step = max(1.0, min(step, 3.0))
        total_w = self.bar_count * bar_w + (self.bar_count - 1) * step
        start_x = (w - total_w) / 2.0
        max_bar_h = max(4.0, h - 4.0)

        for i, val in enumerate(self._bars):
            bar_h = max(2.0, val * max_bar_h)
            x = start_x + i * (bar_w + step)
            y = cy - bar_h / 2.0
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 0.75, 0.75)


class SessionTimerWidget(QLabel):
    """Optional tabular elapsed timer widget kept for API compatibility."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("00:00", parent)
        self._seconds = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._increment)

    def start(self) -> None:
        self._seconds = 0
        self.setText("00:00")
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _increment(self) -> None:
        self._seconds += 1
        mins, secs = divmod(self._seconds, 60)
        self.setText(f"{mins:02d}:{secs:02d}")


class FlowBarTooltipPill(QWidget):
    """Dark Wispr-style hover hint floating cleanly above the FlowBar with keycap hints."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setStyleSheet(
            "QFrame { background-color: #1A1A1A; border: 1px solid #30302F; border-radius: 8px; }"
        )
        c_lay = QHBoxLayout(self.card)
        c_lay.setContentsMargins(10, 5, 10, 5)
        c_lay.setSpacing(6)

        self.lbl_text = QLabel("Click or hold Ctrl+Win to start dictating")
        self.lbl_text.setFont(get_ui_font(12, 500))
        self.lbl_text.setStyleSheet("color: #FCFCFB; border: none; background: transparent;")
        c_lay.addWidget(self.lbl_text)
        layout.addWidget(self.card)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def set_hint(self, html_or_text: str) -> None:
        self.lbl_text.setText(html_or_text)
        self.adjustSize()

    def position_above(self, pill_rect: QRect) -> None:
        self.adjustSize()
        sh = self.sizeHint()
        x = pill_rect.center().x() - sh.width() // 2
        y = pill_rect.y() - sh.height() - 8
        screen = QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - sh.width() - 8))
            y = max(avail.top() + 8, y)
        self.move(x, y)


class LiveSpeechPreviewBubble(QWidget):
    """Non-activating floating preview bubble 12px above the FlowBar for live interim text & red strikethrough."""

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "light") -> None:
        super().__init__(parent)
        self._theme = theme
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        c_lay = QVBoxLayout(self.card)
        c_lay.setContentsMargins(14, 9, 14, 9)
        self.lbl_preview = QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setMaximumWidth(360)
        self.lbl_preview.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_preview.setFont(get_ui_font(13, 400))
        c_lay.addWidget(self.lbl_preview)
        layout.addWidget(self.card)
        self.apply_theme(theme)
        try:
            apply_non_activating_floating_style(int(self.winId()))
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        t = get_theme_tokens(theme)
        self.card.setStyleSheet(
            f"QFrame {{ background-color: #1A1A1A; border: 1px solid #30302F; border-radius: 12px; }}"
        )
        self.lbl_preview.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")

    def update_preview(self, annotated_html: str) -> None:
        self.lbl_preview.setText(annotated_html)
        self.adjustSize()

    def update_position_above_pill(self, pill_rect: QRect) -> None:
        self.adjustSize()
        sh = self.sizeHint()
        x = pill_rect.center().x() - sh.width() // 2
        y = pill_rect.y() - sh.height() - 12
        screen = QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - sh.width() - 8))
            y = max(avail.top() + 8, y)
        self.move(x, y)


class _RoundPillButton(QPushButton):
    """Crisp circular 18x18 icon button matching Wispr FlowBar's Cancel and Stop buttons."""

    def __init__(self, kind: str = "cancel", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kind = kind  # "cancel" | "stop" | "scratchpad" | "check"
        self.setFixedSize(20 if kind in ("scratchpad",) else 18, 20 if kind in ("scratchpad",) else 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        hovered = self.underMouse()

        if self._kind == "cancel":
            bg = QColor("#666258" if hovered else "#4D4A42")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawEllipse(0, 0, w, h)
            painter.setPen(QPen(QColor("#FCFCFB"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            m = 5.5
            painter.drawLine(QPointF(m, m), QPointF(w - m, h - m))
            painter.drawLine(QPointF(w - m, m), QPointF(m, h - m))
        elif self._kind == "stop":
            bg = QColor("#383632" if hovered else "#2B2926")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawEllipse(0, 0, w, h)
            painter.setBrush(QColor("#FF5757" if self.isEnabled() else "#736E62"))
            s = 7.0
            painter.drawRoundedRect(QRectF((w - s) / 2.0, (h - s) / 2.0, s, s), 2.0, 2.0)
        elif self._kind == "scratchpad":
            bg = QColor("#1E1E1D" if hovered else "#000000")
            painter.setPen(QPen(QColor("#30302F"), 1.0))
            painter.setBrush(bg)
            painter.drawEllipse(QRectF(0.5, 0.5, w - 1, h - 1))
            painter.setPen(QPen(QColor("#E9E8E1"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            # Minimal note square + lines
            painter.drawRoundedRect(QRectF(5.5, 5.0, 9.0, 10.0), 1.5, 1.5)
            painter.drawLine(QPointF(7.5, 8.2), QPointF(12.5, 8.2))
            painter.drawLine(QPointF(7.5, 11.2), QPointF(11.0, 11.2))
        elif self._kind == "check":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#034F46"))
            painter.drawEllipse(0, 0, w, h)
            painter.setPen(QPen(QColor("#84CDC1"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(4.8, 9.2), QPointF(7.8, 12.2))
            painter.drawLine(QPointF(7.8, 12.2), QPointF(13.2, 6.2))


class PlatformBadgeWidget(QWidget):
    """
    Renders official colored badge for Microsoft Teams, Zoom, Google Meet, Slack, or generic.
    """

    def __init__(self, parent: Optional[QWidget] = None, badge_size: int = 24) -> None:
        super().__init__(parent)
        self.badge_size = badge_size
        self._platform: str = "teams"
        self.setFixedSize(badge_size, badge_size)

    def set_platform(self, platform_name: str) -> None:
        self._platform = resolve_platform_badge(platform_name)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(self.badge_size)
        r = QRectF(0, 0, s, s)

        if self._platform == "teams":
            bg = QColor("#5B5FC7")  # Teams purple
            fg = QColor("#FFFFFF")
            letter = "T"
        elif self._platform == "zoom":
            bg = QColor("#2D8CFF")  # Zoom blue
            fg = QColor("#FFFFFF")
            letter = "Z"
        elif self._platform == "meet":
            bg = QColor("#00AC47")  # Meet green
            fg = QColor("#FFFFFF")
            letter = "M"
        elif self._platform == "slack":
            bg = QColor("#4A154B")  # Slack aubergine
            fg = QColor("#E01E5A")
            letter = "#"
        else:
            bg = QColor("#30302F")
            fg = QColor("#FCFCFB")
            letter = "●"

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, 6.0, 6.0)

        painter.setPen(fg)
        font = painter.font()
        font.setPixelSize(int(s * 0.55))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, letter)


class EmeraldDotWidget(QWidget):
    """
    Emerald status dot #10B981 for live meeting indicator.
    """

    def __init__(self, parent: Optional[QWidget] = None, dot_size: int = 7) -> None:
        super().__init__(parent)
        self.dot_size = dot_size
        self.setFixedSize(dot_size, dot_size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#10B981"))
        s = float(self.dot_size)
        painter.drawEllipse(QRectF(0, 0, s, s))


class JoinButton(QPushButton):
    """
    Split Join CTA button with animated auto-start progress fill underlay.
    """

    def __init__(self, text: str = "Join", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._progress: float = 0.0
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_progress(self, p: float) -> None:
        self._progress = max(0.0, min(1.0, float(p)))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        w = float(r.width())
        h = float(r.height())

        # Base background
        base_color = QColor("#065F46" if not self.underMouse() else "#047857")
        if self.isDown():
            base_color = QColor("#064E3B")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base_color)
        painter.drawRoundedRect(QRectF(0, 0, w, h), 6.0, 6.0)

        # Progress fill underlay
        if self._progress > 0.0:
            fill_w = w * self._progress
            painter.setBrush(QColor(16, 185, 129, 210))  # Emerald #10B981
            painter.drawRoundedRect(QRectF(0, 0, fill_w, h), 6.0, 6.0)

        # Border
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#10B981"), 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 6.0, 6.0)

        # Text
        painter.setPen(QColor("#FCFCFB"))
        f = painter.font()
        f.setPixelSize(12)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class VoxFlowBar(QWidget):
    """
    Wispr Flow 1.6.1457 faithful FlowBar capsule implemented cleanly in PyQt6 for VOX.
    Supports Resting (40x8), Ready (50x30 + hover tooltip + Scratchpad btn), Active PTT (74x30),
    Active Hands-Free (104x30), Processing (98x30), Polish (136x30), Committed (152x30), Error (91x30).
    """

    start_dictation_requested = pyqtSignal()
    finalize_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    mode_changed = pyqtSignal(object)
    open_dashboard_requested = pyqtSignal(int)
    meeting_toggle_requested = pyqtSignal()
    open_scratchpad_requested = pyqtSignal()
    open_notetaker_requested = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        theme: str = "light",
        on_finalize: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        controller: Optional[Any] = None,
        on_open_dashboard: Optional[Callable[[int], None]] = None,
        on_exit_app: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme or "light"
        self.controller = controller
        if self.controller is not None:
            try:
                self.controller.floating_pill = self
            except Exception:
                pass
        self.on_finalize = on_finalize
        self.on_cancel = on_cancel
        self.on_open_dashboard = on_open_dashboard
        self.on_exit_app = on_exit_app

        self._state: PillState = PillState.IDLE
        self._is_hovered: bool = False
        self._is_ptt_session: bool = False
        self._current_mode: TransformationMode = TransformationMode.CLEAN
        self._dock_edge: str = (
            getattr(getattr(controller, "config", None), "flow_bar_edge", "bottom") or "bottom"
        ).lower()
        self._meeting_active: bool = False
        self._meeting_elapsed_str: str = "00:00"
        self._status_message: str = ""
        self._dragging: bool = False
        self._drag_start_pos: QPoint = QPoint()
        self._was_dragged: bool = False
        self._prismatic_angle: float = 0.0
        self._last_entry_id: Optional[int] = None

        # Authoritative Milestone 6 Geometry, Morph, and Animation Specifications
        self.morph_duration: int = MORPH_DURATION_MS
        self.fade_out_duration: int = FADE_OUT_MS
        self.fade_in_delay: int = FADE_IN_DELAY_MS
        self.target_size: Optional[QSize] = QSize(30, 6)
        self._current_metadata: Dict[str, Any] = {}
        self._meeting_title: str = "Untitled Meeting"
        self._meeting_platform: str = "teams"
        self._active_meeting_count: int = 1
        self._auto_start_progress: float = 0.0
        self._auto_start_paused: bool = False
        self._expanded_from_mini: bool = False
        self._auto_start_duration_ms: int = 5000
        self._auto_start_elapsed_ms: int = 0

        # Geometry morph animation with cubic spring easing
        self._morph_anim = QVariantAnimation(self)
        self._morph_anim.setDuration(self.morph_duration)
        self._morph_anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._morph_anim.valueChanged.connect(self._on_morph_step)
        self._morph_anim.finished.connect(self._on_morph_finished)
        self.morph_animation = self._morph_anim

        # Auto-start progress timer
        self._auto_start_timer = QTimer(self)
        self._auto_start_timer.setInterval(50)
        self._auto_start_timer.timeout.connect(self._on_auto_start_tick)

        # Snooze reminder timer
        self._snooze_timer = QTimer(self)
        self._snooze_timer.setSingleShot(True)
        self._snooze_timer.timeout.connect(self._on_snooze_timeout)

        # Countdown refresh timer for upcoming meetings in MINI_MEETING state
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

        self.preview_bubble = LiveSpeechPreviewBubble(theme=self._theme)
        self.hover_tooltip = FlowBarTooltipPill()

        self._auto_reset_timer = QTimer(self)
        self._auto_reset_timer.setSingleShot(True)
        self._auto_reset_timer.timeout.connect(self._reset_to_resting)

        self._prismatic_timer = QTimer(self)
        self._prismatic_timer.setInterval(40)
        self._prismatic_timer.timeout.connect(self._advance_prismatic)

        self._build_ui()
        self._connect_controller_signals()
        self._sync_geometry()
        try:
            apply_non_activating_floating_style(int(self.winId()))
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Flanking Quick Scratchpad button (shown on hover/ready when enabled)
        self.btn_scratchpad = _RoundPillButton("scratchpad", self)
        self.btn_scratchpad.setToolTip("Open Scratchpad")
        self.btn_scratchpad.clicked.connect(self._on_open_scratchpad)
        self.btn_scratchpad.hide()
        root.addWidget(self.btn_scratchpad, 0, Qt.AlignmentFlag.AlignVCenter)

        # Primary FlowBar Pill Surface
        self.upper_pill = QFrame(self)
        self.upper_pill.setObjectName("flowBarPill")
        self.upper_pill.setMouseTracking(True)

        # Stacked layout for polymorphic 8-state layouts:
        # Index 0: Dictation & Active Recording layout
        # Index 1: Mini Meeting layMini layout (416x48)
        # Index 2: Card Notification layCard layout (416x80)
        # Index 3: L4 Preview agenda layout (416x322)
        self.pill_stack = QStackedLayout(self.upper_pill)
        self.pill_stack.setContentsMargins(0, 0, 0, 0)

        # --- Page 0: Dictation & Active Recording Layout ---
        self.lay_dictation_widget = QWidget(self.upper_pill)
        d_lay = QHBoxLayout(self.lay_dictation_widget)
        d_lay.setContentsMargins(6, 4, 6, 4)
        d_lay.setSpacing(5)
        d_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_cancel = _RoundPillButton("cancel", self.lay_dictation_widget)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_cancel.hide()
        d_lay.addWidget(self.btn_cancel)

        self.btn_check = _RoundPillButton("check", self.lay_dictation_widget)
        self.btn_check.hide()
        d_lay.addWidget(self.btn_check)

        self.waveform_capsule = DynamicWaveformCapsule(self.lay_dictation_widget, bar_count=10)
        self.waveform_capsule.clicked.connect(self._on_pill_clicked)
        d_lay.addWidget(self.waveform_capsule)

        self.lbl_state_text = QLabel("", self.lay_dictation_widget)
        self.lbl_state_text.setFont(get_ui_font(11.5, 550))
        self.lbl_state_text.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
        self.lbl_state_text.hide()
        d_lay.addWidget(self.lbl_state_text)

        self.btn_undo_toast = QPushButton("Undo", self.lay_dictation_widget)
        self.btn_undo_toast.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_undo_toast.setFixedHeight(18)
        self.btn_undo_toast.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo_toast.setStyleSheet(
            "QPushButton { background-color: #30302F; color: #F0D7FF; border: none; "
            "border-radius: 9px; padding: 0px 7px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { background-color: #4D4A42; }"
        )
        self.btn_undo_toast.clicked.connect(self._on_undo_last)
        self.btn_undo_toast.hide()
        d_lay.addWidget(self.btn_undo_toast)

        self.btn_stop = _RoundPillButton("stop", self.lay_dictation_widget)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.hide()
        d_lay.addWidget(self.btn_stop)

        self.pill_stack.addWidget(self.lay_dictation_widget)

        # --- Page 1: Mini Meeting layMini Layout (416x48) ---
        self.lay_mini_widget = QWidget(self.upper_pill)
        m_lay = QHBoxLayout(self.lay_mini_widget)
        m_lay.setContentsMargins(12, 6, 12, 6)
        m_lay.setSpacing(8)
        m_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.mini_badge = PlatformBadgeWidget(self.lay_mini_widget, badge_size=24)
        m_lay.addWidget(self.mini_badge)

        self.mini_emerald_dot = EmeraldDotWidget(self.lay_mini_widget, dot_size=7)
        m_lay.addWidget(self.mini_emerald_dot)

        self.lbl_meeting_title = QLabel("Untitled Meeting", self.lay_mini_widget)
        self.lbl_meeting_title.setFont(get_ui_font(12, 600))
        self.lbl_meeting_title.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
        m_lay.addWidget(self.lbl_meeting_title, 1)

        self.lbl_countdown_timer = QLabel("00:00", self.lay_mini_widget)
        self.lbl_countdown_timer.setFont(get_ui_font(11, 500))
        self.lbl_countdown_timer.setStyleSheet("color: #9C9A94; background: transparent; border: none;")
        m_lay.addWidget(self.lbl_countdown_timer)

        self.lbl_stack_badge = QLabel("1", self.lay_mini_widget)
        self.lbl_stack_badge.setFont(get_ui_font(10, 700))
        self.lbl_stack_badge.setStyleSheet(
            "background-color: #2B2926; color: #FCFCFB; border: 1px solid #4D4A42; "
            "border-radius: 8px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
        )
        self.lbl_stack_badge.hide()
        m_lay.addWidget(self.lbl_stack_badge)

        self.pill_stack.addWidget(self.lay_mini_widget)

        # --- Page 2: Card Notification layCard Layout (416x80) ---
        self.lay_card_widget = QWidget(self.upper_pill)
        c_lay = QHBoxLayout(self.lay_card_widget)
        c_lay.setContentsMargins(16, 12, 16, 12)
        c_lay.setSpacing(12)
        c_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.card_badge = PlatformBadgeWidget(self.lay_card_widget, badge_size=32)
        c_lay.addWidget(self.card_badge)

        c_text_col = QVBoxLayout()
        c_text_col.setContentsMargins(0, 0, 0, 0)
        c_text_col.setSpacing(2)

        self.lbl_card_title = QLabel("Untitled Meeting", self.lay_card_widget)
        self.lbl_card_title.setFont(get_ui_font(13, 600))
        self.lbl_card_title.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
        c_text_col.addWidget(self.lbl_card_title)

        self.lbl_card_subtitle = QLabel("Incoming Call • Auto-joining...", self.lay_card_widget)
        self.lbl_card_subtitle.setFont(get_ui_font(11, 400))
        self.lbl_card_subtitle.setStyleSheet("color: #9C9A94; background: transparent; border: none;")
        c_text_col.addWidget(self.lbl_card_subtitle)
        c_lay.addLayout(c_text_col, 1)

        # Split CTA button layout: JoinButton + Caret
        btn_box = QHBoxLayout()
        btn_box.setContentsMargins(0, 0, 0, 0)
        btn_box.setSpacing(0)

        self.btn_join = JoinButton("Join", self)
        self.btn_join.clicked.connect(self._on_join_clicked)
        btn_box.addWidget(self.btn_join)

        self.btn_caret = QPushButton("▼", self)
        self.btn_caret.setFixedSize(26, 32)
        self.btn_caret.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_caret.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_caret.setStyleSheet(
            "QPushButton { background-color: #065F46; color: #FCFCFB; border: 1px solid #10B981; "
            "border-left: none; border-top-right-radius: 6px; border-bottom-right-radius: 6px; font-size: 10px; }"
            "QPushButton:hover { background-color: #047857; }"
            "QPushButton:pressed { background-color: #064E3B; }"
        )
        self.btn_caret.clicked.connect(self._show_caret_menu)
        btn_box.addWidget(self.btn_caret)

        c_lay.addLayout(btn_box)

        self.pill_stack.addWidget(self.lay_card_widget)

        # --- Page 3: L4 Preview Agenda Layout (416x322) ---
        self.lay_l4_widget = QWidget(self.upper_pill)
        l4_lay = QVBoxLayout(self.lay_l4_widget)
        l4_lay.setContentsMargins(16, 14, 16, 14)
        l4_lay.setSpacing(8)

        l4_header = QHBoxLayout()
        self.l4_badge = PlatformBadgeWidget(self.lay_l4_widget, badge_size=28)
        l4_header.addWidget(self.l4_badge)
        self.lbl_l4_title = QLabel("Meeting Agenda Preview", self.lay_l4_widget)
        self.lbl_l4_title.setFont(get_ui_font(13.5, 600))
        self.lbl_l4_title.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
        l4_header.addWidget(self.lbl_l4_title, 1)
        l4_lay.addLayout(l4_header)

        self.lbl_l4_body = QLabel(
            "• Discussion Topics & Synchronized Agenda\n• Participants & Shared Documents\n• Historical Decisions & Action Items",
            self.lay_l4_widget,
        )
        self.lbl_l4_body.setFont(get_ui_font(12, 400))
        self.lbl_l4_body.setStyleSheet("color: #D1D5DB; background: transparent; border: none; line-height: 1.4;")
        self.lbl_l4_body.setWordWrap(True)
        l4_lay.addWidget(self.lbl_l4_body, 1)

        self.pill_stack.addWidget(self.lay_l4_widget)

        root.addWidget(self.upper_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        # Optional Notetaker companion chip when a call recording is active
        self.meeting_chip = QPushButton("● 00:00 Notetaker", self)
        self.meeting_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.meeting_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.meeting_chip.setFixedHeight(28)
        self.meeting_chip.setStyleSheet(
            "QPushButton { background-color: #1A1A1A; color: #FCFCFB; border: 1px solid #FF5757; "
            "border-radius: 14px; padding: 0px 10px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #2B1A1B; }"
        )
        self.meeting_chip.clicked.connect(self._on_meeting_chip_clicked)
        self.meeting_chip.hide()
        root.addWidget(self.meeting_chip, 0, Qt.AlignmentFlag.AlignVCenter)

        # Hidden compatibility attributes kept cleanly initialized
        self.timer_widget = SessionTimerWidget(self)
        self.timer_widget.hide()
        self.menu_btn = QPushButton(self)
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.hide()
        self.menu_btn.clicked.connect(lambda: self._on_open_settings())

        self.apply_theme(self._theme)

    def setFixedSize(self, *args) -> None:
        super().setFixedSize(*args)
        if len(args) == 1 and isinstance(args[0], QSize):
            self.target_size = args[0]
        elif len(args) == 2:
            self.target_size = QSize(int(args[0]), int(args[1]))

    @property
    def pill_state(self) -> PillState:
        if self._state in (PillState.IDLE, PillState.RESTING) and self._is_hovered:
            return PillState.READY
        return self._state

    @property
    def current_mode(self) -> TransformationMode:
        return self._current_mode

    @property
    def auto_start_progress(self) -> float:
        return self._auto_start_progress

    @auto_start_progress.setter
    def auto_start_progress(self, val: float) -> None:
        self._auto_start_progress = max(0.0, min(1.0, float(val)))
        if hasattr(self, "btn_join") and hasattr(self.btn_join, "set_progress"):
            self.btn_join.set_progress(self._auto_start_progress)
        self.update()

    def set_pill_state(
        self,
        new_state: PillState | str,
        metadata: Optional[Dict[str, Any]] = None,
        animated: bool = True,
    ) -> None:
        """
        Polymorphic 8-state machine engine conforming to Feature 6 & Interface Contract 3.
        Transitions between Resting, Mini, Card, Active, L4 Preview, and dictation states.
        """
        norm_state = normalize_pill_state(new_state)
        meta = metadata or {}
        self._current_metadata = dict(meta)

        if "title" in meta:
            raw_title = str(meta["title"]).strip()
            self._meeting_title = raw_title if raw_title else "Untitled Meeting"
        if "platform" in meta:
            self._meeting_platform = str(meta["platform"]).strip()
        if "active_meeting_count" in meta:
            self.set_active_meeting_count(int(meta["active_meeting_count"]))

        badge_key = resolve_platform_badge(self._meeting_platform)
        if hasattr(self, "mini_badge"):
            self.mini_badge.set_platform(badge_key)
        if hasattr(self, "card_badge"):
            self.card_badge.set_platform(badge_key)
        if hasattr(self, "l4_badge"):
            self.l4_badge.set_platform(badge_key)

        fm = self.fontMetrics()
        elided_title = fm.elidedText(self._meeting_title, Qt.TextElideMode.ElideRight, 200)
        if hasattr(self, "lbl_meeting_title"):
            self.lbl_meeting_title.setText(elided_title)
            self.lbl_meeting_title.setToolTip(self._meeting_title)
        if hasattr(self, "lbl_card_title"):
            self.lbl_card_title.setText(elided_title)
            self.lbl_card_title.setToolTip(self._meeting_title)
        if hasattr(self, "lbl_l4_title"):
            self.lbl_l4_title.setText(elided_title)

        if "countdown_text" in meta:
            if hasattr(self, "lbl_countdown_timer"):
                self.lbl_countdown_timer.setText(str(meta["countdown_text"]))
        elif "minutes_until_start" in meta:
            if hasattr(self, "lbl_countdown_timer"):
                mins = int(meta["minutes_until_start"])
                txt = "now" if mins <= 0 else f"in {mins}m"
                self.lbl_countdown_timer.setText(txt)
        elif "seconds_until_start" in meta:
            if hasattr(self, "lbl_countdown_timer"):
                secs = max(0.0, float(meta["seconds_until_start"]))
                mins = int(secs // 60)
                rem_s = int(secs % 60)
                txt = f"in {mins}m" if mins > 0 else (f"in {rem_s}s" if rem_s > 0 else "now")
                self.lbl_countdown_timer.setText(txt)

        prev_state = self._state
        self._state = norm_state
        if norm_state != PillState.CARD_NOTIFICATION:
            self._expanded_from_mini = False

        if norm_state == PillState.RESTING:
            self._meeting_active = False
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(30, 6)
            self.pill_stack.setCurrentIndex(0)
        elif norm_state == PillState.IDLE:
            self._meeting_active = False
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(54, 30)
            self.pill_stack.setCurrentIndex(0)
        elif norm_state == PillState.MINI_MEETING:
            self._auto_start_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(416, 48)
            self.pill_stack.setCurrentIndex(1)
            self._update_stack_badge()
            self._countdown_timer.start()
        elif norm_state == PillState.CARD_NOTIFICATION:
            self._countdown_timer.stop()
            self.target_size = QSize(416, 80)
            self.pill_stack.setCurrentIndex(2)
            self._update_stack_badge()
            auto_rec = True
            if self.controller and getattr(self.controller, "config", None):
                auto_rec = bool(getattr(self.controller.config, "auto_record_calls", True))
            if auto_rec and not self._is_hovered:
                if not (prev_state == PillState.CARD_NOTIFICATION and self._auto_start_timer.isActive()):
                    self._start_auto_start_countdown(meta.get("auto_start_delay", 5000))
            else:
                self._auto_start_timer.stop()
                self.auto_start_progress = 0.0
        elif norm_state == PillState.ACTIVE_RECORDING:
            self._meeting_active = True
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(120, 36)
            self.pill_stack.setCurrentIndex(0)
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(36, 18)
            self.waveform_capsule.show()
            self.lbl_state_text.setText(self._meeting_elapsed_str or "00:00")
            self.lbl_state_text.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
            self.lbl_state_text.show()
            self.btn_stop.setEnabled(True)
            self.btn_stop.show()
        elif norm_state == PillState.L4_PREVIEW:
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.target_size = QSize(416, 322)
            self.pill_stack.setCurrentIndex(3)
        else:
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.pill_stack.setCurrentIndex(0)
            self.set_state(norm_state, meta.get("message", ""))
            return

        self._sync_pill_visuals()
        self._animate_to_size(self.target_size, animated=animated)

    def _animate_to_size(self, target_size: QSize, animated: bool = True) -> None:
        self.target_size = target_size
        current_size = self.size()
        if not animated or current_size == target_size or self.morph_duration <= 0:
            self._morph_anim.stop()
            super().setFixedSize(target_size)
            self.upper_pill.setFixedSize(target_size)
            self.dock_to_edge(self._dock_edge)
            self.update()
            return

        self._morph_anim.stop()
        self._morph_anim.setStartValue(current_size)
        self._morph_anim.setEndValue(target_size)
        self._morph_anim.start()

    def _on_morph_step(self, val: Any) -> None:
        if isinstance(val, QSize):
            sz = val
        elif isinstance(val, QRect):
            sz = val.size()
        else:
            return
        super().setFixedSize(sz)
        self.upper_pill.setFixedSize(sz)
        self.dock_to_edge(self._dock_edge)
        self.update()

    def _on_morph_finished(self) -> None:
        if self.target_size is not None:
            super().setFixedSize(self.target_size)
            self.upper_pill.setFixedSize(self.target_size)
        self.dock_to_edge(self._dock_edge)
        self.update()

    def _start_auto_start_countdown(self, delay_ms: int = 5000) -> None:
        self._auto_start_duration_ms = max(0, int(delay_ms))
        self._auto_start_elapsed_ms = 0
        self.auto_start_progress = 0.0
        self._auto_start_paused = False
        if self._auto_start_duration_ms == 0:
            self._on_join_clicked()
            return
        self._auto_start_timer.start(50)

    def _on_auto_start_tick(self) -> None:
        if self._auto_start_paused:
            return
        self._auto_start_elapsed_ms += 50
        p = self._auto_start_elapsed_ms / float(max(1, self._auto_start_duration_ms))
        self.auto_start_progress = p
        if self.auto_start_progress >= 1.0:
            self._auto_start_timer.stop()
            self._on_join_clicked()

    def _on_join_clicked(self) -> None:
        self._auto_start_timer.stop()
        self.auto_start_progress = 0.0
        self.meeting_toggle_requested.emit()
        if self.controller and hasattr(self.controller, "start_meeting_recording"):
            try:
                self.controller.start_meeting_recording(title=self._meeting_title)
            except Exception as exc:
                logger.debug("start_meeting_recording trigger: %s", exc)
        self.set_pill_state(PillState.ACTIVE_RECORDING)

    def _on_join_without_notetaker(self) -> None:
        self._auto_start_timer.stop()
        self.auto_start_progress = 0.0
        self.set_pill_state(PillState.RESTING)

    def _show_caret_menu(self) -> None:
        self._auto_start_timer.stop()
        self._auto_start_paused = True

        menu = QMenu(self)
        menu.setStyleSheet(get_floating_pill_stylesheet(self._theme))
        act_join_no_rec = menu.addAction("Join without Notetaker")
        act_dismiss = menu.addAction("Dismiss")
        act_snooze = menu.addAction("Snooze for 5 minutes")

        act_join_no_rec.triggered.connect(self._on_join_without_notetaker)
        act_dismiss.triggered.connect(self.dismiss_notification)
        act_snooze.triggered.connect(lambda: self.snooze_notification(5))

        btn_geo = self.btn_caret.mapToGlobal(QPoint(0, self.btn_caret.height()))
        menu.exec(btn_geo)

    def dismiss_notification(self) -> None:
        self._auto_start_timer.stop()
        self.auto_start_progress = 0.0
        self.set_pill_state(PillState.RESTING)

    def snooze_notification(self, minutes: int = 5) -> None:
        self._auto_start_timer.stop()
        self.auto_start_progress = 0.0
        self._snooze_timer.setInterval(max(1, int(minutes)) * 60 * 1000)
        self._snooze_timer.start()
        self.set_pill_state(PillState.RESTING)

    def _on_snooze_timeout(self) -> None:
        self.show_meeting_reminder(self._current_metadata)

    def show_meeting_reminder(self, reminder_data: Optional[Dict[str, Any]] = None) -> None:
        data = reminder_data or {}
        self.set_pill_state(PillState.CARD_NOTIFICATION, metadata=data)

    def get_active_meeting_count(self) -> int:
        if getattr(self, "_active_meeting_count", None) is not None:
            return max(0, int(self._active_meeting_count))
        if self.controller and hasattr(self.controller, "get_active_meeting_count"):
            try:
                return max(0, int(self.controller.get_active_meeting_count()))
            except Exception:
                pass
        return 0

    def set_active_meeting_count(self, count: int) -> None:
        self._active_meeting_count = max(0, int(count))
        self._update_stack_badge()

    def _update_stack_badge(self) -> None:
        count = self.get_active_meeting_count()
        if count > 1:
            txt = "9+" if count >= 10 else str(count)
            self.lbl_stack_badge.setText(txt)
            self.lbl_stack_badge.show()
        else:
            self.lbl_stack_badge.hide()

    def get_platform_badge(self, platform_name: str) -> str:
        return resolve_platform_badge(platform_name)

    def _on_countdown_tick(self) -> None:
        if self._state != PillState.MINI_MEETING:
            self._countdown_timer.stop()
            return
        secs = self._current_metadata.get("seconds_until_start")
        if secs is not None:
            secs = max(0.0, float(secs) - 1.0)
            self._current_metadata["seconds_until_start"] = secs
            mins = int(secs // 60)
            rem_s = int(secs % 60)
            if mins > 0:
                txt = f"in {mins}m"
            elif secs > 0:
                txt = f"in {rem_s}s"
            else:
                txt = "now"
            if hasattr(self, "lbl_countdown_timer"):
                self.lbl_countdown_timer.setText(txt)

    def _on_controller_call_detected(self, platform: str, title: str) -> None:
        if self._state not in (PillState.LISTENING, PillState.ACTIVE_RECORDING):
            if self._state == PillState.CARD_NOTIFICATION and self._auto_start_timer.isActive():
                return
            self.set_pill_state(
                PillState.CARD_NOTIFICATION,
                metadata={"platform": platform, "title": title},
            )

    def _on_controller_call_ended(self, platform: str) -> None:
        if self._state in (PillState.CARD_NOTIFICATION, PillState.MINI_MEETING, PillState.ACTIVE_RECORDING):
            self._meeting_active = False
            self.set_pill_state(PillState.RESTING, animated=True)

    def _on_controller_meeting_lifecycle(self, lifecycle: str, msg: str) -> None:
        lc = (lifecycle or "").lower().strip()
        if lc in ("recording", "preparing_capture"):
            self._meeting_active = True
            if self._state != PillState.ACTIVE_RECORDING:
                self.set_pill_state(PillState.ACTIVE_RECORDING, animated=True)
        elif lc in ("meeting_detected", "awaiting_confirmation"):
            if self._state not in (PillState.LISTENING, PillState.ACTIVE_RECORDING):
                title = self._meeting_title or "Meeting"
                plat = self._meeting_platform or "generic_meeting"
                if self.controller:
                    s_info = getattr(self.controller, "_active_session_info", None)
                    if s_info:
                        title = getattr(s_info, "clean_title", None) or getattr(s_info, "title", title)
                        plat = getattr(s_info, "platform", plat)
                self.set_pill_state(
                    PillState.CARD_NOTIFICATION,
                    metadata={"title": title, "platform": plat},
                    animated=True,
                )
        elif lc in ("no_meeting_detected", "completed", "failed", "meeting_ending"):
            if self._state in (PillState.ACTIVE_RECORDING, PillState.CARD_NOTIFICATION, PillState.MINI_MEETING):
                self._meeting_active = False
                self.set_pill_state(PillState.RESTING, animated=True)

    def _on_calendar_upcoming_changed(self, meetings: list) -> None:
        if not meetings:
            self._active_upcoming_meetings = []
            self.set_active_meeting_count(0)
            if self._state in (PillState.MINI_MEETING, PillState.CARD_NOTIFICATION) and not self._meeting_active:
                self.set_pill_state(PillState.RESTING, animated=True)
            return

        self._active_upcoming_meetings = list(meetings)
        self.set_active_meeting_count(len(meetings))

        if self._state not in (
            PillState.LISTENING,
            PillState.ACTIVE_RECORDING,
            PillState.PROCESSING,
            PillState.COMMITTED,
        ):
            import datetime
            now = datetime.datetime.now()
            closest = None
            closest_mins = 999999
            closest_diff_sec = 0.0
            for m in meetings:
                st = getattr(m, "start_time", None)
                if st:
                    diff_sec = (st - now).total_seconds()
                    diff_mins = int(diff_sec // 60)
                    if 0 <= diff_mins < closest_mins:
                        closest_mins = diff_mins
                        closest_diff_sec = diff_sec
                        closest = m

            if closest and closest_mins <= 15:
                countdown = "now" if closest_mins <= 0 else f"in {closest_mins}m"
                title = getattr(closest, "title", "Upcoming Meeting")
                platform = getattr(closest, "platform", "generic")
                if self._state not in (PillState.CARD_NOTIFICATION, PillState.L4_PREVIEW):
                    self.set_pill_state(
                        PillState.MINI_MEETING,
                        metadata={
                            "title": title,
                            "platform": platform,
                            "countdown_text": countdown,
                            "minutes_until_start": closest_mins,
                            "seconds_until_start": closest_diff_sec,
                            "active_meeting_count": len(meetings),
                        },
                        animated=True,
                    )
                elif hasattr(self, "lbl_countdown_timer"):
                    self.lbl_countdown_timer.setText(countdown)
            elif self._state == PillState.MINI_MEETING and not self._meeting_active:
                self.set_pill_state(PillState.RESTING, animated=True)

    def _on_controller_dictation_completed(self, final_text: str, target_desc: str, method: str) -> None:
        if self.controller:
            self._last_entry_id = getattr(self.controller, "_last_dictation_id", None)
        self.set_state(PillState.COMMITTED, "Cleaned up")

    def _connect_controller_signals(self) -> None:
        if not self.controller:
            return
        try:
            self.controller.dictation_state_changed.connect(self._on_controller_dictation_state)
            self.controller.dictation_level_changed.connect(self.set_audio_level)
            self.controller.dictation_interim_updated.connect(self.update_live_preview)
            self.controller.writing_mode_changed.connect(self._on_controller_mode_changed)
            self.controller.meeting_state_changed.connect(self._on_controller_meeting_state)
            self.controller.meeting_tick.connect(self._on_controller_meeting_tick)
            self.controller.theme_changed.connect(self.apply_theme)
            self.controller.flow_bar_edge_changed.connect(self.set_dock_edge)
            self.controller.target_window_changed.connect(self._on_target_window_changed)
            if hasattr(self.controller, "call_detected"):
                self.controller.call_detected.connect(self._on_controller_call_detected)
            if hasattr(self.controller, "call_ended"):
                self.controller.call_ended.connect(self._on_controller_call_ended)
            if hasattr(self.controller, "meeting_lifecycle_changed"):
                self.controller.meeting_lifecycle_changed.connect(self._on_controller_meeting_lifecycle)
            if hasattr(self.controller, "calendar_upcoming_changed"):
                self.controller.calendar_upcoming_changed.connect(self._on_calendar_upcoming_changed)
            if hasattr(self.controller, "dictation_completed"):
                self.controller.dictation_completed.connect(self._on_controller_dictation_completed)
        except Exception as exc:
            logger.debug("Optional controller signal hookup skipped: %s", exc)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        apply_non_activating_floating_style(hwnd)
        if self.controller:
            if hasattr(self.controller, "register_ui_hwnd"):
                self.controller.register_ui_hwnd(hwnd)
            cfg = getattr(self.controller, "config", None)
            if cfg:
                apply_capture_exclusion(hwnd, bool(getattr(cfg, "hide_in_screen_share", False)))
        self.dock_to_edge(self._dock_edge)

    def apply_capture_privacy(self, exclude: bool) -> None:
        apply_capture_exclusion(int(self.winId()), bool(exclude))

    def apply_theme(self, theme: str = "light") -> None:
        self._theme = (theme or "light").lower().strip()
        self.setStyleSheet(get_floating_pill_stylesheet(self._theme))
        self.preview_bubble.apply_theme(self._theme)
        self._sync_pill_visuals()

    def set_dock_edge(self, edge: str) -> None:
        val = (edge or "bottom").lower().strip()
        if val not in ("bottom", "left", "right"):
            val = "bottom"
        self._dock_edge = val
        self._sync_geometry()
        self.dock_to_edge(val)

    def dock_to_bottom_center(self) -> None:
        self.dock_to_edge("bottom")

    def dock_to_edge(self, edge: Optional[str] = None) -> None:
        target_edge = (edge or self._dock_edge or "bottom").lower()
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        margin = 10
        if target_edge == "left":
            x = avail.left() + margin
            y = avail.center().y() - h // 2
        elif target_edge == "right":
            x = avail.right() - w - margin
            y = avail.center().y() - h // 2
        else:
            x = avail.center().x() - w // 2
            y = avail.bottom() - h - margin
        # Clamp within available display work area
        x = max(avail.left() + margin, min(x, avail.right() - w - margin))
        y = max(avail.top() + margin, min(y, avail.bottom() - h - margin))
        self.move(x, y)
        if hasattr(self, "preview_bubble") and self.preview_bubble.isVisible():
            self.preview_bubble.update_position_above_pill(self.geometry())
        if hasattr(self, "hover_tooltip") and self.hover_tooltip.isVisible():
            self.hover_tooltip.position_above(self.geometry())

    def _sound_enabled(self) -> bool:
        if not self.controller:
            return True
        return bool(getattr(getattr(self.controller, "config", None), "sound_effects", True))

    def _reset_to_resting(self) -> None:
        if self._state in (PillState.COMMITTED, PillState.ERROR):
            self.set_state(PillState.READY if self._is_hovered else PillState.IDLE)

    def _advance_prismatic(self) -> None:
        self._prismatic_angle = (self._prismatic_angle + 8.0) % 360.0
        self.update()

    def set_state(self, state: PillState | str, message: str = "") -> None:
        norm_state = normalize_pill_state(state)
        if norm_state in (
            PillState.RESTING,
            PillState.MINI_MEETING,
            PillState.CARD_NOTIFICATION,
            PillState.ACTIVE_RECORDING,
            PillState.L4_PREVIEW,
        ):
            self.set_pill_state(norm_state, metadata={"message": message}, animated=False)
            return

        prev_state = self._state
        self._state = norm_state
        if norm_state != PillState.CARD_NOTIFICATION:
            self._expanded_from_mini = False
        self._status_message = message

        if norm_state == PillState.LISTENING:
            self._auto_reset_timer.stop()
            self._prismatic_timer.stop()
            self.hover_tooltip.hide()
            self.waveform_capsule.set_processing(False)
            if self.controller:
                self._is_ptt_session = not bool(getattr(self.controller, "is_hands_free_active", False))
            if prev_state != PillState.LISTENING:
                self.timer_widget.start()
                play_flow_chime("start", self._sound_enabled())
        elif norm_state == PillState.PROCESSING:
            self._auto_reset_timer.stop()
            self._prismatic_timer.stop()
            self.hover_tooltip.hide()
            self.waveform_capsule.set_processing(True)
            self.timer_widget.stop()
            play_flow_chime("stop", self._sound_enabled())
        elif norm_state == PillState.POLISH_PROCESSING:
            self._auto_reset_timer.stop()
            self._prismatic_timer.start()
            self.hover_tooltip.hide()
            self.waveform_capsule.set_processing(True)
        elif norm_state == PillState.COMMITTED:
            self._prismatic_timer.stop()
            self.waveform_capsule.set_processing(False)
            self.preview_bubble.hide()
            self._auto_reset_timer.start(1900)
            play_flow_chime("commit", self._sound_enabled())
        elif norm_state == PillState.ERROR:
            self._prismatic_timer.stop()
            self.waveform_capsule.set_processing(False)
            self.preview_bubble.hide()
            self._auto_reset_timer.start(2200)
            play_flow_chime("error", self._sound_enabled())
        else:
            self._prismatic_timer.stop()
            self.waveform_capsule.set_processing(False)
            self.timer_widget.stop()
            self.preview_bubble.hide()

        self.pill_stack.setCurrentIndex(0)
        self._sync_pill_visuals()
        self._sync_geometry()

    def _sync_pill_visuals(self) -> None:
        effective = self.pill_state

        if effective == PillState.RESTING:
            # Subtle collapsed resting bar at screen edge
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: rgba(0, 0, 0, 0.52); "
                "border: 1px solid rgba(255, 255, 255, 0.50); border-radius: 3px; }"
            )
            self.pill_stack.setCurrentIndex(0)
            self.btn_cancel.hide()
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.lbl_state_text.hide()
            self.btn_scratchpad.hide()
            self.waveform_capsule.hide()
        elif effective in (PillState.IDLE, PillState.READY):
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: rgba(18, 18, 20, 0.94); border: 1px solid rgba(255, 255, 255, 0.20); border-radius: 15px; }"
            )
            self.pill_stack.setCurrentIndex(0)
            self.btn_cancel.hide()
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.lbl_state_text.hide()
            self.waveform_capsule.set_visual_mode("dim")
            self.waveform_capsule.setFixedSize(34, 16)
            self.waveform_capsule.show()
            show_sp = True
            if self.controller and getattr(self.controller, "config", None):
                show_sp = bool(getattr(self.controller.config, "scratchpad_in_flow_bar", True))
            if show_sp and effective == PillState.READY:
                self.btn_scratchpad.show()
            else:
                self.btn_scratchpad.hide()
            if show_sp:
                self.btn_scratchpad.show()
        elif effective == PillState.LISTENING:
            self.pill_stack.setCurrentIndex(0)
            border_col = "#FFBCF2" if self._current_mode == TransformationMode.STRUCTURED else "#30302F"
            self.upper_pill.setStyleSheet(
                f"QFrame#flowBarPill {{ background-color: #000000; border: 1px solid {border_col}; border-radius: 15px; }}"
            )
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(40, 18)
            self.waveform_capsule.show()
            if not self._is_ptt_session:
                self.btn_cancel.show()
                self.btn_stop.setEnabled(True)
                self.btn_stop.show()
        elif effective == PillState.PROCESSING:
            self.pill_stack.setCurrentIndex(0)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1px solid #4D4A42; border-radius: 15px; }"
            )
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(32, 16)
            self.waveform_capsule.show()
            self.lbl_state_text.setText("Writing")
            self.lbl_state_text.show()
        elif effective == PillState.POLISH_PROCESSING:
            self.pill_stack.setCurrentIndex(0)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1.5px solid #F0D7FF; border-radius: 15px; }"
            )
            self.waveform_capsule.set_visual_mode("instruct")
            self.waveform_capsule.setFixedSize(32, 16)
            self.waveform_capsule.show()
            self.lbl_state_text.setText("Cleaning up")
            self.lbl_state_text.show()
        elif effective == PillState.COMMITTED:
            self.pill_stack.setCurrentIndex(0)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1px solid #369489; border-radius: 15px; }"
            )
            self.btn_check.show()
            self.lbl_state_text.setText("Cleaned up")
            self.lbl_state_text.show()
            self.btn_undo_toast.show()
        elif effective == PillState.ERROR:
            self.pill_stack.setCurrentIndex(0)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1.5px solid #FF5757; border-radius: 15px; }"
            )
            msg = self._status_message if len(self._status_message) <= 14 else "No audio"
            self.lbl_state_text.setText(msg or "Error")
            self.lbl_state_text.setStyleSheet("color: #FF8A8A; background: transparent; border: none;")
            self.lbl_state_text.show()
        elif effective == PillState.MINI_MEETING:
            self.pill_stack.setCurrentIndex(1)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1px solid #30302F; border-radius: 24px; }"
            )
            self._update_stack_badge()
        elif effective == PillState.CARD_NOTIFICATION:
            self.pill_stack.setCurrentIndex(2)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1px solid #30302F; border-radius: 16px; }"
            )
            self._update_stack_badge()
        elif effective == PillState.ACTIVE_RECORDING:
            self.pill_stack.setCurrentIndex(0)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1.5px solid #10B981; border-radius: 18px; }"
            )
            self.btn_cancel.hide()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(36, 18)
            self.waveform_capsule.show()
            self.lbl_state_text.setText(self._meeting_elapsed_str or "00:00")
            self.lbl_state_text.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")
            self.lbl_state_text.show()
            self.btn_stop.setEnabled(True)
            self.btn_stop.show()
        elif effective == PillState.L4_PREVIEW:
            self.pill_stack.setCurrentIndex(3)
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #000000; border: 1px solid #30302F; border-radius: 16px; }"
            )

        if effective not in (PillState.ERROR, PillState.ACTIVE_RECORDING):
            self.lbl_state_text.setStyleSheet("color: #FCFCFB; background: transparent; border: none;")

        if self._meeting_active and effective not in (
            PillState.MINI_MEETING,
            PillState.CARD_NOTIFICATION,
            PillState.ACTIVE_RECORDING,
        ):
            self.meeting_chip.setText(f"● {self._meeting_elapsed_str} Notetaker")
            self.meeting_chip.show()
        else:
            self.meeting_chip.hide()

    def _sync_geometry(self) -> None:
        effective = self.pill_state
        side_edge = self._dock_edge in ("left", "right")
        if effective == PillState.RESTING:
            pw, ph = (6, 30) if side_edge else (30, 6)
        elif effective in (PillState.IDLE, PillState.READY):
            pw, ph = (54, 30)
        elif effective == PillState.LISTENING:
            pw, ph = (74, 30) if self._is_ptt_session else (104, 30)
        elif effective == PillState.PROCESSING:
            pw, ph = (98, 30)
        elif effective == PillState.POLISH_PROCESSING:
            pw, ph = (136, 30)
        elif effective == PillState.COMMITTED:
            pw, ph = (152, 30)
        elif effective == PillState.ERROR:
            err_txt = self.lbl_state_text.text() or "Error"
            err_w = self.lbl_state_text.fontMetrics().horizontalAdvance(err_txt)
            pw, ph = (max(96, err_w + 32), 30)
        elif effective == PillState.MINI_MEETING:
            pw, ph = (416, 48)
        elif effective == PillState.CARD_NOTIFICATION:
            pw, ph = (416, 80)
        elif effective == PillState.ACTIVE_RECORDING:
            pw, ph = (120, 36)
        elif effective == PillState.L4_PREVIEW:
            pw, ph = (416, 322)
        else:
            pw, ph = (50, 30)

        self.target_size = QSize(pw, ph)
        self.upper_pill.setFixedSize(pw, ph)
        total_w = pw
        if self.btn_scratchpad.isVisible():
            total_w += 26
        if self.meeting_chip.isVisible() and effective not in (
            PillState.MINI_MEETING,
            PillState.CARD_NOTIFICATION,
            PillState.ACTIVE_RECORDING,
        ):
            total_w += self.meeting_chip.sizeHint().width() + 8
        total_h = max(
            ph,
            30
            if (
                self.meeting_chip.isVisible()
                and effective
                not in (
                    PillState.MINI_MEETING,
                    PillState.CARD_NOTIFICATION,
                    PillState.ACTIVE_RECORDING,
                )
            )
            else ph,
        )
        super().setFixedSize(total_w, total_h)
        if self.isVisible():
            self.dock_to_edge(self._dock_edge)

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        if self._auto_start_timer.isActive():
            self._auto_start_timer.stop()
            self._auto_start_paused = True

        if self._state in (PillState.IDLE, PillState.RESTING):
            self._sync_pill_visuals()
            self._sync_geometry()
            self.dock_to_edge(self._dock_edge)
            ptt = "Ctrl+Win"
            if self.controller and getattr(self.controller, "config", None):
                ptt = str(getattr(self.controller.config, "ptt_hotkey", "ctrl+win")).title()
            self.hover_tooltip.set_hint(f"Click or hold {ptt} to start dictating")
            self.hover_tooltip.show()
            self.hover_tooltip.position_above(self.geometry())
        elif self._state == PillState.MINI_MEETING:
            self._expanded_from_mini = True
            self.set_pill_state(PillState.CARD_NOTIFICATION, metadata=self._current_metadata)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self.hover_tooltip.hide()
        if self._state == PillState.CARD_NOTIFICATION and getattr(self, "_expanded_from_mini", False):
            self._expanded_from_mini = False
            self.set_pill_state(PillState.MINI_MEETING, metadata=self._current_metadata)
        elif self._state in (PillState.IDLE, PillState.RESTING):
            self._sync_pill_visuals()
            self._sync_geometry()
            self.dock_to_edge(self._dock_edge)
        super().leaveEvent(event)

    def set_audio_level(self, rms: float) -> None:
        self.waveform_capsule.set_level(rms)

    def set_mode(self, mode: TransformationMode | str) -> None:
        if isinstance(mode, TransformationMode):
            self._current_mode = mode
        else:
            try:
                self._current_mode = TransformationMode(str(mode).lower())
            except ValueError:
                self._current_mode = TransformationMode.CLEAN
        self.mode_changed.emit(self._current_mode.value)
        self._sync_pill_visuals()

    def _cycle_next_mode(self) -> None:
        order = [
            TransformationMode.CLEAN,
            TransformationMode.POLISHED,
            TransformationMode.STRUCTURED,
            TransformationMode.EMAIL,
            TransformationMode.VERBATIM,
        ]
        try:
            idx = order.index(self._current_mode)
        except ValueError:
            idx = 0
        next_m = order[(idx + 1) % len(order)]
        self.set_mode(next_m)
        if self.controller and hasattr(self.controller, "set_writing_mode"):
            self.controller.set_writing_mode(next_m)

    def _on_controller_mode_changed(self, mode_str: str) -> None:
        self.set_mode(mode_str)

    def _on_controller_dictation_state(self, state: str, msg: str) -> None:
        s = (state or "idle").lower().strip()
        if s in ("starting", "listening", "speaking"):
            self.set_state(PillState.LISTENING, msg)
        elif s in ("processing", "stopping"):
            self.set_state(PillState.PROCESSING, msg)
        elif s == "completed":
            if self.controller:
                self._last_entry_id = getattr(self.controller, "_last_dictation_id", None)
            self.set_state(PillState.COMMITTED, msg)
        elif s == "failed":
            self.set_state(PillState.ERROR, msg)
        else:
            self.set_state(PillState.IDLE, msg)

    def _on_controller_meeting_state(self, state: str, _msg: str) -> None:
        s = (state or "").lower().strip()
        self._meeting_active = s in ("recording", "paused")
        if self._meeting_active:
            if self._state != PillState.ACTIVE_RECORDING:
                self.set_pill_state(PillState.ACTIVE_RECORDING, animated=True)
        else:
            if self._state == PillState.ACTIVE_RECORDING:
                self.set_pill_state(PillState.RESTING, animated=True)
        self._sync_pill_visuals()
        self._sync_geometry()

    def _on_controller_meeting_tick(self, elapsed: float, *_args) -> None:
        mins, secs = divmod(int(max(0.0, elapsed)), 60)
        self._meeting_elapsed_str = f"{mins:02d}:{secs:02d}"
        if self._meeting_active:
            if hasattr(self, "meeting_chip"):
                self.meeting_chip.setText(f"● {self._meeting_elapsed_str} Notetaker")
            if self._state == PillState.ACTIVE_RECORDING and hasattr(self, "lbl_state_text"):
                self.lbl_state_text.setText(self._meeting_elapsed_str)
        if _args and self._state == PillState.ACTIVE_RECORDING:
            mic_lvl = float(_args[0]) if len(_args) > 0 else 0.0
            sys_lvl = float(_args[1]) if len(_args) > 1 else 0.0
            self.set_audio_level(max(mic_lvl, sys_lvl))

    def _on_target_window_changed(self, title: str, proc: str) -> None:
        if title and self._is_hovered:
            app_name = proc.replace(".exe", "") if proc else title[:24]
            self.hover_tooltip.set_hint(f"Dictate into {app_name}")
            if self.hover_tooltip.isVisible():
                self.hover_tooltip.position_above(self.geometry())

    def update_live_preview(self, provisional_text: str, stable_prefix: str = "") -> None:
        combined = " ".join(x for x in (stable_prefix.strip(), provisional_text.strip()) if x).strip()
        if not combined:
            self.preview_bubble.hide()
            return
        res = DictationProcessor.detect_verbal_self_corrections(combined)
        self.preview_bubble.update_preview(res.get("annotated_html") or combined)
        if self.isVisible():
            self.preview_bubble.show()
            self.preview_bubble.update_position_above_pill(self.geometry())

    def hide_widget(self) -> None:
        self.hover_tooltip.hide()
        self.preview_bubble.hide()
        self.hide()

    def set_recording(self, recording: bool) -> None:
        self.set_state(PillState.LISTENING if recording else PillState.IDLE)

    def _on_pill_clicked(self) -> None:
        if self._was_dragged:
            return
        if self._state in (PillState.IDLE, PillState.RESTING, PillState.READY, PillState.COMMITTED, PillState.ERROR):
            self.start_dictation_requested.emit()
            if self.controller and hasattr(self.controller, "toggle_dictation"):
                self.controller.toggle_dictation()
        elif self._state == PillState.LISTENING:
            self._on_stop_clicked()

    def _on_stop_clicked(self) -> None:
        if self._state == PillState.ACTIVE_RECORDING or self._meeting_active:
            if self.controller and hasattr(self.controller, "stop_meeting_recording"):
                try:
                    self.controller.stop_meeting_recording()
                except Exception as exc:
                    logger.debug("stop_meeting_recording trigger: %s", exc)
            self.set_pill_state(PillState.RESTING, animated=True)
            return

        self.finalize_requested.emit()
        if self.on_finalize:
            self.on_finalize()
        elif self.controller and hasattr(self.controller, "finalize_active_dictation"):
            self.controller.finalize_active_dictation()

    def _on_cancel_clicked(self) -> None:
        self.cancel_requested.emit()
        if self.on_cancel:
            self.on_cancel()
        elif self.controller and hasattr(self.controller, "cancel_dictation"):
            self.controller.cancel_dictation()

    def _on_undo_last(self) -> None:
        if self.controller and self._last_entry_id and hasattr(self.controller, "undo_ai_edit"):
            self.controller.undo_ai_edit(self._last_entry_id)
        self.set_state(PillState.IDLE)

    def _on_open_scratchpad(self) -> None:
        self.open_scratchpad_requested.emit()
        if self.controller and hasattr(self.controller, "open_scratchpad_requested"):
            self.controller.open_scratchpad_requested.emit()

    def _on_meeting_chip_clicked(self) -> None:
        self.open_notetaker_requested.emit()
        if self.controller and hasattr(self.controller, "open_notetaker_drawer_requested"):
            self.controller.open_notetaker_drawer_requested.emit()

    def _on_open_settings(self) -> None:
        if self.on_open_dashboard:
            self.on_open_dashboard(7)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._was_dragged = False
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start_pos - self.frameGeometry().topLeft()
            if delta.manhattanLength() > 4:
                self._was_dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_drag = self._was_dragged
            self._dragging = False
            if was_drag:
                # Snap to nearest screen edge cleanly
                screen = QGuiApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    c = self.geometry().center()
                    dist_bottom = abs(avail.bottom() - c.y())
                    dist_left = abs(c.x() - avail.left())
                    dist_right = abs(avail.right() - c.x())
                    edge = "bottom"
                    if dist_left < dist_bottom and dist_left < dist_right:
                        edge = "left"
                    elif dist_right < dist_bottom and dist_right < dist_left:
                        edge = "right"
                    if self.controller and hasattr(self.controller, "set_flow_bar_edge"):
                        self.controller.set_flow_bar_edge(edge)
                    else:
                        self.set_dock_edge(edge)
                self._was_dragged = False
                return
            self._on_pill_clicked()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.on_open_dashboard:
            self.on_open_dashboard(0)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.hover_tooltip.hide()
        menu = QMenu(self)
        menu.setStyleSheet(get_floating_pill_stylesheet(self._theme))

        act_hide = menu.addAction("Hide this for 1 hour")
        act_hide.triggered.connect(
            lambda: self.controller.hide_flow_bar_temporarily(1.0)
            if (self.controller and hasattr(self.controller, "hide_flow_bar_temporarily"))
            else self.hide_widget()
        )
        act_mute = menu.addAction("Mute reminders for 12 hours")
        act_mute.triggered.connect(
            lambda: self.controller.mute_reminders_temporarily(12.0)
            if (self.controller and hasattr(self.controller, "mute_reminders_temporarily"))
            else None
        )
        menu.addSeparator()

        act_hub = menu.addAction("Transcript history (Open VOX)")
        act_hub.triggered.connect(lambda: self.on_open_dashboard(0) if self.on_open_dashboard else None)

        act_sp = menu.addAction("Open Scratchpad")
        act_sp.triggered.connect(self._on_open_scratchpad)

        act_nt = menu.addAction(
            "Open Notetaker Drawer" if self._meeting_active else "Start Notetaker Recording"
        )
        act_nt.triggered.connect(
            self._on_meeting_chip_clicked
            if self._meeting_active
            else (
                lambda: self.controller.toggle_meeting_recording()
                if (self.controller and hasattr(self.controller, "toggle_meeting_recording"))
                else None
            )
        )

        act_paste = menu.addAction("Paste last transcript")
        act_paste.triggered.connect(
            lambda: self.controller.paste_last_dictation()
            if (self.controller and hasattr(self.controller, "paste_last_dictation"))
            else None
        )
        act_copy = menu.addAction("Copy last transcript")
        act_copy.triggered.connect(
            lambda: self.controller.copy_last_dictation()
            if (self.controller and hasattr(self.controller, "copy_last_dictation"))
            else None
        )

        menu.addSeparator()

        # Writing Style / Mode submenu
        style_menu = menu.addMenu("Writing Style")
        for lbl, mode_enum in [
            ("Clean (Default)", TransformationMode.CLEAN),
            ("Polished", TransformationMode.POLISHED),
            ("Structured", TransformationMode.STRUCTURED),
            ("Email", TransformationMode.EMAIL),
            ("Verbatim", TransformationMode.VERBATIM),
        ]:
            act_m = style_menu.addAction(lbl)
            act_m.setCheckable(True)
            act_m.setChecked(self._current_mode == mode_enum)
            act_m.triggered.connect(
                lambda _c=False, me=mode_enum: (
                    self.set_mode(me),
                    self.controller.set_writing_mode(me)
                    if (self.controller and hasattr(self.controller, "set_writing_mode"))
                    else None,
                )
            )

        # Real Microphone submenu
        mic_menu = menu.addMenu("Microphone")
        try:
            from voxlocal.audio.devices import AudioDeviceManager

            devices = AudioDeviceManager.list_input_devices()
            curr_idx = getattr(getattr(self.controller, "config", None), "mic_device_index", None)
            act_default = mic_menu.addAction("System Default Microphone")
            act_default.setCheckable(True)
            act_default.setChecked(curr_idx is None)
            act_default.triggered.connect(lambda: self._select_mic(None, ""))
            for dev in devices[:10]:
                act_d = mic_menu.addAction(dev.name[:44])
                act_d.setCheckable(True)
                act_d.setChecked(curr_idx == dev.index)
                act_d.triggered.connect(lambda _c=False, idx=dev.index, nm=dev.name: self._select_mic(idx, nm))
        except Exception:
            act_none = mic_menu.addAction("Default Device")
            act_none.setEnabled(False)

        # Language & Style submenus
        lang_menu = menu.addMenu("Language")
        curr_lang = getattr(getattr(self.controller, "config", None), "language", "en+hi")
        for lbl, code in [
            ("Auto (English + Hindi)", "en+hi"),
            ("English Only", "en"),
            ("Hinglish (Roman Script)", "hi_latn"),
            ("Hindi (Devanagari)", "hi"),
        ]:
            act_l = lang_menu.addAction(lbl)
            act_l.setCheckable(True)
            act_l.setChecked(curr_lang == code)
            act_l.triggered.connect(
                lambda _c=False, lc=code: self.controller.set_language_mode(lc)
                if (self.controller and hasattr(self.controller, "set_language_mode"))
                else None
            )

        dock_menu = menu.addMenu("Position Flow Bar")
        for lbl, edge in [("Bottom", "bottom"), ("Left edge", "left"), ("Right edge", "right")]:
            act_e = dock_menu.addAction(lbl)
            act_e.setCheckable(True)
            act_e.setChecked(self._dock_edge == edge)
            act_e.triggered.connect(
                lambda _c=False, e=edge: (
                    self.controller.set_flow_bar_edge(e)
                    if (self.controller and hasattr(self.controller, "set_flow_bar_edge"))
                    else self.set_dock_edge(e)
                )
            )

        menu.addSeparator()
        act_settings = menu.addAction("Settings")
        act_settings.triggered.connect(self._on_open_settings)

        if self.on_exit_app:
            act_quit = menu.addAction("Quit VOX")
            act_quit.triggered.connect(self.on_exit_app)

        menu.exec(event.globalPos())

    def _select_mic(self, dev_index: Optional[int], dev_name: str) -> None:
        if not self.controller or not getattr(self.controller, "config", None):
            return
        self.controller.config.mic_device_index = dev_index
        self.controller.config.mic_device_name = dev_name
        self.controller.config.save()


# Alias preserving imports across controller, tests, and packaging
DualTierFloatingPill = VoxFlowBar

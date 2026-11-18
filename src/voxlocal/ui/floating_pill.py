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
    """Wispr-faithful #080808 full-capsule tooltip with medium action text and bold hotkey hint."""

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
            "QFrame { background-color: #080808; border: 1px solid rgba(255, 255, 255, 0.14); "
            "border-radius: 16px; }"
        )
        c_lay = QHBoxLayout(self.card)
        c_lay.setContentsMargins(14, 0, 14, 0)
        c_lay.setSpacing(6)
        self.card.setFixedHeight(32)

        self.lbl_text = QLabel("Dictate <b>Ctrl+Win</b>")
        self.lbl_text.setTextFormat(Qt.TextFormat.RichText)
        f = get_ui_font(12, 500)
        f.setFamilies(["Segoe UI Variable", "Segoe UI Variable Text", "Segoe UI", "Inter"])
        f.setPixelSize(13)
        self.lbl_text.setFont(f)
        self.lbl_text.setStyleSheet("color: #F4F4F5; border: none; background: transparent;")
        c_lay.addWidget(self.lbl_text)
        layout.addWidget(self.card)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def set_hint(self, html_or_text: str) -> None:
        self.lbl_text.setText(html_or_text)
        self.lbl_text.adjustSize()
        self.card.adjustSize()
        self.adjustSize()

    def position_above(self, pill_rect: QRect) -> None:
        self.position_adjacent_to(pill_rect, "bottom", gap=10)

    def position_adjacent_to(self, target_rect: QRect, dock_edge: str = "bottom", gap: int = 10) -> None:
        self.adjustSize()
        sh = self.sizeHint()
        w = max(sh.width(), self.width())
        h = max(32, sh.height())
        edge = (dock_edge or "bottom").lower()
        if edge == "right":
            x = target_rect.left() - w - gap
            y = target_rect.center().y() - h // 2
        elif edge == "left":
            x = target_rect.right() + gap
            y = target_rect.center().y() - h // 2
        else:
            x = target_rect.center().x() - w // 2
            y = target_rect.top() - h - gap

        screen = QGuiApplication.screenAt(target_rect.center()) or QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - w - 8))
            y = max(avail.top() + 8, min(y, avail.bottom() - h - 8))
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


# =========================================================================
# MILESTONE 1: FLOATING TOGGLE BAR RECONSTRUCTION (WHISPERFLOW PARITY)
# =========================================================================

class VoxRestrainedTooltip(QWidget):
    """
    Visually restrained non-activating floating micro-tooltip.
    Dark obsidian card (#181716), 1px border (#2D2B28), 6px radius, legible 11px text (#EDEAE3).
    Displays with a 160ms delay and positions adjacent to the target button inside the screen.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._show_animated)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.valueChanged.connect(self._on_anim_val)

        self._opacity: float = 0.0
        self._text: str = ""
        self._target_rect: QRect = QRect()
        self._dock_edge: str = "right"

        self._lbl = QLabel("", self)
        self._lbl.setFont(get_ui_font(11, 500))
        self._lbl.setStyleSheet("color: #EDEAE3; background: transparent; padding: 4px 8px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._lbl)

    def _on_anim_val(self, val: Any) -> None:
        self._opacity = float(val)
        self.setWindowOpacity(max(0.0, min(1.0, self._opacity)))
        self.update()

    def show_for_rect(self, text: str, target_rect: QRect, dock_edge: str = "right") -> None:
        if not text:
            self.hide_tooltip()
            return
        self._text = text
        self._target_rect = target_rect
        self._dock_edge = dock_edge
        self._lbl.setText(text)
        self.adjustSize()
        self._timer.start()

    def hide_tooltip(self) -> None:
        self._timer.stop()
        self._anim.stop()
        self._opacity = 0.0
        self.hide()

    def _show_animated(self) -> None:
        if not self._text:
            return
        self.adjustSize()
        sz = self.sizeHint()
        tw = sz.width()
        th = sz.height()

        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        tr = self._target_rect
        if self._dock_edge == "right":
            # Show to the left of the button (inner screen)
            x = tr.left() - tw - 8
            y = tr.center().y() - th // 2
        elif self._dock_edge == "left":
            # Show to the right of the button
            x = tr.right() + 8
            y = tr.center().y() - th // 2
        else:
            # Bottom dock: show above the button
            x = tr.center().x() - tw // 2
            y = tr.top() - th - 8

        x = max(avail.left() + 4, min(avail.right() - tw - 4, x))
        y = max(avail.top() + 4, min(avail.bottom() - th - 4, y))

        self.move(x, y)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, event) -> None:
        if self._opacity <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(self._opacity)
        r = self.rect()
        rf = QRectF(r.adjusted(0, 0, -1, -1))

        painter.setBrush(QColor(22, 21, 20, 245))
        painter.setPen(QPen(QColor(45, 43, 40, 220), 1.0))
        painter.drawRoundedRect(rf, 6.0, 6.0)


class VoxEqualizerControl(QWidget):
    """
    Crisp 6-bar equalizer/waveform button inside Active Recording pill.
    - Bottom dock: 6 vertical bars arranged horizontally (like WhisperFlow `13_hover_notepad_bottom_tooltip`).
    - Side dock (left/right): 6 horizontal bars stacked vertically (like `12_docked_right_hover_tooltip_right`).
    Clicks toggle Notepad/Notetaker drawer; hovering emits rect for `Show Notepad` / `Hide Notepad` tooltip.
    """

    clicked = pyqtSignal()
    hover_changed = pyqtSignal(bool, QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hovered: bool = False
        self._active: bool = False
        self._dimmed: bool = False
        self._dock_edge: str = "bottom"
        self._level: float = 0.0
        self._smooth_level: float = 0.0
        self._phase: float = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(45)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        if self._dock_edge in ("left", "right"):
            self.setFixedSize(22, 28)
        else:
            self.setFixedSize(28, 22)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        self._dimmed = bool(dimmed)
        self.update()

    def set_level(self, lvl: float) -> None:
        self._level = max(0.0, min(1.0, float(lvl)))

    def _tick(self) -> None:
        self._phase += 0.28
        self._smooth_level += (self._level - self._smooth_level) * 0.32
        if self._smooth_level > 0.015 or self._hovered:
            self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        g_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self.hover_changed.emit(True, g_rect)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        self.hover_changed.emit(False, QRect())
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Base 6-bar iconic silhouette (exact Wispr proportions: dot, short, mid, tall, short, dot)
        base_lens = [2.2, 5.2, 8.8, 12.2, 5.6, 2.2]
        alpha = 135 if self._dimmed else (255 if (self._hovered or self._active) else 238)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, alpha))

        thickness = 1.8
        gap = 2.0
        n = len(base_lens)
        span = n * thickness + (n - 1) * gap
        side = self._dock_edge in ("left", "right")

        for i, base_l in enumerate(base_lens):
            amp = self._smooth_level
            if amp > 0.03 and not self._dimmed:
                wave = 0.65 + 0.55 * abs(math.sin(self._phase + i * 0.85))
                cur_l = min(14.5, max(2.2, base_l * (0.75 + amp * 1.45 * wave)))
            else:
                cur_l = base_l
            pos = -(span / 2.0) + i * (thickness + gap)
            if side:
                # Vertical capsule: horizontal bars stacked vertically
                painter.drawRoundedRect(
                    QRectF(cx - cur_l / 2.0, cy + pos, cur_l, thickness),
                    0.9,
                    0.9,
                )
            else:
                # Bottom capsule: vertical bars lined horizontally
                painter.drawRoundedRect(
                    QRectF(cx + pos, cy - cur_l / 2.0, thickness, cur_l),
                    0.9,
                    0.9,
                )


class VoxAudioVisualizer(QWidget):
    """Subtle auxiliary 3-bar level indicator kept for module compatibility."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(26, 16)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._level: float = 0.0
        self._smooth_level: float = 0.0

    def set_level(self, lvl: float) -> None:
        self._level = max(0.0, min(1.0, float(lvl)))
        self._smooth_level += (self._level - self._smooth_level) * 0.45
        self.update()

    def paintEvent(self, event) -> None:
        if self._smooth_level <= 0.04:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#30C382"))
        bar_w = 2.0
        gap = 3.0
        h_c = max(3.0, min(12.0, self._smooth_level * 14.0))
        h_s = max(2.5, min(9.0, self._smooth_level * 9.0))
        painter.drawRoundedRect(QRectF(cx - 5.0, cy - h_s / 2.0, bar_w, h_s), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(cx - 1.0, cy - h_c / 2.0, bar_w, h_c), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(cx + 3.0, cy - h_s / 2.0, bar_w, h_s), 1.0, 1.0)


class VoxStopControl(QWidget):
    """
    Wispr-faithful Stop button inside Active Recording pill:
    - Neutral dark charcoal circle (#363634, hover #484846) with centered 7x7 white rounded square.
    - Finishing mode (`set_finishing(True)`): 8-spoke white radial spinner (`15_finishing_up.png`).
    """

    clicked = pyqtSignal()
    hover_changed = pyqtSignal(bool, QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hovered: bool = False
        self._is_recording: bool = False
        self._finishing: bool = False
        self._spinner_step: int = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(75)
        self._spin_timer.timeout.connect(self._on_spin)

    def set_recording(self, recording: bool) -> None:
        self._is_recording = bool(recording)
        if not recording:
            self.set_finishing(False)
        self.update()

    def set_finishing(self, finishing: bool) -> None:
        self._finishing = bool(finishing)
        if self._finishing:
            self.setCursor(Qt.CursorShape.WaitCursor)
            self._spin_timer.start()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._spin_timer.stop()
        self.update()

    def _on_spin(self) -> None:
        self._spinner_step = (self._spinner_step + 1) % 8
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        g_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self.hover_changed.emit(True, g_rect)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        self.hover_changed.emit(False, QRect())
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._finishing:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = self.width() / 2.0
        cy = self.height() / 2.0

        if self._finishing:
            # 8-spoke radial white spinner matching Wispr finishing-up state
            num_spokes = 8
            for i in range(num_spokes):
                rel = (i - self._spinner_step) % num_spokes
                alpha = int(45 + (rel / float(num_spokes - 1)) * 210)
                angle_rad = i * (2.0 * math.pi / num_spokes)
                r_in, r_out = 3.2, 7.2
                x1 = cx + math.cos(angle_rad) * r_in
                y1 = cy + math.sin(angle_rad) * r_in
                x2 = cx + math.cos(angle_rad) * r_out
                y2 = cy + math.sin(angle_rad) * r_out
                pen = QPen(QColor(255, 255, 255, alpha), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            return

        bg = QColor("#484846" if self._hovered else "#363634")
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawEllipse(QPointF(cx, cy), 10.0, 10.0)

        # Centered crisp 7x7 white rounded-square stop glyph
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(cx - 3.5, cy - 3.5, 7.0, 7.0), 1.6, 1.6)


class VoxSatelliteChevronButton(QWidget):
    """
    Separate circular 28x28 chevron button beside (bottom) or below (left/right) the Active Recording pill.
    Appears smoothly on hover or while the Contextual Meeting Panel is open.
    """

    clicked = pyqtSignal()
    hover_changed = pyqtSignal(bool, QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dock_edge: str = "bottom"
        self._hovered: bool = False
        self._opacity: float = 0.0
        self._active: bool = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.valueChanged.connect(self._on_anim_val)

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if active and self._opacity < 0.99:
            self._opacity = 1.0
        self.update()

    def animate_reveal(self) -> None:
        self._anim.stop()
        self._opacity = max(self._opacity, 0.35)
        self.update()
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def animate_fade(self) -> None:
        if self._active:
            return
        self._anim.stop()
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.InCubic))
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_val(self, val: Any) -> None:
        self._opacity = float(val)
        self.setWindowOpacity(max(0.0, min(1.0, self._opacity)))
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        g_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self.hover_changed.emit(True, g_rect)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        self.hover_changed.emit(False, QRect())
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._opacity > 0.05:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        if self._opacity <= 0.02 and not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(max(self._opacity, 1.0 if self._active else 0.0))

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        r = 13.0

        bg = QColor("#222224" if (self._hovered or self._active) else "#080808")
        border = QColor(255, 255, 255, 55 if (self._hovered or self._active) else 36)
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor("#FFFFFF" if (self._hovered or self._active) else "#E4E4E7"), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        path = QPainterPath()
        if self._dock_edge == "right":
            path.moveTo(cx + 2.2, cy - 4.2)
            path.lineTo(cx - 2.2, cy)
            path.lineTo(cx + 2.2, cy + 4.2)
        elif self._dock_edge == "left":
            path.moveTo(cx - 2.2, cy - 4.2)
            path.lineTo(cx + 2.2, cy)
            path.lineTo(cx - 2.2, cy + 4.2)
        else:
            path.moveTo(cx - 4.2, cy + 2.0)
            path.lineTo(cx, cy - 2.2)
            path.lineTo(cx + 4.2, cy + 2.0)
        painter.drawPath(path)


class VoxDictateButton(QWidget):
    """Left capsule of the Wispr Ready bar: 48x30 obsidian pill with crisp white mic icon."""

    clicked = pyqtSignal()
    hover_changed = pyqtSignal(bool, QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(48, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hovered: bool = False
        self._dock_edge: str = "bottom"
        self._is_down: bool = False

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        if self._dock_edge in ("left", "right"):
            self.setFixedSize(30, 44)
        else:
            self.setFixedSize(48, 30)
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        self.hover_changed.emit(True, QRect(self.mapToGlobal(QPoint(0, 0)), self.size()))
        try:
            super().enterEvent(event)
        except TypeError:
            pass

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        self.hover_changed.emit(False, QRect())
        try:
            super().leaveEvent(event)
        except TypeError:
            pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_down = True
            bar = self.window()
            if hasattr(bar, "_begin_drag"):
                bar._begin_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_down:
            bar = self.window()
            if hasattr(bar, "_step_drag"):
                bar._step_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_down:
            self._is_down = False
            bar = self.window()
            was_drag = False
            if hasattr(bar, "_end_drag"):
                was_drag = bar._end_drag(event.globalPosition().toPoint())
            if not was_drag:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = min(w, h) / 2.0

        fill = QColor("#222225" if self._hovered else "#080808")
        rim = QColor(255, 255, 255, 48 if self._hovered else 28)
        painter.setBrush(fill)
        painter.setPen(QPen(rim, 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad - 0.5, rad - 0.5)

        # Centered white Microphone icon
        cx, cy = w / 2.0, h / 2.0
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        # Mic capsule
        painter.drawRoundedRect(QRectF(cx - 2.6, cy - 6.6, 5.2, 8.2), 2.6, 2.6)
        # U-cup arc + stem + base
        pen = QPen(QColor("#FFFFFF"), 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(cx - 4.7, cy - 2.4, 9.4, 6.8), 180 * 16, 180 * 16)
        painter.drawLine(QPointF(cx, cy + 4.4), QPointF(cx, cy + 6.6))
        painter.drawLine(QPointF(cx - 2.5, cy + 6.6), QPointF(cx + 2.5, cy + 6.6))


class VoxNotetakerButton(QWidget):
    """
    Right/Bottom capsule of the Wispr Ready bar:
    - Fixed 30x30 capsule displaying the upcoming meetings chevron arrow.
    - Arrow direction responds to dock edge (UP for bottom, LEFT for right, RIGHT for left).
    - Clicking opens/toggles the Upcoming Meetings popover (never starts dictation).
    - Fixed geometry with zero layout jitter on hover.
    """

    notetaker_clicked = pyqtSignal()
    chevron_clicked = pyqtSignal()
    hover_zone_changed = pyqtSignal(str, bool, QRect)
    size_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dock_edge: str = "bottom"
        self._expanded_extent: float = 30.0
        self._hovered: bool = False
        self._hover_zone: str = "chevron"
        self._is_down: bool = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(30, 30)

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        self.setFixedSize(30, 30)
        self.update()

    def set_expanded(self, expanded: bool) -> None:
        # Keep size fixed to prevent layout sliding on hover
        self._expanded_extent = 30.0
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._hover_zone = "chevron"
        self.update()
        self.hover_zone_changed.emit("chevron", True, QRect(self.mapToGlobal(QPoint(0, 0)), self.size()))
        try:
            super().enterEvent(event)
        except TypeError:
            pass

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_down:
            bar = self.window()
            if hasattr(bar, "_step_drag"):
                bar._step_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._hover_zone = ""
        self.update()
        self.hover_zone_changed.emit("", False, QRect())
        try:
            super().leaveEvent(event)
        except TypeError:
            pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_down = True
            bar = self.window()
            if hasattr(bar, "_begin_drag"):
                bar._begin_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_down:
            self._is_down = False
            bar = self.window()
            was_drag = False
            if hasattr(bar, "_end_drag"):
                was_drag = bar._end_drag(event.globalPosition().toPoint())
            if not was_drag:
                # Clicked arrow button: toggle upcoming meetings popover!
                self.chevron_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = min(w, h) / 2.0

        fill = QColor("#222225" if self._hovered else "#080808")
        rim = QColor(255, 255, 255, 48 if self._hovered else 28)
        painter.setBrush(fill)
        painter.setPen(QPen(rim, 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad - 0.5, rad - 0.5)

        # Centered white chevron arrow pointing towards popover opening direction
        cx, cy = w / 2.0, h / 2.0
        arrow_color = QColor("#FFFFFF") if self._hovered else QColor("#E4E4E7")
        pen = QPen(arrow_color, 1.75)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        if self._dock_edge == "right":
            # Docked on right edge: popover opens leftward -> arrow points LEFT (◀)
            path.moveTo(cx + 2.2, cy - 4.5)
            path.lineTo(cx - 2.5, cy)
            path.lineTo(cx + 2.2, cy + 4.5)
        elif self._dock_edge == "left":
            # Docked on left edge: popover opens rightward -> arrow points RIGHT (▶)
            path.moveTo(cx - 2.2, cy - 4.5)
            path.lineTo(cx + 2.5, cy)
            path.lineTo(cx - 2.2, cy + 4.5)
        elif self._dock_edge == "top":
            # Docked on top edge: popover opens downward -> arrow points DOWN (▼)
            path.moveTo(cx - 4.5, cy - 2.2)
            path.lineTo(cx, cy + 2.5)
            path.lineTo(cx + 4.5, cy - 2.2)
        else:
            # Docked on bottom edge (default): popover opens upward -> arrow points UP (▲)
            path.moveTo(cx - 4.5, cy + 2.2)
            path.lineTo(cx, cy - 2.5)
            path.lineTo(cx + 4.5, cy + 2.2)
        painter.drawPath(path)


class VoxActiveDictationPill(QWidget):
    """
    Wispr `07_dictate_listening_idle.png` active dictation capsule (101x29):
    Left circular #3A3A3C Cancel (✕) -> Center live white waveform -> Right solid #FFFFFF Finish (✓).
    """

    cancel_clicked = pyqtSignal()
    finish_clicked = pyqtSignal()
    hover_zone_changed = pyqtSignal(str, bool, QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(102, 30)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover_zone: str = ""
        self._is_ptt: bool = False
        self._is_processing: bool = False
        self._is_polish: bool = False
        self._border_col: QColor = QColor(255, 255, 255, 42)
        self._wave = DynamicWaveformCapsule(self, bar_count=9)
        self._wave.setFixedSize(44, 18)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 4, 28, 4)
        lay.setSpacing(0)
        lay.addWidget(self._wave, 0, Qt.AlignmentFlag.AlignCenter)

    def set_level(self, rms: float) -> None:
        self._wave.set_level(rms)

    def set_processing(self, proc: bool, polish: bool = False) -> None:
        self._is_processing = bool(proc)
        self._is_polish = bool(polish)
        self._wave.set_processing(proc)
        self._wave.set_visual_mode("instruct" if polish else "speech")
        self._border_col = QColor("#FFBCF2") if polish else QColor(255, 255, 255, 42)
        self.update()

    def set_ptt_mode(self, ptt: bool) -> None:
        self._is_ptt = bool(ptt)
        if ptt:
            self.setFixedSize(72, 30)
            self.layout().setContentsMargins(10, 4, 10, 4)
        else:
            self.setFixedSize(102, 30)
            self.layout().setContentsMargins(28, 4, 28, 4)
        self.update()

    def _zone_at(self, pos: QPoint) -> str:
        if self._is_ptt:
            return ""
        if pos.x() <= 27:
            return "cancel"
        if pos.x() >= self.width() - 27:
            return "finish"
        return ""

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        zone = self._zone_at(event.pos())
        if zone != self._hover_zone:
            self._hover_zone = zone
            self.setCursor(Qt.CursorShape.PointingHandCursor if zone else Qt.CursorShape.ArrowCursor)
            self.update()
            if zone:
                btn_x = 4 if zone == "cancel" else self.width() - 25
                g_rect = QRect(self.mapToGlobal(QPoint(btn_x, 0)), QSize(22, self.height()))
                self.hover_zone_changed.emit(zone, True, g_rect)
            else:
                self.hover_zone_changed.emit("", False, QRect())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_zone:
            self._hover_zone = ""
            self.update()
            self.hover_zone_changed.emit("", False, QRect())
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            zone = self._zone_at(event.pos())
            if zone == "cancel":
                self.cancel_clicked.emit()
                event.accept()
                return
            if zone == "finish":
                self.finish_clicked.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = h / 2.0

        painter.setBrush(QColor("#080808"))
        painter.setPen(QPen(self._border_col, 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad - 0.5, rad - 0.5)

        if self._is_ptt:
            return

        cy = h / 2.0
        # Left circular #3A3A3C Cancel button
        ccx = 15.0
        c_bg = QColor("#505054" if self._hover_zone == "cancel" else "#3A3A3C")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c_bg)
        painter.drawEllipse(QPointF(ccx, cy), 10.0, 10.0)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(ccx - 3.0, cy - 3.0), QPointF(ccx + 3.0, cy + 3.0))
        painter.drawLine(QPointF(ccx + 3.0, cy - 3.0), QPointF(ccx - 3.0, cy + 3.0))

        # Right solid white circular Finish button with dark checkmark
        fcx = w - 15.0
        f_bg = QColor("#E4E4E7" if self._hover_zone == "finish" else "#FFFFFF")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(f_bg)
        painter.drawEllipse(QPointF(fcx, cy), 10.0, 10.0)
        chk_pen = QPen(QColor("#141414"), 1.75, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(chk_pen)
        painter.drawLine(QPointF(fcx - 3.8, cy + 0.2), QPointF(fcx - 1.0, cy + 3.0))
        painter.drawLine(QPointF(fcx - 1.0, cy + 3.0), QPointF(fcx + 4.0, cy - 2.6))


class VoxDictationCancelledToast(QWidget):
    """
    Wispr `08_dictate_cancelled_toast.png` pill (`230x45`, border-radius `22.5px`):
    "Transcript cancelled" + "Undo" button + animated 2px white bottom progress bar.
    """

    undo_clicked = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(230, 45)

        self._progress: float = 1.0
        self._hovered: bool = False
        self._duration_ms: int = 3600
        self._elapsed_ms: int = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(30)
        self._tick_timer.timeout.connect(self._on_tick)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 6, 8, 8)
        lay.setSpacing(10)

        self.lbl_msg = QLabel("Transcript cancelled", self)
        f = get_ui_font(12, 500)
        f.setFamilies(["Segoe UI Variable", "Segoe UI Variable Text", "Segoe UI"])
        f.setPixelSize(12)
        self.lbl_msg.setFont(f)
        self.lbl_msg.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        lay.addWidget(self.lbl_msg, 1)

        self.btn_undo = QPushButton("Undo", self)
        self.btn_undo.setFixedSize(54, 30)
        self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_undo.setStyleSheet(
            "QPushButton { background-color: #2C2C2E; color: #FFFFFF; border: none; "
            "border-radius: 10px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background-color: #3D3D40; }"
        )
        self.btn_undo.clicked.connect(self._on_undo)
        lay.addWidget(self.btn_undo)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def trigger(self, pill_rect: QRect, dock_edge: str = "bottom") -> None:
        self._elapsed_ms = 0
        self._progress = 1.0
        self._hovered = False

        screen = QGuiApplication.screenAt(pill_rect.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        edge = (dock_edge or "bottom").lower()
        if edge == "right":
            x = avail.right() - self.width() - 12
            y = pill_rect.center().y() - self.height() // 2
        elif edge == "left":
            x = avail.left() + 12
            y = pill_rect.center().y() - self.height() // 2
        else:
            x = pill_rect.center().x() - self.width() // 2
            y = avail.bottom() - self.height() - 12

        x = max(avail.left() + 8, min(x, avail.right() - self.width() - 8))
        y = max(avail.top() + 8, min(y, avail.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()
        self.update()
        self._tick_timer.start()

    def _on_undo(self) -> None:
        self._tick_timer.stop()
        self.hide()
        self.undo_clicked.emit()

    def _on_tick(self) -> None:
        if self._hovered:
            return
        self._elapsed_ms += 30
        self._progress = max(0.0, 1.0 - (self._elapsed_ms / float(self._duration_ms)))
        self.update()
        if self._progress <= 0.0:
            self._tick_timer.stop()
            self.hide()
            self.dismissed.emit()

    def enterEvent(self, event) -> None:
        self._hovered = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = h / 2.0

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad, rad)
        painter.setClipPath(clip)

        painter.setBrush(QColor("#141416"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(0.0, 0.0, w, h), rad, rad)

        # Bottom countdown bar track + crisp 2px white sweep
        painter.setBrush(QColor(255, 255, 255, 32))
        painter.drawRect(QRectF(0.0, h - 2.5, w, 2.5))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRect(QRectF(0.0, h - 2.5, w * self._progress, 2.5))

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad - 0.5, rad - 0.5)


class VoxUpcomingNotesPopover(QWidget):
    """
    Wispr `03_upcoming_card.png` compact popover (`422x76`, radius `18px`, `#191919` bg):
    Platform badge + Meeting title + time range + `Start notes` action button.
    """

    start_notes_clicked = pyqtSignal(object)  # meeting object or None
    open_vox_clicked = pyqtSignal()

    def __init__(self, controller: Optional[Any] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.controller = controller
        self._meeting: Optional[Any] = None
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(422, 76)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setStyleSheet(
            "QFrame { background-color: #191919; border: 1px solid rgba(255, 255, 255, 0.12); "
            "border-radius: 18px; }"
        )
        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(18, 12, 16, 12)
        lay.setSpacing(14)

        self.badge = PlatformBadgeWidget(self.card, badge_size=28)
        lay.addWidget(self.badge)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        self.lbl_title = QLabel("Start a New Note", self.card)
        tf = get_ui_font(13, 600)
        tf.setFamilies(["Segoe UI Variable", "Segoe UI"])
        tf.setPixelSize(13)
        self.lbl_title.setFont(tf)
        self.lbl_title.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        text_col.addWidget(self.lbl_title)

        self.lbl_time = QLabel("No upcoming calendar meetings", self.card)
        sf = get_ui_font(11.5, 400)
        sf.setFamilies(["Segoe UI Variable", "Segoe UI"])
        sf.setPixelSize(12)
        self.lbl_time.setFont(sf)
        self.lbl_time.setStyleSheet("color: #8E8E93; background: transparent; border: none;")
        text_col.addWidget(self.lbl_time)
        lay.addLayout(text_col, 1)

        self.btn_start = QPushButton("Start notes", self.card)
        self.btn_start.setFixedHeight(36)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_start.setStyleSheet(
            "QPushButton { background-color: #2A2A2C; color: #FFFFFF; border: none; "
            "border-radius: 10px; padding: 0px 15px; font-size: 12.5px; font-weight: 600; }"
            "QPushButton:hover { background-color: #3A3A3D; }"
        )
        self.btn_start.clicked.connect(lambda: (self.hide(), self.start_notes_clicked.emit(self._meeting)))
        lay.addWidget(self.btn_start)

        outer.addWidget(self.card)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def refresh_and_position(self, bar_rect: QRect, dock_edge: str = "bottom") -> None:
        upcoming = []
        if self.controller:
            try:
                if hasattr(self.controller, "calendar_engine") and hasattr(self.controller.calendar_engine, "get_upcoming_meetings"):
                    upcoming = self.controller.calendar_engine.get_upcoming_meetings(limit=1)
                elif hasattr(self.controller, "calendar_manager") and hasattr(self.controller.calendar_manager, "get_upcoming_meetings"):
                    upcoming = self.controller.calendar_manager.get_upcoming_meetings(limit=1)
            except Exception as e:
                logger.debug("Failed fetching upcoming meeting for popover: %s", e)
                upcoming = []

        if upcoming:
            mtg = upcoming[0]
            self._meeting = mtg
            title = str(getattr(mtg, "title", "Upcoming Meeting") or "Upcoming Meeting")
            self.lbl_title.setText(self.lbl_title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 230))
            st = getattr(mtg, "local_start_time", None) or getattr(mtg, "start_time", None)
            et = getattr(mtg, "local_end_time", None) or getattr(mtg, "end_time", None)
            if st and et:
                self.lbl_time.setText(f"{st.strftime('%I:%M %p').lstrip('0').lower()} - {et.strftime('%I:%M %p').lstrip('0').lower()}")
            else:
                mins = int(getattr(mtg, "minutes_until_start", 0))
                self.lbl_time.setText("Starting now" if mins <= 0 else f"Starts in {mins} min")
            self.badge.set_platform(resolve_platform_badge(str(getattr(mtg, "platform", "generic"))))
            self.btn_start.setText("Start notes")
        else:
            self._meeting = None
            self.lbl_title.setText("No meetings for today")
            self.lbl_time.setText("No upcoming meetings scheduled")
            self.badge.set_platform("generic_meeting")
            self.btn_start.setText("Start notes")

        edge = (dock_edge or "bottom").lower()
        gap = 8
        if edge == "right":
            x = bar_rect.left() - self.width() - gap
            y = bar_rect.center().y() - self.height() // 2
        elif edge == "left":
            x = bar_rect.right() + gap
            y = bar_rect.center().y() - self.height() // 2
        else:
            x = bar_rect.center().x() - self.width() // 2
            y = bar_rect.top() - self.height() - gap

        screen = QGuiApplication.screenAt(bar_rect.center()) or QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - self.width() - 8))
            y = max(avail.top() + 8, min(y, avail.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()
        self.raise_()
        self.card.update()


class VoxMistakeSafeguardCard(QWidget):
    """
    Wispr `16_started_by_mistake_card.png` short-recording confirmation card (`374x135`, `#0A0A0B`, radius `18px`).
    """

    discard_clicked = pyqtSignal(int)
    keep_clicked = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self._meeting_id: int = -1
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(374, 135)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setStyleSheet(
            "QFrame { background-color: #0A0A0B; border: 1px solid rgba(255, 255, 255, 0.14); "
            "border-radius: 18px; }"
        )
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(18, 16, 16, 14)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self.lbl_warn = QLabel("⚠  Started by mistake?", self.card)
        hf = get_ui_font(13.5, 600)
        hf.setFamilies(["Segoe UI Variable", "Segoe UI"])
        self.lbl_warn.setFont(hf)
        self.lbl_warn.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        hdr.addWidget(self.lbl_warn)
        hdr.addStretch(1)

        self.btn_close = QPushButton("✕", self.card)
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #E4E4E7; border: 1px solid rgba(255,255,255,0.22); "
            "border-radius: 14px; font-size: 12px; }"
            "QPushButton:hover { background-color: #1F1F23; }"
        )
        self.btn_close.clicked.connect(self._on_keep)
        hdr.addWidget(self.btn_close)
        lay.addLayout(hdr)

        self.lbl_body = QLabel(
            "Only a few words were captured. Keep this meeting or discard it.",
            self.card,
        )
        self.lbl_body.setWordWrap(True)
        bf = get_ui_font(12, 400)
        bf.setFamilies(["Segoe UI Variable", "Segoe UI"])
        self.lbl_body.setFont(bf)
        self.lbl_body.setStyleSheet("color: #A1A1AA; background: transparent; border: none;")
        lay.addWidget(self.lbl_body, 1)

        ftr = QHBoxLayout()
        ftr.addStretch(1)
        ftr.setSpacing(8)
        self.btn_discard = QPushButton("Discard", self.card)
        self.btn_discard.setFixedSize(78, 32)
        self.btn_discard.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_discard.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_discard.setStyleSheet(
            "QPushButton { background-color: #1F1F23; color: #FFFFFF; border: none; "
            "border-radius: 10px; font-size: 12.5px; font-weight: 600; }"
            "QPushButton:hover { background-color: #2E2E34; }"
        )
        self.btn_discard.clicked.connect(self._on_discard)
        ftr.addWidget(self.btn_discard)

        self.btn_keep = QPushButton("✓", self.card)
        self.btn_keep.setFixedSize(54, 32)
        self.btn_keep.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_keep.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_keep.setStyleSheet(
            "QPushButton { background-color: #71717A; color: #111113; border: none; "
            "border-radius: 10px; font-size: 14px; font-weight: 700; }"
            "QPushButton:hover { background-color: #94949E; }"
        )
        self.btn_keep.clicked.connect(self._on_keep)
        ftr.addWidget(self.btn_keep)
        lay.addLayout(ftr)

        outer.addWidget(self.card)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def _on_discard(self) -> None:
        self.hide()
        self.discard_clicked.emit(self._meeting_id)

    def _on_keep(self) -> None:
        self.hide()
        self.keep_clicked.emit(self._meeting_id)

    def show_for_meeting(self, meeting_id: int, bar_rect: QRect, dock_edge: str = "bottom") -> None:
        self._meeting_id = int(meeting_id)
        screen = QGuiApplication.screenAt(bar_rect.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        edge = (dock_edge or "bottom").lower()
        gap = 10
        if edge == "right":
            x = bar_rect.left() - self.width() - gap
            y = bar_rect.center().y() - self.height() // 2
        elif edge == "left":
            x = bar_rect.right() + gap
            y = bar_rect.center().y() - self.height() // 2
        else:
            x = bar_rect.center().x() - self.width() // 2
            y = bar_rect.top() - self.height() - gap
        x = max(avail.left() + 8, min(x, avail.right() - self.width() - 8))
        y = max(avail.top() + 8, min(y, avail.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()


class VoxDragSnapOverlay(QWidget):
    """
    Fullscreen non-activating frosted-white drop zone pill silhouettes shown while dragging FlowBar
    (`f_48.0s.png` replica: Bottom Center, Left Center, Right Center).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._active_edge: str = "bottom"
        self._avail_rect: QRect = QRect(0, 0, 1920, 1080)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def update_drag(self, active_edge: str, ref_point: QPoint) -> None:
        screen = QGuiApplication.screenAt(ref_point) or QGuiApplication.primaryScreen()
        if screen:
            self._avail_rect = screen.availableGeometry()
            self.setGeometry(self._avail_rect)
        self._active_edge = (active_edge or "bottom").lower()
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        margin = 14.0

        targets = [
            ("bottom", QRectF(w / 2.0 - 26.0, h - margin - 10.0, 52.0, 10.0)),
            ("left", QRectF(margin, h / 2.0 - 26.0, 10.0, 52.0)),
            ("right", QRectF(w - margin - 10.0, h / 2.0 - 26.0, 10.0, 52.0)),
        ]
        for edge, rect in targets:
            is_sel = edge == self._active_edge
            fill = QColor(255, 255, 255, 165 if is_sel else 82)
            rim = QColor(255, 255, 255, 235 if is_sel else 135)
            painter.setBrush(fill)
            painter.setPen(QPen(rim, 1.5 if is_sel else 1.0))
            painter.drawRoundedRect(rect, 5.0, 5.0)


class VoxActiveRecordingCapsule(QWidget):
    """
    Wispr Active Meeting Recording Pill:
    - Bottom-docked: `72x30` horizontal capsule (`VoxEqualizerControl` on left, `VoxStopControl` on right).
    - Side-docked (`left`/`right`): `30x72` vertical capsule (`VoxEqualizerControl` on top, `VoxStopControl` on bottom).
    - Emerald `#30C382` outline + subtle outer glow when unhovered; transitions to warm translucent grey hover frame when hovered.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dock_edge: str = "bottom"
        self._hovered: bool = False
        self._finishing: bool = False
        self._panel_open: bool = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.equalizer = VoxEqualizerControl(self)
        self.stop_ctrl = VoxStopControl(self)

        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(6, 4, 6, 4)
        self._lay.setSpacing(4)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(self.equalizer)
        self._lay.addWidget(self.stop_ctrl)
        self.setFixedSize(72, 30)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        self.equalizer.set_dock_edge(self._dock_edge)
        side = self._dock_edge in ("left", "right")
        # Rebuild layout direction cleanly
        old_lay = self.layout()
        if old_lay is not None:
            while old_lay.count():
                old_lay.takeAt(0)
            QWidget().setLayout(old_lay)
        if side:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(4, 6, 4, 6)
            lay.setSpacing(4)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setFixedSize(30, 72)
        else:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(6, 4, 6, 4)
            lay.setSpacing(4)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setFixedSize(72, 30)
        lay.addWidget(self.equalizer)
        lay.addWidget(self.stop_ctrl)
        self._lay = lay
        self.update()

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = bool(hovered)
        self.update()

    def set_panel_open(self, open_flag: bool) -> None:
        self._panel_open = bool(open_flag)
        self.equalizer.set_active(open_flag)
        self.update()

    def set_finishing(self, finishing: bool) -> None:
        self._finishing = bool(finishing)
        self.equalizer.set_dimmed(finishing)
        self.stop_ctrl.set_finishing(finishing)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = min(w, h) / 2.0

        if self._hovered and not self._finishing:
            # Wispr hover state: emerald outline transitions to translucent grey frame (`12` & `13`)
            painter.setBrush(QColor("#1A1A19"))
            painter.setPen(QPen(QColor(150, 150, 146, 155), 1.6))
            painter.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), rad - 1.0, rad - 1.0)
        else:
            # Soft outer emerald glow + crisp #30C382 emerald outline (`09` & `15`)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(48, 195, 130, 56), 3.0))
            painter.drawRoundedRect(QRectF(1.5, 1.5, w - 3.0, h - 3.0), rad - 0.5, rad - 0.5)

            painter.setBrush(QColor("#050505"))
            painter.setPen(QPen(QColor("#30C382"), 1.65))
            painter.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), rad - 1.0, rad - 1.0)


class VoxContextualMeetingPanel(QWidget):
    """
    Connected floating contextual panel (Meeting-Selector Card).
    Opens adjacent to the floating bar extending towards the screen interior.
    Shows Active Meeting (live timer, Open Note, Stop), Upcoming Calendar Meeting (Join & Record),
    Writing Style chips, and Scratchpad access.
    """
    open_note_requested = pyqtSignal()
    stop_meeting_requested = pyqtSignal()
    join_meeting_requested = pyqtSignal(object)
    open_scratchpad_requested = pyqtSignal()
    writing_mode_selected = pyqtSignal(object)
    connect_calendar_requested = pyqtSignal()

    def __init__(self, controller: Optional[Any] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.controller = controller
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setFixedWidth(374)
        self._opacity: float = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.valueChanged.connect(self._on_anim_val)

        self._active_meeting_title: str = "Active Call"
        self._active_meeting_elapsed: str = "00:00"
        self._active_meeting_platform: str = "teams"
        self._is_active_recording: bool = False
        self._upcoming_meeting: Optional[Any] = None

        self._build_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def _on_anim_val(self, val: Any) -> None:
        self._opacity = float(val)
        self.setWindowOpacity(max(0.0, min(1.0, self._opacity)))
        self.update()

    def _build_ui(self) -> None:
        self.card = QFrame(self)
        self.card.setObjectName("voxContextualCard")
        self.card.setStyleSheet(
            "QFrame#voxContextualCard { background-color: #141416; border: 1px solid rgba(255, 255, 255, 0.12); "
            "border-radius: 18px; }"
        )
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(10)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        self.lbl_hdr = QLabel("VOX Meetings & Notetaker", self.card)
        self.lbl_hdr.setFont(get_ui_font(12.5, 600))
        self.lbl_hdr.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        hdr_row.addWidget(self.lbl_hdr)
        hdr_row.addStretch(1)

        self.btn_close = QPushButton("✕", self.card)
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #A1A1AA; border: none; "
            "border-radius: 11px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background: #2C2C2E; color: #FFFFFF; }"
        )
        self.btn_close.clicked.connect(self.animate_close)
        hdr_row.addWidget(self.btn_close)
        card_lay.addLayout(hdr_row)

        self.active_card = QFrame(self.card)
        self.active_card.setStyleSheet(
            "QFrame { background-color: #1C1C1E; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; }"
        )
        act_lay = QHBoxLayout(self.active_card)
        act_lay.setContentsMargins(10, 9, 10, 9)
        act_lay.setSpacing(10)

        self.active_badge = PlatformBadgeWidget(self.active_card, badge_size=24)
        act_lay.addWidget(self.active_badge)

        act_text_col = QVBoxLayout()
        act_text_col.setContentsMargins(0, 0, 0, 0)
        act_text_col.setSpacing(2)
        self.lbl_act_title = QLabel("Active Call", self.active_card)
        self.lbl_act_title.setFont(get_ui_font(12, 600))
        self.lbl_act_title.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        act_text_col.addWidget(self.lbl_act_title)

        self.lbl_act_status = QLabel("● Recording • 00:00", self.active_card)
        self.lbl_act_status.setFont(get_ui_font(11, 500))
        self.lbl_act_status.setStyleSheet("color: #30C382; background: transparent; border: none;")
        act_text_col.addWidget(self.lbl_act_status)
        act_lay.addLayout(act_text_col, 1)

        self.btn_open_note = QPushButton("Open Note", self.active_card)
        self.btn_open_note.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_note.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_open_note.setStyleSheet(
            "QPushButton { background-color: #2C2C2E; color: #FFFFFF; border: none; "
            "border-radius: 8px; padding: 5px 11px; font-size: 11.5px; font-weight: 600; }"
            "QPushButton:hover { background-color: #3A3A3D; }"
        )
        self.btn_open_note.clicked.connect(self._on_open_note)
        act_lay.addWidget(self.btn_open_note)

        self.btn_stop_act = QPushButton("Stop", self.active_card)
        self.btn_stop_act.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_act.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_stop_act.setStyleSheet(
            "QPushButton { background-color: #272729; color: #E4E4E7; border: 1px solid rgba(255,255,255,0.12); "
            "border-radius: 8px; padding: 5px 9px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { background-color: #3A3A3D; }"
        )
        self.btn_stop_act.clicked.connect(self._on_stop_meeting)
        act_lay.addWidget(self.btn_stop_act)
        card_lay.addWidget(self.active_card)

        self.upcoming_card = QFrame(self.card)
        self.upcoming_card.setStyleSheet(
            "QFrame { background-color: #1C1C1E; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; }"
        )
        up_lay = QHBoxLayout(self.upcoming_card)
        up_lay.setContentsMargins(10, 9, 10, 9)
        up_lay.setSpacing(10)

        self.up_badge = PlatformBadgeWidget(self.upcoming_card, badge_size=24)
        up_lay.addWidget(self.up_badge)

        up_text_col = QVBoxLayout()
        up_text_col.setContentsMargins(0, 0, 0, 0)
        up_text_col.setSpacing(2)
        self.lbl_up_title = QLabel("Upcoming Meeting", self.upcoming_card)
        self.lbl_up_title.setFont(get_ui_font(12, 600))
        self.lbl_up_title.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        up_text_col.addWidget(self.lbl_up_title)

        self.lbl_up_time = QLabel("Starts soon", self.upcoming_card)
        self.lbl_up_time.setFont(get_ui_font(11, 400))
        self.lbl_up_time.setStyleSheet("color: #8E8E93; background: transparent; border: none;")
        up_text_col.addWidget(self.lbl_up_time)
        up_lay.addLayout(up_text_col, 1)

        self.btn_join_up = QPushButton("Switch", self.upcoming_card)
        self.btn_join_up.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_join_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_join_up.setStyleSheet(
            "QPushButton { background-color: #2C2C2E; color: #FFFFFF; border: none; "
            "border-radius: 8px; padding: 5px 11px; font-size: 11.5px; font-weight: 600; }"
            "QPushButton:hover { background-color: #3A3A3D; }"
        )
        self.btn_join_up.clicked.connect(self._on_join_upcoming)
        up_lay.addWidget(self.btn_join_up)
        card_lay.addWidget(self.upcoming_card)

        ftr_row = QHBoxLayout()
        ftr_row.setContentsMargins(0, 2, 0, 0)
        ftr_row.setSpacing(6)

        lbl_style = QLabel("Style:", self.card)
        lbl_style.setFont(get_ui_font(10.5, 500))
        lbl_style.setStyleSheet("color: #8E8E93; background: transparent;")
        ftr_row.addWidget(lbl_style)

        self.style_chips: list[QPushButton] = []
        for name, mode in [
            ("Clean", TransformationMode.CLEAN),
            ("Polished", TransformationMode.POLISHED),
            ("Email", TransformationMode.EMAIL),
            ("Structured", TransformationMode.STRUCTURED),
        ]:
            btn = QPushButton(name, self.card)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                "QPushButton { background-color: #242426; color: #D4D4D8; border: 1px solid rgba(255,255,255,0.1); "
                "border-radius: 6px; padding: 0px 7px; font-size: 10.5px; font-weight: 500; }"
                "QPushButton:hover { background-color: #323235; color: #FFFFFF; }"
                "QPushButton:checked { background-color: #1B3B2F; color: #FFFFFF; border-color: #30C382; font-weight: 600; }"
            )
            btn.clicked.connect(lambda _=False, m=mode: self._select_mode(m))
            ftr_row.addWidget(btn)
            self.style_chips.append(btn)

        ftr_row.addStretch(1)

        self.btn_sp_ftr = QPushButton("Scratchpad", self.card)
        self.btn_sp_ftr.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_sp_ftr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sp_ftr.setFixedHeight(22)
        self.btn_sp_ftr.setStyleSheet(
            "QPushButton { background: transparent; color: #E4E4E7; border: 1px solid rgba(255,255,255,0.14); "
            "border-radius: 6px; padding: 0px 8px; font-size: 10.5px; font-weight: 500; }"
            "QPushButton:hover { background-color: #2C2C2E; color: #FFFFFF; }"
        )
        self.btn_sp_ftr.clicked.connect(lambda: self.open_scratchpad_requested.emit())
        ftr_row.addWidget(self.btn_sp_ftr)
        card_lay.addLayout(ftr_row)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.card)
        self.adjustSize()

    def _select_mode(self, mode: TransformationMode) -> None:
        self.writing_mode_selected.emit(mode)
        if self.controller and hasattr(self.controller, "set_writing_mode"):
            self.controller.set_writing_mode(mode)
        self.sync_active_mode(mode)

    def sync_active_mode(self, mode: TransformationMode) -> None:
        modes = [
            TransformationMode.CLEAN,
            TransformationMode.POLISHED,
            TransformationMode.EMAIL,
            TransformationMode.STRUCTURED,
        ]
        for btn, m in zip(self.style_chips, modes):
            btn.setChecked(m == mode)

    def _on_open_note(self) -> None:
        self.open_note_requested.emit()
        self.animate_close()

    def _on_stop_meeting(self) -> None:
        self.stop_meeting_requested.emit()
        self.animate_close()

    def _on_join_upcoming(self) -> None:
        if self._upcoming_meeting:
            self.join_meeting_requested.emit(self._upcoming_meeting)
        else:
            self.connect_calendar_requested.emit()
        self.animate_close()

    def update_meeting_data(
        self,
        is_recording: bool,
        active_title: str = "",
        elapsed_str: str = "00:00",
        platform: str = "teams",
    ) -> None:
        self._is_active_recording = is_recording
        self._active_meeting_title = active_title or "Meeting"
        self._active_meeting_elapsed = elapsed_str or "00:00"
        self._active_meeting_platform = platform or "teams"

        if is_recording:
            self.lbl_act_title.setText(self._active_meeting_title[:28])
            self.lbl_act_status.setText(f"● Live • {self._active_meeting_elapsed}")
            self.lbl_act_status.setStyleSheet("color: #30C382; background: transparent; border: none;")
            self.active_badge.set_platform(resolve_platform_badge(platform))
            self.btn_open_note.setText("Open Note")
            self.btn_open_note.setVisible(True)
            self.btn_stop_act.setVisible(True)
        else:
            self.lbl_act_title.setText("Start Meeting Notetaker")
            self.lbl_act_status.setText("Record mic & call audio with live notes")
            self.lbl_act_status.setStyleSheet("color: #8E8E93; background: transparent; border: none;")
            self.active_badge.set_platform("generic_meeting")
            self.btn_open_note.setText("Start")
            self.btn_open_note.setVisible(True)
            self.btn_stop_act.setVisible(False)

        self._refresh_upcoming()
        self.adjustSize()

    def _refresh_upcoming(self) -> None:
        upcoming_list = []
        if self.controller:
            if hasattr(self.controller, "calendar_engine") and hasattr(self.controller.calendar_engine, "get_upcoming_meetings"):
                upcoming_list = self.controller.calendar_engine.get_upcoming_meetings(limit=1)
            elif hasattr(self.controller, "calendar_manager") and hasattr(self.controller.calendar_manager, "get_upcoming_meetings"):
                upcoming_list = self.controller.calendar_manager.get_upcoming_meetings(limit=1)

        if upcoming_list:
            mtg = upcoming_list[0]
            self._upcoming_meeting = mtg
            self.lbl_up_title.setText(str(getattr(mtg, "title", "Upcoming Meeting"))[:28])
            mins = int(getattr(mtg, "minutes_until_start", 10))
            time_txt = "Starting now" if mins <= 0 else f"Starts in {mins} min"
            self.lbl_up_time.setText(time_txt)
            plat = str(getattr(mtg, "platform", "teams"))
            self.up_badge.set_platform(resolve_platform_badge(plat))
            self.btn_join_up.setText("Switch" if self._is_active_recording else "Start notes")
            self.btn_join_up.setVisible(True)
        else:
            self._upcoming_meeting = None
            self.lbl_up_title.setText("No upcoming calendar meetings")
            self.lbl_up_time.setText("Connect your calendar in Settings")
            self.up_badge.set_platform("generic_meeting")
            self.btn_join_up.setText("Connect")
            self.btn_join_up.setVisible(True)

    def update_meeting_timer(self, elapsed_str: str) -> None:
        self._active_meeting_elapsed = elapsed_str
        if self._is_active_recording:
            self.lbl_act_status.setText(f"● Live • {elapsed_str}")

    def animate_open(self) -> None:
        self.show()
        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def animate_close(self) -> None:
        self._anim.stop()
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.InCubic))
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(0.0)

        def _on_done() -> None:
            self.hide()
            self.setWindowOpacity(0.0)
            try:
                self._anim.finished.disconnect(_on_done)
            except Exception:
                pass

        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.finished.connect(_on_done)
        self._anim.start()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)


class VoxVerticalCapsule(QFrame):
    """Auxiliary obsidian capsule surface kept for compatibility."""

    def __init__(self, flow_bar: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.flow_bar = flow_bar
        self.setObjectName("flowBarPill")
        self.setMouseTracking(True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        radius = min(w, h) / 2.0
        painter.setBrush(QColor(8, 8, 8, 245))
        painter.setPen(QPen(QColor(255, 255, 255, 36), 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius - 0.5, radius - 0.5)


def _format_hotkey_display(raw: str) -> str:
    """Format a config hotkey string like 'ctrl+win' or 'ctrl+alt+r' cleanly for tooltips."""
    txt = (raw or "ctrl+win").strip()
    if not txt:
        return "Ctrl+Win"
    parts = [p.strip() for p in txt.split("+") if p.strip()]
    mapping = {
        "ctrl": "Ctrl",
        "control": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "win": "Win",
        "windows": "Win",
        "super": "Win",
        "cmd": "Cmd",
        "fn": "Fn",
        "space": "Space",
    }
    return "+".join(mapping.get(p.lower(), p.upper() if len(p) <= 2 else p.title()) for p in parts)


class _WisprRestingCapsule(QWidget):
    """Subtle dark edge pill shown at rest (40x8 bottom, 8x40 side)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 8)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._dock_edge: str = "bottom"

    def set_dock_edge(self, edge: str) -> None:
        self._dock_edge = (edge or "bottom").lower()
        if self._dock_edge in ("left", "right"):
            self.setFixedSize(8, 40)
        else:
            self.setFixedSize(40, 8)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        rad = min(w, h) / 2.0
        painter.setBrush(QColor(12, 12, 14, 215))
        painter.setPen(QPen(QColor(255, 255, 255, 115), 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad - 0.5, rad - 0.5)


class VoxFlowBar(QWidget):
    """
    Wispr Flow authentic interactive FlowBar capsule implemented cleanly in PyQt6 for VOX.

    Reconstructs Wispr Flow's dual-capsule Ready bar, hover-morphing Notetaker + Chevron trigger,
    upcoming meeting popover, active dictation pill with solid white finish checkmark, cancelled
    transcript undo toast with progress bar, compact emerald-rimmed Active Recording pill with
    adaptive equalizer + neutral charcoal stop + finishing radial spinner + satellite chevron,
    short-recording safeguard card, contextual meeting drawer, and 3-edge frosted drag-snap ghosts.
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
        self._dock_y_ratio: float = float(
            getattr(getattr(controller, "config", None), "flow_bar_y_ratio", 0.5) or 0.5
        )
        self._meeting_active: bool = False
        self._meeting_finishing: bool = False
        self._meeting_elapsed_str: str = "00:00"
        self._last_meeting_elapsed_sec: float = 0.0
        self._notepad_drawer_open: bool = False
        self._contextual_panel_open: bool = False
        self._status_message: str = ""
        self._dragging: bool = False
        self._drag_start_pos: QPoint = QPoint()
        self._was_dragged: bool = False
        self._prismatic_angle: float = 0.0
        self._last_entry_id: Optional[int] = None
        self._active_upcoming_meetings: List[Any] = []
        self._pending_safeguard_check: bool = False
        self._last_hover_zone: str = ""

        # Geometry, Morph, and Animation Specifications preserved for test compatibility
        self.morph_duration: int = MORPH_DURATION_MS
        self.fade_out_duration: int = FADE_OUT_MS
        self.fade_in_delay: int = FADE_IN_DELAY_MS
        self.target_size: Optional[QSize] = QSize(40, 8)
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

        # Smooth snap animation when released after drag
        self._snap_anim = QVariantAnimation(self)
        self._snap_anim.setDuration(190)
        self._snap_anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._snap_anim.valueChanged.connect(self._on_snap_pos_step)

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

        # 70ms hover-leave debounce so moving across satellite chevron gap never flickers
        self._hover_leave_timer = QTimer(self)
        self._hover_leave_timer.setSingleShot(True)
        self._hover_leave_timer.setInterval(70)
        self._hover_leave_timer.timeout.connect(self._handle_delayed_hover_leave)

        # Debounced tooltip timer so gliding over buttons never flickers distracting text
        self._tooltip_target_html: str = ""
        self._tooltip_target_rect: QRect = QRect()
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(160)
        self._tooltip_timer.timeout.connect(self._do_show_tooltip)

        # Safety fallback timer in case finishing spinner misses completion signal
        self._finishing_fallback_timer = QTimer(self)
        self._finishing_fallback_timer.setSingleShot(True)
        self._finishing_fallback_timer.setInterval(15000)
        self._finishing_fallback_timer.timeout.connect(self._clear_finishing_state)

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

        # Top-level non-activating floating satellites
        self.preview_bubble = LiveSpeechPreviewBubble(theme=self._theme)
        self.hover_tooltip = FlowBarTooltipPill()
        self.cancelled_toast = VoxDictationCancelledToast()
        self.cancelled_toast.undo_clicked.connect(self._on_undo_cancelled_dictation)
        self.upcoming_popover = VoxUpcomingNotesPopover(controller=self.controller)
        self.upcoming_popover.start_notes_clicked.connect(self._on_start_notes_from_popover)
        self.upcoming_popover.open_vox_clicked.connect(
            lambda: self.on_open_dashboard(0) if self.on_open_dashboard else None
        )
        self.mistake_card = VoxMistakeSafeguardCard()
        self.mistake_card.discard_clicked.connect(self._on_mistake_discard)
        self.mistake_card.keep_clicked.connect(self._on_mistake_keep)
        self.contextual_panel = VoxContextualMeetingPanel(controller=self.controller)
        self.contextual_panel.open_note_requested.connect(self._on_panel_open_note)
        self.contextual_panel.stop_meeting_requested.connect(self._on_stop_clicked)
        self.contextual_panel.join_meeting_requested.connect(self._on_start_notes_from_popover)
        self.contextual_panel.open_scratchpad_requested.connect(self._on_open_scratchpad)
        self.contextual_panel.writing_mode_selected.connect(self._on_panel_mode_selected)
        self.contextual_panel.connect_calendar_requested.connect(
            lambda: self.on_open_dashboard(7) if self.on_open_dashboard else None
        )
        self.drag_overlay = VoxDragSnapOverlay()

        self._auto_reset_timer = QTimer(self)
        self._auto_reset_timer.setSingleShot(True)
        self._auto_reset_timer.timeout.connect(self._reset_to_resting)

        self._prismatic_timer = QTimer(self)
        self._prismatic_timer.setInterval(40)
        self._prismatic_timer.timeout.connect(self._advance_prismatic)

        self._build_ui()
        self._connect_controller_signals()
        self._sync_pill_visuals()
        self._sync_geometry()
        try:
            apply_non_activating_floating_style(int(self.winId()))
        except Exception:
            pass

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QBoxLayout

        self._root_lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._root_lay.setContentsMargins(0, 0, 0, 0)
        self._root_lay.setSpacing(6)
        self._root_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Primary FlowBar Pill Surface
        self.upper_pill = QFrame(self)
        self.upper_pill.setObjectName("flowBarPill")
        self.upper_pill.setMouseTracking(True)

        # Stacked layout:
        # Index 0: Page 0 (Resting / Ready Dual-Capsule / Active Dictation / Status / Active Recording)
        # Index 1: Mini Meeting layMini layout (416x48)
        # Index 2: Card Notification layCard layout (416x80)
        # Index 3: L4 Preview agenda layout (416x322)
        self.pill_stack = QStackedLayout(self.upper_pill)
        self.pill_stack.setContentsMargins(0, 0, 0, 0)

        # --- Page 0: Wispr Flow Interactive Capsule Views ---
        self.lay_dictation_widget = QWidget(self.upper_pill)
        self.lay_dictation_widget.setMouseTracking(True)
        p0_root = QHBoxLayout(self.lay_dictation_widget)
        p0_root.setContentsMargins(0, 0, 0, 0)
        p0_root.setSpacing(0)

        self._p0_stack = QStackedLayout()
        self._p0_stack.setContentsMargins(0, 0, 0, 0)
        p0_root.addLayout(self._p0_stack)

        # Subpage 0: Resting Capsule (40x8 bottom / 8x40 side)
        self._resting_capsule = _WisprRestingCapsule(self.lay_dictation_widget)
        self._p0_stack.addWidget(self._resting_capsule)

        # Subpage 1: Ready Dual-Capsule Bar ([Dictate 48x30] + 4px + [Notetaker 30x30 -> 52x30])
        self._ready_host = QWidget(self.lay_dictation_widget)
        self._ready_host.setMouseTracking(True)
        self._ready_lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self._ready_host)
        self._ready_lay.setContentsMargins(0, 0, 0, 0)
        self._ready_lay.setSpacing(4)
        self._ready_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_dictate = VoxDictateButton(self._ready_host)
        self.btn_dictate.clicked.connect(self._on_dictate_clicked)
        self.btn_dictate.hover_changed.connect(self._on_dictate_hover)
        self._ready_lay.addWidget(self.btn_dictate, 0, Qt.AlignmentFlag.AlignCenter)

        self.btn_notetaker = VoxNotetakerButton(self._ready_host)
        self.btn_notetaker.notetaker_clicked.connect(self._on_notetaker_target_clicked)
        self.btn_notetaker.chevron_clicked.connect(self._on_notetaker_chevron_clicked)
        self.btn_notetaker.hover_zone_changed.connect(self._on_notetaker_hover_zone)
        self.btn_notetaker.size_changed.connect(self._sync_geometry)
        self._ready_lay.addWidget(self.btn_notetaker, 0, Qt.AlignmentFlag.AlignCenter)

        self._p0_stack.addWidget(self._ready_host)

        # Subpage 2: Active Dictation Pill (Cancel | Waveform | Finish ✓)
        self.active_dictation_pill = VoxActiveDictationPill(self.lay_dictation_widget)
        self.active_dictation_pill.cancel_clicked.connect(self._on_cancel_clicked)
        self.active_dictation_pill.finish_clicked.connect(self._on_stop_clicked)
        self.active_dictation_pill.hover_zone_changed.connect(self._on_active_dictation_hover)
        self._p0_stack.addWidget(self.active_dictation_pill)

        # Subpage 3: Compact Obsidian Status Toast (Processing / Polish / Committed / Error)
        self._status_pill = QFrame(self.lay_dictation_widget)
        self._status_pill.setObjectName("voxStatusPill")
        st_lay = QHBoxLayout(self._status_pill)
        st_lay.setContentsMargins(10, 4, 10, 4)
        st_lay.setSpacing(6)
        st_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_wave = DynamicWaveformCapsule(self._status_pill, bar_count=7)
        self._status_wave.setFixedSize(28, 16)
        self._status_wave.hide()
        st_lay.addWidget(self._status_wave)

        self._status_lbl = QLabel("", self._status_pill)
        st_lay_font = get_ui_font(11.5, 600)
        st_lay_font.setFamilies(["Segoe UI Variable", "Segoe UI", "Inter"])
        self._status_lbl.setFont(st_lay_font)
        self._status_lbl.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        st_lay.addWidget(self._status_lbl)

        self._status_undo_btn = QPushButton("Undo", self._status_pill)
        self._status_undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._status_undo_btn.setFixedHeight(20)
        self._status_undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._status_undo_btn.setStyleSheet(
            "QPushButton { background-color: #2C2C2E; color: #F4F4F5; border: none; "
            "border-radius: 8px; padding: 0px 8px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #3A3A3C; }"
        )
        self._status_undo_btn.clicked.connect(self._on_undo_last)
        self._status_undo_btn.hide()
        st_lay.addWidget(self._status_undo_btn)

        self._p0_stack.addWidget(self._status_pill)

        # Subpage 4: Active Meeting Recording Emerald Capsule (72x30 / 30x72)
        self.active_recording_capsule = VoxActiveRecordingCapsule(self.lay_dictation_widget)
        self.active_recording_capsule.clicked.connect(self._on_meeting_chip_clicked)
        self.active_recording_capsule.equalizer.clicked.connect(self._on_meeting_chip_clicked)
        self.active_recording_capsule.equalizer.hover_changed.connect(self._on_equalizer_hover)
        self.active_recording_capsule.stop_ctrl.clicked.connect(self._on_stop_clicked)
        self.active_recording_capsule.stop_ctrl.hover_changed.connect(self._on_stop_ctrl_hover)
        self._p0_stack.addWidget(self.active_recording_capsule)

        # Zero-footprint hidden compatibility holder preserving all legacy test attributes
        self._compat_holder = QWidget(self.lay_dictation_widget)
        self._compat_holder.setFixedSize(0, 0)
        self.btn_cancel = _RoundPillButton("cancel", self._compat_holder)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_cancel.hide()

        self.btn_check = _RoundPillButton("check", self._compat_holder)
        self.btn_check.hide()

        self.waveform_capsule = DynamicWaveformCapsule(self._compat_holder, bar_count=10)
        self.waveform_capsule.clicked.connect(self._on_pill_clicked)

        self.lbl_state_text = QLabel("", self._compat_holder)
        self.lbl_state_text.setFont(get_ui_font(11.5, 550))
        self.lbl_state_text.hide()

        self.btn_undo_toast = QPushButton("Undo", self._compat_holder)
        self.btn_undo_toast.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_undo_toast.clicked.connect(self._on_undo_last)
        self.btn_undo_toast.hide()

        self.btn_stop = _RoundPillButton("stop", self._compat_holder)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.hide()

        self.btn_scratchpad = _RoundPillButton("scratchpad", self._compat_holder)
        self.btn_scratchpad.clicked.connect(self._on_open_scratchpad)
        self.btn_scratchpad.hide()

        self.meeting_chip = QPushButton("● 00:00 Notetaker", self._compat_holder)
        self.meeting_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.meeting_chip.clicked.connect(self._on_meeting_chip_clicked)
        self.meeting_chip.hide()

        self.timer_widget = SessionTimerWidget(self._compat_holder)
        self.timer_widget.hide()
        self.menu_btn = QPushButton(self._compat_holder)
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.hide()
        self.menu_btn.clicked.connect(lambda: self._on_open_settings())

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

        self._root_lay.addWidget(self.upper_pill, 0, Qt.AlignmentFlag.AlignCenter)

        # Separate circular obsidian satellite chevron (28x28) shown on Active Recording hover
        self.satellite_chevron = VoxSatelliteChevronButton(self)
        self.satellite_chevron.clicked.connect(self._toggle_contextual_panel)
        self.satellite_chevron.hover_changed.connect(self._on_chevron_hover)
        self.satellite_chevron.hide()
        self._root_lay.addWidget(self.satellite_chevron, 0, Qt.AlignmentFlag.AlignCenter)

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

        side_edge = self._dock_edge in ("left", "right")
        if norm_state in (PillState.RESTING, PillState.IDLE):
            self._meeting_active = False
            self._meeting_finishing = False
            self._finishing_fallback_timer.stop()
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(8, 40) if side_edge else QSize(40, 8)
            self.pill_stack.setCurrentIndex(0)
        elif norm_state == PillState.MINI_MEETING:
            self._auto_start_timer.stop()
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
            self._meeting_finishing = False
            self._auto_start_timer.stop()
            self._countdown_timer.stop()
            self.auto_start_progress = 0.0
            self.target_size = QSize(30, 72) if side_edge else QSize(72, 30)
            self.pill_stack.setCurrentIndex(0)
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(36, 18)
            self.waveform_capsule.show()
            self.lbl_state_text.setText(self._meeting_elapsed_str or "00:00")
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
        if norm_state == PillState.ACTIVE_RECORDING or norm_state in (PillState.RESTING, PillState.IDLE):
            self._sync_geometry()
        else:
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
            if self._meeting_finishing:
                return
            self._meeting_active = False
            self.set_pill_state(PillState.RESTING, animated=True)

    def _clear_finishing_state(self) -> None:
        if not self._meeting_finishing:
            return
        self._meeting_finishing = False
        self._pending_safeguard_check = False
        self.active_recording_capsule.set_finishing(False)
        if not self._meeting_active and self._state == PillState.ACTIVE_RECORDING:
            self.set_pill_state(PillState.RESTING, animated=False)

    def _on_meeting_saved_and_transcribed(self, meeting_id: int) -> None:
        elapsed_sec = float(self._last_meeting_elapsed_sec or 0.0)
        should_check = self._pending_safeguard_check or self._meeting_finishing
        self._pending_safeguard_check = False
        self._meeting_finishing = False
        self._finishing_fallback_timer.stop()
        self.active_recording_capsule.set_finishing(False)

        if not self._meeting_active and self._state == PillState.ACTIVE_RECORDING:
            self.set_pill_state(PillState.RESTING, animated=False)

        if should_check and meeting_id > 0:
            word_count = 999
            if self.controller and getattr(self.controller, "db", None):
                try:
                    m = self.controller.db.get_meeting(meeting_id)
                    if m:
                        seg_text = " ".join(str(s.get("text", "")) for s in (getattr(m, "transcript", None) or []))
                        if not seg_text:
                            segs = getattr(m, "segments", None) or []
                            seg_text = " ".join(
                                str(getattr(s, "text", s.get("text", "") if isinstance(s, dict) else ""))
                                for s in segs
                            )
                        word_count = len(seg_text.split())
                        if elapsed_sec <= 0.0:
                            elapsed_sec = float(getattr(m, "duration_sec", 0.0) or 0.0)
                except Exception:
                    pass
            if elapsed_sec < 4.0 and word_count < 3:
                self.mistake_card.show_for_meeting(
                    meeting_id, self._anchor_pill_global_rect(), self._dock_edge
                )

    def _on_controller_meeting_lifecycle(self, lifecycle: str, msg: str) -> None:
        lc = (lifecycle or "").lower().strip()
        if lc in ("recording", "preparing_capture"):
            self._meeting_active = True
            self._meeting_finishing = False
            self._finishing_fallback_timer.stop()
            self._last_meeting_elapsed_sec = 0.0
            self.upcoming_popover.hide()
            self.mistake_card.hide()
            if self._state != PillState.ACTIVE_RECORDING:
                self.set_pill_state(PillState.ACTIVE_RECORDING, animated=False)
            if self._contextual_panel_open:
                self._refresh_contextual_panel()
        elif lc == "finalizing":
            self._meeting_finishing = True
            self._pending_safeguard_check = True
            self.active_recording_capsule.set_finishing(True)
            self.satellite_chevron.hide()
            self.hover_tooltip.hide()
            self._finishing_fallback_timer.start()
            self._sync_geometry()
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
            if lc == "completed" and self._meeting_finishing:
                # Keep finishing spinner visible briefly until meeting_saved_and_transcribed arrives
                self._meeting_active = False
                return
            self._meeting_active = False
            self._meeting_finishing = False
            self._finishing_fallback_timer.stop()
            self.active_recording_capsule.set_finishing(False)
            self._contextual_panel_open = False
            self.contextual_panel.hide()
            if self._state in (PillState.ACTIVE_RECORDING, PillState.CARD_NOTIFICATION, PillState.MINI_MEETING):
                self.set_pill_state(PillState.RESTING, animated=True)

    def _on_calendar_upcoming_changed(self, meetings: list) -> None:
        self._active_upcoming_meetings = list(meetings or [])
        if self._contextual_panel_open:
            self._refresh_contextual_panel()
        if not self._active_upcoming_meetings:
            self.set_active_meeting_count(0)
            if self._state in (PillState.MINI_MEETING, PillState.CARD_NOTIFICATION) and not self._meeting_active:
                self.set_pill_state(PillState.RESTING, animated=True)
            return

        self.set_active_meeting_count(len(self._active_upcoming_meetings))
        if self._state not in (
            PillState.LISTENING,
            PillState.ACTIVE_RECORDING,
            PillState.PROCESSING,
            PillState.COMMITTED,
        ):
            import datetime

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            closest = None
            closest_mins = 999999
            closest_diff_sec = 0.0
            for m in self._active_upcoming_meetings:
                st = getattr(m, "start_time", None)
                if st:
                    try:
                        st_utc = st.astimezone(datetime.timezone.utc)
                    except (OSError, OverflowError, ValueError):
                        st_utc = st.replace(tzinfo=datetime.timezone.utc)
                    diff_sec = (st_utc - now_utc).total_seconds()
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
                            "active_meeting_count": len(self._active_upcoming_meetings),
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
            if hasattr(self.controller, "meeting_saved_and_transcribed"):
                self.controller.meeting_saved_and_transcribed.connect(self._on_meeting_saved_and_transcribed)
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
        if self.controller and getattr(self.controller, "config", None):
            self._dock_y_ratio = float(
                getattr(self.controller.config, "flow_bar_y_ratio", self._dock_y_ratio) or 0.5
            )
        self._sync_pill_visuals()
        self._sync_geometry()
        self.dock_to_edge(val)

    def dock_to_bottom_center(self) -> None:
        self.dock_to_edge("bottom")

    def _compute_dock_position(self, edge: Optional[str] = None) -> QPoint:
        target_edge = (edge or self._dock_edge or "bottom").lower()
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return self.pos()
        avail = screen.availableGeometry()
        pw = self.upper_pill.width() if self.upper_pill.width() > 0 else self.width()
        ph = self.upper_pill.height() if self.upper_pill.height() > 0 else self.height()
        w, h = self.width(), self.height()
        margin = 10
        y_center_ratio = max(0.15, min(0.85, float(self._dock_y_ratio or 0.5)))

        if target_edge == "left":
            x = avail.left() + margin
            y = int(avail.top() + avail.height() * y_center_ratio - ph // 2)
        elif target_edge == "right":
            x = avail.right() - pw - margin
            y = int(avail.top() + avail.height() * y_center_ratio - ph // 2)
        else:
            x = avail.center().x() - pw // 2
            y = avail.bottom() - ph - margin

        x = max(avail.left() + margin, min(x, avail.right() - w - margin))
        y = max(avail.top() + margin, min(y, avail.bottom() - h - margin))
        return QPoint(x, y)

    def dock_to_edge(self, edge: Optional[str] = None) -> None:
        pos = self._compute_dock_position(edge)
        self.move(pos)
        if hasattr(self, "preview_bubble") and self.preview_bubble.isVisible():
            self.preview_bubble.update_position_above_pill(self._anchor_pill_global_rect())
        if hasattr(self, "hover_tooltip") and self.hover_tooltip.isVisible():
            self.hover_tooltip.position_adjacent_to(self._anchor_pill_global_rect(), self._dock_edge, gap=10)
        if hasattr(self, "_contextual_panel_open") and self._contextual_panel_open and self.contextual_panel.isVisible():
            self._position_contextual_panel()

    def _on_snap_pos_step(self, val: Any) -> None:
        if isinstance(val, QPoint):
            self.move(val)

    def _anchor_pill_global_rect(self) -> QRect:
        if hasattr(self, "upper_pill") and self.upper_pill is not None:
            return QRect(self.upper_pill.mapToGlobal(QPoint(0, 0)), self.upper_pill.size())
        return self.geometry()

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
            self.upcoming_popover.hide()
            self.cancelled_toast.hide()
            self.waveform_capsule.set_processing(False)
            if self.controller:
                self._is_ptt_session = not bool(getattr(self.controller, "is_hands_free_active", False))
            self.active_dictation_pill.set_ptt_mode(self._is_ptt_session)
            self.active_dictation_pill.set_processing(False, polish=False)
            if prev_state != PillState.LISTENING:
                self.timer_widget.start()
                play_flow_chime("start", self._sound_enabled())
        elif norm_state == PillState.PROCESSING:
            self._auto_reset_timer.stop()
            self._prismatic_timer.stop()
            self.hover_tooltip.hide()
            self.waveform_capsule.set_processing(True)
            self.active_dictation_pill.set_processing(True, polish=False)
            self.timer_widget.stop()
            play_flow_chime("stop", self._sound_enabled())
        elif norm_state == PillState.POLISH_PROCESSING:
            self._auto_reset_timer.stop()
            self._prismatic_timer.start()
            self.hover_tooltip.hide()
            self.waveform_capsule.set_processing(True)
            self.active_dictation_pill.set_processing(True, polish=True)
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
        from PyQt6.QtWidgets import QBoxLayout

        effective = self.pill_state
        side_edge = self._dock_edge in ("left", "right")

        # Keep container layout directions aligned with dock edge
        self._root_lay.setDirection(
            QBoxLayout.Direction.TopToBottom if side_edge else QBoxLayout.Direction.LeftToRight
        )
        self._ready_lay.setDirection(
            QBoxLayout.Direction.TopToBottom if side_edge else QBoxLayout.Direction.LeftToRight
        )
        self._resting_capsule.set_dock_edge(self._dock_edge)
        self.btn_dictate.set_dock_edge(self._dock_edge)
        self.btn_notetaker.set_dock_edge(self._dock_edge)
        self.active_recording_capsule.set_dock_edge(self._dock_edge)
        self.satellite_chevron.set_dock_edge(self._dock_edge)
        self.satellite_chevron.set_active(self._contextual_panel_open)

        if effective in (PillState.RESTING, PillState.IDLE):
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._resting_capsule)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self.satellite_chevron.hide()
            self.btn_cancel.hide()
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.lbl_state_text.hide()
            self.btn_scratchpad.hide()
            self.waveform_capsule.hide()
        elif effective == PillState.READY:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._ready_host)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self.satellite_chevron.hide()
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
            if show_sp:
                self.btn_scratchpad.show()
            else:
                self.btn_scratchpad.hide()
        elif effective == PillState.LISTENING:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self.active_dictation_pill)
            self.active_dictation_pill.set_ptt_mode(self._is_ptt_session)
            self.active_dictation_pill.set_processing(False, polish=False)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self.satellite_chevron.hide()
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(40, 18)
            self.waveform_capsule.show()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.lbl_state_text.hide()
            if not self._is_ptt_session:
                self.btn_cancel.show()
                self.btn_stop.setEnabled(True)
                self.btn_stop.show()
            else:
                self.btn_cancel.hide()
                self.btn_stop.setEnabled(False)
                self.btn_stop.hide()
        elif effective == PillState.PROCESSING:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._status_pill)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self._status_pill.setStyleSheet(
                "QFrame#voxStatusPill { background-color: #080808; border: 1px solid #3A3A3C; border-radius: 15px; }"
            )
            self.satellite_chevron.hide()
            self._status_wave.set_visual_mode("speech")
            self._status_wave.set_processing(True)
            self._status_wave.show()
            self._status_undo_btn.hide()
            self._status_lbl.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
            self._status_lbl.setText("Writing")
            self.waveform_capsule.set_visual_mode("speech")
            self.waveform_capsule.setFixedSize(32, 16)
            self.waveform_capsule.show()
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.lbl_state_text.setText("Writing")
            self.lbl_state_text.show()
        elif effective == PillState.POLISH_PROCESSING:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._status_pill)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self._status_pill.setStyleSheet(
                "QFrame#voxStatusPill { background-color: #080808; border: 1.5px solid #FFBCF2; border-radius: 15px; }"
            )
            self.satellite_chevron.hide()
            self._status_wave.set_visual_mode("instruct")
            self._status_wave.set_processing(True)
            self._status_wave.show()
            self._status_undo_btn.hide()
            self._status_lbl.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
            self._status_lbl.setText("Cleaning up")
            self.waveform_capsule.set_visual_mode("instruct")
            self.waveform_capsule.setFixedSize(32, 16)
            self.waveform_capsule.show()
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.lbl_state_text.setText("Cleaning up")
            self.lbl_state_text.show()
        elif effective == PillState.COMMITTED:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._status_pill)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self._status_pill.setStyleSheet(
                "QFrame#voxStatusPill { background-color: #080808; border: 1px solid #30C382; border-radius: 15px; }"
            )
            self.satellite_chevron.hide()
            self._status_wave.hide()
            self._status_lbl.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
            self._status_lbl.setText("Cleaned up")
            self._status_undo_btn.show()
            self.btn_check.show()
            self.lbl_state_text.setText("Cleaned up")
            self.lbl_state_text.show()
            self.btn_undo_toast.show()
        elif effective == PillState.ERROR:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self._status_pill)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self._status_pill.setStyleSheet(
                "QFrame#voxStatusPill { background-color: #080808; border: 1.5px solid #FF5757; border-radius: 15px; }"
            )
            self.satellite_chevron.hide()
            self._status_wave.hide()
            self._status_undo_btn.hide()
            msg = self._status_message if len(self._status_message) <= 18 else "No audio"
            self._status_lbl.setStyleSheet("color: #FF8A8A; background: transparent; border: none;")
            self._status_lbl.setText(msg or "Error")
            self.lbl_state_text.setText(msg or "Error")
            self.lbl_state_text.show()
        elif effective == PillState.MINI_MEETING:
            self.pill_stack.setCurrentIndex(1)
            self.satellite_chevron.hide()
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #080808; border: 1px solid #30302F; border-radius: 24px; }"
            )
            self._update_stack_badge()
        elif effective == PillState.CARD_NOTIFICATION:
            self.pill_stack.setCurrentIndex(2)
            self.satellite_chevron.hide()
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #080808; border: 1px solid #30302F; border-radius: 16px; }"
            )
            self._update_stack_badge()
        elif effective == PillState.ACTIVE_RECORDING:
            self.pill_stack.setCurrentIndex(0)
            self._p0_stack.setCurrentWidget(self.active_recording_capsule)
            self.upper_pill.setStyleSheet("QFrame#flowBarPill { background: transparent; border: none; }")
            self.active_recording_capsule.set_hovered(self._is_hovered or self._contextual_panel_open)
            self.active_recording_capsule.set_finishing(self._meeting_finishing)
            self.active_recording_capsule.set_panel_open(self._contextual_panel_open)
            show_chevron = (self._is_hovered or self._contextual_panel_open) and not self._meeting_finishing
            if show_chevron:
                self.satellite_chevron.show()
                self.satellite_chevron.raise_()
                self.satellite_chevron.animate_reveal()
            else:
                self.satellite_chevron.hide()
            self.btn_cancel.hide()
            self.btn_check.hide()
            self.btn_undo_toast.hide()
            self.waveform_capsule.hide()
            self.lbl_state_text.hide()
            self.btn_stop.hide()
            self._compat_holder.hide()
        elif effective == PillState.L4_PREVIEW:
            self.pill_stack.setCurrentIndex(3)
            self.satellite_chevron.hide()
            self.upper_pill.setStyleSheet(
                "QFrame#flowBarPill { background-color: #080808; border: 1px solid #30302F; border-radius: 16px; }"
            )

        self.meeting_chip.hide()
        self._compat_holder.hide()

    def _sync_geometry(self) -> None:
        effective = self.pill_state
        side_edge = self._dock_edge in ("left", "right")
        if effective in (PillState.RESTING, PillState.IDLE):
            pw, ph = (8, 40) if side_edge else (40, 8)
        elif effective == PillState.READY:
            pw, ph = self._ready_dims()
        elif effective == PillState.LISTENING:
            pw, ph = (72, 30) if self._is_ptt_session else (102, 30)
        elif effective == PillState.PROCESSING:
            pw, ph = (100, 30)
        elif effective == PillState.POLISH_PROCESSING:
            pw, ph = (134, 30)
        elif effective == PillState.COMMITTED:
            pw, ph = (154, 30)
        elif effective == PillState.ERROR:
            err_txt = self._status_lbl.text() or "Error"
            err_w = self._status_lbl.fontMetrics().horizontalAdvance(err_txt)
            pw, ph = (max(96, err_w + 34), 30)
        elif effective == PillState.MINI_MEETING:
            pw, ph = (416, 48)
        elif effective == PillState.CARD_NOTIFICATION:
            pw, ph = (416, 80)
        elif effective == PillState.ACTIVE_RECORDING:
            pw, ph = (30, 72) if side_edge else (72, 30)
        elif effective == PillState.L4_PREVIEW:
            pw, ph = (416, 322)
        else:
            pw, ph = (82, 30)

        self.target_size = QSize(pw, ph)
        self.upper_pill.setFixedSize(pw, ph)

        total_w, total_h = pw, ph
        if effective == PillState.ACTIVE_RECORDING and not self.satellite_chevron.isHidden():
            if side_edge:
                total_h = ph + 6 + self.satellite_chevron.height()
                total_w = max(pw, self.satellite_chevron.width())
            else:
                total_w = pw + 6 + self.satellite_chevron.width()
                total_h = max(ph, self.satellite_chevron.height())

        super().setFixedSize(total_w, total_h)
        if self.isVisible():
            self.dock_to_edge(self._dock_edge)

    def _ready_dims(self) -> tuple[int, int]:
        bw = self.btn_dictate.width()
        bh = self.btn_dictate.height()
        nw = self.btn_notetaker.width()
        nh = self.btn_notetaker.height()
        if self._dock_edge in ("left", "right"):
            return (max(bw, nw), bh + 4 + nh)
        return (bw + 4 + nw, max(bh, nh))

    def _dictate_tooltip_html(self) -> str:
        hk = "F8"
        if self.controller and getattr(self.controller, "config", None):
            hk = _format_hotkey_display(getattr(self.controller.config, "ptt_hotkey", "f8"))
        return f"Dictate <b>{hk}</b>"

    def _notetaker_tooltip_html(self) -> str:
        return "Upcoming meetings"

    def _show_tooltip_for_rect(self, html: str, target_rect: QRect) -> None:
        if not target_rect.isValid():
            target_rect = self._anchor_pill_global_rect()
        self._tooltip_target_html = html
        self._tooltip_target_rect = target_rect
        if self.hover_tooltip.isVisible():
            # If already visible, smoothly update text and position without blinking
            self.hover_tooltip.set_hint(html)
            self.hover_tooltip.position_adjacent_to(target_rect, self._dock_edge, gap=10)
        else:
            # Debounce initial popup to prevent rapid flicker during mouse transitions
            self._tooltip_timer.start(160)

    def _do_show_tooltip(self) -> None:
        if not self._is_hovered and not self._last_hover_zone:
            return
        if self._tooltip_target_html:
            self.hover_tooltip.set_hint(self._tooltip_target_html)
            self.hover_tooltip.show()
            self.hover_tooltip.position_adjacent_to(self._tooltip_target_rect, self._dock_edge, gap=10)

    def enterEvent(self, event) -> None:
        self._hover_leave_timer.stop()
        was_hovered = self._is_hovered
        self._is_hovered = True
        if self._auto_start_timer.isActive():
            self._auto_start_timer.stop()
            self._auto_start_paused = True

        if self._state in (PillState.IDLE, PillState.RESTING):
            self._sync_pill_visuals()
            self._sync_geometry()
            self.dock_to_edge(self._dock_edge)
        elif self._state == PillState.ACTIVE_RECORDING and not was_hovered:
            self._sync_pill_visuals()
            self._sync_geometry()
            self.dock_to_edge(self._dock_edge)
        elif self._state == PillState.MINI_MEETING:
            self._expanded_from_mini = True
            self.set_pill_state(PillState.CARD_NOTIFICATION, metadata=self._current_metadata)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # 70ms grace prevents flickering when crossing between capsule and satellite chevron
        self._hover_leave_timer.start()
        super().leaveEvent(event)

    def _handle_delayed_hover_leave(self) -> None:
        try:
            gpos = QCursor.pos()
            if self.isVisible() and self.geometry().adjusted(-4, -4, 4, 4).contains(gpos):
                return
        except Exception:
            pass
        self._tooltip_timer.stop()
        self._is_hovered = False
        self._last_hover_zone = ""
        self.hover_tooltip.hide()
        if self._state == PillState.CARD_NOTIFICATION and getattr(self, "_expanded_from_mini", False):
            self._expanded_from_mini = False
            self.set_pill_state(PillState.MINI_MEETING, metadata=self._current_metadata)
        elif self._state in (PillState.IDLE, PillState.RESTING, PillState.ACTIVE_RECORDING):
            self._sync_pill_visuals()
            self._sync_geometry()
            self.dock_to_edge(self._dock_edge)

    def _on_dictate_clicked(self) -> None:
        if self._was_dragged:
            return
        self.hover_tooltip.hide()
        self.upcoming_popover.hide()
        self.start_dictation_requested.emit()
        if self.controller and hasattr(self.controller, "toggle_dictation"):
            self.controller.toggle_dictation()

    def _on_dictate_hover(self, entered: bool, rect: QRect) -> None:
        if self._dragging or self._state not in (PillState.IDLE, PillState.RESTING, PillState.READY):
            return
        if entered:
            self._hover_leave_timer.stop()
            self._last_hover_zone = "dictate"
            self._show_tooltip_for_rect(self._dictate_tooltip_html(), rect)
        elif self._last_hover_zone == "dictate":
            self._last_hover_zone = ""
            self._tooltip_timer.stop()
            self.hover_tooltip.hide()

    def _on_notetaker_target_clicked(self) -> None:
        if self._was_dragged:
            return
        self.hover_tooltip.hide()
        self.upcoming_popover.hide()
        self.meeting_toggle_requested.emit()
        if self.controller and hasattr(self.controller, "toggle_meeting_recording"):
            self.controller.toggle_meeting_recording()

    def _on_notetaker_chevron_clicked(self) -> None:
        if self._was_dragged:
            return
        self.hover_tooltip.hide()
        try:
            if self.upcoming_popover.isVisible():
                self.upcoming_popover.hide()
            else:
                self.upcoming_popover.refresh_and_position(
                    self._anchor_pill_global_rect(), self._dock_edge
                )
        except Exception as exc:
            logger.debug("Failed opening upcoming popover: %s", exc)

    def _on_notetaker_hover_zone(self, zone: str, entered: bool, rect: QRect) -> None:
        if self._dragging or self._state not in (PillState.IDLE, PillState.RESTING, PillState.READY):
            return
        if entered and zone:
            self._hover_leave_timer.stop()
            self._last_hover_zone = f"notetaker_{zone}"
            if zone == "chevron":
                self._show_tooltip_for_rect("Upcoming meetings", rect)
            else:
                self._show_tooltip_for_rect(self._notetaker_tooltip_html(), rect)
        elif not entered and self._last_hover_zone.startswith("notetaker_"):
            self._last_hover_zone = ""
            self._tooltip_timer.stop()
            self.hover_tooltip.hide()

    def _on_active_dictation_hover(self, zone: str, entered: bool, rect: QRect) -> None:
        if self._state != PillState.LISTENING:
            return
        if entered and zone == "cancel":
            self._last_hover_zone = "dictate_cancel"
            self._show_tooltip_for_rect("Cancel", rect)
        elif entered and zone == "finish":
            self._last_hover_zone = "dictate_finish"
            self._show_tooltip_for_rect("Finish and paste", rect)
        elif not entered and self._last_hover_zone.startswith("dictate_"):
            self._last_hover_zone = ""
            self.hover_tooltip.hide()

    def _on_equalizer_hover(self, entered: bool, rect: QRect) -> None:
        if self._state != PillState.ACTIVE_RECORDING or self._meeting_finishing:
            return
        if entered:
            self._hover_leave_timer.stop()
            self._last_hover_zone = "equalizer"
            txt = "Hide Notepad" if self._notepad_drawer_open else "Show Notepad"
            self._show_tooltip_for_rect(txt, rect)
        elif self._last_hover_zone == "equalizer":
            self._last_hover_zone = ""
            self.hover_tooltip.hide()

    def _on_stop_ctrl_hover(self, entered: bool, rect: QRect) -> None:
        if self._state != PillState.ACTIVE_RECORDING or self._meeting_finishing:
            return
        if entered:
            self._hover_leave_timer.stop()
            self._last_hover_zone = "stop_ctrl"
            self._show_tooltip_for_rect("Stop recording", rect)
        elif self._last_hover_zone == "stop_ctrl":
            self._last_hover_zone = ""
            self.hover_tooltip.hide()

    def _on_chevron_hover(self, entered: bool, rect: QRect) -> None:
        if self._state != PillState.ACTIVE_RECORDING or self._meeting_finishing:
            return
        if entered:
            self._hover_leave_timer.stop()
            self._last_hover_zone = "satellite_chevron"
            self._show_tooltip_for_rect("Meeting options", rect)
        elif self._last_hover_zone == "satellite_chevron":
            self._last_hover_zone = ""
            self.hover_tooltip.hide()

    def _refresh_contextual_panel(self) -> None:
        self.contextual_panel.update_meeting_data(
            is_recording=bool(self._meeting_active or self._state == PillState.ACTIVE_RECORDING),
            active_title=self._meeting_title or "Untitled Meeting",
            elapsed_str=self._meeting_elapsed_str or "00:00",
            platform=self._meeting_platform or "generic_meeting",
        )
        self.contextual_panel.sync_active_mode(self._current_mode)

    def _position_contextual_panel(self) -> None:
        anchor = self._anchor_pill_global_rect()
        self.contextual_panel.adjustSize()
        pw = max(374, self.contextual_panel.width())
        ph = self.contextual_panel.height()
        gap = 10
        if self._dock_edge == "right":
            x = anchor.left() - pw - gap
            y = anchor.center().y() - ph // 2
        elif self._dock_edge == "left":
            x = anchor.right() + gap
            y = anchor.center().y() - ph // 2
        else:
            x = anchor.center().x() - pw // 2
            y = anchor.top() - ph - gap

        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left() + 8, min(x, avail.right() - pw - 8))
            y = max(avail.top() + 8, min(y, avail.bottom() - ph - 8))
        self.contextual_panel.move(x, y)

    def _toggle_contextual_panel(self) -> None:
        if self._meeting_finishing:
            return
        self.hover_tooltip.hide()
        if self._contextual_panel_open and self.contextual_panel.isVisible():
            self._contextual_panel_open = False
            self.contextual_panel.animate_close()
        else:
            self._contextual_panel_open = True
            self._refresh_contextual_panel()
            self._position_contextual_panel()
            self.contextual_panel.animate_open()
        self._sync_pill_visuals()
        self._sync_geometry()

    def _on_panel_open_note(self) -> None:
        self._contextual_panel_open = False
        self.contextual_panel.animate_close()
        self._on_meeting_chip_clicked()

    def _on_panel_mode_selected(self, mode_obj: Any) -> None:
        self.set_mode(mode_obj)
        if self.controller and hasattr(self.controller, "set_writing_mode"):
            self.controller.set_writing_mode(self._current_mode)

    def _on_start_notes_from_popover(self, meeting_obj: Any) -> None:
        self.upcoming_popover.hide()
        self._contextual_panel_open = False
        self.contextual_panel.hide()
        if meeting_obj is not None and self.controller and hasattr(self.controller, "start_meeting_from_calendar"):
            try:
                self.controller.start_meeting_from_calendar(meeting_obj)
                return
            except Exception as exc:
                logger.debug("start_meeting_from_calendar fallback: %s", exc)
        if self.controller and hasattr(self.controller, "toggle_meeting_recording"):
            self.controller.toggle_meeting_recording()

    def _on_undo_cancelled_dictation(self) -> None:
        if self.controller and hasattr(self.controller, "undo_cancelled_dictation"):
            self.controller.undo_cancelled_dictation()

    def _on_mistake_discard(self, meeting_id: int) -> None:
        if self.controller and getattr(self.controller, "db", None) and meeting_id > 0:
            try:
                self.controller.db.delete_meeting(meeting_id, delete_audio_files=True)
                if hasattr(self.controller, "history_updated"):
                    self.controller.history_updated.emit()
            except Exception as exc:
                logger.debug("Discard short meeting failed: %s", exc)

    def _on_mistake_keep(self, _meeting_id: int) -> None:
        pass

    def set_audio_level(self, rms: float) -> None:
        self.waveform_capsule.set_level(rms)
        self.active_dictation_pill.set_level(rms)
        if self._state == PillState.ACTIVE_RECORDING:
            self.active_recording_capsule.equalizer.set_level(rms)

    def set_mode(self, mode: TransformationMode | str) -> None:
        if isinstance(mode, TransformationMode):
            self._current_mode = mode
        else:
            try:
                self._current_mode = TransformationMode(str(mode).lower())
            except ValueError:
                self._current_mode = TransformationMode.CLEAN
        self.mode_changed.emit(self._current_mode.value)
        if hasattr(self, "contextual_panel"):
            self.contextual_panel.sync_active_mode(self._current_mode)
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
        if s == "transcribing":
            self._meeting_finishing = True
            self._pending_safeguard_check = True
            self.active_recording_capsule.set_finishing(True)
            self.satellite_chevron.hide()
            self._finishing_fallback_timer.start()
            self._sync_pill_visuals()
            self._sync_geometry()
            return
        self._meeting_active = s in ("recording", "paused")
        if self._meeting_active:
            self._meeting_finishing = False
            if self._state != PillState.ACTIVE_RECORDING:
                self.set_pill_state(PillState.ACTIVE_RECORDING, animated=False)
        else:
            if self._state == PillState.ACTIVE_RECORDING and not self._meeting_finishing:
                self.set_pill_state(PillState.RESTING, animated=True)
        self._sync_pill_visuals()
        self._sync_geometry()

    def _on_controller_meeting_tick(self, elapsed: float, *_args) -> None:
        self._last_meeting_elapsed_sec = max(self._last_meeting_elapsed_sec, float(elapsed or 0.0))
        mins, secs = divmod(int(max(0.0, elapsed)), 60)
        self._meeting_elapsed_str = f"{mins:02d}:{secs:02d}"
        if self._meeting_active:
            self.meeting_chip.setText(f"● {self._meeting_elapsed_str} Notetaker")
            if self._state == PillState.ACTIVE_RECORDING:
                self.lbl_state_text.setText(self._meeting_elapsed_str)
            if self._contextual_panel_open and self.contextual_panel.isVisible():
                self.contextual_panel.update_meeting_timer(self._meeting_elapsed_str)
        if _args and self._state == PillState.ACTIVE_RECORDING:
            mic_lvl = float(_args[0]) if len(_args) > 0 else 0.0
            sys_lvl = float(_args[1]) if len(_args) > 1 else 0.0
            lvl = max(mic_lvl, sys_lvl)
            self.set_audio_level(lvl)

    def _on_target_window_changed(self, title: str, proc: str) -> None:
        if title and self._is_hovered and self._last_hover_zone == "dictate":
            self.hover_tooltip.set_hint(self._dictate_tooltip_html())
            if self.hover_tooltip.isVisible():
                self.hover_tooltip.position_adjacent_to(
                    QRect(self.btn_dictate.mapToGlobal(QPoint(0, 0)), self.btn_dictate.size()),
                    self._dock_edge,
                    gap=10,
                )

    def update_live_preview(self, provisional_text: str, stable_prefix: str = "") -> None:
        combined = " ".join(x for x in (stable_prefix.strip(), provisional_text.strip()) if x).strip()
        if not combined:
            self.preview_bubble.hide()
            return
        res = DictationProcessor.detect_verbal_self_corrections(combined)
        annotated = res.get("annotated_html") or combined
        formatted = f"<span style='color: #A3A3A3; font-style: italic;'>{annotated}</span>"
        self.preview_bubble.update_preview(formatted)
        if self.isVisible():
            self.preview_bubble.show()
            self.preview_bubble.update_position_above_pill(self._anchor_pill_global_rect())

    def hide_widget(self) -> None:
        self.hover_tooltip.hide()
        self.preview_bubble.hide()
        self.upcoming_popover.hide()
        self.contextual_panel.hide()
        self.drag_overlay.hide()
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
        self.hover_tooltip.hide()
        if self._state == PillState.ACTIVE_RECORDING or self._meeting_active:
            if self._meeting_finishing:
                return
            self._meeting_finishing = True
            self._pending_safeguard_check = True
            self._contextual_panel_open = False
            self.contextual_panel.hide()
            self.active_recording_capsule.set_finishing(True)
            self.satellite_chevron.hide()
            self._sync_pill_visuals()
            self._sync_geometry()
            self._finishing_fallback_timer.start()
            if self.controller and hasattr(self.controller, "stop_meeting_recording"):
                try:
                    self.controller.stop_meeting_recording()
                except Exception as exc:
                    logger.debug("stop_meeting_recording trigger: %s", exc)
            else:
                self._clear_finishing_state()
            return

        self.finalize_requested.emit()
        if self.on_finalize:
            self.on_finalize()
        elif self.controller and hasattr(self.controller, "finalize_active_dictation"):
            self.controller.finalize_active_dictation()

    def _on_cancel_clicked(self) -> None:
        self.hover_tooltip.hide()
        self.cancel_requested.emit()
        if self.on_cancel:
            self.on_cancel()
        elif self.controller and hasattr(self.controller, "cancel_dictation"):
            self.controller.cancel_dictation()
        self.cancelled_toast.trigger(self._anchor_pill_global_rect(), self._dock_edge)

    def _on_undo_last(self) -> None:
        if self.controller and self._last_entry_id and hasattr(self.controller, "undo_ai_edit"):
            self.controller.undo_ai_edit(self._last_entry_id)
        self.set_state(PillState.IDLE)

    def _on_open_scratchpad(self) -> None:
        self.open_scratchpad_requested.emit()
        if self.controller and hasattr(self.controller, "open_scratchpad_requested"):
            self.controller.open_scratchpad_requested.emit()

    def _on_meeting_chip_clicked(self) -> None:
        self._notepad_drawer_open = not self._notepad_drawer_open
        if self._last_hover_zone == "equalizer" and self.hover_tooltip.isVisible():
            self._on_equalizer_hover(
                True,
                QRect(
                    self.active_recording_capsule.equalizer.mapToGlobal(QPoint(0, 0)),
                    self.active_recording_capsule.equalizer.size(),
                ),
            )
        self.open_notetaker_requested.emit()
        if self.controller and hasattr(self.controller, "open_notetaker_drawer_requested"):
            self.controller.open_notetaker_drawer_requested.emit()

    def _on_open_settings(self) -> None:
        if self.on_open_dashboard:
            self.on_open_dashboard(7)

    def _nearest_edge_and_ratio(self, global_pt: QPoint) -> tuple[str, float]:
        screen = QGuiApplication.screenAt(global_pt) or QGuiApplication.primaryScreen()
        if not screen:
            return ("bottom", 0.5)
        avail = screen.availableGeometry()
        dist_bottom = abs(avail.bottom() - global_pt.y())
        dist_left = abs(global_pt.x() - avail.left())
        dist_right = abs(avail.right() - global_pt.x())
        edge = "bottom"
        if dist_left < dist_bottom and dist_left < dist_right:
            edge = "left"
        elif dist_right < dist_bottom and dist_right < dist_left:
            edge = "right"
        y_ratio = (global_pt.y() - avail.top()) / float(max(1, avail.height()))
        y_ratio = max(0.15, min(0.85, y_ratio))
        return (edge, y_ratio)

    def _begin_drag(self, global_pt: QPoint) -> None:
        self._dragging = True
        self._was_dragged = False
        self._drag_start_pos = global_pt - self.frameGeometry().topLeft()

    def _step_drag(self, global_pt: QPoint) -> bool:
        if self._dragging:
            new_top_left = global_pt - self._drag_start_pos
            delta = new_top_left - self.frameGeometry().topLeft()
            if not self._was_dragged and delta.manhattanLength() > 6:
                self._was_dragged = True
                self._tooltip_timer.stop()
                self.hover_tooltip.hide()
                self.upcoming_popover.hide()
            if self._was_dragged:
                self.move(new_top_left)
                edge, _ = self._nearest_edge_and_ratio(global_pt)
                self.drag_overlay.update_drag(edge, global_pt)
                return True
        return False

    def _end_drag(self, global_pt: QPoint) -> bool:
        was_drag = self._was_dragged
        self._dragging = False
        self.drag_overlay.hide()
        if was_drag:
            edge, y_ratio = self._nearest_edge_and_ratio(global_pt)
            self._dock_y_ratio = y_ratio
            start_pos = self.pos()
            if self.controller and hasattr(self.controller, "set_flow_bar_edge"):
                try:
                    self.controller.set_flow_bar_edge(edge, y_ratio=y_ratio)
                except TypeError:
                    self.controller.set_flow_bar_edge(edge)
            else:
                self.set_dock_edge(edge)
            end_pos = self._compute_dock_position(edge)
            self._snap_anim.stop()
            self._snap_anim.setStartValue(start_pos)
            self._snap_anim.setEndValue(end_pos)
            self._snap_anim.start()
            self._was_dragged = False
            return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            self._step_drag(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_drag = self._end_drag(event.globalPosition().toPoint())
            if not was_drag:
                if self._state in (PillState.IDLE, PillState.RESTING):
                    self._on_pill_clicked()
            event.accept()
            return
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

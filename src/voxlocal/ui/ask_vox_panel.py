"""
VOX v2.2 Material 3 Ask VOX Conversational Panel.

A calm, Gemini-inspired conversational interface featuring:
- Immediate optimistic user message visibility and animated assistant placeholder
- Pure icon + subtle motion processing indicator (zero noisy processing text)
- Real token streaming with coalesced Markdown rendering (tables, lists, code, wrapping)
- Icon-only Copy button with Copy -> Check -> Copy feedback (plus error fallback)
- Zero visible source/citation clutter (citations stay attached internally only)
- Multiline composer (Enter submits, Shift+Enter newline, auto-grow up to max height, Stop while streaming)
- Light/Dark adaptive Material 3 surface hierarchy & Windows reduced-motion support
"""

from __future__ import annotations

import enum
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from voxlocal.ui.animations import is_reduced_motion_enabled
from voxlocal.ui.components import VoxFlowLayout
from voxlocal.ui.icons import get_icon, get_pixmap
from voxlocal.ui.meeting_ui_helpers import (
    WheelForwardingFilter,
    render_safe_markdown_html,
    sanitize_ask_vox_display_markdown,
)
from voxlocal.ui.theme import (
    get_html_font_family,
    get_scrollbar_qss,
    get_theme_palette,
    get_theme_tokens,
    get_ui_font,
)


class AskMessageState(str, enum.Enum):
    """Internal Ask VOX lifecycle states driving motion without visible status text."""

    SUBMITTED = "submitted"
    RETRIEVING_CONTEXT = "retrieving_context"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_pending(self) -> bool:
        return self in (
            AskMessageState.SUBMITTED,
            AskMessageState.RETRIEVING_CONTEXT,
            AskMessageState.GENERATING,
            AskMessageState.VALIDATING,
        )


@dataclass
class AskChatMessage:
    """Stable conversation message object separating user prompts and assistant turns."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = "user"  # "user" | "assistant"
    content: str = ""
    state: AskMessageState = AskMessageState.COMPLETED
    citations: List[Dict[str, Any]] = field(default_factory=list)
    scope_key: str = ""
    scope_label: str = ""
    error_message: str = ""
    question_for_retry: str = ""
    created_at: float = field(default_factory=time.time)


class VoxProcessingIndicator(QWidget):
    """
    Quiet vector + animated dot processing indicator with zero explanatory text.
    Uses a restrained breathing/wave sequence driven by internal processing states
    and automatically freezes into calm static dots when reduced motion is enabled.
    """

    def __init__(
        self,
        theme: str = "light",
        config: Optional[Any] = None,
        compact_trailing: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = (theme or "light").lower()
        self._config = config
        self._compact_trailing = bool(compact_trailing)
        self._state = AskMessageState.SUBMITTED
        self._active = False
        self._phase = 0.0

        self.setFixedHeight(18 if self._compact_trailing else 26)
        self.setFixedWidth(44 if self._compact_trailing else 74)
        self.setAccessibleName("VOX processing response")

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        self.update()

    def set_state(self, state: AskMessageState) -> None:
        self._state = state
        if state.is_pending:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        self._active = True
        if not is_reduced_motion_enabled(self._config):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def stop(self) -> None:
        self._active = False
        if self._timer.isActive():
            self._timer.stop()
        self.update()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self._active and not is_reduced_motion_enabled(self._config):
            if not self._timer.isActive():
                self._timer.start()

    def hideEvent(self, event: Any) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if not self._active or not self.isVisible() or is_reduced_motion_enabled(self._config):
            self._timer.stop()
            return
        step = 0.085
        if self._state == AskMessageState.GENERATING:
            step = 0.12
        elif self._state == AskMessageState.VALIDATING:
            step = 0.065
        self._phase = (self._phase + step) % (2.0 * math.pi)
        self.update()

    def paintEvent(self, _event: Any) -> None:
        t = get_theme_tokens(self._theme)
        accent = QColor(t.get("accent", "#1967D2"))
        muted_col = QColor(t.get("text_muted", "#64748B"))
        reduced = is_reduced_motion_enabled(self._config)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cy = self.height() / 2.0
        dots_start_x = 6.0 if self._compact_trailing else 26.0

        if not self._compact_trailing:
            # Draw soft orbital ring when retrieving context / submitted
            cx_icon = 10.0
            if self._state in (AskMessageState.SUBMITTED, AskMessageState.RETRIEVING_CONTEXT) and not reduced:
                orbit_col = QColor(accent)
                orbit_col.setAlphaF(0.28)
                p.setPen(QPen(orbit_col, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                arc_deg = int((math.degrees(self._phase) * 1.4) % 360)
                p.drawArc(QRectF(cx_icon - 8.0, cy - 8.0, 16.0, 16.0), arc_deg * 16, 110 * 16)

            pm = get_pixmap("sparkles", t.get("accent", "#1967D2"), 13)
            p.drawPixmap(int(cx_icon - 6.5), int(cy - 6.5), pm)

        # Draw 3 calm pulsing dots
        p.setPen(Qt.PenStyle.NoPen)
        spacing = 10.0
        base_r = 2.0 if self._compact_trailing else 2.25
        for idx in range(3):
            if reduced or not self._active:
                alpha = 0.45 + 0.12 * idx
                r = base_r
            else:
                wave = 0.5 + 0.5 * math.sin(self._phase - idx * 0.85)
                alpha = 0.24 + 0.68 * wave
                r = base_r * (0.85 + 0.28 * wave)
            dot_col = QColor(accent if idx != 1 or not reduced else muted_col)
            dot_col.setAlphaF(max(0.15, min(0.95, alpha)))
            p.setBrush(dot_col)
            p.drawEllipse(QPointF(dots_start_x + idx * spacing, cy), r, r)

        p.end()


class VoxUserMessageWidget(QFrame):
    """Right-aligned Material 3 tonal user message bubble supporting multiline plain text."""

    def __init__(
        self,
        message: AskChatMessage,
        theme: str = "light",
        config: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.message = message
        self._theme = (theme or "light").lower()
        self._config = config
        self.setObjectName("askUserTurnHost")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 2, 0, 2)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.bubble = QFrame(self)
        self.bubble.setObjectName("askUserBubble")
        self.bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        b_lay = QVBoxLayout(self.bubble)
        b_lay.setContentsMargins(13, 9, 13, 9)
        b_lay.setSpacing(0)

        self.lbl_text = QLabel(self.bubble)
        self.lbl_text.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.lbl_text.setFont(get_ui_font(13, 400))
        self.lbl_text.setText(str(message.content or ""))
        b_lay.addWidget(self.lbl_text)

        outer.addWidget(self.bubble)
        self.set_theme(self._theme)

    def set_viewport_width(self, width_px: int) -> None:
        max_b = max(180, int(max(240, width_px) * 0.84))
        self.bubble.setMaximumWidth(max_b)
        self.lbl_text.setMaximumWidth(max(140, max_b - 28))

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        bubble_bg = t.get("accent_dawn", "#E8F0FE")
        fg = t.get("text_primary", "#0F172A")
        self.setStyleSheet("QFrame#askUserTurnHost { background: transparent; border: none; }")
        self.bubble.setStyleSheet(
            f"QFrame#askUserBubble {{ background-color: {bubble_bg}; border: none; "
            f"border-radius: 16px; border-bottom-right-radius: 5px; }}"
        )
        self.lbl_text.setStyleSheet(
            f"background: transparent; border: none; color: {fg}; font-size: 13px; line-height: 1.45;"
        )


class VoxAssistantMessageWidget(QFrame):
    """
    Borderless, conversational assistant turn with smooth animated processing placeholder,
    progressive Markdown streaming (tables, lists, headings, code blocks), zero visible
    citations/sources, and an icon-only Copy button (Copy -> Check -> Copy).
    """

    retry_requested = pyqtSignal(str)
    content_height_changed = pyqtSignal()

    def __init__(
        self,
        message: AskChatMessage,
        theme: str = "light",
        config: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.message = message
        self._theme = (theme or "light").lower()
        self._config = config
        self._hovered = False
        self._copy_feedback_state = "idle"  # "idle" | "copied" | "error"
        self._last_rendered_md: str = ""
        self._last_rendered_theme: str = ""
        self._dirty_stream = False

        self.setObjectName("askAssistantTurn")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 4, 2, 8)
        root.setSpacing(4)

        # Minimal top row: quiet sparkle icon on left + hover-accented icon-only action cluster
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)

        self.lbl_badge_icon = QLabel(self)
        self.lbl_badge_icon.setFixedSize(16, 16)
        hdr.addWidget(self.lbl_badge_icon)

        hdr.addStretch(1)

        self.btn_retry = QPushButton("", self)
        self.btn_retry.setObjectName("askIconActionBtn")
        self.btn_retry.setFixedSize(26, 26)
        self.btn_retry.setIconSize(QSize(14, 14))
        self.btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_retry.setToolTip("Retry")
        self.btn_retry.setAccessibleName("Retry generation")
        self.btn_retry.clicked.connect(self._on_retry_clicked)
        self.btn_retry.setVisible(False)
        hdr.addWidget(self.btn_retry)

        self.btn_copy = QPushButton("", self)
        self.btn_copy.setObjectName("askIconActionBtn")
        self.btn_copy.setFixedSize(26, 26)
        self.btn_copy.setIconSize(QSize(14, 14))
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setToolTip("Copy response")
        self.btn_copy.setAccessibleName("Copy response")
        self.btn_copy.clicked.connect(self._on_copy_clicked)
        self.btn_copy.setVisible(False)
        hdr.addWidget(self.btn_copy)

        root.addLayout(hdr)

        # Pending animation placeholder (icons + motion only, zero status text)
        self.indicator = VoxProcessingIndicator(
            theme=self._theme,
            config=self._config,
            compact_trailing=False,
            parent=self,
        )
        root.addWidget(self.indicator)

        # Clean Markdown browser (borderless, wheel forwarding, non-clipping)
        self.browser = QTextBrowser(self)
        self.browser.setObjectName("askAssistantBrowser")
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setOpenExternalLinks(False)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.browser.setMinimumWidth(0)
        self._wheel_filter = WheelForwardingFilter(self.browser)
        self.browser.installEventFilter(self._wheel_filter)
        self.browser.viewport().installEventFilter(self._wheel_filter)
        self.browser.setVisible(False)
        root.addWidget(self.browser)

        # Subtle trailing dot indicator shown ONLY while streaming tokens live
        self.trailing_indicator = VoxProcessingIndicator(
            theme=self._theme,
            config=self._config,
            compact_trailing=True,
            parent=self,
        )
        self.trailing_indicator.setVisible(False)
        root.addWidget(self.trailing_indicator)

        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.timeout.connect(self._reset_copy_icon)

        # Coalesce rapid streamed tokens into smooth ~25fps HTML reflows
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.setInterval(40)
        self._stream_flush_timer.timeout.connect(self._flush_stream_render)

        self.set_theme(self._theme)
        self.sync_from_message()

    def play_entrance(self) -> None:
        if is_reduced_motion_enabled(self._config):
            return
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(140)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self.setGraphicsEffect(None))
        anim.start()
        self._entrance_anim = anim

    def set_viewport_width(self, _width_px: int) -> None:
        if self.browser.isVisible():
            self._sync_browser_height()

    def set_state(self, state: AskMessageState) -> None:
        self.message.state = state
        self.sync_from_message()

    def append_token(self, chunk: str) -> None:
        if not chunk:
            return
        self.message.content = str(self.message.content or "") + str(chunk)
        if self.message.state in (AskMessageState.SUBMITTED, AskMessageState.RETRIEVING_CONTEXT):
            self.message.state = AskMessageState.GENERATING
        self._dirty_stream = True
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _flush_stream_render(self) -> None:
        if not self._dirty_stream:
            return
        self._dirty_stream = False
        self.sync_from_message()
        self.content_height_changed.emit()

    def set_final_result(
        self,
        *,
        content: str,
        citations: Optional[List[Dict[str, Any]]] = None,
        state: AskMessageState = AskMessageState.COMPLETED,
        error_message: str = "",
    ) -> None:
        if self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
        self._dirty_stream = False
        self.message.content = str(content or "")
        if citations is not None:
            self.message.citations = list(citations)
        self.message.state = state
        self.message.error_message = str(error_message or "")
        self.sync_from_message()
        self.content_height_changed.emit()

    def sync_from_message(self) -> None:
        st = self.message.state
        sanitized = sanitize_ask_vox_display_markdown(self.message.content)
        has_text = bool(sanitized.strip())

        if st.is_pending:
            self.btn_retry.setVisible(False)
            self.btn_copy.setVisible(False)
            if not has_text:
                self.indicator.set_state(st)
                self.indicator.setVisible(True)
                self.trailing_indicator.stop()
                self.trailing_indicator.setVisible(False)
                self.browser.setVisible(False)
                self.lbl_badge_icon.setVisible(False)
            else:
                self.indicator.stop()
                self.indicator.setVisible(False)
                self.lbl_badge_icon.setVisible(True)
                self._render_markdown(sanitized)
                self.browser.setVisible(True)
                self.trailing_indicator.set_state(st)
                self.trailing_indicator.setVisible(True)
        elif st == AskMessageState.COMPLETED:
            self.indicator.stop()
            self.indicator.setVisible(False)
            self.trailing_indicator.stop()
            self.trailing_indicator.setVisible(False)
            self.lbl_badge_icon.setVisible(True)
            body = sanitized or "No matching information was found in the selected meeting scope."
            self._render_markdown(body)
            self.browser.setVisible(True)
            self.btn_retry.setVisible(False)
            self.btn_copy.setVisible(True)
        elif st == AskMessageState.FAILED:
            self.indicator.stop()
            self.indicator.setVisible(False)
            self.trailing_indicator.stop()
            self.trailing_indicator.setVisible(False)
            self.lbl_badge_icon.setVisible(True)
            err_md = sanitized or (self.message.error_message or "Couldn't generate a response right now.")
            self._render_markdown(err_md)
            self.browser.setVisible(True)
            self.btn_retry.setVisible(bool(self.message.question_for_retry))
            self.btn_copy.setVisible(bool(sanitized))
        elif st == AskMessageState.CANCELLED:
            self.indicator.stop()
            self.indicator.setVisible(False)
            self.trailing_indicator.stop()
            self.trailing_indicator.setVisible(False)
            self.lbl_badge_icon.setVisible(True)
            body = sanitized if has_text else "Generation stopped."
            self._render_markdown(body)
            self.browser.setVisible(True)
            self.btn_copy.setVisible(has_text)
            self.btn_retry.setVisible(bool(self.message.question_for_retry))

        self._refresh_action_icons()

    def _render_markdown(self, markdown_text: str) -> None:
        if (
            markdown_text == self._last_rendered_md
            and self._theme == self._last_rendered_theme
            and self.browser.toPlainText()
        ):
            self._sync_browser_height()
            return
        self._last_rendered_md = markdown_text
        self._last_rendered_theme = self._theme
        html_str = render_safe_markdown_html(
            markdown_text,
            theme=self._theme,
            include_timestamp_chips=False,
        )
        self.browser.setHtml(html_str)
        self._sync_browser_height()

    def _sync_browser_height(self) -> None:
        doc = self.browser.document()
        if doc is None:
            return
        w = self.browser.viewport().width()
        if w <= 40:
            w = max(200, self.width() - 16)
        doc.setTextWidth(float(max(160, w)))
        h = int(math.ceil(doc.size().height())) + 8
        self.browser.setMinimumHeight(max(24, h))
        self.browser.setMaximumHeight(max(24, h + 4))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.browser.isVisible():
            self._sync_browser_height()

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self._refresh_action_icons()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self._refresh_action_icons()
        super().leaveEvent(event)

    def _on_copy_clicked(self) -> None:
        payload = sanitize_ask_vox_display_markdown(self.message.content) or self.browser.toPlainText().strip()
        if not payload:
            return
        try:
            cb = QApplication.clipboard()
            if cb is None:
                raise RuntimeError("Clipboard unavailable")
            cb.setText(payload)
            self._copy_feedback_state = "copied"
            self.btn_copy.setToolTip("Copied")
            self.btn_copy.setAccessibleName("Copied response")
            self._refresh_action_icons()
            self._copy_reset_timer.start(1600)
        except Exception:
            self._copy_feedback_state = "error"
            self.btn_copy.setToolTip("Could not copy")
            self.btn_copy.setAccessibleName("Could not copy response")
            self._refresh_action_icons()
            self._copy_reset_timer.start(2000)

    def _reset_copy_icon(self) -> None:
        self._copy_feedback_state = "idle"
        self.btn_copy.setToolTip("Copy response")
        self.btn_copy.setAccessibleName("Copy response")
        self._refresh_action_icons()

    def _on_retry_clicked(self) -> None:
        q = str(self.message.question_for_retry or "").strip()
        if q:
            self.retry_requested.emit(q)

    def _refresh_action_icons(self) -> None:
        t = get_theme_tokens(self._theme)
        ai_col = t.get("accent", "#1967D2")
        self.lbl_badge_icon.setPixmap(get_pixmap("sparkles", ai_col, 14))

        muted = t.get("text_muted", "#64748B")
        fg = t.get("text_primary", "#0F172A")
        hover_bg = t.get("surface_hover", "#F1F5F9")

        if self._copy_feedback_state == "copied":
            copy_icon_name = "check"
            copy_col = t.get("status_success", "#059669")
        elif self._copy_feedback_state == "error":
            copy_icon_name = "alert"
            copy_col = t.get("status_danger", "#DC2626")
        else:
            copy_icon_name = "copy"
            copy_col = fg if self._hovered else muted

        self.btn_copy.setIcon(get_icon(copy_icon_name, copy_col, 14))
        self.btn_retry.setIcon(get_icon("retry", fg if self._hovered else muted, 14))

        btn_css = (
            f"QPushButton#askIconActionBtn {{ background: transparent; border: none; border-radius: 13px; padding: 0px; }} "
            f"QPushButton#askIconActionBtn:hover {{ background-color: {hover_bg}; }}"
        )
        self.btn_copy.setStyleSheet(btn_css)
        self.btn_retry.setStyleSheet(btn_css)

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"QFrame#askAssistantTurn {{ background: transparent; border: none; }} "
            f"QTextBrowser#askAssistantBrowser {{ background: transparent; border: none; color: {t['text_primary']}; }}"
        )
        self.indicator.set_theme(self._theme)
        self.trailing_indicator.set_theme(self._theme)
        self._refresh_action_icons()
        if self._last_rendered_md:
            self._render_markdown(self._last_rendered_md)


class VoxContextDividerWidget(QFrame):
    """Quiet subtle hairline separating turns after switching scope within one conversation."""

    def __init__(self, label: str, theme: str = "light", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = (theme or "light").lower()
        self.setObjectName("askContextDivider")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        self.line_l = QFrame(self)
        self.line_l.setFixedHeight(1)
        self.lbl = QLabel(str(label or ""), self)
        self.line_r = QFrame(self)
        self.line_r.setFixedHeight(1)

        lay.addWidget(self.line_l, 1)
        lay.addWidget(self.lbl)
        lay.addWidget(self.line_r, 1)
        self.set_theme(self._theme)

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        bdr = t.get("outline_variant", "#E2E8F0")
        muted = t.get("text_muted", "#64748B")
        self.line_l.setStyleSheet(f"background-color: {bdr}; border: none;")
        self.line_r.setStyleSheet(f"background-color: {bdr}; border: none;")
        self.lbl.setStyleSheet(f"color: {muted}; font-size: 11px; font-weight: 500; background: transparent;")


class VoxChatTextEdit(QPlainTextEdit):
    """Auto-growing multiline input: Enter submits, Shift+Enter inserts newline, Escape cancels active run."""

    send_triggered = pyqtSignal()
    stop_triggered = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._min_height = 34
        self._max_height = 112
        self._is_busy = False
        self.setObjectName("askChatTextEdit")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setPlaceholderText("Ask VOX anything...")
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFont(get_ui_font(13, 400))
        self.textChanged.connect(self._adjust_height)
        self._adjust_height()

    # Compatibility helpers so callers/tests treating input like QLineEdit work cleanly
    def text(self) -> str:
        return self.toPlainText()

    def setText(self, val: str) -> None:
        self.setPlainText(str(val or ""))

    def set_busy(self, busy: bool) -> None:
        self._is_busy = bool(busy)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Escape and self._is_busy:
            event.accept()
            self.stop_triggered.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                self._adjust_height()
                return
            event.accept()
            self.send_triggered.emit()
            return
        super().keyPressEvent(event)

    def _adjust_height(self) -> None:
        doc = self.document()
        if doc is None:
            return
        fm = QFontMetrics(self.font())
        line_cnt = max(1, int(doc.blockCount()))
        doc_h = int(math.ceil(doc.size().height()))
        # Ensure clean single-line compactness before wrapping
        if doc_h <= 10:
            doc_h = fm.lineSpacing() * line_cnt + 10
        target = max(self._min_height, min(self._max_height, doc_h + 10))
        self.setFixedHeight(target)


class VoxChatInputComposer(QFrame):
    """Material 3 multiline chat composer with focus highlight and icon-only Send/Stop button."""

    query_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()

    def __init__(
        self,
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = (theme or "light").lower()
        self._busy = False
        self._submit_validator: Optional[Any] = None

        self.setObjectName("askChatComposer")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 7, 6)
        lay.setSpacing(6)

        self.input_edit = VoxChatTextEdit(self)
        self.input_edit.send_triggered.connect(self._on_submit_triggered)
        self.input_edit.stop_triggered.connect(self.stop_requested.emit)
        self.input_edit.textChanged.connect(self._sync_action_button)
        self.input_edit.installEventFilter(self)
        lay.addWidget(self.input_edit, 1, Qt.AlignmentFlag.AlignVCenter)

        self.btn_action = QPushButton("", self)
        self.btn_action.setObjectName("askComposerActionBtn")
        self.btn_action.setFixedSize(32, 32)
        self.btn_action.setIconSize(QSize(15, 15))
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.setToolTip("Send message")
        self.btn_action.setAccessibleName("Send message")
        self.btn_action.clicked.connect(self._on_action_btn_clicked)
        lay.addWidget(self.btn_action, 0, Qt.AlignmentFlag.AlignBottom)

        self.set_theme(self._theme)
        self._sync_action_button()

    def set_submit_validator(self, validator: Any) -> None:
        """Register callable returning True only when parent accepted submission (so draft is safely cleared)."""
        self._submit_validator = validator

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.input_edit.set_busy(self._busy)
        self._sync_action_button()

    def is_busy(self) -> bool:
        return self._busy

    def set_placeholder(self, text: str) -> None:
        self.input_edit.setPlaceholderText(str(text or "Ask VOX anything..."))

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        if obj is self.input_edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.set_theme(self._theme)
        return super().eventFilter(obj, event)

    def _on_action_btn_clicked(self) -> None:
        if self._busy:
            self.stop_requested.emit()
        else:
            self._on_submit_triggered()

    def _on_submit_triggered(self) -> None:
        if self._busy:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if self._submit_validator is not None:
            accepted = bool(self._submit_validator(text))
            if accepted:
                self.input_edit.clear()
        else:
            self.query_submitted.emit(text)
            self.input_edit.clear()

    def _sync_action_button(self) -> None:
        t = get_theme_tokens(self._theme)
        has_text = bool(self.input_edit.toPlainText().strip())
        if self._busy:
            self.btn_action.setEnabled(True)
            self.btn_action.setToolTip("Stop generating")
            self.btn_action.setAccessibleName("Stop generating")
        else:
            self.btn_action.setToolTip("Send message")
            self.btn_action.setAccessibleName("Send message")
            self.btn_action.setEnabled(has_text)

        state_key = (self._theme, self._busy, has_text)
        if state_key == getattr(self, "_last_qss_btn_state", None):
            return
        self._last_qss_btn_state = state_key
        if self._busy:
            self.btn_action.setIcon(get_icon("stop", t.get("primary_btn_text", "#FFFFFF"), 13))
            self.btn_action.setStyleSheet(
                f"QPushButton#askComposerActionBtn {{ background-color: {t['accent']}; border: none; border-radius: 16px; padding: 0px; }} "
                f"QPushButton#askComposerActionBtn:hover {{ background-color: {t['primary_btn_hover']}; }}"
            )
        elif has_text:
            self.btn_action.setIcon(get_icon("send", t.get("primary_btn_text", "#FFFFFF"), 14))
            self.btn_action.setStyleSheet(
                f"QPushButton#askComposerActionBtn {{ background-color: {t['primary_btn_bg']}; border: none; border-radius: 16px; padding: 0px; }} "
                f"QPushButton#askComposerActionBtn:hover {{ background-color: {t['primary_btn_hover']}; }} "
                f"QPushButton#askComposerActionBtn:pressed {{ background-color: {t['accent']}; }}"
            )
        else:
            self.btn_action.setIcon(get_icon("send", t.get("text_dim", "#94A3B8"), 14))
            self.btn_action.setStyleSheet(
                f"QPushButton#askComposerActionBtn {{ background-color: {t['surface_well']}; border: none; border-radius: 16px; padding: 0px; }}"
            )

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        focused = self.input_edit.hasFocus()
        bdr = t.get("input_focus_border", "#1A73E8") if focused else t.get("outline_variant", "#E2E8F0")
        bw = "1.5px" if focused else "1px"
        bg = t.get("surface", "#FFFFFF") if self._theme == "light" else t.get("surface_container_low", "#0F1624")
        self.setStyleSheet(
            f"QFrame#askChatComposer {{ background-color: {bg}; border: {bw} solid {bdr}; border-radius: 16px; }} "
            f"QPlainTextEdit#askChatTextEdit {{ background: transparent; border: none; color: {t['text_primary']}; "
            f"selection-background-color: {t['accent_dawn']}; selection-color: {t['text_primary']}; }}"
        )
        self._sync_action_button()


class VoxAskConversationView(QScrollArea):
    """
    Smooth vertical conversation feed holding user bubbles, borderless assistant answers,
    subtle empty-state chips, and context dividers without source/citation clutter.
    """

    retry_requested = pyqtSignal(str)
    chip_clicked = pyqtSignal(int)

    def __init__(
        self,
        theme: str = "light",
        config: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = (theme or "light").lower()
        self._config = config
        self._msg_widgets: Dict[str, QWidget] = {}
        self._dividers: List[VoxContextDividerWidget] = []
        self._ordered_ids: List[str] = []
        self._user_scrolled_up = False

        self.setObjectName("askConversationScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._host = QWidget()
        self._host.setObjectName("askConversationHost")
        self._host.setMinimumWidth(0)
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(4, 4, 6, 8)
        self._layout.setSpacing(8)

        # Quiet Material 3 empty state (auto-hidden when conversation begins)
        self.empty_state = QWidget(self._host)
        es_lay = QVBoxLayout(self.empty_state)
        es_lay.setContentsMargins(6, 20, 6, 12)
        es_lay.setSpacing(10)

        self.lbl_empty_title = QLabel("Ask about this meeting", self.empty_state)
        self.lbl_empty_title.setObjectName("askEmptyTitle")
        self.lbl_empty_title.setWordWrap(True)
        es_lay.addWidget(self.lbl_empty_title)

        self.lbl_empty_sub = QLabel(
            "Summaries, decisions, action items, or questions grounded in your discussion.",
            self.empty_state,
        )
        self.lbl_empty_sub.setObjectName("askEmptySub")
        self.lbl_empty_sub.setWordWrap(True)
        es_lay.addWidget(self.lbl_empty_sub)

        chips_flow = VoxFlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self.starter_chip_buttons: List[QPushButton] = []
        for idx in range(3):
            cb = QPushButton("", self.empty_state)
            cb.setObjectName("askStarterChipBtn")
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.clicked.connect(lambda _=False, i=idx: self.chip_clicked.emit(i))
            chips_flow.addWidget(cb)
            self.starter_chip_buttons.append(cb)
        es_lay.addLayout(chips_flow)
        self._layout.addWidget(self.empty_state)

        self._feed_layout = QVBoxLayout()
        self._feed_layout.setContentsMargins(0, 0, 0, 0)
        self._feed_layout.setSpacing(10)
        self._layout.addLayout(self._feed_layout)
        self._layout.addStretch(1)

        self.setWidget(self._host)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(10)
        self._scroll_timer.timeout.connect(self._snap_scroll_bottom)

        sb = self.verticalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(self._on_scroll_changed)

        self.set_theme(self._theme)

    def _on_scroll_changed(self, value: int) -> None:
        sb = self.verticalScrollBar()
        if sb is None or sb.maximum() <= 0:
            self._user_scrolled_up = False
            return
        self._user_scrolled_up = (sb.maximum() - value) > 48

    def set_empty_prompts(self, title: str, subtitle: str, chips: List[Tuple[str, str]]) -> None:
        self.lbl_empty_title.setText(title)
        self.lbl_empty_sub.setText(subtitle)
        for idx, btn in enumerate(self.starter_chip_buttons):
            if idx < len(chips):
                btn.setText(chips[idx][0])
                btn.setProperty("ask_prompt", chips[idx][1])
                btn.setVisible(True)
            else:
                btn.setVisible(False)

    def clear_messages(self) -> None:
        for mid in list(self._ordered_ids):
            w = self._msg_widgets.pop(mid, None)
            if w is not None:
                self._feed_layout.removeWidget(w)
                w.deleteLater()
        for div in self._dividers:
            self._feed_layout.removeWidget(div)
            div.deleteLater()
        self._dividers.clear()
        self._ordered_ids.clear()
        self._user_scrolled_up = False
        self.empty_state.setVisible(True)

    def load_messages(self, messages: List[AskChatMessage]) -> None:
        self.clear_messages()
        for msg in messages:
            self.append_message(msg, animate=False, scroll=False)
        if messages:
            self.scroll_to_bottom(force=True)

    def append_divider(self, label: str) -> None:
        if not self._ordered_ids:
            return
        div = VoxContextDividerWidget(label=label, theme=self._theme, parent=self._host)
        self._feed_layout.addWidget(div)
        self._dividers.append(div)

    def append_message(
        self,
        message: AskChatMessage,
        *,
        animate: bool = True,
        scroll: bool = True,
    ) -> QWidget:
        self.empty_state.setVisible(False)
        vp_w = self.viewport().width() if self.viewport() else 320
        if message.role == "user":
            w: QWidget = VoxUserMessageWidget(
                message, theme=self._theme, config=self._config, parent=self._host
            )
            w.set_viewport_width(vp_w)
        else:
            w = VoxAssistantMessageWidget(
                message, theme=self._theme, config=self._config, parent=self._host
            )
            w.retry_requested.connect(self.retry_requested.emit)
            w.content_height_changed.connect(lambda: self.scroll_to_bottom(force=False))
            w.set_viewport_width(vp_w)
            if animate:
                w.play_entrance()

        self._msg_widgets[message.id] = w
        self._ordered_ids.append(message.id)
        self._feed_layout.addWidget(w)
        if scroll:
            self.scroll_to_bottom(force=(message.role == "user"))
        return w

    def get_message_widget(self, msg_id: str) -> Optional[QWidget]:
        return self._msg_widgets.get(msg_id)

    def message_count(self) -> int:
        return len(self._ordered_ids)

    def _snap_scroll_bottom(self) -> None:
        try:
            sb = self.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
        except RuntimeError:
            pass

    def scroll_to_bottom(self, *, force: bool = False) -> None:
        if self._user_scrolled_up and not force:
            return
        if force:
            self._user_scrolled_up = False
        self._snap_scroll_bottom()
        self._scroll_timer.start()

    def stop_all_animations(self) -> None:
        if self._scroll_timer.isActive():
            self._scroll_timer.stop()
        for w in self._msg_widgets.values():
            if isinstance(w, VoxAssistantMessageWidget):
                w.indicator.stop()
                w.trailing_indicator.stop()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        vp_w = self.viewport().width() if self.viewport() else self.width()
        for w in self._msg_widgets.values():
            if hasattr(w, "set_viewport_width"):
                w.set_viewport_width(vp_w)
        if not self._user_scrolled_up and self._ordered_ids:
            self._scroll_timer.start()

    def toPlainText(self) -> str:
        """Compatibility helper returning visible conversational text with zero citation/source clutter."""
        chunks: List[str] = []
        for mid in self._ordered_ids:
            w = self._msg_widgets.get(mid)
            if isinstance(w, VoxUserMessageWidget):
                txt = w.lbl_text.text().strip()
                if txt:
                    chunks.append(txt)
            elif isinstance(w, VoxAssistantMessageWidget):
                txt = w.browser.toPlainText().strip() if w.browser.isVisible() else ""
                if txt:
                    chunks.append(txt)
        return "\n\n".join(chunks)

    def toHtml(self) -> str:
        chunks: List[str] = []
        for mid in self._ordered_ids:
            w = self._msg_widgets.get(mid)
            if isinstance(w, VoxUserMessageWidget):
                chunks.append(w.lbl_text.text())
            elif isinstance(w, VoxAssistantMessageWidget) and w.browser.isVisible():
                chunks.append(w.browser.toHtml())
        return "\n".join(chunks)

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        self.setPalette(get_theme_palette(self._theme))
        self.viewport().setStyleSheet(f"background: {t['surface']}; border: none;")
        self.setStyleSheet(
            f"QScrollArea#askConversationScroll {{ background: {t['surface']}; border: none; }} "
            f"QWidget#askConversationHost {{ background: {t['surface']}; border: none; }} "
            f"{get_scrollbar_qss(self._theme)} "
            f"QLabel#askEmptyTitle {{ color: {t['text_primary']}; font-size: 14px; font-weight: 600; background: transparent; }} "
            f"QLabel#askEmptySub {{ color: {t['text_secondary']}; font-size: 12.5px; line-height: 1.45; background: transparent; }} "
            f"QPushButton#askStarterChipBtn {{ background-color: {t['surface_container']}; color: {t['text_primary']}; "
            f"border: 1px solid {t['outline_variant']}; border-radius: 14px; padding: 6px 11px; "
            f"font-size: 12px; font-weight: 500; }} "
            f"QPushButton#askStarterChipBtn:hover {{ background-color: {t['accent_dawn']}; color: {t['accent_dawn_text']}; "
            f"border-color: {t['dawn_btn_border']}; }}"
        )
        for div in self._dividers:
            div.set_theme(self._theme)
        for w in self._msg_widgets.values():
            if hasattr(w, "set_theme"):
                w.set_theme(self._theme)


class VoxAskPanel(QFrame):
    """
    Minimal Material 3 Ask VOX Right Rail / Side-Sheet Panel.

    Header: sparkle icon + "Ask VOX" + icon-only New Chat + icon-only Close
    Context bar: compact scope dropdown + subtle muted meeting/scope label
    Body: VoxAskConversationView
    Footer: VoxChatInputComposer ("Ask VOX anything...")
    """

    query_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()
    retry_requested = pyqtSignal(str)
    new_chat_requested = pyqtSignal()
    scope_changed = pyqtSignal(str)
    close_requested = pyqtSignal()

    def __init__(
        self,
        theme: str = "light",
        config: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = (theme or "light").lower()
        self._config = config
        self._compact_mode = False
        self._submit_validator: Optional[Any] = None

        self.setObjectName("ntAskRail")
        self.setMinimumWidth(260)
        self.setMaximumWidth(480)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(14, 12, 14, 12)
        self._root_layout.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(2, 0, 0, 0)
        hdr.setSpacing(6)

        self.lbl_header_icon = QLabel(self)
        self.lbl_header_icon.setFixedSize(16, 16)
        hdr.addWidget(self.lbl_header_icon)

        self.lbl_title = QLabel("Ask VOX", self)
        self.lbl_title.setObjectName("askPanelTitle")
        self.lbl_title.setFont(get_ui_font(14, 600))
        hdr.addWidget(self.lbl_title)
        hdr.addStretch(1)

        self.btn_new_chat = QPushButton("", self)
        self.btn_new_chat.setObjectName("askHeaderIconBtn")
        self.btn_new_chat.setFixedSize(28, 28)
        self.btn_new_chat.setIconSize(QSize(14, 14))
        self.btn_new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new_chat.setToolTip("New chat")
        self.btn_new_chat.setAccessibleName("New chat")
        self.btn_new_chat.clicked.connect(self.new_chat_requested.emit)
        hdr.addWidget(self.btn_new_chat)

        self.btn_close = QPushButton("", self)
        self.btn_close.setObjectName("askHeaderIconBtn")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setIconSize(QSize(14, 14))
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("Close Ask VOX")
        self.btn_close.setAccessibleName("Close Ask VOX rail")
        self.btn_close.clicked.connect(self.close_requested.emit)
        hdr.addWidget(self.btn_close)

        self._root_layout.addLayout(hdr)

        # Minimal scope bar (compact selector + calm muted scope label)
        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 2)
        scope_row.setSpacing(8)

        self.combo_scope = QComboBox(self)
        self.combo_scope.setObjectName("askScopeCombo")
        self.combo_scope.addItem("This Meeting", "this_meeting")
        self.combo_scope.addItem("Last 3 Meetings", "last_3")
        self.combo_scope.addItem("Last 5 Meetings", "last_5")
        self.combo_scope.addItem("All Meetings", "all")
        self.combo_scope.setToolTip("Choose conversation scope")
        self.combo_scope.setAccessibleName("Ask VOX scope")
        self.combo_scope.currentIndexChanged.connect(
            lambda _idx: self.scope_changed.emit(str(self.combo_scope.currentData() or "this_meeting"))
        )
        scope_row.addWidget(self.combo_scope)

        self.lbl_scope_context = QLabel("", self)
        self.lbl_scope_context.setObjectName("askScopeContextLbl")
        self.lbl_scope_context.setMinimumWidth(0)
        self.lbl_scope_context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scope_row.addWidget(self.lbl_scope_context, 1)

        self._root_layout.addLayout(scope_row)

        # Conversation scroll feed
        self.conversation_view = VoxAskConversationView(
            theme=self._theme,
            config=self._config,
            parent=self,
        )
        self.conversation_view.retry_requested.connect(self.retry_requested.emit)
        self.conversation_view.chip_clicked.connect(self._on_starter_chip_clicked)
        self._root_layout.addWidget(self.conversation_view, 1)

        # Bottom multiline composer
        self.composer = VoxChatInputComposer(theme=self._theme, parent=self)
        self.composer.stop_requested.connect(self.stop_requested.emit)
        self.composer.query_submitted.connect(self.query_submitted.emit)
        self._root_layout.addWidget(self.composer)

        self.starter_chip_buttons = self.conversation_view.starter_chip_buttons
        self.set_theme(self._theme)

    def set_submit_validator(self, validator: Any) -> None:
        self._submit_validator = validator
        self.composer.set_submit_validator(validator)

    def set_compact_mode(self, compact: bool) -> None:
        c = bool(compact)
        if c == self._compact_mode:
            return
        self._compact_mode = c
        if c:
            self._root_layout.setContentsMargins(10, 10, 10, 10)
            self._root_layout.setSpacing(6)
        else:
            self._root_layout.setContentsMargins(14, 12, 14, 12)
            self._root_layout.setSpacing(8)

    def set_context_display(
        self,
        *,
        scope_selector_visible: bool,
        context_text: str,
        empty_title: str,
        empty_subtitle: str,
        chips: List[Tuple[str, str]],
    ) -> None:
        self.combo_scope.setVisible(scope_selector_visible)
        self.lbl_scope_context.setText(str(context_text or ""))
        self.lbl_scope_context.setToolTip(str(context_text or ""))
        self.conversation_view.set_empty_prompts(empty_title, empty_subtitle, chips)

    def set_busy(self, busy: bool) -> None:
        self.composer.set_busy(busy)

    def _on_starter_chip_clicked(self, idx: int) -> None:
        if 0 <= idx < len(self.starter_chip_buttons):
            prompt = str(self.starter_chip_buttons[idx].property("ask_prompt") or "").strip()
            if prompt:
                if self._submit_validator is not None:
                    self._submit_validator(prompt)
                else:
                    self.query_submitted.emit(prompt)

    def set_theme(self, theme_name: str) -> None:
        self._theme = (theme_name or "light").lower()
        t = get_theme_tokens(self._theme)
        self.setPalette(get_theme_palette(self._theme))
        self.setStyleSheet(
            f"QFrame#ntAskRail {{ background-color: {t['surface']}; border: 1px solid {t['outline_variant']}; border-radius: 12px; }} "
            f"QLabel#askPanelTitle {{ color: {t['text_primary']}; background: transparent; }} "
            f"QLabel#askScopeContextLbl {{ color: {t['text_muted']}; font-size: 11.5px; font-weight: 500; background: transparent; }} "
            f"QPushButton#askHeaderIconBtn {{ background: transparent; border: none; border-radius: 14px; padding: 0px; }} "
            f"QPushButton#askHeaderIconBtn:hover {{ background-color: {t['surface_hover']}; }} "
            f"QComboBox#askScopeCombo {{ background-color: {t['surface_container']}; color: {t['text_primary']}; "
            f"border: 1px solid {t['outline_variant']}; border-radius: 8px; padding: 4px 10px; font-size: 12px; font-weight: 600; }} "
            f"QComboBox#askScopeCombo:hover {{ background-color: {t['surface_hover']}; }} "
            f"QComboBox#askScopeCombo QAbstractItemView {{ background-color: {t['surface']}; color: {t['text_primary']}; "
            f"border: 1px solid {t['border']}; selection-background-color: {t['accent_dawn']}; selection-color: {t['accent_dawn_text']}; }}"
        )
        self.lbl_header_icon.setPixmap(get_pixmap("sparkles", t.get("accent", "#1967D2"), 15))
        fg_sec = t.get("text_secondary", "#475569")
        self.btn_new_chat.setIcon(get_icon("new_chat", fg_sec, 14))
        self.btn_close.setIcon(get_icon("close", fg_sec, 14))
        self.conversation_view.set_theme(self._theme)
        self.composer.set_theme(self._theme)

    apply_theme = set_theme

"""
VoxLocal Advance Meeting Alert Prompt.
Non-activating floating notification banner (Wispr Flow inspired)
alerting user of imminent calendar meetings and providing 1-click local capture.

Invariants:
- Win32 Focus Invariance: WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
  ensuring active document cursor focus is never stolen.
- 1-Click Bot-Free Capture: Instantly pre-fills meeting details and launches local recording.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.calendar.base import CalendarMeeting
from voxlocal.core.logger import get_logger
from voxlocal.ui.animations import AnimatedPushButton
from voxlocal.ui.theme import get_theme_tokens
from voxlocal.win32.window_focus import apply_non_activating_floating_style

logger = get_logger("ui.calendar_prompt")


class VoxMeetingPromptPopup(QWidget):
    """
    Non-activating floating toast notification popup alerting user of upcoming meetings.
    Anchored to bottom-right of primary screen with countdown timer and 1-click capture CTA.
    """

    start_capture_requested = pyqtSignal(object)  # (CalendarMeeting)
    dismissed = pyqtSignal(str)  # (meeting_id: str)
    snoozed = pyqtSignal(str, int)  # (meeting_id: str, minutes: int)

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark") -> None:
        super().__init__(parent)
        self.theme = theme
        self.current_meeting: Optional[CalendarMeeting] = None
        self._target_start_time: Optional[datetime] = None

        # Non-activating floating window flags
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._update_countdown)

        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(0)

        t = get_theme_tokens(self.theme)

        # Card container with shadow & border
        self.card = QFrame()
        self.card.setObjectName("calendarPromptCard")
        self.card.setStyleSheet(
            f"QFrame#calendarPromptCard {{ "
            f"background-color: {t.get('surface_alt', '#1C1A18')}; "
            f"border: 1.5px solid {t.get('border', '#334155')}; "
            f"border-radius: 12px; }}"
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 8)
        self.card.setGraphicsEffect(shadow)

        c_layout = QVBoxLayout(self.card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(8)

        # Header Row: Icon, Status Badge, Close
        h_row = QHBoxLayout()
        h_row.setSpacing(8)

        self.lbl_header = QLabel("Calendar · Upcoming Meeting")
        self.lbl_header.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {t.get('accent_fathom', '#034F46')}; "
            f"letter-spacing: 0.04em; text-transform: uppercase; background: transparent;"
        )
        h_row.addWidget(self.lbl_header)

        h_row.addStretch()

        self.lbl_countdown = QLabel("in 5m")
        self.lbl_countdown.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {t.get('accent_amber', '#FFA946')}; background: transparent;")
        h_row.addWidget(self.lbl_countdown)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {t.get('text_muted', '#736E62')}; font-size: 12px; font-weight: bold; }} "
            f"QPushButton:hover {{ color: {t.get('accent_crimson', '#FF5757')}; }}"
        )
        btn_close.clicked.connect(self.dismiss)
        h_row.addWidget(btn_close)

        c_layout.addLayout(h_row)

        # Title Row
        self.lbl_title = QLabel("Product Architecture & PLM Sync")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setStyleSheet(f"font-size: 13.5px; font-weight: 700; color: {t.get('text', '#1A1A1A')}; background: transparent;")
        c_layout.addWidget(self.lbl_title)

        # Metadata Row (Organizer & Attendees)
        self.lbl_meta = QLabel("Organized by Organizer • Attendees • Teams")
        self.lbl_meta.setStyleSheet(f"font-size: 11.5px; color: {t.get('text_muted', '#736E62')}; background: transparent;")
        c_layout.addWidget(self.lbl_meta)

        # Action Buttons Row
        b_row = QHBoxLayout()
        b_row.setSpacing(8)

        self.btn_capture = AnimatedPushButton("Start Notetaker")
        self.btn_capture.setStyleSheet(
            f"QPushButton {{ background-color: {t.get('dawn_btn_bg', '#F0D7FF')}; "
            f"color: {t.get('dawn_btn_text', '#1A1A1A')}; font-weight: 700; font-size: 12px; "
            f"border: 1px solid {t.get('dawn_btn_border', '#D2ACE9')}; border-radius: 8px; padding: 6px 14px; }} "
            f"QPushButton:hover {{ background-color: {t.get('accent_dawn_hover', '#E6BEFF')}; }}"
        )
        self.btn_capture.clicked.connect(self._on_capture_clicked)
        b_row.addWidget(self.btn_capture)

        self.btn_snooze = AnimatedPushButton("Snooze (5m)")
        self.btn_snooze.setStyleSheet(
            f"QPushButton {{ background-color: {t.get('secondary_btn_bg', '#FFFFFF')}; "
            f"color: {t.get('secondary_btn_text', '#1A1A1A')}; font-size: 11px; font-weight: 600; "
            f"border: 1px solid {t.get('secondary_btn_border', '#E5E2D9')}; border-radius: 8px; padding: 6px 10px; }} "
            f"QPushButton:hover {{ background-color: {t.get('secondary_btn_hover', '#F5F4F0')}; }}"
        )
        self.btn_snooze.clicked.connect(self.snooze)
        b_row.addWidget(self.btn_snooze)

        self.btn_dismiss = QPushButton("Dismiss")
        self.btn_dismiss.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {t.get('text_muted', '#736E62')}; font-size: 11px; border: none; padding: 6px 8px; }} "
            f"QPushButton:hover {{ color: {t.get('text', '#1A1A1A')}; }}"
        )
        self.btn_dismiss.clicked.connect(self.dismiss)
        b_row.addWidget(self.btn_dismiss)

        c_layout.addLayout(b_row)
        root_layout.addWidget(self.card)
        self.apply_theme(self.theme)
        self.setFixedSize(440, 152)

    def apply_theme(self, theme_name: str) -> None:
        """Update prompt card styling when global Light/Dark theme toggles."""
        self.theme = (theme_name or "light").lower()
        t = get_theme_tokens(self.theme)
        self.card.setStyleSheet(
            f"QFrame#calendarPromptCard {{ "
            f"background-color: {t.get('surface', '#FFFFFF')}; "
            f"border: 1px solid {t.get('border', '#E5E2D9')}; "
            f"border-radius: 14px; "
            f"}}"
        )
        if hasattr(self, "lbl_header"):
            self.lbl_header.setStyleSheet(
                f"font-size: 11px; font-weight: 700; color: {t.get('accent_fathom', '#034F46')}; "
                f"letter-spacing: 0.04em; text-transform: uppercase; background: transparent;"
            )
        if hasattr(self, "lbl_title"):
            self.lbl_title.setStyleSheet(
                f"font-size: 13.5px; font-weight: 700; color: {t.get('text', '#1A1A1A')}; background: transparent;"
            )
        if hasattr(self, "lbl_meta"):
            self.lbl_meta.setStyleSheet(
                f"font-size: 11.5px; color: {t.get('text_muted', '#736E62')}; background: transparent;"
            )
        if hasattr(self, "btn_capture"):
            self.btn_capture.setStyleSheet(
                f"QPushButton {{ background-color: {t.get('dawn_btn_bg', '#F0D7FF')}; "
                f"color: {t.get('dawn_btn_text', '#1A1A1A')}; font-weight: 700; font-size: 12px; "
                f"border: 1px solid {t.get('dawn_btn_border', '#D2ACE9')}; border-radius: 8px; padding: 6px 14px; }} "
                f"QPushButton:hover {{ background-color: {t.get('accent_dawn_hover', '#E6BEFF')}; }}"
            )
        if hasattr(self, "btn_snooze"):
            self.btn_snooze.setStyleSheet(
                f"QPushButton {{ background-color: {t.get('secondary_btn_bg', '#FFFFFF')}; "
                f"color: {t.get('secondary_btn_text', '#1A1A1A')}; font-size: 11px; font-weight: 600; "
                f"border: 1px solid {t.get('secondary_btn_border', '#E5E2D9')}; border-radius: 8px; padding: 6px 10px; }} "
                f"QPushButton:hover {{ background-color: {t.get('secondary_btn_hover', '#F5F4F0')}; }}"
            )
        if hasattr(self, "btn_dismiss"):
            self.btn_dismiss.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {t.get('text_muted', '#736E62')}; "
                f"font-size: 11px; border: none; padding: 6px 8px; }} "
                f"QPushButton:hover {{ color: {t.get('text', '#1A1A1A')}; }}"
            )

    set_theme = apply_theme

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._position_bottom_right()
        # Apply Win32 extended style on Windows so focus remains untouched
        if sys.platform == "win32":
            try:
                apply_non_activating_floating_style(int(self.winId()))
            except Exception as exc:
                logger.debug("Could not apply Win32 non-activating style: %s", exc)

    def _position_bottom_right(self) -> None:
        """Position popup 24px from bottom-right of primary active screen."""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - 24
        y = geo.bottom() - self.height() - 24
        self.move(x, y)

    def show_meeting_alert(self, meeting: CalendarMeeting, minutes_until_start: int) -> None:
        """Configure popup content and display floating banner without activating focus."""
        self.current_meeting = meeting
        self._target_start_time = meeting.start_time

        self.lbl_title.setText(meeting.title)

        att_count = len(meeting.attendees)
        org = meeting.organizer or "Organizer"
        platform = meeting.location or ("Teams" if meeting.is_online else "In-Person")
        att_preview = f" ({', '.join(meeting.attendees[:2])})" if meeting.attendees else ""
        self.lbl_meta.setText(f"Organized by {org} • {att_count} Attendees{att_preview} • {platform}")

        if minutes_until_start <= 0:
            self.lbl_countdown.setText("Starting Now!")
            self.lbl_countdown.setStyleSheet("font-size: 11px; font-weight: 700; color: #EF4444;")
        else:
            self.lbl_countdown.setText(f"in {minutes_until_start}m")
            self.lbl_countdown.setStyleSheet("font-size: 11px; font-weight: 600; color: #F59E0B;")

        self._countdown_timer.start()
        self.show()
        logger.info("Shown advance meeting prompt for: %s (in %dm)", meeting.title, minutes_until_start)

    def _update_countdown(self) -> None:
        if not self._target_start_time:
            return
        now = datetime.now(timezone.utc)
        target = self._target_start_time
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        diff = (target - now).total_seconds()
        if diff <= 0:
            self.lbl_countdown.setText("Starting Now!")
            self.lbl_countdown.setStyleSheet("font-size: 11px; font-weight: 700; color: #EF4444;")
        else:
            m = int(diff) // 60
            s = int(diff) % 60
            self.lbl_countdown.setText(f"in {m:02d}:{s:02d}")

    def _on_capture_clicked(self) -> None:
        if self.current_meeting:
            self.start_capture_requested.emit(self.current_meeting)
        self.hide()
        self._countdown_timer.stop()

    def snooze(self) -> None:
        """Snooze alert prompt for 5 minutes."""
        if self.current_meeting:
            self.snoozed.emit(self.current_meeting.id, 5)
        self.hide()
        self._countdown_timer.stop()

    def dismiss(self) -> None:
        """Dismiss alert prompt for this meeting."""
        if self.current_meeting:
            self.dismissed.emit(self.current_meeting.id)
        self.hide()
        self._countdown_timer.stop()

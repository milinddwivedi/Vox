"""
Interactive Bottom-Right Meeting Confirmation Popup for VoxLocal.

Appears automatically when an active Microsoft Teams, Zoom, or Google Meet
call is detected. Anchored to the bottom-right of the user's primary screen
(above the taskbar), non-modal, and never steals focus from the active meeting.

Actions:
- [Record Meeting]: Launches Call-Only Mode A recording strictly bound to the meeting surface.
- [Not Now]: Dismisses the prompt for this session without badgering.
- [Ignore this meeting]: Permanently suppresses prompts for this specific meeting session ID.
- Auto-dismisses after 20 seconds of inactivity without interrupting the call.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING, Optional, Tuple
from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QScreen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.audio.call_detector import MeetingSessionInfo
from voxlocal.ui.theme import get_theme_tokens
from voxlocal.win32.window_focus import apply_non_activating_floating_style

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController


class MeetingPromptPopup(QWidget):
    """
    Polished, non-activating bottom-right desktop notification popup
    requesting explicit user consent to record the detected meeting.
    """

    COUNTDOWN_SECONDS = 20

    def __init__(
        self,
        session_info: MeetingSessionInfo,
        controller: AppController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.session_info = session_info
        self.controller = controller
        self._seconds_left = self.COUNTDOWN_SECONDS

        # Non-activating, always-on-top, frameless tool window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._build_ui()
        self.reposition_bottom_right()

        # 1-second countdown timer for auto-dismiss
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def update_session(self, session_info: MeetingSessionInfo) -> None:
        """Update existing popup with new or refreshed session details without re-animating."""
        self.session_info = session_info
        self._seconds_left = self.COUNTDOWN_SECONDS
        if hasattr(self, "lbl_title"):
            self.lbl_title.setText(self.session_info.clean_title)
        if hasattr(self, "lbl_badge"):
            self._update_badge_style()
        if hasattr(self, "btn_record"):
            is_rec = bool(
                hasattr(self.controller, "_capture_state")
                and getattr(self.controller, "_capture_state", "") in ("recording", "paused")
            )
            self.btn_record.setText("⚡ Switch Call" if is_rec else "Start Notetaker")
        self._update_countdown_label()
        self.reposition_smart()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        self.controller.register_ui_hwnd(hwnd)
        apply_non_activating_floating_style(hwnd)
        self.reposition_smart()

    def _find_meeting_screen(self) -> QScreen:
        """Find the QScreen where the detected meeting is located, or fallback to primary."""
        screen = None
        if self.session_info and self.session_info.window_hwnd:
            try:
                rect = wintypes.RECT()
                if user32.GetWindowRect(self.session_info.window_hwnd, ctypes.byref(rect)):
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w > 0 and h > 0:
                        center_pt = QPoint(rect.left + w // 2, rect.top + h // 2)
                        screen = QGuiApplication.screenAt(center_pt)
            except Exception:
                screen = None
        if not screen:
            screen = QGuiApplication.primaryScreen()
        return screen

    def _detect_bottom_right_obstruction(
        self, screen_avail
    ) -> Optional[Tuple[int, int, int, int]]:
        """Detect any visible external notification toasts (e.g. Teams, Action Center) in bottom-right corner."""
        my_hwnd = int(self.winId()) if self.isVisible() else 0
        registered = getattr(self.controller, "registered_ui_hwnds", set())
        corner_left = screen_avail.right() - 520
        corner_top = screen_avail.bottom() - 420

        found_rect: Optional[Tuple[int, int, int, int]] = None

        def _enum_cb(hwnd: int, _lparam: int) -> bool:
            nonlocal found_rect
            try:
                if hwnd == my_hwnd or hwnd in registered:
                    return True
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True

                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True

                w = rect.right - rect.left
                h = rect.bottom - rect.top
                # Must be a reasonable notification/toast window size
                if w < 100 or h < 50 or w > 750 or h > 550:
                    return True

                # Check if window intersects bottom-right toast zone OR intersects VOX window
                intersects_corner = (
                    rect.right > corner_left
                    and rect.bottom > corner_top
                    and rect.left < screen_avail.right()
                    and rect.top < screen_avail.bottom()
                )
                my_rect = self.geometry()
                intersects_vox = (
                    rect.left < my_rect.right()
                    and rect.right > my_rect.left()
                    and rect.top < my_rect.bottom()
                    and rect.bottom > my_rect.top()
                )
                if intersects_corner or intersects_vox:
                    cbuf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cbuf, 256)
                    cls = cbuf.value.lower()
                    # Skip taskbars and desktop shell
                    if "shell_traywnd" in cls or "progman" in cls or "workerw" in cls:
                        return True

                    # Identified candidate notification toast (e.g. Teams toast, Windows toast)
                    found_rect = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                    return False
            except Exception:
                pass
            return True

        cb = WNDENUMPROC(_enum_cb)
        user32.EnumWindows(cb, 0)
        return found_rect

    def reposition_smart(self) -> None:
        """Intelligently anchor popup with collision avoidance against Teams and Windows toasts.

        Rules:
        1. Multi-monitor aware: Anchors on the screen containing the active meeting window.
        2. Taskbar-aware: Uses screen.availableGeometry() to stay above/inside the work area.
        3. Toast collision avoidance:
           - Stacks cleanly ABOVE detected Teams/Windows notification toasts (+16px margin).
           - If no toast is currently active on primary screen, reserves 280px above taskbar
             so subsequent Teams toasts never obscure VOX prompt.
           - If vertical space is constrained, shifts to the left of the toast.
        4. Focus protection: Frameless tool window, non-activating, HWND_TOPMOST without focus stealing.
        """
        screen = self._find_meeting_screen()
        if not screen:
            screen = QGuiApplication.primaryScreen()
        if not screen:
            return

        avail = screen.availableGeometry()
        w = 348
        h = max(195, self.sizeHint().height())
        pad = 16
        toast_lane_w = 420  # Windows 10/11 notification + Teams toast width lane (420px)
        obstruction = self._detect_bottom_right_obstruction(avail)

        if obstruction:
            obs_left, obs_top, obs_right, obs_bottom = obstruction
            cand_y = obs_top - h - pad
            cand_x = avail.right() - w - pad

            # If there is vertical room above the toast, stack above it
            if cand_y >= avail.top() + pad:
                x = cand_x
                y = cand_y
            else:
                # If tight vertically, dock safely to the left of the notification toast
                x = obs_left - w - pad
                y = min(obs_top, avail.bottom() - h - pad)
        else:
            # Proactive collision prevention:
            # Anchor safely to the LEFT of the 420px toast shelf
            # so subsequent Teams toasts (lobby, chat, call) can NEVER overlap or occlude VOX.
            if avail.width() >= 1200:
                x = avail.right() - toast_lane_w - w - pad
                y = avail.bottom() - h - pad
            elif avail.height() >= 840:
                x = avail.right() - w - pad
                y = avail.bottom() - h - 320 - pad
            else:
                x = avail.right() - toast_lane_w - w - pad
                y = avail.bottom() - h - pad

        # Clamp strictly inside available screen boundaries
        x = max(avail.left() + pad, min(x, avail.right() - w - pad))
        y = max(avail.top() + pad, min(y, avail.bottom() - h - pad))
        self.setGeometry(x, y, w, h)
        try:
            # Enforce topmost placement at exact coordinates with SWP_NOACTIVATE (0x0010)
            user32.SetWindowPos(int(self.winId()), -1, x, y, w, h, 0x0010 | 0x0040)
            self.raise_()
        except Exception:
            pass

    def reposition_bottom_right(self) -> None:
        """Backwards compatibility alias for reposition_smart."""
        self.reposition_smart()

    def _get_platform_color(self) -> Tuple[str, str, str]:
        """Returns (badge_icon, badge_text, badge_bg_color) for the meeting platform."""
        plat = self.session_info.platform.lower()
        if "teams" in plat:
            return "●", "Microsoft Teams", "#464EB8"
        elif "zoom" in plat:
            return "●", "Zoom", "#2D8CFF"
        elif "meet" in plat:
            return "●", "Google Meet", "#0F9D58"
        elif "slack" in plat:
            return "●", "Slack Huddle", "#E01E5A"
        elif "webex" in plat:
            return "●", "Webex", "#00A0D1"
        return "●", self.session_info.platform or "Meeting", "#0EA5E9"

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        theme_name = getattr(self.controller, "theme", None) or getattr(getattr(self.controller, "config", None), "theme", "light")
        if theme_name not in ("light", "dark"):
            theme_name = "light"
        self._theme = theme_name
        t = get_theme_tokens(theme_name)

        self.card = QFrame()
        self.card.setStyleSheet(
            f"""
            QFrame {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 14px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {t['text']};
            }}
            QPushButton {{
                border-radius: 8px;
                font-size: 11px;
                font-weight: 700;
                padding: 6px 12px;
            }}
            """
        )
        c_layout = QVBoxLayout(self.card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(8)

        # Header Row: Badge + Status + Close '✕'
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)

        icon_str, badge_text, badge_bg = self._get_platform_color()
        self.lbl_badge = QLabel(f"{icon_str}  {badge_text}")
        self.lbl_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: white; border-radius: 6px; padding: 3px 7px; font-size: 10px; font-weight: 700;"
        )
        self.lbl_badge.setMinimumWidth(0)
        hdr_row.addWidget(self.lbl_badge)

        self.lbl_hdr_status = QLabel("Meeting Detected")
        self.lbl_hdr_status.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px; font-weight: 600;")
        self.lbl_hdr_status.setMinimumWidth(0)
        hdr_row.addWidget(self.lbl_hdr_status, 1)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setStyleSheet(
            f"background: transparent; color: {t['text_muted']}; border: none; font-size: 13px; font-weight: bold; border-radius: 4px;"
        )
        btn_close.clicked.connect(self._on_dismiss)
        hdr_row.addWidget(btn_close)
        c_layout.addLayout(hdr_row)

        # Meeting Title
        self.lbl_title = QLabel(self.session_info.clean_title)
        self.lbl_title.setStyleSheet(f"color: {t['text']}; font-size: 13.5px; font-weight: 700;")
        self.lbl_title.setWordWrap(True)
        c_layout.addWidget(self.lbl_title)

        # Minimal Sub-caption + Compact Recording Mode Toggle (Audio Only vs Audio + Video)
        sub_mode_row = QHBoxLayout()
        sub_mode_row.setContentsMargins(0, 0, 0, 0)
        sub_mode_row.setSpacing(6)
        self.lbl_sub = QLabel("Capture:")
        self.lbl_sub.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")
        sub_mode_row.addWidget(self.lbl_sub)

        def_mode = getattr(getattr(self.controller, "config", None), "meeting_recording_mode", "audio_video")
        if def_mode not in ("audio_only", "audio_video"):
            def_mode = "audio_video" if getattr(getattr(self.controller, "config", None), "record_meeting_screen", True) else "audio_only"
        self._selected_mode = def_mode

        self.btn_mode_audio = QPushButton("Audio Only")
        self.btn_mode_audio.setFixedHeight(24)
        self.btn_mode_audio.setMinimumWidth(0)
        self.btn_mode_audio.setToolTip("Record meeting audio only (mic + speaker loopback)")
        self.btn_mode_audio.clicked.connect(lambda: self.set_recording_mode("audio_only"))
        sub_mode_row.addWidget(self.btn_mode_audio)

        self.btn_mode_video = QPushButton("Audio + Video")
        self.btn_mode_video.setFixedHeight(24)
        self.btn_mode_video.setMinimumWidth(0)
        self.btn_mode_video.setToolTip("Record meeting audio and active call window video")
        self.btn_mode_video.clicked.connect(lambda: self.set_recording_mode("audio_video"))
        sub_mode_row.addWidget(self.btn_mode_video)
        sub_mode_row.addStretch()
        c_layout.addLayout(sub_mode_row)
        self.set_recording_mode(self._selected_mode)

        # Countdown Progress Indicator
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, self.COUNTDOWN_SECONDS)
        self.progress_bar.setValue(self.COUNTDOWN_SECONDS)
        self.progress_bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: {t['progressbar_bg']};
                border: none;
                border-radius: 1px;
            }}
            QProgressBar::chunk {{
                background: {t['accent_fathom']};
                border-radius: 1px;
            }}
            """
        )
        c_layout.addWidget(self.progress_bar)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        is_already_rec = bool(
            hasattr(self.controller, "_capture_state")
            and getattr(self.controller, "_capture_state", "") in ("recording", "paused")
        )
        self.btn_record = QPushButton("⚡ Switch Call" if is_already_rec else "Start Notetaker")
        self.btn_record.setMinimumWidth(0)
        self.btn_record.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                font-weight: 700;
                padding: 7px 10px;
            }}
            QPushButton:hover {{
                background-color: {t['accent_dawn_hover']};
            }}
            """
        )
        self.btn_record.clicked.connect(self._on_record)
        btn_row.addWidget(self.btn_record, 2)

        self.btn_not_now = QPushButton("Not Now")
        self.btn_not_now.setMinimumWidth(0)
        self.btn_not_now.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {t['secondary_btn_bg']};
                color: {t['secondary_btn_text']};
                border: 1px solid {t['secondary_btn_border']};
                font-weight: 600;
                padding: 7px 10px;
            }}
            QPushButton:hover {{
                background-color: {t['secondary_btn_hover']};
            }}
            """
        )
        self.btn_not_now.clicked.connect(self._on_dismiss)
        btn_row.addWidget(self.btn_not_now, 1)
        c_layout.addLayout(btn_row)

        # Bottom Sub-Row: Countdown text & 'Ignore this meeting' link
        sub_row = QHBoxLayout()
        self.lbl_countdown = QLabel(f"Auto-dismiss in {self._seconds_left}s")
        self.lbl_countdown.setStyleSheet(f"color: {t['text_dim']}; font-size: 10px;")
        sub_row.addWidget(self.lbl_countdown)
        sub_row.addStretch()

        btn_ignore = QPushButton("Ignore this meeting")
        btn_ignore.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {t['text_muted']};
                border: none;
                font-size: 10px;
                text-decoration: underline;
                padding: 0px 4px;
            }}
            QPushButton:hover {{
                color: {t['text']};
            }}
            """
        )
        btn_ignore.clicked.connect(self._on_ignore)
        sub_row.addWidget(btn_ignore)
        c_layout.addLayout(sub_row)

        outer.addWidget(self.card)
        self.setFixedWidth(348)
        self.adjustSize()

    def set_recording_mode(self, mode: str) -> None:
        if mode not in ("audio_only", "audio_video"):
            mode = "audio_video"
        self._selected_mode = mode
        is_video = (mode == "audio_video")
        t = get_theme_tokens(getattr(self, "_theme", "light"))
        active_css = (
            f"background-color: {t['accent_fathom_tint']}; color: {t['accent_fathom']}; "
            f"border: 1px solid {t['accent_fathom']}; border-radius: 6px; font-size: 10px; font-weight: 700; padding: 2px 8px;"
        )
        inactive_css = (
            f"background-color: {t['chip_bg']}; color: {t['text_muted']}; "
            f"border: 1px solid {t['border']}; border-radius: 6px; font-size: 10px; font-weight: 600; padding: 2px 8px;"
        )
        if hasattr(self, "btn_mode_audio"):
            self.btn_mode_audio.setStyleSheet(active_css if not is_video else inactive_css)
        if hasattr(self, "btn_mode_video"):
            self.btn_mode_video.setStyleSheet(active_css if is_video else inactive_css)

    def _update_badge_style(self) -> None:
        icon_str, badge_text, badge_bg = self._get_platform_color()
        self.lbl_badge.setText(f"{icon_str}  {badge_text}")
        self.lbl_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: white; border-radius: 6px; padding: 3px 8px; font-size: 10px; font-weight: 700;"
        )

    def _update_countdown_label(self) -> None:
        if hasattr(self, "lbl_countdown"):
            self.lbl_countdown.setText(f"Auto-dismiss in {self._seconds_left}s")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(self._seconds_left)

    def _on_tick(self) -> None:
        self._seconds_left -= 1
        self._update_countdown_label()
        # Active collision avoidance: reposition dynamically if an external toast appears
        try:
            self.reposition_smart()
        except Exception:
            pass
        if self._seconds_left <= 0:
            self._on_dismiss()

    def _on_record(self) -> None:
        self._timer.stop()
        sess = self.session_info
        if hasattr(self.controller, "call_detector") and self.controller.call_detector:
            self.controller.call_detector.mark_decision(sess.session_id, "approved")

        rec_mode = getattr(self, "_selected_mode", "audio_video")
        rec_screen = (rec_mode == "audio_video")
        if hasattr(self.controller, "config") and self.controller.config:
            self.controller.config.meeting_recording_mode = rec_mode
            self.controller.config.record_meeting_screen = rec_screen
            try:
                self.controller.config.save()
            except Exception:
                pass

        # If meeting is already recording, execute seamless call switch
        if (
            hasattr(self.controller, "_capture_state")
            and getattr(self.controller, "_capture_state", "") in ("recording", "paused")
            and hasattr(self.controller, "switch_to_meeting")
        ):
            self.controller.switch_to_meeting(
                new_title=sess.clean_title,
                new_platform=sess.platform,
                new_target_hwnd=sess.window_hwnd,
                new_pid=sess.pid,
                recording_mode=rec_mode,
            )
            self.close()
            return

        # Start Call-Only Recording bound directly to the meeting surface in the user's chosen mode
        self.controller.start_meeting_recording(
            title=sess.clean_title,
            capture_mode="both",
            record_screen=rec_screen,
            screen_fps=int(getattr(getattr(self.controller, "config", None), "screen_fps", 15)),
            target_hwnd=sess.window_hwnd,
            recording_mode=rec_mode,
        )
        self.close()

    def _on_dismiss(self) -> None:
        self._timer.stop()
        sess = self.session_info
        if hasattr(self.controller, "call_detector") and self.controller.call_detector:
            self.controller.call_detector.mark_decision(sess.session_id, "dismissed")
        if hasattr(self.controller, "dismiss_call_prompt"):
            self.controller.dismiss_call_prompt(sess.session_id)
        self.close()

    def _on_ignore(self) -> None:
        self._timer.stop()
        sess = self.session_info
        if hasattr(self.controller, "call_detector") and self.controller.call_detector:
            self.controller.call_detector.mark_decision(sess.session_id, "ignored")
        if hasattr(self.controller, "dismiss_call_prompt"):
            self.controller.dismiss_call_prompt(sess.session_id)
        self.close()

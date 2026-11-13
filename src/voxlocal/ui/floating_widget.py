"""Compatibility & Dialog module unifying VOX's FloatingWidget with the Wispr-style VoxFlowBar.

Provides:
- `FloatingWidget`: positional-argument facade around `VoxFlowBar` so TrayManager and legacy callers
  route through the unified FlowBar overlay with zero duplicate floating windows.
- `FloatingInfoBubble`: compact non-activating hover/shortcut tooltip popup.
- `MeetingQuickStartDialog`: Call-Only Privacy Mode selector (Audio Only vs Audio + Video).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional, Tuple

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.controller import AppController
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    DynamicWaveformCapsule,
    LiveSpeechPreviewBubble,
    PillState,
    SessionTimerWidget,
    VoxFlowBar,
)
from voxlocal.ui.theme import get_dialog_stylesheet, get_theme_tokens, get_ui_font
from voxlocal.win32.window_focus import apply_non_activating_floating_style


class FloatingInfoBubble(QWidget):
    """Non-activating tooltip card displayed near the FlowBar with quick shortcuts & status."""

    def __init__(self, theme: str = "light", controller: Optional[AppController] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = theme or "light"
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        c_lay = QVBoxLayout(self.card)
        c_lay.setContentsMargins(12, 8, 12, 8)
        c_lay.setSpacing(3)

        self.lbl_title = QLabel("VOX FlowBar")
        self.lbl_title.setFont(get_ui_font(12, 600))
        self.lbl_body = QLabel("Hold Ctrl+Win to dictate or double-tap for Hands-Free.")
        self.lbl_body.setFont(get_ui_font(11, 400))
        self.lbl_body.setWordWrap(True)
        c_lay.addWidget(self.lbl_title)
        c_lay.addWidget(self.lbl_body)
        layout.addWidget(self.card)
        self.apply_theme(self._theme)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_non_activating_floating_style(int(self.winId()))

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        t = get_theme_tokens(theme)
        self.card.setStyleSheet(
            f"QFrame {{ background-color: #1A1A1A; border: 1px solid #30302F; border-radius: 10px; }}"
        )
        self.lbl_title.setStyleSheet("color: #FCFCFB; border: none; background: transparent;")
        self.lbl_body.setStyleSheet(f"color: #DEDDD7; border: none; background: transparent;")

    def update_content(self, title: str, body: str) -> None:
        self.lbl_title.setText(title)
        self.lbl_body.setText(body)
        self.adjustSize()


class FloatingWidget(VoxFlowBar):
    """
    Backwards-compatible positional wrapper around VoxFlowBar (`controller, on_open_dashboard, on_exit_app`).
    Eliminates legacy vertical floating bar duplication so the desktop shows one crisp FlowBar.
    """

    def __init__(
        self,
        controller: AppController,
        on_open_dashboard: Callable[[int], None],
        on_exit_app: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        theme = controller.theme if controller else "light"
        super().__init__(
            parent=parent,
            theme=theme,
            on_finalize=controller.finalize_active_dictation if hasattr(controller, "finalize_active_dictation") else None,
            on_cancel=controller.cancel_dictation if hasattr(controller, "cancel_dictation") else None,
            controller=controller,
            on_open_dashboard=on_open_dashboard,
            on_exit_app=on_exit_app,
        )
        self.info_bubble = FloatingInfoBubble(theme=theme, controller=controller)


class MeetingQuickStartDialog(QDialog):
    """Compact Recording Mode Selector & Call Detection Dialog matching VOX's Sand Light / Vast Dark style."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        default_screen_rec: bool = True,
        default_fps: int = 15,
        theme: str = "light",
        controller: Optional[AppController] = None,
        on_open_studio: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.on_open_studio = on_open_studio
        self._default_screen_rec = default_screen_rec
        self._default_fps = default_fps
        self._theme = theme or "light"
        self.detected_call: Optional[Tuple[int, str, str, Tuple[int, int, int, int]]] = None
        self._selected_mode: str = "audio_video" if default_screen_rec else "audio_only"

        self.setWindowTitle("VOX Notetaker — Select Recording Mode")
        self.setFixedWidth(470)
        self.setStyleSheet(get_dialog_stylesheet(self._theme))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        t = get_theme_tokens(self._theme)
        self._tokens = t

        header_row = QHBoxLayout()
        header = QLabel("VOX Notetaker Session")
        header.setFont(get_ui_font(17, 600, serif=True))
        header.setStyleSheet(f"color: {t['text']}; border: none; background: transparent;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.btn_retry_detect = QPushButton("Retry Detection")
        self.btn_retry_detect.setToolTip("Rescan for active Microsoft Teams, Zoom, Google Meet, Webex, or Slack calls")
        self.btn_retry_detect.clicked.connect(self.refresh_detection)
        header_row.addWidget(self.btn_retry_detect)
        layout.addLayout(header_row)

        self.status_card = QFrame()
        sc_layout = QVBoxLayout(self.status_card)
        sc_layout.setContentsMargins(12, 10, 12, 10)
        sc_layout.setSpacing(4)
        self.lbl_call = QLabel("")
        self.lbl_call_desc = QLabel("")
        self.lbl_call_desc.setWordWrap(True)
        sc_layout.addWidget(self.lbl_call)
        sc_layout.addWidget(self.lbl_call_desc)
        layout.addWidget(self.status_card)

        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("Meeting Title")
        layout.addWidget(self.txt_title)

        mode_lbl = QLabel("Capture Mode:")
        mode_lbl.setFont(get_ui_font(12, 600))
        layout.addWidget(mode_lbl)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)

        self.btn_mode_audio = QPushButton("Audio Only\nMic + system call audio")
        self.btn_mode_audio.setCheckable(True)
        self.btn_mode_audio.setMinimumHeight(54)
        self.btn_mode_audio.clicked.connect(lambda: self.select_recording_mode("audio_only"))
        mode_row.addWidget(self.btn_mode_audio, 1)

        self.btn_mode_video = QPushButton("Audio + Call Video\nMic + audio + call window")
        self.btn_mode_video.setCheckable(True)
        self.btn_mode_video.setMinimumHeight(54)
        self.btn_mode_video.clicked.connect(lambda: self.select_recording_mode("audio_video"))
        mode_row.addWidget(self.btn_mode_video, 1)
        layout.addLayout(mode_row)

        self.chk_screen = QCheckBox("Record Call Window (Call-Only Privacy Guard)")
        self.chk_screen.toggled.connect(self._on_chk_screen_toggled)
        self.chk_screen.hide()
        layout.addWidget(self.chk_screen)

        self.fps_box = QWidget()
        fps_layout = QVBoxLayout(self.fps_box)
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(4)
        fps_lbl = QLabel("Call Video Framerate:")
        fps_lbl.setFont(get_ui_font(11.5, 600))
        fps_layout.addWidget(fps_lbl)
        from voxlocal.video.screen_recorder import get_system_fps_options
        self.combo_fps = QComboBox()
        sel_idx = 0
        for idx, (label, val) in enumerate(get_system_fps_options()):
            self.combo_fps.addItem(label, val)
            if val == default_fps:
                sel_idx = idx
        self.combo_fps.setCurrentIndex(sel_idx)
        fps_layout.addWidget(self.combo_fps)
        layout.addWidget(self.fps_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_studio = QPushButton("Open Notetaker")
        self.btn_studio.clicked.connect(self._on_click_open_studio)
        btn_row.addWidget(self.btn_studio)
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("cancelBtn")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_start = QPushButton("Start Notetaker")
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

        self.refresh_detection()

    def _discover_active_call(self) -> Optional[Tuple[int, str, str, Tuple[int, int, int, int]]]:
        from voxlocal.audio.call_detector import list_all_meeting_windows
        detected_call = None
        if self.controller and hasattr(self.controller, "call_detector") and self.controller.call_detector:
            sess = getattr(self.controller.call_detector, "active_session", None)
            if sess and getattr(sess, "is_active", True):
                detected_call = (sess.hwnd or 0, sess.platform, sess.clean_title, getattr(sess, "bounds", (0, 0, 1920, 1080)))
        if not detected_call:
            wins = list_all_meeting_windows()
            detected_call = wins[0] if wins else None
        return detected_call

    def refresh_detection(self) -> None:
        from voxlocal.audio.call_detector import clean_meeting_title
        t = self._tokens
        self.detected_call = self._discover_active_call()
        now_str = datetime.now().strftime("%d %b, %I:%M %p")

        if self.detected_call:
            _hwnd, platform, w_title, bounds = self.detected_call
            clean_title = clean_meeting_title(w_title, platform)
            self._default_title = clean_title
            self.status_card.setStyleSheet(
                f"background: {t['accent_fathom_tint']}; border: 1px solid {t['accent_emerald']}; border-radius: 10px;"
            )
            self.lbl_call.setText(f"Active Call Detected: {platform}")
            self.lbl_call.setStyleSheet(f"font-size: 12.5px; font-weight: 700; color: {t['accent_fathom']}; border: none; background: transparent;")
            self.lbl_call_desc.setText(
                f"Window: \"{clean_title}\" ({bounds[2]}x{bounds[3]}). Only the call window is captured; full desktop stays private."
            )
            self.lbl_call_desc.setStyleSheet(f"font-size: 11.5px; color: {t['text_muted']}; border: none; background: transparent;")
            self.txt_title.setText(self._default_title)
            self.txt_title.selectAll()
            self.btn_studio.setVisible(False)
            self.select_recording_mode("audio_video" if self._default_screen_rec else "audio_only")
        else:
            self._default_title = f"Meeting {now_str}"
            self.status_card.setStyleSheet(
                f"background: {t['surface_well']}; border: 1px solid {t['border']}; border-radius: 10px;"
            )
            self.lbl_call.setText("Audio / Call Meeting Notes")
            self.lbl_call.setStyleSheet(f"font-size: 12.5px; font-weight: 700; color: {t['text']}; border: none; background: transparent;")
            self.lbl_call_desc.setText(
                "No active call window locked yet. Choose Audio Only for voice notes or Audio + Video to bind automatically when a call appears."
            )
            self.lbl_call_desc.setStyleSheet(f"font-size: 11.5px; color: {t['text_muted']}; border: none; background: transparent;")
            self.txt_title.setText(self._default_title)
            self.txt_title.selectAll()
            self.btn_studio.setVisible(self.on_open_studio is not None)
            rec_mode = "audio_only" if self.on_open_studio is not None else ("audio_video" if self._default_screen_rec else "audio_only")
            self.select_recording_mode(rec_mode)

    def select_recording_mode(self, mode: str) -> None:
        self._selected_mode = mode
        is_video = mode == "audio_video"
        self.btn_mode_audio.setChecked(not is_video)
        self.btn_mode_video.setChecked(is_video)
        t = self._tokens
        active_style = (
            f"background: {t['accent_fathom_tint']}; border: 2px solid {t['accent_emerald']}; "
            f"border-radius: 10px; font-size: 12px; font-weight: 650; color: {t['text']}; padding: 6px;"
        )
        inactive_style = (
            f"background: {t['surface']}; border: 1px solid {t['border']}; "
            f"border-radius: 10px; font-size: 12px; font-weight: 550; color: {t['text_muted']}; padding: 6px;"
        )
        self.btn_mode_audio.setStyleSheet(active_style if not is_video else inactive_style)
        self.btn_mode_video.setStyleSheet(active_style if is_video else inactive_style)
        self.chk_screen.blockSignals(True)
        self.chk_screen.setChecked(is_video)
        self.chk_screen.blockSignals(False)
        self.fps_box.setVisible(is_video)

    def _on_chk_screen_toggled(self, checked: bool) -> None:
        self.select_recording_mode("audio_video" if checked else "audio_only")

    def _on_click_open_studio(self) -> None:
        self.reject()
        if self.on_open_studio:
            self.on_open_studio()

    @property
    def recording_mode(self) -> str:
        return "audio_video" if self.chk_screen.isChecked() else "audio_only"

    @property
    def target_hwnd(self) -> Optional[int]:
        return self.detected_call[0] if (self.detected_call and self.detected_call[0] > 0) else None

    @property
    def meeting_title(self) -> str:
        return self.txt_title.text().strip()

    @property
    def record_screen(self) -> bool:
        return self.chk_screen.isChecked()

    @property
    def screen_fps(self) -> int:
        return int(self.combo_fps.currentData() or 15)

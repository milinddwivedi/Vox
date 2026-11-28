"""
Read AI-Style Meeting Call Consent Dialog for VoxLocal.
Appears when Zoom, Microsoft Teams, Google Meet, or Webex starts.
Ensures meeting recording is 100% user-controlled and explicit.
Allows one-click recording of dual-track audio (both parties) + optional 1080p screen capture.
"""

from __future__ import annotations

from typing import Callable, Optional
from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.controller import AppController
from voxlocal.ui.animations import AnimatedPushButton
from voxlocal.ui.theme import get_theme_tokens
from voxlocal.win32.window_focus import apply_non_activating_floating_style


class CallConsentDialog(QDialog):
    """
    Polished, non-modal call prompt that asks user consent before recording.
    Never steals focus from the meeting application.
    """

    def __init__(
        self,
        app_name: str,
        suggested_title: str,
        controller: AppController,
        window_title: str = "",
        window_hwnd: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.app_name = app_name
        self.suggested_title = suggested_title
        self.controller = controller

        # Auto-resolve window if not provided directly
        if (window_hwnd is None or not window_title) and hasattr(controller, "call_detector") and controller.call_detector and controller.call_detector.active:
            self.window_hwnd = controller.call_detector.active.window_hwnd
            self.window_title = controller.call_detector.active.window_title or window_title
        else:
            self.window_hwnd = window_hwnd
            self.window_title = window_title

        self.rb_window: Optional[QRadioButton] = None
        self.rb_fullscreen: Optional[QRadioButton] = None

        # Non-activating, always-on-top tool window
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

        # Auto-dismiss after 35 seconds of inactivity so it doesn't linger forever
        self._auto_dismiss_timer = QTimer(self)
        self._auto_dismiss_timer.setSingleShot(True)
        self._auto_dismiss_timer.setInterval(35000)
        self._auto_dismiss_timer.timeout.connect(self._on_dismiss)
        self._auto_dismiss_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        self.controller.register_ui_hwnd(hwnd)
        apply_non_activating_floating_style(hwnd)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        t = get_theme_tokens(self.controller.theme)
        is_dark = self.controller.theme == "dark"

        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background-color: {t['surface']};
                border: 1px solid {t['accent_gold'] if is_dark else t['accent_sky']};
                border-radius: 14px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {t['text']};
            }}
            QCheckBox, QRadioButton {{
                color: {t['text']};
                font-size: 11px;
                border: none;
                background: transparent;
            }}
            QPushButton {{
                border-radius: 8px;
                font-size: 11px;
                font-weight: 700;
                padding: 6px 14px;
            }}
            """
        )
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(8)

        # Header
        top_row = QHBoxLayout()
        icon_lbl = QLabel("🎙️")
        icon_lbl.setStyleSheet("font-size: 18px;")
        top_row.addWidget(icon_lbl)

        title_lbl = QLabel(f"Meeting Detected: {self.app_name}")
        title_lbl.setStyleSheet(f"color: {t['text']}; font-size: 13px; font-weight: 700;")
        top_row.addWidget(title_lbl, 1)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setStyleSheet(
            f"background: transparent; color: {t['text_muted']}; border: none; font-size: 12px; font-weight: bold;"
        )
        btn_close.clicked.connect(self._on_dismiss)
        top_row.addWidget(btn_close)
        c_layout.addLayout(top_row)

        desc_lbl = QLabel(
            "Configure recording options for the detected meeting:"
        )
        desc_lbl.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px; line-height: 1.3;")
        desc_lbl.setWordWrap(True)
        c_layout.addWidget(desc_lbl)

        # 1. Meeting Title Configuration
        lbl_title = QLabel("Meeting Title:")
        lbl_title.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; font-weight: 600;")
        c_layout.addWidget(lbl_title)

        self.txt_title = QLineEdit()
        self.txt_title.setText(self.suggested_title)
        self.txt_title.setStyleSheet(
            f"background: {t.get('surface_subtle', t['surface'])}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 4px 8px; font-size: 11px;"
        )
        c_layout.addWidget(self.txt_title)

        # 2. Call Interface Lock Badge (Record ONLY meeting interface, not desktop)
        win_display = self.window_title if self.window_title else f"{self.app_name} Call Window"
        if len(win_display) > 38:
            win_display = win_display[:35] + "..."

        target_box = QFrame()
        target_box.setStyleSheet(f"background: {t.get('surface_subtle', t['surface'])}; border: 1px solid {t['line']}; border-radius: 8px;")
        tb_layout = QVBoxLayout(target_box)
        tb_layout.setContentsMargins(8, 6, 8, 6)
        tb_layout.setSpacing(3)

        lbl_target = QLabel(f"🪟 Call Interface: <b>{win_display}</b>")
        lbl_target.setStyleSheet(f"color: {t['text']}; font-size: 11px;")
        tb_layout.addWidget(lbl_target)

        lbl_target_scope = QLabel("✓ Records active speaker, video feeds & shared screens.<br>✗ Excludes desktop and unrelated applications.")
        lbl_target_scope.setStyleSheet(f"color: {t.get('accent_emerald', '#10B981')}; font-size: 10px; line-height: 1.2;")
        tb_layout.addWidget(lbl_target_scope)
        c_layout.addWidget(target_box)

        scope_choice = QHBoxLayout()
        scope_choice.setSpacing(12)
        self.rb_call_window = QRadioButton("🪟 Call Window Only (Video + Audio)")
        self.rb_call_window.setChecked(True)
        self.rb_call_window.setStyleSheet(f"color: {t['text']}; font-size: 11px;")
        self.rb_audio_only = QRadioButton("🎙️ Meeting Audio Only")
        self.rb_audio_only.setStyleSheet(f"color: {t['text']}; font-size: 11px;")
        scope_choice.addWidget(self.rb_call_window)
        scope_choice.addWidget(self.rb_audio_only)
        scope_choice.addStretch()
        c_layout.addLayout(scope_choice)

        # 3. Resolution & Framerate (FPS) Grid
        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)

        # Resolution
        res_col = QVBoxLayout()
        res_col.setSpacing(2)
        lbl_res = QLabel("Resolution:")
        lbl_res.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; font-weight: 600;")
        res_col.addWidget(lbl_res)

        self.combo_res = QComboBox()
        self.combo_res.addItem("1080p (Full HD)", "1080p")
        self.combo_res.addItem("720p (HD)", "720p")
        self.combo_res.addItem("Native Size", "native")
        self.combo_res.setStyleSheet(
            f"background: {t.get('surface_subtle', t['surface'])}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 3px 6px; font-size: 11px;"
        )
        res_col.addWidget(self.combo_res)
        opt_row.addLayout(res_col, 1)

        # FPS
        fps_col = QVBoxLayout()
        fps_col.setSpacing(2)
        lbl_fps = QLabel("Framerate (FPS):")
        lbl_fps.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; font-weight: 600;")
        fps_col.addWidget(lbl_fps)

        self.combo_fps = QComboBox()
        self.combo_fps.addItem("15 FPS (Recommended)", 15)
        self.combo_fps.addItem("30 FPS (Smooth)", 30)
        self.combo_fps.addItem("60 FPS", 60)
        self.combo_fps.setStyleSheet(
            f"background: {t.get('surface_subtle', t['surface'])}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 3px 6px; font-size: 11px;"
        )
        fps_col.addWidget(self.combo_fps)
        opt_row.addLayout(fps_col, 1)

        c_layout.addLayout(opt_row)

        # 4. Audio Capture Mode
        aud_row = QVBoxLayout()
        aud_row.setSpacing(2)
        lbl_aud = QLabel("Meeting Audio:")
        lbl_aud.setStyleSheet(f"color: {t['text_muted']}; font-size: 10px; font-weight: 600;")
        aud_row.addWidget(lbl_aud)

        self.combo_audio = QComboBox()
        self.combo_audio.addItem("🎙️+🔊 Dual-Track: Mic + Meeting Attendees", "both")
        self.combo_audio.addItem("🔊 Attendees Only (System Audio)", "system_only")
        self.combo_audio.addItem("🎙️ Local Microphone Only", "mic_only")
        self.combo_audio.setStyleSheet(
            f"background: {t.get('surface_subtle', t['surface'])}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 3px 6px; font-size: 11px;"
        )
        aud_row.addWidget(self.combo_audio)
        c_layout.addLayout(aud_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_record = AnimatedPushButton("⏺ Record Meeting")
        self.btn_record.setStyleSheet(
            f"background-color: {t['accent_crimson']}; color: white; border: none; font-weight: 700;"
        )
        self.btn_record.clicked.connect(self._on_record_clicked)
        btn_row.addWidget(self.btn_record)

        self.btn_dismiss = AnimatedPushButton("Dismiss")
        self.btn_dismiss.setStyleSheet(
            f"background-color: {t['secondary_btn_bg']}; color: {t['text']}; border: 1px solid {t['line']};"
        )
        self.btn_dismiss.clicked.connect(self._on_dismiss)
        btn_row.addWidget(self.btn_dismiss)

        c_layout.addLayout(btn_row)
        outer.addWidget(card)
        self.adjustSize()

    def _on_record_clicked(self) -> None:
        self._auto_dismiss_timer.stop()
        rec_title = self.txt_title.text().strip() or self.suggested_title
        rec_res = str(self.combo_res.currentData() or "1080p")
        rec_fps = int(self.combo_fps.currentData() or 15)
        rec_audio = str(self.combo_audio.currentData() or "both")

        record_call_video = bool(
            not hasattr(self, "rb_audio_only") or not self.rb_audio_only.isChecked()
        )
        self.controller.config.record_meeting_screen = record_call_video
        self.controller.config.screen_fps = rec_fps
        self.controller.config.meeting_capture_mode = rec_audio
        self.controller.config.save()

        self.controller.start_meeting_recording(
            title=rec_title,
            capture_mode=rec_audio,
            record_screen=record_call_video,
            screen_fps=rec_fps,
            target_hwnd=self.window_hwnd,
            resolution=rec_res,
        )
        self.accept()

    def _on_dismiss(self) -> None:
        self._auto_dismiss_timer.stop()
        self.controller.dismiss_call_prompt()
        self.reject()

"""VOX Settings Workspace & Shortcut Capture Dialog (Sand Light / Vast Dark)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.ui.components import VoxAudioVisualizerBar, VoxToggleSwitch
from voxlocal.ui.theme import (
    MARGINS_CARD,
    SPACING_CARD_ITEMS_PX,
    get_theme_tokens,
    get_ui_font,
)

COMPUTE_LABEL_TO_VALUE: Dict[str, str] = {
    "Fastest (Lower Memory)": "int8",
    "Balanced (Recommended)": "int8_float16",
    "Balanced": "default",
    "Maximum Accuracy": "float16",
    "Automatic": "default",
}

COMPUTE_VALUE_TO_LABEL: Dict[str, str] = {
    "int8": "Fastest (Lower Memory)",
    "int8_float16": "Balanced (Recommended)",
    "default": "Balanced",
    "float16": "Maximum Accuracy",
    "float32": "Maximum Accuracy",
}

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController

logger = get_logger("ui.settings")


class ShortcutCaptureDialog(QDialog):
    """Interactive modal dialog for capturing and validating global keyboard shortcuts."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        current_shortcut: str = "",
        title: str = "Record Shortcut",
        theme: str = "light",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(440, 260)
        self.setModal(True)
        self._final_shortcut: str = current_shortcut or ""
        self._theme = "dark" if (theme or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)

        self.setStyleSheet(
            f"QDialog {{ background-color: {t['surface']}; color: {t['text']}; }}"
            f"QLabel {{ color: {t['text']}; background: transparent; border: none; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header_lbl = QLabel(title)
        header_lbl.setFont(get_ui_font(15, 700))
        layout.addWidget(header_lbl)

        instructions = QLabel(
            "Press any key on your keyboard (e.g. F8, F9, CapsLock, Right Alt, Ctrl + Win) or choose a preset below.\n"
            "💡 Note: On PC laptops, the standalone 'Fn' key is handled by BIOS hardware and does not emit a Windows key. "
            "Use F8, F9, CapsLock, or Right Alt for reliable one-touch voice typing."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet(f"font-size: 11px; color: {t['text_muted']}; line-height: 1.4;")
        layout.addWidget(instructions)

        self.display_chip = QLabel(
            self._final_shortcut.upper().replace("+", " + ") if self._final_shortcut else "Press keys..."
        )
        self.display_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_chip.setAccessibleName("Recorded shortcut key display")
        self.display_chip.setAccessibleDescription("Shows the currently detected key combination or prompts to press keys")
        self.display_chip.setStyleSheet(
            f"background: {t['surface_well']}; border: 1.5px solid {t['accent_fathom']}; "
            f"border-radius: 10px; padding: 12px; font-size: 14px; font-weight: 700; "
            f"color: {t['text']}; letter-spacing: 0.6px;"
        )
        layout.addWidget(self.display_chip)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for label, combo in (
            ("F8 (Recommended)", "f8"),
            ("F9", "f9"),
            ("Caps Lock", "capslock"),
            ("Right Alt", "ralt"),
            ("Right Ctrl", "rctrl"),
            ("Ctrl + Win", "ctrl+win"),
        ):
            pb = QPushButton(label)
            pb.setFixedHeight(24)
            pb.setCursor(Qt.CursorShape.PointingHandCursor)
            pb.setAccessibleName(f"Preset shortcut {label}")
            pb.setAccessibleDescription(f"Set hotkey to {label}")
            pb.setStyleSheet(
                f"QPushButton {{ background: {t['chip_bg']}; color: {t['text']}; "
                f"border: 1px solid {t['border']}; border-radius: 6px; padding: 0 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ background: {t['sidebar_active']}; }}"
            )
            pb.clicked.connect(lambda _=False, c=combo: self._set_candidate(c))
            preset_row.addWidget(pb)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setStyleSheet(f"font-size: 11px; color: {t['accent_crimson']}; font-weight: 600;")
        layout.addWidget(self.lbl_feedback)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setAccessibleName("Clear shortcut")
        self.btn_clear.setAccessibleDescription("Reset and remove the current key binding")
        self.btn_clear.setStyleSheet(
            f"QPushButton {{ background: {t['secondary_btn_bg']}; color: {t['text']}; "
            f"border: 1px solid {t['border']}; border-radius: 8px; padding: 6px 12px; }}"
        )
        self.btn_clear.clicked.connect(self._on_clear)
        btn_row.addWidget(self.btn_clear)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAccessibleName("Cancel shortcut capture")
        self.btn_cancel.setAccessibleDescription("Close dialog without saving shortcut changes")
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {t['text_muted']}; border: none; padding: 6px 10px; }}"
        )
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save Shortcut")
        self.btn_save.setAccessibleName("Save shortcut")
        self.btn_save.setAccessibleDescription("Save and apply the recorded key combination")
        self.btn_save.setStyleSheet(
            f"QPushButton {{ background: {t['primary_btn_bg']}; color: {t['primary_btn_text']}; "
            f"font-weight: 700; border-radius: 8px; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {t['primary_btn_hover']}; }}"
        )
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def _set_candidate(self, candidate: str) -> None:
        t = get_theme_tokens(self._theme)
        self._final_shortcut = candidate
        self.lbl_feedback.setText("")
        self.display_chip.setText(candidate.upper().replace("+", " + "))
        self.display_chip.setStyleSheet(
            f"background: {t['accent_fathom_tint']}; border: 1.5px solid {t['accent_fathom']}; "
            f"border-radius: 10px; padding: 12px; font-size: 14px; font-weight: 700; color: {t['text']};"
        )

    def _on_clear(self) -> None:
        t = get_theme_tokens(self._theme)
        self._final_shortcut = ""
        self.display_chip.setText("Press keys...")
        self.display_chip.setStyleSheet(
            f"background: {t['surface_well']}; border: 1.5px solid {t['border']}; "
            f"border-radius: 10px; padding: 12px; font-size: 14px; font-weight: 700; color: {t['text']};"
        )
        self.lbl_feedback.setText("")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Escape and not modifiers:
            self.reject()
            event.accept()
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            event.accept()
            return

        parts: List[str] = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")

        key_name = QKeySequence(key).toString().lower()
        if not key_name or key_name in ("ctrl", "alt", "shift", "meta"):
            if key == Qt.Key.Key_Space:
                key_name = "space"
            elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
                key_name = f"f{key - Qt.Key.Key_F1 + 1}"
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                key_name = "enter"
            elif key == Qt.Key.Key_Tab:
                key_name = "tab"
            elif key == Qt.Key.Key_Escape:
                key_name = "esc"

        if key_name and key_name not in parts:
            parts.append(key_name)
        if not parts:
            event.accept()
            return

        candidate = "+".join(parts)
        prohibited = {"ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "alt+tab", "ctrl+alt+del", "win+l", "alt+f4"}
        if candidate in prohibited:
            t = get_theme_tokens(self._theme)
            self.lbl_feedback.setText(f"'{candidate.upper()}' is a reserved Windows shortcut.")
            self.display_chip.setStyleSheet(
                f"background: {t['surface_well']}; border: 1.5px solid {t['accent_crimson']}; "
                f"border-radius: 10px; padding: 12px; font-size: 14px; font-weight: 700; color: {t['text']};"
            )
            return

        from voxlocal.win32.hotkeys import parse_hotkey_string
        if not parse_hotkey_string(candidate):
            self.lbl_feedback.setText(f"Unable to bind shortcut: {candidate}")
            return

        self._set_candidate(candidate)
        event.accept()

    def _on_save(self) -> None:
        if not self._final_shortcut:
            self.lbl_feedback.setText("Please press a valid key combination first.")
            return
        self.accept()

    def get_shortcut(self) -> str:
        return self._final_shortcut


class SettingsWorkspaceWidget(QWidget):
    """Wispr Flow-inspired two-group Settings workspace backed directly by AppConfig & DB."""

    replay_tour_requested = pyqtSignal()
    status_toast = pyqtSignal(str, str)
    mic_level_updated = pyqtSignal(float)
    open_calendar_requested = pyqtSignal()

    SECTIONS = [
        ("General", "Hotkeys, microphone & local AI engine", "group1"),
        ("System", "Appearance, Flow Bar docking & audio cues", "group1"),
        ("Notetaker", "Call detection, video capture & defaults", "group1"),
        ("Vibe coding", "Developer identifiers & file tagging", "group1"),
        ("Account & Calendar", "Microsoft 365 calendar sync & profile", "group2"),
        ("Data and Privacy", "Retention, export & selective cleanup", "group2"),
    ]

    def __init__(
        self,
        controller: AppController,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._nav_buttons: List[QPushButton] = []
        self._toggles: List[VoxToggleSwitch] = []
        self._hotkey_buttons: Dict[str, QPushButton] = {}
        self._calendar_poll_timer: Optional[QTimer] = None
        self._mic_probe = None

        self._build_ui()
        self.mic_level_updated.connect(lambda lvl: self.mic_level_bar.set_level(lvl) if hasattr(self, "mic_level_bar") else None)
        if hasattr(self.controller, "auth_state_changed"):
            self.controller.auth_state_changed.connect(lambda _: self._update_account_ui())
        if hasattr(self.controller, "entitlement_changed"):
            self.controller.entitlement_changed.connect(lambda _: self._update_account_ui())
        self.load_from_config()
        self._update_account_ui()
        self.apply_theme(self._theme)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left sub-navigation pane
        self.sub_nav = QFrame()
        self.sub_nav.setFixedWidth(210)
        nav_layout = QVBoxLayout(self.sub_nav)
        nav_layout.setContentsMargins(16, 22, 12, 22)
        nav_layout.setSpacing(4)

        lbl_settings_hdr = QLabel("Settings")
        lbl_settings_hdr.setFont(get_ui_font(22, 500, serif=True))
        nav_layout.addWidget(lbl_settings_hdr)
        nav_layout.addSpacing(8)

        self.lbl_group1 = QLabel("WORKSPACE")
        self.lbl_group1.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.08em;")
        nav_layout.addWidget(self.lbl_group1)

        added_g2 = False
        for idx, (title, _desc, grp) in enumerate(self.SECTIONS):
            if grp == "group2" and not added_g2:
                added_g2 = True
                nav_layout.addSpacing(12)
                self.lbl_group2 = QLabel("ACCOUNT & PRIVACY")
                self.lbl_group2.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.08em;")
                nav_layout.addWidget(self.lbl_group2)

            btn = QPushButton(title.replace("&", "&&"))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setAccessibleName(f"{title} settings section")
            btn.setAccessibleDescription(_desc)
            btn.clicked.connect(lambda _=False, i=idx: self.select_section(i))
            self._nav_buttons.append(btn)
            nav_layout.addWidget(btn)

        nav_layout.addStretch(1)
        root.addWidget(self.sub_nav)

        # Right content stack inside scroll area
        self.stack = QStackedWidget()
        self.stack.addWidget(self._scroll_wrap(self._build_general_page()))
        self.stack.addWidget(self._scroll_wrap(self._build_system_page()))
        self.stack.addWidget(self._scroll_wrap(self._build_notetaker_page()))
        self.stack.addWidget(self._scroll_wrap(self._build_vibe_page()))
        self.stack.addWidget(self._scroll_wrap(self._build_account_page()))
        self.stack.addWidget(self._scroll_wrap(self._build_privacy_page()))
        root.addWidget(self.stack, 1)

        self.select_section(0)

    def _scroll_wrap(self, inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(inner)
        return sc

    def select_section(self, idx: int) -> None:
        idx = max(0, min(idx, self.stack.count() - 1))
        if idx != 0:
            self._stop_mic_probe()
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == idx)
        if idx == 4:
            self._refresh_calendar_status()

    # ------------------------------------------------------------------
    # Row builders
    # ------------------------------------------------------------------
    def _page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("settingsScrollBody")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)

        lbl_t = QLabel(title)
        lbl_t.setFont(get_ui_font(24, 500, serif=True))
        lay.addWidget(lbl_t)

        lbl_s = QLabel(subtitle)
        lbl_s.setObjectName("settingsMuted")
        lbl_s.setWordWrap(True)
        lay.addWidget(lbl_s)
        return w, lay

    def _make_card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("settingsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(*MARGINS_CARD)
        lay.setSpacing(SPACING_CARD_ITEMS_PX)
        return card, lay

    def _add_toggle_row(
        self,
        parent_lay: QVBoxLayout,
        title: str,
        desc: str,
        on_toggle: Callable[[bool], None],
    ) -> VoxToggleSwitch:
        row = QHBoxLayout()
        row.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("font-size: 13px; font-weight: 600;")
        lbl_d = QLabel(desc)
        lbl_d.setObjectName("settingsMuted")
        lbl_d.setWordWrap(True)
        col.addWidget(lbl_t)
        col.addWidget(lbl_d)
        row.addLayout(col, 1)

        sw = VoxToggleSwitch(theme=self._theme)
        sw.setAccessibleName(f"{title} toggle")
        sw.setAccessibleDescription(desc)
        sw.toggled.connect(on_toggle)
        self._toggles.append(sw)
        row.addWidget(sw)
        parent_lay.addLayout(row)
        return sw

    def _add_control_row(
        self,
        parent_lay: QVBoxLayout,
        title: str,
        desc: str,
        control: QWidget,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        text_wrap = QWidget()
        text_wrap.setStyleSheet("background: transparent;")
        text_wrap.setMinimumWidth(220)
        col = QVBoxLayout(text_wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("font-size: 13px; font-weight: 600;")
        lbl_d = QLabel(desc)
        lbl_d.setObjectName("settingsMuted")
        lbl_d.setWordWrap(True)
        col.addWidget(lbl_t)
        col.addWidget(lbl_d)
        row.addWidget(text_wrap, 1)
        control.setAccessibleName(title)
        control.setAccessibleDescription(desc)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        parent_lay.addLayout(row)

    def _add_hotkey_row(
        self,
        parent_lay: QVBoxLayout,
        field_name: str,
        title: str,
        desc: str,
    ) -> QPushButton:
        btn = QPushButton("...")
        btn.setObjectName("keycapBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(148)
        btn.setAccessibleName(f"{title} hotkey")
        btn.setAccessibleDescription(f"Click to record a new key combination for {title}")
        btn.clicked.connect(lambda _=False, f=field_name, t=title: self._edit_hotkey(f, t))
        self._hotkey_buttons[field_name] = btn
        self._add_control_row(parent_lay, title, desc, btn)
        return btn

    # ------------------------------------------------------------------
    # Sub-pages
    # ------------------------------------------------------------------
    def _build_general_page(self) -> QWidget:
        page, lay = self._page_shell("General", "Configure hotkeys, microphone hardware, and local speech models.")

        # Hotkeys card
        card_hk, hk_lay = self._make_card()
        self.combo_dict_mode = QComboBox()
        self.combo_dict_mode.setMinimumWidth(260)
        self.combo_dict_mode.setMaximumWidth(320)
        self.combo_dict_mode.setAccessibleName("Dictation trigger style")
        self.combo_dict_mode.addItem("Hold to Speak (Hold key down, release to insert)", "push_to_talk")
        self.combo_dict_mode.addItem("Click to Toggle (Click once to speak, click again to stop)", "toggle")
        self.combo_dict_mode.addItem("Hybrid (Hold to speak or tap to toggle)", "hybrid")
        self.combo_dict_mode.currentIndexChanged.connect(self._on_dict_mode_changed)
        self._add_control_row(
            hk_lay,
            "Dictation Trigger Style",
            "Choose whether your dictation key holds to speak or switches on/off with a click.",
            self.combo_dict_mode,
        )
        self._add_hotkey_row(hk_lay, "ptt_hotkey", "Push-to-Talk / Dictation Key", "Primary key to hold or toggle speech dictation.")
        self.sw_hands_free_dt = self._add_toggle_row(
            hk_lay,
            "Double-tap for Hands-Free",
            "Quickly double-tap your dictation key to lock listening without holding.",
            lambda v: self._save_cfg("hands_free_double_tap", v, apply_hotkeys=True),
        )
        self.sw_notetaker_tt = self._add_toggle_row(
            hk_lay,
            "Triple-tap to start Notetaker",
            "Triple-tap your dictation key to start or stop meeting note capture.",
            lambda v: self._save_cfg("notetaker_triple_tap", v, apply_hotkeys=True),
        )
        self._add_hotkey_row(hk_lay, "typing_hotkey", "Toggle Dictation Hotkey", "Start or stop hands-free voice typing.")
        self._add_hotkey_row(hk_lay, "scratchpad_hotkey", "Open Scratchpad", "Toggle floating note pad from any app.")
        self._add_hotkey_row(hk_lay, "paste_last_hotkey", "Paste Last Dictation", "Re-insert your most recent dictation at cursor.")
        self._add_hotkey_row(hk_lay, "copy_last_hotkey", "Copy Last Dictation", "Copy your most recent dictation to clipboard.")
        lay.addWidget(card_hk)

        # Microphone & Model card
        card_mic, mic_lay = self._make_card()
        mic_wrap = QWidget()
        mic_wrap.setStyleSheet("background: transparent;")
        mic_box = QHBoxLayout(mic_wrap)
        mic_box.setContentsMargins(0, 0, 0, 0)
        mic_box.setSpacing(6)
        self.combo_mic = QComboBox()
        self.combo_mic.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_mic.setMinimumWidth(210)
        self.combo_mic.setMaximumWidth(280)
        self.combo_mic.setAccessibleName("Microphone input device")
        self.combo_mic.setAccessibleDescription("Select primary audio input device for dictation and meeting recording")
        self.combo_mic.currentIndexChanged.connect(self._on_mic_selected)
        btn_refresh_mic = QPushButton("Refresh")
        btn_refresh_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh_mic.setAccessibleName("Refresh microphones")
        btn_refresh_mic.setAccessibleDescription("Scan and reload available system audio input devices")
        btn_refresh_mic.clicked.connect(self._populate_mics)
        mic_box.addWidget(self.combo_mic)
        mic_box.addWidget(btn_refresh_mic)
        self._add_control_row(mic_lay, "Microphone", "Primary input device for dictation and calls.", mic_wrap)

        # Live microphone deflection level visualizer row
        level_wrap = QWidget()
        level_wrap.setStyleSheet("background: transparent;")
        level_box = QHBoxLayout(level_wrap)
        level_box.setContentsMargins(0, 0, 0, 0)
        level_box.setSpacing(8)

        self.mic_level_bar = VoxAudioVisualizerBar(parent=level_wrap)
        self.mic_level_bar.setFixedHeight(12)
        self.mic_level_bar.setMinimumWidth(160)
        self.mic_level_bar.setAccessibleName("Microphone input level meter")
        self.mic_level_bar.setAccessibleDescription("Live visual volume meter reflecting audio input from the selected microphone")

        self.btn_test_mic = QPushButton("Test Mic")
        self.btn_test_mic.setCheckable(True)
        self.btn_test_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test_mic.setAccessibleName("Test microphone")
        self.btn_test_mic.setAccessibleDescription("Start or stop real-time microphone level meter testing")
        self.btn_test_mic.clicked.connect(self._toggle_mic_test)

        level_box.addWidget(self.mic_level_bar, 1)
        level_box.addWidget(self.btn_test_mic)
        self._add_control_row(mic_lay, "Microphone Input Level", "Speak into your microphone to verify audio deflection.", level_wrap)

        gain_wrap = QWidget()
        gain_wrap.setStyleSheet("background: transparent;")
        gain_box = QHBoxLayout(gain_wrap)
        gain_box.setContentsMargins(0, 0, 0, 0)
        self.slider_mic_gain = QSlider(Qt.Orientation.Horizontal)
        self.slider_mic_gain.setRange(50, 250)
        self.slider_mic_gain.setFixedWidth(160)
        self.slider_mic_gain.setAccessibleName("Microphone gain slider")
        self.slider_mic_gain.setAccessibleDescription("Adjust digital microphone boost from 0.5x to 2.5x")
        self.lbl_mic_gain = QLabel("1.0x")
        self.slider_mic_gain.valueChanged.connect(self._on_mic_gain_changed)
        gain_box.addWidget(self.slider_mic_gain)
        gain_box.addWidget(self.lbl_mic_gain)
        self._add_control_row(mic_lay, "Microphone Gain", "Boost quiet headset or laptop microphones.", gain_wrap)

        self.combo_lang = QComboBox()
        self.combo_lang.setMinimumWidth(240)
        self.combo_lang.setMaximumWidth(300)
        self.combo_lang.setAccessibleName("Recognition language")
        self.combo_lang.setAccessibleDescription("Select spoken dictation language: English + Hindi, English only, or Hindi only")
        self.combo_lang.addItem("English + Hindi (Bilingual / Hinglish)", "en+hi")
        self.combo_lang.addItem("English only", "en")
        self.combo_lang.addItem("Hindi only", "hi")
        self.combo_lang.currentIndexChanged.connect(self._on_language_changed)
        self._add_control_row(mic_lay, "Languages", "Select recognized dictation languages.", self.combo_lang)

        self.sw_hinglish = self._add_toggle_row(
            mic_lay,
            "Hinglish Smart Normalization",
            "Keep Romanized Hinglish phrasing crisp and readable automatically.",
            lambda v: self._save_cfg("hinglish_enabled", v),
        )

        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(260)
        self.combo_model.setMaximumWidth(320)
        self.combo_model.setAccessibleName("Speech transcription model")
        self.combo_model.setAccessibleDescription("Select local Whisper model for transcription speed versus accuracy")
        for mdl_lbl, mdl in (
            ("small (Recommended · Fast EN/HI ~0.5s)", "small"),
            ("base.en (Ultra-Fast English ~0.2s)", "base.en"),
            ("large-v3-turbo (Deep Multilingual ~1.7s)", "large-v3-turbo"),
            ("large-v3 (Max Accuracy)", "large-v3"),
            ("medium", "medium"),
            ("base", "base"),
            ("tiny", "tiny"),
        ):
            self.combo_model.addItem(mdl_lbl, mdl)
        self.combo_model.currentIndexChanged.connect(self._on_model_changed)
        self._add_control_row(mic_lay, "Speech Model", "All transcription runs locally on your PC.", self.combo_model)

        self.combo_compute = QComboBox()
        self.combo_compute.setMinimumWidth(260)
        self.combo_compute.setMaximumWidth(320)
        self.combo_compute.setAccessibleName("Whisper compute precision")
        self.combo_compute.setAccessibleDescription("Hardware precision for Whisper speech inference: Fastest (int8), Balanced (default), or Maximum Accuracy (float16)")
        for label, val in (
            ("Fastest (Lower Memory)", "int8"),
            ("Balanced (Recommended)", "int8_float16"),
            ("Maximum Accuracy", "float16"),
            ("Automatic", "default"),
        ):
            self.combo_compute.addItem(label, val)
        self.combo_compute.currentIndexChanged.connect(self._on_compute_changed)
        self._add_control_row(mic_lay, "Compute Precision", "Hardware precision for Whisper speech inference.", self.combo_compute)

        self.combo_profile = QComboBox()
        self.combo_profile.setMinimumWidth(220)
        self.combo_profile.setMaximumWidth(260)
        self.combo_profile.setAccessibleName("Speed versus accuracy profile")
        self.combo_profile.setAccessibleDescription("Select performance profile: Fast, Balanced, or Accurate")
        for prof_lbl, prof in (("Fast", "fast"), ("Balanced (Recommended)", "balanced"), ("Accurate", "accurate")):
            self.combo_profile.addItem(prof_lbl, prof)
        self.combo_profile.currentIndexChanged.connect(self._on_profile_changed)
        self._add_control_row(mic_lay, "Speed vs. Accuracy Profile", "Balance dictation latency and accuracy.", self.combo_profile)

        lay.addWidget(card_mic)
        lay.addStretch(1)
        return page

    def _build_system_page(self) -> QWidget:
        page, lay = self._page_shell("System", "Customize VOX appearance, Flow Bar docking, sound cues, and behavior.")

        card_app, app_lay = self._make_card()
        self.combo_theme = QComboBox()
        self.combo_theme.setAccessibleName("Appearance theme")
        self.combo_theme.setAccessibleDescription("Select application color theme: Light Sand, Dark Vast, or Windows System")
        self.combo_theme.addItem("Light (Sand)", "light")
        self.combo_theme.addItem("Dark (Vast)", "dark")
        self.combo_theme.addItem("Match Windows System", "system")
        self.combo_theme.currentIndexChanged.connect(self._on_theme_selected)
        self._add_control_row(app_lay, "Appearance Theme", "Editorial Sand Light or Obsidian Vast Dark.", self.combo_theme)

        self.combo_edge = QComboBox()
        self.combo_edge.setAccessibleName("Flow Bar screen dock edge")
        self.combo_edge.setAccessibleDescription("Pin the floating dictation pill to any screen edge")
        self.combo_edge.addItem("Bottom Edge", "bottom")
        self.combo_edge.addItem("Left Edge", "left")
        self.combo_edge.addItem("Right Edge", "right")
        self.combo_edge.currentIndexChanged.connect(self._on_edge_selected)
        self._add_control_row(app_lay, "Flow Bar Dock Edge", "Pin the floating dictation pill to any screen edge.", self.combo_edge)

        self.sw_show_flow = self._add_toggle_row(
            app_lay,
            "Show Flow Bar",
            "Keep the floating dictation pill accessible on screen.",
            self._on_show_flow_toggled,
        )
        self.sw_hide_screenshare = self._add_toggle_row(
            app_lay,
            "Hide Flow Bar During Screen Share",
            "Exclude VOX floating surfaces from desktop/window screen shares.",
            lambda v: self._save_cfg("hide_in_screen_share", v),
        )
        self.sw_launch_login = self._add_toggle_row(
            app_lay,
            "Launch VOX at Windows Startup",
            "Start quietly in the system tray when you sign in.",
            self._on_startup_toggled,
        )
        lay.addWidget(card_app)

        card_ux, ux_lay = self._make_card()
        self.sw_sounds = self._add_toggle_row(
            ux_lay,
            "Sound Effects",
            "Play subtle warm chimes when dictation starts, stops, or cancels.",
            lambda v: self._save_cfg("sound_effects", v),
        )
        self.sw_mute_sys = self._add_toggle_row(
            ux_lay,
            "Mute System Audio While Dictating",
            "Pause background music/video audio during active dictation.",
            lambda v: self._save_cfg("mute_audio_while_dictating", v),
        )
        self.sw_smart_fmt = self._add_toggle_row(
            ux_lay,
            "Smart Formatting & Punctuation",
            "Automatically insert punctuation, capitalization, and list structure.",
            lambda v: self._save_cfg("smart_formatting", v),
        )
        self.sw_auto_dict = self._add_toggle_row(
            ux_lay,
            "Auto-Learn Dictionary From Corrections",
            "Learn spellings automatically when you edit a Home dictation card.",
            lambda v: self._save_cfg("auto_add_dictionary", v),
        )
        self.sw_ctx = self._add_toggle_row(
            ux_lay,
            "App Context Awareness",
            "Adapt tone and formatting based on the active target application.",
            lambda v: self._save_cfg("context_awareness", v),
        )
        self.sw_email_sig = self._add_toggle_row(
            ux_lay,
            "Email Auto-Signature",
            "Append your custom sign-off when dictating in mail apps.",
            lambda v: self._save_cfg("email_auto_signature", v),
        )
        self.txt_email_sig = QLineEdit()
        self.txt_email_sig.setPlaceholderText("e.g. Best regards, Milind")
        self.txt_email_sig.setAccessibleName("Email auto-signature text")
        self.txt_email_sig.setAccessibleDescription("Enter custom sign-off message automatically appended in mail applications")
        self.txt_email_sig.editingFinished.connect(
            lambda: self._save_cfg("email_signature_text", self.txt_email_sig.text().strip())
        )
        self._add_control_row(ux_lay, "Email Sign-Off Text", "Text appended when Email Auto-Signature is enabled.", self.txt_email_sig)

        btn_tour = QPushButton("Replay Onboarding Tour")
        btn_tour.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_tour.setAccessibleName("Replay guided onboarding tour")
        btn_tour.setAccessibleDescription("Launch interactive setup walkthrough and dictation playground")
        btn_tour.clicked.connect(self.replay_tour_requested.emit)
        self._add_control_row(ux_lay, "Guided Tour & Test", "Re-open the VOX setup walkthrough and playground.", btn_tour)
        lay.addWidget(card_ux)
        lay.addStretch(1)
        return page

    def _build_notetaker_page(self) -> QWidget:
        page, lay = self._page_shell("Notetaker", "Control call detection, call-only video recording, and summary automation.")

        card_nt, nt_lay = self._make_card()
        self.sw_prompt_call = self._add_toggle_row(
            nt_lay,
            "Detect Teams / Zoom / Meet Calls",
            "Show a non-intrusive card when an active call window starts.",
            lambda v: self._save_cfg("prompt_on_call", v),
        )
        self.sw_auto_start_call = self._add_toggle_row(
            nt_lay,
            "Start Notetaker Automatically on Call",
            "Turn on Notetaker as soon as your meeting begins without waiting for confirmation.",
            self._on_auto_start_call_toggled,
        )
        self.combo_rec_mode = QComboBox()
        self.combo_rec_mode.setAccessibleName("Default call capture mode")
        self.combo_rec_mode.setAccessibleDescription("Capture only the meeting window (never your desktop)")
        self.combo_rec_mode.addItem("Audio + Call Window Video", "audio_video")
        self.combo_rec_mode.addItem("Audio Only", "audio_only")
        self.combo_rec_mode.currentIndexChanged.connect(self._on_rec_mode_changed)
        self._add_control_row(nt_lay, "Default Call Capture Mode", "Capture only the meeting window (never your desktop).", self.combo_rec_mode)

        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(5, 30)
        self.spin_fps.setSuffix(" fps")
        self.spin_fps.setAccessibleName("Call video frame rate")
        self.spin_fps.setAccessibleDescription("Frame rate used when recording shared slides/screen")
        self.spin_fps.valueChanged.connect(lambda v: self._save_cfg("screen_fps", int(v)))
        self._add_control_row(nt_lay, "Call Video Frame Rate", "Frame rate used when recording shared slides/screen.", self.spin_fps)

        self.sw_nt_auto_end = self._add_toggle_row(
            nt_lay,
            "Auto-End When Call Closes",
            "Finish and summarize notes automatically when the meeting window closes.",
            lambda v: self._save_cfg("notetaker_auto_end", v),
        )
        self.sw_nt_auto_open = self._add_toggle_row(
            nt_lay,
            "Auto-Open Notes After Call",
            "Open completed summary notes in VOX when call transcription finishes.",
            lambda v: self._save_cfg("notetaker_auto_open_summary", v),
        )
        self.sw_nt_speakers = self._add_toggle_row(
            nt_lay,
            "Automatic Speaker Attribution",
            "Separate voices locally and attribute speakers in transcripts.",
            lambda v: self._save_cfg("notetaker_auto_detect_speakers", v),
        )
        self.sw_nt_live = self._add_toggle_row(
            nt_lay,
            "Live Call Transcript",
            "Stream live captioned lines while a meeting is in progress.",
            lambda v: self._save_cfg("notetaker_live_transcript", v),
        )
        self.combo_drawer_start = QComboBox()
        self.combo_drawer_start.setAccessibleName("Notepad on Call Start")
        self.combo_drawer_start.setAccessibleDescription("Drawer behavior when Notetaker begins recording")
        self.combo_drawer_start.addItem("Open Floating Drawer", "open")
        self.combo_drawer_start.addItem("Show Compact Pill Only", "minimized")
        self.combo_drawer_start.addItem("Keep Closed", "closed")
        self.combo_drawer_start.currentIndexChanged.connect(
            lambda: self._save_cfg("notetaker_notepad_on_start", self.combo_drawer_start.currentData())
        )
        self._add_control_row(nt_lay, "Notepad on Call Start", "Drawer behavior when Notetaker begins recording.", self.combo_drawer_start)

        self.spin_max_len = QSpinBox()
        self.spin_max_len.setRange(15, 360)
        self.spin_max_len.setSingleStep(15)
        self.spin_max_len.setSuffix(" min")
        self.spin_max_len.setAccessibleName("Maximum Recording Length")
        self.spin_max_len.setAccessibleDescription("Safety cap per single call recording")
        self.spin_max_len.valueChanged.connect(lambda v: self._save_cfg("notetaker_max_length_min", int(v)))
        self._add_control_row(nt_lay, "Maximum Recording Length", "Safety cap per single call recording.", self.spin_max_len)

        self.combo_lead_min = QComboBox()
        self.combo_lead_min.setAccessibleName("Calendar Reminder Lead Time")
        self.combo_lead_min.setAccessibleDescription("When to surface upcoming meeting reminder cards")
        for mins in (1, 2, 5, 10):
            self.combo_lead_min.addItem(f"{mins} min before start", mins)
        self.combo_lead_min.currentIndexChanged.connect(
            lambda: self._save_cfg("calendar_reminder_lead_min", int(self.combo_lead_min.currentData() or 1))
        )
        self._add_control_row(nt_lay, "Calendar Reminder Lead Time", "When to surface upcoming meeting reminder cards.", self.combo_lead_min)

        lay.addWidget(card_nt)
        lay.addStretch(1)
        return page

    def _build_vibe_page(self) -> QWidget:
        page, lay = self._page_shell("Vibe coding", "Improve accuracy for software development prompts, codebases, and IDEs.")

        card_vc, vc_lay = self._make_card()
        self.sw_vibe_vars = self._add_toggle_row(
            vc_lay,
            "Code Identifier Recognition",
            "Format spoken camelCase, snake_case, PascalCase, and API symbols accurately.",
            lambda v: self._save_cfg("vibe_coding_variable_recognition", v),
        )
        self.sw_vibe_files = self._add_toggle_row(
            vc_lay,
            "Automatic File Tagging",
            "Normalize spoken source file names and paths into clean references.",
            lambda v: self._save_cfg("vibe_coding_file_tagging", v),
        )
        lay.addWidget(card_vc)
        lay.addStretch(1)
        return page

    def _build_account_page(self) -> QWidget:
        page, lay = self._page_shell("Account & Subscription", "VOX Pro entitlements, weekly usage meters, and connected meeting calendars.")

        # 1. Commercial Subscription & Metering Card (P0)
        card_sub, s_lay = self._make_card()

        # Header Row: Plan Badge + Status
        hdr_row = QHBoxLayout()
        self.lbl_user_email = QLabel("Signed in as: <b>Guest / Local Mode</b>")
        self.lbl_user_email.setWordWrap(True)
        self.lbl_user_email.setStyleSheet("font-size: 13px; font-weight: 500;")
        hdr_row.addWidget(self.lbl_user_email, 1)

        self.lbl_plan_badge = QLabel("VOX FREE TIER · ₹0/mo")
        self.lbl_plan_badge.setStyleSheet("color: #f59e0b; font-weight: 700; font-size: 12px; padding: 4px 8px; border-radius: 6px; background: rgba(245, 158, 11, 0.12);")
        hdr_row.addWidget(self.lbl_plan_badge)
        s_lay.addLayout(hdr_row)

        s_lay.addSpacing(6)

        # Quota Meters
        meter_grid = QVBoxLayout()
        meter_grid.setSpacing(8)

        # 1. Voice Typing Words
        w_box = QVBoxLayout()
        self.lbl_usage_words = QLabel("Voice Typing: 0 / 3,000 words this week")
        self.lbl_usage_words.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.bar_words = QProgressBar()
        self.bar_words.setFixedHeight(6)
        self.bar_words.setTextVisible(False)
        self.bar_words.setStyleSheet("QProgressBar { background: rgba(120, 120, 120, 0.2); border-radius: 3px; } QProgressBar::chunk { background: #3b82f6; border-radius: 3px; }")
        w_box.addWidget(self.lbl_usage_words)
        w_box.addWidget(self.bar_words)
        meter_grid.addLayout(w_box)

        # 2. Meeting Audio Minutes
        m_box = QVBoxLayout()
        self.lbl_usage_minutes = QLabel("Meeting Audio: 0 / 180 mins this week")
        self.lbl_usage_minutes.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.bar_minutes = QProgressBar()
        self.bar_minutes.setFixedHeight(6)
        self.bar_minutes.setTextVisible(False)
        self.bar_minutes.setStyleSheet("QProgressBar { background: rgba(120, 120, 120, 0.2); border-radius: 3px; } QProgressBar::chunk { background: #10b981; border-radius: 3px; }")
        m_box.addWidget(self.lbl_usage_minutes)
        m_box.addWidget(self.bar_minutes)
        meter_grid.addLayout(m_box)

        # 3. Meeting Count
        c_box = QVBoxLayout()
        self.lbl_usage_meetings = QLabel("Meetings Recorded: 0 / 5 sessions this week")
        self.lbl_usage_meetings.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.bar_meetings = QProgressBar()
        self.bar_meetings.setFixedHeight(6)
        self.bar_meetings.setTextVisible(False)
        self.bar_meetings.setStyleSheet("QProgressBar { background: rgba(120, 120, 120, 0.2); border-radius: 3px; } QProgressBar::chunk { background: #8b5cf6; border-radius: 3px; }")
        c_box.addWidget(self.lbl_usage_meetings)
        c_box.addWidget(self.bar_meetings)
        meter_grid.addLayout(c_box)

        # 4. Ask VOX Queries
        q_box = QVBoxLayout()
        self.lbl_usage_ask_vox = QLabel("Ask VOX Queries: 0 / 10 queries this week")
        self.lbl_usage_ask_vox.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.bar_ask_vox = QProgressBar()
        self.bar_ask_vox.setFixedHeight(6)
        self.bar_ask_vox.setTextVisible(False)
        self.bar_ask_vox.setStyleSheet("QProgressBar { background: rgba(120, 120, 120, 0.2); border-radius: 3px; } QProgressBar::chunk { background: #f59e0b; border-radius: 3px; }")
        q_box.addWidget(self.lbl_usage_ask_vox)
        q_box.addWidget(self.bar_ask_vox)
        meter_grid.addLayout(q_box)

        s_lay.addLayout(meter_grid)

        s_lay.addSpacing(6)
        lbl_reset_note = QLabel("ℹ️ Quota resets every Monday at 00:00 UTC (05:30 IST). Audio, notes, and local files never leave your PC.")
        lbl_reset_note.setStyleSheet("font-size: 11px; opacity: 0.7;")
        lbl_reset_note.setWordWrap(True)
        s_lay.addWidget(lbl_reset_note)

        # Action Buttons Row
        sub_btns = QHBoxLayout()
        sub_btns.setSpacing(8)

        self.btn_auth_action = QPushButton("Sign In with Google")
        self.btn_auth_action.setObjectName("secondaryBtn")
        self.btn_auth_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auth_action.clicked.connect(self._on_login_clicked)
        sub_btns.addWidget(self.btn_auth_action)

        self.btn_manual_auth = QPushButton("Paste Sign-in URL...")
        self.btn_manual_auth.setObjectName("secondaryBtn")
        self.btn_manual_auth.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_auth.setToolTip("Paste browser callback URL (e.g. localhost:3000/#access_token=...) if browser did not redirect automatically")
        self.btn_manual_auth.clicked.connect(self._on_manual_auth_clicked)
        sub_btns.addWidget(self.btn_manual_auth)

        self.btn_upgrade_pro = QPushButton("Upgrade to VOX Pro — ₹199/mo →")
        self.btn_upgrade_pro.setObjectName("dawnBtn")
        self.btn_upgrade_pro.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upgrade_pro.clicked.connect(self._on_upgrade_pro_clicked)
        sub_btns.addWidget(self.btn_upgrade_pro)

        self.btn_sync_sub = QPushButton("Sync Plan")
        self.btn_sync_sub.setObjectName("secondaryBtn")
        self.btn_sync_sub.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync_sub.clicked.connect(self._on_sync_subscription_clicked)
        sub_btns.addWidget(self.btn_sync_sub)

        sub_btns.addStretch(1)
        s_lay.addLayout(sub_btns)
        lay.addWidget(card_sub)

        # 2. Calendar Integrations Card
        card_acct, a_lay = self._make_card()
        lbl_local = QLabel("Unified Calendar Integrations · Retrieve real meetings, detect conferencing links, and 1-click capture.")
        lbl_local.setWordWrap(True)
        lbl_local.setStyleSheet("font-size: 13px; font-weight: 600;")
        a_lay.addWidget(lbl_local)

        # 1. Microsoft 365 / Outlook
        ms_box = QHBoxLayout()
        self.lbl_ms_status = QLabel("Microsoft 365 / Outlook: Not connected")
        self.lbl_ms_status.setObjectName("settingsMuted")
        ms_box.addWidget(self.lbl_ms_status, 1)

        self.btn_connect_ms = QPushButton("Connect Outlook")
        self.btn_connect_ms.setObjectName("dawnBtn")
        self.btn_connect_ms.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect_ms.clicked.connect(self._on_connect_calendar_clicked)
        ms_box.addWidget(self.btn_connect_ms)

        self.btn_disconnect_ms = QPushButton("Disconnect")
        self.btn_disconnect_ms.setObjectName("secondaryBtn")
        self.btn_disconnect_ms.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_disconnect_ms.clicked.connect(lambda: self._on_disconnect_provider("ms_graph"))
        ms_box.addWidget(self.btn_disconnect_ms)
        a_lay.addLayout(ms_box)

        # 2. Google Calendar
        g_box = QHBoxLayout()
        self.lbl_google_status = QLabel("Google Calendar: Not connected")
        self.lbl_google_status.setObjectName("settingsMuted")
        g_box.addWidget(self.lbl_google_status, 1)

        self.btn_connect_google = QPushButton("Connect Google")
        self.btn_connect_google.setObjectName("dawnBtn")
        self.btn_connect_google.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect_google.clicked.connect(self._on_connect_calendar_clicked)
        g_box.addWidget(self.btn_connect_google)

        self.btn_disconnect_google = QPushButton("Disconnect")
        self.btn_disconnect_google.setObjectName("secondaryBtn")
        self.btn_disconnect_google.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_disconnect_google.clicked.connect(lambda: self._on_disconnect_provider("google"))
        g_box.addWidget(self.btn_disconnect_google)
        a_lay.addLayout(g_box)

        # 3. Zoom Meetings
        z_box = QHBoxLayout()
        self.lbl_zoom_status = QLabel("Zoom Meetings: Not connected")
        self.lbl_zoom_status.setObjectName("settingsMuted")
        z_box.addWidget(self.lbl_zoom_status, 1)

        self.btn_connect_zoom = QPushButton("Connect Zoom")
        self.btn_connect_zoom.setObjectName("dawnBtn")
        self.btn_connect_zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect_zoom.clicked.connect(self._on_connect_calendar_clicked)
        z_box.addWidget(self.btn_connect_zoom)

        self.btn_disconnect_zoom = QPushButton("Disconnect")
        self.btn_disconnect_zoom.setObjectName("secondaryBtn")
        self.btn_disconnect_zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_disconnect_zoom.clicked.connect(lambda: self._on_disconnect_provider("zoom"))
        z_box.addWidget(self.btn_disconnect_zoom)
        a_lay.addLayout(z_box)

        # Global sync row
        cal_btns = QHBoxLayout()
        cal_btns.setSpacing(8)
        self.btn_sync_cal = QPushButton("Sync All Calendars Now")
        self.btn_sync_cal.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync_cal.clicked.connect(self._on_sync_calendar_clicked)
        cal_btns.addWidget(self.btn_sync_cal)

        self.btn_disconnect_cal = QPushButton("Disconnect All")
        self.btn_disconnect_cal.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_disconnect_cal.clicked.connect(self._on_disconnect_calendar_clicked)
        cal_btns.addWidget(self.btn_disconnect_cal)

        self.btn_open_calendar_hub = QPushButton("Manage Accounts & Calendars →")
        self.btn_open_calendar_hub.setObjectName("dawnBtn")
        self.btn_open_calendar_hub.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_calendar_hub.clicked.connect(self.open_calendar_requested.emit)
        cal_btns.addWidget(self.btn_open_calendar_hub)

        cal_btns.addStretch(1)
        a_lay.addLayout(cal_btns)

        # Compatibility alias
        self.lbl_cal_status = self.lbl_ms_status

        lay.addWidget(card_acct)
        lay.addStretch(1)
        return page

    def _update_account_ui(self) -> None:
        """Dynamically refresh account status, plan badge, and quota meters."""
        if not hasattr(self, "lbl_user_email") or not hasattr(self.controller, "get_account_status"):
            return
        status = self.controller.get_account_status()
        is_pro = status.get("is_pro", False)
        signed_in = status.get("signed_in", False)
        email = status.get("email")
        name = status.get("name") or "Guest User"
        usage = status.get("usage", {})
        limits = status.get("limits", {})

        if signed_in and email:
            self.lbl_user_email.setText(f"Signed in as: <b>{name}</b> ({email})")
            self.btn_auth_action.setText("Sign Out")
            if hasattr(self, "btn_manual_auth"):
                self.btn_manual_auth.setVisible(False)
            try:
                self.btn_auth_action.clicked.disconnect()
            except Exception:
                pass
            self.btn_auth_action.clicked.connect(self._on_logout_clicked)
        else:
            self.lbl_user_email.setText("Signed in as: <b>Guest / Local Mode</b>")
            self.btn_auth_action.setText("Sign In with Google")
            if hasattr(self, "btn_manual_auth"):
                self.btn_manual_auth.setVisible(True)
            try:
                self.btn_auth_action.clicked.disconnect()
            except Exception:
                pass
            self.btn_auth_action.clicked.connect(self._on_login_clicked)

        if is_pro:
            self.lbl_plan_badge.setText("VOX PRO (Active) · ₹199/mo")
            self.lbl_plan_badge.setStyleSheet("color: #10b981; font-weight: 700; font-size: 12px; padding: 4px 8px; border-radius: 6px; background: rgba(16, 185, 129, 0.12);")
            self.btn_upgrade_pro.setVisible(False)
            self.lbl_usage_words.setText(f"Voice Typing: {usage.get('dictation_words', 0):,} words this week (Unlimited)")
            self.bar_words.setMaximum(100)
            self.bar_words.setValue(100)
            self.lbl_usage_minutes.setText(f"Meeting Audio: {int(usage.get('meeting_duration_sec', 0) / 60)} mins this week (Unlimited)")
            self.bar_minutes.setMaximum(100)
            self.bar_minutes.setValue(100)
            self.lbl_usage_meetings.setText(f"Meetings Recorded: {usage.get('meeting_count', 0)} sessions this week (Unlimited)")
            self.bar_meetings.setMaximum(100)
            self.bar_meetings.setValue(100)
            self.lbl_usage_ask_vox.setText(f"Ask VOX Queries: {usage.get('ask_vox_count', 0)} queries this week (Unlimited)")
            self.bar_ask_vox.setMaximum(100)
            self.bar_ask_vox.setValue(100)
        else:
            self.lbl_plan_badge.setText("VOX FREE TIER · ₹0/mo")
            self.lbl_plan_badge.setStyleSheet("color: #f59e0b; font-weight: 700; font-size: 12px; padding: 4px 8px; border-radius: 6px; background: rgba(245, 158, 11, 0.12);")
            self.btn_upgrade_pro.setVisible(True)

            w_used = usage.get("dictation_words", 0)
            w_lim = limits.get("dictation_words_per_week", 3000)
            self.lbl_usage_words.setText(f"Voice Typing: {w_used:,} / {w_lim:,} words this week")
            self.bar_words.setMaximum(w_lim)
            self.bar_words.setValue(min(w_used, w_lim))

            m_sec = usage.get("meeting_duration_sec", 0.0)
            m_min = int(m_sec / 60)
            m_lim = int(limits.get("meeting_duration_sec_per_week", 10800) / 60)
            self.lbl_usage_minutes.setText(f"Meeting Audio: {m_min} / {m_lim} mins this week")
            self.bar_minutes.setMaximum(m_lim)
            self.bar_minutes.setValue(min(m_min, m_lim))

            c_used = usage.get("meeting_count", 0)
            c_lim = limits.get("meeting_count_per_week", 5)
            self.lbl_usage_meetings.setText(f"Meetings Recorded: {c_used} / {c_lim} sessions this week")
            self.bar_meetings.setMaximum(c_lim)
            self.bar_meetings.setValue(min(c_used, c_lim))

            q_used = usage.get("ask_vox_count", 0)
            q_lim = limits.get("ask_vox_queries_per_week", 10)
            self.lbl_usage_ask_vox.setText(f"Ask VOX Queries: {q_used} / {q_lim} queries this week")
            self.bar_ask_vox.setMaximum(q_lim)
            self.bar_ask_vox.setValue(min(q_used, q_lim))

    def _on_login_clicked(self) -> None:
        self.status_toast.emit("Signing In", "Opening browser for Google Authentication...")
        if hasattr(self.controller, "login_with_google"):
            self.controller.login_with_google(callback=lambda ok, msg: self.status_toast.emit("Sign In", msg))

    def _on_manual_auth_clicked(self) -> None:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        url, ok = QInputDialog.getText(
            self,
            "Complete Sign-In",
            "Paste the authorization redirect URL or access token from your browser:\n(e.g. http://localhost:3000/#access_token=...)",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if ok and url.strip():
            if hasattr(self.controller, "complete_login_from_url"):
                success, msg = self.controller.complete_login_from_url(url.strip())
                self.status_toast.emit("Sign In", msg)
                if success:
                    self._update_subscription_display()
            else:
                self.status_toast.emit("Sign In", "Manual login helper unavailable.")

    def _on_logout_clicked(self) -> None:
        if hasattr(self.controller, "logout_user"):
            self.controller.logout_user()
            self.status_toast.emit("Signed Out", "Reverted to Free tier successfully.")

    def _on_upgrade_pro_clicked(self) -> None:
        try:
            from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog
            theme = getattr(self.controller, "config", None) and getattr(self.controller.config, "theme", "light") or "light"
            dlg = VoxProUpgradeDialog(
                self.controller,
                reason="Upgrade to VOX Pro to unlock unlimited dictation, unlimited meetings, and cognitive AI memory.",
                theme=theme,
                parent=self,
            )
            dlg.exec()
        except Exception:
            import webbrowser
            webbrowser.open("https://vox-voice-assistant.vercel.app/pricing")

    def _on_sync_subscription_clicked(self) -> None:
        if hasattr(self.controller, "sync_entitlements_with_cloud"):
            self.controller.sync_entitlements_with_cloud()
            self.status_toast.emit("Synchronizing", "Refreshing entitlement token and usage with cloud...")

    def _build_privacy_page(self) -> QWidget:
        page, lay = self._page_shell("Data and Privacy", "Manage local SQLite retention, backups, and selective data removal.")

        card_ret, ret_lay = self._make_card()
        self.combo_retention = QComboBox()
        self.combo_retention.setAccessibleName("Data auto-retention window")
        self.combo_retention.setAccessibleDescription("Automatically prune old records past the selected window")
        for lbl, days in (("Keep Forever", 0), ("7 Days", 7), ("30 Days", 30), ("90 Days", 90)):
            self.combo_retention.addItem(lbl, days)
        self.combo_retention.currentIndexChanged.connect(self._on_retention_changed)
        self._add_control_row(ret_lay, "Auto-Retention Window", "Automatically prune old records past the selected window.", self.combo_retention)

        btn_export = QPushButton("Export All VOX Data (.json)")
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.setAccessibleName("Export all VOX data")
        btn_export.setAccessibleDescription("Export dictations, dictionary, snippets, scratchpad, and meeting metadata")
        btn_export.clicked.connect(self._on_export_all_data)
        self._add_control_row(ret_lay, "Full Local Backup", "Export dictations, dictionary, snippets, scratchpad, and meeting metadata.", btn_export)
        lay.addWidget(card_ret)

        card_wipe, w_lay = self._make_card()
        lbl_w = QLabel("Selective Data Deletion")
        lbl_w.setStyleSheet("font-size: 13.5px; font-weight: 700;")
        w_lay.addWidget(lbl_w)

        self._wipe_checks: Dict[str, QCheckBox] = {}
        for key, lbl_txt in (
            ("dictation", "Dictation History"),
            ("vocabulary", "Custom Dictionary Words"),
            ("snippets", "Voice Snippets"),
            ("notes", "Scratchpad Notes & Version History"),
            ("meetings", "Recorded Meetings, Transcripts & Audio/Video Files"),
        ):
            cb = QCheckBox(lbl_txt)
            cb.setAccessibleName(f"Select {lbl_txt} for deletion")
            cb.setAccessibleDescription(f"Include {lbl_txt} in permanent deletion")
            self._wipe_checks[key] = cb
            w_lay.addWidget(cb)

        wipe_row = QHBoxLayout()
        btn_wipe = QPushButton("Delete Selected Data")
        btn_wipe.setObjectName("dangerBtn")
        btn_wipe.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_wipe.setAccessibleName("Delete selected data")
        btn_wipe.setAccessibleDescription("Permanently erase selected data categories from local storage")
        btn_wipe.clicked.connect(self._on_selective_wipe)
        wipe_row.addWidget(btn_wipe)
        wipe_row.addStretch(1)
        w_lay.addLayout(wipe_row)
        lay.addWidget(card_wipe)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # State hydration & Handlers
    # ------------------------------------------------------------------
    def _populate_mics(self) -> None:
        self.combo_mic.blockSignals(True)
        self.combo_mic.clear()
        self.combo_mic.addItem("System Default Microphone", None)
        try:
            from voxlocal.audio.devices import AudioDeviceManager
            mics, _ = AudioDeviceManager.list_devices()
            for dev in mics:
                tag = " (Default)" if dev.is_default else ""
                self.combo_mic.addItem(f"{dev.name}{tag}", (dev.index, dev.name))
        except Exception as exc:
            logger.debug("Mic listing fallback: %s", exc)

        cfg = self.controller.config
        idx_target = getattr(cfg, "mic_device_index", None)
        name_target = getattr(cfg, "mic_device_name", "") or ""
        matched = 0
        for i in range(1, self.combo_mic.count()):
            data = self.combo_mic.itemData(i)
            if isinstance(data, tuple) and (data[0] == idx_target or (name_target and name_target in data[1])):
                matched = i
                break
        self.combo_mic.setCurrentIndex(matched)
        self.combo_mic.blockSignals(False)

    def load_from_config(self) -> None:
        """Populate every widget cleanly from controller.config."""
        cfg = self.controller.config
        for field_name, btn in self._hotkey_buttons.items():
            val = str(getattr(cfg, field_name, "") or "").strip()
            btn.setText(val.upper().replace("+", " + ") if val else "Not Set")

        for sw, attr, default in (
            (self.sw_hands_free_dt, "hands_free_double_tap", True),
            (self.sw_notetaker_tt, "notetaker_triple_tap", False),
            (self.sw_hinglish, "hinglish_enabled", True),
            (self.sw_show_flow, "show_floating_icon", True),
            (self.sw_hide_screenshare, "hide_in_screen_share", False),
            (self.sw_launch_login, "launch_at_login", True),
            (self.sw_sounds, "sound_effects", True),
            (self.sw_mute_sys, "mute_audio_while_dictating", False),
            (self.sw_smart_fmt, "smart_formatting", True),
            (self.sw_auto_dict, "auto_add_dictionary", True),
            (self.sw_ctx, "context_awareness", True),
            (self.sw_email_sig, "email_auto_signature", False),
            (self.sw_prompt_call, "prompt_on_call", True),
            (self.sw_nt_auto_end, "notetaker_auto_end", True),
            (self.sw_nt_auto_open, "notetaker_auto_open_summary", True),
            (self.sw_nt_speakers, "notetaker_auto_detect_speakers", True),
            (self.sw_nt_live, "notetaker_live_transcript", True),
            (self.sw_vibe_vars, "vibe_coding_variable_recognition", True),
            (self.sw_vibe_files, "vibe_coding_file_tagging", True),
        ):
            sw.blockSignals(True)
            sw.setChecked(bool(getattr(cfg, attr, default)))
            sw.blockSignals(False)

        if hasattr(self, "sw_auto_start_call"):
            self.sw_auto_start_call.blockSignals(True)
            is_auto_on = bool(getattr(cfg, "auto_record_calls", True)) and not bool(getattr(cfg, "prompt_on_call", True))
            self.sw_auto_start_call.setChecked(is_auto_on)
            self.sw_auto_start_call.blockSignals(False)

        self._populate_mics()
        gain_val = int(round(float(getattr(cfg, "mic_gain", 1.0) or 1.0) * 100))
        self.slider_mic_gain.blockSignals(True)
        self.slider_mic_gain.setValue(max(50, min(250, gain_val)))
        self.lbl_mic_gain.setText(f"{self.slider_mic_gain.value() / 100.0:.2f}x")
        self.slider_mic_gain.blockSignals(False)

        self._set_combo_by_data(self.combo_dict_mode, getattr(cfg, "dictation_mode", "push_to_talk"))
        self._set_combo_by_data(self.combo_lang, getattr(cfg, "language", "en+hi"))
        self._set_combo_by_data(self.combo_model, getattr(cfg, "whisper_model", "large-v3-turbo"))
        self._set_combo_by_data(self.combo_compute, getattr(cfg, "compute_type", "default") or "default")
        self._set_combo_by_data(self.combo_profile, getattr(cfg, "transcribe_profile", "balanced"))
        self._set_combo_by_data(self.combo_theme, getattr(cfg, "theme", "light"))
        self._set_combo_by_data(self.combo_edge, getattr(cfg, "flow_bar_edge", "bottom"))
        self._set_combo_by_data(self.combo_rec_mode, getattr(cfg, "meeting_recording_mode", "audio_video"))
        self._set_combo_by_data(self.combo_drawer_start, getattr(cfg, "notetaker_notepad_on_start", "open"))
        self._set_combo_by_data(self.combo_lead_min, int(getattr(cfg, "calendar_reminder_lead_min", 1)))
        self._set_combo_by_data(self.combo_retention, int(getattr(cfg, "retention_days", 0)))

        self.txt_email_sig.setText(str(getattr(cfg, "email_signature_text", "") or ""))
        self.spin_fps.blockSignals(True)
        self.spin_fps.setValue(int(getattr(cfg, "screen_fps", 15)))
        self.spin_fps.blockSignals(False)
        self.spin_max_len.blockSignals(True)
        self.spin_max_len.setValue(int(getattr(cfg, "notetaker_max_length_min", 120)))
        self.spin_max_len.blockSignals(False)
        self._refresh_calendar_status()

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, target: Any) -> None:
        combo.blockSignals(True)
        for i in range(combo.count()):
            if combo.itemData(i) == target:
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    def _save_cfg(self, field: str, value: Any, apply_hotkeys: bool = False) -> None:
        setattr(self.controller.config, field, value)
        self.controller.config.save()
        if apply_hotkeys and hasattr(self.controller, "apply_hotkey_config"):
            try:
                self.controller.apply_hotkey_config()
            except Exception as exc:
                logger.warning("Failed re-applying hotkeys: %s", exc)
        if field == "hide_in_screen_share":
            pill = getattr(self.controller, "floating_pill", None)
            if pill is not None and hasattr(pill, "set_screen_share_hidden"):
                pill.set_screen_share_hidden(bool(value))

    def _edit_hotkey(self, field_name: str, title: str) -> None:
        current = str(getattr(self.controller.config, field_name, "") or "")
        dlg = ShortcutCaptureDialog(self, current_shortcut=current, title=f"Set {title}", theme=self._theme)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            val = dlg.get_shortcut()
            self._save_cfg(field_name, val, apply_hotkeys=True)
            btn = self._hotkey_buttons.get(field_name)
            if btn is not None:
                btn.setText(val.upper().replace("+", " + ") if val else "Not Set")
            self.status_toast.emit("Shortcut Updated", f"{title} bound to {val.upper()}")

    def _on_mic_selected(self) -> None:
        data = self.combo_mic.currentData()
        if isinstance(data, tuple):
            self.controller.config.mic_device_index = int(data[0])
            self.controller.config.mic_device_name = str(data[1])
        else:
            self.controller.config.mic_device_index = None
            self.controller.config.mic_device_name = ""
        self.controller.config.save()
        if hasattr(self, "_mic_probe") and self._mic_probe is not None and getattr(self, "btn_test_mic", None) and self.btn_test_mic.isChecked():
            self._start_mic_probe(self.controller.config.mic_device_index, self.controller.config.mic_device_name)

    def _on_dict_mode_changed(self) -> None:
        val = str(self.combo_dict_mode.currentData() or "push_to_talk")
        self._save_cfg("dictation_mode", val, apply_hotkeys=True)

    def _on_auto_start_call_toggled(self, checked: bool) -> None:
        if checked:
            self.controller.config.auto_record_calls = True
            self.controller.config.prompt_on_call = False
        else:
            self.controller.config.prompt_on_call = True
        self.controller.config.save()
        if hasattr(self, "sw_prompt_call"):
            self.sw_prompt_call.blockSignals(True)
            self.sw_prompt_call.setChecked(self.controller.config.prompt_on_call)
            self.sw_prompt_call.blockSignals(False)

    def _on_mic_gain_changed(self, value: int) -> None:
        gain = round(value / 100.0, 2)
        self.lbl_mic_gain.setText(f"{gain:.2f}x")
        if hasattr(self.controller, "set_mic_gain"):
            self.controller.set_mic_gain(gain)
        else:
            self._save_cfg("mic_gain", gain)

    def _on_language_changed(self) -> None:
        lang = str(self.combo_lang.currentData() or "en+hi")
        if hasattr(self.controller, "set_language_mode"):
            self.controller.set_language_mode(lang)
        else:
            self._save_cfg("language", lang)

    def _on_model_changed(self) -> None:
        mdl = str(self.combo_model.currentData() or "large-v3-turbo")
        if hasattr(self.controller, "switch_whisper_model"):
            self.controller.switch_whisper_model(mdl)
        else:
            self._save_cfg("whisper_model", mdl)

    def _on_compute_changed(self) -> None:
        val = str(self.combo_compute.currentData() or "default")
        self._save_cfg("compute_type", val)
        if hasattr(self.controller, "set_compute_type"):
            self.controller.set_compute_type(val)

    def _toggle_mic_test(self) -> None:
        if self.btn_test_mic.isChecked():
            dev_idx = None
            dev_name = ""
            data = self.combo_mic.currentData()
            if isinstance(data, tuple):
                dev_idx = int(data[0])
                dev_name = str(data[1])
            self._start_mic_probe(dev_idx, dev_name)
            self.btn_test_mic.setText("Stop Test")
        else:
            self._stop_mic_probe()
            self.btn_test_mic.setText("Test Mic")

    def _start_mic_probe(self, dev_idx: Optional[int] = None, dev_name: str = "") -> None:
        self._stop_mic_probe()
        try:
            from voxlocal.audio.devices import MicrophoneLevelProbe
            self._mic_probe = MicrophoneLevelProbe(
                device_index=dev_idx,
                device_name=dev_name,
                on_level=lambda lvl: self.mic_level_updated.emit(lvl),
                on_error=lambda err: logger.debug("Mic probe error: %s", err),
            )
            self._mic_probe.start()
        except Exception as exc:
            logger.debug("Failed to start mic probe: %s", exc)

    def _stop_mic_probe(self) -> None:
        if getattr(self, "_mic_probe", None) is not None:
            try:
                self._mic_probe.stop()
            except Exception:
                pass
        if hasattr(self, "btn_test_mic") and self.btn_test_mic.isChecked():
            self.btn_test_mic.blockSignals(True)
            self.btn_test_mic.setChecked(False)
            self.btn_test_mic.setText("Test Mic")
            self.btn_test_mic.blockSignals(False)
        if hasattr(self, "mic_level_bar"):
            self.mic_level_bar.set_level(0.0)

    def teardown(self) -> None:
        self._stop_mic_probe()
        if hasattr(self, "mic_level_bar"):
            self.mic_level_bar.teardown()

    def hideEvent(self, event) -> None:
        self._stop_mic_probe()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.teardown()
        super().closeEvent(event)

    def _on_profile_changed(self) -> None:
        prof = str(self.combo_profile.currentData() or "balanced")
        if hasattr(self.controller, "set_transcribe_profile"):
            self.controller.set_transcribe_profile(prof)
        else:
            self._save_cfg("transcribe_profile", prof)

    def _on_theme_selected(self) -> None:
        choice = str(self.combo_theme.currentData() or "light")
        if hasattr(self.controller, "set_theme"):
            self.controller.set_theme(choice)

    def _on_edge_selected(self) -> None:
        edge = str(self.combo_edge.currentData() or "bottom")
        if hasattr(self.controller, "set_flow_bar_edge"):
            self.controller.set_flow_bar_edge(edge)

    def _on_show_flow_toggled(self, checked: bool) -> None:
        self._save_cfg("show_floating_icon", checked)
        pill = getattr(self.controller, "floating_pill", None)
        if pill is not None:
            if checked:
                pill.show()
            else:
                pill.hide()

    def _on_startup_toggled(self, checked: bool) -> None:
        self._save_cfg("launch_at_login", checked)
        try:
            from voxlocal.win32.startup import StartupManager
            StartupManager.set_enabled(checked, minimized=True)
        except Exception as exc:
            logger.debug("Startup registry update deferred/blocked: %s", exc)

    def _on_rec_mode_changed(self) -> None:
        mode = str(self.combo_rec_mode.currentData() or "audio_video")
        self.controller.config.meeting_recording_mode = mode
        self.controller.config.record_meeting_screen = (mode == "audio_video")
        self.controller.config.save()

    def _on_retention_changed(self) -> None:
        days = int(self.combo_retention.currentData() or 0)
        self._save_cfg("retention_days", days)
        if days > 0 and getattr(self.controller, "db", None):
            try:
                self.controller.db.apply_retention_policy(days)
            except Exception:
                pass

    def _refresh_calendar_status(self) -> None:
        cfg = getattr(self.controller, "config", None)
        storage_dir = Path(getattr(cfg, "storage_dir", "data"))
        db_accounts = []
        if hasattr(self.controller, "list_connected_calendar_accounts"):
            try:
                db_accounts = list(self.controller.list_connected_calendar_accounts() or [])
            except Exception:
                db_accounts = []

        ms_accounts = [a for a in db_accounts if a.get("provider_type") == "ms_graph" and a.get("is_authenticated")]
        g_accounts = [a for a in db_accounts if a.get("provider_type") == "google" and a.get("is_authenticated")]
        z_accounts = [a for a in db_accounts if a.get("provider_type") == "zoom" and a.get("is_authenticated")]

        # 1. MS Graph status
        ms_connected = bool(ms_accounts or getattr(cfg, "ms_graph_enabled", False) or (storage_dir / "calendar_auth.dat").exists())
        ms_acct = ", ".join(a.get("account_email") or a.get("display_name") or "Outlook" for a in ms_accounts) or getattr(cfg, "calendar_account_email", "") or "Authenticated"
        if ms_connected:
            self.lbl_ms_status.setText(f"Microsoft 365: Connected ({ms_acct})")
            self.btn_connect_ms.setText("Reconnect Outlook")
            self.btn_disconnect_ms.setVisible(True)
        else:
            self.lbl_ms_status.setText("Microsoft 365: Not connected")
            self.btn_connect_ms.setText("Connect Outlook")
            self.btn_disconnect_ms.setVisible(False)

        # 2. Google Calendar status
        google_connected = bool(g_accounts or getattr(cfg, "google_calendar_enabled", False) or (storage_dir / "calendar_google.dat").exists())
        g_acct = ", ".join(a.get("account_email") or a.get("display_name") or "Google" for a in g_accounts) or getattr(cfg, "google_calendar_account_email", "") or "Authenticated"
        if google_connected:
            self.lbl_google_status.setText(f"Google Calendar: Connected ({g_acct})")
            self.btn_connect_google.setText("Reconnect Google")
            self.btn_disconnect_google.setVisible(True)
        else:
            self.lbl_google_status.setText("Google Calendar: Not connected")
            self.btn_connect_google.setText("Connect Google")
            self.btn_disconnect_google.setVisible(False)

        # 3. Zoom status
        zoom_connected = bool(z_accounts or getattr(cfg, "zoom_enabled", False) or (storage_dir / "calendar_zoom.dat").exists())
        z_acct = ", ".join(a.get("account_email") or a.get("display_name") or "Zoom" for a in z_accounts) or getattr(cfg, "zoom_account_email", "") or "Authenticated"
        if zoom_connected:
            self.lbl_zoom_status.setText(f"Zoom Meetings: Connected ({z_acct})")
            self.btn_connect_zoom.setText("Reconnect Zoom")
            self.btn_disconnect_zoom.setVisible(True)
        else:
            self.lbl_zoom_status.setText("Zoom Meetings: Not connected")
            self.btn_connect_zoom.setText("Connect Zoom")
            self.btn_disconnect_zoom.setVisible(False)

        any_connected = ms_connected or google_connected or zoom_connected or bool(db_accounts)
        self.btn_sync_cal.setVisible(any_connected)
        self.btn_disconnect_cal.setVisible(any_connected)

    def _on_disconnect_provider(self, provider_type: str) -> None:
        if hasattr(self.controller, "disconnect_calendar_provider"):
            self.controller.disconnect_calendar_provider(provider_type)
        self._refresh_calendar_status()
        self.status_toast.emit("Calendar Disconnected", f"{provider_type.replace('_', ' ').title()} disconnected.")

    def _on_connect_calendar_clicked(self) -> None:
        from voxlocal.ui.calendar_connect_dialog import VoxCalendarConnectDialog

        dlg = VoxCalendarConnectDialog(self.controller, theme=self._theme, parent=self)
        dlg.connected.connect(lambda msg: self.status_toast.emit("Calendar Connection", msg))
        dlg.exec()
        self._refresh_calendar_status()

    def _on_sync_calendar_clicked(self) -> None:
        mgr = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
        if mgr is not None:
            mgr.poll_now()
            self.status_toast.emit("Calendar Synced", "Checked all connected calendars for upcoming meetings.")

    def _on_disconnect_calendar_clicked(self) -> None:
        if hasattr(self.controller, "disconnect_calendar"):
            self.controller.disconnect_calendar()
        self._refresh_calendar_status()
        self.status_toast.emit("Calendar Disconnected", "All calendar accounts disconnected.")

    def _on_export_all_data(self) -> None:
        default_name = f"vox_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export VOX Data", default_name, "JSON Files (*.json)")
        if not path:
            return
        db = self.controller.db
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "dictation_history": db.list_dictation_history(limit=5000),
            "dictionary": db.list_vocabulary(active_only=False),
            "snippets": db.list_snippets(),
            "scratchpad_notes": db.list_notes(),
            "meetings": db.list_meetings(limit=500),
        }
        Path(path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self.status_toast.emit("Export Complete", f"Saved VOX data to {Path(path).name}")

    def _on_selective_wipe(self) -> None:
        cats = [k for k, cb in self._wipe_checks.items() if cb.isChecked()]
        if not cats:
            self.status_toast.emit("Nothing Selected", "Select at least one data category to remove.")
            return
        ans = QMessageBox.question(
            self,
            "Delete Selected Data",
            f"Permanently delete {', '.join(cats)} from local storage? This cannot be undone.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        rec_dir = Path(self.controller.config.storage_dir) / "recordings" if self.controller.config.storage_dir else None
        self.controller.db.clear_data_categories(cats, recordings_dir=rec_dir)
        for cb in self._wipe_checks.values():
            cb.setChecked(False)
        self.controller.history_updated.emit()
        self.controller.vocabulary_updated.emit()
        self.controller.snippets_updated.emit()
        self.controller.notes_updated.emit()
        self.status_toast.emit("Data Deleted", "Selected local data categories were removed.")

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            SettingsWorkspaceWidget, QScrollArea, QWidget#settingsScrollBody {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#settingsMuted {{ color: {t['text_muted']}; font-size: 11.5px; }}
            QFrame#settingsCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            QPushButton#keycapBtn {{
                background-color: {t['surface_well']};
                color: {t['text']};
                border: 1px solid {t['keycap_border']};
                border-bottom: 2px solid {t['line_hover']};
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11.5px;
                font-weight: 700;
            }}
            QPushButton#keycapBtn:hover {{ background-color: {t['sidebar_active']}; }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 700;
            }}
            QPushButton#dangerBtn {{
                background-color: transparent;
                color: {t['accent_crimson']};
                border: 1px solid {t['accent_crimson']};
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton#dangerBtn:hover {{ background-color: rgba(255, 87, 87, 0.12); }}
            QComboBox, QSpinBox, QLineEdit {{
                background-color: {t['input_bg']};
                color: {t['text']};
                border: 1px solid {t['input_border']};
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 12.5px;
                min-height: 20px;
            }}
            QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{
                border-color: {t['line_hover']};
            }}
            QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
                border-color: {t['input_focus_border']};
            }}
            QComboBox QAbstractItemView {{
                background-color: {t['surface']};
                color: {t['text']};
                border: 1px solid {t['border']};
                selection-background-color: {t['sidebar_active']};
                selection-color: {t['text']};
            }}
            QPushButton {{
                background-color: {t['secondary_btn_bg']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {t['sidebar_hover']};
                border-color: {t['line_hover']};
            }}
            QCheckBox {{
                color: {t['text']};
                background: transparent;
                spacing: 8px;
            }}
            """
        )
        self.sub_nav.setStyleSheet(
            f"QFrame {{ background-color: {t['sidebar_bg']}; border-right: 1px solid {t['line']}; }}"
            f"QLabel {{ color: {t['text']}; border: none; background: transparent; }}"
        )
        for lbl in (self.lbl_group1, self.lbl_group2):
            lbl.setStyleSheet(f"color: {t['text_dim']}; font-size: 10px; font-weight: 700; letter-spacing: 0.08em; padding-left: 8px;")
        nav_qss = (
            f"QPushButton {{ text-align: left; padding: 6px 12px; border-radius: 8px; border: none; "
            f"background: transparent; color: {t['text_muted']}; font-size: 13px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {t['sidebar_hover']}; color: {t['text']}; }}"
            f"QPushButton:checked {{ background: {t['sidebar_active']}; color: {t['text']}; font-weight: 700; }}"
        )
        for btn in self._nav_buttons:
            btn.setStyleSheet(nav_qss)
        for sw in self._toggles:
            sw.set_theme(self._theme)

    set_theme = apply_theme


class SettingsDialog(QDialog):
    """Optional standalone dialog wrapper around SettingsWorkspaceWidget."""

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VOX Settings")
        self.resize(920, 660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.workspace = SettingsWorkspaceWidget(controller, parent=self)
        lay.addWidget(self.workspace)

    def reject(self) -> None:
        if hasattr(self, "workspace"):
            self.workspace.teardown()
        super().reject()

    def closeEvent(self, event) -> None:
        if hasattr(self, "workspace"):
            self.workspace.teardown()
        super().closeEvent(event)

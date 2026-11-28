"""
VOX First-Run Onboarding & Try-It-Out Tour (4 Steps)
====================================================
Editorial Wispr Flow-inspired walkthrough adapted to VOX Sand Light / Vast Dark:
  Step 1: Welcome — Editorial intro, FlowBar visual preview & 100% local capabilities
  Step 2: Personalize Setup — Theme, FlowBar edge, microphone selection & live mic probe
  Step 3: Master Shortcuts — Tactile keycaps, hands-free toggle & Hinglish preference
  Step 4: Live Voice Test — Seamless in-app voice test (zero focus-steal), live waveform,
          streaming interim text, verbal self-correction demo & prompt chips
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal.audio.devices import AudioDeviceManager, compute_rms_level, to_mono_f32
from voxlocal.core.logger import get_logger
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.ui.animations import MultiChannelEqualizerWidget
from voxlocal.ui.components import VoxToggleSwitch
from voxlocal.ui.floating_pill import DynamicWaveformCapsule
from voxlocal.ui.theme import (
    create_app_pixmap,
    get_dialog_stylesheet,
    get_theme_tokens,
    get_ui_font,
)

logger = get_logger("ui.onboarding")


_TRY_IT_PROMPTS = [
    (
        "Self-correction",
        "Let's push the Sprint review to Tuesday, actually make that Thursday at 3 PM.",
    ),
    (
        "Quick Teams note",
        "Hey team, comma, the Q4 budget deck is ready for final review, period.",
    ),
    (
        "Action checklist",
        "Follow up with Finance tomorrow morning, new line, confirm rollout timing with QA.",
    ),
]


class VoxOnboardingWizard(QDialog):
    """Wispr-inspired 4-step local onboarding wizard with seamless live in-app voice testing."""

    _mic_probe_level = pyqtSignal(float)
    _mic_probe_error = pyqtSignal(str)

    def __init__(self, controller, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light") if controller else "light"
        self._step_names = ["Welcome", "Setup", "Shortcuts", "Try VOX"]
        self._step_pills: List[QPushButton] = []

        self._mic_probe_stop = threading.Event()
        self._mic_probe_thread: Optional[threading.Thread] = None
        self._tiy_received_text: bool = False
        self._tiy_active_state: str = "idle"

        self.setObjectName("voxOnboardingWizard")
        self.setWindowTitle("Welcome to VOX")
        self.setMinimumSize(780, 620)
        self.resize(840, 656)

        self._build_ui()
        self._connect_signals()
        self.apply_theme(self._theme)
        self._go_to_step(0)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)

        # Editorial Brand Header + Segmented Step Bar
        hdr = QHBoxLayout()
        hdr.setSpacing(10)

        self.brand_mark = QLabel()
        self.brand_mark.setFixedSize(28, 28)
        hdr.addWidget(self.brand_mark)

        brand_title = QLabel("VOX")
        brand_title.setFont(get_ui_font(15, 700))
        hdr.addWidget(brand_title)

        self.badge_local = QLabel("100% Local AI")
        self.badge_local.setObjectName("mutedLabel")
        self.badge_local.setContentsMargins(6, 2, 6, 2)
        hdr.addWidget(self.badge_local)

        hdr.addStretch()

        self._pills_wrap = QFrame()
        pills_lay = QHBoxLayout(self._pills_wrap)
        pills_lay.setContentsMargins(0, 0, 0, 0)
        pills_lay.setSpacing(6)
        for i, sname in enumerate(self._step_names):
            pill = QPushButton(f"{i + 1}. {sname}")
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            pill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            pill.clicked.connect(lambda _c=False, idx=i: self._go_to_step(idx))
            pills_lay.addWidget(pill)
            self._step_pills.append(pill)
        hdr.addWidget(self._pills_wrap)

        hdr.addSpacing(8)
        self.lbl_step = QLabel("Step 1 of 4")
        self.lbl_step.setObjectName("mutedLabel")
        hdr.addWidget(self.lbl_step)
        root.addLayout(hdr)

        # Slim refined progress bar (kept as self.progress_bar for API/test parity)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 4)
        self.progress_bar.setValue(1)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        root.addWidget(self.progress_bar)

        # Pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step_welcome())
        self.stack.addWidget(self._build_step_setup())
        self.stack.addWidget(self._build_step_shortcuts())
        self.stack.addWidget(self._build_step_try_it_out())
        root.addWidget(self.stack, 1)

        # Bottom Navigation Footer
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.btn_skip = QPushButton("Skip tour")
        self.btn_skip.setObjectName("ghostBtn")
        self.btn_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skip.clicked.connect(self._finish_tour)
        foot.addWidget(self.btn_skip)

        foot.addStretch()

        self.btn_back = QPushButton("Back")
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.clicked.connect(self._prev_step)
        foot.addWidget(self.btn_back)

        self.btn_next = QPushButton("Get Started  →")
        self.btn_next.setObjectName("primaryBtn")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.clicked.connect(self._next_step)
        foot.addWidget(self.btn_next)
        root.addLayout(foot)

    def _build_step_welcome(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(12)

        eyebrow = QLabel("VOICE-FIRST DESKTOP WORKSPACE  ·  100% LOCAL")
        eyebrow.setObjectName("onbEyebrow")
        lay.addWidget(eyebrow)

        headline = QLabel("Don’t type, <i>just speak.</i>")
        headline.setObjectName("onbHeadline")
        headline.setTextFormat(Qt.TextFormat.RichText)
        headline.setFont(get_ui_font(34, 500, serif=True))
        lay.addWidget(headline)

        sub = QLabel(
            "Turn natural speech into clear, formatted writing inside every desktop app — "
            "plus bot-free local notes, timelines, and summaries for Teams, Zoom, and Meet calls."
        )
        sub.setObjectName("mutedLabel")
        sub.setWordWrap(True)
        sub.setFont(get_ui_font(14, 400))
        lay.addWidget(sub)

        # Interactive Preview of the Obsidian FlowBar Capsule (Wispr Flow Signature)
        preview_card = QFrame()
        preview_card.setObjectName("voxWellCard")
        pv_lay = QHBoxLayout(preview_card)
        pv_lay.setContentsMargins(16, 12, 16, 12)
        pv_lay.setSpacing(14)

        self.welcome_capsule = QFrame()
        self.welcome_capsule.setStyleSheet(
            "background-color: #000000; border: 1px solid #30302F; border-radius: 19px;"
        )
        cap_lay = QHBoxLayout(self.welcome_capsule)
        cap_lay.setContentsMargins(14, 7, 14, 7)
        cap_lay.setSpacing(10)

        cap_dot = QLabel("●")
        cap_dot.setStyleSheet("color: #4DAA9D; font-size: 9px; border: none;")
        cap_lay.addWidget(cap_dot)

        cap_txt = QLabel("VOX Ready")
        cap_txt.setStyleSheet("color: #FCFCFB; font-size: 12px; font-weight: 600; border: none;")
        cap_lay.addWidget(cap_txt)

        self.welcome_wave = DynamicWaveformCapsule(bar_count=10)
        self.welcome_wave.set_level(0.32)
        cap_lay.addWidget(self.welcome_wave)

        mode_chip = QLabel("Polished")
        mode_chip.setStyleSheet(
            "background-color: #2A1F33; color: #F0D7FF; border: 1px solid #59416B;"
            "border-radius: 9px; padding: 2px 8px; font-size: 10.5px; font-weight: 600;"
        )
        cap_lay.addWidget(mode_chip)
        pv_lay.addWidget(self.welcome_capsule)

        pv_note = QLabel(
            "<b>Your floating Flow Bar</b> docks quietly on your screen edge and never steals focus from what you’re writing."
        )
        pv_note.setTextFormat(Qt.TextFormat.RichText)
        pv_note.setWordWrap(True)
        pv_lay.addWidget(pv_note, 1)
        lay.addWidget(preview_card)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        for tag, title, desc in [
            (
                "Ctrl + Win",
                "Universal Dictation",
                "Press once or hold to dictate into Teams, Outlook, Notion, Word, or your code editor with automatic self-correction.",
            ),
            (
                "Zero Bots",
                "Bot-Free Notetaker",
                "Captures your mic and Windows call audio privately with dual-track transcripts, speaker timelines, and Ask VOX.",
            ),
            (
                "100% Offline",
                "Private On-Device AI",
                "Faster-Whisper speech recognition and local Qwen models run on your own PC — zero cloud uploads required.",
            ),
        ]:
            card = QFrame()
            card.setObjectName("voxCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 15, 16, 15)
            cl.setSpacing(7)

            badge = QLabel(tag)
            badge.setObjectName("keycapBadge")
            badge.setSizePolicy(badge.sizePolicy().Policy.Maximum, badge.sizePolicy().Policy.Fixed)
            cl.addWidget(badge)

            tl = QLabel(title)
            tl.setFont(get_ui_font(14.5, 600))
            cl.addWidget(tl)

            dl = QLabel(desc)
            dl.setObjectName("mutedLabel")
            dl.setWordWrap(True)
            cl.addWidget(dl, 1)
            cards.addWidget(card, 1)
        lay.addLayout(cards, 1)

        # Truthful Local System & AI Readiness Summary Banner (first-run health check)
        readiness_card = QFrame()
        readiness_card.setObjectName("voxWellCard")
        rd_lay = QVBoxLayout(readiness_card)
        rd_lay.setContentsMargins(14, 10, 14, 10)
        rd_lay.setSpacing(4)
        self.lbl_readiness_title = QLabel("Local System & AI Readiness")
        self.lbl_readiness_title.setFont(get_ui_font(12, 700))
        rd_lay.addWidget(self.lbl_readiness_title)
        self.lbl_readiness_items = QLabel("Checking local runtime & AI models...")
        self.lbl_readiness_items.setObjectName("mutedLabel")
        self.lbl_readiness_items.setWordWrap(True)
        self.lbl_readiness_items.setTextFormat(Qt.TextFormat.RichText)
        rd_lay.addWidget(self.lbl_readiness_items)
        lay.addWidget(readiness_card)
        QTimer.singleShot(40, self._refresh_first_run_readiness)
        return page

    def _refresh_first_run_readiness(self) -> None:
        """Populate truthful readiness status on Step 1 using local model/runtime checks only."""
        if not hasattr(self, "lbl_readiness_items"):
            return
        try:
            from voxlocal.core.first_run_health import run_first_run_health_check

            cfg = getattr(self.controller, "config", None)
            report = run_first_run_health_check(config=cfg, verify_hashes=False)
            feats = report.get("features") or {}
            checks = [
                ("VOX Runtime", bool(feats.get("vox_installed"))),
                ("Voice Dictation", bool(feats.get("voice_dictation_ready"))),
                ("Meeting Transcription", bool(feats.get("meeting_transcription_ready"))),
                ("Speaker Identification", bool(feats.get("speaker_identification_ready"))),
                ("Meeting Intelligence", bool(feats.get("meeting_intelligence_ready"))),
                ("Knowledge Search", bool(feats.get("knowledge_ready"))),
            ]
            chips = []
            for label, ok in checks:
                dot = "<span style='color:#137333;'>●</span>" if ok else "<span style='color:#B06000;'>○</span>"
                state_txt = "Ready" if ok else "Setup needed"
                chips.append(f"{dot} <b>{label}</b>: {state_txt}")
            self.lbl_readiness_items.setText(" &nbsp;·&nbsp; ".join(chips))
            if report.get("overall_ready"):
                self.lbl_readiness_title.setText("System Check Complete — 100% Local AI Ready")
            else:
                missing = ", ".join(report.get("missing_components") or [])
                self.lbl_readiness_title.setText(
                    f"System Check — Optional/Missing Components: {missing or 'Check Settings → Models'}"
                )
        except Exception as exc:
            self.lbl_readiness_items.setText(f"Readiness check unavailable: {exc}")

    def _build_step_setup(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(12)

        eyebrow = QLabel("STEP 2 · PERSONALIZE YOUR WORKSPACE")
        eyebrow.setObjectName("onbEyebrow")
        lay.addWidget(eyebrow)

        t = QLabel("Make VOX <i>feel at home.</i>")
        t.setObjectName("onbStepTitle")
        t.setTextFormat(Qt.TextFormat.RichText)
        t.setFont(get_ui_font(26, 500, serif=True))
        lay.addWidget(t)

        sub = QLabel("Choose your palette, where the Flow Bar rests, and confirm your active microphone.")
        sub.setObjectName("mutedLabel")
        lay.addWidget(sub)

        # Appearance Selector Card
        theme_card = QFrame()
        theme_card.setObjectName("voxCard")
        tc_lay = QVBoxLayout(theme_card)
        tc_lay.setContentsMargins(18, 14, 18, 14)
        tc_lay.setSpacing(10)

        tc_hdr = QLabel("Appearance & Palette")
        tc_hdr.setFont(get_ui_font(13.5, 600))
        tc_lay.addWidget(tc_hdr)

        th_row = QHBoxLayout()
        th_row.setSpacing(10)

        self.btn_theme_light = QPushButton("☀  Sand Light (Warm Editorial Cream)")
        self.btn_theme_light.setCheckable(True)
        self.btn_theme_light.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_light.clicked.connect(lambda: self._pick_theme("light"))

        self.btn_theme_dark = QPushButton("☾  Vast Dark (Obsidian Charcoal)")
        self.btn_theme_dark.setCheckable(True)
        self.btn_theme_dark.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_dark.clicked.connect(lambda: self._pick_theme("dark"))

        th_row.addWidget(self.btn_theme_light, 1)
        th_row.addWidget(self.btn_theme_dark, 1)
        tc_lay.addLayout(th_row)
        lay.addWidget(theme_card)

        # FlowBar Position Card
        pos_card = QFrame()
        pos_card.setObjectName("voxCard")
        pos_lay = QHBoxLayout(pos_card)
        pos_lay.setContentsMargins(18, 13, 18, 13)
        pos_lay.setSpacing(12)

        pos_txt = QVBoxLayout()
        pos_txt.setSpacing(2)
        pos_title = QLabel("Floating Flow Bar Dock")
        pos_title.setFont(get_ui_font(13.5, 600))
        pos_desc = QLabel("Screen edge where the compact capsule sits during dictation.")
        pos_desc.setObjectName("mutedLabel")
        pos_txt.addWidget(pos_title)
        pos_txt.addWidget(pos_desc)
        pos_lay.addLayout(pos_txt, 1)

        self.combo_edge = QComboBox()
        self.combo_edge.setMinimumWidth(200)
        self.combo_edge.addItem("Bottom Center (Recommended)", "bottom")
        self.combo_edge.addItem("Top Center", "top")
        self.combo_edge.addItem("Right Edge", "right")
        self.combo_edge.addItem("Left Edge", "left")
        cur_edge = str(getattr(self.controller.config, "flow_bar_edge", "bottom") or "bottom")
        idx_e = max(0, self.combo_edge.findData(cur_edge))
        self.combo_edge.setCurrentIndex(idx_e)
        self.combo_edge.currentIndexChanged.connect(self._on_edge_changed)
        pos_lay.addWidget(self.combo_edge)
        lay.addWidget(pos_card)

        # Microphone Selection & Interactive Live Signal Test
        mic_card = QFrame()
        mic_card.setObjectName("voxCard")
        mc_lay = QVBoxLayout(mic_card)
        mc_lay.setContentsMargins(18, 14, 18, 14)
        mc_lay.setSpacing(10)

        mc_top = QHBoxLayout()
        mc_top.setSpacing(10)
        mc_title = QLabel("Microphone Input")
        mc_title.setFont(get_ui_font(13.5, 600))
        mc_top.addWidget(mc_title)
        mc_top.addStretch()

        self.combo_mic = QComboBox()
        self.combo_mic.setMinimumWidth(280)
        self.combo_mic.addItem("System Default Microphone", None)
        try:
            for d in AudioDeviceManager.list_input_devices():
                tag = " (Default)" if d.is_default else ""
                self.combo_mic.addItem(f"{d.name}{tag}", (d.index, d.name))
        except Exception:
            pass
        cur_mic_name = str(getattr(self.controller.config, "mic_device_name", "") or "")
        for i in range(1, self.combo_mic.count()):
            data = self.combo_mic.itemData(i)
            if isinstance(data, tuple) and cur_mic_name and cur_mic_name in data[1]:
                self.combo_mic.setCurrentIndex(i)
                break
        self.combo_mic.currentIndexChanged.connect(self._on_mic_changed)
        mc_top.addWidget(self.combo_mic)

        self.btn_test_mic = QPushButton("Test Mic")
        self.btn_test_mic.setObjectName("fathomBtn")
        self.btn_test_mic.setCheckable(True)
        self.btn_test_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test_mic.clicked.connect(self._toggle_mic_probe)
        mc_top.addWidget(self.btn_test_mic)
        mc_lay.addLayout(mc_top)

        meter_row = QHBoxLayout()
        meter_row.setSpacing(12)
        self.mic_eq = MultiChannelEqualizerWidget(channels=10)
        meter_row.addWidget(self.mic_eq)

        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setValue(0)
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(8)
        meter_row.addWidget(self.mic_meter, 1)

        self.lbl_mic_status = QLabel("Click Test Mic to check your signal level")
        self.lbl_mic_status.setObjectName("mutedLabel")
        meter_row.addWidget(self.lbl_mic_status)
        mc_lay.addLayout(meter_row)

        lay.addWidget(mic_card)
        lay.addStretch()
        return page

    def _build_step_shortcuts(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(12)

        eyebrow = QLabel("STEP 3 · KEYBOARD & LANGUAGE PREFERENCES")
        eyebrow.setObjectName("onbEyebrow")
        lay.addWidget(eyebrow)

        t = QLabel("One shortcut, <i>infinite speed.</i>")
        t.setObjectName("onbStepTitle")
        t.setTextFormat(Qt.TextFormat.RichText)
        t.setFont(get_ui_font(26, 500, serif=True))
        lay.addWidget(t)

        sub = QLabel(
            "Trigger VOX from any window, email, or chat — without switching apps or touching the mouse."
        )
        sub.setObjectName("mutedLabel")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # Unified structured settings card matching native Wispr Flow preferences
        pref_card = QFrame()
        pref_card.setObjectName("voxCard")
        pl = QVBoxLayout(pref_card)
        pl.setContentsMargins(20, 16, 20, 16)
        pl.setSpacing(14)

        # Row 1: Primary Push-to-Talk Hotkey + Keycaps
        r1 = QHBoxLayout()
        r1.setSpacing(12)
        r1_txt = QVBoxLayout()
        r1_txt.setSpacing(2)
        r1_t = QLabel("Primary Dictation Shortcut")
        r1_t.setFont(get_ui_font(14, 600))
        r1_d = QLabel("Hold to dictate anywhere, or tap to toggle if hands-free mode is active.")
        r1_d.setObjectName("mutedLabel")
        r1_d.setWordWrap(True)
        r1_txt.addWidget(r1_t)
        r1_txt.addWidget(r1_d)
        r1.addLayout(r1_txt, 1)

        hotkey_str = str(getattr(self.controller.config, "ptt_hotkey", "ctrl+win") or "ctrl+win")
        keys_row = QHBoxLayout()
        keys_row.setSpacing(5)
        parts = [p.strip().title() for p in hotkey_str.split("+") if p.strip()] or ["Ctrl", "Win"]
        for idx, kpart in enumerate(parts):
            if idx > 0:
                plus = QLabel("+")
                plus.setObjectName("mutedLabel")
                keys_row.addWidget(plus)
            cap = QLabel(kpart)
            cap.setObjectName("keycapBadge")
            keys_row.addWidget(cap)
        r1.addLayout(keys_row)
        pl.addLayout(r1)

        div1 = QFrame()
        div1.setFrameShape(QFrame.Shape.HLine)
        div1.setFixedHeight(1)
        div1.setObjectName("voxWellCard")
        pl.addWidget(div1)

        # Row 2: Double-tap hands-free toggle
        r2 = QHBoxLayout()
        r2.setSpacing(12)
        r2_txt = QVBoxLayout()
        r2_txt.setSpacing(2)
        r2_t = QLabel("Hands-Free Double-Tap")
        r2_t.setFont(get_ui_font(14, 600))
        r2_d = QLabel("Quickly tap your shortcut twice to lock continuous dictation without holding any keys.")
        r2_d.setObjectName("mutedLabel")
        r2_d.setWordWrap(True)
        r2_txt.addWidget(r2_t)
        r2_txt.addWidget(r2_d)
        r2.addLayout(r2_txt, 1)

        self.chk_double_tap = VoxToggleSwitch("", theme=self._theme)
        self.chk_double_tap.setChecked(bool(getattr(self.controller.config, "hands_free_double_tap", True)))
        self.chk_double_tap.toggled.connect(lambda v: self.controller.update_setting("hands_free_double_tap", bool(v)))
        r2.addWidget(self.chk_double_tap)
        pl.addLayout(r2)

        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setFixedHeight(1)
        div2.setObjectName("voxWellCard")
        pl.addWidget(div2)

        # Row 3: Bilingual English + Hinglish Romanized toggle
        r3 = QHBoxLayout()
        r3.setSpacing(12)
        r3_txt = QVBoxLayout()
        r3_txt.setSpacing(2)
        r3_t = QLabel("English + Hinglish (Romanized) Mixing")
        r3_t.setFont(get_ui_font(14, 600))
        r3_d = QLabel("Naturally blend English and Hindi phrases into clean Romanized text automatically.")
        r3_d.setObjectName("mutedLabel")
        r3_d.setWordWrap(True)
        r3_txt.addWidget(r3_t)
        r3_txt.addWidget(r3_d)
        r3.addLayout(r3_txt, 1)

        self.chk_hinglish = VoxToggleSwitch("", theme=self._theme)
        self.chk_hinglish.setChecked(bool(getattr(self.controller.config, "hinglish_enabled", True)))
        self.chk_hinglish.toggled.connect(lambda v: self.controller.update_setting("hinglish_enabled", bool(v)))
        r3.addWidget(self.chk_hinglish)
        pl.addLayout(r3)

        lay.addWidget(pref_card)

        # Spoken Voice Commands Hint Strip
        cmd_banner = QFrame()
        cmd_banner.setObjectName("dawnBanner")
        cb_lay = QHBoxLayout(cmd_banner)
        cb_lay.setContentsMargins(16, 11, 16, 11)
        cb_lay.setSpacing(10)
        cb_lbl = QLabel("Built-in spoken commands:")
        cb_lbl.setFont(get_ui_font(12.5, 600))
        cb_lay.addWidget(cb_lbl)
        for cmd_name in ("\"new line\"", "\"delete that\"", "\"stop listening\""):
            cbadge = QLabel(cmd_name)
            cbadge.setObjectName("keycapBadge")
            cb_lay.addWidget(cbadge)
        cb_lay.addStretch()
        lay.addWidget(cmd_banner)

        lay.addStretch()
        return page

    def _build_step_try_it_out(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(10)

        eyebrow = QLabel("STEP 4 · INTERACTIVE SANDBOX")
        eyebrow.setObjectName("onbEyebrow")
        lay.addWidget(eyebrow)

        t = QLabel("Try VOX out <i>right here.</i>")
        t.setObjectName("onbStepTitle")
        t.setTextFormat(Qt.TextFormat.RichText)
        t.setFont(get_ui_font(26, 500, serif=True))
        lay.addWidget(t)

        # Lumen hint card with exact matching button copy
        hint = QFrame()
        hint.setObjectName("lumenBanner")
        hl = QVBoxLayout(hint)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(6)
        self.lbl_tiy_hint = QLabel(
            "Click <b>Start Live Voice Test</b> below (or press Ctrl+Win while focused inside the box) and speak naturally — "
            "try pausing or self-correcting mid-sentence like <i>\"let's meet Tuesday, actually Wednesday at 4 PM\"</i>."
        )
        self.lbl_tiy_hint.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_tiy_hint.setWordWrap(True)
        hl.addWidget(self.lbl_tiy_hint)

        # Suggestion chips so user knows exactly what fun phrases to try speaking
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        chips_lbl = QLabel("Ideas to read aloud:")
        chips_lbl.setObjectName("mutedLabel")
        chips_row.addWidget(chips_lbl)
        for label, phrase in _TRY_IT_PROMPTS:
            chip = QPushButton(label)
            chip.setObjectName("ghostBtn")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet("font-size: 11.5px; padding: 3px 9px; border-radius: 10px;")
            chip.clicked.connect(
                lambda _c=False, ph=phrase: self.lbl_tiy_status.setText(f"Try saying: “{ph}”")
            )
            chips_row.addWidget(chip)
        chips_row.addStretch()
        hl.addLayout(chips_row)
        lay.addWidget(hint)

        self.tiy_editor = QPlainTextEdit()
        self.tiy_editor.setPlaceholderText(
            "Your live dictated text will stream right here — no window switching needed..."
        )
        self.tiy_editor.setFont(get_ui_font(14.5, 400))
        self.tiy_editor.textChanged.connect(
            lambda: self.btn_tiy_clear.setEnabled(bool(self.tiy_editor.toPlainText().strip()))
        )
        lay.addWidget(self.tiy_editor, 1)

        # Integrated Wispr-style Live Voice Control Strip
        ctrl_bar = QFrame()
        ctrl_bar.setObjectName("voxWellCard")
        cb = QHBoxLayout(ctrl_bar)
        cb.setContentsMargins(12, 10, 12, 10)
        cb.setSpacing(10)

        self.tiy_wave_pill = QFrame()
        self.tiy_wave_pill.setFixedSize(56, 30)
        self.tiy_wave_pill.setStyleSheet(
            "background-color: #000000; border: 1px solid #30302F; border-radius: 15px;"
        )
        wp_lay = QHBoxLayout(self.tiy_wave_pill)
        wp_lay.setContentsMargins(8, 5, 8, 5)
        self.tiy_waveform = DynamicWaveformCapsule(bar_count=10)
        wp_lay.addWidget(self.tiy_waveform)
        cb.addWidget(self.tiy_wave_pill)

        self.lbl_tiy_status = QLabel("Ready — click Start Live Voice Test to speak")
        self.lbl_tiy_status.setObjectName("mutedLabel")
        self.lbl_tiy_status.setWordWrap(True)
        cb.addWidget(self.lbl_tiy_status, 1)

        self.btn_tiy_clear = QPushButton("Clear")
        self.btn_tiy_clear.setObjectName("ghostBtn")
        self.btn_tiy_clear.setEnabled(False)
        self.btn_tiy_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tiy_clear.clicked.connect(self._clear_tiy_editor)
        cb.addWidget(self.btn_tiy_clear)

        self.btn_tiy_demo = QPushButton("Run Self-Correction Demo")
        self.btn_tiy_demo.setObjectName("dawnBtn")
        self.btn_tiy_demo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tiy_demo.clicked.connect(self._run_sample_preview)
        cb.addWidget(self.btn_tiy_demo)

        self.btn_tiy_speak = QPushButton("🎙  Start Live Voice Test")
        self.btn_tiy_speak.setObjectName("fathomBtn")
        self.btn_tiy_speak.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tiy_speak.clicked.connect(self._trigger_tiy_voice)
        cb.addWidget(self.btn_tiy_speak)

        lay.addWidget(ctrl_bar)
        return page

    # ------------------------------------------------------------------
    # Signal Wiring & Lifecycle Cleanup
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._mic_probe_level.connect(self._on_mic_level)
        self._mic_probe_error.connect(self._on_mic_probe_error)
        if not self.controller:
            return
        if hasattr(self.controller, "dictation_level_changed"):
            self.controller.dictation_level_changed.connect(self._on_mic_level)
        if hasattr(self.controller, "dictation_completed"):
            self.controller.dictation_completed.connect(self._on_tiy_dictation_completed)
        if hasattr(self.controller, "dictation_state_changed"):
            self.controller.dictation_state_changed.connect(self._on_dictation_state_changed)
        if hasattr(self.controller, "dictation_interim_updated"):
            self.controller.dictation_interim_updated.connect(self._on_dictation_interim_updated)

    def _stop_active_sessions(self) -> None:
        self._stop_mic_probe()
        if self.controller:
            if hasattr(self.controller, "clear_in_app_dictation_target"):
                self.controller.clear_in_app_dictation_target(self.tiy_editor)
            cur_state = getattr(self.controller, "_dictation_state", "idle")
            if cur_state in ("starting", "listening", "speaking") and hasattr(
                self.controller, "cancel_dictation"
            ):
                self.controller.cancel_dictation()

    def hideEvent(self, event) -> None:
        self._stop_active_sessions()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._stop_active_sessions()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_active_sessions()
        super().reject()

    # ------------------------------------------------------------------
    # Microphone Hardware Probe (Step 2)
    # ------------------------------------------------------------------
    def _toggle_mic_probe(self, checked: bool) -> None:
        if checked:
            self._start_mic_probe()
        else:
            self._stop_mic_probe()

    def _start_mic_probe(self) -> None:
        if self._mic_probe_thread and self._mic_probe_thread.is_alive():
            return
        if getattr(self.controller, "_dictation_state", "idle") in ("starting", "listening", "speaking"):
            self.btn_test_mic.setChecked(False)
            self.lbl_mic_status.setText("Stop active dictation first to test microphone.")
            return

        self._mic_probe_stop.clear()
        self.btn_test_mic.setChecked(True)
        self.btn_test_mic.setText("Stop Mic Test")
        self.lbl_mic_status.setText("Listening to microphone — speak a few words...")

        idx = getattr(self.controller.config, "mic_device_index", None) if self.controller else None
        name = str(getattr(self.controller.config, "mic_device_name", "") or "") if self.controller else ""

        self._mic_probe_thread = threading.Thread(
            target=self._mic_probe_loop,
            args=(idx, name),
            name="OnboardingMicProbe",
            daemon=True,
        )
        self._mic_probe_thread.start()

    def _stop_mic_probe(self) -> None:
        self._mic_probe_stop.set()
        if hasattr(self, "btn_test_mic"):
            self.btn_test_mic.setChecked(False)
            self.btn_test_mic.setText("Test Mic")
        if hasattr(self, "mic_meter"):
            self.mic_meter.setValue(0)
        if hasattr(self, "mic_eq"):
            self.mic_eq.set_level(0.0)
        if hasattr(self, "lbl_mic_status"):
            self.lbl_mic_status.setText("Click Test Mic to check your signal level")

    def _mic_probe_loop(self, preferred_index: Optional[int], preferred_name: str) -> None:
        pa = None
        stream = None
        try:
            import pyaudiowpatch as pyaudio

            dev = AudioDeviceManager.resolve_microphone(preferred_index, preferred_name)
            if not dev:
                self._mic_probe_error.emit("No working microphone found in Windows Sound settings.")
                return
            pa = pyaudio.PyAudio()
            rate = max(16000, int(dev.sample_rate or 48000))
            channels = max(1, int(dev.channels or 1))
            chunk = 1024
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=dev.index,
                frames_per_buffer=chunk,
            )
            while not self._mic_probe_stop.is_set():
                data = stream.read(chunk, exception_on_overflow=False)
                pcm = np.frombuffer(data, dtype=np.int16)
                mono = to_mono_f32(pcm, channels)
                lvl = float(compute_rms_level(mono))
                self._mic_probe_level.emit(min(1.0, lvl * 2.2))
                time.sleep(0.01)
        except Exception as exc:
            logger.debug("Mic probe ended/unavailable: %s", exc)
            if not self._mic_probe_stop.is_set():
                self._mic_probe_error.emit(f"Mic preview unavailable: {exc}")
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if pa is not None:
                    pa.terminate()
            except Exception:
                pass

    def _on_mic_probe_error(self, msg: str) -> None:
        self._stop_mic_probe()
        self.lbl_mic_status.setText(msg)

    # ------------------------------------------------------------------
    # Actions & Live Voice Test Handlers
    # ------------------------------------------------------------------
    def _pick_theme(self, theme: str) -> None:
        self._theme = theme
        if hasattr(self.controller, "set_theme"):
            self.controller.set_theme(theme)
        self.apply_theme(theme)

    def _on_edge_changed(self, _idx: int) -> None:
        val = self.combo_edge.currentData()
        if val:
            self.controller.update_setting("flow_bar_edge", str(val))

    def _on_mic_changed(self, _idx: int) -> None:
        data = self.combo_mic.currentData()
        was_probing = bool(self.btn_test_mic.isChecked())
        if was_probing:
            self._stop_mic_probe()
        if isinstance(data, tuple):
            self.controller.update_setting("mic_device_index", data[0])
            self.controller.update_setting("mic_device_name", data[1])
        else:
            self.controller.update_setting("mic_device_index", None)
            self.controller.update_setting("mic_device_name", "")
        if was_probing:
            QTimer.singleShot(120, self._start_mic_probe)

    def _on_mic_level(self, lvl: float) -> None:
        val = int(min(100.0, max(0.0, float(lvl)) * 140.0))
        self.mic_meter.setValue(val)
        if hasattr(self, "mic_eq"):
            self.mic_eq.set_level(float(lvl))
        if hasattr(self, "tiy_waveform") and self.stack.currentIndex() == 3:
            self.tiy_waveform.set_level(float(lvl))
        if self._mic_probe_thread and self._mic_probe_thread.is_alive() and self.stack.currentIndex() == 1:
            if val >= 20:
                self.lbl_mic_status.setText("Good signal detected — microphone is ready ✓")

    def _clear_tiy_editor(self) -> None:
        self.tiy_editor.clear()
        self.tiy_editor.setFocus()
        self.lbl_tiy_status.setText("Ready — click Start Live Voice Test to speak")

    def _trigger_tiy_voice(self) -> None:
        self._stop_mic_probe()
        self.tiy_editor.setFocus()
        if hasattr(self.controller, "toggle_in_app_dictation"):
            state = getattr(self.controller, "_dictation_state", "idle")
            if state in ("idle", "completed", "failed"):
                self._tiy_received_text = False
            self.controller.toggle_in_app_dictation(self.tiy_editor, "VOX Onboarding — Try It Out")
        else:
            self.controller.toggle_dictation()

    def _on_dictation_state_changed(self, state: str, msg: str) -> None:
        self._tiy_active_state = state
        if self.stack.currentIndex() != 3:
            return

        if state == "starting":
            self.btn_tiy_speak.setText("Starting Mic...")
            self.btn_tiy_speak.setEnabled(False)
            self.tiy_waveform.set_processing(True)
            self.lbl_tiy_status.setText(msg or "Warming up microphone & local speech model...")
        elif state in ("listening", "speaking"):
            self._set_speak_button_role("startBtn", "⏹  Stop && Insert", enabled=True)
            self.tiy_waveform.set_processing(False)
            self.lbl_tiy_status.setText(
                "Speaking detected — keep talking..."
                if state == "speaking"
                else "Listening — speak naturally, then click Stop & Insert"
            )
        elif state in ("stopping", "processing"):
            self._set_speak_button_role("fathomBtn", "Transcribing...", enabled=False)
            self.tiy_waveform.set_processing(True)
            self.lbl_tiy_status.setText(msg or "Transcribing & formatting locally...")
        elif state == "completed":
            self._set_speak_button_role("fathomBtn", "🎙  Start Live Voice Test", enabled=True)
            self.tiy_waveform.set_processing(False)
            self.tiy_waveform.set_level(0.0)
            self.lbl_tiy_status.setText("✓ " + (msg or "Inserted speech above — click again to add more"))
        elif state == "failed":
            self._set_speak_button_role("fathomBtn", "🎙  Start Live Voice Test", enabled=True)
            self.tiy_waveform.set_processing(False)
            self.tiy_waveform.set_level(0.0)
            self.lbl_tiy_status.setText(f"⚠ {msg or 'Microphone unavailable'}")
        else:  # idle
            self._set_speak_button_role("fathomBtn", "🎙  Start Live Voice Test", enabled=True)
            self.tiy_waveform.set_processing(False)
            self.tiy_waveform.set_level(0.0)
            if "No speech detected" in (msg or "") and not self._tiy_received_text:
                self.lbl_tiy_status.setText(
                    "No speech detected — speak closer to your mic or try the sample button."
                )
            elif self._tiy_received_text:
                self.lbl_tiy_status.setText("✓ Ready — click Start Live Voice Test to continue dictating")
            else:
                self.lbl_tiy_status.setText("Ready — click Start Live Voice Test to speak")

    def _set_speak_button_role(self, object_name: str, text: str, enabled: bool = True) -> None:
        self.btn_tiy_speak.setText(text)
        self.btn_tiy_speak.setEnabled(enabled)
        if self.btn_tiy_speak.objectName() != object_name:
            self.btn_tiy_speak.setObjectName(object_name)
            st = self.btn_tiy_speak.style()
            if st is not None:
                st.unpolish(self.btn_tiy_speak)
                st.polish(self.btn_tiy_speak)
            self.btn_tiy_speak.update()

    def _on_dictation_interim_updated(self, provisional: str, stable: str) -> None:
        if self.stack.currentIndex() != 3:
            return
        combined = f"{stable} {provisional}".strip()
        if combined:
            snippet = combined if len(combined) <= 72 else "..." + combined[-69:]
            self.lbl_tiy_status.setText(f"Hearing live: “{snippet}”")

    def _run_sample_preview(self) -> None:
        sample_raw = (
            "um hey team comma so we should ship the onboarding flow on tuesday "
            "actually wait make that wednesday at 3 pm period new paragraph let's follow up on teams"
        )
        cleaned = DictationProcessor.process(sample_raw, mode="clean")
        self.tiy_editor.setPlainText(cleaned)
        cursor = self.tiy_editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.tiy_editor.setTextCursor(cursor)
        self.tiy_editor.setFocus()
        self.btn_tiy_clear.setEnabled(True)
        self.lbl_tiy_status.setText("Sample self-corrected: removed 'um' and changed Tuesday → Wednesday.")

    def _on_tiy_dictation_completed(self, text: str, *_args) -> None:
        if self.stack.currentIndex() == 3 and text:
            self._tiy_received_text = True
            cursor = self.tiy_editor.textCursor()
            existing = self.tiy_editor.toPlainText()
            pos = cursor.position()
            if text == "\n":
                to_insert = "\n"
            elif existing and pos > 0 and not existing[pos - 1].isspace():
                to_insert = " " + text.lstrip()
            else:
                to_insert = text.lstrip() if existing and pos > 0 and existing[pos - 1].isspace() else text
            cursor.insertText(to_insert)
            self.tiy_editor.setTextCursor(cursor)
            self.tiy_editor.ensureCursorVisible()
            self.tiy_editor.setFocus()
            self.btn_tiy_clear.setEnabled(True)
            self.lbl_tiy_status.setText("✓ Live speech inserted cleanly above")

    # ------------------------------------------------------------------
    # Navigation & Theme
    # ------------------------------------------------------------------
    def _go_to_step(self, idx: int) -> None:
        idx = max(0, min(3, idx))
        if idx != 1:
            self._stop_mic_probe()
        if self.controller and hasattr(self.controller, "set_in_app_dictation_target"):
            if idx == 3:
                self.controller.set_in_app_dictation_target(self.tiy_editor, "VOX Onboarding — Try It Out")
            else:
                self.controller.clear_in_app_dictation_target(self.tiy_editor)
                cur_state = getattr(self.controller, "_dictation_state", "idle")
                if cur_state in ("starting", "listening", "speaking") and hasattr(
                    self.controller, "cancel_dictation"
                ):
                    self.controller.cancel_dictation()

        self.stack.setCurrentIndex(idx)
        self.progress_bar.setValue(idx + 1)
        self.lbl_step.setText(f"Step {idx + 1} of 4")
        self.btn_back.setVisible(idx > 0)
        if idx == 0:
            self.btn_next.setText("Get Started  →")
        elif idx < 3:
            self.btn_next.setText("Continue  →")
        else:
            self.btn_next.setText("Finish Setup ✓")
            self.tiy_editor.setFocus()
        self._refresh_step_pills()

    def _prev_step(self) -> None:
        self._go_to_step(self.stack.currentIndex() - 1)

    def _next_step(self) -> None:
        idx = self.stack.currentIndex()
        if idx < 3:
            self._go_to_step(idx + 1)
        else:
            self._finish_tour()

    def _finish_tour(self) -> None:
        self._stop_active_sessions()
        if self.controller:
            self.controller.update_setting("first_run_done", True)
            self.controller.update_setting("onboarding_completed", True)
        self.accept()

    def _refresh_step_pills(self) -> None:
        t = get_theme_tokens(self._theme)
        cur = self.stack.currentIndex() if hasattr(self, "stack") else 0
        for i, pill in enumerate(self._step_pills):
            if i == cur:
                pill.setStyleSheet(
                    f"QPushButton {{ background-color: {t['dawn_btn_bg']}; color: {t['dawn_btn_text']};"
                    f" border: 1px solid {t['dawn_btn_border']}; border-radius: 13px;"
                    f" padding: 4px 10px; font-size: 11px; font-weight: 700; }}"
                )
            elif i < cur:
                pill.setStyleSheet(
                    f"QPushButton {{ background-color: {t['accent_fathom_tint']}; color: {t['accent_fathom']};"
                    f" border: 1px solid {t['border']}; border-radius: 13px;"
                    f" padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
                )
            else:
                pill.setStyleSheet(
                    f"QPushButton {{ background-color: transparent; color: {t['text_muted']};"
                    f" border: 1px solid {t['line']}; border-radius: 13px;"
                    f" padding: 4px 10px; font-size: 11px; font-weight: 500; }}"
                )

    def apply_theme(self, theme: str = "light") -> None:
        self._theme = (theme or "light").lower()
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(get_dialog_stylesheet(self._theme))

        if hasattr(self, "brand_mark"):
            self.brand_mark.setPixmap(create_app_pixmap("idle", 28, self._theme))

        if hasattr(self, "mic_eq"):
            self.mic_eq.set_color(t["accent_fathom"])

        if hasattr(self, "chk_double_tap") and hasattr(self.chk_double_tap, "set_theme"):
            self.chk_double_tap.set_theme(self._theme)
        if hasattr(self, "chk_hinglish") and hasattr(self.chk_hinglish, "set_theme"):
            self.chk_hinglish.set_theme(self._theme)

        if hasattr(self, "btn_theme_light") and hasattr(self, "btn_theme_dark"):
            self.btn_theme_light.setChecked(self._theme == "light")
            self.btn_theme_dark.setChecked(self._theme == "dark")
            for btn, active in (
                (self.btn_theme_light, self._theme == "light"),
                (self.btn_theme_dark, self._theme == "dark"),
            ):
                if active:
                    btn.setStyleSheet(
                        f"QPushButton {{ background-color: {t['accent_dawn_tint']}; color: {t['text']};"
                        f" border: 2px solid {t['accent_fathom']}; border-radius: 10px;"
                        f" padding: 11px 14px; font-weight: 700; text-align: left; }}"
                    )
                else:
                    btn.setStyleSheet(
                        f"QPushButton {{ background-color: {t['surface_well']}; color: {t['text_muted']};"
                        f" border: 1px solid {t['line']}; border-radius: 10px;"
                        f" padding: 11px 14px; font-weight: 500; text-align: left; }}"
                    )
        self._refresh_step_pills()

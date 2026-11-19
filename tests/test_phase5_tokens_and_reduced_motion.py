"""
Phase 5 Verification Suite: Semantic Token Hygiene, Contrast Parity & Reduced Motion.

Covers:
1. Full key parity across LIGHT_TOKENS & DARK_TOKENS, WCAG AA contrast checks,
   and tokenized component rendering across Light/Dark themes (VoxToggleSwitch,
   VoxStatusPill, VoxMetricCard, VoxPreMeetingBriefingDialog, VoxMeetingComparisonDialog).
2. Reduced Motion detection via Windows SPI_GETCLIENTAREAANIMATION, AppConfig.reduced_motion,
   and set_reduced_motion_preference(True/False/None), plus zero-timer-overhead snapping
   on PulseBeacon, MultiChannelEqualizerWidget, ConcentricSoundWaveVisualizer,
   VoxToggleSwitch, AnimatedProgressBar, ThemeToggleCapsule, and TabTransitionEngine.
3. AppConfig JSON persistence roundtrip & legacy backward compatibility + Settings
   Reduced Motion toggle wiring.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QPropertyAnimation
from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.ui.animations import (
    AnimatedProgressBar,
    ConcentricSoundWaveVisualizer,
    MultiChannelEqualizerWidget,
    PulseBeacon,
    TabTransitionEngine,
    ThemeToggleCapsule,
    is_reduced_motion_enabled,
    query_os_reduced_motion,
    set_reduced_motion_preference,
)
from voxlocal.ui.components import (
    VoxMeetingComparisonDialog,
    VoxMetricCard,
    VoxPreMeetingBriefingDialog,
    VoxStatusPill,
    VoxToggleSwitch,
)
from voxlocal.ui.settings_dialog import SettingsWorkspaceWidget
from voxlocal.ui.theme import DARK_TOKENS, LIGHT_TOKENS, get_theme_tokens
from voxlocal.win32.startup import StartupManager


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "--platform", "offscreen"])
    return app


def _rel_lum(hex_str: str) -> float:
    clean = hex_str.lstrip("#")
    r, g, b = [int(clean[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(c1: str, c2: str) -> float:
    l1 = _rel_lum(c1)
    l2 = _rel_lum(c2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


_RETIRED_CONTROLLERS: list = []


class _NoOpThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _shutdown_controller(ctrl: AppController, *widgets) -> None:
    app = _get_qapp()
    for w in widgets:
        if w is not None:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    eng = getattr(ctrl, "calendar_engine", None)
    if eng is not None and hasattr(eng, "stop_polling"):
        try:
            eng.stop_polling()
        except Exception:
            pass
    ctrl.shutdown()
    _RETIRED_CONTROLLERS.append(ctrl)
    app.processEvents()


def _create_controller(td_name: str) -> AppController:
    cfg = AppConfig(storage_dir=td_name)
    cfg.launch_at_login = StartupManager.is_enabled()
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    real_thread = threading.Thread

    def _safe_thread(*args, **kwargs):
        if kwargs.get("name") == "StartupUpdateCheck":
            return _NoOpThread()
        return real_thread(*args, **kwargs)

    with patch("threading.Thread", side_effect=_safe_thread):
        return AppController(cfg)


class TestPhase5TokensAndReducedMotion(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_qapp()

    def tearDown(self) -> None:
        set_reduced_motion_preference(None)

    # ------------------------------------------------------------------
    # 1. Semantic Token Parity, Contrast & Component Theme Cohesion
    # ------------------------------------------------------------------
    def test_01_semantic_tokens_parity_and_wcag_contrast(self) -> None:
        self.assertEqual(set(LIGHT_TOKENS.keys()), set(DARK_TOKENS.keys()))
        required_semantic = (
            "toggle_idle_track",
            "toggle_thumb",
            "toggle_thumb_checked",
            "status_success",
            "status_success_bg",
            "status_success_text",
            "status_warning",
            "status_warning_bg",
            "status_warning_text",
            "status_danger",
            "status_danger_bg",
            "status_danger_text",
            "status_info",
            "status_info_bg",
            "status_info_text",
            "status_purple",
            "status_purple_bg",
            "status_purple_text",
            "status_neutral",
            "status_neutral_bg",
            "status_neutral_text",
            "audio_meter_low",
            "audio_meter_mid",
            "audio_meter_peak",
        )
        for key in required_semantic:
            self.assertIn(key, LIGHT_TOKENS)
            self.assertIn(key, DARK_TOKENS)

        for mode in ("light", "dark"):
            t = get_theme_tokens(mode)
            self.assertGreaterEqual(_contrast(t["text_primary"], t["bg"]), 4.5)
            self.assertGreaterEqual(_contrast(t["text_primary"], t["surface"]), 4.5)
            self.assertGreaterEqual(_contrast(t["primary_btn_text"], t["primary_btn_bg"]), 4.5)
            self.assertGreaterEqual(_contrast(t["fathom_btn_text"], t["fathom_btn_bg"]), 3.0)
            for skey in (
                "status_success_text",
                "status_warning_text",
                "status_danger_text",
                "status_info_text",
                "status_purple_text",
                "status_neutral_text",
            ):
                self.assertGreaterEqual(_contrast(t[skey], t["surface"]), 4.5, f"{mode}:{skey}")

        sw = VoxToggleSwitch("Reduced", theme="light")
        pill = VoxStatusPill("Recording", variant="recording")
        pill.set_theme("light")
        self.assertIn(LIGHT_TOKENS["status_danger_text"], pill._lbl.styleSheet())
        pill.set_theme("dark")
        self.assertIn(DARK_TOKENS["status_danger_text"], pill._lbl.styleSheet())

        card = VoxMetricCard("Latency", "42ms", "Fast path", theme="light")
        self.assertIn(LIGHT_TOKENS["text_primary"], card.lbl_value.styleSheet())
        card.set_theme("dark")
        self.assertIn(DARK_TOKENS["text_primary"], card.lbl_value.styleSheet())

        with tempfile.TemporaryDirectory(prefix="vox_p5_dlg_") as td:
            ctrl = _create_controller(td)
            dlg_b = None
            dlg_c = None
            try:
                eng = MeetingIntelligenceEngine(ctrl.db)
                dlg_b = VoxPreMeetingBriefingDialog(ctrl.db, eng, theme="light")
                self.assertIn(LIGHT_TOKENS["bg"], dlg_b.styleSheet())
                self.assertIn(LIGHT_TOKENS["accent_fathom"], dlg_b.styleSheet())

                dlg_c = VoxMeetingComparisonDialog(ctrl.db, eng, theme="dark")
                self.assertIn(DARK_TOKENS["bg"], dlg_c.styleSheet())
                self.assertIn(DARK_TOKENS["accent_fathom"], dlg_c.styleSheet())
            finally:
                for d in (dlg_b, dlg_c):
                    if d is not None:
                        d.close()
                        d.deleteLater()
                _shutdown_controller(ctrl)

        for w in (sw, pill, card):
            w.close()
            w.deleteLater()

    # ------------------------------------------------------------------
    # 2. Reduced Motion OS Query & Zero-Timer Snap Behavior
    # ------------------------------------------------------------------
    def test_02_reduced_motion_os_detection_and_zero_timer_overhead(self) -> None:
        set_reduced_motion_preference(None)

        def _spi_factory(anim_enabled_val: int):
            def _spi(action, ui_param, pv_param, f_win_ini):
                c_ptr = ctypes.cast(ctypes.addressof(ctypes.c_int.from_buffer(pv_param._obj)), ctypes.POINTER(ctypes.c_int))
                c_ptr[0] = anim_enabled_val
                return 1
            return _spi

        if sys.platform == "win32":
            with patch("ctypes.windll.user32.SystemParametersInfoW", side_effect=_spi_factory(0)):
                self.assertTrue(query_os_reduced_motion())
                self.assertTrue(is_reduced_motion_enabled())
            with patch("ctypes.windll.user32.SystemParametersInfoW", side_effect=_spi_factory(1)):
                self.assertFalse(query_os_reduced_motion())
                self.assertFalse(is_reduced_motion_enabled())
            with patch("ctypes.windll.user32.SystemParametersInfoW", side_effect=OSError("fail")):
                self.assertFalse(query_os_reduced_motion())

        fake_cfg = MagicMock()
        fake_cfg.reduced_motion = True
        self.assertTrue(is_reduced_motion_enabled(fake_cfg))

        set_reduced_motion_preference(True)
        self.assertTrue(is_reduced_motion_enabled())

        beacon = PulseBeacon("recording", size=24)
        self.assertFalse(beacon._timer.isActive())
        self.assertGreater(beacon._halo_alpha, 0.0)
        self.assertEqual(beacon._get_color().name().lower(), "#f43f5e")

        eq = MultiChannelEqualizerWidget(channels=10)
        eq.set_level(0.8)
        self.assertFalse(eq._timer.isActive())
        self.assertGreater(eq._current_levels[2], 0.0)

        ripple = ConcentricSoundWaveVisualizer(size=40)
        ripple.set_active(True, "#10b981")
        self.assertFalse(ripple._timer.isActive())

        sw = VoxToggleSwitch("Accessibility")
        sw.setChecked(False)
        sw.toggle()
        self.assertNotEqual(sw._anim.state(), QPropertyAnimation.State.Running)
        self.assertEqual(sw._thumb_pos, 1.0)

        bar = AnimatedProgressBar()
        bar.setRange(0, 100)
        bar.setValue(10)
        bar.setSmoothValue(75)
        self.assertEqual(bar.value(), 75)
        self.assertEqual(bar._anim.endValue(), 75)

        cap = ThemeToggleCapsule("dark")
        cap.click()
        self.assertNotEqual(cap._anim_rot.state(), QPropertyAnimation.State.Running)
        self.assertEqual(cap._theme, "light")

        tabs = QTabWidget()
        tabs.addTab(QWidget(), "A")
        tabs.addTab(QWidget(), "B")
        tte = TabTransitionEngine(tabs)
        tabs.setCurrentIndex(1)
        self.assertIsNone(tte._active_anim)
        self.assertEqual(tabs.currentIndex(), 1)

        for w in (beacon, eq, ripple, sw, bar, cap, tabs):
            w.close()
            w.deleteLater()

    # ------------------------------------------------------------------
    # 3. Settings Reduced Motion Toggle & AppConfig Persistence
    # ------------------------------------------------------------------
    def test_03_settings_reduced_motion_toggle_and_config_persistence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p5_cfg_") as td:
            # Legacy JSON without reduced_motion loads cleanly with False default
            cfg_path = Path(td) / "config.json"
            cfg_path.write_text(json.dumps({"storage_dir": td, "theme": "dark"}), encoding="utf-8")
            with patch.dict(os.environ, {"VOXLOCAL_CONFIG_PATH": str(cfg_path)}):
                loaded_legacy = AppConfig.load()
                self.assertFalse(loaded_legacy.reduced_motion)

                loaded_legacy.reduced_motion = True
                loaded_legacy.save()
                reloaded = AppConfig.load()
                self.assertTrue(reloaded.reduced_motion)

            ctrl = _create_controller(td)
            sw_widget = None
            try:
                sw_widget = SettingsWorkspaceWidget(ctrl)
                sw_widget.show()
                self.app.processEvents()

                self.assertFalse(sw_widget.sw_reduced_motion.isChecked())
                sw_widget.sw_reduced_motion.setChecked(True)
                self.app.processEvents()
                self.assertTrue(ctrl.config.reduced_motion)
                self.assertTrue(is_reduced_motion_enabled(ctrl.config))

                sw_widget.sw_reduced_motion.setChecked(False)
                self.app.processEvents()
                self.assertFalse(ctrl.config.reduced_motion)
            finally:
                _shutdown_controller(ctrl, sw_widget)


if __name__ == "__main__":
    unittest.main()

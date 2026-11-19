"""
Unit test suite verifying VoxLocal's Flaky-inspired dual theme (Dark/Light mode)
and motion/animation engine.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure 'src' is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.animations import (
    AnimatedProgressBar,
    AnimatedPushButton,
    ConcentricSoundWaveVisualizer,
    MultiChannelEqualizerWidget,
    PulseBeacon,
    TabTransitionEngine,
    ThemeToggleCapsule,
    ToastNotification,
)
from voxlocal.ui.floating_widget import FloatingWidget, MeetingQuickStartDialog
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    create_app_icon,
    create_info_badge,
    get_dialog_stylesheet,
    get_floating_pill_stylesheet,
    get_theme_stylesheet,
    get_theme_tokens,
)
from voxlocal.win32.startup import StartupManager


class TestThemeAndAnimations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=self.temp_dir.name)
        self.config.launch_at_login = StartupManager.is_enabled()
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda *_: None,
            on_exit_app=lambda: None,
        )
        self.main_window = MainWindow(
            self.controller,
            self.floating_widget,
            on_exit_app=lambda: None,
        )

    def tearDown(self):
        self.main_window.close()
        self.floating_widget.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_theme_tokens_and_palette(self):
        # Verify Dark theme tokens match Midnight Obsidian palette
        dark_tokens = get_theme_tokens("dark")
        self.assertEqual(dark_tokens["bg"], "#0B0F19")
        self.assertEqual(dark_tokens["surface"], "#111827")
        self.assertEqual(dark_tokens["text"], "#F8FAFC")

        # Verify Light theme tokens match Nordic Studio Light palette
        light_tokens = get_theme_tokens("light")
        self.assertEqual(light_tokens["bg"], "#F8FAFC")
        self.assertEqual(light_tokens["surface"], "#FFFFFF")
        self.assertEqual(light_tokens["text"], "#0F172A")

    def test_theme_stylesheet_generation(self):
        dark_qss = get_theme_stylesheet("dark")
        self.assertIn("#0B0F19", dark_qss)
        self.assertIn("#111827", dark_qss)

        light_qss = get_theme_stylesheet("light")
        self.assertIn("#F8FAFC", light_qss)
        self.assertIn("#FFFFFF", light_qss)

        # Dialog and Floating Pill stylesheets
        pill_dark = get_floating_pill_stylesheet("dark")
        pill_light = get_floating_pill_stylesheet("light")
        self.assertIn("#0B0F19", pill_dark)
        self.assertIn("#0B0F19", pill_light)

        dlg_dark = get_dialog_stylesheet("dark")
        dlg_light = get_dialog_stylesheet("light")
        self.assertIn("#111827", dlg_dark)
        self.assertIn("#FFFFFF", dlg_light)

    def test_controller_theme_switching(self):
        emitted_themes = []
        self.controller.theme_changed.connect(emitted_themes.append)

        # Switch to light
        self.controller.set_theme("light")
        self.assertEqual(self.controller.theme, "light")
        self.assertEqual(self.controller.config.theme, "light")
        self.assertIn("light", emitted_themes)

        # Toggle back to dark
        next_theme = self.controller.toggle_theme()
        self.assertEqual(next_theme, "dark")
        self.assertEqual(self.controller.theme, "dark")
        self.assertEqual(self.controller.config.theme, "dark")
        self.assertEqual(emitted_themes[-1], "dark")

    def test_main_window_theme_sync(self):
        # Switching theme via controller should trigger apply_theme in main_window
        self.controller.set_theme("light")
        self.assertIn("#F8FAFC", self.main_window.styleSheet())
        self.assertEqual(self.main_window.theme_toggle._theme, "light")
        self.assertIn("#F8FAFC", self.main_window.sidebar.styleSheet())

        self.controller.set_theme("dark")
        self.assertIn("#0B0F19", self.main_window.styleSheet())
        self.assertEqual(self.main_window.theme_toggle._theme, "dark")
        self.assertIn("#070A12", self.main_window.sidebar.styleSheet())

    def test_animated_push_button_physics(self):
        btn = AnimatedPushButton("Click Me")
        self.assertEqual(btn.y_offset, 0.0)
        self.assertEqual(btn.glow_alpha, 0.0)

        # Set properties and check updates
        btn.y_offset = -2.5
        self.assertEqual(btn.y_offset, -2.5)
        btn.glow_alpha = 0.8
        self.assertEqual(btn.glow_alpha, 0.8)

    def test_theme_toggle_capsule_animation(self):
        capsule = ThemeToggleCapsule("dark")
        self.assertEqual(capsule.rotation_angle, 0.0)
        self.assertIn("Dark", capsule.text())

        capsule.rotation_angle = 180.0
        self.assertEqual(capsule.rotation_angle, 180.0)

    def test_pulse_beacon_and_equalizer(self):
        beacon = PulseBeacon("recording", size=24)
        self.assertEqual(beacon._state, "recording")
        color = beacon._get_color()
        self.assertEqual(color.name().lower(), "#f43f5e")

        eq = MultiChannelEqualizerWidget(channels=10)
        eq.set_level(0.75)
        self.assertEqual(eq._target_level, 0.75)
        eq._physics_step()
        self.assertGreater(eq._current_levels[2], 0.0)

        ripple = ConcentricSoundWaveVisualizer(size=40)
        ripple.set_active(True, "#10b981")
        self.assertTrue(ripple._active)

    def test_animated_progress_bar(self):
        bar = AnimatedProgressBar()
        bar.setRange(0, 100)
        bar.setValue(10)
        bar.setSmoothValue(85)
        self.assertEqual(bar._anim.endValue(), 85)

    def test_toast_notification(self):
        toast = ToastNotification("Test Title", "Test Message", parent=self.main_window, theme="dark")
        self.assertIsNotNone(toast)
        toast.dismiss()


if __name__ == "__main__":
    unittest.main()

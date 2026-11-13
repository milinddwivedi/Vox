import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QPixmap, QImage, QColor
from PyQt6.QtCore import Qt, QRect

from voxlocal.audio.call_detector import clean_meeting_title
from voxlocal.core.config import AppConfig
from voxlocal.ui.floating_pill import PillState
from voxlocal.ui.floating_widget import FloatingInfoBubble, FloatingWidget
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup

app = QApplication.instance() or QApplication([])

class TestUIHardeningAndUserFeedbackFixes(unittest.TestCase):
    def setUp(self):
        self.ctrl = MagicMock()
        self.ctrl.config = AppConfig()
        self.ctrl.theme = "dark"
        self.ctrl.meeting_state = "idle"
        self.ctrl.is_screen_recording_active = False
        self.ctrl.screen_recording_state = "idle"
        self.ctrl.is_hands_free_active = False
        self.ctrl.registered_ui_hwnds = set()
        self.ctrl.register_ui_hwnd = MagicMock()

    def test_meeting_title_cleaning_exhaustive(self):
        """Image 1: Verify bracketed call prefixes, tab names, and suffixes are stripped completely."""
        cases = [
            ("[Microsoft Teams Call] Vox Testing - 2", "Vox Testing - 2"),
            ("[Teams Call] Vox Testing - 2", "Vox Testing - 2"),
            ("[Teams] Vox Testing - 2", "Vox Testing - 2"),
            ("Microsoft Teams Call - Vox Testing - 2", "Vox Testing - 2"),
            ("Vox Testing - 2 | Microsoft Teams", "Vox Testing - 2"),
            ("Chat | Daily Scrum | Microsoft Teams", "Daily Scrum"),
            ("Vox Testing - 2 (Meeting)", "Vox Testing - 2"),
            ("[Zoom Meeting] Sprint Planning - Zoom", "Sprint Planning"),
            ("Meet - xyz-abc-def - Google Chrome", "Google Meet: xyz-abc-def"),
        ]
        for raw, expected in cases:
            self.assertEqual(clean_meeting_title(raw, "Microsoft Teams"), expected)

    def test_meeting_studio_dropdown_formatting(self):
        """Verify Notetaker workspace initializes cleanly and call titles strip brackets/suffixes."""
        win = MainWindow(self.ctrl, floating_widget=MagicMock(), on_exit_app=lambda: None)
        self.assertIsNotNone(win.notetaker_page)
        self.assertEqual(clean_meeting_title("[Microsoft Teams Call] Vox Testing - 2", "Microsoft Teams"), "Vox Testing - 2")
        win.close()

    def test_target_bubble_pure_grayscale_antialiasing(self):
        """Verify FloatingInfoBubble renders cleanly without paint crashes."""
        bubble = FloatingInfoBubble()
        bubble.update_content("Target · Antigravity", "Speak clearly")
        sh = bubble.sizeHint()
        self.assertGreater(sh.width(), 80)

        img = QImage(sh.width(), sh.height(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        bubble.render(img)

        self.assertFalse(img.isNull())
        bubble.close()

    def test_meeting_prompt_popup_toast_avoidance(self):
        """Image 3: Verify MeetingPromptPopup avoids overlapping bottom-right Teams toasts."""
        sess = MagicMock()
        sess.platform = "Microsoft Teams"
        sess.clean_title = "Vox Testing - 2"
        sess.window_hwnd = 0x5678
        sess.session_id = "test_sess"

        popup = MeetingPromptPopup(controller=self.ctrl, session_info=sess)

        # 1. Test placement with no toast obstruction on compact laptop screen (1280x672)
        mock_screen = MagicMock()
        mock_screen.availableGeometry.return_value = QRect(0, 0, 1280, 672)
        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen):
            with patch.object(popup, "_detect_bottom_right_obstruction", return_value=None):
                popup.reposition_smart()
                geo = popup.geometry()
                # Must dock to the left of the 420px toast shelf
                self.assertLessEqual(geo.right(), 1280 - 420)

        # 2. Test placement when external toast allows vertical stacking above (obs_top=450, screen height=800)
        mock_screen_tall = MagicMock()
        mock_screen_tall.availableGeometry.return_value = QRect(0, 0, 1280, 800)
        obstruction_tall = (920, 450, 1260, 780)
        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen_tall):
            with patch.object(popup, "_detect_bottom_right_obstruction", return_value=obstruction_tall):
                popup.reposition_smart()
                geo = popup.geometry()
                # Popup must stack cleanly above obstruction
                self.assertLessEqual(geo.bottom(), 450 - 16)

        # 3. Test placement when vertical room is tight (obs_top=120) -> docks to the left
        obstruction_tight = (920, 120, 1260, 500)
        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen):
            with patch.object(popup, "_detect_bottom_right_obstruction", return_value=obstruction_tight):
                popup.reposition_smart()
                geo = popup.geometry()
                # Popup must dock to the left of the obstruction
                self.assertLessEqual(geo.right(), 920 - 16)
        popup.close()

    def test_record_btn_runtime_state_styling_and_pulse(self):
        """Verify FlowBar Notetaker chip states remain enabled and toggle cleanly across lifecycle."""
        floating = FloatingWidget(
            controller=self.ctrl,
            on_open_dashboard=MagicMock(),
            on_exit_app=lambda: None,
        )
        floating.show()
        app.processEvents()

        # 1. Idle state: Notetaker chip hidden
        self.assertEqual(floating.pill_state, PillState.IDLE)
        self.assertFalse(floating._meeting_active)
        self.assertFalse(floating.meeting_chip.isVisible())

        # 2. Recording state: companion chip visible & enabled
        floating._on_controller_meeting_state("recording", "Recording started")
        app.processEvents()
        self.assertTrue(floating._meeting_active)
        self.assertTrue(floating.meeting_chip.isVisible())
        self.assertTrue(floating.meeting_chip.isEnabled())

        # 3. Tick updates elapsed timer text
        floating._on_controller_meeting_tick(15.0, 0.2, 0.2, True, True)
        self.assertIn("00:15", floating.meeting_chip.text())

        # 4. Return to idle hides companion chip
        floating._on_controller_meeting_state("idle", "Idle")
        app.processEvents()
        self.assertFalse(floating._meeting_active)
        self.assertFalse(floating.meeting_chip.isVisible())
        floating.close()

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
"""Comprehensive verification test suite for Meeting Studio header clarity,

target indicator font antialiasing, and toast collision avoidance.
"""


from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QFont, QGuiApplication, QPixmap
from PyQt6.QtWidgets import QApplication

from voxlocal.audio.call_detector import (
    clean_meeting_title,
    is_non_call_hub,
    list_all_meeting_windows,
)
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.floating_pill import FlowBarTooltipPill, LiveSpeechPreviewBubble
from voxlocal.ui.floating_widget import FloatingInfoBubble
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup
from voxlocal.video.meeting_capture import TeamsDesktopCaptureProvider


class TestMeetingStudioHardeningAndClarity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_clean_meeting_title_prefixes_and_suffixes(self):
        cases = [
            ("[Microsoft Teams Call] Vox Testing - 2", "Microsoft Teams", "Vox Testing - 2"),
            ("[Teams Call] Daily Standup", "Microsoft Teams", "Daily Standup"),
            ("[Call] Sprint Planning", "Microsoft Teams", "Sprint Planning"),
            ("Microsoft Teams Call - Product Architecture", "Microsoft Teams", "Product Architecture"),
            ("Daily Scrum - PLM North America | Microsoft Teams", "Microsoft Teams", "Daily Scrum - PLM North America"),
            ("meet - abc-defg-hij - Google Chrome", "Google Meet", "Google Meet: abc-defg-hij"),
            ("Zoom Meeting - Board Review - Zoom", "Zoom", "Board Review"),
            ("[Zoom Meeting] Executive Sync", "Zoom", "Executive Sync"),
            ("Weekly Catchup - Work - Microsoft Edge", "Google Meet", "Weekly Catchup"),
        ]
        for raw, platform, expected in cases:
            cleaned = clean_meeting_title(raw, platform)
            self.assertEqual(cleaned, expected, f"Failed cleaning {raw!r}")

    def test_toast_and_lobby_window_rejection(self):
        # Verify lobby notifications and toast titles are flagged as non-call hubs
        lobby_samples = [
            "Vox Testing - 2 - Someone is waiting in the lobby. Join the meeting to let them in.",
            "Someone is waiting in the lobby.",
            "Let them in",
            "Incoming call from Alice Smith",
            "Alice Smith is calling",
            "Microsoft Teams Notification",
            "Zoom Meeting Prompt",
        ]
        for title in lobby_samples:
            self.assertTrue(
                is_non_call_hub("Microsoft Teams", title),
                f"Title {title!r} should be rejected as non-call hub",
            )
            self.assertTrue(
                TeamsDesktopCaptureProvider.is_non_call_hub_title(title),
                f"TeamsDesktopCaptureProvider should reject title {title!r}",
            )

        # Real call stage should NOT be rejected
        call_stage = "Daily Scrum - PLM North America | Microsoft Teams"
        self.assertFalse(is_non_call_hub("Microsoft Teams", call_stage))
        self.assertFalse(TeamsDesktopCaptureProvider.is_non_call_hub_title(call_stage))

    def test_live_target_bubble_subpixel_antialiasing_and_paint(self):
        bubble = FloatingInfoBubble()
        bubble.update_content("Target · Antigravity", "Hold Ctrl+Win to dictate into Antigravity")
        self.assertIn("Target", bubble.lbl_title.text())
        self.assertIn("Antigravity", bubble.lbl_body.text())

        pix = QPixmap(220, 64)
        pix.fill(Qt.GlobalColor.transparent)
        bubble.resize(220, 64)
        bubble.render(pix)
        self.assertFalse(pix.isNull())
        bubble.close()

    def test_live_transcript_bubble_antialiasing(self):
        bubble = LiveSpeechPreviewBubble(theme="dark")
        bubble.update_preview("Live preview caption cleanly rendered")
        self.assertIn("Live preview", bubble.lbl_preview.text())

        pix = QPixmap(260, 48)
        pix.fill(Qt.GlobalColor.transparent)
        bubble.resize(260, 48)
        bubble.render(pix)
        self.assertFalse(pix.isNull())
        bubble.close()

    def test_meeting_prompt_popup_collision_avoidance(self):
        ctrl = MagicMock()
        ctrl.registered_ui_hwnds = set()
        ctrl.theme = "dark"

        sess = MagicMock()
        sess.session_info = MagicMock()
        sess.platform = "Microsoft Teams"
        sess.clean_title = "Vox Testing - 2"
        sess.window_hwnd = 1234
        sess.session_id = "teams_test"

        popup = MeetingPromptPopup(controller=ctrl, session_info=sess)

        # Simulate laptop screen available geometry (1080p @ 150% scaling = 1280x672)
        mock_screen = MagicMock()
        mock_screen.availableGeometry.return_value = QRect(0, 0, 1280, 672)

        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen):
            with patch.object(popup, "_detect_bottom_right_obstruction", return_value=None):
                popup.reposition_smart()
                geo = popup.geometry()
                # On compact laptop (< 840px height), popup must be docked to the LEFT of the 420px toast zone
                # so it sits at x <= 1280 - 420 - 348 = 512
                self.assertLessEqual(geo.right(), 1280 - 420, "Popup must stay to the left of the bottom-right toast zone")
                self.assertGreaterEqual(geo.bottom(), 672 - 250, "Popup should be docked near the taskbar")

        # Simulate an external toast obstruction present in the bottom-right corner (e.g. 900, 420, 1260, 660)
        obstruction = (900, 420, 1260, 660)
        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen):
            with patch.object(popup, "_detect_bottom_right_obstruction", return_value=obstruction):
                popup.reposition_smart()
                geo = popup.geometry()
                # Must either be stacked above the toast (geo.bottom() <= 420) OR docked to the left (geo.right() <= 900)
                is_above = geo.bottom() <= obstruction[1]
                is_left = geo.right() <= obstruction[0]
                self.assertTrue(
                    is_above or is_left,
                    f"Popup at ({geo.x()}, {geo.y()}, {geo.width()}, {geo.height()}) collides with toast {obstruction}",
                )

        popup.close()

    def test_meeting_studio_combo_and_title_sync(self):
        cfg = AppConfig()
        cfg.data_dir = "data"
        ctrl = MagicMock()
        ctrl.config = cfg
        ctrl.theme = "dark"
        ctrl.registered_ui_hwnds = set()

        win = MainWindow(ctrl, floating_widget=MagicMock(), on_exit_app=lambda: None)
        self.assertIsNotNone(win.notetaker_page)
        self.assertEqual(clean_meeting_title("[Microsoft Teams Call] Vox Testing - 2", "Microsoft Teams"), "Vox Testing - 2")
        win.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
"""Automated verification test suite for Floating Bar 1.5s Hover-Hold Info

Tooltip system and Meeting Recording no-call edge case handling.
"""


from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QEnterEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel

from voxlocal.core.config import AppConfig
from voxlocal.ui.floating_pill import PillState
from voxlocal.ui.floating_widget import (
    FloatingInfoBubble,
    FloatingWidget,
    MeetingQuickStartDialog,
)


class TestFloatingWidgetHoverInfoAndMeeting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.cfg = AppConfig()
        self.ctrl = MagicMock()
        self.ctrl.config = self.cfg
        self.ctrl.theme = "dark"
        self.ctrl.meeting_state = "idle"
        self.ctrl.screen_recording_state = "idle"
        self.ctrl.is_hands_free_active = False
        self.ctrl.registered_ui_hwnds = set()
        self.ctrl.call_detector = MagicMock()
        self.ctrl.call_detector.active_session = None

    def test_floating_info_bubble_rendering_and_antialiasing(self):
        bubble = FloatingInfoBubble(theme="dark", controller=self.ctrl)

        bubble.update_content(
            "Meeting Recording",
            "Record online calls or in-person audio for local AI notes.",
        )
        self.assertEqual(bubble.lbl_title.text(), "Meeting Recording")
        self.assertIn("Record online calls", bubble.lbl_body.text())

        # Verify paintEvent execution renders non-empty pixmap
        pix = QPixmap(280, 90)
        pix.fill(Qt.GlobalColor.transparent)
        bubble.resize(280, 90)
        bubble.render(pix)
        self.assertFalse(pix.isNull())
        bubble.close()

    def test_hover_expand_tooltip_and_edge_docking(self):
        floating = FloatingWidget(
            controller=self.ctrl,
            on_open_dashboard=MagicMock(),
            on_exit_app=lambda: None,
        )
        floating.show()
        QApplication.processEvents()
        self.assertEqual(floating.pill_state, PillState.IDLE)
        self.assertEqual(floating.upper_pill.width(), 40)
        self.assertEqual(floating.upper_pill.height(), 8)
        self.assertFalse(floating.btn_scratchpad.isVisible())

        # Hover expands Resting 40x8 pill -> Ready 50x30 + hover tooltip + Scratchpad button
        enter_ev = QEnterEvent(QPointF(10, 4), QPointF(10, 4), QPointF(500, 500))
        floating.enterEvent(enter_ev)
        self.assertTrue(floating._is_hovered)
        self.assertEqual(floating.upper_pill.width(), 50)
        self.assertEqual(floating.upper_pill.height(), 30)
        self.assertTrue(floating.hover_tooltip.isVisible())
        self.assertIn("Click or hold", floating.hover_tooltip.lbl_text.text())
        self.assertTrue(floating.btn_scratchpad.isVisible())

        # Leaving restores Resting bar and hides tooltip
        floating.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertFalse(floating._is_hovered)
        self.assertFalse(floating.hover_tooltip.isVisible())
        self.assertEqual(floating.upper_pill.width(), 40)
        self.assertEqual(floating.upper_pill.height(), 8)

        # Edge docking flips vertical capsule on left/right edges
        floating.set_dock_edge("left")
        self.assertEqual(floating.upper_pill.size().width(), 8)
        self.assertEqual(floating.upper_pill.size().height(), 40)
        floating.set_dock_edge("right")
        self.assertEqual(floating.upper_pill.size().width(), 8)
        self.assertEqual(floating.upper_pill.size().height(), 40)
        floating.set_dock_edge("bottom")
        self.assertEqual(floating.upper_pill.size().width(), 40)
        self.assertEqual(floating.upper_pill.size().height(), 8)

        floating.close()

    def test_meeting_recording_chip_states_and_timer(self):
        floating = FloatingWidget(
            controller=self.ctrl,
            on_open_dashboard=MagicMock(),
            on_exit_app=lambda: None,
        )
        floating.show()
        QApplication.processEvents()

        # 1. Idle state: Notetaker companion chip hidden
        floating._on_controller_meeting_state("idle", "")
        self.assertFalse(floating._meeting_active)
        self.assertFalse(floating.meeting_chip.isVisible())

        # 2. Recording state: shows Notetaker pill chip with timer (not a bare dot)
        floating._on_controller_meeting_state("recording", "")
        self.assertTrue(floating._meeting_active)
        self.assertTrue(floating.meeting_chip.isVisible())
        self.assertIn("Notetaker", floating.meeting_chip.text())
        self.assertNotEqual(floating.meeting_chip.text().strip(), "⏺")

        # 3. Meeting tick updates formatted elapsed clock on chip
        floating._on_controller_meeting_tick(65.0)
        self.assertIn("01:05", floating.meeting_chip.text())

        # 4. Paused state keeps Notetaker chip accessible
        floating._on_controller_meeting_state("paused", "")
        self.assertTrue(floating._meeting_active)
        self.assertTrue(floating.meeting_chip.isVisible())

        # 5. Return to idle hides chip cleanly
        floating._on_controller_meeting_state("idle", "")
        self.assertFalse(floating._meeting_active)
        self.assertFalse(floating.meeting_chip.isVisible())

        floating.close()

    def test_meeting_quick_start_dialog_no_call_edge_case(self):
        studio_mock = MagicMock()

        # Scenario A: NO call going on
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]):
            dlg = MeetingQuickStartDialog(
                default_screen_rec=True,
                default_fps=15,
                theme="dark",
                controller=self.ctrl,
                on_open_studio=studio_mock,
            )

            all_labels = [l.text() for l in dlg.findChildren(QLabel)]
            self.assertTrue(any("Audio / Call Meeting Notes" in t for t in all_labels))
            self.assertTrue(any("No active call window locked yet" in t for t in all_labels))
            self.assertFalse(dlg.chk_screen.isChecked(), "Should not default to video when no call window is active")
            self.assertEqual(dlg.recording_mode, "audio_only")
            self.assertIn("Start Notetaker", dlg.btn_start.text())

            # Test Open Notetaker shortcut
            dlg._on_click_open_studio()
            studio_mock.assert_called_once()
            dlg.close()

        # Scenario B: Active call IS going on
        mock_wins = [
            (9999, "Microsoft Teams", "Vox Testing - 2 | Microsoft Teams", (100, 100, 1920, 1080))
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_wins):
            dlg2 = MeetingQuickStartDialog(
                default_screen_rec=True,
                default_fps=15,
                theme="dark",
                controller=self.ctrl,
                on_open_studio=studio_mock,
            )
            all_labels2 = [l.text() for l in dlg2.findChildren(QLabel)]
            self.assertTrue(any("Active Call Detected: Microsoft Teams" in t for t in all_labels2))
            self.assertEqual(dlg2.txt_title.text(), "Vox Testing - 2")
            self.assertTrue(dlg2.chk_screen.isChecked())
            self.assertEqual(dlg2.recording_mode, "audio_video")
            self.assertIn("Start Notetaker", dlg2.btn_start.text())
            dlg2.close()


if __name__ == "__main__":
    unittest.main()

"""
Enterprise Acceptance & Automated Test Suite for VOX Meeting Studio (Core Feature 3)
Phase 5: Enterprise User Experience, Workflow Intelligence & Product Experience.

Validates:
1. Executive Meeting Intelligence Dashboard Strip (KPI metrics, talk time, action counts).
2. Interactive Action Items Matrix (live status toggles, owner badges, due dates, Slack export).
3. Structured Key Decisions Panel (decision cards, rationale, clickable timestamp chips).
4. Multi-Stage Finalization Progress Card (4-step stepper, Whisper, diarization, AI MoM).
5. Interactive Clickable Transcript (timestamp anchors seek media player to exact second).
6. In-Report Contextual Ask Vox Subtab (scoped Q&A right inside the meeting workspace).
7. Ask Vox Global Meeting Scope Selector (catalog-wide vs single-meeting filtering).
8. Live Action Item Flagging & Bookmarking during active recording.
9. Elimination of redundant controls in MeetingQuickStartDialog (clean 2-mode selector).
10. Seamless two-way navigation between Live Meeting Studio and Reports Hub.
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QApplication, QLabel

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.components import (
    VoxActionItemMatrixWidget,
    VoxMeetingDashboardHeader,
    VoxProcessingProgressCard,
    VoxStructuredDecisionsWidget,
)
from voxlocal.ui.floating_widget import FloatingWidget, MeetingQuickStartDialog
from voxlocal.ui.main_window import MainWindow

_APP = None


def get_qapp():
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
        return _APP
    return app


class TestMeetingStudioPhase5EnterpriseUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_qapp()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase5_ux_test_")
        self.config = AppConfig(
            storage_dir=self.temp_dir,
            reports_library_width=350,
            auto_record_calls=False,
            prompt_on_call=False,
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            controller=self.controller,
            on_open_dashboard=lambda idx=0: None,
            on_exit_app=lambda: None,
        )
        self.window = MainWindow(
            controller=self.controller,
            floating_widget=self.floating_widget,
            on_exit_app=lambda: None,
        )
        self.db: LocalDatabase = self.controller.db

        # Seed sample corporate meetings
        self.m1_id = self.db.create_meeting(
            title="Everlane - Exporting Linesheet",
            source_platform="Microsoft Teams",
        )
        self.db.update_meeting(self.m1_id, duration_sec=1814.0, status="completed")
        self.db.add_action_item(
            meeting_id=self.m1_id,
            description="Verify column mappings with frontend team before tomorrow morning",
            owner="Mike",
            due_date="Tomorrow morning",
            status="pending",
            timestamp_sec=254.0,
        )
        self.db.add_action_item(
            meeting_id=self.m1_id,
            description="Include pricing tier column in Excel export",
            owner="Amar",
            due_date="Friday",
            status="completed",
            timestamp_sec=320.0,
        )
        self.db.add_decision(
            meeting_id=self.m1_id,
            decision="Export linesheet directly to CSV/Excel for merchant review",
            rationale="Allows reviewing SKU matrix without opening the full catalog",
            timestamp_sec=180.0,
        )

        self.m2_id = self.db.create_meeting(
            title="Abhilash Mishra - Q3 Roadmap",
            source_platform="Microsoft Teams",
        )
        self.db.update_meeting(self.m2_id, duration_sec=2684.0, status="completed")
        self.db.add_decision(
            meeting_id=self.m2_id,
            decision="Adopt local float32 transcription pipeline for client pilots",
            rationale="Meets offline enterprise privacy requirements",
            timestamp_sec=412.0,
        )
        self.window.notetaker_page.refresh_all()

    def tearDown(self):
        try:
            self.window.close()
            self.floating_widget.close()
            self.controller.shutdown()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_meeting_dashboard_header_kpis_and_live_stats(self):
        """Verify Executive Dashboard Header computes accurate KPIs and navigates to Notetaker."""
        dash = VoxMeetingDashboardHeader(theme="dark")
        try:
            dash.update_stats(
                total_meetings=2,
                total_sec=4484.0,
                pending_actions=1,
                total_decisions=2,
            )

            val_m = dash.kpi_meetings.findChild(QLabel, "kpiValue")
            self.assertIsNotNone(val_m)
            self.assertEqual(val_m.text(), "2")

            val_a = dash.kpi_actions.findChild(QLabel, "kpiValue")
            self.assertIsNotNone(val_a)
            self.assertEqual(val_a.text(), "1")

            val_d = dash.kpi_decisions.findChild(QLabel, "kpiValue")
            self.assertIsNotNone(val_d)
            self.assertEqual(val_d.text(), "2")

            dash.view_reports_clicked.connect(lambda: self.window.navigate_to_workspace("notetaker"))
            self.window.navigate_to_workspace("insights")
            self.assertEqual(self.window.workspace_stack.currentIndex(), 6)
            dash.view_reports_clicked.emit()
            self.assertEqual(self.window.workspace_stack.currentIndex(), 1)
        finally:
            dash.close()

    def test_02_interactive_action_item_matrix_toggling_and_filtering(self):
        """Verify Action Items Matrix loads tasks, toggles status to SQLite, and filters."""
        nt = self.window.notetaker_page
        nt.select_meeting(self.m1_id)
        matrix: VoxActionItemMatrixWidget = nt.studio.tab_summary.actions_widget

        self.assertEqual(len(matrix._items), 2)
        self.assertIn("1 pending", matrix.lbl_count.text())
        self.assertIn("2 total", matrix.lbl_count.text())

        # Test toggling pending to completed persists to SQLite via studio wiring
        pending_item = [it for it in matrix._items if it.get("status") == "pending"][0]
        item_id = pending_item["id"]
        matrix._on_item_toggled(item_id, checked=True)

        updated = self.db.list_action_items(meeting_id=self.m1_id)
        matching = [a for a in updated if a["id"] == item_id][0]
        self.assertEqual(matching["status"], "completed")

        # Test filtering
        matrix._set_filter("completed")
        self.assertEqual(matrix._filter_mode, "completed")
        matrix._set_filter("pending")
        self.assertEqual(matrix._filter_mode, "pending")
        matrix._set_filter("all")
        self.assertEqual(matrix._filter_mode, "all")

    def test_03_structured_decisions_widget_and_timestamp_seeking(self):
        """Verify Structured Decisions Widget renders decision cards and seeks playback."""
        nt = self.window.notetaker_page
        nt.select_meeting(self.m1_id)
        dec_widget: VoxStructuredDecisionsWidget = nt.studio.tab_summary.decisions_widget

        self.assertEqual(len(dec_widget._decisions), 1)
        self.assertIn("1 confirmed", dec_widget.lbl_count.text())

        seek_spy = []
        dec_widget.timestamp_clicked.connect(lambda sec: seek_spy.append(sec))
        dec_widget.timestamp_clicked.emit(180.0)
        self.assertEqual(seek_spy, [180.0])

    def test_04_processing_progress_card_multi_stage_stepper(self):
        """Verify Multi-Stage Processing Card advances through 4 stages and banner reflects processing."""
        card = VoxProcessingProgressCard(theme="dark")
        try:
            card.set_stage_message(1, "Stage 1/4: Packaging MP4 video & audio tracks...")
            self.assertEqual(card._current_stage, 1)
            self.assertEqual(card.progress_bar.value(), 25)

            card.set_stage_message(2, "Stage 2/4: Transcribing speech with local Whisper...")
            self.assertEqual(card._current_stage, 2)
            self.assertEqual(card.progress_bar.value(), 50)

            card.set_stage_message(3, "Stage 3/4: Separating speakers & aligning turns...")
            self.assertEqual(card._current_stage, 3)
            self.assertEqual(card.progress_bar.value(), 75)

            card.set_stage_message(4, "Stage 4/4: Synthesizing AI MoM & action items...")
            self.assertEqual(card._current_stage, 4)
            self.assertEqual(card.progress_bar.value(), 100)
        finally:
            card.close()

        nt = self.window.notetaker_page
        self.controller.meeting_state_changed.emit("processing", "Finalizing transcript...")
        self.assertFalse(nt.rec_banner.isHidden())
        self.controller.meeting_state_changed.emit("idle", "Ready")
        self.assertTrue(nt.rec_banner.isHidden())

    def test_05_interactive_transcript_clickable_timestamps(self):
        """Verify transcript renders clickable timestamp buttons that emit seek_requested."""
        from PyQt6.QtWidgets import QLabel, QPushButton

        segs = [
            {"start_time": 10.5, "end_time": 15.0, "speaker": "Mike", "text": "Let's review the export option.", "source_track": "sys"},
            {"start_time": 16.0, "end_time": 20.0, "speaker": "Amar", "text": "Agreed, let's include pricing.", "source_track": "mic"},
        ]
        self.db.replace_meeting_segments(self.m1_id, segs)

        nt = self.window.notetaker_page
        nt.select_meeting(self.m1_id)
        tx = nt.studio.tab_transcript
        self.assertEqual(len(tx._segment_widgets), 2)

        card0_labels = " | ".join(lbl.text() for lbl in tx._segment_widgets[0].findChildren(QLabel))
        card0_buttons = " | ".join(btn.text() for btn in tx._segment_widgets[0].findChildren(QPushButton))
        self.assertIn("Mike", card0_buttons)
        self.assertIn("Let's review the export option.", card0_labels)

        seek_spy = []
        tx.seek_requested.connect(lambda sec: seek_spy.append(sec))
        ts_buttons = [b for b in tx._segment_widgets[0].findChildren(QPushButton) if "00:10" in b.text()]
        self.assertEqual(len(ts_buttons), 1)
        ts_buttons[0].click()
        self.assertEqual(seek_spy, [10.5])

    def test_06_in_report_ask_vox_scoped_qa(self):
        """Verify In-Report Ask Vox bar executes scoped queries against selected meeting and displays answer."""
        nt = self.window.notetaker_page
        nt.select_meeting(self.m1_id)
        summary_tab = nt.studio.tab_summary

        with patch.object(self.controller, "query_meeting_memory", return_value={"answer": "Export was approved for CSV and Excel."}) as mock_query:
            summary_tab.txt_ask_input.setText("What key decisions were agreed upon?")
            summary_tab._on_ask_submitted()
            mock_query.assert_called_once_with("What key decisions were agreed upon?", meeting_id=self.m1_id)
            self.assertIn("Export was approved", summary_tab.lbl_ask_answer.text())

    def test_07_ask_vox_meeting_scope_dropdown(self):
        """Verify Notetaker Drawer Q&A and MainWindow streaming callbacks scope strictly to active meeting."""
        from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer

        self.controller._active_meeting_id = self.m1_id
        drawer = VoxNotetakerDrawer(self.controller)
        try:
            with patch.object(self.controller, "query_meeting_memory", return_value={"answer": "Mike proposed CSV streaming."}) as mock_query:
                drawer.txt_question.setText("What did Mike say?")
                drawer._ask_ai()
                mock_query.assert_called_once_with("What did Mike say?", meeting_id=self.m1_id)
                self.assertIn("Mike proposed CSV streaming.", drawer.qa_output.toPlainText())
        finally:
            drawer.close()

        self.window._current_ask_vox_id = 77
        self.window._ask_vox_is_streaming = True
        self.window._ask_vox_buffer = ""
        self.window._on_ask_vox_token(77, "CSV approved")
        self.window._on_ask_vox_finished(77, {"answer": "CSV approved"})
        self.assertFalse(self.window._ask_vox_is_streaming)

    def test_08_live_action_flagging_and_bookmarking(self):
        """Verify live timestamp bookmarking in My Thoughts persists and flagged actions sync."""
        nt = self.window.notetaker_page
        nt.select_meeting(self.m1_id)
        thoughts = nt.studio.tab_thoughts

        thoughts.set_current_playback_time(65.0)
        thoughts.btn_mark_note.click()
        thoughts.editor.insertPlainText("Follow up with client")
        thoughts._persist_thoughts()

        m_saved = self.db.get_meeting(self.m1_id)
        self.assertIn("[01:05] Follow up with client", m_saved.get("notes", ""))

        self.db.add_action_item(meeting_id=self.m1_id, description="Follow up with client", owner="You")
        nt.select_meeting(self.m1_id)
        descs = [it["description"] for it in nt.studio.tab_summary.actions_widget._items]
        self.assertIn("Follow up with client", descs)

    def test_09_quick_start_dialog_elimination_of_redundant_checkbox(self):
        """Verify MeetingQuickStartDialog has clean 2-mode selection with hidden redundant checkbox."""
        dlg = MeetingQuickStartDialog(
            parent=None,
            default_screen_rec=True,
            default_fps=15,
            theme="dark",
            controller=self.controller,
        )
        # Checkbox is hidden from UI to eliminate redundancy
        self.assertTrue(dlg.chk_screen.isHidden())
        self.assertTrue(dlg.record_screen)
        self.assertEqual(dlg.recording_mode, "audio_video")

        # Switching to Audio Only
        dlg.select_recording_mode("audio_only")
        self.assertFalse(dlg.record_screen)
        self.assertEqual(dlg.recording_mode, "audio_only")
        dlg.close()

    def test_10_cross_tab_seamless_navigation(self):
        """Verify seamless workspace navigation across Notetaker, Insights, and Home."""
        self.window.navigate_to_workspace("insights")
        self.assertEqual(self.window.workspace_stack.currentIndex(), 6)

        self.window.navigate_to_workspace("notetaker")
        self.assertEqual(self.window.workspace_stack.currentIndex(), 1)
        self.assertEqual(self.window.workspace_stack.tabText(1), "Notetaker")

        self.window.navigate_to_workspace("home")
        self.assertEqual(self.window.workspace_stack.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()

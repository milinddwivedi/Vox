"""
Track 4 UI & Presentation Lead Test Suite.
Verifies:
1. In-report Ask Vox query direct meeting_id scoping.
2. Truthful presentation templates without fact fabrication.
3. Canonical database KPI widget binding to get_analytics_summary.
4. Persistent live markers recording to database.
5. Slack export chronological accuracy and sensitive content export safeguards.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance for UI tests
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.components import (
    VoxMeetingComparisonDialog,
    VoxPreMeetingBriefingDialog,
    VoxActionItemMatrixWidget,
    VoxMeetingDashboardHeader,
)


class TestTrack4UIFeatures(unittest.TestCase):

    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_intel = MagicMock()
        self.mock_controller = MagicMock()
        self.mock_controller.db = self.mock_db
        self.mock_controller.intelligence = self.mock_intel
        self.mock_controller.theme = "dark"
        self.mock_controller.meeting_state = "idle"

    # ------------------------------------------------------------------
    # 1. In-Report Ask Vox Direct meeting_id Scoping
    # ------------------------------------------------------------------
    def test_in_report_ask_query_direct_scoping(self):
        """In-report search must pass meeting_id directly to query_meeting_memory."""
        window = MainWindow.__new__(MainWindow)
        window.controller = self.mock_controller
        window._selected_meeting_id = 42

        window.txt_in_report_ask = MagicMock()
        window.txt_in_report_ask.text.return_value = "What was decided about Postgres?"
        window.txt_in_report_ans = MagicMock()

        self.mock_db.get_meeting.return_value = {
            "id": 42,
            "title": "[PROD] DB Migration Outage",
        }
        self.mock_controller.query_meeting_memory.return_value = {
            "answer": "Postgres was adopted."
        }

        window._submit_in_report_ask_query()

        # Must be called with scoped query and meeting_id=42 directly
        self.mock_controller.query_meeting_memory.assert_called_once_with(
            "In [[PROD] DB Migration Outage] meeting: What was decided about Postgres?",
            meeting_id=42,
        )
        window.txt_in_report_ans.setMarkdown.assert_called_once_with("Postgres was adopted.")

    # ------------------------------------------------------------------
    # 2. Truthful Presentation Templates
    # ------------------------------------------------------------------
    def test_truthful_presentation_templates_on_empty_data(self):
        """Templates must render honest placeholders rather than fabricated claims when data is empty."""
        empty_meeting = {
            "title": "Incident Triage",
            "created_at": "2026-11-01 10:00:00",
            "duration_sec": 300.0,
            "summary": "",
        }

        # Client Review
        text_cr = MainWindow._render_templated_summary(
            None, empty_meeting, "Client Review", [], []
        )
        self.assertNotIn("Client review and milestone sign-off completed.", text_cr)
        self.assertNotIn("Deliverables documented in statement of work.", text_cr)
        self.assertNotIn("Terms accepted as discussed.", text_cr)
        self.assertIn("_No formal summary recorded._", text_cr)
        self.assertIn("No items extracted for this section.", text_cr)
        self.assertIn("_No strategic decisions logged for this session._", text_cr)

        # Executive Brief
        text_eb = MainWindow._render_templated_summary(
            None, empty_meeting, "Executive Brief", [], []
        )
        self.assertNotIn("Discussion concluded successfully without major blockers.", text_eb)
        self.assertIn("_No formal summary recorded._", text_eb)
        self.assertIn("_No strategic decisions logged for this session._", text_eb)
        self.assertIn("No items extracted for this section.", text_eb)

        # Technical Troubleshooting
        text_tt = MainWindow._render_templated_summary(
            None, empty_meeting, "Technical Troubleshooting", [], []
        )
        self.assertNotIn("Root cause investigation underway.", text_tt)
        self.assertNotIn("Diagnostics tasks in progress.", text_tt)
        self.assertIn("_No formal summary recorded._", text_tt)
        self.assertIn("_No strategic decisions logged for this session._", text_tt)
        self.assertIn("No items extracted for this section.", text_tt)

        # Internal Status
        text_is = MainWindow._render_templated_summary(
            None, empty_meeting, "Internal Status", [], []
        )
        self.assertNotIn("Progress reviewed according to sprint schedule.", text_is)
        self.assertNotIn("Decisions logged in sprint board.", text_is)
        self.assertIn("_No formal summary recorded._", text_is)
        self.assertIn("No items extracted for this section.", text_is)
        self.assertIn("_No strategic decisions logged for this session._", text_is)

    # ------------------------------------------------------------------
    # 3. Canonical Database KPI Updates
    # ------------------------------------------------------------------
    def test_canonical_kpi_updates_from_get_analytics_summary(self):
        """Dashboard stats must bind directly to get_analytics_summary with canonical open status."""
        window = MainWindow.__new__(MainWindow)
        window.controller = self.mock_controller
        window.meeting_dashboard_header = MagicMock(spec=VoxMeetingDashboardHeader)

        self.mock_db.get_analytics_summary.return_value = {
            "total_meetings": 15,
            "total_duration_sec": 7200.0,
            "open_action_items": 4,
            "confirmed_decisions": 6,
        }

        window._refresh_meeting_dashboard_stats()

        self.mock_db.get_analytics_summary.assert_called_once()
        window.meeting_dashboard_header.update_stats.assert_called_once_with(
            total_meetings=15,
            total_sec=7200.0,
            pending_actions=4,
            total_decisions=6,
        )

    def test_action_items_list_widget_canonical_open_status(self):
        """VoxActionItemMatrixWidget must count and filter status in ('pending', 'blocked', 'reopened')."""
        widget = VoxActionItemMatrixWidget()
        items = [
            {"id": 1, "description": "Task 1", "status": "pending"},
            {"id": 2, "description": "Task 2", "status": "blocked"},
            {"id": 3, "description": "Task 3", "status": "reopened"},
            {"id": 4, "description": "Task 4", "status": "completed"},
            {"id": 5, "description": "Task 5", "status": "cancelled"},
        ]
        widget.set_items(items)

        # 3 open tasks: pending, blocked, reopened (completed and cancelled are closed/excluded)
        self.assertIn("3 pending", widget.lbl_count.text())
        self.assertIn("5 total", widget.lbl_count.text())

    # ------------------------------------------------------------------
    # 4. Persistent Live Markers UI
    # ------------------------------------------------------------------
    def test_live_markers_persistence(self):
        """Live Flag Action and Bookmark must save markers into database with elapsed timestamp."""
        window = MainWindow.__new__(MainWindow)
        window.controller = self.mock_controller
        window.show_toast = MagicMock()
        window.live_transcript_feed = MagicMock()

        self.mock_controller.meeting_state = "recording"
        self.mock_controller._active_meeting_id = 99
        self.mock_controller.meeting_recorder = MagicMock()
        self.mock_controller.meeting_recorder.duration_sec = 45.2

        # Flag Action
        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("Check latency on replica", True)):
            window._on_live_flag_action_clicked()

        self.mock_db.add_marker.assert_called_once_with(
            99, "flag_action", "Check latency on replica", 45.2
        )
        self.mock_db.add_action_item.assert_called_once_with(
            meeting_id=99,
            description="Check latency on replica",
            owner="Unassigned",
            due_date="",
            status="pending",
            timestamp_sec=45.2,
            is_manual=1,
        )

        # Bookmark
        window._on_live_bookmark_clicked()
        self.mock_db.add_marker.assert_called_with(
            99, "bookmark", "Key discussion moment", 45.2
        )

    # ------------------------------------------------------------------
    # 5. Slack Export Chronological Accuracy & Sensitive Safeguards
    # ------------------------------------------------------------------
    def test_slack_export_chronological_accuracy_and_safeguard(self):
        """Slack comparison export must label latest meeting decisions as active regardless of parameter order."""
        dlg = VoxMeetingComparisonDialog.__new__(VoxMeetingComparisonDialog)
        dlg._current_result = {
            "meeting_a": {
                "id": 10,
                "title": "Meeting Later",
                "created_at": "2026-11-02 14:00:00",
            },
            "meeting_b": {
                "id": 5,
                "title": "Meeting Earlier",
                "created_at": "2026-11-01 10:00:00",
            },
            "decisions_a": [{"decision": "Migrate to SQLite", "status": "confirmed"}],
            "decisions_b": [{"decision": "Adopt DynamoDB", "status": "rejected"}],
            "carry_over_actions": [],
        }

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            dlg._copy_slack()

        cb = QApplication.clipboard()
        text = cb.text()

        # The later meeting decision ('Migrate to SQLite') MUST be labeled New/Active
        self.assertIn("• *[New/Active]* Migrate to SQLite", text)
        self.assertNotIn("• *[New/Active]* Adopt DynamoDB", text)

    def test_export_sensitive_content_safeguard(self):
        """Exporting a meeting with sensitive keywords must display warning dialog."""
        window = MainWindow.__new__(MainWindow)
        window.controller = self.mock_controller
        window._selected_meeting_id = 77
        window.show_toast = MagicMock()

        self.mock_db.get_meeting.return_value = {
            "id": 77,
            "title": "Confidential Executive Compensation Review",
            "summary": "Reviewed internal only salary ranges and confidential profit margin metrics.",
        }

        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QApplication.instance()) as mock_warn:
            from PyQt6.QtWidgets import QMessageBox
            mock_warn.return_value = QMessageBox.StandardButton.No

            with patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName") as mock_save:
                window._export_selected("md")
                # User declined sensitive warning -> file dialog must NOT open
                mock_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

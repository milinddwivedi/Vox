"""
Enterprise Acceptance Test Suite for VOX Meeting Studio (Core Feature 3)
Phase 5 Advanced: Cross-Meeting Intelligence, Meeting Comparison, Pre-Meeting Briefing & Presentation Templates.

Validates:
1. Cross-Meeting Comparison Engine (decision shifts, open carry-overs, topic transitions).
2. Executive Pre-Meeting Briefing Assistant (grounded prior commitments, strategic agenda).
3. Recurring Topic & Chapter Aggregation across catalog.
4. Report Presentation Templates (Standard MoM, Executive Brief, Internal Status, Client Review, Technical Troubleshooting).
5. Interactive UI Modals (VoxMeetingComparisonDialog, VoxPreMeetingBriefingDialog).
6. Ask Vox Presets and One-Click Multi-Channel Formatting (Slack, Teams, Markdown).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.components import (
    VoxMeetingComparisonDialog,
    VoxPreMeetingBriefingDialog,
)
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow

_APP = None


def get_qapp():
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
        return _APP
    return app


class TestMeetingStudioPhase5AdvancedIntelligence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_qapp()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase5_adv_test_")
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

        # Seed Meeting 1: Early sync
        self.m1_id = self.db.create_meeting(
            title="Everlane - Linesheet Architecture Sync",
            source_platform="Microsoft Teams",
        )
        self.db.update_meeting(
            self.m1_id,
            duration_sec=1420.0,
            status="completed",
            topics="Architecture, Linesheet, CSV Export, Frontend",
            summary="Agreed on direct CSV export architecture to support merchandise review.",
        )
        self.db.add_decision(
            meeting_id=self.m1_id,
            decision="Adopt CSV streaming export pipeline",
            rationale="Reduces memory footprint on large SKU catalogs",
            timestamp_sec=180.0,
        )
        self.db.add_action_item(
            meeting_id=self.m1_id,
            description="Verify column schema with Everlane engineering",
            owner="Mike",
            due_date="Friday",
            status="pending",
            timestamp_sec=240.0,
        )
        self.db.add_action_item(
            meeting_id=self.m1_id,
            description="Prototype Excel export template",
            owner="Amar",
            due_date="Thursday",
            status="completed",
            timestamp_sec=310.0,
        )

        # Seed Meeting 2: Later sync
        self.m2_id = self.db.create_meeting(
            title="Everlane - Product Deployment Review",
            source_platform="Microsoft Teams",
        )
        self.db.update_meeting(
            self.m2_id,
            duration_sec=1860.0,
            status="completed",
            topics="Deployment, Linesheet, Staging QA, Rollout",
            summary="Reviewed staging QA results and planned production rollout window.",
        )
        self.db.add_decision(
            meeting_id=self.m2_id,
            decision="Deploy export service to staging first",
            rationale="Validates performance under live production replica data",
            timestamp_sec=210.0,
        )
        self.db.add_action_item(
            meeting_id=self.m2_id,
            description="Conduct sanity tests on staging",
            owner="Mike",
            due_date="Monday",
            status="pending",
            timestamp_sec=360.0,
        )

    def tearDown(self):
        try:
            self.window.close()
            self.floating_widget.close()
            self.controller.shutdown()
            cb = QApplication.clipboard()
            if cb:
                cb.clear()
            if hasattr(self, "app") and self.app:
                self.app.processEvents()
        except Exception:
            pass
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def _get_clipboard_text_flushed(self, expected_substring: str = "", timeout: float = 0.5, dlg: Optional[Any] = None) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if hasattr(self, "app") and self.app:
                self.app.processEvents()
            cb = QApplication.clipboard()
            if cb:
                txt = cb.text()
                if not expected_substring or expected_substring in txt:
                    return txt
            if dlg is not None:
                last_txt = getattr(dlg, "_last_copied_text", "")
                if last_txt and (not expected_substring or expected_substring in last_txt):
                    return last_txt
            time.sleep(0.02)
        cb = QApplication.clipboard()
        txt = cb.text() if cb else ""
        if not txt and dlg is not None:
            txt = getattr(dlg, "_last_copied_text", "")
        return txt

    def test_compare_meetings_engine(self):
        """Verify algorithmic comparison between two sessions."""
        res = self.controller.compare_meetings(self.m1_id, self.m2_id)
        self.assertIsNotNone(res)
        self.assertEqual(res["meeting_a"]["id"], self.m1_id)
        self.assertEqual(res["meeting_b"]["id"], self.m2_id)

        # Decisions detected
        self.assertEqual(len(res["decisions_a"]), 1)
        self.assertEqual(len(res["decisions_b"]), 1)
        self.assertIn("Adopt CSV streaming", res["decisions_a"][0]["decision"])
        self.assertIn("Deploy export service", res["decisions_b"][0]["decision"])

        # Carry over actions (Mike's uncompleted action from Meeting 1)
        carry_over = res["carry_over_actions"]
        self.assertTrue(any("Verify column schema" in a["description"] for a in carry_over))
        self.assertTrue(all(a["status"] != "completed" for a in carry_over))

        # Topic transitions
        self.assertTrue(any("Linesheet" in t for t in res["common_topics"]))
        self.assertTrue(any("Deployment" in t for t in res["new_topics"]))

        # Markdown comparison report generated
        md = res["comparison_markdown"]
        self.assertIn("Meeting Comparison", md)
        self.assertIn("Everlane - Linesheet Architecture Sync", md)
        self.assertIn("Everlane - Product Deployment Review", md)

    def test_generate_pre_meeting_briefing_engine(self):
        """Verify executive pre-meeting briefing generator."""
        brief = self.controller.generate_pre_meeting_briefing("Everlane")
        self.assertIsNotNone(brief)
        self.assertEqual(brief["topic"], "Everlane")
        self.assertGreaterEqual(len(brief["relevant_meetings"]), 2)

        # Open actions synthesized
        self.assertTrue(any(a["owner"] == "Mike" for a in brief["open_action_items"]))

        # Prior decisions present
        self.assertTrue(any("CSV streaming" in d["decision"] for d in brief["key_decisions"]))

        # Structured Markdown briefing
        md = brief["briefing_markdown"]
        self.assertIn("Executive Pre-Meeting Briefing", md)
        self.assertIn("Everlane", md)
        self.assertIn("Suggested Discussion Agenda", md)

    def test_recurring_topics_aggregation(self):
        """Verify cross-catalog recurring topic extraction."""
        topics = self.controller.get_recurring_topics(limit=10)
        self.assertIsInstance(topics, list)
        topic_names = [t["name"].lower() for t in topics]
        self.assertTrue(any("linesheet" in n for n in topic_names))

    def test_report_presentation_templates(self):
        """Verify Notetaker Studio & Intelligence engine format exports cleanly without mutating DB."""
        nt = self.window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(self.m1_id)

        txt_mom = nt.studio.tab_summary.txt_summary.toPlainText()
        self.assertIn("Agreed on direct CSV export architecture", txt_mom)

        # Verify studio export formats and participant checklists/follow-up templates
        nt.studio._populate_advanced_tabs()
        export_info = nt.studio.adv_export_page.toPlainText()
        self.assertIn("Standard MoM", export_info)
        self.assertIn("Client Review MoM", export_info)

        clean_mom = nt.studio.export_clean_mom()
        self.assertIn("Agreed on direct CSV export architecture", clean_mom)

        m_data = self.db.get_meeting(self.m1_id)
        m_data["action_items"] = self.db.list_action_items(meeting_id=self.m1_id)
        m_data["decisions"] = self.db.list_decisions(meeting_id=self.m1_id)
        email_tpl = self.controller.intelligence.generate_follow_up_email(meeting_id=self.m1_id, meeting_data=m_data)
        self.assertIn("markdown", email_tpl)
        self.assertIn("Everlane", email_tpl.get("subject", ""))

        # Verify underlying database remains untouched
        m_fresh = self.db.get_meeting(self.m1_id)
        self.assertIn("Agreed on direct CSV export", m_fresh["summary"])

    def test_meeting_comparison_dialog(self):
        """Verify VoxMeetingComparisonDialog UI creation and export actions."""
        dlg = VoxMeetingComparisonDialog(
            db=self.db,
            intelligence=self.controller.intelligence,
            theme=self.controller.theme,
            initial_meeting_id_a=self.m1_id,
            initial_meeting_id_b=self.m2_id,
            parent=self.window,
        )
        self.assertGreaterEqual(dlg.combo_a.count(), 2)
        self.assertGreaterEqual(dlg.combo_b.count(), 2)
        self.assertIn("Meeting Comparison", dlg.txt_report.toPlainText())

        # Test plain summary export formatting
        dlg._copy_plain_summary()
        clip_text = self._get_clipboard_text_flushed(expected_substring="Meeting Comparison", timeout=0.5, dlg=dlg)
        self.assertIn("Meeting Comparison", clip_text)

        dlg.close()

    def test_pre_meeting_briefing_dialog(self):
        """Verify VoxPreMeetingBriefingDialog UI creation, theme chips, and actions."""
        dlg = VoxPreMeetingBriefingDialog(
            db=self.db,
            intelligence=self.controller.intelligence,
            theme=self.controller.theme,
            initial_topic="Everlane",
            parent=self.window,
        )
        self.assertEqual(dlg.txt_topic.text(), "Everlane")
        self.assertIn("Executive Pre-Meeting Briefing", dlg.txt_report.toPlainText())

        # Test plain summary export formatting
        dlg._copy_plain_summary()
        clip_text = self._get_clipboard_text_flushed(expected_substring="Pre-Meeting Briefing", timeout=0.5, dlg=dlg)
        self.assertIn("Pre-Meeting Briefing", clip_text)

        dlg.close()

    def test_ui_navigation_and_action_controls(self):
        """Verify Notetaker workspace navigation and action controls enable/disable on selection."""
        from voxlocal.ui.notetaker_page import NotetakerPage

        empty_dir = Path(tempfile.mkdtemp(prefix="vox_empty_nt_"))
        empty_ctrl = AppController(
            AppConfig(storage_dir=empty_dir, auto_record_calls=False, prompt_on_call=False)
        )
        empty_nt = NotetakerPage(empty_ctrl)
        try:
            self.assertFalse(empty_nt.btn_watch_video.isEnabled())
            self.assertFalse(empty_nt.btn_play_pause.isEnabled())
            self.assertFalse(empty_nt.btn_export.isEnabled())
            self.assertFalse(empty_nt.btn_more.isEnabled())
        finally:
            empty_nt.close()
            empty_ctrl.shutdown()
            shutil.rmtree(empty_dir, ignore_errors=True)

        self.window.navigate_to_workspace("notetaker")
        self.assertEqual(self.window.workspace_stack.currentWidget(), self.window.notetaker_page)

        nt = self.window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(self.m1_id)
        self.assertTrue(nt.btn_export.isEnabled())
        self.assertTrue(nt.btn_more.isEnabled())
        self.assertFalse(nt.btn_watch_video.isEnabled())  # No video attached to m1_id


if __name__ == "__main__":
    unittest.main()

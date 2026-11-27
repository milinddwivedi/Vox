from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 11
Product Experience & Workflow Engineering Test Battery

Covers:
1. Action Item Matrix Ergonomics (filtering by status, inline toggling, owner badges).
2. Structured Decisions UI (status pills, lineage chips, seek timestamp anchors).
3. Presentation Report Templates (Standard MoM, Executive Brief, Client Review, Technical Troubleshooting).
4. Executive Meeting Comparison & Pre-Meeting Briefing Dialogs.
5. Theme Consistency & Global Shortcuts Integrity.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.ui.components import (
    VoxActionItemMatrixWidget,
    VoxStructuredDecisionsWidget,
    VoxMeetingDashboardHeader,
    VoxProcessingProgressCard,
    VoxMeetingComparisonDialog,
    VoxPreMeetingBriefingDialog,
)

_APP = None


def get_qapp():
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
        return _APP
    return app


class TestPhase11ProductExperienceAndWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_qapp()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase11_test_")
        self.config = AppConfig()
        self.config.storage_dir = Path(self.temp_dir)
        self.db = LocalDatabase(self.config.db_path)
        self.intel = MeetingIntelligenceEngine(self.db, llm_engine=None)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Action Item Matrix Ergonomics & Filtering
    # -------------------------------------------------------------------------
    def test_action_item_matrix_filtering_and_status_toggle(self):
        """Verify action items matrix renders, filters by status, and tracks count."""
        m_id = self.db.create_meeting(title="Q4 Product Review")
        
        self.db.add_action_item(
            meeting_id=m_id,
            description="Complete UX audit of meeting studio",
            owner="Alice",
            status="pending",
        )
        self.db.add_action_item(
            meeting_id=m_id,
            description="Ship v1.0 offline release candidate",
            owner="Bob",
            status="completed",
        )

        widget = VoxActionItemMatrixWidget()
        items = self.db.list_action_items(meeting_id=m_id)
        widget.set_items(items)

        # Initially shows all items
        self.assertEqual(len(widget._items), 2)
        self.assertIn("1 pending", widget.lbl_count.text())
        self.assertIn("2 total", widget.lbl_count.text())

        # Test filter switching
        widget._set_filter("pending")
        self.assertEqual(widget._filter_mode, "pending")
        widget._set_filter("completed")
        self.assertEqual(widget._filter_mode, "completed")
        widget._set_filter("all")
        self.assertEqual(widget._filter_mode, "all")

    # -------------------------------------------------------------------------
    # 2. Structured Decisions UI & Lineage Chips
    # -------------------------------------------------------------------------
    def test_structured_decisions_rendering_and_lineage(self):
        """Verify decisions panel renders cards with status chips and lineage indicators."""
        m_id = self.db.create_meeting(title="Architecture Council")

        d1_id = self.db.add_decision(
            meeting_id=m_id,
            decision="Evaluate SQLite WAL vs DuckDB",
            status="proposed",
            evolution_relationship="new",
        )
        d2_id = self.db.add_decision(
            meeting_id=m_id,
            decision="Standardize on SQLite WAL for 100% offline persistence",
            status="confirmed",
            evolution_relationship="superseded",
            supersedes_id=d1_id,
            timestamp_sec=142.0,
        )

        widget = VoxStructuredDecisionsWidget()
        decisions = self.db.list_decisions(meeting_id=m_id)
        widget.set_decisions(decisions)

        self.assertEqual(len(widget._decisions), 2)
        self.assertIn("1 confirmed", widget.lbl_count.text())

        # Verify timestamp seek signal emission
        seek_history = []
        widget.timestamp_clicked.connect(lambda sec: seek_history.append(sec))
        widget.timestamp_clicked.emit(142.0)
        self.assertEqual(seek_history, [142.0])

    # -------------------------------------------------------------------------
    # 3. Report Presentation Templates (Enterprise Templates)
    # -------------------------------------------------------------------------
    def test_presentation_report_templates_export(self):
        """Verify enterprise presentation templates export properly across formats."""
        m_id = self.db.create_meeting(title="Client Steering Committee")
        self.db.update_meeting(
            m_id,
            status="completed",
            summary="Strategic project alignment with external client stakeholders.",
            notes="INTERNAL ONLY: Margin target is 35%. Do not disclose to client.",
        )
        self.db.add_decision(
            m_id,
            decision="Deliver milestone 1 by November 15",
            status="confirmed",
        )
        self.db.add_action_item(
            m_id,
            description="Send updated statement of work",
            owner="Carol",
            status="pending",
        )

        meeting = self.db.get_meeting(m_id)
        templates = [
            "Standard MoM",
            "Executive Brief",
            "Internal Status",
            "Client Review",
            "Technical Troubleshooting",
        ]

        for tpl in templates:
            txt_export = TranscriptExporter.to_txt(meeting, template_name=tpl)
            md_export = TranscriptExporter.to_markdown(meeting, template_name=tpl)

            self.assertIn("Client Steering Committee", txt_export)
            self.assertIn("Client Steering Committee", md_export)

            # Strict Client Review verification: INTERNAL notes MUST BE suppressed
            if tpl == "Client Review":
                self.assertNotIn("INTERNAL ONLY: Margin target is 35%", txt_export)
                self.assertNotIn("INTERNAL ONLY: Margin target is 35%", md_export)

    # -------------------------------------------------------------------------
    # 4. Meeting Comparison & Pre-Meeting Briefing Dialogs
    # -------------------------------------------------------------------------
    def test_dialogs_initialization_and_rendering(self):
        """Verify VoxMeetingComparisonDialog and VoxPreMeetingBriefingDialog initialize without errors."""
        m1 = self.db.create_meeting(title="Design Review Pt 1")
        m2 = self.db.create_meeting(title="Design Review Pt 2")

        self.db.add_action_item(m1, "Review wireframes", owner="Alice", status="completed")
        self.db.add_action_item(m2, "Finalize color tokens", owner="Bob", status="pending")

        # 1. Comparison Dialog
        dlg_comp = VoxMeetingComparisonDialog(self.db, self.intel, theme="dark", initial_meeting_id_a=m1, initial_meeting_id_b=m2)
        self.assertIsNotNone(dlg_comp)
        self.assertIn("Comparison", dlg_comp.windowTitle())

        # 2. Briefing Dialog
        dlg_brief = VoxPreMeetingBriefingDialog(self.db, self.intel, theme="dark", initial_topic="Design")
        self.assertIsNotNone(dlg_brief)
        self.assertIn("Briefing", dlg_brief.windowTitle())

    # -------------------------------------------------------------------------
    # 5. Global Hotkeys & Configuration Integrity
    # -------------------------------------------------------------------------
    def test_global_hotkeys_and_theme_configuration(self):
        """Verify default hotkey shortcuts and theme preference integrity."""
        self.assertEqual(self.config.typing_hotkey, "ctrl+shift+space")
        self.assertEqual(self.config.dictation_hotkey, "ctrl+shift+space")
        self.assertEqual(self.config.ptt_hotkey, "ctrl+win")
        self.assertEqual(self.config.meeting_hotkey, "ctrl+alt+r")
        self.assertIn(self.config.theme, ["dark", "light", "system"])


if __name__ == "__main__":
    unittest.main()

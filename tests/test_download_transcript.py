import os
import sys
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow, clean_summary_for_display
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline


class TestDownloadTranscriptAndSummaryFormatting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_test_dl_")
        self.config = AppConfig(
            storage_dir=self.temp_dir,
            auto_record_calls=False,
            prompt_on_call=False,
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda tab_idx=0, maximize=False: None,
            on_exit_app=lambda: None,
        )
        self.window = MainWindow(
            self.controller,
            self.floating_widget,
            on_exit_app=lambda: None,
        )
        self.window.resize(1280, 800)
        self.app.processEvents()

    def tearDown(self):
        try:
            self.window.close()
            self.controller.shutdown()
        except Exception:
            pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_download_transcript_button_exists_and_state(self):
        """Verify NotetakerPage export button exists and requires a selected meeting."""
        nt = self.window.notetaker_page
        self.assertTrue(hasattr(nt, "btn_export"))
        btn = nt.btn_export
        self.assertIn("Export", btn.text())
        # Initially disabled when no meeting is selected
        self.assertFalse(btn.isEnabled())

    def test_download_transcript_export_txt_and_md(self):
        """Verify NotetakerPage._export_format exports valid .txt, .md, and .srt files."""
        # Insert a test meeting with transcript segments
        m_id = self.controller.db.create_meeting(
            title="Sprint Review",
            source_platform="Microsoft Teams",
        )
        self.controller.db.update_meeting(
            m_id,
            duration_sec=120.0,
            summary="Sprint velocity and deployment signoff.",
            full_transcript="[00:00] Speaker 1: Hello team.\n\n[00:05] You (Mic): Glad to be here.",
        )
        self.controller.db.add_transcript_segment(m_id, 0, 0.0, 4.0, "Speaker 1", "Hello team.")
        self.controller.db.add_transcript_segment(m_id, 1, 5.0, 9.0, "You (Mic)", "Glad to be here.")
        nt = self.window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(m_id)
        self.assertTrue(nt.btn_export.isEnabled())

        # Test TXT Export
        out_txt = Path(self.temp_dir) / "export_test.txt"
        with patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(out_txt), "*.txt")):
            nt._export_format("txt")

        self.assertTrue(out_txt.exists())
        content_txt = out_txt.read_text(encoding="utf-8")
        self.assertIn("Sprint Review", content_txt)
        self.assertIn("Speaker 1", content_txt)
        self.assertIn("Hello team.", content_txt)
        self.assertIn("Glad to be here.", content_txt)

        # Test MD Export
        out_md = Path(self.temp_dir) / "export_test.md"
        with patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(out_md), "*.md")):
            nt._export_format("md")

        self.assertTrue(out_md.exists())
        content_md = out_md.read_text(encoding="utf-8")
        self.assertIn("Sprint Review", content_md)
        self.assertIn("Speaker 1", content_md)
        self.assertIn("Hello team.", content_md)

        # Test SRT Export
        out_srt = Path(self.temp_dir) / "export_test.srt"
        with patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(out_srt), "*.srt")):
            nt._export_format("srt")

        self.assertTrue(out_srt.exists())
        self.assertIn("-->", out_srt.read_text(encoding="utf-8"))

    def test_clean_summary_formatting_constraint(self):
        """Verify that summary display never starts lines with '#' or '*'."""
        raw_summary = """# 📋 Minutes of Meeting
## 1. Meeting Details
* **Title:** Testing Session
* **Duration:** 10 mins
## 2. Executive Summary
* Key discussion point 1
* Key discussion point 2
### Actions
* [ACT-01] Deploy release
"""
        cleaned = clean_summary_for_display(raw_summary)
        for line in cleaned.splitlines():
            trimmed = line.strip()
            self.assertFalse(trimmed.startswith(("#", "*")), f"Found forbidden starting header character: {trimmed}")

        # Check clean headers exist
        self.assertIn("MINUTES OF MEETING", cleaned)
        self.assertIn("1. MEETING DETAILS", cleaned)
        self.assertIn("2. EXECUTIVE SUMMARY", cleaned)
        self.assertIn("• Key discussion point 1", cleaned)


if __name__ == "__main__":
    unittest.main()

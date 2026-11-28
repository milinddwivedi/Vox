"""
VOX v2.3.0 Verification Suite:
1. Prevent Browser PDF auto-meeting detection & remove Slack platform detection.
2. Executive PDF export (Brief & Detailed modes, sanitized text, no raw transcript).
3. Unified Summary Box (Brief vs Detailed, separate overall & section copy, zero clutter/Slack).
4. Multi-select bulk delete & Light/Dark adaptive confirmation dialogs.
5. Visible scrollbars & global QApplication Light/Dark palette coherence.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

from voxlocal.audio.call_detector import (
    _is_non_meeting_document_window,
    classify,
    is_non_call_hub,
)
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.ui.ask_vox_panel import VoxAskConversationView
from voxlocal.ui.components import VoxActionItemMatrixWidget, VoxStructuredDecisionsWidget
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.meeting_studio import VoxSummaryDeliverablesTab
from voxlocal.ui.notetaker_page import NotetakerPage, VoxMeetingListDelegate
from voxlocal.ui.theme import get_scrollbar_qss
from voxlocal.video.meeting_capture import (
    GenericCallWindowCaptureProvider,
    GoogleMeetTabCaptureProvider,
)


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


def _sample_noisy_meeting() -> dict:
    return {
        "id": 999,
        "title": "Northstar PLM Migration Sync",
        "created_at": "2026-11-27 23:05:00",
        "duration_sec": 491.0,
        "source_platform": "Microsoft Teams",
        "participants_json": ["Milind", "Amar"],
        "brief_summary": (
            "Milind and Amar reviewed blockers preventing the Northstar Apparel PLM migration "
            "and agreed to fix sample SQ import sheets before bulk upload."
        ),
        "detailed_summary": (
            "The team audited the SQ sandbox import sheets and identified 12 records missing buyer references, "
            "inconsistent color coding (NAV vs NBY), and product group mismatches (TSH vs TEET).\n\n"
            "- Amar will confirm customer reference codes and finance field requirements.\n"
            "- Sample external ID generation will move into a dedicated column before production import."
        ),
        "key_points_json": [
            {"topic": "Greetings", "summary": "Hi Amar. Good morning Amar. How are you doing today?", "timestamp_str": "00:05"},
            {"topic": "Buyer References", "summary": "Resolve 12 SQ records with missing buyer reference values prior to upload.", "timestamp_str": "04:56"},
            {"topic": "Color & Product Mapping", "summary": "Avoid auto-replacing NAV with NBY until customer reference lists are confirmed.", "timestamp_str": "05:40"},
        ],
        "topics_json": [
            {
                "title": "Amar. morning amar. today?",
                "summary": "Hi Amar. Good morning Amar. How are you doing today?",
                "timestamp_range": "00:00-00:18",
                "key_points": ["Hi Amar."],
            },
            {
                "title": "SQ Import Data Validation",
                "summary": "Reviewed missing buyer references and spreadsheet column formatting prior to WFX style upload.",
                "timestamp_range": "01:59-06:12",
                "key_points": ["Add separate column for generated external IDs", "Confirm finance accounting reference field"],
            },
        ],
        "decisions": [
            {
                "id": 1,
                "decision": "Keep existing NAV color codes unchanged until the customer reference list is signed off.",
                "status": "confirmed",
                "rationale": "Prevents silent SKUs corruption in PLM sandbox.",
                "speaker": "Milind",
                "timestamp_sec": 340.0,
                "evidence_quote": "We should not automatically replace NAV with NBY...",
            }
        ],
        "action_items": [
            {
                "id": 10,
                "description": "Prepare bulk upload template for WFX style upload by EOD.",
                "owner": None,
                "due_date": "Today EOD",
                "priority": "High",
                "status": "pending",
                "timestamp_sec": 372.0,
                "evidence_quote": "Let's prepare the bulk upload template today.",
            },
            {
                "id": 11,
                "description": "Confirm whether finance requires an additional accounting reference field.",
                "owner": "Amar",
                "due_date": "Next Thursday",
                "priority": "Medium",
                "status": "pending",
                "timestamp_sec": 410.0,
            },
        ],
        "follow_ups_json": [
            {"item": "Share validated sample CSV after finance sign-off.", "owner": "Amar", "timestamp_str": "06:30"}
        ],
        "risks_and_blockers_list": [
            {
                "item_type": "risk",
                "impact": "MEDIUM",
                "description": "RISKImpact: MEDIUM Incomplete buyer references could generate invalid external IDs.",
                "mitigation_or_resolution": "Block production import until all 12 SQ rows are verified.",
                "timestamp_sec": 296.0,
            }
        ],
        "open_questions_json": [
            {"question": "Hi Amar. Good morning Amar. How are you doing today?", "raised_by": "You (Mic)", "timestamp_str": "00:02"},
            {"question": "Does finance require an extra accounting reference column on production SQ files?", "raised_by": "Milind", "timestamp_str": "06:45"},
        ],
        "segments": [
            {"start_time": 1.0, "end_time": 5.0, "speaker": "You (Mic)", "text": "Hi Amar. Good morning Amar. How are you doing today?"}
        ],
    }


class TestV23MeetingUXPdfAndDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_app()

    def test_browser_pdf_and_slack_not_detected_as_meeting(self) -> None:
        fake_windows = [
            {"hwnd": 101, "pid": 1, "proc_name": "msedge.exe", "title": "Zoom_Meeting.pdf - Work - Microsoft Edge", "plat": "Zoom"},
            {"hwnd": 102, "pid": 2, "proc_name": "chrome.exe", "title": "Teams_Summary.pdf - Google Chrome", "plat": "Microsoft Teams"},
            {"hwnd": 103, "pid": 3, "proc_name": "acrobat.exe", "title": "Google_Meet_Notes.pdf - Adobe Acrobat Reader", "plat": "Google Meet"},
            {"hwnd": 104, "pid": 4, "proc_name": "msedge.exe", "title": "file:///C:/Zoom_Meeting.pdf - Microsoft Edge", "plat": "Zoom"},
        ]
        for w in fake_windows:
            self.assertTrue(
                _is_non_meeting_document_window(w["title"].lower(), w["proc_name"]),
                f"Expected document window to be rejected: {w}",
            )
            self.assertTrue(
                is_non_call_hub(w["plat"], w["title"]),
                f"Expected is_non_call_hub to ignore document: {w}",
            )

        self.assertIsNone(classify(r"c:\program files\slack\slack.exe"))
        self.assertFalse(GoogleMeetTabCaptureProvider.is_google_meet_title("Zoom_Meeting.pdf - Work - Microsoft Edge"))
        self.assertFalse(GoogleMeetTabCaptureProvider.is_google_meet_title("Google_Meet_Summary.pdf - Google Chrome"))
        self.assertTrue(GoogleMeetTabCaptureProvider.is_google_meet_title("Meet - abc-defg-hij - Google Chrome"))

        generic = GenericCallWindowCaptureProvider()
        bounds = (0, 0, 1280, 720)
        self.assertIsNone(generic.inspect_window(101, 1, "msedge.exe", "Chrome_WidgetWin_1", "Zoom_Meeting.pdf - Work - Microsoft Edge", bounds))
        self.assertIsNone(generic.inspect_window(102, 2, "chrome.exe", "Chrome_WidgetWin_1", "Teams_Summary.pdf - Google Chrome", bounds))
        self.assertIsNone(generic.inspect_window(103, 3, "acrobat.exe", "AcrobatSDIWindow", "Zoom_Meeting.pdf - Adobe Acrobat Reader", bounds))
        self.assertIsNone(generic.inspect_window(104, 4, "slack.exe", "Chrome_WidgetWin_1", "Slack | Huddle", bounds))
        teams_src = generic.inspect_window(202, 5, "msedge.exe", "Chrome_WidgetWin_1", "Weekly Sync | Microsoft Teams", bounds)
        self.assertIsNotNone(teams_src)
        zoom_web_src = generic.inspect_window(203, 6, "chrome.exe", "Chrome_WidgetWin_1", "Zoom Workplace", bounds)
        self.assertIsNotNone(zoom_web_src)
        self.assertEqual(classify(r"c:\program files\zoom\bin\zoom.exe"), ("Zoom", False))
        self.assertEqual(classify(r"c:\program files\teams\ms-teams.exe"), ("Microsoft Teams", False))

    def test_executive_pdf_export_brief_and_detailed_quality(self) -> None:
        m = _sample_noisy_meeting()
        brief_html = TranscriptExporter.to_pdf_html(m, detail_mode="brief", include_transcript=False)
        det_html = TranscriptExporter.to_pdf_html(m, detail_mode="detailed", include_transcript=False)

        self.assertIn("Executive Summary", brief_html)
        self.assertIn("Action Items", brief_html)
        self.assertNotIn("Topics Discussed", brief_html)
        self.assertNotIn("Open Questions", brief_html)

        self.assertIn("Topics Discussed", det_html)
        self.assertIn("Risks &amp; Blockers", det_html)
        self.assertIn("Open Questions", det_html)
        for html_doc in (brief_html, det_html):
            self.assertNotIn("RISKImpact:", html_doc)
            self.assertNotIn("How are you doing today?", html_doc)
            self.assertNotIn("Owner Unknown", html_doc)
            self.assertNotIn("[04:56]", html_doc)

        with tempfile.TemporaryDirectory(prefix="vox_pdf_v23_") as td:
            p_brief = Path(td) / "brief.pdf"
            p_det = Path(td) / "detailed.pdf"
            TranscriptExporter.to_pdf(m, target_path=p_brief, detail_mode="brief")
            TranscriptExporter.to_pdf(m, target_path=p_det, detail_mode="detailed")
            self.assertTrue(p_brief.exists() and p_brief.stat().st_size > 2000)
            self.assertTrue(p_det.exists() and p_det.stat().st_size > 2000)
            self.assertTrue(p_brief.read_bytes().startswith(b"%PDF-"))
            self.assertTrue(p_det.read_bytes().startswith(b"%PDF-"))

    def test_unified_summary_box_modes_copy_and_no_slack(self) -> None:
        m = _sample_noisy_meeting()
        tab = VoxSummaryDeliverablesTab(theme="light")
        try:
            tab.load_meeting_data(m)
            self.assertTrue(hasattr(tab, "unified_doc_card"))
            self.assertTrue(tab.card_summary._flat_embedded)
            self.assertTrue(tab.decisions_widget._clean_summary_mode)
            self.assertTrue(tab.actions_widget._clean_summary_mode)
            self.assertFalse(hasattr(tab, "btn_copy_slack_checklists"))
            self.assertFalse(hasattr(tab, "btn_copy_slack_inline"))
            self.assertFalse(hasattr(tab.actions_widget, "btn_copy_slack"))

            # Brief mode (default): shows Summary, Takeaways, Decisions, Actions; hides Topics & deep sections
            self.assertEqual(tab._detail_mode, "brief")
            self.assertFalse(tab.card_summary.isHidden())
            self.assertFalse(tab.card_takeaways.isHidden())
            self.assertFalse(tab.card_decisions.isHidden())
            self.assertFalse(tab.card_actions.isHidden())
            self.assertTrue(tab.card_topics.isHidden())
            self.assertTrue(tab.card_followups.isHidden())
            self.assertTrue(tab.card_risks.isHidden())
            self.assertTrue(tab.card_questions.isHidden())

            brief_overall = tab._section_plain_text("overall_brief")
            self.assertIn("Northstar PLM Migration Sync", brief_overall)
            self.assertIn("Executive Summary", brief_overall)
            self.assertIn("Action Items", brief_overall)
            self.assertNotIn("Topics Discussed", brief_overall)
            self.assertNotIn("How are you doing today?", brief_overall)

            # Switch to Detailed mode
            tab.detail_mode_control.set_current("detailed", emit=True)
            self.assertEqual(tab._detail_mode, "detailed")
            self.assertFalse(tab.card_topics.isHidden())
            self.assertFalse(tab.card_risks.isHidden())
            self.assertFalse(tab.card_questions.isHidden())

            detailed_overall = tab._section_plain_text("overall_detailed")
            self.assertIn("Topics Discussed", detailed_overall)
            self.assertIn("Risks & Blockers", detailed_overall)
            self.assertIn("Open Questions", detailed_overall)
            self.assertNotIn("How are you doing today?", detailed_overall)
            self.assertNotIn("RISKImpact:", detailed_overall)

            topics_copy = tab._section_plain_text("topics")
            self.assertIn("SQ Import Data Validation", topics_copy)
            self.assertNotIn("01:59", topics_copy)
            self.assertNotIn("How are you doing today?", topics_copy)

            oq_copy = tab._section_plain_text("questions")
            self.assertIn("accounting reference", oq_copy)
            self.assertNotIn("How are you doing today?", oq_copy)
            self.assertNotIn("Raised by", oq_copy)
        finally:
            tab.close()

    def test_multi_select_bulk_delete_and_light_confirm_theme(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_nt_v23_", ignore_cleanup_errors=True) as td:
            cfg = AppConfig(storage_dir=Path(td), theme="light", auto_record_calls=False, prompt_on_call=False)
            ctrl = AppController(cfg)
            try:
                id1 = ctrl.db.create_meeting(title="Sync Alpha", source_platform="Microsoft Teams")
                id2 = ctrl.db.create_meeting(title="Sync Beta", source_platform="Zoom")
                id3 = ctrl.db.create_meeting(title="Sync Gamma", source_platform="Google Meet")
                for mid in (id1, id2, id3):
                    ctrl.db.update_meeting(mid, status="completed", full_transcript="Discussed release.", summary="Release ok.")

                page = NotetakerPage(ctrl)
                page.apply_theme("light")
                try:
                    page.refresh_meetings_list(force=True)
                    self.assertEqual(len(ctrl.db.list_meetings()), 3)

                    page._toggle_multi_select_mode(True)
                    self.assertTrue(page._multi_select_mode)
                    page._checked_meeting_ids = {int(id1), int(id2)}
                    page.refresh_meetings_list(force=True)

                    checked_count = 0
                    for i in range(page.meetings_list.count()):
                        it = page.meetings_list.item(i)
                        if bool(it.data(VoxMeetingListDelegate.ROLE_CHECKED)):
                            checked_count += 1
                    self.assertEqual(checked_count, 2)

                    captured = {}

                    def _fake_confirm(parent, title, text, **kwargs):
                        captured["theme"] = kwargs.get("theme")
                        captured["title"] = title
                        return True

                    with patch("voxlocal.ui.notetaker_page.show_themed_confirm", side_effect=_fake_confirm):
                        deleted = page._delete_checked_meetings()
                    self.assertTrue(deleted)
                    self.assertEqual(captured.get("theme"), "light")
                    remaining = ctrl.db.list_meetings()
                    self.assertEqual([int(r["id"]) for r in remaining], [int(id3)])

                    page.apply_theme("dark")
                    page._selected_meeting_id = int(id3)
                    with patch("voxlocal.ui.notetaker_page.show_themed_confirm", side_effect=_fake_confirm):
                        page._delete_selected_meeting()
                    self.assertEqual(captured.get("theme"), "dark")
                    self.assertEqual(len(ctrl.db.list_meetings()), 0)
                finally:
                    page.close()
            finally:
                ctrl.shutdown()

    def test_visible_scrollbars_and_app_palette_sync(self) -> None:
        qss_light = get_scrollbar_qss("light")
        qss_dark = get_scrollbar_qss("dark")
        self.assertIn("width: 10px", qss_light)
        self.assertIn("height: 10px", qss_light)
        self.assertIn("width: 10px", qss_dark)

        with tempfile.TemporaryDirectory(prefix="vox_mw_v23_", ignore_cleanup_errors=True) as td:
            cfg = AppConfig(storage_dir=Path(td), theme="light", auto_record_calls=False, prompt_on_call=False)
            ctrl = AppController(cfg)
            win = MainWindow(ctrl)
            try:
                win.apply_theme("light")
                pal_light = self.app.palette()
                self.assertGreater(pal_light.color(QPalette.ColorRole.Window).lightness(), 200)
                self.assertIn("QScrollBar::handle:vertical", win.notetaker_page.styleSheet())
                self.assertIn("QScrollBar::handle:vertical", win.notetaker_page.studio.tab_summary.summary_scroll.styleSheet())
                if hasattr(win.notetaker_page, "ask_rail_browser"):
                    self.assertIn("QScrollBar::handle:vertical", win.notetaker_page.ask_rail_browser.styleSheet())

                win.apply_theme("dark")
                pal_dark = self.app.palette()
                self.assertLess(pal_dark.color(QPalette.ColorRole.Window).lightness(), 60)
            finally:
                win.close()
                ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()

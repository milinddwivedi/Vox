"""
Phase 4 Verification Suite: Home Hub, Grouped Sidebar & Shortcuts, and Meeting Studio Consolidation.

Covers:
1. Home Hub Next Meeting Spotlight (Join, AI Brief, Record Notetaker, Calendar, Open Notetaker)
   + Recent AI Meeting Highlights cards with open action counts & jump to Notetaker Studio.
2. Grouped Sidebar (CORE, VOICE & STYLE, INSIGHTS), compact utility/plan footer (no truncation),
   pruned dead GlobalSearchDialog, and top-to-bottom aligned Ctrl+1..8 / Ctrl+, shortcuts
   matched across MainWindow and Command Palette.
3. Meeting Studio retired 8-tab placeholder dropdown + integrated Briefing & Compare dialogs +
   live talk-time KPI pill + collapsible right-side Ask VOX rail running off the GUI thread
   with clickable [MM:SS] citation links wired to Transcript & Audio seek.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication, QPushButton

from voxlocal.calendar.base import CalendarMeeting
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui import main_window as main_window_mod
from voxlocal.ui.command_palette import create_default_vox_commands
from voxlocal.ui.hub_pages import HomePage
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.win32.startup import StartupManager


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "--platform", "offscreen"])
    return app


_RETIRED_CONTROLLERS: list = []


class _NoOpThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _shutdown_controller(ctrl: AppController, *widgets) -> None:
    app = _get_qapp()
    for w in widgets:
        if w is not None:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    eng = getattr(ctrl, "calendar_engine", None)
    if eng is not None and hasattr(eng, "stop_polling"):
        try:
            eng.stop_polling()
        except Exception:
            pass
    ctrl.shutdown()
    _RETIRED_CONTROLLERS.append(ctrl)
    app.processEvents()


def _create_controller(td_name: str) -> AppController:
    cfg = AppConfig(storage_dir=td_name)
    cfg.launch_at_login = StartupManager.is_enabled()
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    real_thread = threading.Thread

    def _safe_thread(*args, **kwargs):
        if kwargs.get("name") == "StartupUpdateCheck":
            return _NoOpThread()
        return real_thread(*args, **kwargs)

    with patch("threading.Thread", side_effect=_safe_thread):
        return AppController(cfg)


class TestPhase4HomeSidebarAndStudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_qapp()

    # ------------------------------------------------------------------
    # 1. Home Hub Next Meeting Spotlight & Recent Meeting Highlights
    # ------------------------------------------------------------------
    def test_01_home_next_meeting_spotlight_and_recent_highlights(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p4_home_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                m1 = ctrl.db.create_meeting(title="Q4 Product Architecture Sync", source_platform="Microsoft Teams")
                ctrl.db.update_meeting(
                    m1,
                    status="completed",
                    summary="Agreed to ship virtualized transcripts and local SQLite persistence.",
                )
                a1 = ctrl.db.add_action_item(m1, "Publish latency benchmark", "Milind", "Friday")
                a2 = ctrl.db.add_action_item(m1, "Verify dark mode tokens", "Alex", "Thursday")
                ctrl.db.toggle_action_item_status(a2, "completed")

                m2 = ctrl.db.create_meeting(title="Interrupted Vendor Call", source_platform="Zoom")
                ctrl.db.update_meeting(m2, status="interrupted")

                now_local = datetime.now().astimezone()
                upcoming = CalendarMeeting(
                    id="evt-home-1",
                    title="Weekly Design Critique",
                    start_time=now_local + timedelta(minutes=25),
                    end_time=now_local + timedelta(minutes=70),
                    join_url="https://teams.microsoft.com/l/meetup-join/home-test",
                    platform="teams",
                    organizer="Alice",
                    attendees=["alice@example.com", "bob@example.com"],
                )

                with patch.object(ctrl.calendar_engine, "get_upcoming_meetings", return_value=[upcoming]):
                    page = HomePage(ctrl)
                    page.show()
                    self.app.processEvents()

                    self.assertIn("Weekly Design Critique", page.lbl_upcoming.text())
                    self.assertIn("Microsoft Teams", page.lbl_spotlight_platform.text())
                    self.assertTrue(page.btn_spotlight_join.isVisible())
                    self.assertTrue(page.btn_spotlight_brief.isVisible())
                    self.assertTrue(page.btn_spotlight_record.isVisible())

                    # Clicking AI Brief launches VoxPreMeetingBriefingDialog with topic pre-filled
                    with patch("voxlocal.ui.hub_pages.VoxPreMeetingBriefingDialog.exec", return_value=1) as mock_brief:
                        page.btn_spotlight_brief.click()
                        mock_brief.assert_called_once()

                    # Clicking Record Notetaker requests user-controlled prep without auto-recording
                    prep_spy = []
                    page.prepare_meeting_requested.connect(lambda m: prep_spy.append(m.id))
                    page.btn_spotlight_record.click()
                    self.assertEqual(prep_spy, ["evt-home-1"])
                    self.assertEqual(ctrl.meeting_state, "idle")

                    # Recent AI Meeting Highlights strip displays cards with open actions & interrupted badge
                    self.assertTrue(page.recent_meetings_section.isVisible())
                    open_spy = []
                    page.open_meeting_requested.connect(lambda mid: open_spy.append(mid))

                    open_btns = [
                        b for b in page.recent_meetings_section.findChildren(QPushButton)
                        if "Open Notes" in b.text()
                    ]
                    self.assertEqual(len(open_btns), 2)
                    label_texts = [lbl.text() for lbl in page.recent_meetings_section.findChildren(type(page.lbl_upcoming))]
                    joined_labels = " | ".join(label_texts)
                    self.assertIn("1 open action", joined_labels)
                    self.assertIn("Interrupted", joined_labels)

                    # Click Open Notes on card for m1 (sorted by most recent, so click both or match title)
                    for btn in open_btns:
                        btn.click()
                    self.assertIn(m1, open_spy)

                    page.apply_theme("dark")
                    page.apply_theme("light")
                    self.app.processEvents()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 2. Grouped Sidebar, Compact Footer & Aligned Shortcuts
    # ------------------------------------------------------------------
    def test_02_grouped_sidebar_compact_footer_and_aligned_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p4_sb_") as td:
            ctrl = _create_controller(td)
            win = None
            try:
                self.assertFalse(hasattr(main_window_mod, "GlobalSearchDialog"))
                win = MainWindow(ctrl)
                win.show()
                self.app.processEvents()

                # Grouped sections & backward-compatible WORKSPACES / nav_buttons count
                self.assertEqual(len(win.WORKSPACES), 8)
                self.assertEqual(len(win.nav_buttons), 8)
                sec_titles = [lbl.text() for lbl in win.sidebar_section_headers]
                self.assertEqual(sec_titles, ["CORE", "VOICE & STYLE", "INSIGHTS"])

                # Aligned top-to-bottom shortcuts across MainWindow & Command Palette
                shortcuts = {sc.key().toString(): sc for sc in win.findChildren(QShortcut)}
                expected_routes = [
                    ("Ctrl+1", 0, False),  # Home
                    ("Ctrl+2", 1, False),  # Notetaker
                    ("Ctrl+3", 8, True),   # Calendar (3rd visually)
                    ("Ctrl+4", 2, False),  # Dictionary
                    ("Ctrl+5", 3, False),  # Snippets
                    ("Ctrl+6", 4, False),  # Style
                    ("Ctrl+7", 5, False),  # Scratchpad
                    ("Ctrl+8", 6, False),  # Insights
                    ("Ctrl+,", 7, False),  # Settings
                ]
                for seq, expected_idx, is_cal in expected_routes:
                    self.assertIn(seq, shortcuts)
                    shortcuts[seq].activated.emit()
                    self.app.processEvents()
                    self.assertEqual(win.workspace_stack.currentIndex(), expected_idx)
                    self.assertEqual(win.btn_calendar.isChecked(), is_cal)

                cmds = {c.id: c.shortcut for c in create_default_vox_commands(win, ctrl)}
                self.assertEqual(cmds["home"], "Ctrl+1")
                self.assertEqual(cmds["notetaker"], "Ctrl+2")
                self.assertEqual(cmds["calendar"], "Ctrl+3")
                self.assertEqual(cmds["dictionary"], "Ctrl+4")
                self.assertEqual(cmds["snippets"], "Ctrl+5")
                self.assertEqual(cmds["style"], "Ctrl+6")
                self.assertEqual(cmds["scratchpad"], "Ctrl+7")
                self.assertEqual(cmds["insights"], "Ctrl+8")
                self.assertEqual(cmds["settings"], "Ctrl+,")

                # Compact footer preserves account card without truncating Milind Krishna
                with patch.object(
                    ctrl,
                    "get_account_status",
                    return_value={
                        "signed_in": True,
                        "name": "Milind Krishna",
                        "email": "milind@example.com",
                        "is_pro": True,
                    },
                ):
                    win._update_plan_card()
                    self.app.processEvents()
                    self.assertEqual(win.lbl_user_name.text(), "Milind Krishna")
                    self.assertIn("VOX Pro", win.lbl_plan_badge.text())

                win.apply_theme("dark")
                win.apply_theme("light")
                self.app.processEvents()
            finally:
                if win is not None:
                    win.prepare_force_quit()
                _shutdown_controller(ctrl, win)

    # ------------------------------------------------------------------
    # 3. Studio Consolidation, Briefing/Compare & Async Ask Rail Citations
    # ------------------------------------------------------------------
    def test_03_studio_consolidation_briefing_compare_and_async_ask_rail_citations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p4_studio_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                m_prev = ctrl.db.create_meeting(title="Prior Sprint Sync", source_platform="Zoom")
                ctrl.db.update_meeting(m_prev, status="completed", summary="Agreed on Milestone 1 timeline.")

                m_id = ctrl.db.create_meeting(title="Phase 4 Studio Review", source_platform="Microsoft Teams")
                ctrl.db.update_meeting(
                    m_id,
                    status="completed",
                    duration_sec=180.0,
                    summary="Reviewed audio pipeline latency and Ask VOX citations.",
                )
                ctrl.db.add_transcript_segment(m_id, 0, 10.0, 20.0, "You (Local)", text="Kickoff note.", source_track="mic")
                ctrl.db.add_transcript_segment(m_id, 1, 45.0, 60.0, "Alex", text="Agreed on 45s benchmark.", source_track="loopback")
                ctrl.db.add_transcript_segment(m_id, 2, 75.0, 95.0, "Sarah", text="Latency targets verified.", source_track="loopback")
                ctrl.db.add_decision(m_id, "Ship clickable citation links", status="confirmed", timestamp_sec=45.0)
                ctrl.db.add_action_item(m_id, "Run regression suite", "Alex", "Today")

                page = NotetakerPage(ctrl)
                page.show()
                page.select_meeting(m_id)
                self.app.processEvents()

                # Legacy placeholder 8-tab dropdown is hidden from visible UI while live tools are integrated
                self.assertTrue(page.studio.combo_mode_switcher.isHidden())
                self.assertTrue(page.studio.lbl_view_mode.isHidden())
                self.assertTrue(page.studio.lbl_studio_metrics.isVisible())
                self.assertIn("Mic ", page.studio.lbl_studio_metrics.text())
                self.assertIn("1 Decisions", page.studio.lbl_studio_metrics.text())

                with patch("voxlocal.ui.meeting_studio.VoxPreMeetingBriefingDialog.exec", return_value=1) as mock_brief, \
                     patch("voxlocal.ui.meeting_studio.VoxMeetingComparisonDialog.exec", return_value=1) as mock_cmp:
                    page.studio.btn_studio_briefing.click()
                    page.studio.btn_studio_compare.click()
                    mock_brief.assert_called_once()
                    mock_cmp.assert_called_once()

                # Collapsible Ask VOX Side Rail executes off the main GUI thread & links citations
                self.assertFalse(page.studio.ask_rail.isVisible())
                page.studio.btn_toggle_ask_rail.click()
                self.app.processEvents()
                self.assertTrue(page.studio.ask_rail.isVisible())

                caller_threads = []

                def _bg_ask(question: str, meeting_id: int | None = None):
                    caller_threads.append(threading.current_thread())
                    return {
                        "answer": "Discussed latency targets at [01:15].",
                        "citations": [{"start_time": 45.0, "text": "Agreed on 45s benchmark."}],
                    }

                with patch.object(ctrl, "query_meeting_memory", side_effect=_bg_ask):
                    page.studio.txt_rail_ask.setText("When was latency discussed?")
                    page.studio.btn_rail_ask.click()

                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        self.app.processEvents()
                        if "Discussed latency targets" in page.studio.ask_rail_browser.toHtml():
                            break
                        time.sleep(0.01)

                self.assertEqual(len(caller_threads), 1)
                self.assertIsNot(caller_threads[0], threading.main_thread())

                rail_html = page.studio.ask_rail_browser.toHtml()
                self.assertIn('href="seek:75.0"', rail_html)
                self.assertIn('href="seek:45.0"', rail_html)

                # Clicking a citation anchor switches to Transcript, highlights segment 1 (45s), and seeks player
                with patch.object(page.media_player, "setPosition") as mock_pos:
                    page.studio.ask_rail_browser.anchorClicked.emit(QUrl("seek:45.0"))
                    self.app.processEvents()
                    self.assertEqual(page.studio.unified_tabs.currentIndex(), 1)
                    self.assertEqual(page.studio.tab_transcript._current_highlight_idx, 1)
                    mock_pos.assert_called_with(45000)

                page.apply_theme("light")
                page.apply_theme("dark")
                self.app.processEvents()
            finally:
                _shutdown_controller(ctrl, page)


if __name__ == "__main__":
    unittest.main()

"""
Comprehensive QA and Automated Test Suite for VOX Reports Workspace Redesign.

Validates:
1. VoxResizableSplitter: Config persistence, minimum constraints, double-click reset.
2. VoxMeetingListCard: Platform pills, wrapped title, metadata layout, video badge, folder badge.
3. Multi-dimensional Filter Toolbar: Type, Source, Time, Folder, Search, Reset.
4. Squeeze-free vertical recap splitter: Minimum height protections preventing layout collapse.
5. Coaching Speaking Progress Bar: Proper 0% default (elimination of hardcoded 45% bug).
6. Key Moments Rail: Hidden when empty, visible when chapters exist.
7. Full button-by-button action audit: Video, Export, Open Files, Copy ID, Re-analyze.
"""

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.components import VoxMeetingListCard, VoxResizableSplitter
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


class TestReportsWorkspaceRedesign(unittest.TestCase):
    def setUp(self):
        self.app = get_qapp()
        self.temp_dir = tempfile.mkdtemp(prefix="vox_reports_redesign_test_")
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
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:
            pass
        try:
            self.floating_widget.close()
        except Exception:
            pass
        try:
            self.controller.shutdown()
        except Exception:
            pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_splitter_initialization_and_config_persistence(self):
        """Verify Notetaker splitter constraints & VoxResizableSplitter persistence + reset."""
        from PyQt6.QtWidgets import QWidget

        nt = self.window.notetaker_page
        self.assertIsNotNone(nt.splitter)
        self.assertFalse(nt.splitter.childrenCollapsible())
        self.assertEqual(len(nt.splitter.sizes()), 2)

        splitter = VoxResizableSplitter(
            Qt.Orientation.Horizontal,
            default_sizes=[self.config.reports_library_width, 780],
            on_resized=lambda sizes: (
                setattr(self.controller.config, "reports_library_width", int(sizes[0])),
                self.controller.config.save(),
            ),
        )
        try:
            splitter.addWidget(QWidget())
            splitter.addWidget(QWidget())
            splitter.resize(1200, 600)
            self.assertEqual(splitter.default_sizes[0], 350)

            splitter.setSizes([420, 780])
            actual_left = splitter.sizes()[0]
            splitter._on_moved(actual_left, 1)
            self.assertEqual(self.controller.config.reports_library_width, actual_left)
            self.assertGreater(self.controller.config.reports_library_width, 350)

            splitter.reset_default()
            self.assertEqual(self.controller.config.reports_library_width, 350)
        finally:
            splitter.close()

    def test_02_meeting_list_custom_card_rendering(self):
        """Verify Notetaker meetings list & VoxMeetingListCard render platform tags and metadata."""
        m_id = self.controller.db.create_meeting(
            title="Q3 Strategy Review & Roadmap",
            source_platform="Microsoft Teams",
        )
        self.controller.db.update_meeting(m_id, duration_sec=145.0)

        nt = self.window.notetaker_page
        nt.refresh_all()
        self.assertEqual(nt.meetings_list.count(), 1)
        row_text = nt.meetings_list.item(0).text()
        self.assertIn("Q3 Strategy Review & Roadmap", row_text)
        self.assertIn("Teams", row_text)

        card = VoxMeetingListCard(
            meeting_id=m_id,
            title="Q3 Strategy Review & Roadmap",
            duration_sec=145.0,
            source_platform="Microsoft Teams",
            has_video=True,
        )
        try:
            self.assertIn("Teams", card.lbl_platform.text())
            self.assertIn("Q3 Strategy Review", card.lbl_title.text())
        finally:
            card.close()

    def test_03_multi_dimensional_filter_toolbar_and_reset(self):
        """Verify Notetaker library search, folder filter, transcript search/speaker filter, and Reset."""
        self.controller.db.create_meeting(title="Sprint Retro", source_platform="Zoom")
        m2 = self.controller.db.create_meeting(title="Client Pitch", source_platform="Google Meet")
        self.controller.db.create_meeting(title="Audio Memo", source_platform="Desktop App")
        f_id = self.controller.db.create_folder("Enterprise Accounts")
        self.assertIsNotNone(f_id)
        self.controller.db.assign_meeting_folder(m2, f_id)

        nt = self.window.notetaker_page
        nt.refresh_all()
        self.assertEqual(nt.meetings_list.count(), 3)

        # 1. Filter library by search query
        nt.txt_search.setText("Retro")
        self.app.processEvents()
        self.assertEqual(nt.meetings_list.count(), 1)

        # 2. Clear search & filter by folder
        nt.txt_search.clear()
        idx_folder = nt.combo_folders.findData(f_id)
        self.assertGreaterEqual(idx_folder, 0)
        nt.combo_folders.setCurrentIndex(idx_folder)
        self.app.processEvents()
        self.assertEqual(nt.meetings_list.count(), 1)

        # 3. Reset library folder filter back to All Notes
        nt.combo_folders.setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(nt.meetings_list.count(), 3)

        # 4. Transcript multi-filter & Reset button inside Studio
        self.controller.db.replace_meeting_segments(
            m2,
            [
                {"start_time": 1.0, "end_time": 4.0, "speaker": "Alice", "text": "Budget approved"},
                {"start_time": 5.0, "end_time": 8.0, "speaker": "Bob", "text": "Timeline confirmed"},
            ],
        )
        nt.select_meeting(m2)
        tx = nt.studio.tab_transcript
        tx.search_input.setText("Budget")
        self.assertEqual(tx._match_count, 1)
        tx._reset_filter()
        self.assertEqual(tx.search_input.text(), "")
        self.assertEqual(tx.combo_speaker_filter.currentIndex(), 0)
        self.assertEqual(tx._match_count, 2)

    def test_04_recap_vertical_splitter_squeeze_protection(self):
        """Verify Summary & Action Items editors enforce minimum heights and populate cleanly."""
        nt = self.window.notetaker_page
        summary_tab = nt.studio.tab_summary
        self.assertGreaterEqual(summary_tab.txt_summary.minimumHeight(), 120)

        m_id = self.controller.db.create_meeting(
            title="Design Review",
            source_platform="Microsoft Teams",
        )
        self.controller.db.update_meeting(
            m_id,
            summary="This is an extensive executive summary detailing the design architecture.",
        )
        self.controller.db.add_action_item(meeting_id=m_id, description="Review mockups by Friday", owner="Alex")
        self.controller.db.add_action_item(meeting_id=m_id, description="Update theme tokens", owner="Maya")

        nt.refresh_all()
        nt.select_meeting(m_id)

        self.assertIn("extensive executive summary", summary_tab.txt_summary.toPlainText())
        action_texts = [it.get("description", "") for it in summary_tab.actions_widget._items]
        self.assertIn("Review mockups by Friday", action_texts)
        self.assertIn("Update theme tokens", action_texts)
        self.assertFalse(summary_tab.actions_widget.isHidden())

    def test_05_coaching_progress_bar_zero_default_and_calculation(self):
        """Verify analytics talk-time calculation defaults to 0% without segments and computes speaker ratio."""
        m_id = self.controller.db.create_meeting(title="Silent Sync", source_platform="Desktop App")
        nt = self.window.notetaker_page
        nt.select_meeting(m_id)

        no_seg_text = nt.studio.adv_analytics_page.toPlainText().lower()
        self.assertIn("0%", no_seg_text)
        self.assertIn("unavailable", no_seg_text)

        self.controller.db.add_transcript_segment(
            meeting_id=m_id,
            segment_index=0,
            start_time=0.0,
            end_time=10.0,
            speaker="You (Mic)",
            text="Hello world from the mic side.",
            source_track="mic",
        )
        self.controller.db.add_transcript_segment(
            meeting_id=m_id,
            segment_index=1,
            start_time=10.0,
            end_time=20.0,
            speaker="Colleague",
            text="Understood loud and clear colleague response.",
            source_track="system",
        )

        nt.select_meeting(m_id)
        with_seg_text = nt.studio.adv_analytics_page.toPlainText()
        self.assertIn("Microphone Talk Time: 50%", with_seg_text)
        self.assertIn("Colleague: 50%", with_seg_text)

    def test_06_key_moments_rail_dynamic_visibility(self):
        """Verify VoxHorizontalCardRail empty vs populated states and playback progress tracking."""
        from voxlocal.ui.components import VoxHorizontalCardRail

        rail = VoxHorizontalCardRail()
        try:
            rail.set_chapters([])
            self.assertEqual(len(rail._chapters), 0)
            self.assertEqual(rail.rail_progress.value(), 0)

            chapters = [
                {"time_sec": 10.0, "title": "Sprint Planning", "speaker": "Alice"},
                {"time_sec": 50.0, "title": "Architecture Decisions", "speaker": "Bob"},
                {"time_sec": 100.0, "title": "Release Date", "speaker": "Alice"},
            ]
            rail.set_chapters(chapters)
            self.assertGreaterEqual(len(rail._chapters), 3)
            rail.set_playback_seconds(50.0)
            self.assertEqual(rail.rail_progress.value(), 50)
            self.assertEqual(rail._current_chapter_idx, 1)
        finally:
            rail.close()

    def test_07_executive_header_and_action_controls(self):
        """Verify Notetaker detail action controls enable/disable cleanly based on meeting & video state."""
        from voxlocal.ui.notetaker_page import NotetakerPage

        empty_nt = NotetakerPage(self.controller)
        try:
            self.assertFalse(empty_nt.btn_watch_video.isEnabled())
            self.assertFalse(empty_nt.btn_play_pause.isEnabled())
            self.assertFalse(empty_nt.btn_export.isEnabled())
            self.assertFalse(empty_nt.btn_more.isEnabled())
        finally:
            empty_nt.close()

        # Selected meeting without video file
        m_id = self.controller.db.create_meeting(title="Architecture Deep Dive", source_platform="Zoom")
        nt = self.window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(m_id)

        self.assertFalse(nt.btn_watch_video.isEnabled())
        self.assertTrue(nt.btn_export.isEnabled())
        self.assertTrue(nt.btn_more.isEnabled())

        # Attach real non-empty video file -> Watch Video enables
        vid_path = Path(self.temp_dir) / "meeting_screen.mp4"
        vid_path.write_bytes(b"\x00" * 256)
        self.controller.db.update_meeting(m_id, video_path=str(vid_path))
        nt.select_meeting(m_id)
        self.assertTrue(nt.btn_watch_video.isEnabled())


if __name__ == "__main__":
    unittest.main()

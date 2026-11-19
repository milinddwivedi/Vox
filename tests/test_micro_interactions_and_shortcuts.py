"""
Unit and regression tests for Micro-Interactions, Shortcuts, Live Search Filters,
and Cross-Tab State Synchronization in VoxLocal.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut
from PyQt6.QtWidgets import QApplication, QLabel

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.win32.startup import StartupManager
from voxlocal.ui.command_palette import VoxCommandPaletteDialog, create_default_vox_commands
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow


class TestMicroInteractionsAndShortcuts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
        self.config.launch_at_login = StartupManager.is_enabled()
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)

        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda tab_idx=0: None,
            on_exit_app=lambda: None,
        )
        self.main_window = MainWindow(
            self.controller,
            self.floating_widget,
            on_exit_app=lambda: None,
        )
        self.main_window.show()

    def tearDown(self):
        self.main_window.close()
        self.floating_widget.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_01_tab_shortcuts_ctrl_1_through_comma(self):
        """Test all 8 workspace shortcuts (Ctrl+1..Ctrl+7, Ctrl+,) switch workspaces cleanly."""
        shortcuts = {sc.key().toString(): sc for sc in self.main_window.findChildren(QShortcut)}
        expected = [ws["shortcut"] for ws in self.main_window.WORKSPACES]
        self.assertEqual(len(expected), 8)
        for idx, seq in enumerate(expected):
            self.assertIn(seq, shortcuts)
            shortcuts[seq].activated.emit()
            self.app.processEvents()
            self.assertEqual(self.main_window.workspace_stack.currentIndex(), idx)

    def test_02_settings_search_and_theme_shortcuts(self):
        """Test Ctrl+, opens Settings, Ctrl+T toggles theme, and Home/Palette searches debounce DB reads."""
        shortcuts = {sc.key().toString(): sc for sc in self.main_window.findChildren(QShortcut)}
        shortcuts["Ctrl+,"].activated.emit()
        self.app.processEvents()
        self.assertEqual(self.main_window.workspace_stack.currentIndex(), 7)

        initial_theme = self.controller.theme
        shortcuts["Ctrl+T"].activated.emit()
        self.app.processEvents()
        self.assertNotEqual(self.controller.theme, initial_theme)

        # Home feed live filter (200ms debounced)
        self.controller.db.save_dictation("raw alpha", "Alpha deployment ready", duration_sec=2.0)
        self.controller.db.save_dictation("raw beta", "Beta invoice sent", duration_sec=1.5)
        self.main_window.navigate_to_workspace("home")
        self.app.processEvents()

        home = self.main_window.home_page
        with patch.object(
            self.controller.db,
            "list_dictation_history",
            wraps=self.controller.db.list_dictation_history,
        ) as mock_hist:
            for partial in ("A", "Al", "Alp", "Alpha"):
                home.txt_feed_search.setText(partial)
            self.app.processEvents()
            self.assertTrue(home._feed_search_timer.isActive())
            self.assertEqual(mock_hist.call_count, 0, "Rapid keystrokes must debounce DB queries")

            deadline = time.perf_counter() + 1.5
            while time.perf_counter() < deadline:
                self.app.processEvents()
                if not home._feed_search_timer.isActive():
                    break
                time.sleep(0.015)

            self.assertEqual(mock_hist.call_count, 1)

        labels = [lbl.text() for lbl in home.feed_container.findChildren(QLabel)]
        self.assertTrue(any("Alpha deployment ready" in t for t in labels))
        self.assertFalse(any("Beta invoice sent" in t for t in labels))

        # Command Palette: static commands remain synchronous while DB search debounces
        palette = VoxCommandPaletteDialog(
            theme=self.controller.theme,
            commands=create_default_vox_commands(self.main_window, self.controller),
            controller=self.controller,
            parent=self.main_window,
        )
        try:
            palette.txt_search.setText("Set")
            self.app.processEvents()
            self.assertTrue(any(c.id == "settings" for c in palette._filtered_commands))

            with patch.object(
                palette,
                "_search_database_matches",
                wraps=palette._search_database_matches,
            ) as mock_db_search:
                for partial in ("A", "Al", "Alp", "Alpha"):
                    palette.txt_search.setText(partial)
                self.app.processEvents()
                self.assertTrue(palette._db_search_timer.isActive())
                self.assertEqual(mock_db_search.call_count, 0)

                deadline = time.perf_counter() + 1.5
                while time.perf_counter() < deadline:
                    self.app.processEvents()
                    if not palette._db_search_timer.isActive():
                        break
                    time.sleep(0.015)

                self.assertEqual(mock_db_search.call_count, 1)
                self.assertTrue(any("Alpha deployment ready" in c.title for c in palette._filtered_commands))
        finally:
            palette.close()

    def test_03_notetaker_action_items_sync(self):
        """Test Notetaker Studio renders meeting action items cleanly."""
        m_id = self.controller.db.create_meeting(title="Sprint Retro")
        self.controller.db.add_action_item(m_id, "Prepare presentation deck", "Alice", "Monday")
        self.controller.db.add_action_item(m_id, "Verify audio codecs", "Bob", "Friday")
        nt = self.main_window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(m_id)
        self.app.processEvents()

        chk_txt = nt.studio.tab_summary.txt_participant_checklists.toPlainText()
        self.assertIn("Prepare presentation deck", chk_txt)
        self.assertIn("Verify audio codecs", chk_txt)

    def test_04_dictionary_live_filter(self):
        """Test live filtering in Dictionary workspace without manual reload."""
        init_rows = len(self.controller.db.list_vocabulary(active_only=False))
        self.controller.db.upsert_vocabulary("machine learning", "ML")
        self.controller.db.upsert_vocabulary("artificial intelligence", "AI")
        dp = self.main_window.dictionary_page
        dp.refresh()
        self.app.processEvents()

        self.assertEqual(len(self.controller.db.list_vocabulary(active_only=False)), init_rows + 2)
        dp.txt_search.setText("machine")
        self.app.processEvents()
        labels = [lbl.text() for lbl in dp.list_wrap.findChildren(QLabel)]
        self.assertTrue(any("machine learning" in t for t in labels))
        self.assertFalse(any("artificial intelligence" in t for t in labels))

        dp.txt_search.setText("")
        self.app.processEvents()
        labels_all = [lbl.text() for lbl in dp.list_wrap.findChildren(QLabel)]
        self.assertTrue(any("artificial intelligence" in t for t in labels_all))

    def test_05_notetaker_folder_filter_sync(self):
        """Test creating and filtering meetings by folder inside NotetakerPage."""
        f_id = self.controller.db.create_folder(name="Design Review", folder_type="personal")
        m_id = self.controller.db.create_meeting(title="UX Review")
        m_other = self.controller.db.create_meeting(title="Backend Sync")
        self.controller.db.assign_meeting_folder(m_id, f_id)

        nt = self.main_window.notetaker_page
        nt.refresh_all()
        self.app.processEvents()
        self.assertEqual(nt.meetings_list.count(), 2)

        idx = nt.combo_folders.findData(f_id)
        self.assertGreater(idx, 0)
        nt.combo_folders.setCurrentIndex(idx)
        self.app.processEvents()
        self.assertEqual(nt.meetings_list.count(), 1)
        self.assertEqual(nt.meetings_list.item(0).data(Qt.ItemDataRole.UserRole), m_id)

    def test_06_inline_correction_auto_learns_dictionary_and_toast(self):
        """Test editing dictation entry auto-learns replacement into DictionaryPage."""
        d_id = self.controller.db.save_dictation("use postgre database", "use postgre database", duration_sec=1.5)
        self.controller.apply_dictation_correction(d_id, "use PostgreSQL database")
        self.main_window.dictionary_page.refresh()
        self.app.processEvents()

        vocab = self.controller.db.list_vocabulary(active_only=False)
        self.assertTrue(any(v["spoken_phrase"] == "postgre" and v["replacement"] == "PostgreSQL" for v in vocab))
        labels = [lbl.text() for lbl in self.main_window.dictionary_page.list_wrap.findChildren(QLabel)]
        self.assertTrue(any("PostgreSQL" in t for t in labels))

    def test_07_grouped_navigation_tracks_the_selected_workspace(self):
        """Sidebar navigation buttons and workspace stack stay synchronized."""
        self.assertEqual(len(self.main_window.nav_buttons), 8)
        self.main_window.nav_buttons[2].click()
        self.app.processEvents()

        self.assertEqual(self.main_window.workspace_stack.currentIndex(), 2)
        self.assertTrue(self.main_window.nav_buttons[2].isChecked())

    def test_08_action_item_edits_preserve_status_and_meeting(self):
        """Editable task details persist without changing completion or meeting links."""
        meeting_id = self.controller.db.create_meeting(title="Planning")
        action_id = self.controller.db.add_action_item(
            meeting_id, "Send draft", "Riya", "Friday"
        )
        self.controller.db.toggle_action_item_status(action_id, "completed")

        self.assertTrue(
            self.controller.db.update_action_item(
                action_id, "Send revised draft", "Sam", "Next Monday"
            )
        )
        self.assertFalse(self.controller.db.update_action_item(action_id, "   "))
        self.assertFalse(
            self.controller.db.update_action_item(999999, "Missing task")
        )

        saved = next(
            action
            for action in self.controller.db.list_action_items(limit=100)
            if action["id"] == action_id
        )
        self.assertEqual(saved["meeting_id"], meeting_id)
        self.assertEqual(saved["description"], "Send revised draft")
        self.assertEqual(saved["owner"], "Sam")
        self.assertEqual(saved["due_date"], "Next Monday")
        self.assertEqual(saved["status"], "completed")

    def test_09_notetaker_actions_require_a_selected_meeting(self):
        """Empty Notetaker library keeps video, speakers, export, and playback disabled."""
        nt = self.main_window.notetaker_page
        nt.refresh_all()
        self.assertEqual(nt.meetings_list.count(), 0)
        for button in (
            nt.btn_watch_video,
            nt.btn_speakers,
            nt.btn_export,
            nt.btn_more,
            nt.btn_play_pause,
        ):
            self.assertFalse(button.isEnabled())

    def test_10_notetaker_assign_speakers_updates_segments(self):
        """Assigning participant names renames segments cleanly across transcript storage."""
        m_id = self.controller.db.create_meeting(title="Design Review")
        self.controller.db.add_transcript_segment(m_id, 0, 0.0, 3.0, "Speaker 1", "Let's finalize icons.")
        changed = self.controller.db.rename_meeting_speaker(m_id, "Speaker 1", "Morgan")
        self.assertGreaterEqual(changed, 1)

        segs = self.controller.db.get_meeting_segments(m_id)
        self.assertEqual(segs[0]["speaker"], "Morgan")
        nt = self.main_window.notetaker_page
        nt.refresh_all()
        nt.select_meeting(m_id)
        self.app.processEvents()
        self.assertTrue(nt.btn_speakers.isEnabled())


if __name__ == "__main__":
    unittest.main()

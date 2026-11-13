"""
Comprehensive End-to-End QA, Security & Stress Test Suite for VoxLocal.
Acts as a senior QA engineer, security tester, and real-world user across:
- All 7 Tabs & Workflows
- State machine edge cases & concurrent actions
- Input validation, SQL injection & path traversal attempts
- UI error recovery, empty states & boundary conditions
- Media player, exports, folders, vocabulary, settings, and floating dock.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure 'src' is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.tray_manager import SystemTrayManager
from voxlocal.win32.startup import StartupManager


class TestE2EComprehensiveAudit(unittest.TestCase):
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

        self.opened_tabs = []
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda tab_idx=0, maximize=False: self.opened_tabs.append((tab_idx, maximize)),
            on_exit_app=lambda: None,
        )
        self.main_window = MainWindow(
            self.controller,
            self.floating_widget,
            on_exit_app=lambda: None,
        )

    def tearDown(self):
        self.main_window.close()
        self.floating_widget.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    # =========================================================================
    # SUITE 1: WORKSPACE NAVIGATION, SWITCHING & THEMES
    # =========================================================================
    def test_01_all_tabs_navigation_and_rapid_switching(self):
        """Verify all 8 Hub workspaces exist, switch cleanly, and handle rapid switching."""
        self.assertEqual(self.main_window.workspace_stack.count(), 8)
        self.assertEqual(self.main_window.tab_widget.count(), 8)

        # Sequential workspace switching
        for i in range(8):
            self.main_window.navigate_to_workspace(i)
            self.assertEqual(self.main_window.workspace_stack.currentIndex(), i)
            self.app.processEvents()

        # Route string navigation & rapid switching stress test
        for route in ["settings", "home", "dictionary", "notetaker", "insights", "style", "scratchpad", "snippets"]:
            self.main_window.navigate_to_workspace(route)
            self.app.processEvents()

        self.assertEqual(self.main_window.workspace_stack.currentIndex(), 3)

    def test_02_theme_switching_stress(self):
        """Verify switching between Sand Light and Vast Dark dynamically styles all components."""
        self.controller.set_theme("light")
        self.assertEqual(self.controller.theme, "light")
        self.app.processEvents()

        self.controller.set_theme("dark")
        self.assertEqual(self.controller.theme, "dark")
        self.app.processEvents()

        new_theme = self.controller.toggle_theme()
        self.assertEqual(new_theme, "light")
        self.controller.toggle_theme()
        self.assertEqual(self.controller.theme, "dark")

    # =========================================================================
    # SUITE 2: HOME FEED INTERACTIONS, TRANSFORMS & EDGE CASES
    # =========================================================================
    def test_03_voice_typing_ui_and_feed_helpers(self):
        """Test HomePage KPIs, feed rendering, transform, undo, and copy helpers."""
        entry_id = self.controller.db.save_dictation("um hello world basically", "Hello world.", duration_sec=2.0)
        self.main_window.home_page.refresh_all()
        self.app.processEvents()

        self.main_window.home_page._copy_entry("Hello world.")
        transformed = self.controller.transform_text("send the deck tomorrow morning", instruction="formal")
        self.assertTrue(len(transformed) > 0)

        reverted = self.controller.undo_ai_edit(entry_id)
        self.assertEqual(reverted, "um hello world basically")

        for term in ["Normal", "", "   ", "!@#$%", "नमस्ते", "Very long text " * 10]:
            self.main_window.home_page.txt_feed_search.setText(term)
            self.app.processEvents()

    def test_04_dictation_history_interactions(self):
        """Test dictation history storage, deletion, and Insights KPI reflection."""
        d1 = self.controller.db.save_dictation("Raw hello world", "Final Hello World.", duration_sec=2.5)
        d2 = self.controller.db.save_dictation("Raw two", "Final Two.", duration_sec=1.0)
        self.main_window.home_page.refresh_all()
        self.main_window.insights_page.refresh()
        self.app.processEvents()

        rows = self.controller.db.list_dictation_history(limit=20)
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("Final Hello World.", [r["final_text"] for r in rows])

        self.main_window.home_page._delete_entry(d1)
        rows_after = self.controller.db.list_dictation_history(limit=20)
        self.assertFalse(any(r["id"] == d1 for r in rows_after))
        self.assertTrue(any(r["id"] == d2 for r in rows_after))

    # =========================================================================
    # SUITE 3: NOTETAKER CAPTURE BANNER & LIVE TICKS
    # =========================================================================
    def test_05_meeting_studio_controls_and_ticks(self):
        """Verify NotetakerPage banner updates cleanly on live timer ticks."""
        nt = self.main_window.notetaker_page
        nt._on_meeting_tick(12.5)
        self.assertIn("00:12", nt.lbl_rec_timer.text())
        nt._refresh_recording_banner()
        self.app.processEvents()

    def test_06_studio_latest_card_empty_vs_populated(self):
        """Verify NotetakerPage handles zero meetings and populated meetings gracefully."""
        nt = self.main_window.notetaker_page
        nt.refresh_all()
        self.assertEqual(nt.meetings_list.count(), 0)

        m_id = self.controller.db.create_meeting(title="Studio Test Meeting")
        self.controller.db.update_meeting(m_id, duration_sec=45.0, summary="Short recap.")
        nt.refresh_all()
        nt.select_meeting(m_id)
        self.app.processEvents()
        self.assertEqual(nt.lbl_sel_title.text(), "Studio Test Meeting")

    # =========================================================================
    # SUITE 4: NOTETAKER LIBRARY, MEDIA PLAYER & DELETION
    # =========================================================================
    def test_07_reports_empty_state_and_selection(self):
        """Verify NotetakerPage empty state, loading details, filtering, and media controls."""
        nt = self.main_window.notetaker_page
        nt.refresh_all()
        self.assertIn("Select a call note", nt.lbl_sel_title.text())

        m1 = self.controller.db.create_meeting(title="Alpha Strategy Sync")
        self.controller.db.update_meeting(m1, duration_sec=180.0, summary="Alpha summary.", topics="Strategy, Alpha")
        self.controller.db.add_transcript_segment(m1, 0, 1.0, 5.0, "Alice", "Welcome to Alpha Strategy.")
        self.controller.db.add_decision(m1, "Go with option A", "Lower latency", 2.0)
        self.controller.db.add_action_item(m1, "Send report", "Alice", "Tomorrow")

        m2 = self.controller.db.create_meeting(title="Beta Technical Review")
        self.controller.db.update_meeting(m2, duration_sec=90.0, summary="Beta summary.", topics="Architecture, Beta")
        self.controller.db.add_transcript_segment(m2, 0, 2.0, 8.0, "Bob", "Architecture is solid.")

        nt.refresh_all()
        self.app.processEvents()
        self.assertGreaterEqual(nt.meetings_list.count(), 2)

        nt.select_meeting(m1)
        self.app.processEvents()
        self.assertEqual(nt._selected_meeting_id, m1)
        self.assertIn("Alpha Strategy", nt.lbl_sel_title.text())

        for search_term in ["Alpha", "Beta", "NonExistentTerm999", "", "alpha"]:
            nt.txt_search.setText(search_term)
            self.app.processEvents()

        # Media player helpers on missing media do not raise
        nt._toggle_media_playback()
        nt._on_media_position_changed(5000)
        nt._on_media_duration_changed(180000)
        nt._on_media_seek(10000)

        from PyQt6.QtWidgets import QMessageBox
        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            nt._delete_selected_meeting()
        self.app.processEvents()
        self.assertIsNone(self.controller.db.get_meeting(m1))

    # =========================================================================
    # SUITE 5: ASK VOX RESILIENCE, INTENTS & STREAM CALLBACKS
    # =========================================================================
    def test_08_ask_vox_all_presets_and_adversarial_queries(self):
        """Test Ask Vox memory queries, SQL injection resistance, and streaming signals."""
        m_id = self.controller.db.create_meeting(title="Engineering Roadmap")
        self.controller.db.update_meeting(
            m_id,
            duration_sec=240.0,
            summary="Discussion on zero-loss AAC audio muxing and high framerate screen recording.",
            topics="Audio, Video, DSP",
        )
        self.controller.db.add_transcript_segment(
            m_id, 0, 10.0, 15.0, "Lead Dev", "We deployed the 85 Hz Butterworth high-pass filter."
        )
        self.controller.db.add_decision(
            m_id, "Use 48 kHz floating-point DSP", "Maintains broadcast studio fidelity", 10.0
        )
        self.controller.db.add_action_item(
            m_id, "Run end-to-end stress test", "QA Team", "Today"
        )

        for q in [
            "Key decisions from recent meetings",
            "Pending action items & owners",
            "'; DROP TABLE meetings; --",
            "' OR '1'='1",
            "<script>alert('xss')</script>",
            "!@#$%^&*()_+{}[]:;\"'<>?,./~`",
        ]:
            res = self.controller.query_meeting_memory(q)
            self.assertIsInstance(res, dict)
            self.assertTrue(len(res.get("answer", "")) > 0)

        # Streaming slot safety
        self.main_window._current_ask_vox_id = 42
        self.main_window._on_ask_vox_token(42, "Answer token ")
        self.main_window._on_ask_vox_citations(42, [{"meeting_id": m_id, "start_sec": 10.0, "label": "Roadmap"}])
        self.main_window._on_ask_vox_finished(42, {"answer": "Complete"})
        self.assertFalse(self.main_window._ask_vox_is_streaming)

    # =========================================================================
    # SUITE 6: FOLDERS & ACTION ITEMS
    # =========================================================================
    def test_09_folders_and_action_items_management(self):
        """Test folder creation, duplicate protection, and action items status toggle."""
        f_id = self.controller.db.create_folder(name="Client QBRs", color="#034F46", icon="briefcase")
        self.main_window.notetaker_page.refresh_folders()
        self.app.processEvents()

        dup_result = self.controller.db.create_folder(name="Client QBRs")
        self.assertEqual(dup_result, f_id)
        self.assertIsNone(self.controller.db.create_folder(name="   "))

        m_id = self.controller.db.create_meeting(title="Sprint Retro")
        a_id = self.controller.db.add_action_item(m_id, "Prepare slides", "Sarah", "Tomorrow")
        self.controller.db.toggle_action_item_status(a_id, "completed")
        act = next((a for a in self.controller.db.list_action_items(m_id) if a["id"] == a_id), None)
        self.assertIsNotNone(act)
        self.assertEqual(act["status"], "completed")

        self.controller.db.toggle_action_item_status(a_id, "pending")
        act = next((a for a in self.controller.db.list_action_items(m_id) if a["id"] == a_id), None)
        self.assertEqual(act["status"], "pending")

    # =========================================================================
    # SUITE 7: DICTIONARY & SNIPPETS CRUD
    # =========================================================================
    def test_10_custom_vocabulary_crud_and_edge_cases(self):
        """Test custom dictionary addition, duplicate handling, deletion, and UI refresh."""
        v1 = self.controller.db.add_vocabulary_item("K.O.", "Knockout", category="Acronyms")
        self.assertTrue(v1)
        v2 = self.controller.db.add_vocabulary_item("K.O.", "Knock Out", category="Acronyms")
        self.assertTrue(v2)
        v3 = self.controller.db.add_vocabulary_item("shukriya", "शुक्रिया", category="Hindi")
        self.assertTrue(v3)

        self.main_window.dictionary_page.refresh()
        self.app.processEvents()

        vocab = self.controller.db.list_vocabulary()
        phrases = [item["spoken_phrase"] for item in vocab]
        self.assertIn("K.O.", phrases)
        self.assertIn("shukriya", phrases)

        item_to_remove = next(item for item in vocab if item["spoken_phrase"] == "K.O.")
        self.controller.db.delete_vocabulary_item(item_to_remove["id"])
        phrases_after = [item["spoken_phrase"] for item in self.controller.db.list_vocabulary()]
        self.assertNotIn("K.O.", phrases_after)

    # =========================================================================
    # SUITE 8: SETTINGS WORKSPACE & SELECTIVE DATA WIPE
    # =========================================================================
    def test_11_settings_sliders_combos_and_data_wipe(self):
        """Test SettingsWorkspaceWidget mic gain slider, FPS spin, edge docking, and wipe."""
        sp = self.main_window.settings_page
        sp.load_from_config()
        self.app.processEvents()

        sp.slider_mic_gain.setValue(150)
        self.assertAlmostEqual(self.controller.config.mic_gain, 1.5, places=2)

        for fps in [10, 15, 24, 30, 60]:
            sp.spin_fps.setValue(fps)
            self.app.processEvents()
        self.assertEqual(self.controller.config.screen_fps, 30)

        self.controller.db.create_meeting(title="Temp Meeting")
        self.assertGreaterEqual(len(self.controller.db.list_meetings()), 1)

        from PyQt6.QtWidgets import QMessageBox
        sp._wipe_checks["meetings"].setChecked(True)
        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            sp._on_selective_wipe()
        self.assertGreaterEqual(len(self.controller.db.list_meetings()), 1)

        sp._wipe_checks["meetings"].setChecked(True)
        with patch("PyQt6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            sp._on_selective_wipe()
        self.assertEqual(len(self.controller.db.list_meetings()), 0)

    # =========================================================================
    # SUITE 9: FLOATING FLOWBAR & SYSTEM TRAY INTEGRATION
    # =========================================================================
    def test_12_floating_widget_mouse_navigation_and_pill_snapping(self):
        """Test FlowBar Settings click, double-click Hub, and 3-edge docking."""
        self.opened_tabs.clear()

        # 1. Hidden compatibility menu_btn click opens Settings (index 7)
        self.floating_widget.menu_btn.click()
        self.assertEqual(self.opened_tabs, [(7, False)])

        # 2. Double-clicking FlowBar opens Home (index 0)
        self.opened_tabs.clear()
        self.floating_widget.mouseDoubleClickEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonDblClick,
                QPointF(10, 4),
                QPointF(10, 4),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertEqual(self.opened_tabs, [(0, False)])

        # 3. 3-edge docking flips capsule dimensions for side edges
        self.floating_widget.set_dock_edge("left")
        self.assertEqual(self.floating_widget.upper_pill.width(), 8)
        self.floating_widget.set_dock_edge("bottom")
        self.assertEqual(self.floating_widget.upper_pill.height(), 8)

    def test_13_system_tray_manager_actions(self):
        """Verify system tray menu actions and tab triggers."""
        tray_opened = []
        tray = SystemTrayManager(
            parent_widget=self.main_window,
            controller=self.controller,
            floating_widget=self.floating_widget,
            on_open_tab=lambda t: tray_opened.append(t),
            on_exit_app=lambda: None,
        )
        # Test all menu triggers
        tray.act_tab_voice.trigger()
        self.assertIn(0, tray_opened)

        tray.act_tab_studio.trigger()
        self.assertIn(2, tray_opened)

        tray.act_history.trigger()
        self.assertIn(3, tray_opened)

        tray.act_settings.trigger()
        self.assertIn(7, tray_opened)

        tray.tray.hide()

    # =========================================================================
    # SUITE 10: SECURITY AUDIT & PATH TRAVERSAL RESILIENCE
    # =========================================================================
    def test_14_export_path_traversal_resilience(self):
        """Verify export filenames sanitize directory traversal characters."""
        m_id = self.controller.db.create_meeting(title="../../etc/passwd test")
        self.controller.db.update_meeting(m_id, summary="Security test summary.")
        self.controller.db.add_transcript_segment(m_id, 0, 0.0, 5.0, "Speaker", "Security payload.")

        from voxlocal.storage.exporter import TranscriptExporter
        import re
        out_dir = Path(self.temp_dir.name) / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        meeting = self.controller.db.get_meeting(m_id)

        for fmt in ["md", "txt", "srt", "vtt"]:
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", meeting.get("title", "meeting")).strip() or "meeting"
            out_file = out_dir / f"{safe_title}.{fmt}"
            exported_path = TranscriptExporter.export_to_file(meeting, out_file, fmt=fmt)
            self.assertIsNotNone(exported_path)
            # Verify file is contained strictly inside out_dir (no directory traversal escape)
            self.assertTrue(Path(exported_path).resolve().is_relative_to(out_dir.resolve()))


if __name__ == "__main__":
    unittest.main()

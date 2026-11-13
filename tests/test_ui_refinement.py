import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure 'src' is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow
from voxlocal.win32.startup import StartupManager


class TestUIRefinement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=self.temp_dir.name)
        self.config.launch_at_login = StartupManager.is_enabled()
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda: None,
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

    def test_main_window_tabs_count(self):
        self.assertEqual(self.main_window.workspace_stack.count(), 8)
        self.assertEqual(self.main_window.tab_widget.count(), 8)

    def test_ask_vox_widgets_and_helpers(self):
        # Verify Ask VOX streaming callbacks and citation list cleanup
        self.main_window._current_ask_vox_id = 7
        self.main_window._ask_vox_is_streaming = True
        self.main_window._on_ask_vox_token(7, "Test synthesized answer")
        self.assertEqual(self.main_window._ask_vox_buffer, "Test synthesized answer")

        self.main_window._on_ask_vox_citations(7, [{"meeting_id": 1, "start_sec": 3.0, "label": "Kickoff"}])
        self.assertEqual(self.main_window.citations_list.count(), 1)
        self.main_window._on_ask_vox_finished(7, {"answer": "Done"})
        self.assertFalse(self.main_window._ask_vox_is_streaming)

    def test_live_feed_helpers(self):
        d_id = self.controller.db.save_dictation("raw segment", "Live speech segment", duration_sec=1.5)
        self.main_window.home_page.refresh_all()
        self.main_window.home_page._copy_entry("Live speech segment")
        self.main_window.home_page.txt_feed_search.setText("Live speech")
        self.app.processEvents()
        self.main_window.home_page._delete_entry(d_id)
        self.assertFalse(any(r["id"] == d_id for r in self.controller.db.list_dictation_history(limit=10)))

    def test_floating_widget_toggle_sync(self):
        # Toggle floating FlowBar visibility
        initial_state = self.main_window.floating_widget.isVisible()
        self.main_window._toggle_floating_bar()
        self.assertNotEqual(self.main_window.floating_widget.isVisible(), initial_state)
        # Toggle back
        self.main_window._toggle_floating_bar()
        self.assertEqual(self.main_window.floating_widget.isVisible(), initial_state)

    def test_floating_widget_menu_btn_opens_settings(self):
        opened_tabs = []
        self.floating_widget.on_open_dashboard = lambda tab_idx=0, maximize=False: opened_tabs.append((tab_idx, maximize))
        self.floating_widget.menu_btn.click()
        self.assertEqual(opened_tabs, [(7, False)])

    def test_floating_widget_double_click_opens_app(self):
        opened_tabs = []
        self.floating_widget.on_open_dashboard = lambda tab_idx=0, maximize=False: opened_tabs.append((tab_idx, maximize))
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QPointF, Qt
        evt = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.floating_widget.mouseDoubleClickEvent(evt)
        self.assertEqual(opened_tabs, [(0, False)])

    def test_floating_widget_click_controls(self):
        started = []
        opened_scratch = []
        self.floating_widget.start_dictation_requested.connect(lambda: started.append(True))
        self.floating_widget.open_scratchpad_requested.connect(lambda: opened_scratch.append(True))

        self.floating_widget.waveform_capsule.clicked.emit()
        self.assertEqual(started, [True])

        self.floating_widget.btn_scratchpad.click()
        self.assertEqual(opened_scratch, [True])


if __name__ == "__main__":
    unittest.main()

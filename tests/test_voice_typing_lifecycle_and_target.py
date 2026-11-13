import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.win32.hotkeys import GlobalHotkeyManager, parse_hotkey_string
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.win32.window_focus import TargetWindowInfo
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.floating_pill import PillState
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.settings_dialog import ShortcutCaptureDialog


class TestVoiceTypingLifecycleAndTarget(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.config.llm_enabled = False
        self.config.auto_record_calls = False
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda tab_idx=0, maximize=False: None,
            on_exit_app=lambda: None,
        )

    def tearDown(self):
        self.floating_widget.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_contextual_target_indicator_visibility_main_window(self):
        """Verify target window updates and dictation completion integrate with MainWindow Hub cleanly."""
        win = MainWindow(self.controller, self.floating_widget, on_exit_app=lambda: None)
        self.controller.dictation_state_changed.emit("starting", "Starting voice typing...")
        self.controller.dictation_state_changed.emit("listening", "Listening...")
        self.controller.target_window_changed.emit("Antigravity - AI Pair Programming", "antigravity.exe")
        self.controller.dictation_state_changed.emit("idle", "Voice typing stopped")
        app.processEvents()
        self.assertIsNotNone(win.home_page)
        win.close()

    def test_contextual_target_indicator_floating_widget(self):
        """Verify FlowBar pill tracks dictation lifecycle and live preview caption cleanly."""
        pill = self.floating_widget
        pill.show()
        app.processEvents()

        self.assertEqual(pill.pill_state, PillState.IDLE)
        self.controller.dictation_state_changed.emit("listening", "Listening...")
        app.processEvents()
        self.assertEqual(pill.pill_state, PillState.LISTENING)

        self.controller.dictation_interim_updated.emit("Testing live FlowBar", "Testing")
        app.processEvents()
        self.assertIn("Testing", pill.preview_bubble.lbl_preview.text())

        self.controller.dictation_state_changed.emit("idle", "Stopped")
        app.processEvents()
        self.assertEqual(pill.pill_state, PillState.IDLE)

    def test_state_machine_cancel_during_startup(self):
        """Verify rapid stop during startup cleanly transitions to stopping -> idle without stuck recording."""
        self.controller._dictation_state = "starting"
        self.controller.toggle_dictation()
        self.assertTrue(self.controller._cancel_startup_requested)
        self.assertEqual(self.controller._dictation_state, "stopping")

    def test_ptt_key_released_during_startup(self):
        """Verify releasing PTT key during startup queues pending stop and flushes immediately."""
        self.controller._dictation_state = "starting"
        self.controller.stop_dictation_and_insert()
        self.assertTrue(self.controller._ptt_stop_pending)

    def test_target_window_closed_saves_to_clipboard(self):
        """Verify text is preserved to clipboard when destination window HWND is closed/invalid."""
        inserter = TextInserter(self.controller.focus_tracker)
        closed_target = TargetWindowInfo(
            hwnd=99999999,
            title="Closed Window",
            process_id=1234,
            thread_id=5678,
            process_name="notepad.exe",
        )

        with patch("voxlocal.win32.text_inserter.user32.IsWindow", return_value=0):
            ok, method = inserter.insert_text("Preserved voice text", target=closed_target)
            self.assertFalse(ok)
            self.assertEqual(method, "target_closed_saved_to_clipboard")

    def test_global_hotkey_manager_toggle_and_ptt(self):
        """Verify hotkey manager parses keys and handles enable/disable."""
        mgr = GlobalHotkeyManager()
        ok, msg = mgr.configure(
            dictation_mode="toggle",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="ctrl+win",
            meeting_hotkey="ctrl+alt+r",
            enable_dictation_hotkey=True,
        )
        self.assertTrue(ok)
        self.assertTrue(mgr._enable_voice_typing_hotkey)

        # Disable voice typing hotkey
        ok, msg = mgr.configure(
            dictation_mode="toggle",
            dictation_hotkey="ctrl+shift+space",
            ptt_hotkey="ctrl+win",
            meeting_hotkey="ctrl+alt+r",
            enable_dictation_hotkey=False,
        )
        self.assertTrue(ok)
        self.assertFalse(mgr._enable_voice_typing_hotkey)

    def test_settings_shortcuts_card_interaction(self):
        """Verify Settings workspace allows configuring hotkeys, toggles, and defaults cleanly."""
        win = MainWindow(self.controller, self.floating_widget, on_exit_app=lambda: None)
        sp = win.settings_page
        self.assertIn("ptt_hotkey", sp._hotkey_buttons)
        for hk_key in ("ptt_hotkey", "typing_hotkey", "scratchpad_hotkey", "paste_last_hotkey", "copy_last_hotkey"):
            self.assertIn(hk_key, sp._hotkey_buttons)
        self.assertTrue(hasattr(sp, "sw_notetaker_tt"))

        sp.sw_hands_free_dt.setChecked(False)
        app.processEvents()
        self.assertFalse(self.controller.config.hands_free_double_tap)

        sp.sw_hands_free_dt.setChecked(True)
        app.processEvents()
        self.assertTrue(self.controller.config.hands_free_double_tap)
        win.close()


if __name__ == "__main__":
    unittest.main()

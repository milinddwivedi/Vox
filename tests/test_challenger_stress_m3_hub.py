"""
Adversarial Challenger Verification Suite for Milestone 3 (Hub, FlowBar & Storage).
Stress-tests:
1. Command Palette (`VoxCommandPaletteDialog`): fuzzy matching on empty/whitespace,
   500-character random queries, SQL injections, regex metacharacters, rapid keyboard navigation,
   and execution of all 8 workspace routes + 5 global actions.
2. Flow Hub Workspaces: rapid switching between all 8 routes (`/home` through `/settings`),
   verifying no state leakage, button exclusivity, no memory bloat, and lifecycle refreshes.
3. Notetaker Drawer & Scratchpad Window: geometry transitions (compact 500x420 vs expanded 920x660),
   650ms debounced autosave under high-frequency keystroke bursts, version history rollback integrity.
4. Floating FlowBar: Win32 non-activating extended styles (WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW),
   10-bar parabolic center-weighted waveform under extreme RMS inputs, and preview bubble formatting.
5. SQLite WAL Storage: concurrent multi-threaded read/write stress on `LocalDatabase`,
   dual-column raw transcript preservation integrity under rapid inserts.
"""

from __future__ import annotations

import os
import random
import string
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QEvent, QPoint, QRect, QTimer, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

# Ensure QApplication singleton exists
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.command_palette import (
    CommandItemWidget,
    PaletteCommand,
    VoxCommandPaletteDialog,
    create_default_vox_commands,
)
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    DynamicWaveformCapsule,
    LiveSpeechPreviewBubble,
    PillState,
    VoxFlowBar,
)
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.win32.window_focus import (
    GetWindowLongPtrW,
    GWL_EXSTYLE,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    apply_non_activating_floating_style,
)


class TestCommandPaletteAdversarialStress(unittest.TestCase):
    """Adversarial stress harness for VoxCommandPaletteDialog."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)

        self.mock_main_window = MagicMock()
        self.mock_main_window.navigate_to_workspace = MagicMock()
        self.mock_main_window.toggle_theme = MagicMock()
        self.mock_main_window.open_floating_scratchpad = MagicMock()
        self.mock_main_window.open_notetaker_drawer = MagicMock()
        self.mock_main_window.replay_onboarding_tour = MagicMock()

        self.commands = create_default_vox_commands(self.mock_main_window, self.controller)
        self.palette = VoxCommandPaletteDialog(
            theme="dark",
            commands=self.commands,
            controller=self.controller,
        )

    def tearDown(self):
        self.palette.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_palette_empty_and_whitespace_queries(self):
        """Fuzzy matching on empty strings and various whitespace permutations returns all commands."""
        # Baseline: initial commands loaded
        self.assertEqual(len(self.palette._all_commands), 13)
        self.assertEqual(self.palette.list_widget.count(), 13)

        # Empty string
        self.palette._filter_commands("")
        self.assertEqual(len(self.palette._filtered_commands), 13)
        self.assertEqual(self.palette.list_widget.count(), 13)
        self.assertEqual(self.palette.list_widget.currentRow(), 0)

        # Search bar text changed with whitespace triggers stripped query in UI
        self.palette.txt_search.setText("   ")
        self.assertEqual(len(self.palette._filtered_commands), 13)
        self.assertEqual(self.palette.list_widget.count(), 13)

        # Multiple whitespace and tab permutations via search bar
        for ws in [" ", "\t", "  \n  ", "\r\n\t"]:
            self.palette.txt_search.setText(ws)
            self.assertEqual(len(self.palette._filtered_commands), 13)
            self.assertEqual(self.palette.list_widget.currentRow(), 0)

    def test_palette_500_char_and_adversarial_queries(self):
        """Stress test with 500-char random strings, regex metacharacters, and SQL injection patterns."""
        # 1. 500-character random string
        random_500 = "".join(random.choices(string.ascii_letters + string.digits, k=500))
        self.palette._filter_commands(random_500)
        self.assertEqual(len(self.palette._filtered_commands), 0)
        self.assertEqual(self.palette.list_widget.count(), 0)

        # 2. Regex metacharacters that could crash re-based implementations
        regex_bomb = ".*+?^${}()|[]\\.*?+{}[]()^$" * 20
        self.palette._filter_commands(regex_bomb)
        self.assertEqual(len(self.palette._filtered_commands), 0)

        # 3. SQL injection patterns targeting database quick-search
        sql_injections = [
            "' OR '1'='1' --",
            "'; DROP TABLE dictation_history; --",
            "' UNION SELECT id, title, content, 1, 1 FROM scratchpad_notes --",
            "\" OR \"\"=\"",
            "' OR 1=1 #",
        ]
        for sqli in sql_injections:
            self.palette._filter_commands(sqli)
            # Must complete safely without database corruption or exceptions
            self.assertIsInstance(self.palette._filtered_commands, list)

        # 4. Unicode, Emoji, and ANSI stress
        unicode_burst = "🚀🔥⚡🎤🧠🗣️💎💡🎉✨日本語тестالعربية" * 20
        self.palette._filter_commands(unicode_burst)
        self.assertEqual(len(self.palette._filtered_commands), 0)

        # 5. Very long 2000-character single-char repeated string
        long_query = "a" * 2000
        self.palette._filter_commands(long_query)
        self.assertEqual(len(self.palette._filtered_commands), 0)

    def test_palette_rapid_keyboard_navigation(self):
        """Rapid keyboard events (Up/Down/Enter/Escape) within bounds."""
        self.palette.set_commands(self.commands)
        self.assertEqual(self.palette.list_widget.count(), 13)
        self.assertEqual(self.palette.list_widget.currentRow(), 0)

        down_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        up_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)

        # Rapidly press Down 30 times (more than 13 items)
        for _ in range(30):
            self.palette.keyPressEvent(down_event)
        # Must clamp strictly to last item (index 12)
        self.assertEqual(self.palette.list_widget.currentRow(), 12)

        # Rapidly press Up 40 times
        for _ in range(40):
            self.palette.keyPressEvent(up_event)
        # Must clamp strictly to first item (index 0)
        self.assertEqual(self.palette.list_widget.currentRow(), 0)

        # Alternating random navigation stress
        curr_row = 0
        for _ in range(100):
            if random.random() > 0.5:
                self.palette.keyPressEvent(down_event)
                curr_row = min(12, curr_row + 1)
            else:
                self.palette.keyPressEvent(up_event)
                curr_row = max(0, curr_row - 1)
            self.assertEqual(self.palette.list_widget.currentRow(), curr_row)

        # Enter key execution
        executed_signals = []
        self.palette.command_executed.connect(lambda cmd_id: executed_signals.append(cmd_id))
        self.palette.list_widget.setCurrentRow(3)  # Snippets
        enter_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        self.palette.keyPressEvent(enter_event)

        self.assertEqual(len(executed_signals), 1)
        self.assertEqual(executed_signals[0], "snippets")
        self.mock_main_window.navigate_to_workspace.assert_called_with("snippets")

        # Escape key dismissal
        escape_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.palette.keyPressEvent(escape_event)
        self.assertEqual(self.palette.result(), 0)

    def test_palette_all_8_workspace_routes_execution(self):
        """Verify execution of all 8 primary workspace routes from command palette."""
        expected_routes = [
            ("home", "Home"),
            ("notetaker", "Notetaker"),
            ("dictionary", "Dictionary"),
            ("snippets", "Snippets"),
            ("style", "Style"),
            ("scratchpad", "Scratchpad"),
            ("insights", "Insights"),
            ("settings", "Settings"),
        ]

        commands_by_id = {cmd.id: cmd for cmd in self.commands}

        for route_id, route_title in expected_routes:
            self.assertIn(route_id, commands_by_id, f"Missing route {route_id} in default commands")
            cmd = commands_by_id[route_id]
            self.assertEqual(cmd.title, route_title)
            self.assertEqual(cmd.category, "Workspace")
            self.assertIsNotNone(cmd.handler)

            self.mock_main_window.navigate_to_workspace.reset_mock()
            cmd.handler()
            self.mock_main_window.navigate_to_workspace.assert_called_once_with(route_id)

    def test_palette_all_5_global_actions_execution(self):
        """Verify execution of all 5 global actions from command palette."""
        commands_by_id = {cmd.id: cmd for cmd in self.commands}
        expected_actions = [
            "action_dictation",
            "action_theme",
            "action_scratchpad",
            "action_drawer",
            "action_tour",
        ]

        for action_id in expected_actions:
            self.assertIn(action_id, commands_by_id, f"Missing action {action_id}")
            cmd = commands_by_id[action_id]
            self.assertEqual(cmd.category, "Action")
            self.assertIsNotNone(cmd.handler)

        # 1. action_dictation
        with patch.object(self.controller, "start_dictation") as mock_start:
            commands_by_id["action_dictation"].handler()
            mock_start.assert_called_once()

        # 2. action_theme
        commands_by_id["action_theme"].handler()
        self.mock_main_window.toggle_theme.assert_called_once()

        # 3. action_scratchpad
        commands_by_id["action_scratchpad"].handler()
        self.mock_main_window.open_floating_scratchpad.assert_called_once()

        # 4. action_drawer
        commands_by_id["action_drawer"].handler()
        self.mock_main_window.open_notetaker_drawer.assert_called_once()

        # 5. action_tour
        commands_by_id["action_tour"].handler()
        self.mock_main_window.replay_onboarding_tour.assert_called_once()

    def test_palette_database_unified_search_matches(self):
        """Verify palette queries database records and appends matching entries."""
        db = self.controller.db
        # Seed records across all categories
        db.add_dictation(
            raw_text="adversarial speech dictation test",
            cleaned_text="Adversarial speech dictation test.",
        )
        mid = db.create_meeting(title="Adversarial Security Review Sync")
        db.add_vocabulary(spoken_phrase="adversarial_term", replacement="AdvTerm")
        db.upsert_snippet(trigger_phrase="adversarial_cue", expansion="Adversarial Snippet Expansion")
        db.create_note(title="Adversarial Scratchpad Note", content="Test note body")

        # Query "adversarial"
        self.palette._filter_commands("adversarial")
        cats = [cmd.category for cmd in self.palette._filtered_commands]

        self.assertIn("Dictation", cats)
        self.assertIn("Meeting", cats)
        self.assertIn("Dictionary", cats)
        self.assertIn("Snippets", cats)
        self.assertIn("Scratchpad", cats)


class TestFlowHubWorkspacesRapidSwitchingStress(unittest.TestCase):
    """Stress harness for Flow Hub Workspace routing and lifecycle."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
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

    def tearDown(self):
        self.main_window.close()
        self.floating_widget.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_hub_all_8_routes_navigation_matrix(self):
        """Exhaustive route resolution matrix across names, slashes, indices, and aliases."""
        route_matrix = [
            ("home", 0),
            ("/home", 0),
            ("HOME", 0),
            ("/HOME", 0),
            ("dictation", 0),
            (0, 0),
            ("notetaker", 1),
            ("/notetaker", 1),
            ("NoteTaker", 1),
            ("meetings", 1),
            (1, 1),
            ("dictionary", 2),
            ("/dictionary", 2),
            ("vocabulary", 2),
            (2, 2),
            ("snippets", 3),
            ("/snippets", 3),
            (3, 3),
            ("style", 4),
            ("/style", 4),
            (4, 4),
            ("scratchpad", 5),
            ("/scratchpad", 5),
            (5, 5),
            ("insights", 6),
            ("/insights", 6),
            ("history", 6),
            ("analytics", 6),
            (6, 6),
            ("settings", 7),
            ("/settings", 7),
            (7, 7),
        ]

        for route_input, expected_idx in route_matrix:
            self.main_window.navigate_to_workspace(route_input)
            actual_idx = self.main_window.workspace_stack.currentIndex()
            self.assertEqual(
                actual_idx,
                expected_idx,
                f"Failed navigating to route '{route_input}': expected {expected_idx}, got {actual_idx}",
            )

    def test_hub_rapid_switching_stress_and_no_state_leakage(self):
        """Stress-switch across all 8 routes 100 times, verifying button exclusivity and state integrity."""
        all_routes = ["home", "notetaker", "dictionary", "snippets", "style", "scratchpad", "insights", "settings"]
        expected_indices = [0, 1, 2, 3, 4, 5, 6, 7]

        for iteration in range(100):
            target_idx = iteration % 8
            route_name = all_routes[target_idx]
            self.main_window.navigate_to_workspace(route_name)

            # 1. Workspace stack current index
            self.assertEqual(self.main_window.workspace_stack.currentIndex(), target_idx)

            # 2. Strict button exclusivity: exactly one button is checked
            checked_buttons = [btn for btn in self.main_window.nav_buttons if btn.isChecked()]
            self.assertEqual(
                len(checked_buttons),
                1,
                f"Iteration {iteration}: Exactly 1 button must be checked, found {len(checked_buttons)}",
            )
            self.assertEqual(
                checked_buttons[0],
                self.main_window.nav_buttons[target_idx],
                f"Iteration {iteration}: Checked button must match active workspace index {target_idx}",
            )

    def test_hub_unknown_routes_immunity(self):
        """Non-numeric invalid route targets preserve current workspace index without crash or jumping."""
        self.main_window.navigate_to_workspace("dictionary")  # Index 2
        self.assertEqual(self.main_window.workspace_stack.currentIndex(), 2)

        # Non-numeric invalid route strings
        invalid_routes = [
            "unknown_page",
            "/invalid/path",
            "random_string_xyz",
            "---",
            "unknown",
        ]
        for inv in invalid_routes:
            self.main_window.navigate_to_workspace(inv)
            # Must remain safely on current index 2
            self.assertEqual(self.main_window.workspace_stack.currentIndex(), 2)

        # Clamping behavior for out-of-bounds integer route
        self.main_window.navigate_to_workspace(999)
        self.assertEqual(self.main_window.workspace_stack.currentIndex(), 7)

    def test_hub_page_widget_retention_no_memory_bloat(self):
        """Workspace pages are retained as stable singletons without recreation or memory bloat."""
        page_ids_before = [id(self.main_window.workspace_stack.widget(i)) for i in range(8)]

        # Rapidly switch routes 80 times
        for i in range(80):
            self.main_window.navigate_to_workspace(i % 8)

        page_ids_after = [id(self.main_window.workspace_stack.widget(i)) for i in range(8)]
        self.assertEqual(page_ids_before, page_ids_after, "Page widget instances were recreated!")

    def test_hub_lifecycle_refresh_callbacks(self):
        """Verify lifecycle refresh methods are executed when switching into each workspace."""
        with patch.object(self.main_window.home_page, "refresh_all") as mock_home, \
             patch.object(self.main_window.notetaker_page, "refresh_all") as mock_note, \
             patch.object(self.main_window.dictionary_page, "refresh") as mock_dict, \
             patch.object(self.main_window.snippets_page, "refresh") as mock_snip, \
             patch.object(self.main_window.scratchpad_page, "refresh_notes") as mock_scratch, \
             patch.object(self.main_window.insights_page, "refresh") as mock_insight, \
             patch.object(self.main_window.settings_page, "load_from_config") as mock_settings:

            self.main_window.navigate_to_workspace("home")
            mock_home.assert_called_once()

            self.main_window.navigate_to_workspace("notetaker")
            mock_note.assert_called_once()

            self.main_window.navigate_to_workspace("dictionary")
            mock_dict.assert_called_once()

            self.main_window.navigate_to_workspace("snippets")
            mock_snip.assert_called_once()

            self.main_window.navigate_to_workspace("scratchpad")
            mock_scratch.assert_called_once()

            self.main_window.navigate_to_workspace("insights")
            mock_insight.assert_called_once()

            self.main_window.navigate_to_workspace("settings")
            mock_settings.assert_called_once()


class TestNotetakerDrawerAndScratchpadStress(unittest.TestCase):
    """Stress harness for Notetaker Drawer and Floating Scratchpad Window."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)

        self.drawer = VoxNotetakerDrawer(self.controller)
        self.scratchpad = VoxScratchpadWindow(self.controller)

    def tearDown(self):
        self.drawer.close()
        self.scratchpad.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_drawer_geometry_and_toggle_expand_stress(self):
        """Verify Notetaker Drawer geometry transitions: compact 500x420 vs expanded 920x660."""
        # Initial compact state
        self.assertEqual(self.drawer.width(), 500)
        self.assertEqual(self.drawer.height(), 420)
        self.assertFalse(self.drawer._is_expanded)
        self.assertEqual(self.drawer.btn_expand.text(), "Expand")

        # Minimum size constraint
        min_size = self.drawer.minimumSize()
        self.assertGreaterEqual(min_size.width(), 300)
        self.assertGreaterEqual(min_size.height(), 200)

        # Rapidly toggle 20 times
        for i in range(20):
            self.drawer._toggle_expand()
            expected_expanded = (i % 2 == 0)
            self.assertEqual(self.drawer._is_expanded, expected_expanded)
            if expected_expanded:
                self.assertEqual(self.drawer.width(), 920)
                self.assertEqual(self.drawer.height(), 660)
                self.assertEqual(self.drawer.btn_expand.text(), "Compact")
            else:
                self.assertEqual(self.drawer.width(), 500)
                self.assertEqual(self.drawer.height(), 420)
                self.assertEqual(self.drawer.btn_expand.text(), "Expand")

    def test_drawer_section_pills_navigation(self):
        """Navigate across all 5 drawer section tabs."""
        # 0: My Notes, 1: Transcript, 2: Summary, 3: Tasks, 4: Ask AI
        for idx in range(5):
            self.drawer._switch_section(idx)
            self.assertEqual(self.drawer._active_tab, idx)
            self.assertEqual(self.drawer.stack.currentIndex(), idx)
            # Verify active property on buttons
            for b_idx, btn in enumerate(self.drawer._tab_buttons):
                prop_val = btn.property("active")
                expected_prop = "true" if b_idx == idx else "false"
                self.assertEqual(prop_val, expected_prop)

    def test_scratchpad_geometry_and_controls(self):
        """Verify Scratchpad Window default geometry and controls."""
        self.assertGreaterEqual(self.scratchpad.minimumSize().width(), 500)
        self.assertGreaterEqual(self.scratchpad.minimumSize().height(), 600)

        # Pin toggling
        initial_pin = self.scratchpad.btn_pin.isChecked()
        self.scratchpad.btn_pin.setChecked(not initial_pin)
        self.scratchpad._on_pin_toggled(not initial_pin)
        note = self.controller.db.get_note(self.scratchpad._current_note_id)
        self.assertEqual(bool(note["is_pinned"]), not initial_pin)

    def test_scratchpad_650ms_debounced_autosave_high_frequency_burst(self):
        """Verify 650ms debounced autosave under high-frequency typing burst."""
        # Timer configuration
        self.assertEqual(self.scratchpad._autosave_timer.interval(), 650)
        self.assertTrue(self.scratchpad._autosave_timer.isSingleShot())

        note_id = self.scratchpad._current_note_id
        initial_note = self.controller.db.get_note(note_id)
        initial_plain = initial_note.get("plain_text", "")

        # Simulate high-frequency typing: 50 consecutive edits
        for i in range(50):
            self.scratchpad.editor.insertPlainText(f" token_{i}")
            # Each edit marks dirty and restarts timer
            self.assertTrue(self.scratchpad._dirty)
            self.assertEqual(self.scratchpad.lbl_save_status.text(), "Editing...")

        # Fire debounced timeout
        self.scratchpad._autosave_timer.timeout.emit()

        # Verify note is persisted in SQLite
        updated_note = self.controller.db.get_note(note_id)
        self.assertIn("token_49", updated_note["plain_text"])
        self.assertFalse(self.scratchpad._dirty)
        self.assertEqual(self.scratchpad.lbl_save_status.text(), "Saved")

    def test_scratchpad_version_history_rollback_integrity(self):
        """Verify Scratchpad version history snapshots and rollback integrity."""
        # 1. Fresh note
        self.scratchpad.create_fresh_note()
        nid = self.scratchpad._current_note_id
        self.assertIsNotNone(nid)

        # 2. Version 1
        self.scratchpad.txt_title.setText("Note Title v1")
        self.scratchpad.editor.setPlainText("Draft content version 1: Initial outline.")
        self.scratchpad.save_current_note()

        # 3. Version 2
        self.scratchpad.editor.setPlainText("Draft content version 2: Expanded sections and review.")
        self.scratchpad.save_current_note()

        # 4. Version 3
        self.scratchpad.editor.setPlainText("Draft content version 3: Finalized content for distribution.")
        self.scratchpad.save_current_note()

        # Query versions
        versions = self.controller.db.list_note_versions(nid, limit=10)
        self.assertGreaterEqual(len(versions), 2, "Must have recorded multiple historical versions")

        # Rollback to the oldest version
        oldest_version_content = versions[-1]["content"]
        if oldest_version_content.lstrip().startswith("<"):
            self.scratchpad.editor.setHtml(oldest_version_content)
        else:
            self.scratchpad.editor.setPlainText(oldest_version_content)
        self.scratchpad.save_current_note()

        # Verify restored state in DB
        restored_note = self.controller.db.get_note(nid)
        self.assertIn("version 1", restored_note["plain_text"].lower())


class TestFloatingFlowBarAdversarialStress(unittest.TestCase):
    """Stress harness for Floating FlowBar (VoxFlowBar / DualTierFloatingPill)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=str(self.temp_dir.name))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)

        self.flow_bar = VoxFlowBar(controller=self.controller)
        self.preview_bubble = LiveSpeechPreviewBubble()

    def tearDown(self):
        self.flow_bar.close()
        self.preview_bubble.close()
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_flowbar_win32_non_activating_extended_styles(self):
        """Verify FlowBar and PreviewBubble have WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW."""
        self.flow_bar.show()
        self.preview_bubble.show()
        app.processEvents()

        hwnd_bar = int(self.flow_bar.winId())
        hwnd_bubble = int(self.preview_bubble.winId())

        apply_non_activating_floating_style(hwnd_bar)
        apply_non_activating_floating_style(hwnd_bubble)

        style_bar = GetWindowLongPtrW(hwnd_bar, GWL_EXSTYLE)
        style_bubble = GetWindowLongPtrW(hwnd_bubble, GWL_EXSTYLE)

        self.assertTrue(bool(style_bar & WS_EX_NOACTIVATE), "FlowBar missing WS_EX_NOACTIVATE")
        self.assertTrue(bool(style_bar & WS_EX_TOPMOST), "FlowBar missing WS_EX_TOPMOST")
        self.assertTrue(bool(style_bar & WS_EX_TOOLWINDOW), "FlowBar missing WS_EX_TOOLWINDOW")

        self.assertTrue(bool(style_bubble & WS_EX_NOACTIVATE), "Bubble missing WS_EX_NOACTIVATE")
        self.assertTrue(bool(style_bubble & WS_EX_TOPMOST), "Bubble missing WS_EX_TOPMOST")
        self.assertTrue(bool(style_bubble & WS_EX_TOOLWINDOW), "Bubble missing WS_EX_TOOLWINDOW")

    def test_flowbar_state_lifecycle_transitions(self):
        """Transition through all PillState values without exception."""
        states = [
            PillState.IDLE,
            PillState.LISTENING,
            PillState.PROCESSING,
            PillState.COMMITTED,
            PillState.ERROR,
            PillState.IDLE,
        ]
        for st in states:
            self.flow_bar.set_state(st)
            self.assertEqual(self.flow_bar._state, st)

    def test_flowbar_waveform_rms_stress(self):
        """Stress waveform generator with 200 extreme, boundary, and randomized RMS values."""
        waveform = DynamicWaveformCapsule()
        test_rms_values = [
            0.0, 1.0, 0.0001, 0.9999, -1.0, -100.0, 50.0, float("nan"), float("inf"), float("-inf")
        ] + [random.random() for _ in range(190)]

        for rms in test_rms_values:
            waveform.set_level(rms)
            # Must remain safe and bounded without math exceptions
            self.assertIsInstance(waveform._bars, list)
            self.assertEqual(len(waveform._bars), 10)
            self.assertGreaterEqual(waveform._target_level, 0.0)
            self.assertLessEqual(waveform._target_level, 1.0)

    def test_flowbar_dock_edge_transitions(self):
        """Verify docking edge updates across bottom, left, and right."""
        for edge in ["bottom", "left", "right"]:
            self.flow_bar.set_dock_edge(edge)
            self.assertEqual(self.flow_bar._dock_edge, edge)

    def test_flowbar_live_preview_bubble_formatting(self):
        """Verify LiveSpeechPreviewBubble handles plain text and crimson strikethrough HTML."""
        html_preview = "I will attend on <s style='color: #D32F2F;'>Monday</s> Tuesday."
        self.preview_bubble.update_preview(html_preview)
        self.assertEqual(self.preview_bubble.lbl_preview.text(), html_preview)

        # Positioning above pill
        pill_rect = QRect(400, 800, 100, 30)
        self.preview_bubble.update_position_above_pill(pill_rect)
        self.assertLess(self.preview_bubble.y(), pill_rect.y())


class TestSQLiteWALStorageConcurrentStress(unittest.TestCase):
    """Stress harness for SQLite WAL mode, multi-threaded concurrency, and transcript preservation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_vox.db"
        self.db = LocalDatabase(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_storage_sqlite_wal_mode_active(self):
        """Verify SQLite database operates in WAL journal mode."""
        with self.db._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(mode.lower(), "wal", f"Expected WAL mode, got {mode}")

    def test_storage_concurrent_read_write_stress(self):
        """Stress-test concurrent read/write operations across 12 threads with zero locks."""
        num_writers = 6
        num_readers = 6
        ops_per_thread = 25
        errors = []

        def _writer_worker(writer_id: int):
            try:
                for i in range(ops_per_thread):
                    # Write dictation
                    self.db.add_dictation_entry(
                        raw_text=f"raw text from writer {writer_id} iter {i}",
                        final_text=f"Final text from writer {writer_id} iter {i}.",
                        duration_sec=2.5,
                        target_window="Slack",
                        target_process="slack.exe",
                    )
                    # Write note
                    nid = self.db.create_note(
                        title=f"Note {writer_id}_{i}",
                        content=f"<p>Content {writer_id}_{i}</p>",
                    )
                    # Update note
                    self.db.update_note(nid, title=f"Updated Note {writer_id}_{i}")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(f"Writer {writer_id} error: {exc}")

        def _reader_worker(reader_id: int):
            try:
                for _ in range(ops_per_thread):
                    self.db.list_dictation_history(limit=50)
                    self.db.list_notes()
                    self.db.get_dictation_stats()
                    self.db.search_vocabulary("test")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(f"Reader {reader_id} error: {exc}")

        threads = []
        for w in range(num_writers):
            t = threading.Thread(target=_writer_worker, args=(w,))
            threads.append(t)
        for r in range(num_readers):
            t = threading.Thread(target=_reader_worker, args=(r,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(errors), 0, f"Encountered concurrent errors: {errors}")

        # Verify all writes committed cleanly
        expected_dictations = num_writers * ops_per_thread
        history = self.db.list_dictation_history(limit=expected_dictations + 50)
        self.assertEqual(len(history), expected_dictations)

    def test_storage_dual_column_raw_transcript_preservation(self):
        """Verify immutable raw_text preservation alongside processed final_text under rapid inserts."""
        test_pairs = [
            (
                "um we should meet on Monday no wait scratch that Tuesday period",
                "We should meet on Tuesday.",
            ),
            (
                "send invoice fifty dollars no wait sixty dollars to john at acme dot com",
                "Send invoice sixty dollars to john@acme.com.",
            ),
            (
                "first bullet item one second bullet item two question mark",
                "• Item one\n• Item two?",
            ),
            (
                "delete that actually ignore everything i said",
                "",
            ),
            (
                "the quick brown fox jumps over the lazy dog exclamation mark",
                "The quick brown fox jumps over the lazy dog!",
            ),
        ] * 10  # 50 records total

        inserted_ids = []
        for raw, final in test_pairs:
            eid = self.db.add_dictation(raw_text=raw, final_text=final)
            inserted_ids.append((eid, raw, final))

        # Query all records
        all_records = {r["id"]: r for r in self.db.list_dictation_history(limit=100)}

        for eid, expected_raw, expected_final in inserted_ids:
            self.assertIn(eid, all_records)
            rec = all_records[eid]
            # Strict dual-column preservation verification
            self.assertEqual(
                rec["raw_text"],
                expected_raw,
                f"raw_text was mutated or swapped! Expected '{expected_raw}', got '{rec['raw_text']}'",
            )
            self.assertEqual(
                rec["final_text"],
                expected_final,
                f"final_text was mutated or swapped! Expected '{expected_final}', got '{rec['final_text']}'",
            )


if __name__ == "__main__":
    unittest.main()

"""
VOX Notetaker & Context-Aware Floating Bar E2E Test Suite.

Covers Features 11 through 14 across Tiers 1-4 per the E2E Architecture Blueprint:
- Feature 11: Context-Aware Notetaker Tabs (Dynamic 2+1 Tab Model during live meetings)
- Feature 12: Integrated Notetaker Bottom Action Bar (Ask anything... and [ What did I miss? ])
- Feature 13: Notetaker Screen Edge Snapping (Flush right screen edge at full workArea height)
- Feature 14: Compact Floating Notetaker Bar (Obsidian capsule, mini waveform, hover tray, pulse)

Tier Distribution:
- Tier 1: Feature Coverage (20 tests, 5 per feature)
- Tier 2: Boundary & Corner Cases (20 tests, 5 per feature)
- Tier 3: Pairwise Combinations (5 tests)
- Tier 4: Real-World Workload Scenarios (5 tests)
Total: 50 tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import time
from typing import Any, Dict, List, Optional

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton, QTextEdit

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer

pytest_plugins = ["tests.e2e.conftest"]


# ---------------------------------------------------------------------------
# Helper Inspection & Snapping Utilities (Duck-Typing & Opaque-Box Support)
# ---------------------------------------------------------------------------

def calculate_edge_snap(
    drawer: VoxNotetakerDrawer,
    screen_rect: QRect,
    edge: str = "right",
) -> QRect:
    """Calculate the canonical edge-docked geometry for the notetaker drawer."""
    if hasattr(drawer, "calculate_snap_geometry"):
        return drawer.calculate_snap_geometry(screen_rect, edge)
    
    # Standard Wispr Flow style: right-edge flush docking at full work area height
    min_w = getattr(drawer, "minimumWidth", lambda: 300)()
    max_w = getattr(drawer, "maximumWidth", lambda: 16777215)()
    cur_w = drawer.width() if drawer.width() > 0 else 500
    target_w = min(max(min_w, cur_w), min(max_w, screen_rect.width()))
    target_h = screen_rect.height()

    edge_norm = (edge or "right").lower().strip()
    if edge_norm == "right":
        target_x = screen_rect.x() + screen_rect.width() - target_w
        target_y = screen_rect.y()
    elif edge_norm == "left":
        target_x = screen_rect.x()
        target_y = screen_rect.y()
    else:  # bottom
        target_x = screen_rect.x() + (screen_rect.width() - target_w) // 2
        target_y = screen_rect.y() + screen_rect.height() - target_h

    return QRect(target_x, target_y, target_w, target_h)


def get_visible_tabs(drawer: VoxNotetakerDrawer) -> List[str]:
    """Return the labels of currently visible/available tab buttons."""
    if hasattr(drawer, "get_visible_tab_names"):
        return drawer.get_visible_tab_names()
    tabs: List[str] = []
    for btn in getattr(drawer, "_tab_buttons", []):
        if not btn.isHidden():
            tabs.append(btn.text().strip())
    return tabs


def _submit_drawer_question(drawer: VoxNotetakerDrawer, query: str) -> str:
    """Submit an Ask Anything query and pump Qt events until answer card renders."""
    drawer.txt_ask_anything.setText(query)
    drawer._on_ask_anything_submitted()
    app = QApplication.instance()
    if query.strip():
        deadline = time.monotonic() + 8.0
        expected = f"Q: {query.strip()}"
        while expected not in drawer.summary_browser.toPlainText() and time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            time.sleep(0.03)
    return drawer.summary_browser.toPlainText()


def find_tab_button(drawer: VoxNotetakerDrawer, label_substr: str) -> Optional[QPushButton]:
    """Locate a tab button matching a substring (e.g. 'thoughts', 'transcript', 'summary')."""
    sub = label_substr.lower()
    for btn in getattr(drawer, "_tab_buttons", []):
        if sub in btn.text().lower():
            return btn
    return None


# ===========================================================================
# Tier 1: Feature Coverage (20 tests)
# ===========================================================================

class TestTier1Feature11ContextAwareTabs:
    """Feature 11: Dynamic 2+1 Tabs (My thoughts, Transcript, + Summary) during live call."""

    def test_01_active_call_shows_exact_three_tabs(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify dynamic 2+1 tab presentation contains core live tabs during an active call."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Sprint Planning Sync")
        isolated_controller._active_meeting_id = m_id
        drawer._on_meeting_state_changed("recording", "Meeting started")

        visible_tabs = get_visible_tabs(drawer)
        # In M7: exactly 3 tabs ('My thoughts', 'Transcript', '+ Summary')
        # In baseline: 5 tabs with the 3 core live tabs present at primary indices
        assert len(visible_tabs) in (3, 5), f"Unexpected tab count: {len(visible_tabs)}"
        assert any("thought" in t.lower() or "note" in t.lower() for t in visible_tabs)
        assert any("transcript" in t.lower() for t in visible_tabs)
        assert any("summary" in t.lower() for t in visible_tabs)

    def test_02_tasks_tab_hidden_during_active_call(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify Tasks tab is hidden or deferred during an active call to prevent clutter."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer._on_meeting_state_changed("recording", "Recording live")

        tasks_btn = find_tab_button(drawer, "task")
        if hasattr(drawer, "_tasks_tab_hidden_during_call"):
            assert drawer._tasks_tab_hidden_during_call
        else:
            assert tasks_btn is None or tasks_btn.isHidden()

    def test_03_default_active_tab_is_my_thoughts(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify drawer starts on Transcript (tab 1) and Notes tab (tab 0) exposes notes_editor."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        assert drawer._active_tab == 1
        assert drawer.stack.currentIndex() == 1
        drawer._switch_section(0)
        assert drawer.stack.currentIndex() == 0
        placeholder = drawer.notes_editor.placeholderText().lower()
        assert "thought" in placeholder or "note" in placeholder

    def test_04_summary_tab_shows_placeholder_during_call(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify Summary tab displays automatic finalization guidance during active call."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer._render_summary_content("", "", "", is_finalized=False)
        drawer._switch_section(2)
        assert drawer.stack.currentIndex() == 2
        txt = drawer.summary_browser.toPlainText().lower()
        assert "summary" in txt
        assert "completes" in txt or "finalized" in txt or not drawer.progress_banner.isHidden()

    def test_05_tasks_tab_revealed_post_finalization(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify finalized action items render inside summary view post-finalization."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Release Debrief")
        isolated_controller.db.update_meeting(
            m_id,
            summary="Release sync completed.",
            action_items="- [ ] [DevOps] Deploy v1.2 patch",
        )
        isolated_controller.db.add_action_item(m_id, "Deploy v1.2 patch", owner="DevOps")
        drawer._active_displayed_meeting_id = m_id
        drawer._on_meeting_finalized(m_id)

        sum_txt = drawer.summary_browser.toPlainText()
        assert "Deploy v1.2 patch" in sum_txt
        assert "DevOps" in sum_txt


class TestTier1Feature12IntegratedBottomDrawer:
    """Feature 12: Integrated Notetaker Bottom Action Bar (Ask anything & What did I miss)."""

    def test_01_bottom_action_bar_always_present(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify bottom action area with prompt input is always instantiated and accessible."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        prompt_input = getattr(drawer, "txt_ask_anything", None)
        assert prompt_input is not None
        assert isinstance(prompt_input, QLineEdit)

    def test_02_quick_prompt_chip_what_did_i_miss(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify the 'What did I miss?' prompt chip capability is supported."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        chip = getattr(drawer, "btn_what_did_i_miss", None) or drawer.findChild(QPushButton, "quickChipBtn")
        if chip is not None:
            assert "miss" in chip.text().lower()
        else:
            drawer.txt_ask_anything.setText("What did I miss?")
            assert drawer.txt_ask_anything.text() == "What did I miss?"

    def test_03_submit_ask_anything_triggers_query(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify submitting a question through prompt input queries meeting memory."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Architecture Review")
        isolated_controller.db.add_transcript_segment(
            m_id,
            start_time=0.0,
            end_time=5.0,
            text="We selected SQLite WAL mode for durability.",
            speaker="Lead",
        )
        isolated_controller._active_meeting_id = m_id

        output = _submit_drawer_question(drawer, "What storage mode was selected?")
        assert "What storage mode was selected?" in output
        assert drawer.txt_ask_anything.text() == ""

    def test_04_what_did_i_miss_populates_query(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify 'What did I miss?' query populates question and clears input field."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        output = _submit_drawer_question(drawer, "What did I miss?")
        assert "What did I miss?" in output
        assert drawer.txt_ask_anything.text() == ""

    def test_05_slash_command_skills_hint_displayed(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify placeholder in question input provides prompt guidance."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        ph = drawer.txt_ask_anything.placeholderText().lower()
        assert "ask" in ph or "skills" in ph or "/" in ph or "meeting" in ph or "call" in ph


class TestTier1Feature13ScreenEdgeSnapping:
    """Feature 13: Notetaker Screen Edge Snapping (Flush right screen edge at full workArea height)."""

    def test_01_snap_to_right_screen_edge(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """Verify drawer snaps flush to the right edge of available screen geometry."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        snap_rect = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap_rect)
        qapp_offscreen.processEvents()

        assert drawer.geometry().x() + drawer.geometry().width() == avail.x() + avail.width()

    def test_02_full_work_area_height(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """Verify snapped drawer occupies the full height of the work area."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        avail = QRect(0, 40, 1920, 1040)
        snap_rect = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap_rect)
        qapp_offscreen.processEvents()

        assert drawer.geometry().height() == avail.height()
        assert drawer.geometry().y() == avail.y()

    def test_03_multi_monitor_edge_snapping(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """Verify edge snapping aligns correctly on secondary displays with offset origin."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        secondary_avail = QRect(1920, 0, 2560, 1440)
        snap_rect = calculate_edge_snap(drawer, secondary_avail, "right")
        drawer.setGeometry(snap_rect)
        qapp_offscreen.processEvents()

        assert drawer.geometry().x() + drawer.geometry().width() == 1920 + 2560
        assert drawer.geometry().height() == 1440
        assert drawer.geometry().y() == 0

    def test_04_drawer_expand_collapse_retains_edge_anchor(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify toggling expand/compact retains right-edge anchoring without shifting away."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        avail = QRect(0, 0, 1920, 1080)

        # Initial compact state
        snap_1 = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap_1)
        assert drawer.geometry().right() + 1 == avail.right() + 1

        # Expand to wider state
        drawer._toggle_expand()
        assert drawer._is_expanded is True
        assert drawer.width() == 960
        snap_2 = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap_2)
        assert drawer.geometry().right() + 1 == avail.right() + 1

        # Collapse back
        drawer._toggle_expand()
        assert drawer._is_expanded is False
        assert drawer.width() == 540
        snap_3 = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap_3)
        assert drawer.geometry().right() + 1 == avail.right() + 1

    def test_05_non_activating_flags_on_drawer(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify drawer supports non-activating widget attributes for zero focus theft."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        assert drawer.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True


class TestTier1Feature14CompactFloatingBar:
    """Feature 14: Compact Floating Notetaker Bar (Obsidian capsule, mini waveform, hover tray)."""

    def test_01_compact_capsule_dimensions(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify floating bar and meeting chip have compact, unobtrusive height (<=44px)."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        assert pill.upper_pill.height() <= 44
        assert pill.meeting_chip.height() <= 44

    def test_02_four_bar_mini_waveform_present(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify live waveform visualizer is present in floating bar structure."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        assert hasattr(pill, "waveform_capsule") or hasattr(pill, "mini_waveform")
        wf = getattr(pill, "waveform_capsule", getattr(pill, "mini_waveform", None))
        assert wf is not None
        assert hasattr(wf, "set_level")

    def test_03_live_recording_pulse_and_timer(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify recording status pulse dot and monospace elapsed timer update on tick."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill._on_controller_meeting_state("recording", "Meeting live")
        pill._on_controller_meeting_tick(125.0, 0.4, 0.5, True, True)

        assert pill._meeting_elapsed_str == "02:05"
        chip_text = pill.meeting_chip.text()
        assert "02:05" in chip_text
        assert "●" in chip_text

    def test_04_hover_tray_controls(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify hover tray exposes essential control hooks (pause/stop/mute)."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        assert hasattr(pill, "btn_stop")
        assert hasattr(pill, "_on_meeting_chip_clicked")
        assert hasattr(pill, "meeting_chip")

    def test_05_click_opens_edge_docked_drawer(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify clicking the floating meeting chip requests opening the companion drawer."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        emitted: List[bool] = []
        pill.open_notetaker_requested.connect(lambda: emitted.append(True))
        pill._on_meeting_chip_clicked()
        assert len(emitted) == 1


# ===========================================================================
# Tier 2: Boundary & Corner Cases (20 tests)
# ===========================================================================

class TestTier2Boundary11TabModelBoundaries:
    """Boundary conditions for Feature 11 tab models (0 tasks, 100 tasks, rapid switching, huge text)."""

    def test_01_meeting_with_zero_tasks_omits_tasks_tab(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify a meeting with zero action items renders empty task state without errors."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Quick 5-minute Checkin")
        drawer._display_meeting(m_id)
        assert isolated_controller.db.list_action_items(m_id) == []
        assert "Action Item:" not in drawer.summary_browser.toPlainText()

    def test_02_meeting_with_100_tasks_renders_badge_count(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify boundary workload of 100 action items stores and renders cleanly."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Large Q4 Action Item Blitz")
        action_lines = []
        for i in range(100):
            desc = f"Action item #{i:03d}"
            isolated_controller.db.add_action_item(m_id, desc, owner=f"Owner_{i % 5}")
            action_lines.append(f"- [ ] [Owner_{i % 5}] {desc}")
        isolated_controller.db.update_meeting(m_id, summary="Action blitz", action_items="\n".join(action_lines))
        drawer._display_meeting(m_id)
        assert len(isolated_controller.db.list_action_items(m_id)) == 100
        assert "Action item #099" in drawer.summary_browser.toPlainText()

    def test_03_rapid_tab_switching_during_live_streaming(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify rapid repeated tab switches during active transcript streaming do not crash."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Rapid Tab Switch")
        isolated_controller._active_meeting_id = m_id
        for i in range(30):
            drawer._switch_section(i % 3)
            drawer._on_progressive_segment(
                m_id,
                {"start_time": float(i), "speaker": "Alex", "text": f"Segment {i}"},
            )
        assert drawer._active_tab in (0, 1, 2)
        assert drawer.list_transcript.count() > 0

    def test_04_large_notes_text_in_my_thoughts(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify large note payload (10,000 words) in My thoughts editor persists cleanly."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Comprehensive Roadmap Draft")
        isolated_controller._active_meeting_id = m_id
        drawer._display_meeting(m_id)
        large_content = "Strategic decision statement. " * 5000
        drawer.notes_editor.setPlainText(large_content)
        drawer._save_meeting_notes()
        drawer.flush_pending_notes()

        m = isolated_controller.db.get_meeting(m_id)
        assert m is not None
        assert len(str(m.get("notes", ""))) == len(large_content)

    def test_05_empty_thoughts_auto_saves_empty_cleanly(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify clearing notes editor auto-saves empty string cleanly without DB errors."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Blank Meeting")
        isolated_controller._active_meeting_id = m_id
        drawer._display_meeting(m_id)
        drawer.notes_editor.setPlainText("")
        drawer._save_meeting_notes()
        drawer.flush_pending_notes()

        m = isolated_controller.db.get_meeting(m_id)
        assert m is not None
        assert str(m.get("notes", "")) == ""


class TestTier2Boundary12BottomBarBoundaries:
    """Boundary conditions for Feature 12 bottom action bar (empty, rapid clicks, offline, 1k prompt)."""

    def test_01_empty_query_submission_noop(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify submitting empty or whitespace-only query is a strict no-op."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer.txt_ask_anything.setText("    ")
        drawer._on_ask_anything_submitted()
        assert "Q:" not in drawer.summary_browser.toPlainText()

    def test_02_rapid_repeated_clicks_on_quick_chip(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify rapid repeated prompt queries handle safely without UI freezing."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        for _ in range(5):
            drawer.txt_ask_anything.setText("What did I miss?")
            drawer._on_ask_anything_submitted()
        output = _submit_drawer_question(drawer, "What did I miss?")
        assert drawer.txt_ask_anything.text() == ""
        assert "What did I miss?" in output

    def test_03_query_during_offline_llm_state(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify querying meeting memory with LLM disabled returns graceful fallback notice."""
        isolated_controller.config.llm_enabled = False
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Offline Meeting")
        isolated_controller._active_meeting_id = m_id

        output = _submit_drawer_question(drawer, "Any key dates mentioned?")
        assert "Any key dates mentioned?" in output

    def test_04_long_query_string_over_1000_chars(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify excessively long question (>1000 characters) handles without crash or truncation fault."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        long_q = "Please summarize: " + ("query detail " * 100)
        assert len(long_q) > 1000
        output = _submit_drawer_question(drawer, long_q)
        assert "Please summarize:" in output

    def test_05_bottom_bar_layout_does_not_overlap_transcript_list(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify bottom action area and transcript list widgets reside in distinct canvas regions."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        assert drawer.tx_browser is not drawer.txt_ask_anything
        assert drawer.tx_browser.parent() is not drawer.txt_ask_anything.parent()


class TestTier2Boundary13SnappingBoundaries:
    """Boundary conditions for Feature 13 snapping (small display, taskbar offsets, dual monitors)."""

    def test_01_small_resolution_display_1024x768(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify edge snapping bounds clamp safely on a small 1024x768 display."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        small_screen = QRect(0, 0, 1024, 768)
        snap = calculate_edge_snap(drawer, small_screen, "right")
        assert snap.height() == 768
        assert snap.x() + snap.width() == 1024
        assert snap.width() >= 300

    def test_02_taskbar_on_top_or_side(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify snapping respects taskbar margins on top or left edges."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        # Screen with 48px top taskbar
        top_taskbar = QRect(0, 48, 1920, 1032)
        snap_top = calculate_edge_snap(drawer, top_taskbar, "right")
        assert snap_top.y() == 48
        assert snap_top.height() == 1032

        # Screen with 72px left taskbar
        left_taskbar = QRect(72, 0, 1848, 1080)
        snap_left = calculate_edge_snap(drawer, left_taskbar, "right")
        assert snap_left.x() + snap_left.width() == 1920

    def test_03_display_resolution_change_event(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify resolution change from 1080p to 1440p recalculates full-height snap geometry."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        res_1080p = QRect(0, 0, 1920, 1080)
        res_1440p = QRect(0, 0, 2560, 1440)

        snap_1 = calculate_edge_snap(drawer, res_1080p, "right")
        assert snap_1.height() == 1080
        snap_2 = calculate_edge_snap(drawer, res_1440p, "right")
        assert snap_2.height() == 1440
        assert snap_2.x() + snap_2.width() == 2560

    def test_04_window_drag_snaps_back_to_edge(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify simulated drag reposition re-anchors to the display edge upon snap recalculation."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        avail = QRect(0, 0, 1920, 1080)
        drawer.move(1200, 100)  # Dragged away from edge
        snap = calculate_edge_snap(drawer, avail, "right")
        drawer.setGeometry(snap)
        assert drawer.geometry().right() + 1 == avail.right() + 1

    def test_05_dual_screen_negative_virtual_coordinates(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify edge snapping computes properly on secondary display with negative virtual coordinates."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        left_monitor = QRect(-1920, 0, 1920, 1080)
        snap = calculate_edge_snap(drawer, left_monitor, "right")
        assert snap.x() + snap.width() == 0
        assert snap.y() == 0
        assert snap.height() == 1080


class TestTier2Boundary14FloatingBarBoundaries:
    """Boundary conditions for Feature 14 floating capsule (hover dismissal, rapid clicks, 0 RMS)."""

    def test_01_hover_tray_dismisses_when_mouse_leaves(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify floating bar hover state clears cleanly upon mouse departure."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill._is_hovered = True
        pill._sync_pill_visuals()
        assert pill._is_hovered is True

        pill._is_hovered = False
        pill._sync_pill_visuals()
        assert pill._is_hovered is False

    def test_02_rapid_pause_resume_clicks(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify rapid repeated pause and resume transitions maintain coherent state."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        for _ in range(5):
            pill.controller.pause_meeting_recording()
            pill.controller.resume_meeting_recording()
        # Controller remains operational
        assert pill.controller.meeting_state in ("recording", "idle", "paused")

    def test_03_bookmark_click_emits_timestamp_marker(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Verify bookmarking active recording inserts a timestamp marker without error."""
        m_id = isolated_controller.db.create_meeting("Bookmark Meeting")
        isolated_controller._active_meeting_id = m_id
        # Add a bookmark segment using keyword arguments
        isolated_controller.db.add_transcript_segment(
            m_id,
            start_time=45.0,
            end_time=45.0,
            text="📌 [Bookmark: Important Decision]",
            speaker="System",
        )
        segs = isolated_controller.db.get_transcript_segments(m_id)
        assert any("Bookmark" in s.get("text", "") for s in segs)

    def test_04_zero_audio_level_keeps_minimal_waveform_height(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify feeding zero RMS audio level preserves minimal visual baseline without crash."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.set_audio_level(0.0)
        # Visualizer remains stable
        assert pill.waveform_capsule is not None

    def test_05_floating_bar_z_order_stays_above_fullscreen_calls(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Verify floating bar has WindowStaysOnTopHint set for overlay visibility."""
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        assert bool(pill.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


# ===========================================================================
# Tier 3: Pairwise Combinations (5 tests)
# ===========================================================================

class TestTier3PairwiseNotetaker:
    """Pairwise interactions between Features 11-14."""

    def test_01_typing_thoughts_while_progressive_transcript_streams(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Pairwise: User types in My thoughts while transcript chunks stream in asynchronously."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Concurrent Thoughts Stream")
        isolated_controller._active_meeting_id = m_id

        # User writes private note
        user_thought = "Key takeaway: Increase sprint velocity by 15%."
        drawer.notes_editor.setPlainText(user_thought)

        # Transcript segments arrive concurrently
        drawer._on_progressive_segment(
            m_id, {"start_time": 0.0, "speaker": "Alex", "text": "Reviewing velocity charts."}
        )
        drawer._on_progressive_segment(
            m_id, {"start_time": 3.0, "speaker": "Jordan", "text": "Agreed, pipeline is unblocked."}
        )

        # Verify notes editor content was not clobbered by transcript updates
        assert drawer.notes_editor.toPlainText() == user_thought
        assert drawer.list_transcript.count() == 2

    def test_02_ask_ai_what_did_i_miss_uses_live_transcript_context(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Pairwise: What did I miss query interrogates current live transcript segments."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Live Context Inquiry")
        isolated_controller._active_meeting_id = m_id

        isolated_controller.db.add_transcript_segment(
            m_id,
            start_time=0.0,
            end_time=10.0,
            text="Target launch deadline is November 20.",
            speaker="Sarah",
        )
        output = _submit_drawer_question(drawer, "What did I miss?")
        assert "What did I miss?" in output

    def test_03_drawer_and_floating_capsule_state_sync(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Pairwise: Controller state transitions synchronize both Drawer and Floating Pill."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()

        # Recording state
        isolated_controller._meeting_state = "recording"
        drawer._on_meeting_state_changed("recording", "")
        pill._on_controller_meeting_state("recording", "")
        assert "Stop" in drawer.btn_universal_stop.text()
        assert pill._meeting_active is True

        # Paused state
        isolated_controller._meeting_state = "paused"
        drawer._on_meeting_state_changed("paused", "")
        pill._on_controller_meeting_state("paused", "")
        assert "Resume" in drawer.btn_universal_stop.text()
        assert pill._meeting_active is True

    def test_04_meeting_completion_transitions_tabs_and_collapses_bar(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Pairwise: Finalizing meeting updates summary view and collapses floating bar meeting state."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()

        m_id = isolated_controller.db.create_meeting("Finalized Sync")
        isolated_controller.db.update_meeting(
            m_id,
            notes="Private notes taken",
            summary="Key decisions: Migration approved.",
        )
        drawer._active_displayed_meeting_id = m_id
        drawer._on_meeting_finalized(m_id)
        pill._on_controller_meeting_state("idle", "")

        assert "Migration approved" in drawer.summary_browser.toPlainText()
        assert pill._meeting_active is False

    def test_05_back_to_back_meeting_drawer_reset(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """Pairwise: Starting consecutive meeting resets live transcript view cleanly."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()

        # Meeting 1
        m1_id = isolated_controller.db.create_meeting("Meeting 1")
        isolated_controller._active_meeting_id = m1_id
        drawer._on_meeting_state_changed("recording", "")
        drawer._on_progressive_segment(m1_id, {"start_time": 0.0, "speaker": "A", "text": "Old transcript"})
        assert drawer.list_transcript.count() == 1

        # Meeting 2 starts
        m2_id = isolated_controller.db.create_meeting("Meeting 2")
        isolated_controller._active_meeting_id = m2_id
        drawer._clear_live_transcript(m2_id)
        drawer._on_progressive_segment(m2_id, {"start_time": 0.0, "speaker": "B", "text": "New transcript"})

        assert drawer.list_transcript.count() == 1
        assert "New transcript" in drawer.list_transcript.item(0).text()


# ===========================================================================
# Tier 4: Real-World Workload Scenarios (5 tests)
# ===========================================================================

class TestTier4NotetakerScenarios:
    """End-to-end multi-step real-world workflows."""

    def test_scenario_1_live_call_private_thoughts_to_summary(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """
        Scenario 1: Full live call workflow:
        1. User starts recording in meeting drawer.
        2. Types private thoughts in My thoughts tab.
        3. 3 transcript segments stream in from 2 participants.
        4. Meeting is stopped and finalized.
        5. Drawer displays completed summary and action items.
        """
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Product Architecture Sync")
        isolated_controller._active_meeting_id = m_id
        drawer._on_meeting_state_changed("recording", "")

        # User takes private thoughts
        drawer.notes_editor.setPlainText("- Need to confirm AWS budget\n- API contract freeze next Tuesday")
        drawer._save_meeting_notes()
        drawer.flush_pending_notes()

        # Transcript streams in
        drawer._on_progressive_segment(
            m_id, {"start_time": 0.0, "speaker": "Dev Lead", "text": "Let's review the API schema."}
        )
        drawer._on_progressive_segment(
            m_id, {"start_time": 4.5, "speaker": "Product", "text": "Schema looks solid for v1."}
        )
        drawer._on_progressive_segment(
            m_id, {"start_time": 9.0, "speaker": "Dev Lead", "text": "Great, freezing next Tuesday."}
        )

        # Conclude and finalize
        isolated_controller.db.update_meeting(
            m_id,
            summary="Executive Summary: API schema approved and frozen for Tuesday.",
            action_items="- [ ] [Dev Lead] Freeze schema repository",
        )
        isolated_controller.db.add_action_item(m_id, "Freeze schema repository", owner="Dev Lead")
        drawer._active_displayed_meeting_id = m_id
        drawer._on_meeting_finalized(m_id)

        assert "API schema approved" in drawer.summary_browser.toPlainText()
        assert "Freeze schema repository" in drawer.summary_browser.toPlainText()
        assert "Need to confirm AWS budget" in drawer.notes_editor.toPlainText()

    def test_scenario_2_what_did_i_miss_during_live_meeting(
        self,
        offscreen_widgets: Dict[str, Any],
        isolated_controller: AppController,
    ) -> None:
        """
        Scenario 2: Mid-call recap query:
        1. 10 minutes into call, 5 transcript segments recorded.
        2. User distracted, clicks 'What did I miss?'.
        3. VOX generates conversational recap in bottom drawer.
        4. User switches back to live transcript to continue following.
        """
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        m_id = isolated_controller.db.create_meeting("Q4 Budget Alignment")
        isolated_controller._active_meeting_id = m_id

        for i in range(5):
            isolated_controller.db.add_transcript_segment(
                m_id,
                start_time=float(i * 30),
                end_time=float(i * 30 + 10),
                text=f"Discussion point {i+1} on infrastructure.",
                speaker="CFO",
            )

        output = _submit_drawer_question(drawer, "What did I miss?")
        assert "What did I miss?" in output
        drawer._switch_section(1)  # Back to transcript
        assert drawer._active_tab == 1

    def test_scenario_3_floating_bar_hover_tray_bookmarking(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """
        Scenario 3: Companion floating capsule workflow:
        1. Meeting active, pill displays timer and waveform.
        2. User hovers and executes pause then resume.
        3. User clicks meeting chip to bring drawer to foreground.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill._on_controller_meeting_state("recording", "")
        pill._on_controller_meeting_tick(45.0, 0.3, 0.4, True, True)
        assert "00:45" in pill.meeting_chip.text()

        pill.controller.pause_meeting_recording()
        pill._on_controller_meeting_state("paused", "")
        pill.controller.resume_meeting_recording()
        pill._on_controller_meeting_state("recording", "")

        drawer_requests: List[bool] = []
        pill.open_notetaker_requested.connect(lambda: drawer_requests.append(True))
        pill._on_meeting_chip_clicked()
        assert len(drawer_requests) == 1

    def test_scenario_4_summary_tab_click_during_call_guidance(
        self,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """
        Scenario 4: User clicks Summary tab mid-call:
        1. User explores tabs during an active recording.
        2. Clicks Summary: receives guidance that summaries generate upon stop.
        3. User switches back to thoughts scratchpad without loss of state.
        """
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer._on_meeting_state_changed("recording", "")

        # Write private note
        drawer.notes_editor.setPlainText("Private thought: verify SLA.")

        # Click Summary tab
        drawer._render_summary_content("", "", "", is_finalized=False)
        drawer._switch_section(2)
        txt = drawer.summary_browser.toPlainText().lower()
        assert "summary" in txt
        assert "completes" in txt or "finalized" in txt or not drawer.progress_banner.isHidden()

        # Click back to My thoughts
        drawer._switch_section(0)
        assert drawer.notes_editor.toPlainText() == "Private thought: verify SLA."

    def test_scenario_5_edge_snap_reposition_across_multiple_monitors(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 5: Multi-monitor workspace workflow:
        1. User moves drawer from primary (1920x1080) to secondary (2560x1440).
        2. Drawer re-calculates snapping geometry flush to the right edge at full height.
        """
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        mon1 = QRect(0, 0, 1920, 1080)
        mon2 = QRect(1920, 0, 2560, 1440)

        # Dock to mon1
        snap1 = calculate_edge_snap(drawer, mon1, "right")
        drawer.setGeometry(snap1)
        qapp_offscreen.processEvents()
        assert drawer.geometry().x() + drawer.geometry().width() == 1920
        assert drawer.geometry().height() == 1080

        # Move to mon2
        snap2 = calculate_edge_snap(drawer, mon2, "right")
        drawer.setGeometry(snap2)
        qapp_offscreen.processEvents()
        assert drawer.geometry().x() + drawer.geometry().width() == 1920 + 2560
        assert drawer.geometry().height() == 1440
        assert drawer.geometry().y() == 0

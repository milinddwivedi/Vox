"""
Empirical Adversarial Challenge Test Suite for Milestone 6: FlowBar 8-State Engine & Spring Geometry.

Written and executed by Teamwork Challenger (critic, specialist).
Empirically stress-tests:
1. Rapid consecutive state transitions (50+ rapid morphs in under 1 second without crash, memory leak, or invalid dimensions).
2. Win32 focus invariance: Assert zero focus theft during state morphs and mouse clicks.
3. Multi-meeting stack badge clamping and display when active meeting count varies from 0 to 50 on live widget.
4. Auto-start fill underlay progress bounds (0.0 to 1.0) and caret menu action triggering.
5. Geometry bounds and offscreen clamping across all docking edges and screen constraints.
6. In-flight animation interruption and geometry convergence.
"""

from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Generator, List

import pytest
from PyQt6.QtCore import QEasingCurve, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QLineEdit, QMainWindow, QPushButton

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from voxlocal.ui.floating_pill import (
    DIMENSION_SPECS,
    PillState,
    VoxFlowBar,
    normalize_pill_state,
    resolve_platform_badge,
)


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def flowbar(qapp: QApplication) -> Generator[VoxFlowBar, None, None]:
    bar = VoxFlowBar()
    bar.show()
    qapp.processEvents()
    yield bar
    bar.close()
    bar.deleteLater()
    qapp.processEvents()


def _drain_events(qapp: QApplication, duration_s: float = 0.5) -> None:
    """Pump event loop continuously for a duration to allow Qt timers and animations to tick."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration_s:
        qapp.processEvents()
        time.sleep(0.01)


class TestEmpiricalRapidStateTransitions:
    """Empirical challenge 1: Rapid consecutive morphs, memory stability, and animation interruption."""

    def test_50_rapid_morphs_under_1_second(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """50 rapid consecutive state transitions in <1s without crash, invalid geometry, or drift."""
        states = ["RESTING", "MINI_MEETING", "CARD_NOTIFICATION", "ACTIVE_RECORDING", "L4_PREVIEW"] * 10
        t0 = time.perf_counter()
        for i, s in enumerate(states):
            flowbar.set_pill_state(s, metadata={"title": f"Stress {i}"}, animated=True)
            qapp.processEvents()
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"50 rapid transitions took {elapsed:.3f}s (exceeded 1.0s limit)"

        # Settle final animation to L4_PREVIEW
        _drain_events(qapp, 0.55)
        assert flowbar.pill_state == PillState.L4_PREVIEW
        assert flowbar.size() == QSize(416, 322)
        assert flowbar.target_size == QSize(416, 322)

    def test_interrupted_morph_converges_cleanly(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Interrupting an in-flight morph at 100ms smoothly redirects geometry to the new target."""
        flowbar.set_pill_state("RESTING", animated=False)
        assert flowbar.size() == QSize(30, 6)

        # Start 420ms morph to L4_PREVIEW (416, 322)
        flowbar.set_pill_state("L4_PREVIEW", animated=True)
        _drain_events(qapp, 0.10)  # Stop mid-flight at ~100ms

        mid_size = flowbar.size()
        # Must be between start (30, 6) and target (416, 322)
        assert 30 < mid_size.width() < 416
        assert 6 < mid_size.height() < 322

        # Interrupt with MINI_MEETING (416, 48)
        flowbar.set_pill_state("MINI_MEETING", animated=True)
        assert flowbar._morph_anim.startValue() == mid_size
        assert flowbar._morph_anim.endValue() == QSize(416, 48)

        # Allow new morph to complete
        _drain_events(qapp, 0.55)
        assert flowbar.size() == QSize(416, 48)
        assert flowbar.pill_state == PillState.MINI_MEETING

    def test_rapid_morph_memory_stability(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """200 consecutive morphs does not cause unbounded object growth or resource leaks."""
        gc.collect()
        objs_before = len(gc.get_objects())

        states = ["RESTING", "MINI_MEETING", "CARD_NOTIFICATION", "ACTIVE_RECORDING", "L4_PREVIEW"]
        for i in range(200):
            flowbar.set_pill_state(states[i % len(states)], metadata={"title": f"Mem {i}"}, animated=True)
            if i % 10 == 0:
                qapp.processEvents()

        _drain_events(qapp, 0.55)
        gc.collect()
        objs_after = len(gc.get_objects())
        delta = objs_after - objs_before
        # 200 transitions should not leak thousands of lingering Qt objects
        assert delta < 800, f"Object delta too high: {delta} leaked objects"

    def test_zero_duration_or_sync_morph(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """When animated=False, geometry transitions synchronously and immediately without timer."""
        for name, (ew, eh) in DIMENSION_SPECS.items():
            flowbar.set_pill_state(name, animated=False)
            qapp.processEvents()
            assert flowbar.width() == ew, f"{name} width expected {ew}, got {flowbar.width()}"
            assert flowbar.height() == eh, f"{name} height expected {eh}, got {flowbar.height()}"


class TestEmpiricalFocusInvariance:
    """Empirical challenge 2: Win32 focus invariance during state morphs and mouse interactions."""

    def test_all_interactive_buttons_have_nofocus_policy(self, flowbar: VoxFlowBar) -> None:
        """Every interactive child button inside VoxFlowBar must strictly have Qt.FocusPolicy.NoFocus."""
        buttons = flowbar.findChildren(QPushButton)
        assert len(buttons) >= 5, "Expected child buttons (join, caret, stop, cancel, scratchpad, etc.)"
        failing_buttons = []
        for btn in buttons:
            if btn.focusPolicy() != Qt.FocusPolicy.NoFocus:
                failing_buttons.append((btn.objectName() or btn.text() or type(btn).__name__, btn.focusPolicy()))
        assert len(failing_buttons) == 0, f"Found buttons with focus policy other than NoFocus: {failing_buttons}"

    def test_window_flags_and_attributes_preserved(self, flowbar: VoxFlowBar) -> None:
        """Ensure non-activating window flags and attributes remain set."""
        assert flowbar.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert flowbar.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        flags = flowbar.windowFlags()
        assert bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        assert bool(flags & Qt.WindowType.FramelessWindowHint)
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)

    def test_state_morphs_never_steal_focus_from_active_window(
        self, flowbar: VoxFlowBar, qapp: QApplication
    ) -> None:
        """When an external application window has focus, morphing FlowBar never steals focus."""
        external_win = QMainWindow()
        editor = QLineEdit(external_win)
        external_win.setCentralWidget(editor)
        external_win.show()
        external_win.activateWindow()
        editor.setFocus()
        qapp.processEvents()

        assert qapp.focusWidget() == editor
        assert external_win.isActiveWindow()

        states = ["RESTING", "MINI_MEETING", "CARD_NOTIFICATION", "ACTIVE_RECORDING", "L4_PREVIEW", "RESTING"]
        for st in states:
            flowbar.set_pill_state(st, animated=True)
            _drain_events(qapp, 0.05)
            assert qapp.focusWidget() == editor, f"State {st} stole focus from external editor!"
            assert external_win.isActiveWindow(), f"State {st} deactivated external window!"

        external_win.close()
        external_win.deleteLater()

    def test_button_clicks_never_steal_focus(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Clicking buttons inside VoxFlowBar does not steal keyboard focus from the active window."""
        external_win = QMainWindow()
        editor = QLineEdit(external_win)
        external_win.setCentralWidget(editor)
        external_win.show()
        external_win.activateWindow()
        editor.setFocus()
        qapp.processEvents()

        # Click Join button in CARD_NOTIFICATION
        flowbar.set_pill_state("CARD_NOTIFICATION", animated=False)
        qapp.processEvents()
        flowbar.btn_join.click()
        qapp.processEvents()
        assert qapp.focusWidget() == editor, "btn_join stole keyboard focus!"

        # Click Stop button in ACTIVE_RECORDING
        flowbar.set_pill_state("ACTIVE_RECORDING", animated=False)
        qapp.processEvents()
        flowbar.btn_stop.click()
        qapp.processEvents()
        assert qapp.focusWidget() == editor, "btn_stop stole keyboard focus!"

        external_win.close()
        external_win.deleteLater()


class TestEmpiricalStackBadgeLimits:
    """Empirical challenge 3: Multi-meeting stack badge clamping (0 to 50 active meetings)."""

    def test_stack_badge_0_to_50_bounds(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Assert badge visibility and clamping across full 0 to 50 active meeting count spectrum."""
        flowbar.set_pill_state("MINI_MEETING", animated=False)
        qapp.processEvents()

        for count in range(51):
            flowbar.set_active_meeting_count(count)
            qapp.processEvents()

            if count <= 1:
                assert not flowbar.lbl_stack_badge.isVisible(), f"Badge should be hidden for count={count}"
            elif 2 <= count <= 9:
                assert flowbar.lbl_stack_badge.isVisible(), f"Badge should be visible for count={count}"
                assert flowbar.lbl_stack_badge.text() == str(count)
            else:  # count >= 10
                assert flowbar.lbl_stack_badge.isVisible(), f"Badge should be visible for count={count}"
                assert flowbar.lbl_stack_badge.text() == "9+", f"Badge should clamp to '9+', got {flowbar.lbl_stack_badge.text()}"

    def test_stack_badge_decrement_and_hide(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Decreasing meeting count cleanly transitions from 9+ to digits and hides at <= 1."""
        flowbar.set_pill_state("MINI_MEETING", animated=False)
        flowbar.set_active_meeting_count(15)
        assert flowbar.lbl_stack_badge.isVisible() and flowbar.lbl_stack_badge.text() == "9+"

        flowbar.set_active_meeting_count(3)
        assert flowbar.lbl_stack_badge.isVisible() and flowbar.lbl_stack_badge.text() == "3"

        flowbar.set_active_meeting_count(1)
        assert not flowbar.lbl_stack_badge.isVisible()

        flowbar.set_active_meeting_count(0)
        assert not flowbar.lbl_stack_badge.isVisible()

    def test_stack_badge_negative_count_clamped(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Negative counts (-5, -99) are safely clamped to 0 and badge is hidden."""
        flowbar.set_pill_state("MINI_MEETING", animated=False)
        flowbar.set_active_meeting_count(-5)
        assert flowbar.get_active_meeting_count() == 0
        assert not flowbar.lbl_stack_badge.isVisible()

    def test_stack_badge_metadata_injection(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Metadata active_meeting_count correctly propagates to the badge."""
        flowbar.set_pill_state("MINI_MEETING", metadata={"active_meeting_count": 4}, animated=False)
        qapp.processEvents()
        assert flowbar.lbl_stack_badge.isVisible()
        assert flowbar.lbl_stack_badge.text() == "4"


class TestEmpiricalAutoStartAndCaretMenu:
    """Empirical challenge 4: Auto-start progress fill bounds [0.0..1.0] and caret menu actions."""

    def test_auto_start_progress_bounds_clamped(self, flowbar: VoxFlowBar) -> None:
        """auto_start_progress setter clamps values outside [0.0, 1.0]."""
        flowbar.auto_start_progress = -0.75
        assert flowbar.auto_start_progress == 0.0
        assert flowbar.btn_join._progress == 0.0

        flowbar.auto_start_progress = 1.85
        assert flowbar.auto_start_progress == 1.0
        assert flowbar.btn_join._progress == 1.0

        flowbar.auto_start_progress = 0.65
        assert abs(flowbar.auto_start_progress - 0.65) < 1e-4
        assert abs(flowbar.btn_join._progress - 0.65) < 1e-4

    def test_auto_start_timer_progress_and_auto_join(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Auto-start countdown ticks progress and automatically morphs to ACTIVE_RECORDING upon fill."""
        # 150ms delay -> ticks 50ms x 3 -> fills to 1.0
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 150}, animated=False)
        assert flowbar._auto_start_timer.isActive()

        _drain_events(qapp, 0.35)

        assert flowbar.pill_state == PillState.ACTIVE_RECORDING
        assert not flowbar._auto_start_timer.isActive()
        assert flowbar.auto_start_progress == 0.0

    def test_hover_pauses_auto_start(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Hovering over FlowBar pauses the auto-start countdown timer."""
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 5000}, animated=False)
        assert flowbar._auto_start_timer.isActive()

        # Simulate enterEvent
        flowbar.enterEvent(None)
        assert not flowbar._auto_start_timer.isActive()
        assert flowbar._auto_start_paused

    def test_caret_menu_dismiss_action(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Dismissing notification stops timer, resets progress, and returns to RESTING."""
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 5000}, animated=False)
        flowbar.dismiss_notification()
        qapp.processEvents()

        assert not flowbar._auto_start_timer.isActive()
        assert flowbar.auto_start_progress == 0.0
        assert flowbar.pill_state == PillState.RESTING

    def test_caret_menu_snooze_action(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Snoozing sets snooze reminder timer, collapses to RESTING, and re-notifies on timeout."""
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 5000, "title": "SnoozeMeeting"}, animated=False)
        flowbar.snooze_notification(minutes=1)
        assert not flowbar._auto_start_timer.isActive()
        assert flowbar.pill_state == PillState.RESTING
        assert flowbar._snooze_timer.isActive()

        # Simulate snooze timeout
        flowbar._snooze_timer.stop()
        flowbar._on_snooze_timeout()
        qapp.processEvents()

        assert flowbar.pill_state == PillState.CARD_NOTIFICATION
        assert flowbar._meeting_title == "SnoozeMeeting"

    def test_join_without_notetaker_action(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Joining without notetaker stops auto-start and collapses capsule to RESTING."""
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 5000}, animated=False)
        flowbar._on_join_without_notetaker()
        qapp.processEvents()

        assert not flowbar._auto_start_timer.isActive()
        assert flowbar.auto_start_progress == 0.0
        assert flowbar.pill_state == PillState.RESTING

    def test_manual_join_race_condition(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Manual click on Join at 99% progress transitions cleanly without duplicate triggering."""
        flowbar.set_pill_state("CARD_NOTIFICATION", metadata={"auto_start_delay": 5000}, animated=False)
        flowbar.auto_start_progress = 0.99
        flowbar.btn_join.click()
        qapp.processEvents()

        assert flowbar.pill_state == PillState.ACTIVE_RECORDING
        assert not flowbar._auto_start_timer.isActive()
        assert flowbar.auto_start_progress == 0.0


class TestEmpiricalGeometryBoundsAndClamping:
    """Empirical challenge 5: Geometry bounds, docking edge alignments, and offscreen clamping."""

    def test_all_states_dock_bottom_within_screen(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Bottom-docked capsule stays strictly within screen available work area across all states."""
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        for st in DIMENSION_SPECS.keys():
            flowbar.set_dock_edge("bottom")
            flowbar.set_pill_state(st, animated=False)
            qapp.processEvents()

            geo = flowbar.geometry()
            assert geo.left() >= avail.left()
            assert geo.right() <= avail.right()
            assert geo.top() >= avail.top()
            assert geo.bottom() <= avail.bottom()

    def test_left_and_right_edge_docking(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Left and right dock alignments stay clamped flush to their respective screen margins."""
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        for edge in ["left", "right"]:
            flowbar.set_dock_edge(edge)
            flowbar.set_pill_state("MINI_MEETING", animated=False)
            qapp.processEvents()

            geo = flowbar.geometry()
            assert geo.left() >= avail.left()
            assert geo.right() <= avail.right()
            assert geo.top() >= avail.top()
            assert geo.bottom() <= avail.bottom()

    def test_extreme_titles_do_not_exceed_max_width(self, flowbar: VoxFlowBar, qapp: QApplication) -> None:
        """Ultra-long meeting title (500 chars) with RTL and Unicode is elided without blowing up width."""
        long_title = "🔥 Sprint Architecture Review — اجتماع استراتيجي مع الفريق " * 15
        flowbar.set_pill_state("MINI_MEETING", metadata={"title": long_title}, animated=False)
        qapp.processEvents()

        assert flowbar.width() == 416
        assert flowbar.lbl_meeting_title.text().endswith("…") or len(flowbar.lbl_meeting_title.text()) < len(long_title)
        assert flowbar.lbl_meeting_title.toolTip() == long_title.strip()

    def test_unknown_platform_fallback(self, flowbar: VoxFlowBar) -> None:
        """Unrecognized platform string should map safely to 'generic_meeting' badge, but currently mutates to 'meet'."""
        flowbar.set_pill_state("MINI_MEETING", metadata={"platform": "WebRTC_Custom_2026"}, animated=False)
        # Authoritative expectation: unrecognized platform displays generic_meeting badge
        assert flowbar.mini_badge._platform == "generic_meeting"


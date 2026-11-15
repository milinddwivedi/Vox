"""
Tests for VOX Toggle Bar Bug Fixes:
- Active meeting timer live updating on toggle bar (lbl_state_text)
- Real-time audio waveform animation from meeting tick RMS levels
- PillState.RESTING hover expansion to READY and click-to-dictate trigger
- Upcoming meeting countdown timer display and tick decrement in MINI_MEETING
- Controller signal integration (meeting_lifecycle_changed, calendar_upcoming_changed, dictation_completed)
- Calendar alert routing to FlowBar
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from voxlocal.calendar.base import CalendarMeeting
from voxlocal.engine.dictation_processor import TransformationMode
from voxlocal.ui.floating_pill import PillState, VoxFlowBar


@pytest.fixture
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


@pytest.fixture
def mock_controller():
    ctrl = MagicMock()
    ctrl.config = MagicMock()
    ctrl.config.flow_bar_edge = "bottom"
    ctrl.config.sound_effects = False
    ctrl.config.scratchpad_in_flow_bar = True
    ctrl.config.auto_record_calls = True
    ctrl.config.ptt_hotkey = "ctrl+win"
    ctrl.is_hands_free_active = False
    ctrl._last_dictation_id = 42
    return ctrl


def test_active_meeting_timer_updates_state_text(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_pill_state(PillState.ACTIVE_RECORDING, animated=False)
    assert bar._state == PillState.ACTIVE_RECORDING
    assert bar.lbl_state_text.text() == "00:00"

    # Simulate 85 seconds meeting tick
    bar._on_controller_meeting_tick(85.0, 0.45, 0.2, True, True)
    assert bar._meeting_elapsed_str == "01:25"
    assert bar.lbl_state_text.text() == "01:25"
    assert bar.meeting_chip.text() == "● 01:25 Notetaker"
    bar.hide()


def test_active_meeting_audio_level_animates_waveform(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_pill_state(PillState.ACTIVE_RECORDING, animated=False)

    # Initial level
    bar.set_audio_level(0.0)
    assert bar.waveform_capsule._target_level == 0.0

    # Tick with mic=0.6, sys=0.2 -> effective level=0.6
    bar._on_controller_meeting_tick(10.0, 0.6, 0.2, True, True)
    assert bar.waveform_capsule._target_level == pytest.approx(0.6, rel=1e-2)
    bar.hide()


def test_resting_pill_hover_expands_to_ready(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_pill_state(PillState.RESTING, animated=False)
    assert bar._state == PillState.RESTING
    assert bar.pill_state == PillState.RESTING

    # Hovering over resting bar expands pill_state property to READY
    bar._is_hovered = True
    assert bar.pill_state == PillState.READY

    bar._sync_pill_visuals()
    assert bar.upper_pill.styleSheet() != ""
    assert not bar.waveform_capsule.isHidden()

    # Unhover restores RESTING
    bar._is_hovered = False
    assert bar.pill_state == PillState.RESTING
    bar._sync_pill_visuals()
    assert bar.waveform_capsule.isHidden()
    bar.hide()


def test_resting_pill_clicked_starts_dictation(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.set_pill_state(PillState.RESTING, animated=False)

    called = []
    bar.start_dictation_requested.connect(lambda: called.append(True))

    bar._on_pill_clicked()
    assert len(called) == 1
    mock_controller.toggle_dictation.assert_called_once()


def test_countdown_timer_in_mini_meeting_and_tick(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()

    # Transition to MINI_MEETING with countdown metadata
    bar.set_pill_state(
        PillState.MINI_MEETING,
        metadata={"title": "Team Sync", "platform": "teams", "seconds_until_start": 125.0},
        animated=False,
    )
    assert bar._state == PillState.MINI_MEETING
    assert bar.lbl_countdown_timer.text() == "in 2m"

    # Simulate 1-second countdown tick
    bar._on_countdown_tick()
    assert bar._current_metadata["seconds_until_start"] == pytest.approx(124.0, rel=1e-2)
    assert bar.lbl_countdown_timer.text() == "in 2m"

    # Set to 5 seconds remaining
    bar._current_metadata["seconds_until_start"] = 5.0
    bar._on_countdown_tick()
    assert bar.lbl_countdown_timer.text() == "in 4s"

    # Set to 0 seconds remaining
    bar._current_metadata["seconds_until_start"] = 0.5
    bar._on_countdown_tick()
    assert bar.lbl_countdown_timer.text() == "now"
    bar.hide()


def test_controller_meeting_lifecycle_transitions(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_pill_state(PillState.RESTING, animated=False)

    # Lifecycle: meeting_detected -> CARD_NOTIFICATION
    bar._on_controller_meeting_lifecycle("meeting_detected", "Call detected")
    assert bar._state == PillState.CARD_NOTIFICATION

    # Lifecycle: recording -> ACTIVE_RECORDING
    bar._on_controller_meeting_lifecycle("recording", "Recording started")
    assert bar._state == PillState.ACTIVE_RECORDING

    # Lifecycle: completed -> RESTING
    bar._on_controller_meeting_lifecycle("completed", "Recording finished")
    assert bar._state == PillState.RESTING
    bar.hide()


def test_calendar_upcoming_changed_mini_meeting_routing(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_pill_state(PillState.RESTING, animated=False)

    now = datetime.now()
    meeting = CalendarMeeting(
        id="mtg123",
        title="Weekly Sprint",
        start_time=now + timedelta(minutes=4),
        end_time=now + timedelta(minutes=34),
        platform="zoom",
    )

    bar._on_calendar_upcoming_changed([meeting])
    assert bar._state == PillState.MINI_MEETING
    assert bar._meeting_title == "Weekly Sprint"
    assert bar._meeting_platform == "zoom"
    assert "in 4m" in bar.lbl_countdown_timer.text() or "in 3m" in bar.lbl_countdown_timer.text()

    # Empty list collapses back to resting
    bar._on_calendar_upcoming_changed([])
    assert bar._state == PillState.RESTING
    bar.hide()


def test_dictation_completed_toast_feedback(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.show()
    bar.set_state(PillState.LISTENING)

    bar._on_controller_dictation_completed("Hello world", "Notepad", "typewrite")
    assert bar._state == PillState.COMMITTED
    assert bar._last_entry_id == 42
    assert bar.lbl_state_text.text() == "Cleaned up"
    assert not bar.btn_check.isHidden()
    assert not bar.btn_undo_toast.isHidden()
    bar.hide()


def test_wispr_active_recording_chevron_contextual_panel_and_finishing(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.set_dock_edge("right")
    bar.show()
    bar.set_pill_state(PillState.ACTIVE_RECORDING, metadata={"title": "Sync", "platform": "zoom"}, animated=False)

    assert not bar.isHidden()
    assert bar.satellite_chevron.isHidden()
    assert (bar.width(), bar.height()) == (30, 72)

    # Hovering reveals the satellite chevron and expands height to 30x106 without shifting pill
    bar.enterEvent(None)
    assert not bar.satellite_chevron.isHidden()
    assert bar.satellite_chevron._opacity >= 0.35
    assert (bar.width(), bar.height()) == (30, 106)

    # Clicking chevron toggles the Contextual Meeting Panel cleanly
    bar._toggle_contextual_panel()
    assert bar._contextual_panel_open is True
    assert not bar.contextual_panel.isHidden()
    bar._toggle_contextual_panel()
    assert bar._contextual_panel_open is False

    # Finishing hides the chevron and switches capsule to finishing spinner mode
    bar._on_controller_meeting_lifecycle("finalizing", "")
    assert bar._meeting_finishing is True
    assert bar.satellite_chevron.isHidden()
    assert bar.active_recording_capsule._finishing is True

    bar.hide_widget()


def test_wispr_upcoming_popover_cancelled_toast_and_mistake_card(app, mock_controller):
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.set_dock_edge("bottom")
    bar.show()

    # Upcoming popover toggles open cleanly on chevron click
    assert bar.upcoming_popover.isHidden()
    bar._on_notetaker_chevron_clicked()
    assert bar.upcoming_popover.isVisible()
    bar.upcoming_popover.hide()

    # Cancelling dictation triggers the cancelled toast
    bar.set_state(PillState.LISTENING)
    bar._on_cancel_clicked()
    assert bar.cancelled_toast.isVisible()
    bar.cancelled_toast.hide()

    # Short recording save triggers Started by mistake? card
    bar.set_pill_state(PillState.ACTIVE_RECORDING, animated=False)
    bar._last_meeting_elapsed_sec = 2.0
    bar._pending_safeguard_check = True
    bar._meeting_active = False
    bar._on_meeting_saved_and_transcribed(77)
    assert bar.mistake_card.isVisible()
    bar.mistake_card.hide()
    bar.hide_widget()


def test_ready_bar_fixed_dimensions_and_no_hover_shift(app, mock_controller):
    """Verify ready bar has fixed size and does not resize or shift on hover."""
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.set_dock_edge("bottom")
    bar.set_pill_state(PillState.READY, animated=False)
    bar.show()

    initial_dims = bar._ready_dims()
    assert initial_dims == (82, 30)

    # Hovering over notetaker button does NOT expand width from 30 to 52
    bar.btn_notetaker.enterEvent(MagicMock())
    assert bar.btn_notetaker.width() == 30
    assert bar.btn_notetaker.height() == 30
    assert bar._ready_dims() == (82, 30)

    # Leaving notetaker button preserves fixed dimensions
    bar.btn_notetaker.leaveEvent(MagicMock())
    assert bar.btn_notetaker.width() == 30
    assert bar._ready_dims() == (82, 30)
    bar.hide_widget()


def test_chevron_click_opens_popover_without_starting_dictation(app, mock_controller):
    """Verify clicking the arrow chevron opens upcoming popover and does NOT start dictation."""
    mock_controller.toggle_dictation.reset_mock()
    bar = VoxFlowBar(parent=None, controller=mock_controller)
    bar.set_dock_edge("bottom")
    bar.set_pill_state(PillState.READY, animated=False)
    bar.show()

    dictation_signals = []
    bar.start_dictation_requested.connect(lambda: dictation_signals.append(True))

    assert bar.upcoming_popover.isHidden()

    # Simulate click on btn_notetaker with no upcoming meetings
    mock_controller.calendar_engine.get_upcoming_meetings.return_value = []
    bar.btn_notetaker._is_down = True
    ev = MagicMock()
    ev.button.return_value = Qt.MouseButton.LeftButton
    ev.globalPosition().toPoint.return_value = bar.btn_notetaker.mapToGlobal(QPoint(15, 15))
    bar.btn_notetaker.mouseReleaseEvent(ev)

    # Popover is now visible
    assert bar.upcoming_popover.isVisible()

    # Dictation must NOT have started
    assert len(dictation_signals) == 0
    mock_controller.toggle_dictation.assert_not_called()

    # Empty state popover text
    assert bar.upcoming_popover.lbl_title.text() == "No meetings for today"

    bar.upcoming_popover.hide()
    bar.hide_widget()


def test_arrow_direction_per_dock_edge(app, mock_controller):
    """Verify arrow orientation correctly reflects dock edges."""
    bar = VoxFlowBar(parent=None, controller=mock_controller)

    bar.set_dock_edge("bottom")
    assert bar.btn_notetaker._dock_edge == "bottom"

    bar.set_dock_edge("right")
    assert bar.btn_notetaker._dock_edge == "right"
    # When docked on right, ready dims are vertical
    assert bar._ready_dims() == (30, 78)

    bar.set_dock_edge("left")
    assert bar.btn_notetaker._dock_edge == "left"
    assert bar._ready_dims() == (30, 78)

    bar.hide_widget()

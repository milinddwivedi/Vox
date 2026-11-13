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

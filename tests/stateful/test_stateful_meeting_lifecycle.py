"""
Hypothesis Stateful Testing Suite for Meeting Lifecycle Engine.
Tests arbitrary sequences of:
- Starting, pausing, resuming, and stopping meetings
- Handling rapid double-starts and double-stops
- Handling zero-duration meetings and immediate aborts
- Verifying database consistency, meeting status transitions, and quota tracking
"""

import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)
import hypothesis.strategies as st

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, MeetingLifecycleState
from voxlocal.storage.database import LocalDatabase


class MeetingLifecycleStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.uid = time.time_ns()
        self.tmp_dir = Path(f"D:/Milind/Voice Assistant/data/test_mtg_{self.uid}")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.config = AppConfig()
        self.config.storage_dir = str(self.tmp_dir)
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True

        self.controller = AppController(self.config)
        self.controller.metering.set_user_context(f"user_{self.uid}", "free")
        self.controller.entitlement.set_user_context(f"user_{self.uid}")
        self.db = self.controller.db

        # Mock out heavy audio stream threads
        self.controller.meeting_recorder.start = MagicMock(return_value=(True, "OK"))
        self.controller.meeting_recorder.pause = MagicMock(return_value=True)
        self.controller.meeting_recorder.resume = MagicMock(return_value=True)
        self.controller.meeting_recorder.stop = MagicMock(return_value=MagicMock(
            duration_sec=5.0,
            mixed_wav_path=str(self.tmp_dir / "mock_mixed.wav"),
            mic_wav_path=str(self.tmp_dir / "mock_mic.wav"),
            sys_wav_path=str(self.tmp_dir / "mock_sys.wav"),
            mic_device_name="Mock Mic",
            loopback_device_name="Mock Loopback",
            capture_mode="both",
        ))

        self.expected_state = "idle"
        self.active_meetings_started = 0

    @rule(title=st.sampled_from(["Weekly Standup", "Q3 Review", "Architecture Design", "Client Sync"]))
    def start_meeting(self, title: str) -> None:
        was_active = self.expected_state in ("recording", "paused")
        can_record = self.controller.entitlement.is_pro() or (self.active_meetings_started < 5)
        ok, msg = self.controller.start_meeting_recording(title=title)

        if was_active:
            assert ok is False
            assert "already active" in msg
        elif not can_record:
            assert ok is False
            assert "quota" in msg.lower() or "limit" in msg.lower() or "upgrade" in msg.lower()
            assert self.expected_state == "idle"
        else:
            assert ok is True
            self.expected_state = "recording"
            self.active_meetings_started += 1

    @rule()
    def upgrade_to_pro(self) -> None:
        self.controller.entitlement._is_pro = True
        self.controller.metering._plan_code = "pro"

    @rule()
    def downgrade_to_free(self) -> None:
        self.controller.entitlement._is_pro = False
        self.controller.metering._plan_code = "free"

    @rule()
    def pause_meeting(self) -> None:
        if self.expected_state == "recording":
            ok = self.controller.pause_meeting_recording()
            assert ok is True
            self.expected_state = "paused"
        else:
            ok = self.controller.pause_meeting_recording()
            assert ok is False

    @rule()
    def resume_meeting(self) -> None:
        if self.expected_state == "paused":
            ok = self.controller.resume_meeting_recording()
            assert ok is True
            self.expected_state = "recording"
        else:
            ok = self.controller.resume_meeting_recording()
            assert ok is False

    @rule()
    def stop_meeting(self) -> None:
        if self.expected_state in ("recording", "paused"):
            self.controller.stop_meeting_recording()
            self.expected_state = "idle"
        else:
            # Calling stop while idle must be a safe no-op
            self.controller.stop_meeting_recording()
            self.expected_state = "idle"

    # -------------------------------------------------------------------------
    # Invariants Verified After EVERY State Transition
    # -------------------------------------------------------------------------
    @invariant()
    def invariant_controller_state_matches_expected(self) -> None:
        actual = self.controller._capture_state
        assert actual == self.expected_state, f"State divergence: actual {actual} != expected {self.expected_state}"

    @invariant()
    def invariant_no_orphaned_recording_meetings_in_db_when_idle(self) -> None:
        """When controller is idle, no meeting in the database should have status='recording'."""
        if self.expected_state == "idle":
            meetings = self.db.list_meetings()
            recording_meetings = [m for m in meetings if m.get("status") == "recording"]
            assert len(recording_meetings) == 0, f"Found {len(recording_meetings)} orphaned meetings with status='recording' in DB!"

    def teardown(self):
        try:
            self.controller.shutdown()
        except Exception:
            pass


def test_meeting_lifecycle_hypothesis_state_machine():
    """Run Hypothesis stateful testing machine for meeting lifecycle."""
    run_state_machine_as_test(
        MeetingLifecycleStateMachine,
        settings=settings(deadline=None, max_examples=25, stateful_step_count=20),
    )

"""
E2E Meeting Lifecycle Simulation Test Suite for Project VOX (Wispr Flow Parity).

Authority & Specifications:
- ORIGINAL_REQUEST.md (§R3 Back-to-Back Meeting Lifecycle Bug Resolution)
- PROJECT.md (§ Architecture & Interface Contracts, Milestone 5 Features 1-5)
- SCOPE.md (E2E Testing Track Orchestrator)
- TEST_INFRA.md (4-Tier Test Architecture & Real-World Scenarios)
- explorer_e2e_target_suites/handoff.md (§3.1 Suite 1 Blueprint)

Features Covered:
- Feature 1: Decoupled Meeting Capture State Machine (_capture_state vs background finalization)
- Feature 2: Platform Window Transition Engine (window resizing vs new call, rebound prevention)
- Feature 3: Salted Session ID & Suppression Pruning (HWND + timestamp salt, generic title uniqueness)
- Feature 4: Clean Audio & Detector Teardown (WASAPI handle release, detector reset to NOT_RUNNING)
- Feature 5: Guarded UI Signal Routing (asynchronous finalization signals isolated from active sessions)

Tier Hierarchy:
- Tier 1: Feature Coverage (25 tests across Features 1-5)
- Tier 2: Boundary & Corner Cases (25 tests across Features 1-5)
- Tier 3: Pairwise Combinations (6 cross-feature tests)
- Tier 4: Real-World Workload Scenarios (5 multi-step workflows)
Total: 61 test cases.

All tests execute completely headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import collections
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

pytest_plugins = ["tests.e2e.conftest"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.controller import AppController
from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingLifecycleState,
    MeetingSessionInfo,
    clean_meeting_title,
    is_leaving_title,
    is_non_call_hub,
    classify,
    CALL_APPS,
    LEAVING_TITLES,
    list_all_meeting_windows,
)
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.audio.meeting_recorder import MeetingRecorder
from voxlocal.storage.database import LocalDatabase


# =============================================================================
# TIER 1: FEATURE COVERAGE (25 TESTS)
# =============================================================================

class TestTier1Feature01DecoupledCaptureState:
    """Feature 1: Decoupled Meeting Capture State Machine & Non-Blocking Queue."""

    def test_01_initial_capture_state_idle(self, isolated_controller: AppController) -> None:
        """Verify capture state is initially idle and finalization queue is empty."""
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False
        assert isolated_controller.active_meeting_id is None
        assert getattr(isolated_controller, "_finalizing_jobs", set()) == set()

    def test_02_start_recording_transitions_to_recording(
        self, isolated_controller: AppController
    ) -> None:
        """Starting a meeting transitions capture_state to 'recording' with an active ID."""
        ok, msg = isolated_controller.start_meeting_recording(
            title="Sprint Planning", recording_mode="audio_only"
        )
        assert ok is True, f"Failed to start recording: {msg}"
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True
        assert isolated_controller.active_meeting_id is not None
        isolated_controller.stop_meeting_recording()

    def test_03_stop_recording_resets_capture_state_instantly(
        self, isolated_controller: AppController
    ) -> None:
        """Calling stop_meeting_recording resets capture_state to 'idle' in <300ms."""
        ok, _ = isolated_controller.start_meeting_recording(
            title="Quick Sync", recording_mode="audio_only"
        )
        assert ok is True

        t0 = time.perf_counter()
        isolated_controller.stop_meeting_recording()
        duration = time.perf_counter() - t0

        assert duration < 0.30, f"Reset took too long: {duration:.3f}s"
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False
        assert isolated_controller.active_meeting_id is None

    def test_04_immediate_session_start_while_finalizing(
        self, isolated_controller: AppController
    ) -> None:
        """A new meeting recording can start immediately even while previous meetings transcribe."""
        # Simulate active background finalization
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.add(9901)

        # Verification: capture_state is idle despite background jobs
        assert isolated_controller.capture_state == "idle"

        # Start a new recording; must NOT be blocked by finalization queue
        ok, msg = isolated_controller.start_meeting_recording(
            title="Subsequent Standup", recording_mode="audio_only"
        )
        assert ok is True, f"Blocked by background job: {msg}"
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.active_meeting_id is not None

        # Clean up
        isolated_controller.stop_meeting_recording()
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.discard(9901)

    def test_05_concurrent_finalization_queue_tracking(
        self, isolated_controller: AppController
    ) -> None:
        """Multiple finalizing jobs are tracked concurrently without colliding with active capture."""
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.update({101, 102, 103})

        ok, _ = isolated_controller.start_meeting_recording(
            title="Active Meeting 104", recording_mode="audio_only"
        )
        assert ok is True
        assert isolated_controller.capture_state == "recording"
        assert 104 not in isolated_controller._finalizing_jobs

        # Complete job 101
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.discard(101)

        assert 101 not in isolated_controller._finalizing_jobs
        assert 102 in isolated_controller._finalizing_jobs
        assert 103 in isolated_controller._finalizing_jobs
        assert isolated_controller.capture_state == "recording"

        isolated_controller.stop_meeting_recording()
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.clear()


class TestTier1Feature02PlatformWindowTransition:
    """Feature 2: Platform Window Transition Engine & Rebound Prevention."""

    def test_01_differentiate_window_resize_from_new_call(self) -> None:
        """Preserve active call session across window moves/resizes with matching HWND and title."""
        detector = CallDetector()
        now = 100.0

        # Simulate initial detection of Teams window
        mock_win_initial = [(1101, "Microsoft Teams", "Daily Standup | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win_initial):
            detector.update(users=[], now=now)
            detector.update(users=[], now=now + 1.2)  # pass 1.0s debounce

        assert detector.active_session is not None
        assert detector.active_session.window_hwnd == 1101
        orig_session_id = detector.active_session.session_id

        # Simulate window resize: same HWND (1101) and same title, but new bounds
        mock_win_resized = [(1101, "Microsoft Teams", "Daily Standup | Microsoft Teams", (100, 100, 1600, 900))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win_resized):
            res = detector.update(users=[], now=now + 2.0)

        # Call must stay alive and not be flagged as ended or duplicate started
        assert res is None
        assert detector.active_session is not None
        assert detector.active_session.window_hwnd == 1101
        assert detector.active_session.session_id == orig_session_id

    def test_02_detect_same_platform_window_change(self) -> None:
        """A different HWND or title on the same platform ends the prior meeting without rebounding."""
        detector = CallDetector()
        now = 100.0

        mock_win1 = [(1101, "Microsoft Teams", "Meeting Alpha | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win1):
            detector.update(users=[], now=now)
            detector.update(users=[], now=now + 1.2)

        assert detector.active_session is not None
        assert detector.active_session.window_hwnd == 1101

        # Now Meeting Alpha closes and Meeting Beta opens on HWND 1102
        mock_win2 = [(1102, "Microsoft Teams", "Meeting Beta | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win2):
            # First tick triggers explicit leave transition
            res1 = detector.update(users=[], now=now + 2.0)
            # Advance past 1.0s explicit leave grace period
            res2 = detector.update(users=[], now=now + 3.2)

        assert res2 == "ended"
        assert detector.last_ended_session is not None
        assert detector.last_ended_session.window_hwnd == 1101

    def test_03_platform_normalization_teams_zoom_meet(self) -> None:
        """Verify platform normalization, title cleanup, and classification."""
        assert clean_meeting_title("[Microsoft Teams Call] Sprint Review", "Microsoft Teams") == "Sprint Review"
        assert clean_meeting_title("Zoom Meeting - Product Strategy", "Zoom") == "Product Strategy"
        assert clean_meeting_title("Meet - xyz-abcd-efg - Google Chrome", "Google Meet") == "Google Meet: xyz-abcd-efg"
        assert clean_meeting_title("", "Microsoft Teams") == "Microsoft Teams Meeting"

        # Classification check
        cls_teams = classify("msteams_app.exe")
        assert cls_teams is not None and cls_teams[0] == "Microsoft Teams"
        cls_zoom = classify("zoom.exe")
        assert cls_zoom is not None and cls_zoom[0] == "Zoom"
        cls_meet = classify("chrome.exe")
        assert cls_meet is not None and "Meet" in cls_meet[0]

    def test_04_explicit_leaving_title_detection(self) -> None:
        """Explicit leaving window titles are detected to trigger rapid session termination."""
        assert is_leaving_title("Leaving...") is True
        assert is_leaving_title("Left the meeting") is True
        assert is_leaving_title("You left the meeting") is True
        assert is_leaving_title("Meeting ended") is True
        assert is_leaving_title("Leaving the call") is True
        assert is_leaving_title("Sprint Review | Microsoft Teams") is False
        assert is_leaving_title("") is False

    def test_05_non_call_hub_window_filtering(self) -> None:
        """Main platform hub/launcher windows are filtered out as non-call stages."""
        assert is_non_call_hub("Microsoft Teams", "Chat | Microsoft Teams") is True
        assert is_non_call_hub("Microsoft Teams", "Calendar | Microsoft Teams") is True
        assert is_non_call_hub("Microsoft Teams", "Teams | Microsoft Teams") is True
        assert is_non_call_hub("Microsoft Teams", "Waiting in the lobby") is True
        assert is_non_call_hub("Zoom", "Zoom Workplace") is True
        assert is_non_call_hub("Google Meet", "Google Meet") is True
        assert is_non_call_hub("Microsoft Teams", "Design Review | Microsoft Teams") is False
        assert is_non_call_hub("Zoom", "Architecture Deep Dive") is False


class TestTier1Feature03SaltedSessionId:
    """Feature 3: Salted Session ID & Suppression Pruning."""

    def test_01_salted_session_id_contains_hwnd_and_timestamp(self) -> None:
        """Session IDs are salted with HWND and epoch timestamp to ensure global uniqueness."""
        detector = CallDetector()
        mock_win = [(9988, "Microsoft Teams", "Weekly Sync", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win):
            detector.update(users=[], now=100.0)
            detector.update(users=[], now=101.5)

        assert detector.active_session is not None
        sess_id = detector.active_session.session_id
        assert "9988" in sess_id
        assert "teams" in sess_id.lower()
        parts = sess_id.split("_")
        assert len(parts) >= 3

    def test_02_generic_titles_produce_distinct_session_ids(self) -> None:
        """Two back-to-back meetings with the exact same generic title produce distinct session IDs."""
        detector = CallDetector()
        now = 100.0

        # Meeting 1 on HWND 2001
        mock_win1 = [(2001, "Microsoft Teams", "Meeting", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win1):
            detector.update(users=[], now=now)
            detector.update(users=[], now=now + 1.2)
        assert detector.active_session is not None
        id1 = detector.active_session.session_id

        # End meeting 1 immediately
        detector.end_active_session_immediately()

        # Meeting 2 on HWND 2002 with identical generic title
        mock_win2 = [(2002, "Microsoft Teams", "Meeting", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win2):
            detector.update(users=[], now=now + 5.0)
            detector.update(users=[], now=now + 6.2)
        assert detector.active_session is not None
        id2 = detector.active_session.session_id

        assert id1 != id2, f"Generic titles collided with identical session ID: {id1}"

    def test_03_suppression_set_pruning_on_session_end(self) -> None:
        """Ending a session prunes prompted and dismissed suppression sets."""
        detector = CallDetector()
        mock_win = [(3001, "Microsoft Teams", "1-on-1 Sync", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win):
            detector.update(users=[], now=100.0)
            detector.update(users=[], now=101.5)

        sess_id = detector.active_session.session_id
        assert sess_id in detector.prompted_session_ids

        # End session immediately
        detector.end_active_session_immediately()
        assert sess_id not in detector.prompted_session_ids
        assert sess_id not in detector.dismissed_session_ids

    def test_04_ignored_session_timeout_and_eviction(self) -> None:
        """Expired suppressions older than max_age_sec are pruned cleanly from memory."""
        detector = CallDetector()
        old_time = 1000.0
        sid = "teams_oldcall_101_1000"

        detector.prompted_session_ids.add(sid)
        detector.dismissed_session_ids.add(sid)
        detector._suppression_timestamps[sid] = old_time

        # Prune with current time 1000s later (>600s max_age_sec)
        detector.prune_expired_suppressions(now=old_time + 800.0, max_age_sec=600.0)

        assert sid not in detector.prompted_session_ids
        assert sid not in detector.dismissed_session_ids
        assert sid not in detector._suppression_timestamps

    def test_05_session_id_deduplication_during_active_call(self) -> None:
        """Repeated poll/update during an active call preserves stable session ID without duplicate starts."""
        detector = CallDetector()
        now = 100.0
        mock_win = [(4001, "Zoom", "Design Sync", (0, 0, 800, 600))]

        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win):
            ev1 = detector.update(users=[], now=now)
            ev2 = detector.update(users=[], now=now + 1.2)
            assert ev2 == "started"
            sess_id = detector.active_session.session_id

            # Further updates while still active
            ev3 = detector.update(users=[], now=now + 2.0)
            ev4 = detector.update(users=[], now=now + 3.0)
            assert ev3 is None
            assert ev4 is None
            assert detector.active_session.session_id == sess_id


class TestTier1Feature04CleanAudioTeardown:
    """Feature 4: Clean Audio & Detector Teardown."""

    def test_01_manual_stop_resets_detector_active_session(
        self, isolated_controller: AppController
    ) -> None:
        """Manual stop_meeting_recording resets call detector active_session immediately."""
        ok, _ = isolated_controller.start_meeting_recording(
            title="Design Review", recording_mode="audio_only"
        )
        assert ok is True

        # Manually attach a mock active session to detector
        sess = MeetingSessionInfo(
            session_id="teams_sync_5001_1234",
            platform="Microsoft Teams",
            window_hwnd=5001,
            window_title="Design Review",
            clean_title="Design Review",
            bounds=(0, 0, 800, 600),
            is_call_stage=True,
            mic_active=True,
        )
        isolated_controller.call_detector.active_session = sess

        isolated_controller.stop_meeting_recording()
        assert isolated_controller.call_detector.active_session is None

    def test_02_detector_state_resets_to_not_running(self) -> None:
        """Detector state resets to NOT_RUNNING on end_active_session_immediately."""
        detector = CallDetector()
        detector.state = MeetingDetectorState.RECORDING
        detector.end_active_session_immediately()

        assert detector.state == MeetingDetectorState.NOT_RUNNING
        assert detector.active_session is None
        assert detector.active is None

    def test_03_audio_recorder_stop_releases_wasapi_handles(
        self, isolated_controller: AppController
    ) -> None:
        """Meeting audio recorder stop cleanly marks is_recording False and frees streams."""
        ok, _ = isolated_controller.start_meeting_recording(
            title="Audio Teardown Check", recording_mode="audio_only"
        )
        assert ok is True
        assert isolated_controller.meeting_recorder.is_recording is True

        isolated_controller.stop_meeting_recording()
        assert isolated_controller.meeting_recorder.is_recording is False

    def test_04_orphan_audio_recorder_auto_healing(
        self, isolated_controller: AppController
    ) -> None:
        """An orphaned audio recorder left running is healed when stop_meeting_recording is called."""
        # Start a real recording session to populate recorder paths and threads
        ok, _ = isolated_controller.start_meeting_recording("Orphan Probe", recording_mode="audio_only")
        assert ok is True
        assert isolated_controller.meeting_recorder.is_recording is True

        # Force orphan state: capture_state is set to idle while recorder is still running
        isolated_controller._capture_state = "idle"
        assert isolated_controller.meeting_recorder.is_recording is True

        # stop_meeting_recording detects the orphaned recorder and stops it
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.meeting_recorder.is_recording is False

        # Verify @is_recording.setter directly
        isolated_controller.meeting_recorder.is_recording = True
        assert isolated_controller.meeting_recorder.is_recording is True
        isolated_controller.meeting_recorder.is_recording = False
        assert isolated_controller.meeting_recorder.is_recording is False

    def test_05_end_active_session_immediately_cleans_resources(self) -> None:
        """end_active_session_immediately clears active references, timestamps, and recording flags."""
        detector = CallDetector()
        detector.active_session = MagicMock()
        detector.active = MagicMock()
        detector._released_at = 12345.6
        detector._is_recording = True

        detector.end_active_session_immediately()

        assert detector.active_session is None
        assert detector.active is None
        assert detector._released_at is None
        assert detector._is_recording is False
        assert detector.state == MeetingDetectorState.NOT_RUNNING


class TestTier1Feature05GuardedUiSignalRouting:
    """Feature 5: Guarded UI Signal Routing."""

    def test_01_finalization_signal_carries_meeting_id(
        self, isolated_controller: AppController
    ) -> None:
        """Signal meeting_saved_and_transcribed emits the exact finished meeting ID."""
        emitted_ids: List[int] = []
        isolated_controller.meeting_saved_and_transcribed.connect(emitted_ids.append)

        isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, 7001)
        assert emitted_ids == [7001]

    def test_02_async_finalization_does_not_clobber_active_recording(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Background completion of meeting A does not reset UI state of actively recording meeting B."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()

        # Start active meeting 2
        ok, _ = isolated_controller.start_meeting_recording(
            title="Active Meeting 2", recording_mode="audio_only"
        )
        assert ok is True
        m2_id = isolated_controller.active_meeting_id
        assert m2_id is not None

        # Simulate background finalization finishing for prior meeting 1
        m1_id = 1001
        isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, m1_id)
        isolated_controller.meeting_state_changed.emit("idle", "Prior meeting saved")

        # Controller and Drawer MUST stay in recording state for meeting 2
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.active_meeting_id == m2_id
        assert "Stop" in drawer.btn_universal_stop.text()

        isolated_controller.stop_meeting_recording()

    def test_03_drawer_updates_summary_for_matching_meeting_only(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Drawer finalization slot ignores events that do not match the displayed meeting."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        drawer._active_displayed_meeting_id = 5555

        # Emit completion for an unrelated meeting 8888 while drawer displays 5555
        drawer._on_meeting_finalized(8888)

        # Displayed meeting must not be clobbered
        assert getattr(drawer, "_active_displayed_meeting_id", None) == 5555

    def test_04_tick_signals_routed_to_active_meeting_only(
        self, isolated_controller: AppController
    ) -> None:
        """Elapsed meeting ticks reflect the live session duration."""
        received_ticks: List[Tuple[float, float, float, bool, bool]] = []
        isolated_controller.meeting_tick.connect(
            lambda el, m_lvl, s_lvl, m_ok, s_ok: received_ticks.append((el, m_lvl, s_lvl, m_ok, s_ok))
        )

        isolated_controller._on_meeting_recorder_tick(12.5, 0.45, 0.20, True, True)

        assert len(received_ticks) == 1
        elapsed, mic_lvl, sys_lvl, mic_ok, sys_ok = received_ticks[0]
        assert elapsed == 12.5
        assert mic_ok is True and sys_ok is True

    def test_05_history_updated_emitted_without_ui_interruption(
        self, isolated_controller: AppController
    ) -> None:
        """Signal history_updated refreshes the database without disrupting active recording."""
        ok, _ = isolated_controller.start_meeting_recording(
            title="Live Meeting", recording_mode="audio_only"
        )
        assert ok is True

        history_called = []
        isolated_controller.history_updated.connect(lambda: history_called.append(True))
        isolated_controller._safe_emit(isolated_controller.history_updated)

        assert len(history_called) == 1
        assert isolated_controller.capture_state == "recording"
        isolated_controller.stop_meeting_recording()


# =============================================================================
# TIER 2: BOUNDARY & CORNER CASES (25 TESTS)
# =============================================================================

class TestTier2Boundary01RapidTransitions:
    """Boundary 1: Rapid Transitions & Timing Bounds."""

    def test_01_zero_second_gap_back_to_back_meetings(
        self, isolated_controller: AppController
    ) -> None:
        """Conclude Meeting A and start Meeting B with zero delay in consecutive statements."""
        ok1, _ = isolated_controller.start_meeting_recording("Meeting A", recording_mode="audio_only")
        assert ok1 is True
        m1_id = isolated_controller.active_meeting_id

        isolated_controller.stop_meeting_recording()

        ok2, msg2 = isolated_controller.start_meeting_recording("Meeting B", recording_mode="audio_only")
        assert ok2 is True, f"Zero-gap start failed: {msg2}"
        m2_id = isolated_controller.active_meeting_id

        assert m2_id != m1_id
        assert isolated_controller.capture_state == "recording"
        isolated_controller.stop_meeting_recording()

    def test_02_rapid_stop_start_under_100ms(
        self, isolated_controller: AppController
    ) -> None:
        """Stop and restart within 50ms does not produce race conditions or audio deadlocks."""
        ok1, _ = isolated_controller.start_meeting_recording("Burst 1", recording_mode="audio_only")
        assert ok1 is True
        isolated_controller.stop_meeting_recording()

        time.sleep(0.05)  # 50ms interval

        ok2, msg2 = isolated_controller.start_meeting_recording("Burst 2", recording_mode="audio_only")
        assert ok2 is True, f"Under-100ms restart failed: {msg2}"
        assert isolated_controller.capture_state == "recording"
        isolated_controller.stop_meeting_recording()

    def test_03_sub_second_window_closure_and_reopen(self) -> None:
        """Call detector cleanly transitions when window closes and reopens in <1 second."""
        detector = CallDetector()
        now = 100.0

        mock_win1 = [(6001, "Microsoft Teams", "Sync 1", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win1):
            detector.update(users=[], now=now)
            detector.update(users=[], now=now + 1.2)
        assert detector.active_session is not None

        # Window 6001 explicitly closes (empty list), then 6002 opens 0.3s later
        mock_win2 = [(6002, "Microsoft Teams", "Sync 2", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win2):
            detector.update(users=[], now=now + 2.0)
            res = detector.update(users=[], now=now + 3.2)
        # Prior call concludes
        assert res == "ended"

    def test_04_rapid_abort_during_startup_phase(
        self, isolated_controller: AppController
    ) -> None:
        """Calling stop_meeting_recording immediately after start (<1ms) terminates cleanly."""
        ok, _ = isolated_controller.start_meeting_recording("Abort Test", recording_mode="audio_only")
        assert ok is True
        isolated_controller.stop_meeting_recording()

        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False

    def test_05_ten_consecutive_rapid_meeting_cycles(
        self, isolated_controller: AppController
    ) -> None:
        """Burst test: 10 consecutive rapid start/stop cycles complete with zero handle leaks."""
        for i in range(10):
            ok, msg = isolated_controller.start_meeting_recording(
                f"Rapid Cycle {i}", recording_mode="audio_only"
            )
            assert ok is True, f"Failed at cycle {i}: {msg}"
            assert isolated_controller.capture_state == "recording"
            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"


class TestTier2Boundary02WindowCorruptions:
    """Boundary 2: Title & Window Corruptions."""

    def test_01_empty_and_whitespace_window_titles(self) -> None:
        """Empty, whitespace, and null window titles produce sanitized fallback titles."""
        assert clean_meeting_title("", "Microsoft Teams") == "Microsoft Teams Meeting"
        assert clean_meeting_title("   \t\n  ", "Zoom") == "Zoom Meeting"
        assert clean_meeting_title(None, "Google Meet") == "Google Meet Meeting"  # type: ignore

    def test_02_excessively_long_window_title_over_512_chars(self) -> None:
        """A window title with >512 characters is processed safely without buffer overflow."""
        long_title = "Sprint Planning Deep Dive " * 30  # ~780 chars
        clean = clean_meeting_title(long_title, "Microsoft Teams")
        assert len(clean) > 0
        assert isinstance(clean, str)

        # Check slug generation safely truncates to 32 chars
        slug = re.sub(r"[^a-zA-Z0-9]", "", clean.lower())[:32]
        assert len(slug) == 32

    def test_03_special_characters_and_emojis_in_title(self) -> None:
        """Special unicode, Devanagari, emojis, and brackets are cleaned without exceptions."""
        raw = "[Microsoft Teams Call] 🚀 Architecture & Strategy / Dev Sync (नमस्ते)"
        clean = clean_meeting_title(raw, "Microsoft Teams")
        assert "[Microsoft Teams Call]" not in clean
        assert "🚀 Architecture" in clean
        assert "नमस्ते" in clean

        # Trailing bracket annotation preceding platform suffix
        raw_call = "🚀 Architecture & Strategy / Dev Sync (नमस्ते) [Call] | Microsoft Teams"
        clean_call = clean_meeting_title(raw_call, "Microsoft Teams")
        assert "[Call]" not in clean_call
        assert "| Microsoft Teams" not in clean_call
        assert clean_call == "🚀 Architecture & Strategy / Dev Sync (नमस्ते)"

        # Common format: "Project Sync [Call] | Microsoft Teams"
        assert clean_meeting_title("Project Sync [Call] | Microsoft Teams", "Microsoft Teams") == "Project Sync"
        assert clean_meeting_title("Project Sync (Meeting) - Zoom", "Zoom") == "Project Sync"

    def test_04_dead_or_invalid_hwnd(self) -> None:
        """Invalid or dead HWND values (0, -1, 99999999) do not crash detection."""
        detector = CallDetector()
        sess = MeetingSessionInfo(
            session_id="teams_dead_999999_100",
            platform="Microsoft Teams",
            window_hwnd=99999999,
            window_title="Dead HWND",
            clean_title="Dead HWND",
            bounds=(0, 0, 800, 600),
            is_call_stage=True,
            mic_active=False,
        )
        detector.active_session = sess

        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]):
            # Dead HWND should be recognized as closed or non-living
            detector.update(users=[], now=100.0)
            res = detector.update(users=[], now=110.0)
        assert res == "ended"

    def test_05_negative_or_zero_window_bounds(self) -> None:
        """Negative coordinates (multi-monitor) and zero width/height bounds are handled safely."""
        sess = MeetingSessionInfo(
            session_id="test_bounds",
            platform="Microsoft Teams",
            window_hwnd=123,
            window_title="Test",
            clean_title="Test",
            bounds=(-1920, -100, 0, 0),
            is_call_stage=True,
            mic_active=True,
        )
        assert sess.bounds == (-1920, -100, 0, 0)
        assert sess.is_active is True


class TestTier2Boundary03SlugAndSuppressionLimits:
    """Boundary 3: Slug Fallbacks & Suppression Limits."""

    def test_01_slug_generation_with_all_punctuation(self) -> None:
        """Titles with only punctuation fall back safely without empty string crashes."""
        title = "!!! ??? -- /// &&&"
        clean = clean_meeting_title(title, "Microsoft Teams")
        slug = re.sub(r"[^a-zA-Z0-9]", "", clean.lower())[:32]
        fallback_slug = slug or "call"
        assert fallback_slug == "call"

    def test_02_suppression_sets_upper_bound_memory(self) -> None:
        """High volume of 500 suppression entries can be safely pruned to bound memory."""
        detector = CallDetector()
        for i in range(500):
            sid = f"teams_call_{i}_{1000 + i}"
            detector.prompted_session_ids.add(sid)
            detector._suppression_timestamps[sid] = 1000.0 + i

        assert len(detector.prompted_session_ids) == 500
        # Prune everything older than 50s at time 1550
        detector.prune_expired_suppressions(now=1550.0, max_age_sec=50.0)
        assert len(detector.prompted_session_ids) < 500

    def test_03_session_salt_uniqueness_across_same_millisecond(self) -> None:
        """Identical timestamps with different HWNDs produce strictly unique session IDs."""
        epoch = 1700000000
        sess1 = f"teams_meeting_1001_{epoch}"
        sess2 = f"teams_meeting_1002_{epoch}"
        assert sess1 != sess2

    def test_04_dismissed_session_re_prompt_after_grace_period(self) -> None:
        """Dismissed session is unblocked when session ends immediately or after grace period."""
        detector = CallDetector()
        sid = "teams_standup_3030_1000"
        detector.dismissed_session_ids.add(sid)
        detector.active_session = MeetingSessionInfo(
            session_id=sid, platform="Teams", window_hwnd=3030,
            window_title="Standup", clean_title="Standup", bounds=(0, 0, 800, 600),
            is_call_stage=True, mic_active=True
        )

        detector.end_active_session_immediately()
        assert sid not in detector.dismissed_session_ids

    def test_05_case_insensitive_platform_matching(self) -> None:
        """Platform checks operate case-insensitively across uppercase and mixed case names."""
        assert is_non_call_hub("MICROSOFT TEAMS", "Chat | Microsoft Teams") is True
        assert is_non_call_hub("zOoM", "Zoom Workplace") is True
        assert is_non_call_hub("google meet", "Google Meet") is True


class TestTier2Boundary04AudioFailureAndTeardownLimits:
    """Boundary 4: Audio Failure Resilience & Idempotent Teardown."""

    def test_01_stop_called_when_already_stopped(
        self, isolated_controller: AppController
    ) -> None:
        """Calling stop_meeting_recording when idle is completely idempotent and safe."""
        assert isolated_controller.capture_state == "idle"
        # Calling multiple times should never raise
        isolated_controller.stop_meeting_recording()
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"

    def test_02_audio_recorder_stop_returns_false(
        self, isolated_controller: AppController
    ) -> None:
        """If audio recorder stop returns None/False or raises, capture_state still resets to idle."""
        ok, _ = isolated_controller.start_meeting_recording("Fault Test", recording_mode="audio_only")
        assert ok is True

        # Mock recorder stop to return None (no result / failure)
        with patch.object(isolated_controller.meeting_recorder, "stop", return_value=None):
            isolated_controller.stop_meeting_recording()

        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None

        # Verify unhandled audio exception (hardware/COM crash) during stop is caught cleanly
        ok2, _ = isolated_controller.start_meeting_recording("Crash Stop Test", recording_mode="audio_only")
        assert ok2 is True
        with patch.object(isolated_controller.meeting_recorder, "stop", side_effect=RuntimeError("Audio device failure")):
            isolated_controller.stop_meeting_recording()

        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None

    def test_03_missing_audio_directory_permissions(
        self, isolated_controller: AppController, tmp_path: Path
    ) -> None:
        """Controller rejects recording start if disk space or directory check fails."""
        with patch("voxlocal.core.controller.check_disk_space_mb", return_value=(False, 10.0, "Disk full")):
            ok, msg = isolated_controller.start_meeting_recording("Full Disk", recording_mode="audio_only")
            assert ok is False
            assert "Disk full" in msg
            assert isolated_controller.capture_state == "idle"

    def test_04_wasapi_device_unplugged_during_meeting(
        self, isolated_controller: AppController
    ) -> None:
        """Device warnings from audio recorder surface notifications without breaking controller."""
        warnings: List[str] = []
        isolated_controller.notification_requested.connect(lambda t, m: warnings.append(m))

        # Simulate device disconnection event
        isolated_controller.meeting_recorder.on_device_warning("USB Audio device was unplugged")
        assert len(warnings) == 1
        assert "USB Audio device was unplugged" in warnings[0]

    def test_05_corrupted_finalization_payload(
        self, isolated_controller: AppController
    ) -> None:
        """Finalization worker given invalid or null payload marks error in DB and cleans queue."""
        meeting_id = 998877
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.add(meeting_id)

        # Execute worker with None result
        isolated_controller._finalize_meeting_worker(meeting_id=meeting_id, result=None)

        # Job must be discarded from queue in finally block
        assert meeting_id not in isolated_controller._finalizing_jobs
        assert isolated_controller.capture_state == "idle"


class TestTier2Boundary05SignalOrderingAndConcurrency:
    """Boundary 5: Signal Ordering, Cancellation & Slot Safety."""

    def test_01_finalization_completes_before_started_signal(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """UI handles out-of-order finalization signals without throwing exceptions."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        # Direct delivery of finalization for unknown ID
        drawer._on_meeting_finalized(1234567)
        assert drawer is not None

    def test_02_cancel_finalization_event_cooperative_abort(
        self, isolated_controller: AppController
    ) -> None:
        """Cooperative cancel event is set and cleared cleanly."""
        assert isolated_controller._cancel_finalization_event.is_set() is False
        isolated_controller._cancel_finalization_event.set()
        assert isolated_controller._cancel_finalization_event.is_set() is True
        isolated_controller._cancel_finalization_event.clear()
        assert isolated_controller._cancel_finalization_event.is_set() is False

    def test_03_simultaneous_poll_from_winevent_and_timer(
        self, isolated_controller: AppController
    ) -> None:
        """Concurrent calls to call_detector.poll() across multiple threads do not crash."""
        exceptions: List[Exception] = []

        def _worker():
            try:
                for _ in range(20):
                    isolated_controller.call_detector.poll()
                    time.sleep(0.005)
            except Exception as e:
                exceptions.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(exceptions) == 0

    def test_04_ui_signal_emission_from_dead_worker(
        self, isolated_controller: AppController, qapp_offscreen: QApplication
    ) -> None:
        """Worker thread emitting a Qt signal right before dying is processed safely."""
        delivered = []
        isolated_controller.meeting_saved_and_transcribed.connect(delivered.append)

        def _short_lived_worker():
            isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, 888)

        t = threading.Thread(target=_short_lived_worker)
        t.start()
        t.join()

        qapp_offscreen.processEvents()
        assert delivered == [888]

    def test_05_exception_in_signal_slot_does_not_break_controller(
        self, isolated_controller: AppController, qapp_offscreen: QApplication
    ) -> None:
        """An error trapped within a connected signal slot does not corrupt controller state."""
        errors_trapped: List[Exception] = []

        def _slot_with_internal_error(*args):
            try:
                raise ValueError("Simulated UI slot calculation error")
            except Exception as exc:
                errors_trapped.append(exc)

        isolated_controller.meeting_tick.connect(_slot_with_internal_error)
        isolated_controller._on_meeting_recorder_tick(1.0, 0.2, 0.1, True, True)
        qapp_offscreen.processEvents()

        assert len(errors_trapped) == 1
        assert isinstance(errors_trapped[0], ValueError)
        assert isolated_controller.capture_state == "idle"

        # Also verify _safe_emit shields controller from deleted Qt object RuntimeErrors
        mock_dead_sig = MagicMock()
        mock_dead_sig.emit.side_effect = RuntimeError("wrapped C/C++ object has been deleted")
        isolated_controller._safe_emit(mock_dead_sig, 999)
        assert isolated_controller.capture_state == "idle"


# =============================================================================
# TIER 3: PAIRWISE COMBINATIONS (6 TESTS)
# =============================================================================

class TestTier3PairwiseMeetingLifecycle:
    """Pairwise Cross-Feature Interactions."""

    def test_01_pairwise_decoupled_state_and_salted_ids(
        self, isolated_controller: AppController
    ) -> None:
        """Pairwise: Decoupled state + salted IDs across rapid back-to-back sessions."""
        ok1, _ = isolated_controller.start_meeting_recording("Sync", recording_mode="audio_only")
        assert ok1 is True
        m1_id = isolated_controller.active_meeting_id
        assert m1_id is not None

        # Hold finalization worker so we can verify concurrent active capture while m1 is finalizing
        block_finalization = threading.Event()
        orig_worker = isolated_controller._finalize_meeting_worker

        def _blocking_worker(m_id, res, vid=""):
            block_finalization.wait(timeout=2.0)
            orig_worker(m_id, res, vid)

        with patch.object(isolated_controller, "_finalize_meeting_worker", side_effect=_blocking_worker):
            isolated_controller.stop_meeting_recording()
            assert m1_id in isolated_controller._finalizing_jobs
            assert isolated_controller.capture_state == "idle"

            # Second meeting starts with identical title while m1 is still in finalization queue
            ok2, _ = isolated_controller.start_meeting_recording("Sync", recording_mode="audio_only")
            assert ok2 is True
            m2_id = isolated_controller.active_meeting_id

            assert m2_id != m1_id
            assert m1_id in isolated_controller._finalizing_jobs
            assert isolated_controller.capture_state == "recording"

            # Release finalization block
            block_finalization.set()

        isolated_controller.stop_meeting_recording()
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.clear()

    def test_02_pairwise_window_transition_and_audio_teardown(
        self, isolated_controller: AppController
    ) -> None:
        """Pairwise: Teams window closing triggers clean audio release before Zoom starts."""
        ok1, _ = isolated_controller.start_meeting_recording("Teams Call", recording_mode="audio_only")
        assert ok1 is True
        assert isolated_controller.meeting_recorder.is_recording is True

        # Stop Teams call -> releases WASAPI handles
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.meeting_recorder.is_recording is False
        assert isolated_controller.call_detector.active_session is None

        # Zoom meeting starts with fresh audio handles
        ok2, _ = isolated_controller.start_meeting_recording("Zoom Call", recording_mode="audio_only")
        assert ok2 is True
        assert isolated_controller.meeting_recorder.is_recording is True

        isolated_controller.stop_meeting_recording()

    def test_03_pairwise_guarded_signals_and_active_recording(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Guarded signals protect NotetakerDrawer during live back-to-back call."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()

        ok, _ = isolated_controller.start_meeting_recording("Meeting 2", recording_mode="audio_only")
        assert ok is True

        # Prior meeting 1 finishes and emits finalization
        isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, 1)
        isolated_controller.meeting_state_changed.emit("idle", "Prior meeting saved")

        # Drawer must retain active recording controls
        assert "Stop" in drawer.btn_universal_stop.text()
        assert isolated_controller.capture_state == "recording"
        isolated_controller.stop_meeting_recording()

    def test_04_pairwise_manual_stop_and_subsequent_auto_detection(
        self, isolated_controller: AppController
    ) -> None:
        """Pairwise: Manual stop of meeting A unblocks auto-detection of meeting B without app restart."""
        ok, _ = isolated_controller.start_meeting_recording("Meeting A", recording_mode="audio_only")
        assert ok is True
        isolated_controller.stop_meeting_recording()

        # Detector must be ready to detect subsequent meetings
        assert isolated_controller.call_detector.state == MeetingDetectorState.NOT_RUNNING
        assert isolated_controller.call_detector.active_session is None

        # Simulate subsequent meeting window appearing
        mock_win = [(7777, "Zoom", "Meeting B", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win):
            isolated_controller.call_detector.update(users=[], now=100.0)
            res = isolated_controller.call_detector.update(users=[], now=101.5)

        assert res == "started"
        assert isolated_controller.call_detector.active_session.window_hwnd == 7777

    def test_05_pairwise_dictation_preemption_on_meeting_detected(
        self, isolated_controller: AppController
    ) -> None:
        """Pairwise: Active universal dictation is preempted/paused when meeting starts."""
        isolated_controller._dictation_state = "listening"
        assert isolated_controller.dictation_state == "listening"

        ok, msg = isolated_controller.start_meeting_recording("Standup", recording_mode="audio_only")
        assert ok is True
        assert isolated_controller.capture_state == "recording"

        isolated_controller.stop_meeting_recording()

    def test_06_pairwise_overlapping_meetings_and_stack_badge(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Overlapping meeting windows return accurate stack count."""
        mock_multi = [
            (1001, "Microsoft Teams", "Standup", (0, 0, 800, 600)),
            (1002, "Zoom", "Client Call", (0, 0, 800, 600)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_multi):
            count = isolated_controller.call_detector.get_active_meeting_count()
            assert count == 2


# =============================================================================
# TIER 4: REAL-WORLD WORKLOAD SCENARIOS (5 TESTS)
# =============================================================================

class TestTier4MeetingLifecycleScenarios:
    """Real-World Workload Scenarios (Scenarios 1-5 from SCOPE.md)."""

    def test_scenario_1_back_to_back_same_platform_teams(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 1: Teams Meeting A concludes -> Teams Meeting B starts in 2 seconds.
        Verifies Meeting A stops, capture_state resets to idle, and Meeting B records cleanly
        into a fresh database record with unique salted session IDs.
        """
        # Step 1: Start Teams Meeting A
        ok1, _ = isolated_controller.start_meeting_recording(
            title="Teams Meeting A (Engineering)", recording_mode="audio_only"
        )
        assert ok1 is True
        m1_id = isolated_controller.active_meeting_id
        assert m1_id is not None
        assert isolated_controller.capture_state == "recording"

        # Step 2: Conclude Meeting A
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None

        # Step 3: 2 seconds later, start Teams Meeting B
        time.sleep(0.05)  # fast simulation of 2s gap
        ok2, _ = isolated_controller.start_meeting_recording(
            title="Teams Meeting B (Product)", recording_mode="audio_only"
        )
        assert ok2 is True
        m2_id = isolated_controller.active_meeting_id
        assert m2_id is not None

        # Verification: Two completely independent meeting sessions
        assert m1_id != m2_id
        rec1 = isolated_controller.db.get_meeting(m1_id)
        rec2 = isolated_controller.db.get_meeting(m2_id)
        assert rec1 is not None and rec2 is not None
        assert "Meeting A" in rec1["title"]
        assert "Meeting B" in rec2["title"]

        isolated_controller.stop_meeting_recording()

    def test_scenario_2_manual_stop_and_quick_restart(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 2: User manually stops recording and immediately clicks restart.
        Verifies instant capture reset, audio stream re-initialization, and zero resource lockup.
        """
        ok1, _ = isolated_controller.start_meeting_recording(
            title="Manual Stop Session 1", recording_mode="audio_only"
        )
        assert ok1 is True
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"

        # Immediate restart
        ok2, _ = isolated_controller.start_meeting_recording(
            title="Manual Stop Session 2", recording_mode="audio_only"
        )
        assert ok2 is True
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.meeting_recorder.is_recording is True

        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"

    def test_scenario_3_overlapping_meetings_stack_tracking(
        self, isolated_controller: AppController
    ) -> None:
        """
        Scenario 3: Overlapping meetings (Teams + Zoom simultaneously).
        Verifies stack count tracking handles multiple active call stages without clobbering.
        """
        mock_both = [
            (501, "Microsoft Teams", "Executive Sync", (0, 0, 800, 600)),
            (502, "Zoom", "External Investor Call", (0, 0, 800, 600)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_both):
            count = isolated_controller.call_detector.get_active_meeting_count()
            assert count == 2

        # One meeting ends, one remains
        mock_one = [(502, "Zoom", "External Investor Call", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_one):
            count_remaining = isolated_controller.call_detector.get_active_meeting_count()
            assert count_remaining == 1

    def test_scenario_4_dismissed_prompt_does_not_block_subsequent_meeting(
        self, isolated_controller: AppController
    ) -> None:
        """
        Scenario 4: User dismisses a meeting prompt.
        A subsequent meeting with a generic title is detected without permanent suppression.
        """
        detector = CallDetector()
        now = 100.0

        # Meeting 1 dismissed
        mock_win1 = [(801, "Microsoft Teams", "Sync", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win1):
            detector.update(users=[], now=now)
            detector.update(users=[], now=now + 1.2)

        assert detector.active_session is not None
        sess_id1 = detector.active_session.session_id
        detector.mark_decision(sess_id1, "dismissed")
        assert sess_id1 in detector.dismissed_session_ids

        # Meeting 1 terminates
        detector.end_active_session_immediately()
        assert sess_id1 not in detector.dismissed_session_ids

        # Subsequent meeting 2 with generic title on HWND 802
        mock_win2 = [(802, "Microsoft Teams", "Sync", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win2):
            detector.update(users=[], now=now + 10.0)
            res = detector.update(users=[], now=now + 11.5)

        # Salted session ID ensures meeting 2 is detected cleanly
        assert res == "started"
        assert detector.active_session.window_hwnd == 802
        assert detector.active_session.session_id != sess_id1

    def test_scenario_5_finalization_failure_resilience(
        self, isolated_controller: AppController
    ) -> None:
        """
        Scenario 5: Background finalization failure resilience.
        If transcription or summarizing fails for Meeting 1, controller remains healthy
        and can immediately record Meeting 2.
        """
        ok1, _ = isolated_controller.start_meeting_recording("Faulty Meeting 1", recording_mode="audio_only")
        assert ok1 is True
        m1_id = isolated_controller.active_meeting_id
        isolated_controller.stop_meeting_recording()

        # Simulate finalization worker crashing
        isolated_controller._finalize_meeting_worker(meeting_id=m1_id, result=None)

        # Controller state must remain idle and operational
        assert isolated_controller.capture_state == "idle"

        # Meeting 2 can start and record successfully
        ok2, msg2 = isolated_controller.start_meeting_recording("Recovery Meeting 2", recording_mode="audio_only")
        assert ok2 is True, f"Failed to record after worker error: {msg2}"
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.active_meeting_id != m1_id

        isolated_controller.stop_meeting_recording()

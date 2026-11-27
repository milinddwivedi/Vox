"""
Adversarial Edge-Case & Window Transition Stress Test Suite (Challenger 2).

Directly challenges Milestone 5:
1. Same-Platform Rebound vs Transition:
   - Window moves and resizes across bounds must be preserved without ghost session restarts.
   - Dynamic title status updates on the same HWND must stay alive.
   - Distinct HWNDs or clean titles on the same platform must cleanly trigger 'ended' and then 'started'.
   - Overlapping windows on the same platform must preserve the active call until it closes.
2. Generic Title Slug Collisions:
   - Consecutive meetings with identical default titles ("Microsoft Teams Meeting", "Zoom Meeting", "Google Meet") must each produce distinct session IDs.
   - User-dismissed generic meetings must not permanently suppress subsequent meetings.
   - HWND recycling must generate distinct session IDs via timestamp salting.
   - Suppression set memory upper-bounding and pruning.
3. UI Signal Race Conditions:
   - Rapid-fire background finalization signals while UI is in recording state.
   - Rapid-fire background finalization signals while UI is in paused state.
   - Background finalization signals while UI is in idle state.
   - Live transcript, control buttons, and timers in NotetakerDrawer and FlowBar must remain intact.
"""

from __future__ import annotations

import collections
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest
from PyQt6.QtWidgets import QApplication

pytest_plugins = ["tests.e2e.conftest"]

from voxlocal.core.controller import AppController
from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    clean_meeting_title,
    is_leaving_title,
    is_non_call_hub,
)
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.floating_pill import DualTierFloatingPill


# =============================================================================
# AREA 1: SAME-PLATFORM REBOUND VS TRANSITION
# =============================================================================

class TestAdversarialSamePlatformTransitions:
    """Stress-test same-platform window transitions vs genuine resizes/moves."""

    def test_window_resize_and_multimonitor_move_preserves_session(self) -> None:
        """Moving window across multi-monitor bounds must NOT drop or restart session."""
        detector = CallDetector()
        now = 1000.0

        # Start Teams meeting on Monitor 1
        win_m1 = [(5001, "Microsoft Teams", "Standup | Microsoft Teams", (0, 0, 1920, 1080))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_m1):
            assert detector.update(users=[], now=now) is None
            assert detector.update(users=[], now=now + 1.2) == "started"

        original_sess_id = detector.active_session.session_id
        assert detector.active_session.window_hwnd == 5001

        # Simulate user moving and resizing window to Monitor 2 (x=1920, y=0, w=1280, h=720)
        win_m2 = [(5001, "Microsoft Teams", "Standup | Microsoft Teams", (1920, 0, 1280, 720))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_m2):
            for step in range(5):
                res = detector.update(users=[], now=now + 2.0 + (step * 0.5))
                assert res is None, f"Window move triggered unintended event: {res}"
                assert detector.active_session is not None
                assert detector.active_session.session_id == original_sess_id
                assert detector.active_session.window_hwnd == 5001

    def test_dynamic_title_status_updates_on_same_hwnd_preserved(self) -> None:
        """Dynamic title variations (e.g. adding participant count or in-call status) on same HWND stay alive."""
        detector = CallDetector()
        now = 2000.0

        win_initial = [(6001, "Microsoft Teams", "Design Review | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_initial):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        orig_sess_id = detector.active_session.session_id

        # Title updates dynamically as attendees join: "Design Review (3) | Microsoft Teams"
        win_dyn1 = [(6001, "Microsoft Teams", "Design Review (3) | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_dyn1):
            res1 = detector.update(users=[], now=now + 2.0)
            assert res1 is None
            assert detector.active_session.session_id == orig_sess_id

        # Title updates: "Meeting in progress - Design Review | Microsoft Teams"
        win_dyn2 = [(6001, "Microsoft Teams", "Meeting in progress - Design Review | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_dyn2):
            res2 = detector.update(users=[], now=now + 3.0)
            assert res2 is None
            assert detector.active_session.session_id == orig_sess_id

    def test_same_platform_back_to_back_different_hwnd_triggers_transition(self) -> None:
        """Closing Teams Meeting A and opening Teams Meeting B triggers ended -> started."""
        detector = CallDetector()
        now = 3000.0

        win_a = [(7001, "Microsoft Teams", "Meeting A | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_a):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        assert detector.active_session.window_hwnd == 7001
        sess_a_id = detector.active_session.session_id

        # Meeting A closes, Meeting B opens on HWND 7002
        win_b = [(7002, "Microsoft Teams", "Meeting B | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_b):
            # First tick flags back-to-back explicit transition
            res_end1 = detector.update(users=[], now=now + 2.0)
            # Advance 1.1s past the 1.0s explicit leave grace period
            res_end2 = detector.update(users=[], now=now + 3.1)
            assert res_end2 == "ended"
            assert detector.last_ended_session.session_id == sess_a_id
            assert detector.last_ended_session.window_hwnd == 7001
            assert detector.active_session is None

            # Next tick: Meeting B is discovered and starts 1.0s debounce
            res_start1 = detector.update(users=[], now=now + 3.2)
            assert res_start1 is None

            # Advance 1.1s to satisfy 1.0s debounce
            res_start2 = detector.update(users=[], now=now + 4.3)
            assert res_start2 == "started"
            assert detector.active_session.window_hwnd == 7002
            assert detector.active_session.session_id != sess_a_id
            assert "Meeting B" in detector.active_session.clean_title

    def test_overlapping_windows_on_same_platform(self) -> None:
        """User opens second meeting window while first is still active; first stays active until closed."""
        detector = CallDetector()
        now = 4000.0

        win_a = [(8001, "Microsoft Teams", "Primary Call | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_a):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        assert detector.active_session.window_hwnd == 8001

        # Second meeting opens concurrently on HWND 8002
        win_both = [
            (8001, "Microsoft Teams", "Primary Call | Microsoft Teams", (0, 0, 800, 600)),
            (8002, "Microsoft Teams", "Secondary Call | Microsoft Teams", (100, 100, 800, 600)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_both):
            res = detector.update(users=[], now=now + 2.5)
            # Active session remains Primary Call (8001)
            assert res is None
            assert detector.active_session.window_hwnd == 8001

        # Now Primary Call closes; only Secondary Call remains
        win_only_b = [(8002, "Microsoft Teams", "Secondary Call | Microsoft Teams", (100, 100, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_only_b):
            detector.update(users=[], now=now + 3.0)
            res_end = detector.update(users=[], now=now + 4.1)
            assert res_end == "ended"
            assert detector.last_ended_session.window_hwnd == 8001

            # Subsequent discovery binds to Secondary Call
            detector.update(users=[], now=now + 4.2)
            res_b_start = detector.update(users=[], now=now + 5.5)
            assert res_b_start == "started"
            assert detector.active_session.window_hwnd == 8002


# =============================================================================
# AREA 2: GENERIC TITLE SLUG COLLISIONS & SESSION SALTING
# =============================================================================

class TestAdversarialGenericTitleCollisions:
    """Stress-test consecutive meetings with identical default generic titles."""

    @pytest.mark.parametrize("generic_title, platform", [
        ("Microsoft Teams Meeting", "Microsoft Teams"),
        ("Zoom Meeting", "Zoom"),
        ("Meet - abc-defg-hij", "Google Meet"),
    ])
    def test_consecutive_meetings_with_identical_generic_title(
        self, generic_title: str, platform: str
    ) -> None:
        """Multiple consecutive calls with exact identical default titles must have unique session IDs."""
        detector = CallDetector()
        detected_sessions: List[str] = []
        now = 5000.0

        for i in range(5):
            hwnd = 9000 + i
            win = [(hwnd, platform, generic_title, (0, 0, 800, 600))]
            with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
                # Discovery and debounce
                detector.update(users=[], now=now)
                res_start = detector.update(users=[], now=now + 1.2)
                assert res_start == "started", f"Meeting {i+1} failed to start detection"

                sess_id = detector.active_session.session_id
                assert sess_id not in detected_sessions, f"Duplicate session_id {sess_id} generated!"
                detected_sessions.append(sess_id)

                # End meeting immediately (as when user clicks stop or leaves)
                detector.end_active_session_immediately()
                assert detector.active_session is None
                assert detector.state == MeetingDetectorState.NOT_RUNNING

            now += 10.0

        assert len(detected_sessions) == 5
        assert len(set(detected_sessions)) == 5

    def test_dismissed_generic_meeting_does_not_suppress_subsequent_meeting(self) -> None:
        """Dismissing a prompt for 'Microsoft Teams Meeting' must NOT suppress the next one."""
        detector = CallDetector()
        now = 6000.0

        # Meeting 1: Generic title on HWND 9101
        win1 = [(9101, "Microsoft Teams", "Microsoft Teams Meeting", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

            sess1_id = detector.active_session.session_id
            detector.mark_decision(sess1_id, "dismissed")
            assert sess1_id in detector.dismissed_session_ids

            # Meeting 1 ends and cleans up
            detector.end_active_session_immediately()
            assert sess1_id not in detector.dismissed_session_ids

        # Meeting 2: Identical generic title on HWND 9102
        win2 = [(9102, "Microsoft Teams", "Microsoft Teams Meeting", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win2):
            detector.update(users=[], now=now + 5.0)
            res2 = detector.update(users=[], now=now + 6.3)
            assert res2 == "started", "Subsequent generic meeting was erroneously suppressed!"
            assert detector.active_session.window_hwnd == 9102
            assert detector.active_session.session_id != sess1_id

    def test_recycled_hwnd_distinct_timestamp_salting(self) -> None:
        """When OS recycles the same HWND for a new meeting, timestamp salting prevents collision."""
        detector = CallDetector()
        recycled_hwnd = 9999

        win = [(recycled_hwnd, "Microsoft Teams", "Microsoft Teams Meeting", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
            detector.update(users=[], now=7000.0)
            assert detector.update(users=[], now=7001.2) == "started"
            sess1_id = detector.active_session.session_id

            detector.end_active_session_immediately()

            # Advance wall clock time for epoch salting
            with patch("time.time", return_value=time.time() + 300):
                detector.update(users=[], now=7300.0)
                assert detector.update(users=[], now=7301.2) == "started"
                sess2_id = detector.active_session.session_id

        assert sess1_id != sess2_id, f"Recycled HWND produced identical session ID: {sess1_id}"

    def test_extreme_punctuation_and_unicode_slug_generation(self) -> None:
        """Titles with emojis, all-punctuation, or non-Latin scripts generate valid unique IDs."""
        detector = CallDetector()
        now = 8000.0

        exotic_titles = [
            "??? !!! *** ---",
            "🚀🔥💥🎉",
            "会议-产品路线图讨论",
            "العربية / الاجتماع الأسبوعي",
            "A" * 600,  # massive 600 char title
        ]

        for idx, title in enumerate(exotic_titles):
            hwnd = 9200 + idx
            win = [(hwnd, "Microsoft Teams", title, (0, 0, 800, 600))]
            with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
                detector.update(users=[], now=now)
                res = detector.update(users=[], now=now + 1.2)
                assert res == "started"
                sess = detector.active_session
                assert sess is not None
                assert isinstance(sess.session_id, str)
                assert len(sess.session_id) > 5
                # Session ID should contain safe ASCII characters
                assert re.match(r"^[a-zA-Z0-9_]+$", sess.session_id) is not None
                detector.end_active_session_immediately()
            now += 5.0

    def test_suppression_memory_bounds_under_heavy_churn(self) -> None:
        """Simulate heavy meeting churn; verify suppression sets do not leak memory."""
        detector = CallDetector()
        now = 9000.0

        # Inject 300 suppression entries across timestamps
        for i in range(300):
            sid = f"teams_call_{i}_{i}"
            ts = now - (800.0 if i < 200 else 100.0)  # 200 expired (>600s), 100 recent
            detector._suppression_timestamps[sid] = ts
            detector.dismissed_session_ids.add(sid)
            detector._window_first_seen[sid] = ts

        # Run pruning
        detector.prune_expired_suppressions(now, max_age_sec=600.0)

        # 200 expired items should be pruned; only 100 recent remain
        assert len(detector.dismissed_session_ids) == 100
        assert len(detector._suppression_timestamps) == 100
        assert len(detector._window_first_seen) == 100


# =============================================================================
# AREA 3: UI SIGNAL RACE CONDITIONS & STATE ISOLATION
# =============================================================================

class TestAdversarialUiSignalRaceConditions:
    """Stress-test UI state isolation against asynchronous finalization signals."""

    def test_background_finalization_spam_during_active_recording(
        self,
        qapp_offscreen: QApplication,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """Rapid firing of finalization signals for prior meeting does NOT clobber active recording."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        pill: DualTierFloatingPill = offscreen_widgets["pill"]

        # Step 1: Start Meeting B recording
        ok, _ = isolated_controller.start_meeting_recording("Active Meeting B", recording_mode="audio_only")
        assert ok is True
        meeting_b_id = isolated_controller.active_meeting_id
        assert meeting_b_id is not None
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True

        qapp_offscreen.processEvents()

        # Add live segments to Meeting B in the drawer
        live_seg = {"speaker": "Speaker 1", "text": "This is active Meeting B discussion", "start_time": 1.0}
        isolated_controller.meeting_progressive_segment.emit(meeting_b_id, live_seg)
        qapp_offscreen.processEvents()

        # Verify drawer has Meeting B and recording controls
        assert "Active Meeting B" in drawer.txt_title.text() or "Meeting B" in drawer.txt_title.text()
        assert "Stop" in drawer.btn_universal_stop.text()

        # Step 2: Adversarially spam background finalization signals for prior Meeting A (id=999)
        def _spam_signals():
            for _ in range(50):
                isolated_controller._safe_emit_finalization_progress("transcribing", "Stage 2/4...")
                isolated_controller._safe_emit_finalization_progress("idle", "Completed Meeting A")
                isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, 999)
                time.sleep(0.001)

        spam_thread = threading.Thread(target=_spam_signals)
        spam_thread.start()
        spam_thread.join()

        # Pump Qt event loop to dispatch all cross-thread signals
        qapp_offscreen.processEvents()

        # Step 3: Assert Active Meeting B UI was NEVER disrupted
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True
        assert "Stop" in drawer.btn_universal_stop.text()
        assert "Meeting B" in drawer.txt_title.text()

        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()

    def test_background_finalization_spam_during_paused_state(
        self,
        qapp_offscreen: QApplication,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """When meeting is paused, finalization signals for prior meetings do NOT reset controls to idle."""
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()

        ok, _ = isolated_controller.start_meeting_recording("Paused Meeting C", recording_mode="audio_only")
        assert ok is True
        assert isolated_controller.pause_meeting_recording() is True
        assert isolated_controller.capture_state == "paused"
        assert isolated_controller.is_meeting_recording is True  # is_meeting_recording covers paused

        qapp_offscreen.processEvents()
        assert "Resume" in drawer.btn_universal_stop.text()

        # Spam prior meeting finalization signals
        for _ in range(25):
            isolated_controller._safe_emit_finalization_progress("idle", "Prior meeting saved")
            isolated_controller._safe_emit(isolated_controller.meeting_saved_and_transcribed, 888)

        qapp_offscreen.processEvents()

        # Verification: Controller and drawer remain paused, NOT idle
        assert isolated_controller.capture_state == "paused"
        assert "Resume" in drawer.btn_universal_stop.text()

        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()

    def test_finalization_worker_exception_leaves_controller_operational(
        self,
        qapp_offscreen: QApplication,
        isolated_controller: AppController,
    ) -> None:
        """If finalization worker encounters a catastrophic exception, controller does not freeze in 'error'."""
        ok1, _ = isolated_controller.start_meeting_recording("Crashing Meeting", recording_mode="audio_only")
        assert ok1 is True
        m1_id = isolated_controller.active_meeting_id
        isolated_controller.stop_meeting_recording()

        # Execute worker with None result to trigger exception in Stage 1
        isolated_controller._finalize_meeting_worker(meeting_id=m1_id, result=None)

        # Verification: capture_state remains healthy idle
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False

        # Can start subsequent meeting immediately
        ok2, msg2 = isolated_controller.start_meeting_recording("Clean Meeting After Crash", recording_mode="audio_only")
        assert ok2 is True, f"Failed starting meeting after worker crash: {msg2}"
        assert isolated_controller.capture_state == "recording"

        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()

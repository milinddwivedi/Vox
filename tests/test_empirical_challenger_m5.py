"""
Empirical Challenger Test Suite for Milestone 5 (Back-to-Back & Meeting Lifecycle).

Empirically challenges:
1. Rapid back-to-back meeting start/stop cycles (<100ms intervals, down to 0ms).
2. Starting a new meeting while a prior meeting is actively running through background Whisper transcription/finalization.
3. Audio device teardown failure simulation during active stop (hardware/COM crash).
4. Generic session ID collision resistance on identical titles across recycled HWNDs.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

pytest_plugins = ["tests.e2e.conftest"]

from voxlocal.core.controller import AppController
from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    clean_meeting_title,
    is_non_call_hub,
)


# ==============================================================================
# CHALLENGE 1: RAPID BACK-TO-BACK CYCLES (<100ms)
# ==============================================================================

class TestEmpiricalRapidBackToBackCycles:
    """Stress-test rapid transitions between meeting conclusion and subsequent meeting start."""

    @pytest.mark.parametrize("delay_sec", [0.0, 0.01, 0.025, 0.05, 0.095])
    def test_rapid_back_to_back_cycle_under_100ms(
        self, isolated_controller: AppController, delay_sec: float
    ) -> None:
        """Conclude Meeting 1 and immediately start Meeting 2 with delay < 100ms."""
        # 1. Start Meeting 1
        ok1, msg1 = isolated_controller.start_meeting_recording(
            f"Rapid Meeting 1 (gap={delay_sec*1000:.0f}ms)", recording_mode="audio_only"
        )
        assert ok1 is True, f"Failed to start Meeting 1: {msg1}"
        m1_id = isolated_controller.active_meeting_id
        assert m1_id is not None
        assert isolated_controller.capture_state == "recording"

        # 2. Stop Meeting 1
        t_stop = time.perf_counter()
        isolated_controller.stop_meeting_recording()
        stop_lat = time.perf_counter() - t_stop

        # Invariant: capture_state resets to idle in <300ms SLA
        assert stop_lat < 0.30, f"Teardown latency {stop_lat*1000:.1f}ms exceeded 300ms SLA"
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None
        assert isolated_controller.is_meeting_recording is False

        # 3. Inter-meeting delay (<100ms)
        if delay_sec > 0:
            time.sleep(delay_sec)

        # 4. Start Meeting 2
        ok2, msg2 = isolated_controller.start_meeting_recording(
            f"Rapid Meeting 2 (gap={delay_sec*1000:.0f}ms)", recording_mode="audio_only"
        )
        assert ok2 is True, f"Failed to start Meeting 2 after {delay_sec*1000:.0f}ms delay: {msg2}"
        m2_id = isolated_controller.active_meeting_id
        assert m2_id is not None
        assert m2_id != m1_id, "Meeting IDs must be distinct"
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True

        # Clean teardown
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"

    def test_burst_30_rapid_start_stop_cycles(
        self, isolated_controller: AppController
    ) -> None:
        """High-frequency burst of 30 rapid start/stop cycles with 0ms pause."""
        meeting_ids: List[int] = []
        teardown_latencies: List[float] = []

        for i in range(30):
            ok, msg = isolated_controller.start_meeting_recording(
                f"Burst Test #{i+1}", recording_mode="audio_only"
            )
            assert ok is True, f"Burst start failed at iteration {i+1}: {msg}"
            mid = isolated_controller.active_meeting_id
            assert mid is not None
            assert mid not in meeting_ids, f"Duplicate ID {mid} at iteration {i+1}"
            meeting_ids.append(mid)

            t0 = time.perf_counter()
            isolated_controller.stop_meeting_recording()
            dur = time.perf_counter() - t0
            teardown_latencies.append(dur)

            assert isolated_controller.capture_state == "idle"
            assert isolated_controller.active_meeting_id is None
            assert isolated_controller.is_meeting_recording is False

        assert len(meeting_ids) == 30
        assert len(set(meeting_ids)) == 30
        p95_lat = sorted(teardown_latencies)[int(len(teardown_latencies) * 0.95)]
        max_lat = max(teardown_latencies)
        assert max_lat < 0.30, f"Max teardown {max_lat*1000:.1f}ms exceeded 300ms SLA"
        print(f"\n[Burst 30 Latency]: p95={p95_lat*1000:.2f}ms, max={max_lat*1000:.2f}ms")


# ==============================================================================
# CHALLENGE 2: CONCURRENT BACKGROUND WHISPER FINALIZATION
# ==============================================================================

class TestEmpiricalConcurrentFinalization:
    """Stress-test starting a new meeting while prior meeting transcribes in background."""

    def test_concurrent_finalization_and_new_recording(
        self, isolated_controller: AppController, qapp_offscreen: QApplication
    ) -> None:
        """Meeting B records cleanly while Meeting A undergoes heavy background finalization."""
        stage_sim_gate = threading.Event()
        worker_finished = threading.Event()
        orig_worker = isolated_controller._finalize_meeting_worker

        def simulated_heavy_whisper_worker(meeting_id, result, video_path=""):
            # Hold Meeting A in Stage 2 background transcription
            if "Heavy Meeting A" in isolated_controller.db.get_meeting(meeting_id).get("title", ""):
                stage_sim_gate.wait(timeout=5.0)
            res = orig_worker(meeting_id, result, video_path)
            worker_finished.set()
            return res

        with patch.object(isolated_controller, "_finalize_meeting_worker", side_effect=simulated_heavy_whisper_worker):
            # 1. Start and stop Meeting A
            ok_a, _ = isolated_controller.start_meeting_recording("Heavy Meeting A", recording_mode="audio_only")
            assert ok_a is True
            id_a = isolated_controller.active_meeting_id
            isolated_controller.stop_meeting_recording()

            # Meeting A is placed in background finalization queue
            assert isolated_controller.capture_state == "idle"
            assert id_a in isolated_controller._finalizing_jobs

            # 2. While Meeting A is held in background worker, immediately start Meeting B
            ok_b, msg_b = isolated_controller.start_meeting_recording("Active Meeting B", recording_mode="audio_only")
            assert ok_b is True, f"Meeting B start rejected: {msg_b}"
            id_b = isolated_controller.active_meeting_id
            assert id_b != id_a

            # Invariant: Meeting B is actively recording while Meeting A is finalizing
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.active_meeting_id == id_b
            assert id_a in isolated_controller._finalizing_jobs

            # Add live segment to Meeting B
            seg_b = {"speaker": "Speaker 1", "text": "Discussion in Meeting B", "start_time": 0.5, "end_time": 2.0, "source_track": "system"}
            isolated_controller.meeting_progressive_segment.emit(id_b, seg_b)
            qapp_offscreen.processEvents()

            # 3. Release Meeting A background worker to complete
            stage_sim_gate.set()
            assert worker_finished.wait(timeout=5.0) is True

            # Wait for thread pool to process
            time.sleep(0.1)
            qapp_offscreen.processEvents()

            # Critical Invariants after Meeting A completes:
            # - Meeting A is removed from finalization queue
            # - Meeting A is completed in DB
            # - Meeting B is STILL actively recording! Its capture_state and active_id are UNTOUCHED!
            assert id_a not in isolated_controller._finalizing_jobs
            mtg_a = isolated_controller.db.get_meeting(id_a)
            assert mtg_a.get("status") == "completed"

            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.active_meeting_id == id_b
            assert isolated_controller.is_meeting_recording is True

            # 4. Stop Meeting B cleanly
            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"

    def test_cooperative_cancellation_preserves_recording_state(
        self, isolated_controller: AppController
    ) -> None:
        """Calling cancel_finalization aborts background worker without affecting active recording."""
        with isolated_controller._state_lock:
            isolated_controller._finalizing_jobs.add(8881)

        ok, _ = isolated_controller.start_meeting_recording("Active Meeting", recording_mode="audio_only")
        assert ok is True
        active_id = isolated_controller.active_meeting_id

        # Cancel background jobs
        isolated_controller.cancel_finalization()
        assert isolated_controller._cancel_finalization_event.is_set()

        # Invariant: Active recording is NOT cancelled or disrupted
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.active_meeting_id == active_id

        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"


# ==============================================================================
# CHALLENGE 3: AUDIO DEVICE TEARDOWN FAILURE RESILIENCE
# ==============================================================================

class TestEmpiricalAudioTeardownFailure:
    """Stress-test resilience when the audio device/driver fails during stop."""

    def test_stop_with_unhandled_audio_device_exception(
        self, isolated_controller: AppController
    ) -> None:
        """When meeting_recorder.stop() raises RuntimeError, controller recovers to idle cleanly."""
        ok, _ = isolated_controller.start_meeting_recording("Device Fail Test", recording_mode="audio_only")
        assert ok is True
        assert isolated_controller.capture_state == "recording"

        # Mock recorder.stop to simulate a catastrophic WASAPI device unplug / COM failure
        with patch.object(
            isolated_controller.meeting_recorder,
            "stop",
            side_effect=RuntimeError("0x88890004: AUDCLNT_E_DEVICE_INVALIDATED"),
        ):
            # Must NOT raise exception to caller
            isolated_controller.stop_meeting_recording()

        # Invariants:
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None
        assert isolated_controller.is_meeting_recording is False

        # Controller is healthy and can start a subsequent meeting immediately
        ok2, msg2 = isolated_controller.start_meeting_recording("Post-Fault Meeting", recording_mode="audio_only")
        assert ok2 is True, f"Failed to start subsequent meeting after audio fault: {msg2}"
        assert isolated_controller.capture_state == "recording"
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"

    def test_audio_recorder_returns_none_result(
        self, isolated_controller: AppController
    ) -> None:
        """When meeting_recorder.stop() returns None (e.g. 0-byte capture or driver failure), reset to idle."""
        ok, _ = isolated_controller.start_meeting_recording("Null Result Test", recording_mode="audio_only")
        assert ok is True

        with patch.object(isolated_controller.meeting_recorder, "stop", return_value=None):
            isolated_controller.stop_meeting_recording()

        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.active_meeting_id is None


# ==============================================================================
# CHALLENGE 4: GENERIC SESSION ID COLLISIONS & SALTING
# ==============================================================================

class TestEmpiricalGenericTitleCollisions:
    """Stress-test session ID uniqueness for generic default titles on repeated calls."""

    @pytest.mark.parametrize(
        "platform, raw_title",
        [
            ("Microsoft Teams", "Microsoft Teams Meeting"),
            ("Microsoft Teams", "Meeting"),
            ("Zoom", "Zoom Meeting"),
            ("Zoom", "Personal Meeting Room"),
            ("Google Meet", "Meet - abc-defg-hij"),
            ("Google Meet", "Meet - zyx-wvu-tsr - Google Chrome"),
        ],
    )
    def test_generic_title_salted_session_uniqueness(
        self, platform: str, raw_title: str
    ) -> None:
        """Repeated meetings with generic titles and recycled HWND generate unique salted session IDs."""
        detector = CallDetector()
        detected_session_ids: List[str] = []
        recycled_hwnd = 4444  # Simulate recycled OS HWND handle
        now = 1000.0

        for i in range(5):
            # Advance simulated epoch time so each session has a distinct second timestamp
            epoch_time = 1700000000 + (i * 30)
            mock_win = [(recycled_hwnd, platform, raw_title, (0, 0, 1024, 768))]

            with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_win), \
                 patch("time.time", return_value=float(epoch_time)):
                detector.update(users=[], now=now)
                res = detector.update(users=[], now=now + 1.2)

                assert res == "started", f"Call {i+1} failed to start for '{raw_title}'"
                assert detector.active_session is not None
                sess_id = detector.active_session.session_id

                # Salted ID invariant: must contain HWND and timestamp
                assert str(recycled_hwnd) in sess_id
                assert sess_id not in detected_session_ids, f"Collision detected: {sess_id}"
                detected_session_ids.append(sess_id)

                # End meeting immediately
                detector.end_active_session_immediately()
                assert detector.active_session is None
                assert detector.state == MeetingDetectorState.NOT_RUNNING

            now += 60.0

        assert len(detected_session_ids) == 5
        assert len(set(detected_session_ids)) == 5

    def test_dismissed_generic_meeting_allows_subsequent_call(self) -> None:
        """A user dismissing a generic title meeting does not permanently block subsequent calls."""
        detector = CallDetector()
        now = 2000.0
        generic_title = "Microsoft Teams Meeting"

        # Meeting 1 on HWND 1001
        win1 = [(1001, "Microsoft Teams", generic_title, (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            detector.update(users=[], now=now)
            res1 = detector.update(users=[], now=now + 1.2)
            assert res1 == "started"
            sess1 = detector.active_session.session_id

            # User clicks "Dismiss"
            detector.mark_decision(sess1, "dismissed")
            assert sess1 in detector.dismissed_session_ids

            # Meeting 1 ends
            detector.end_active_session_immediately()
            # Suppression pruning invariant: dismissed session is pruned on session end
            assert sess1 not in detector.dismissed_session_ids

        # Meeting 2 on HWND 1002 with exact same generic title
        win2 = [(1002, "Microsoft Teams", generic_title, (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win2):
            detector.update(users=[], now=now + 10.0)
            res2 = detector.update(users=[], now=now + 11.2)

            assert res2 == "started", "Subsequent meeting was blocked by previous dismissed meeting!"
            assert detector.active_session.window_hwnd == 1002
            assert detector.active_session.session_id != sess1

    def test_non_call_hub_windows_do_not_trigger_false_call(self) -> None:
        """Main platform launcher / hub windows are filtered out and do not trigger 'started'."""
        detector = CallDetector()
        hub_windows = [
            (901, "Microsoft Teams", "Chat | Microsoft Teams", (0, 0, 1024, 768)),
            (902, "Microsoft Teams", "Calendar | Microsoft Teams", (0, 0, 1024, 768)),
            (903, "Microsoft Teams", "Microsoft Teams", (0, 0, 1024, 768)),
            (904, "Zoom", "Zoom Workplace", (0, 0, 1024, 768)),
            (905, "Google Meet", "Google Meet", (0, 0, 1024, 768)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=hub_windows):
            res1 = detector.update(users=[], now=100.0)
            res2 = detector.update(users=[], now=102.0)
            assert res1 is None
            assert res2 is None
            assert detector.active_session is None
            assert detector.state == MeetingDetectorState.NOT_RUNNING

"""Challenger 1 Empirical Stress Test Suite: Rapid Transitions, Concurrency, and Teardown Latency.

Milestone 5 Focus:
1. Concurrency & Rapid Burst: Test rapid consecutive starts/stops with gaps of 0ms, 50ms, 100ms, 500ms, 2s.
2. Background Processing Concurrency: Verify that starting Meeting B while Meeting A is finalizing
   in the background runs both pipelines concurrently without race conditions, corruption of
   `_active_meeting_id`, or audio capture failure.
3. Triple Pipeline Concurrency: Meeting A and B finalizing while Meeting C actively records.
4. Finalization Exception Isolation: Meeting A failing in background does not disrupt Meeting B.
5. Teardown Latency Benchmark: Verify `stop_meeting_recording` resets `_capture_state` to 'idle' in <300ms.
6. Multi-threaded Race Stress: Concurrent start/stop calls from multiple threads without deadlock.
"""

from __future__ import annotations

import gc
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, MeetingLifecycleState


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_controller(qapp: QApplication, tmp_path: Path):
    """Create an isolated AppController with isolated directories and mock transcriber."""
    storage_dir = tmp_path / "data"
    storage_dir.mkdir(parents=True, exist_ok=True)
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    config = AppConfig(
        storage_dir=str(storage_dir),
        models_dir=str(tmp_path / "models"),
        auto_record_calls=False,
        record_meeting_screen=False,
    )
    config.disable_model_preload = True
    config.disable_hotkeys = True

    ctrl = AppController(config=config)
    ctrl.transcriber.load_model = MagicMock(return_value=True)
    ctrl.transcriber.is_model_loaded = MagicMock(return_value=True)
    ctrl.transcriber.transcribe_file = MagicMock(return_value=[])
    ctrl.transcriber.transcribe_meeting_dual_track = MagicMock(return_value=[])

    yield ctrl

    try:
        ctrl.shutdown()
    except Exception:
        pass
    qapp.processEvents()


# ==============================================================================
# 1. RAPID CONSECUTIVE TRANSITIONS (0ms, 50ms, 100ms, 500ms, 2s) & BURST
# ==============================================================================
class TestRapidConsecutiveTransitions:
    """Empirical verification of consecutive starts/stops with varying gaps."""

    @pytest.mark.parametrize("gap_sec", [0.0, 0.05, 0.1, 0.5, 2.0])
    def test_rapid_consecutive_start_stop_gaps(
        self, isolated_controller: AppController, gap_sec: float
    ) -> None:
        """Verify back-to-back meeting start/stop across 0ms, 50ms, 100ms, 500ms, and 2s gaps."""
        # 1. Start Meeting A
        ok_a, msg_a = isolated_controller.start_meeting_recording(
            title=f"Meeting A (gap={gap_sec}s)", recording_mode="audio_only"
        )
        assert ok_a is True, f"Failed to start Meeting A: {msg_a}"
        id_a = isolated_controller.active_meeting_id
        assert id_a is not None
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True

        # 2. Stop Meeting A
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False
        assert isolated_controller.active_meeting_id is None

        # 3. Inter-meeting gap
        if gap_sec > 0:
            time.sleep(gap_sec)

        # 4. Start Meeting B
        ok_b, msg_b = isolated_controller.start_meeting_recording(
            title=f"Meeting B (gap={gap_sec}s)", recording_mode="audio_only"
        )
        assert ok_b is True, f"Failed to start Meeting B after {gap_sec}s gap: {msg_b}"
        id_b = isolated_controller.active_meeting_id
        assert id_b is not None
        assert id_b != id_a, f"Meeting IDs must be distinct ({id_a} vs {id_b})"
        assert isolated_controller.capture_state == "recording"
        assert isolated_controller.is_meeting_recording is True

        # 5. Stop Meeting B
        isolated_controller.stop_meeting_recording()
        assert isolated_controller.capture_state == "idle"
        assert isolated_controller.is_meeting_recording is False

    def test_rapid_burst_25_consecutive_meetings_zero_gap(
        self, isolated_controller: AppController
    ) -> None:
        """Stress test: 25 rapid consecutive start/stop cycles with 0ms gap."""
        created_ids = []
        for i in range(25):
            ok, msg = isolated_controller.start_meeting_recording(
                title=f"Burst Call #{i+1}", recording_mode="audio_only"
            )
            assert ok is True, f"Cycle {i+1} failed to start: {msg}"
            mid = isolated_controller.active_meeting_id
            assert mid is not None
            assert mid not in created_ids, f"Duplicate meeting ID {mid} on cycle {i+1}"
            created_ids.append(mid)
            assert isolated_controller.capture_state == "recording"

            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"
            assert isolated_controller.active_meeting_id is None

        assert len(created_ids) == 25
        assert len(set(created_ids)) == 25, "All 25 meeting IDs must be strictly unique"


# ==============================================================================
# 2. BACKGROUND PROCESSING CONCURRENCY
# ==============================================================================
class TestBackgroundProcessingConcurrency:
    """Verify concurrent execution of background finalization and new meeting recording."""

    def test_meeting_b_starts_while_meeting_a_finalizes(
        self, isolated_controller: AppController, qapp: QApplication
    ) -> None:
        """Starting Meeting B while Meeting A is finalizing runs both concurrently without corruption."""
        stage1_barrier = threading.Event()
        proceed_barrier = threading.Event()

        # Wrap _finalize_meeting_worker to hold Meeting A in Stage 1
        orig_worker = isolated_controller._finalize_meeting_worker

        def controlled_worker(meeting_id, result, video_path=""):
            if "Meeting Alpha" in isolated_controller.db.get_meeting(meeting_id).get("title", ""):
                stage1_barrier.set()
                # Simulate heavy offline Whisper transcription running in background
                proceed_barrier.wait(timeout=5.0)
            return orig_worker(meeting_id, result, video_path)

        with patch.object(isolated_controller, "_finalize_meeting_worker", side_effect=controlled_worker):
            # 1. Start and record Meeting A
            ok_a, _ = isolated_controller.start_meeting_recording(
                title="Meeting Alpha", recording_mode="audio_only"
            )
            assert ok_a is True
            id_a = isolated_controller.active_meeting_id
            assert id_a is not None

            # Stop Meeting A -> triggers background worker
            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"

            # Wait until Meeting A's worker thread is actively running in background
            assert stage1_barrier.wait(timeout=3.0) is True
            assert id_a in isolated_controller._finalizing_jobs

            # 2. While Meeting A is actively finalizing in background, start Meeting B!
            ok_b, msg_b = isolated_controller.start_meeting_recording(
                title="Meeting Beta", recording_mode="audio_only"
            )
            assert ok_b is True, f"Meeting B rejected during Meeting A finalization: {msg_b}"
            id_b = isolated_controller.active_meeting_id
            assert id_b is not None
            assert id_b != id_a, "Meeting B ID must not equal Meeting A ID"
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.is_meeting_recording is True

            # Invariant: Meeting A is still finalizing, Meeting B is recording
            assert id_a in isolated_controller._finalizing_jobs
            assert isolated_controller.active_meeting_id == id_b

            # 3. Allow Meeting A's background worker to finish while Meeting B is recording
            proceed_barrier.set()
            time.sleep(0.3)
            qapp.processEvents()

            # Verify Meeting A finished in database
            mtg_a = isolated_controller.db.get_meeting(id_a)
            assert mtg_a is not None
            assert mtg_a.get("status") == "completed"

            # CRITICAL CONCURRENCY INVARIANTS:
            # Meeting A finishing MUST NOT corrupt Meeting B's active recording state!
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.is_meeting_recording is True
            assert isolated_controller.active_meeting_id == id_b
            assert id_a not in isolated_controller._finalizing_jobs

            # 4. Stop Meeting B cleanly
            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"
            assert isolated_controller.active_meeting_id is None

            # Wait for Meeting B to finalize
            time.sleep(0.3)
            qapp.processEvents()
            mtg_b = isolated_controller.db.get_meeting(id_b)
            assert mtg_b is not None
            assert mtg_b.get("status") == "completed"

    def test_triple_concurrent_pipeline_two_finalizing_one_recording(
        self, isolated_controller: AppController, qapp: QApplication
    ) -> None:
        """Two meetings finalizing simultaneously while a third is actively recording."""
        hold_jobs = threading.Event()
        orig_worker = isolated_controller._finalize_meeting_worker

        def holding_worker(meeting_id, result, video_path=""):
            hold_jobs.wait(timeout=5.0)
            return orig_worker(meeting_id, result, video_path)

        with patch.object(isolated_controller, "_finalize_meeting_worker", side_effect=holding_worker):
            # Meeting 1
            ok1, _ = isolated_controller.start_meeting_recording("Job 1", recording_mode="audio_only")
            assert ok1 is True
            id1 = isolated_controller.active_meeting_id
            isolated_controller.stop_meeting_recording()

            # Meeting 2
            ok2, _ = isolated_controller.start_meeting_recording("Job 2", recording_mode="audio_only")
            assert ok2 is True
            id2 = isolated_controller.active_meeting_id
            isolated_controller.stop_meeting_recording()

            # Meeting 3 (actively recording)
            ok3, _ = isolated_controller.start_meeting_recording("Job 3 Active", recording_mode="audio_only")
            assert ok3 is True
            id3 = isolated_controller.active_meeting_id

            # Invariants: 1 and 2 finalizing, 3 recording
            assert id1 in isolated_controller._finalizing_jobs
            assert id2 in isolated_controller._finalizing_jobs
            assert id3 not in isolated_controller._finalizing_jobs
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.active_meeting_id == id3

            # Release workers
            hold_jobs.set()
            time.sleep(0.4)
            qapp.processEvents()

            # Verify active capture is still Job 3
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.active_meeting_id == id3

            isolated_controller.stop_meeting_recording()
            time.sleep(0.3)
            qapp.processEvents()

    def test_background_worker_exception_does_not_corrupt_active_recording(
        self, isolated_controller: AppController, qapp: QApplication
    ) -> None:
        """A failure in Meeting A's finalization worker marks A as error, but leaves B recording."""
        fail_barrier = threading.Event()

        def failing_worker(meeting_id, result, video_path=""):
            title = isolated_controller.db.get_meeting(meeting_id).get("title", "")
            if "Doomed" in title:
                fail_barrier.wait(timeout=3.0)
                raise RuntimeError("Simulated Whisper CUDA out-of-memory error")
            return

        with patch.object(isolated_controller, "_finalize_meeting_worker", side_effect=failing_worker):
            # Start and stop doomed meeting
            ok_a, _ = isolated_controller.start_meeting_recording("Doomed Meeting", recording_mode="audio_only")
            assert ok_a is True
            id_a = isolated_controller.active_meeting_id
            isolated_controller.stop_meeting_recording()

            # Start active Meeting B
            ok_b, _ = isolated_controller.start_meeting_recording("Safe Meeting", recording_mode="audio_only")
            assert ok_b is True
            id_b = isolated_controller.active_meeting_id

            # Trigger failure in Doomed Meeting
            fail_barrier.set()
            time.sleep(0.3)
            qapp.processEvents()

            # Verify Meeting A marked as error in DB
            mtg_a = isolated_controller.db.get_meeting(id_a)
            assert mtg_a.get("status") == "error"

            # Invariant: Meeting B is STILL actively recording!
            assert isolated_controller.capture_state == "recording"
            assert isolated_controller.active_meeting_id == id_b
            assert isolated_controller.is_meeting_recording is True

            isolated_controller.stop_meeting_recording()
            assert isolated_controller.capture_state == "idle"


# ==============================================================================
# 3. TEARDOWN LATENCY BENCHMARK (<300ms)
# ==============================================================================
class TestTeardownLatencyBenchmark:
    """Benchmark the synchronous reset latency of stop_meeting_recording (<300ms SLA)."""

    def test_synchronous_teardown_latency_under_300ms(
        self, isolated_controller: AppController
    ) -> None:
        """Benchmark 20 consecutive stop_meeting_recording calls; all must complete in <300ms."""
        latencies_ms: List[float] = []

        for i in range(20):
            ok, _ = isolated_controller.start_meeting_recording(
                f"Latency Probe #{i+1}", recording_mode="audio_only"
            )
            assert ok is True
            assert isolated_controller.capture_state == "recording"

            t0 = time.perf_counter()
            isolated_controller.stop_meeting_recording()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert isolated_controller.capture_state == "idle"
            assert isolated_controller.active_meeting_id is None
            latencies_ms.append(elapsed_ms)
            assert elapsed_ms < 300.0, f"Iteration {i+1} reset latency {elapsed_ms:.2f}ms exceeded 300ms SLA"

        mean_lat = statistics.mean(latencies_ms)
        p95_lat = statistics.quantiles(latencies_ms, n=20)[18] if len(latencies_ms) >= 20 else max(latencies_ms)
        min_lat = min(latencies_ms)
        max_lat = max(latencies_ms)

        print(f"\n[BENCHMARK] Teardown Latency over 20 runs: min={min_lat:.2f}ms, mean={mean_lat:.2f}ms, p95={p95_lat:.2f}ms, max={max_lat:.2f}ms")
        assert max_lat < 300.0, f"Max teardown latency {max_lat:.2f}ms must be strictly < 300ms"
        assert p95_lat < 100.0, f"p95 teardown latency {p95_lat:.2f}ms must be < 100ms"

    def test_call_detector_reset_latency(self, isolated_controller: AppController) -> None:
        """CallDetector.end_active_session_immediately() synchronous latency benchmark."""
        detector = isolated_controller.call_detector
        detector.active_session = MagicMock()
        detector.active = MagicMock()

        t0 = time.perf_counter()
        detector.end_active_session_immediately()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert detector.active_session is None
        assert detector.active is None
        assert elapsed_ms < 10.0, f"Detector reset latency {elapsed_ms:.2f}ms exceeded 10ms"


# ==============================================================================
# 4. MULTI-THREADED CONCURRENCY & RACE CONDITIONS
# ==============================================================================
class TestMultiThreadedRaceStress:
    """Stress test the controller against concurrent multi-threaded operations."""

    def test_concurrent_start_and_stop_from_threads(
        self, isolated_controller: AppController, qapp: QApplication
    ) -> None:
        """Concurrent threads issuing start and stop requests do not deadlock or violate invariants."""
        errors: List[Exception] = []
        barrier = threading.Barrier(4)

        def worker_start(tid: int):
            try:
                barrier.wait(timeout=5.0)
                for i in range(10):
                    isolated_controller.start_meeting_recording(f"Thread {tid} #{i}", recording_mode="audio_only")
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        def worker_stop(tid: int):
            try:
                barrier.wait(timeout=5.0)
                for _ in range(10):
                    isolated_controller.stop_meeting_recording()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_start, args=(1,))
        t2 = threading.Thread(target=worker_start, args=(2,))
        t3 = threading.Thread(target=worker_stop, args=(3,))
        t4 = threading.Thread(target=worker_stop, args=(4,))

        threads = [t1, t2, t3, t4]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "Worker thread deadlocked during concurrent start/stop stress"

        assert len(errors) == 0, f"Thread errors encountered: {errors}"
        # Invariant: Capture state must be valid (either idle or recording)
        assert isolated_controller.capture_state in ("idle", "recording")
        if isolated_controller.capture_state == "idle":
            assert isolated_controller.active_meeting_id is None
        else:
            assert isolated_controller.active_meeting_id is not None
            isolated_controller.stop_meeting_recording()

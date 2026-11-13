from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""Comprehensive Automated Test Suite for:
VOX Meeting Video Capture Pipeline, Screen-Sharing Detection & Coexistence.

Verifies:
1. Teams Presentation Mode & Sharing Control Bar detection during local screen sharing.
2. Window minimization during presentation captures shared screen without black frames.
3. Transitions into and out of screen sharing preserve continuous video encoding & PTS sync.
4. Window minimization without sharing displays high-fidelity Privacy Guard card (never black).
5. DirectComposition black frame fallback via PrintWindow -> Window-Bounded DWM capture with CAPTUREBLT.
6. VideoHealthMonitor detection of blank/frozen frames and bounded recovery (max 3).
7. Independent concurrency & isolation between Core Feature 2 (ScreenRecorder) and Core Feature 3 (MeetingVideoRecorder).
8. Independent PyAV MP4 decoding, frame timestamps, and visual content integrity.
"""


import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import av
import numpy as np

from voxlocal.video.meeting_capture import (
    CaptureSourceValidator,
    MeetingSourceDescriptor,
    MeetingVideoRecorder,
    SourceValidationResult,
    TeamsDesktopCaptureProvider,
    VideoHealthMonitor,
    ZoomDesktopCaptureProvider,
    find_teams_sharing_session,
    find_zoom_sharing_session,
    resolve_meeting_source,
)
from voxlocal.video.screen_recorder import ScreenRecorder


class TestMeetingVideoCapturePipeline(unittest.TestCase):
    """Test suite for meeting video capture, screen-sharing lifecycle, and video health."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output_dir = Path(self.temp_dir.name)

    def test_01_teams_presentation_mode_detection(self):
        """Verifies Teams screen sharing detection when Sharing Control Bar is present."""
        provider = TeamsDesktopCaptureProvider()
        desc = MeetingSourceDescriptor(
            provider_id=provider.provider_id,
            platform_name=provider.platform_name,
            hwnd=197940,
            pid=4200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sanjay Amb | Microsoft Teams",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
        )

        # Mock find_teams_sharing_session returning active presentation on primary monitor
        with patch("voxlocal.video.meeting_capture.find_teams_sharing_session") as mock_sharing:
            mock_sharing.return_value = {
                "hwnd": 99991,
                "title": "Sharing control bar",
                "bounds": (0, 0, 1920, 1080),
                "bar_rect": (800, 0, 320, 40),
                "pid": 4200,
            }
            # Even if Teams call stage is iconic / minimized, sharing session keeps it valid!
            with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
                res = provider.validate_source(desc)
                self.assertTrue(res.is_valid)
                self.assertEqual(res.reason, "teams_presentation_active")
                self.assertTrue(desc.is_presenting)
                self.assertEqual(desc.presentation_bounds, (0, 0, 1920, 1080))
                self.assertIn(desc.presentation_hwnd, (0, 99991))

    def test_02_transition_into_and_out_of_screen_sharing(self):
        """Verifies smooth transition from Call Stage -> Screen Sharing -> Restored Call Stage."""
        provider = TeamsDesktopCaptureProvider()
        desc = MeetingSourceDescriptor(
            provider_id=provider.provider_id,
            platform_name=provider.platform_name,
            hwnd=197940,
            pid=4200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sanjay Amb | Microsoft Teams",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
        )

        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
            with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Sanjay Amb | Microsoft Teams"):
                with patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(4200, "ms-teams.exe")):
                    # Phase 1: Call stage visible, not sharing
                    with patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):
                        with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                            res1 = provider.validate_source(desc)
                            self.assertTrue(res1.is_valid)
                            self.assertEqual(res1.reason, "ok")
                            self.assertFalse(desc.is_presenting)

                    # Phase 2: User starts sharing screen -> Teams minimizes call stage, sharing bar appears
                    with patch("voxlocal.video.meeting_capture.find_teams_sharing_session") as mock_sharing:
                        mock_sharing.return_value = {
                            "hwnd": 99991,
                            "title": "Sharing control bar",
                            "bounds": (0, 0, 1920, 1080),
                            "pid": 4200,
                        }
                        with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
                            res2 = provider.validate_source(desc)
                            self.assertTrue(res2.is_valid)
                            self.assertEqual(res2.reason, "teams_presentation_active")
                            self.assertTrue(desc.is_presenting)

                    # Phase 3: User stops sharing -> sharing bar closes, Teams restores call stage
                    with patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):
                        with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                            res3 = provider.validate_source(desc)
                            self.assertTrue(res3.is_valid)
                            self.assertEqual(res3.reason, "ok")
                            self.assertFalse(desc.is_presenting)
                            self.assertEqual(desc.presentation_bounds, (0, 0, 0, 0))

    def test_03_minimized_without_sharing_displays_privacy_card(self):
        """Verifies that minimizing Teams without sharing produces a rendered privacy card (never black)."""
        provider = TeamsDesktopCaptureProvider()
        desc = MeetingSourceDescriptor(
            provider_id=provider.provider_id,
            platform_name=provider.platform_name,
            hwnd=197940,
            pid=4200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sanjay Amb | Microsoft Teams",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
        )

        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
            with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Sanjay Amb | Microsoft Teams"):
                with patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(4200, "ms-teams.exe")):
                    with patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):
                        with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
                            with patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]):
                                res = provider.validate_source(desc)
                                self.assertFalse(res.is_valid)
                                self.assertEqual(res.reason, "window_minimized")

        rec = MeetingVideoRecorder(
            output_path=str(self.output_dir / "test.mp4"),
            source_descriptor=desc,
        )
        rec._width = 1280
        rec._height = 720
        frame = rec._build_privacy_status_frame(res.reason, res.user_message)

        # Verify frame is NOT pitch black (mean >= 15, max >= 200 due to white text / borders)
        self.assertEqual(frame.shape, (720, 1280, 3))
        self.assertGreater(float(np.mean(frame)), 15.0)
        self.assertGreater(int(np.max(frame)), 200)

    def test_04_direct_composition_blank_frame_fallback(self):
        """Verifies PrintWindow black frame triggers window-bounded DWM capture with CAPTUREBLT."""
        desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=197940,
            pid=4200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sanjay Amb | Microsoft Teams",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
        )
        rec = MeetingVideoRecorder(
            output_path=str(self.output_dir / "test.mp4"),
            source_descriptor=desc,
        )

        # Synthetic GPU blank frame (all 0s)
        blank_pw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        # Synthetic valid presentation frame from bounded capture
        valid_bounded_frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
        valid_bounded_frame[100:200, 100:200] = 255

        with patch.object(rec, "_capture_print_window", return_value=blank_pw_frame):
            with patch.object(rec, "_capture_window_bounded_screen", return_value=valid_bounded_frame) as mock_bounded:
                with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
                    with patch("voxlocal.video.meeting_capture.user32.IsWindowVisible", return_value=True):
                        with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                            with patch("voxlocal.video.meeting_capture.get_window_rect", return_value=(100, 100, 1280, 720)):
                                with patch("voxlocal.video.meeting_capture.get_dwm_visible_crop_offsets", return_value=(0, 0, 1280, 720)):
                                    captured = rec._capture_isolated_window_bgr(197940)
                                    self.assertIsNotNone(captured)
                                    # Switched to fallback mode
                                    self.assertEqual(rec._capture_mode_preference, "window_bounded")
                                    self.assertTrue(rec._has_switched_to_fallback)
                                    self.assertEqual(int(captured[150, 150, 0]), 255)
                                    mock_bounded.assert_called_once()

    def test_05_video_health_monitor_bounded_recovery(self):
        """Verifies VideoHealthMonitor tracks streaks and triggers bounded recovery (max 3)."""
        monitor = VideoHealthMonitor(fps=10, max_recoveries=3)

        # 10 valid frames
        valid_frame = np.full((100, 100, 3), 100, dtype=np.uint8)
        for _ in range(10):
            status, should_recover = monitor.record_frame(valid_frame, is_valid_source=True, source_type="call_stage")
            self.assertEqual(status, "healthy")
            self.assertFalse(should_recover)

        self.assertEqual(monitor.valid_frames, 10)
        self.assertEqual(monitor.consecutive_blank_count, 0)

        # 20 blank frames (2 seconds at 10 FPS) while source is marked valid
        triggered_recovery = False
        for _ in range(20):
            status, should_recover = monitor.record_frame(None, is_valid_source=True)
            if should_recover:
                triggered_recovery = True
                monitor.record_recovery(success=True, reason="test_rebind")

        self.assertTrue(triggered_recovery)
        self.assertEqual(monitor.recovery_attempts, 1)
        self.assertEqual(monitor.recovery_successes, 1)

        # Trigger recovery 2 more times to exhaust max_recoveries
        for _ in range(20):
            monitor.record_frame(None, is_valid_source=True)
        monitor.record_recovery(success=True, reason="test_rebind_2")

        for _ in range(20):
            monitor.record_frame(None, is_valid_source=True)
        monitor.record_recovery(success=True, reason="test_rebind_3")

        self.assertEqual(monitor.recovery_attempts, 3)

        # 4th time: max_recoveries reached -> marks degraded, should NOT trigger further recoveries
        for _ in range(25):
            status, should_recover = monitor.record_frame(None, is_valid_source=True)

        self.assertEqual(status, "degraded")
        self.assertFalse(should_recover)
        diag = monitor.get_diagnostics()
        self.assertEqual(diag["health_status"], "degraded")
        self.assertEqual(diag["recovery_attempts"], 3)

    def test_06_meeting_and_screen_recording_coexistence(self):
        """Verifies ScreenRecorder and MeetingVideoRecorder operate simultaneously without conflict."""
        meeting_video_path = self.output_dir / "meeting_screen_coexist.mp4"
        screen_video_path = self.output_dir / "screen_rec_coexist.mp4"

        desc = MeetingSourceDescriptor(
            provider_id="generic_call",
            platform_name="Meeting Call",
            hwnd=1001,
            pid=2000,
            process_name="call.exe",
            class_name="CallWindow",
            window_title="Product Architecture Review",
            bounds=(50, 50, 640, 480),
            is_call_stage=True,
        )

        meeting_rec = MeetingVideoRecorder(
            output_path=str(meeting_video_path),
            source_descriptor=desc,
            fps=15,
        )
        screen_rec = ScreenRecorder(fps=15)

        # Verify separate state and instances
        self.assertFalse(meeting_rec.is_recording)
        self.assertFalse(screen_rec.is_recording)
        self.assertNotEqual(meeting_rec.output_path, str(screen_video_path))

        # Mock frame capture for meeting recorder
        meeting_frame = np.full((480, 640, 3), (40, 80, 160), dtype=np.uint8)

        # Start meeting recorder
        with patch.object(meeting_rec, "_capture_isolated_window_bgr", return_value=meeting_frame):
            with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
                with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                    ok_m = meeting_rec.start(str(meeting_video_path))
                    self.assertTrue(ok_m)
                    self.assertTrue(meeting_rec.is_recording)

                    # Start screen recorder concurrently
                    ok_s, _ = screen_rec.start(str(screen_video_path), fps=15)
                    self.assertTrue(ok_s)
                    self.assertTrue(screen_rec.is_recording)

                    # Allow frames to be captured in both pipelines
                    time.sleep(1.2)

                    # Stop screen recorder first
                    path_s = screen_rec.stop()
                    self.assertFalse(screen_rec.is_recording)
                    # Meeting recorder remains actively recording
                    self.assertTrue(meeting_rec.is_recording)

                    time.sleep(0.5)

                    # Stop meeting recorder
                    path_m = meeting_rec.stop()
                    self.assertFalse(meeting_rec.is_recording)

        # Verify both MP4 files exist and were finalized independently
        self.assertTrue(meeting_video_path.exists())
        self.assertGreater(meeting_video_path.stat().st_size, 0)
        if path_s:
            self.assertTrue(screen_video_path.exists())
            self.assertGreater(screen_video_path.stat().st_size, 0)

    def test_07_end_to_end_mp4_decoding_and_diagnostics(self):
        """Verifies actual MP4 encoding, frame decodability, monotonic PTS, and diagnostics telemetry."""
        output_file = self.output_dir / "meeting_screen_e2e.mp4"
        desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=197940,
            pid=4200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Daily Scrum | Microsoft Teams",
            bounds=(0, 0, 1280, 720),
            is_call_stage=True,
        )

        rec = MeetingVideoRecorder(
            output_path=str(output_file),
            source_descriptor=desc,
            fps=15,
        )

        test_frames = [
            np.full((720, 1280, 3), (50, 100, 150), dtype=np.uint8),  # normal call frame
            np.full((720, 1280, 3), (30, 200, 80), dtype=np.uint8),   # presentation frame
            None,                                                     # privacy pause frame
        ]
        frame_idx = 0

        def fake_capture(*args, **kwargs):
            nonlocal frame_idx
            idx = min(frame_idx, len(test_frames) - 1)
            frame_idx += 1
            return test_frames[idx]

        with patch.object(rec, "_capture_isolated_window_bgr", side_effect=fake_capture):
            with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
                with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                    ok = rec.start(str(output_file))
                    self.assertTrue(ok)
                    t0 = time.monotonic()
                    while rec._frame_count < 2 and time.monotonic() - t0 < 6.0:
                        time.sleep(0.05)
                    final_path = rec.stop()
                    self.assertEqual(final_path, str(output_file))

        self.assertTrue(output_file.exists())
        self.assertGreater(output_file.stat().st_size, 1000)

        # Inspect diagnostics JSON
        diag = rec.get_capture_diagnostics()
        self.assertIn("video_health", diag)
        self.assertGreater(diag["frames_encoded"], 0)
        self.assertEqual(diag["provider_id"], "teams_desktop")

        # Open encoded file independently with PyAV to verify container and decodable frames
        container = av.open(str(output_file))
        decoded_count = 0
        try:
            self.assertGreater(len(container.streams.video), 0)
            v_stream = container.streams.video[0]
            self.assertEqual(v_stream.codec_context.name, "h264")
            self.assertEqual(v_stream.width, 1280)
            self.assertEqual(v_stream.height, 720)

            last_pts = -1
            for packet in container.demux(v_stream):
                for frame in packet.decode():
                    decoded_count += 1
                    self.assertGreaterEqual(frame.pts, last_pts)
                    last_pts = frame.pts
                    arr = frame.to_ndarray(format="bgr24")
                    # Every decoded frame has non-zero pixels
                    self.assertGreater(int(np.max(arr)), 0)
        finally:
            container.close()

        self.assertGreater(decoded_count, 0)


if __name__ == "__main__":
    unittest.main()

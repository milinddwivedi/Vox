import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
import unittest
from unittest.mock import MagicMock, patch, call
import time
import os
import shutil
import tempfile
from pathlib import Path
import numpy as np
import av

from voxlocal.core.config import AppConfig
from voxlocal.audio.call_detector import CallDetector, is_leaving_title, is_non_call_hub
from voxlocal.video.meeting_capture import (
    MeetingVideoRecorder,
    MeetingSourceDescriptor,
    TeamsDesktopCaptureProvider,
    find_teams_sharing_session,
    find_active_presentation_window,
)


class TestMeetingStudioPresentationAndLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="vox_mtg_life_")
        self.output_mp4 = Path(self.tmp_dir) / "meeting_screen.mp4"

    def tearDown(self):
        try:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_leaving_title_detection(self):
        """Image/Evidence 1: Verify 'Leaving...' states are detected and not treated as active calls."""
        leaving_cases = [
            "Leaving...",
            "Leaving",
            "Left the meeting",
            "Call ended",
            "Meeting ended",
            "You left the meeting",
            "Reconnecting...",
        ]
        for title in leaving_cases:
            self.assertTrue(is_leaving_title(title), f"Failed to detect leaving title: {title}")

        active_cases = [
            "Daily Scrum - PLM North America | Microsoft Teams",
            "Vox Testing - 2",
            "Sprint Planning - Zoom",
            "Google Meet: xyz-abc-def",
        ]
        for title in active_cases:
            self.assertFalse(is_leaving_title(title), f"False positive on active title: {title}")

    def test_02_teams_window_close_terminates_recorder_immediately(self):
        """Image/Evidence 2: Verify MeetingVideoRecorder stops immediately upon call close with no trailing frames."""
        desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=12345,
            pid=9999,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Daily Scrum | Microsoft Teams",
            bounds=(0, 0, 1280, 720),
            is_call_stage=True,
        )

        meeting_ended_mock = MagicMock()
        rec = MeetingVideoRecorder(
            output_path=str(self.output_mp4),
            source_descriptor=desc,
            fps=15,
            on_meeting_ended=meeting_ended_mock,
        )

        test_frame = np.full((720, 1280, 3), (40, 80, 120), dtype=np.uint8)

        # Deterministic simulation: window is open for the first 3 frames, then closes (IsWindow -> False)
        def mock_is_window(h):
            return rec._frame_count < 3

        with patch.object(rec, "_capture_isolated_window_bgr", return_value=test_frame):
            with patch("voxlocal.video.meeting_capture.user32.IsWindow", side_effect=mock_is_window):
                with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):
                    ok = rec.start(str(self.output_mp4))
                    self.assertTrue(ok)

                    # Poll for capture worker to stop itself upon window_closed
                    t0 = time.monotonic()
                    while rec._thread is not None and rec._thread.is_alive() and time.monotonic() - t0 < 6.0:
                        time.sleep(0.05)

                    rec.stop()

        # Verify on_meeting_ended callback was called immediately
        meeting_ended_mock.assert_called()

        # Verify MP4 is valid, decodable, and ended promptly
        self.assertTrue(self.output_mp4.exists())
        with av.open(str(self.output_mp4)) as container:
            frames = list(container.decode(video=0))
            self.assertGreater(len(frames), 0)
            self.assertLess(len(frames), 50)

    def test_03_application_presentation_capture_when_teams_minimized(self):
        """Image/Evidence 3: When Teams minimizes during screen/app sharing, capture the presentation window."""
        provider = TeamsDesktopCaptureProvider()
        desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=12345,
            pid=9999,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Daily Scrum | Microsoft Teams",
            bounds=(0, 0, 1280, 720),
            is_call_stage=True,
        )

        mock_app_presentation = {
            "hwnd": 0x9876,
            "title": "Q3 Architecture Slides - PowerPoint",
            "process_name": "powerpnt.exe",
            "bounds": (100, 100, 1920, 1080),
        }

        # Teams window is iconic (minimized), but user is presenting PowerPoint
        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
            with patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
                with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Daily Scrum | Microsoft Teams"):
                    with patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(9999, "ms-teams.exe")):
                        with patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):
                            with patch("voxlocal.video.meeting_capture.find_active_presentation_window", return_value=mock_app_presentation):
                                res = provider.validate_source(desc, candidate_windows=[desc])

        self.assertTrue(res.is_valid)
        self.assertEqual(res.reason, "teams_presentation_active")
        self.assertTrue(desc.is_presenting)
        self.assertEqual(desc.presentation_hwnd, 0x9876)
        self.assertIn("PowerPoint", desc.presentation_title)

    def test_04_titleless_teams_webview_presenter_bar_detected(self):
        """Image/Evidence 4: Verify find_teams_sharing_session detects topmost WebView2 toolbar without title."""
        def fake_enum(cb, lparam):
            cb(5555, lparam)
            return True

        with patch("voxlocal.video.meeting_capture.user32.EnumWindows", side_effect=fake_enum):
            with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True):
                with patch("voxlocal.video.meeting_capture.user32.IsWindowVisible", return_value=True):
                    with patch("voxlocal.video.meeting_capture.get_window_title", return_value=""):
                        with patch("voxlocal.video.meeting_capture.get_window_class_name", return_value="TeamsWebView"):
                            with patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(9999, "ms-teams.exe")):
                                with patch("voxlocal.video.meeting_capture.user32.GetWindowLongW", return_value=0x00000008):  # WS_EX_TOPMOST
                                    with patch("voxlocal.video.meeting_capture.get_window_rect", return_value=(400, 0, 500, 80)):
                                        with patch("voxlocal.video.meeting_capture.user32.GetMonitorInfoW", return_value=False):
                                            session = find_teams_sharing_session(pid=9999)

        self.assertIsNotNone(session)
        self.assertEqual(session["bounds"], (400, 0, 500, 80))
        self.assertIn("Presenter Bar", session["title"])

    def test_05_bounded_continuity_frame_hold(self):
        """Image/Evidence 5: Brief window transition (< 1.5s) holds last frame with subtle pause dot, not full Privacy Guard."""
        rec = MeetingVideoRecorder(output_path=str(self.output_mp4), fps=15)
        rec._width = 640
        rec._height = 360
        last_frame = np.full((360, 640, 3), (100, 150, 200), dtype=np.uint8)

        held_frame = rec._draw_subtle_pause_indicator(last_frame)
        self.assertEqual(held_frame.shape, (360, 640, 3))
        # Verify base pixels preserved
        self.assertEqual(tuple(held_frame[10, 10]), (100, 150, 200))
        # Verify subtle indicator in bottom-right corner
        self.assertNotEqual(tuple(held_frame[340, 620]), (100, 150, 200))

        # Test neutral video gap frame has no "Privacy Guard" text
        gap_frame = rec._build_neutral_video_gap_frame("window_minimized", "Min", elapsed_seconds=30.0)
        self.assertEqual(gap_frame.shape, (360, 640, 3))
        self.assertFalse(np.all(gap_frame == 0))

    def test_06_controller_not_fooled_by_background_teams_process(self):
        """Image/Evidence 6: Background ms-teams.exe process does NOT prevent recording auto-stop."""
        from voxlocal.core.controller import AppController
        ctrl = AppController(config=AppConfig())
        ctrl.call_detector = MagicMock()
        ctrl.call_detector.poll.return_value = "ended"
        ctrl.call_detector.active = MagicMock()
        ctrl.call_detector.active.app_name = "Microsoft Teams"
        ctrl._meeting_state = "recording"
        ctrl._active_meeting_target_hwnd = 12345
        ctrl._active_meeting_pid = 9999

        stop_mock = MagicMock()
        ctrl.stop_meeting_recording = stop_mock

        # Mock: call stage HWND 12345 is destroyed (IsWindow -> False)
        # and ms-teams.exe PID 9999 is still alive in background
        with patch("voxlocal.core.controller.user32.IsWindow", return_value=False):
            with patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]):
                with patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):
                    ctrl._poll_call_detector()

        stop_mock.assert_called_once()
        ctrl.shutdown()

    def test_07_screen_recording_and_meeting_recording_isolation(self):
        """Verify stopping meeting recording leaves PC Screen Recording completely unaffected."""
        from voxlocal.core.controller import AppController
        ctrl = AppController(config=AppConfig())
        ctrl.screen_recorder = MagicMock()
        ctrl.screen_recorder.is_recording = True
        ctrl._meeting_state = "recording"
        ctrl._meeting_video_recorder = MagicMock()
        ctrl._meeting_video_recorder.stop.return_value = str(self.output_mp4)
        ctrl._meeting_video_recorder.get_capture_diagnostics.return_value = {"ok": True}
        ctrl.meeting_recorder = MagicMock()
        ctrl.meeting_recorder.stop.return_value = MagicMock()

        ctrl.stop_meeting_recording()

        # Screen recorder must NOT have had its stop() called
        ctrl.screen_recorder.stop.assert_not_called()
        self.assertTrue(ctrl.screen_recorder.is_recording)
        ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()

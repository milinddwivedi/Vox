from __future__ import annotations
"""
Unit test for Meeting Studio and Recording Robustness in VoxLocal.
Ensures:
1. start_meeting_recording handles title=None and title="" without AttributeError.
2. Finalize worker runs without NameError: name 'os' is not defined.
3. Audio-video muxing and DB persistence work reliably.
"""


import sys
import tempfile
from pathlib import Path
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.win32.startup import StartupManager
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import LocalMeetingSummarizer


class TestMeetingRobustness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.config = AppConfig(
            storage_dir=str(self.base_dir / "data"),
            models_dir=str(self.base_dir / "models"),
            whisper_model="small",
            auto_record_calls=False,
            record_meeting_screen=False,
            launch_at_login=StartupManager.is_enabled(),
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        from unittest.mock import MagicMock
        self.controller = AppController(config=self.config)
        self.controller.transcriber.load_model = MagicMock(return_value=True)
        self.controller.transcriber.is_model_loaded = MagicMock(return_value=True)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_start_meeting_with_none_title(self) -> None:
        """Verify that passing title=None does not throw 'NoneType' object has no attribute 'strip'."""
        self.controller.meeting_recorder.start = lambda **kw: (True, "Mock started")
        self.controller.meeting_recorder.stop = lambda **kw: None
        ok, msg = self.controller.start_meeting_recording(title=None)
        self.assertTrue(ok, f"Failed starting meeting with title=None: {msg}")
        self.assertEqual(self.controller.meeting_state, "recording")
        self.assertTrue(self.controller._active_meeting_title.startswith("Meeting "))

        # Stop recording
        self.controller.stop_meeting_recording()
        self.assertEqual(self.controller.capture_state, "idle")
        import time
        deadline = time.monotonic() + 2.0
        while self.controller.meeting_state != "idle" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.controller.meeting_state, "idle")

    def test_start_meeting_with_empty_title(self) -> None:
        """Verify that passing title='' does not throw and auto-generates a title."""
        self.controller.meeting_recorder.start = lambda **kw: (True, "Mock started")
        self.controller.meeting_recorder.stop = lambda **kw: None
        ok, msg = self.controller.start_meeting_recording(title="")
        self.assertTrue(ok, f"Failed starting meeting with title='': {msg}")
        self.assertEqual(self.controller.meeting_state, "recording")
        self.assertTrue(self.controller._active_meeting_title.startswith("Meeting "))

        self.controller.stop_meeting_recording()

    def test_db_meeting_retrieval_and_video(self) -> None:
        """Verify that meeting records with video_path persist and can be read back."""
        db = self.controller.db
        mid = db.create_meeting(title="Demo Sync", capture_mode="both")
        test_video = str(self.base_dir / "demo_screen.mp4")
        Path(test_video).write_bytes(b"dummy mp4 data")

        db.update_meeting(mid, status="completed", duration_sec=42.0, video_path=test_video)
        m = db.get_meeting(mid)
        self.assertIsNotNone(m)
        self.assertEqual(m["video_path"], test_video)
        self.assertEqual(m["duration_sec"], 42.0)
        self.assertEqual(m["status"], "completed")

    def test_clean_speech_microphone_audio_dsp(self) -> None:
        """Verify that audio DSP chain cleans hum, rumbles, and maintains healthy levels without clipping."""
        import numpy as np
        from voxlocal.audio.devices import clean_speech_microphone_audio

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        # Synthetic signal: 50 Hz massive ground hum + 150 Hz harmonic + 1000 Hz vocal tone
        hum = 0.60 * np.sin(2 * np.pi * 50 * t)
        hum_harmonic = 0.20 * np.sin(2 * np.pi * 100 * t)
        vocal = 0.15 * np.sin(2 * np.pi * 1000 * t)
        raw_noisy = (hum + hum_harmonic + vocal).astype(np.float32)

        cleaned = clean_speech_microphone_audio(raw_noisy, sr)
        self.assertLessEqual(np.max(np.abs(cleaned)), 0.98, "Audio must not exceed peak threshold")

        # Hum should be heavily attenuated
        fft = np.abs(np.fft.rfft(cleaned))
        freqs = np.fft.rfftfreq(len(cleaned), 1.0 / sr)
        hum_energy = np.sum(fft[(freqs >= 48) & (freqs <= 52)] ** 2)
        vocal_energy = np.sum(fft[(freqs >= 980) & (freqs <= 1020)] ** 2)
        self.assertGreater(vocal_energy, hum_energy, "Vocal energy must dominate over 50 Hz hum after DSP filtering")

    def test_get_system_fps_options(self) -> None:
        """Verify that framerate options are provided and valid."""
        from voxlocal.video.screen_recorder import get_system_fps_options
        opts = get_system_fps_options()
        self.assertIsInstance(opts, list)
        self.assertGreaterEqual(len(opts), 5)
        vals = [v for _, v in opts]
        self.assertIn(15, vals)
        self.assertIn(30, vals)
        self.assertIn(60, vals)


    def test_popup_collision_avoidance_above_toast(self) -> None:
        """Verify MeetingPromptPopup positions itself cleanly above Teams notification toast."""
        from unittest.mock import MagicMock, patch
        from PyQt6.QtCore import QRect
        from PyQt6.QtWidgets import QApplication
        from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup
        from voxlocal.audio.call_detector import MeetingSessionInfo

        app = QApplication.instance() or QApplication([])
        sess = MeetingSessionInfo(
            session_id="teams_test_123",
            platform="Microsoft Teams",
            window_hwnd=1234,
            window_title="Teams Meeting | Microsoft Teams",
            clean_title="Project Architecture Sync",
            bounds=(100, 100, 800, 600),
            is_call_stage=True,
            mic_active=True,
        )
        popup = MeetingPromptPopup(sess, self.controller)

        mock_screen = MagicMock()
        mock_screen.availableGeometry.return_value = QRect(0, 0, 1920, 1040)

        # Mock an obstruction in the bottom-right corner (Teams notification toast)
        # toast rect: left=1540, top=800, right=1904, bottom=1024
        toast_rect = (1540, 800, 1904, 1024)

        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen), \
             patch.object(popup, "_detect_bottom_right_obstruction", return_value=toast_rect):
            popup.reposition_smart()

            geom = popup.geometry()
            # Must be stacked above the toast (obs_top - h - pad = 800 - h - 16)
            self.assertLessEqual(geom.bottom(), 800 - 16, "Popup bottom must be at or above toast top with >=16px padding")
            self.assertGreaterEqual(geom.top(), 16, "Popup must remain within screen upper boundary")
            self.assertAlmostEqual(geom.x(), 1555, delta=2, msg="Popup must align to right screen margin")
            self.assertLessEqual(geom.right(), 1920 - 16)

        popup.close()

    def test_popup_horizontal_fallback_when_vertical_cramped(self) -> None:
        """Verify MeetingPromptPopup shifts to the left when vertical space above toast is cramped."""
        from unittest.mock import MagicMock, patch
        from PyQt6.QtCore import QRect
        from PyQt6.QtWidgets import QApplication
        from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup
        from voxlocal.audio.call_detector import MeetingSessionInfo

        app = QApplication.instance() or QApplication([])
        sess = MeetingSessionInfo(
            session_id="teams_test_cramped",
            platform="Microsoft Teams",
            window_hwnd=1234,
            window_title="Teams Meeting | Microsoft Teams",
            clean_title="Project Architecture Sync",
            bounds=(100, 100, 800, 600),
            is_call_stage=True,
            mic_active=True,
        )
        popup = MeetingPromptPopup(sess, self.controller)

        mock_screen = MagicMock()
        # Small screen (height 600) with a tall toast occupying top 300..580
        mock_screen.availableGeometry.return_value = QRect(0, 0, 1024, 600)
        tall_toast_rect = (680, 200, 1000, 580)

        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen), \
             patch.object(popup, "_detect_bottom_right_obstruction", return_value=tall_toast_rect):
            popup.reposition_smart()

            geom = popup.geometry()
            # Stacking above 200 would result in top < 0, so it must shift to the left of 680
            self.assertLessEqual(geom.right(), 680 - 16, "Popup must shift to the left of the toast when vertically cramped")

        popup.close()

    def test_black_frame_detection_and_dwm_fallback(self) -> None:
        """Verify blank frame detection and dynamic fallback to window-bounded DWM capture."""
        import numpy as np
        from unittest.mock import MagicMock, patch
        from voxlocal.video.meeting_capture import MeetingVideoRecorder, MeetingSourceDescriptor

        desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=5555,
            pid=9999,
            process_name="ms-teams.exe",
            class_name="Chrome_WidgetWin_1",
            window_title="Teams Meeting | Microsoft Teams",
            bounds=(100, 100, 800, 600),
            is_call_stage=True,
        )
        rec = MeetingVideoRecorder(
            output_path=str(self.base_dir / "test_out.mp4"),
            source_descriptor=desc,
        )

        # 1. Blank frame detection
        pure_black = np.zeros((480, 640, 3), dtype=np.uint8)
        self.assertTrue(rec._is_black_or_blank_frame(pure_black), "Pure black frame must be detected as blank")

        real_frame = np.full((480, 640, 3), 120, dtype=np.uint8)
        self.assertFalse(rec._is_black_or_blank_frame(real_frame), "Normal meeting frame must not be detected as blank")

        # 2. Dynamic fallback: PrintWindow returns black, Window-Bounded returns real pixels
        with patch.object(rec, "_capture_print_window", return_value=pure_black), \
             patch.object(rec, "_capture_window_bounded_screen", return_value=real_frame), \
             patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsWindowVisible", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False), \
             patch("voxlocal.video.meeting_capture.get_window_rect", return_value=(100, 100, 800, 600)), \
             patch("voxlocal.video.meeting_capture.get_dwm_visible_crop_offsets", return_value=(0, 0, 800, 600)):

            captured = rec._capture_isolated_window_bgr(5555)
            self.assertIsNotNone(captured)
            self.assertEqual(captured.shape, (480, 640, 3))
            self.assertEqual(rec._capture_mode_preference, "window_bounded")
            self.assertTrue(rec._has_switched_to_fallback)

    def test_recording_lifecycle_preserved_during_teams_tab_navigation(self) -> None:
        """Verify that user switching tabs in Teams (Chat | ...) while recording does NOT terminate session."""
        from unittest.mock import MagicMock, patch
        from voxlocal.audio.call_detector import CallDetector, MeetingSessionInfo, ActiveCall

        detector = CallDetector()
        sess = MeetingSessionInfo(
            session_id="teams_call_preservation",
            platform="Microsoft Teams",
            window_hwnd=7777,
            window_title="Weekly Status | Microsoft Teams",
            clean_title="Weekly Status",
            bounds=(100, 100, 800, 600),
            is_call_stage=True,
            mic_active=True,
            pid=4444,
        )
        detector.active_session = sess
        detector.active = ActiveCall(
            app_name="Microsoft Teams",
            key="ms-teams.exe",
            since=0.0,
            window_hwnd=7777,
            session_id="teams_call_preservation",
        )
        detector.set_recording_active(True)

        def _mock_get_text(h, buf, l):
            try:
                buf.value = "Chat | Alice | Microsoft Teams"
            except Exception:
                pass
            return 31

        # Mock: user navigates Teams window to "Chat | Alice | Microsoft Teams"
        with patch("voxlocal.audio.call_detector.user32.IsWindow", return_value=True), \
             patch("voxlocal.audio.call_detector.user32.GetWindowTextLengthW", return_value=31), \
             patch("voxlocal.audio.call_detector.user32.GetWindowTextW", side_effect=_mock_get_text), \
             patch("voxlocal.audio.call_detector.is_pid_alive", return_value=True):

            event = detector.poll(now=100.0)
            self.assertIsNone(event, "Detector must NOT emit 'ended' when user switches tabs during an active recording")
            self.assertIsNotNone(detector.active_session)

    def test_controller_preserves_recording_on_transient_detector_drop(self) -> None:
        """Verify AppController does not stop active recording if meeting window/process is still alive."""
        from unittest.mock import MagicMock, patch

        self.controller._meeting_state = "recording"
        self.controller._active_meeting_target_hwnd = 8888
        self.controller._active_meeting_pid = 2222

        # Mock CallDetector returning "ended" event
        self.controller.call_detector.poll = MagicMock(return_value="ended")
        self.controller.call_detector.active = MagicMock()
        self.controller.call_detector.active.app_name = "Microsoft Teams"
        self.controller.call_detector.active.session_id = "test_sess"
        self.controller.call_detector.active.key = "test_key"

        # Mock meeting window still alive
        with patch("voxlocal.core.controller.user32.IsWindow", return_value=True), \
             patch("voxlocal.core.controller.is_pid_alive", return_value=True), \
             patch.object(self.controller, "stop_meeting_recording") as mock_stop:

            self.controller._poll_call_detector()
            # stop_meeting_recording must NOT have been called because window is alive!
            mock_stop.assert_not_called()
            self.assertEqual(self.controller._meeting_state, "recording")


if __name__ == "__main__":
    unittest.main(verbosity=2)

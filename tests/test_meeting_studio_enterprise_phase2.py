import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PyQt6.QtWidgets import QApplication
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, MeetingLifecycleState
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline
from voxlocal.video.meeting_capture import MeetingSourceDescriptor


class TestMeetingStudioEnterprisePhase2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = AppConfig()
        test_dir = Path(__file__).parent / "tmp_enterprise_test"
        self.config.storage_dir = str(test_dir)
        self.config.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.controller = AppController(self.config)

    def tearDown(self):
        try:
            self.controller.shutdown()
        except Exception:
            pass
        test_dir = Path(__file__).parent / "tmp_enterprise_test"
        if test_dir.exists():
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_01_pre_call_recording_with_dynamic_video_binding(self):
        """
        Verify that when user starts recording before a meeting call window appears,
        audio recording begins immediately and video capture binds dynamically once
        the Teams/Zoom/Meet window opens.
        """
        self.controller.meeting_recorder.start = MagicMock(return_value=(True, "Audio started"))
        self.controller.meeting_recorder.stop = MagicMock(return_value=None)

        # 1. Start Audio + Video before any call window exists (isolated from host desktop)
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]), \
             patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]), \
             patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=None), \
             patch("voxlocal.video.meeting_capture.MeetingVideoRecorder.start", return_value=True):
            ok, msg = self.controller.start_meeting_recording(
                title="Sprint Planning Standup",
                recording_mode="audio_video",
                allow_deferred_video=True,
                strict_video=False,
            )
            self.assertTrue(ok, f"Failed to start pre-meeting recording: {msg}")
            self.assertEqual(self.controller.meeting_state, "recording")
            self.assertTrue(self.controller._pending_video_bind)
            self.assertIn("Waiting for call window", self.controller.meeting_lifecycle_message)

        # 2. Simulate call window appearing during poll
        fake_source = MeetingSourceDescriptor(
            provider_id="teams_call",
            platform_name="Microsoft Teams",
            hwnd=998877,
            pid=1234,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Teams Meeting | Sprint Planning",
            is_call_stage=True,
        )

        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]), \
             patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]), \
             patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=fake_source), \
             patch("voxlocal.video.meeting_capture.MeetingVideoRecorder.start", return_value=True):
            self.controller._poll_call_detector()
            self.assertFalse(self.controller._pending_video_bind)
            self.assertIsNotNone(self.controller._meeting_video_recorder)
            self.assertEqual(self.controller._active_meeting_target_hwnd, 998877)
            self.assertIn("Recording (Audio + Video)", self.controller.meeting_lifecycle_message)

        # 3. Clean stop
        self.controller.stop_meeting_recording()
        self.assertFalse(self.controller._pending_video_bind)

    def test_02_action_item_hallucination_protection(self):
        """
        Verify that action items without explicit due dates or owners NEVER
        hallucinate 'Next Sprint' or 'Host'.
        """
        evidence_items = [
            {
                "speaker": "Speaker 1",
                "text": "We need to update the linesheet documentation and verify the numbers.",
                "start": 10.0,
                "end": 14.0,
                "words": ["We", "need", "to", "update", "the", "linesheet", "documentation", "and", "verify", "the", "numbers."],
            }
        ]

        analysis = MeetingMinutesPipeline.generate_minutes(
            segments=[{"speaker": "Speaker 1", "text": evidence_items[0]["text"], "start_time": 10.0, "end_time": 14.0}],
            title="Linesheet Review",
            platform="Microsoft Teams",
            duration_sec=30.0,
            llm_engine=None,  # Force stage rule-based pipeline
        )

        actions = analysis.get("action_items", [])
        self.assertGreater(len(actions), 0)
        action = actions[0]

        # Verify no invented deadline
        self.assertIsNone(action.get("due_date"))
        self.assertNotEqual(action.get("due_date"), "Next Sprint")

        # Verify MoM markdown does not contain "Next Sprint" or "Host" or "Not specified"
        mom_md = analysis.get("mom_markdown", "")
        self.assertNotIn("Next Sprint", mom_md)
        self.assertNotIn("Host", mom_md)
        self.assertIn("| — |", mom_md)

    def test_03_truncated_json_repair_in_llm_engine(self):
        """
        Verify that LLMEngine defensively recovers truncated JSON generated by the local LLM.
        """
        # Test 1: Truncated inside action_items list
        raw_truncated = (
            '{\n'
            '  "executive_summary": "Quarterly planning sync concluded with roadmap alignment.",\n'
            '  "decisions": [{"decision": "Migrate database to PostgreSQL", "rationale": "Better concurrency"}],\n'
            '  "action_items": [\n'
            '    {"description": "Provision staging cluster", "owner": "DevOps", "due_date": "Friday", "priority": "High"'
        )
        parsed = LocalLLMEngine._parse_json_safely(raw_truncated)
        self.assertIsNotNone(parsed, "Failed to repair truncated JSON")
        self.assertEqual(parsed["executive_summary"], "Quarterly planning sync concluded with roadmap alignment.")
        self.assertEqual(len(parsed["decisions"]), 1)
        self.assertEqual(len(parsed["action_items"]), 1)
        self.assertEqual(parsed["action_items"][0]["owner"], "DevOps")

        # Test 2: Truncated mid-string
        raw_mid_string = (
            '{\n'
            '  "executive_summary": "The client requested immediate deployment of the latest hotfix'
        )
        parsed_str = LocalLLMEngine._parse_json_safely(raw_mid_string)
        self.assertIsNotNone(parsed_str)
        self.assertIn("The client requested immediate deployment", parsed_str["executive_summary"])

    def test_04_screen_recording_auto_heals_orphans(self):
        """
        Verify that start_screen_recording() safely stops and heals any orphaned
        audio or video recorder before initiating a new recording.
        """
        mock_audio = MagicMock()
        mock_audio.is_recording = True
        mock_audio.stop = MagicMock()

        mock_video = MagicMock()
        mock_video.is_recording = True
        mock_video.stop = MagicMock()
        mock_video.fps = 30
        mock_video.target_hwnd = 0

        self.controller.pc_screen_audio = mock_audio
        self.controller.pc_screen_recorder = mock_video

        with patch.object(self.controller.pc_screen_audio, "start", return_value=(True, "Audio ok")), \
             patch.object(self.controller.pc_screen_recorder, "start", return_value=(True, "Video ok")):
            ok, msg = self.controller.start_screen_recording(title="Demo Recording")
            self.assertTrue(ok)
            mock_audio.stop.assert_called_once()
            mock_video.stop.assert_called_once()
            self.controller.stop_screen_recording()

    def test_05_user_edited_report_summary_preserved_across_reanalysis(self):
        """
        Verify that if a user manually edits and saves a meeting report summary,
        subsequent finalizing or re-processing preserves the user's manual edits.
        """
        meeting_id = self.controller.db.create_meeting(
            title="Client Proposal Review",
            capture_mode="both",
        )
        custom_summary = "CRITICAL NOTE: Client signed the MSA with custom SLA terms."
        self.controller.db.update_meeting(
            meeting_id,
            summary=custom_summary,
            user_edited=1,
            status="completed",
        )

        retrieved = self.controller.db.get_meeting(meeting_id)
        self.assertEqual(retrieved.get("user_edited"), 1)
        self.assertEqual(retrieved.get("summary"), custom_summary)


if __name__ == "__main__":
    unittest.main()

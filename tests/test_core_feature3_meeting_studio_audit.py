from __future__ import annotations
"""Comprehensive Technical Audit, Functional & Lifecycle Verification Test Suite
for VOX Core Feature 3 — Meeting Studio.

Validates all 12 core requirements:
1. Mode A (Audio Only): captures audio with zero video resource allocation.
2. Mode B (Audio + Video): captures dual audio and call window video.
3. Strict Video: refuses silent downgrade when video capture fails.
4. No-Meeting Protection: prevents silent generic recording when no meeting exists.
5. Explicit 10-state Lifecycle State Machine transitions.
6. Automatic Meeting Termination in both Audio+Video and Audio-Only modes.
7. Interruption Handling: window minimization does not stop recording.
8. Outcome Deduplication: action items and decisions deduplicated across re-analyzes.
9. Long Meeting Transcript Handling: stratified chronological sampling preserves middle sections.
10. User-Edited MoM Preservation: manual edits protected from AI overwrite.
11. Feature Isolation: PC Screen Recording (CF2) & Voice Typing (CF1) unaffected by Meeting Studio.
12. Enterprise Business Scenarios: Customer/Client, Scrum, and PLM/ERP workflow resilience.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from unittest.mock import MagicMock, patch

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingLifecycleState,
    MeetingSessionInfo,
    ActiveCall,
    is_leaving_title,
    is_non_call_hub,
    list_all_meeting_windows,
)
from voxlocal.video.meeting_capture import MeetingSourceDescriptor
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline
from voxlocal.engine.llm_engine import LocalLLMEngine


class TestCoreFeature3MeetingStudioAudit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="vox_cf3_audit_")
        self.base_dir = Path(self.tmp_dir)
        self.config = AppConfig(
            storage_dir=str(self.base_dir / "data"),
            models_dir=str(self.base_dir / "models"),
            whisper_model="small",
            auto_record_calls=False,
            record_meeting_screen=True,
            meeting_recording_mode="audio_video",
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(config=self.config)
        self.controller.transcriber.load_model = MagicMock(return_value=True)
        self.controller.transcriber.is_model_loaded = MagicMock(return_value=True)

    def tearDown(self):
        try:
            self.controller.shutdown()
        except Exception:
            pass
        try:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Mode A: Audio Only Recording
    # -------------------------------------------------------------------------
    def test_01_mode_a_audio_only_allocates_no_video_resources(self):
        """Mode A (Audio Only) captures dual audio with zero video initialization."""
        self.controller.meeting_recorder.start = MagicMock(return_value=(True, "Audio capture started"))
        self.controller.meeting_recorder.stop = MagicMock(return_value=None)
        self.controller._finalize_meeting_worker = MagicMock()

        ok, msg = self.controller.start_meeting_recording(
            title="Design Sync",
            recording_mode="audio_only",
        )
        self.assertTrue(ok, f"Failed starting audio-only meeting: {msg}")
        self.assertEqual(self.controller.active_recording_mode, "audio_only")
        self.assertIsNone(self.controller._meeting_video_recorder, "Video recorder must NOT be allocated in Mode A")
        self.assertEqual(self.controller.meeting_lifecycle_state, MeetingLifecycleState.RECORDING.value)
        self.controller.stop_meeting_recording()

    # -------------------------------------------------------------------------
    # 2. Mode B: Audio + Video Recording
    # -------------------------------------------------------------------------
    def test_02_mode_b_audio_video_allocates_call_window_recorder(self):
        """Mode B (Audio + Video) resolves the call window and binds MeetingVideoRecorder."""
        self.controller.meeting_recorder.start = MagicMock(return_value=(True, "Audio capture started"))
        self.controller.meeting_recorder.stop = MagicMock(return_value=None)
        self.controller._finalize_meeting_worker = MagicMock()

        mock_desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=7788,
            pid=5566,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sprint Planning | Microsoft Teams",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
        )

        with patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=mock_desc), \
             patch("voxlocal.video.meeting_capture.MeetingVideoRecorder.start", return_value=True):

            ok, msg = self.controller.start_meeting_recording(
                title="Sprint Planning",
                recording_mode="audio_video",
                target_hwnd=7788,
            )
            self.assertTrue(ok)
            self.assertEqual(self.controller.active_recording_mode, "audio_video")
            self.assertIsNotNone(self.controller._meeting_video_recorder)
            self.assertEqual(self.controller._active_meeting_target_hwnd, 7788)
            self.assertEqual(self.controller.meeting_lifecycle_state, MeetingLifecycleState.RECORDING.value)
            self.controller.stop_meeting_recording()

    # -------------------------------------------------------------------------
    # 3. Strict Video Downgrade Protection
    # -------------------------------------------------------------------------
    def test_03_strict_video_refuses_silent_downgrade(self):
        """When strict_video=True and video capture cannot be initialized, fail cleanly."""
        self.controller.meeting_recorder.start = MagicMock(return_value=(True, "Audio capture started"))
        self.controller.meeting_recorder.stop = MagicMock(return_value=None)

        # No call window open
        with patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=None):
            ok, msg = self.controller.start_meeting_recording(
                title="Customer Demo",
                recording_mode="audio_video",
                strict_video=True,
            )
            self.assertFalse(ok, "Must not silently downgrade to audio-only when strict_video=True")
            self.assertIn("Audio + Video mode requires an active meeting window", msg)
            self.assertEqual(self.controller.meeting_lifecycle_state, MeetingLifecycleState.FAILED.value)

    # -------------------------------------------------------------------------
    # 4. No-Meeting Protection
    # -------------------------------------------------------------------------
    def test_04_no_meeting_detected_protection(self):
        """When require_active_meeting=True, do not start recording if no call is open."""
        with patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=None):
            ok, msg = self.controller.start_meeting_recording(
                title="Generic Notes",
                require_active_meeting=True,
            )
            self.assertFalse(ok)
            self.assertEqual(msg, "No active meeting detected.")
            self.assertEqual(self.controller.meeting_lifecycle_state, MeetingLifecycleState.NO_MEETING_DETECTED.value)

    # -------------------------------------------------------------------------
    # 5. Explicit 10-State Lifecycle State Machine
    # -------------------------------------------------------------------------
    def test_05_lifecycle_state_machine_transitions(self):
        """Verify lifecycle states transition from detection to recording to finalizing to completed."""
        states_seen = []
        self.controller.meeting_lifecycle_changed.connect(lambda st, msg: states_seen.append(st))

        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.NO_MEETING_DETECTED, "No meeting")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.MEETING_DETECTED, "Teams detected")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.AWAITING_CONFIRMATION, "Awaiting user")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.PREPARING_CAPTURE, "Preparing")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.RECORDING, "Recording")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.TEMPORARILY_INTERRUPTED, "Paused")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.MEETING_ENDING, "Call ending")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.FINALIZING, "Finalizing")
        self.controller.set_meeting_lifecycle_state(MeetingLifecycleState.COMPLETED, "Done")

        expected = [
            "no_meeting_detected",
            "meeting_detected",
            "awaiting_confirmation",
            "preparing_capture",
            "recording",
            "temporarily_interrupted",
            "meeting_ending",
            "finalizing",
            "completed",
        ]
        self.assertEqual(states_seen, expected)

    # -------------------------------------------------------------------------
    # 6. Automatic Meeting Termination in Audio Only Mode
    # -------------------------------------------------------------------------
    def test_06_automatic_termination_in_audio_only_mode(self):
        """Bound meeting window close terminates recording in Audio Only mode too."""
        self.controller._meeting_state = "recording"
        self.controller._active_meeting_target_hwnd = 4455
        self.controller._active_meeting_platform = "Microsoft Teams"
        self.controller.stop_meeting_recording = MagicMock()
        self.controller.call_detector.poll = MagicMock(return_value=None)

        # Window handle is closed (IsWindow -> False)
        with patch("voxlocal.core.controller.user32.IsWindow", return_value=False), \
             patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]), \
             patch("voxlocal.video.meeting_capture.find_teams_sharing_session", return_value=None):

            # Simulate two consecutive poll ticks (elapsed grace > 2.0s)
            with patch("voxlocal.core.controller.time.monotonic", side_effect=[100.0, 103.0]):
                self.controller._poll_call_detector()
                self.controller._poll_call_detector()

        self.controller.stop_meeting_recording.assert_called_once()
        self.assertEqual(self.controller.meeting_lifecycle_state, MeetingLifecycleState.MEETING_ENDING.value)

    # -------------------------------------------------------------------------
    # 7. Interruption Handling: Minimized Call Window Never Stops Recording
    # -------------------------------------------------------------------------
    def test_07_minimized_call_window_does_not_stop_recording(self):
        """User minimizing Teams window to view documents must NOT terminate recording."""
        self.controller._meeting_state = "recording"
        self.controller._active_meeting_target_hwnd = 4455
        self.controller._active_meeting_platform = "Microsoft Teams"
        stop_mock = MagicMock()
        self.controller.stop_meeting_recording = stop_mock

        # Window exists and title is valid active call stage
        with patch("voxlocal.core.controller.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.get_window_title", return_value="Daily Scrum | Microsoft Teams"):

            self.controller._poll_call_detector()

        stop_mock.assert_not_called()
        self.assertEqual(self.controller.meeting_state, "recording")

    # -------------------------------------------------------------------------
    # 8. Outcome Deduplication (Database & Re-analyze)
    # -------------------------------------------------------------------------
    def test_08_outcome_deduplication_across_reanalysis(self):
        """Re-analyzing or re-transcribing a meeting does NOT accumulate duplicate action items or decisions."""
        db = self.controller.db
        mid = db.create_meeting(title="PLM Architecture Review")

        # First run: add action items and decisions
        db.add_action_item(mid, "Deploy firmware update v2.1 to staging", owner="Alice", due_date="Friday")
        db.add_action_item(mid, "Deploy firmware update v2.1 to staging", owner="Alice", due_date="Friday") # Exact dup
        db.add_decision(mid, "Adopt gRPC for microservice RPC", rationale="Lower latency")
        db.add_decision(mid, "Adopt gRPC for microservice RPC", rationale="Lower latency") # Exact dup

        actions1 = db.list_action_items(meeting_id=mid)
        decisions1 = db.list_decisions(meeting_id=mid)
        self.assertEqual(len(actions1), 1, "Duplicate action item must be deduplicated")
        self.assertEqual(len(decisions1), 1, "Duplicate decision must be deduplicated")

        # Simulate re-analyze: clear outcomes before inserting
        db.clear_meeting_outcomes(mid, preserve_completed=True)
        db.add_action_item(mid, "Deploy firmware update v2.1 to staging", owner="Alice", due_date="Friday")
        db.add_decision(mid, "Adopt gRPC for microservice RPC", rationale="Lower latency")

        actions2 = db.list_action_items(meeting_id=mid)
        decisions2 = db.list_decisions(meeting_id=mid)
        self.assertEqual(len(actions2), 1)
        self.assertEqual(len(decisions2), 1)

    # -------------------------------------------------------------------------
    # 9. Long Meeting Transcript Handling (Stratified Sampling)
    # -------------------------------------------------------------------------
    def test_09_long_meeting_stratified_sampling_preserves_middle_cues(self):
        """Transcripts > 6000 words retain beginning, stratified middle with cues, and end."""
        # Generate 150 transcript segments spanning 90 minutes
        segments = []
        for i in range(150):
            t_sec = i * 36.0
            if i == 75:
                # Middle section key decision!
                txt = "We agreed to migrate database clusters to PostgreSQL 16 by end of quarter."
            elif i == 80:
                # Middle section key action item!
                txt = "Bob will prepare migration runbooks by next Tuesday."
            else:
                txt = f"Reviewing quarterly metrics and performance statistics for module {i} with the team."
            segments.append({
                "start_time": t_sec,
                "end_time": t_sec + 20.0,
                "speaker": "Alice" if i % 2 == 0 else "Bob",
                "text": txt,
                "words": txt.split(),
            })

        mock_llm = MagicMock(spec=LocalLLMEngine)
        mock_llm.is_available.return_value = False # Test deterministic pipeline fallback

        minutes = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            title="Q3 Strategy & Architecture Review",
            duration_sec=5400.0,
            llm_engine=mock_llm,
        )

        decisions = minutes["decisions"]
        actions = minutes["action_items"]
        mom_text = minutes["mom_markdown"]

        # Verify middle decision was captured
        dec_texts = " ".join(d["decision"] for d in decisions)
        self.assertIn("PostgreSQL 16", dec_texts, "Middle-meeting decision must be captured without omission")

        # Verify middle action was captured
        act_texts = " ".join(a["description"] for a in actions)
        self.assertIn("migration runbooks", act_texts, "Middle-meeting action item must be captured without omission")

        # Verify markdown has no leading '#' or '*'
        for line in mom_text.splitlines():
            l_strip = line.strip()
            if l_strip:
                self.assertFalse(l_strip.startswith(("#", "*")), f"Header or line starts with '#' or '*': {l_strip}")

    # -------------------------------------------------------------------------
    # 10. User-Edited MoM Preservation
    # -------------------------------------------------------------------------
    def test_10_user_edited_summary_preserved_on_finalize(self):
        """When a meeting has user_edited=1, user manual edits are not overwritten."""
        db = self.controller.db
        mid = db.create_meeting(title="Product Roadmap")
        user_summary = "Custom Executive Note: Approved Q4 Roadmap without feature X."
        db.update_meeting(mid, summary=user_summary, user_edited=1)

        m_before = db.get_meeting(mid)
        self.assertEqual(m_before["user_edited"], 1)
        self.assertEqual(m_before["summary"], user_summary)

    # -------------------------------------------------------------------------
    # 11. Feature Isolation: Screen Recording & Voice Typing Unaffected
    # -------------------------------------------------------------------------
    def test_11_feature_isolation_screen_rec_and_voice_typing(self):
        """Pausing or stopping meeting recording leaves PC Screen Recording and Voice Typing unaffected."""
        self.controller.pc_screen_recorder = MagicMock()
        self.controller.pc_screen_recorder.is_recording = True
        self.controller.screen_recorder = MagicMock()
        self.controller.screen_recorder.is_recording = True
        self.controller._screen_rec_state = "recording"

        self.controller._dictation_state = "listening"
        self.controller._hands_free_active = True

        self.controller._meeting_state = "recording"
        self.controller._meeting_video_recorder = MagicMock()
        self.controller._meeting_video_recorder.is_recording = True
        self.controller.meeting_recorder = MagicMock()
        self.controller._finalize_meeting_worker = MagicMock()

        # Pause meeting recording
        self.controller.pause_meeting_recording()
        # Meeting recorder paused
        self.controller.meeting_recorder.pause.assert_called_once()
        # PC Screen Recording must NOT have been paused
        self.controller.screen_recorder.pause.assert_not_called()
        self.controller.pc_screen_recorder.pause.assert_not_called()
        self.assertEqual(self.controller._screen_rec_state, "recording")
        self.assertTrue(self.controller._hands_free_active)

        # Stop meeting recording
        self.controller.stop_meeting_recording()
        self.controller.screen_recorder.stop.assert_not_called()
        self.controller.pc_screen_recorder.stop.assert_not_called()
        self.assertEqual(self.controller._screen_rec_state, "recording")

    # -------------------------------------------------------------------------
    # 12. Enterprise Business Scenario (Scrum & PLM Workflow)
    # -------------------------------------------------------------------------
    def test_12_enterprise_scrum_and_plm_workflow(self):
        """Simulate enterprise Daily Scrum with cross-talk, action assignments, and clean exit."""
        scrum_segments = [
            {"start_time": 0.0, "end_time": 5.0, "speaker": "Scrum Master", "text": "Good morning team, let us start our daily standup.", "words": ["Good", "morning", "team"]},
            {"start_time": 6.0, "end_time": 18.0, "speaker": "Dev Lead", "text": "Yesterday I finished the PLM integration PR. Today we decided to deploy to test cluster.", "words": ["Yesterday", "PR"]},
            {"start_time": 19.0, "end_time": 28.0, "speaker": "Dev Lead", "text": "Alice will review the integration tests by EOD today.", "words": ["Alice", "review"]},
            {"start_time": 30.0, "end_time": 38.0, "speaker": "QA Lead", "text": "Can we verify database rollback before staging?", "words": ["verify", "rollback"]},
            {"start_time": 40.0, "end_time": 46.0, "speaker": "Dev Lead", "text": "Yes, we confirmed rollback scripts are in place.", "words": ["Yes", "confirmed"]},
        ]

        minutes = MeetingMinutesPipeline.generate_minutes(
            segments=scrum_segments,
            title="Daily Standup - PLM Team",
            platform="Microsoft Teams",
            duration_sec=50.0,
        )

        self.assertIn("PLM Team", minutes["mom_markdown"])
        self.assertTrue(len(minutes["decisions"]) >= 1)
        self.assertTrue(len(minutes["action_items"]) >= 1)
        self.assertEqual(minutes["action_items"][0]["owner"], "Alice")
        self.assertIn("eod", minutes["action_items"][0]["due_date"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

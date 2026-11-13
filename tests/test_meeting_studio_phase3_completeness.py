"""
Phase 3: Exhaustive Use-Case Discovery, Functional Completeness & Enterprise-Grade Hardening Tests
Validates:
1. Startup recovery of interrupted / unfinalized meeting recordings after system crash.
2. Re-transcription auto-locating audio files from disk if database path was missing.
3. Ask Vox UI citations signature compatibility and stream completion handling.
4. Video capture minimization privacy state callback and user feedback notification.
5. Bluetooth Hands-Free profile vs Stereo loopback collision detection.
6. Semantic vs Syntactic JSON completion and malformed tail element pruning.
7. Ask Vox cross-meeting retrieval scoping and multi-meeting grounded evidence.
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine, QueryIntent
from voxlocal.storage.database import LocalDatabase
from voxlocal.audio.devices import AudioDeviceInfo, AudioDeviceManager


def create_test_wav(path: Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Generate 16-bit silence/simple sine
        raw_data = bytearray(n_samples * 2)
        wf.writeframes(raw_data)


class TestMeetingStudioPhase3Completeness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase3_test_")
        self.temp_path = Path(self.temp_dir)
        self.config = AppConfig(storage_dir=str(self.temp_path))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.db = LocalDatabase(str(self.config.db_path))
        self.recordings_dir = self.config.recordings_dir
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_startup_recovery_of_interrupted_meetings(self):
        """Simulate crash mid-meeting: DB has status='recording' with empty audio_path."""
        m_id = self.db.create_meeting(
            title="Design Review Crash Sim",
            capture_mode="both",
            audio_path="",
        )
        mtg = self.db.get_meeting(m_id)
        created_slug = str(mtg.get("created_at", "")).replace("-", "").replace(":", "").replace(" ", "_")
        # Place actual recording on disk in matching folder
        meeting_dir = self.recordings_dir / f"meeting_{created_slug[:13]}0000"
        wav_path = meeting_dir / "mixed.wav"
        create_test_wav(wav_path, duration_sec=5.0)

        # Initialize controller -> runs _recover_unfinalized_meetings
        with patch("voxlocal.core.controller.WindowFocusTracker"), \
             patch("voxlocal.core.controller.TextInserter"), \
             patch("voxlocal.core.controller.GlobalHotkeyManager"), \
             patch("voxlocal.core.controller.CallDetector"):
            ctrl = AppController(self.config)
            ctrl.shutdown()

        recovered = self.db.get_meeting(m_id)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertAlmostEqual(recovered["duration_sec"], 5.0, delta=0.2)
        self.assertTrue(Path(recovered["audio_path"]).exists())
        self.assertEqual(recovered["audio_path"], str(wav_path))

    def test_re_transcribe_auto_locating_missing_audio_path(self):
        """Verify re_transcribe_meeting repairs empty audio_path from video_path or disk."""
        m_id = self.db.create_meeting(
            title="Client Quarterly Review",
            capture_mode="both",
            audio_path="",
            video_path="",
        )
        self.db.update_meeting(m_id, status="interrupted")
        # Place WAV in disk folder matching created_at
        mtg = self.db.get_meeting(m_id)
        created_slug = str(mtg.get("created_at", "")).replace("-", "").replace(":", "").replace(" ", "_")
        folder = self.recordings_dir / f"meeting_{created_slug[:13]}0000"
        wav_path = folder / "mixed.wav"
        create_test_wav(wav_path, duration_sec=3.0)

        with patch("voxlocal.core.controller.WindowFocusTracker"), \
             patch("voxlocal.core.controller.TextInserter"), \
             patch("voxlocal.core.controller.GlobalHotkeyManager"), \
             patch("voxlocal.core.controller.CallDetector"):
            ctrl = AppController(self.config)
            with patch("threading.Thread") as mock_thread:
                ctrl.re_transcribe_meeting(m_id)
                self.assertTrue(mock_thread.called)

            updated = self.db.get_meeting(m_id)
            self.assertTrue(Path(updated["audio_path"]).exists())
            ctrl.shutdown()

    def test_bluetooth_profile_conflict_detection(self):
        """Verify Bluetooth Hands-Free mic paired with Stereo loopback triggers advisory."""
        bt_mic = AudioDeviceInfo(
            index=1,
            name="Headset (Sony WH-1000XM4 Hands-Free AG Audio)",
            channels=1,
            sample_rate=16000,
            is_loopback=False,
        )
        stereo_loopback = AudioDeviceInfo(
            index=2,
            name="Headphones (Sony WH-1000XM4 Stereo)",
            channels=2,
            sample_rate=48000,
            is_loopback=True,
        )
        internal_mic = AudioDeviceInfo(
            index=3,
            name="Microphone Array (Realtek(R) Audio)",
            channels=2,
            sample_rate=48000,
            is_loopback=False,
        )

        # Conflict scenario
        warn = AudioDeviceManager.detect_bluetooth_profile_conflict(bt_mic, stereo_loopback)
        self.assertIsNotNone(warn)
        self.assertIn("Bluetooth Profile Conflict Notice", warn)
        self.assertIn("Hands-Free", warn)

        # Healthy scenario 1: Internal mic + Stereo loopback
        ok_warn = AudioDeviceManager.detect_bluetooth_profile_conflict(internal_mic, stereo_loopback)
        self.assertIsNone(ok_warn)

        # Healthy scenario 2: None
        none_warn = AudioDeviceManager.detect_bluetooth_profile_conflict(None, stereo_loopback)
        self.assertIsNone(none_warn)

    def test_video_minimization_state_callback(self):
        """Verify controller responds to video minimization without failing meeting recording."""
        with patch("voxlocal.core.controller.WindowFocusTracker"), \
             patch("voxlocal.core.controller.TextInserter"), \
             patch("voxlocal.core.controller.GlobalHotkeyManager"), \
             patch("voxlocal.core.controller.CallDetector"):
            ctrl = AppController(self.config)

            state_updates = []
            ctrl.meeting_state_changed.connect(lambda st, msg: state_updates.append((st, msg)))

            with ctrl._state_lock:
                ctrl._meeting_state = "recording"
                ctrl._active_meeting_title = "Sprint Planning"
                ctrl._active_recording_mode = "audio_video"

            # Trigger minimized
            ctrl._on_video_source_state_changed("window_minimized", "Meeting window minimized")
            self.assertTrue(len(state_updates) > 0)
            self.assertEqual(state_updates[-1][0], "recording")
            self.assertIn("Video Paused - Window Minimized", state_updates[-1][1])

            # Trigger restored/healthy
            ctrl._on_video_source_state_changed("healthy", "Capture healthy")
            self.assertEqual(state_updates[-1][0], "recording")
            self.assertIn("Audio + Video", state_updates[-1][1])

            ctrl.shutdown()

    def test_semantic_vs_syntactic_json_completion(self):
        """Verify syntactic JSON repair closes brackets and semantic sanitizer prunes incomplete tail fragments."""
        # Truncated JSON with dangling incomplete action item at the end
        truncated_raw = (
            '{\n'
            '  "executive_summary": "Discussed deployment schedule and assigned tasks.",\n'
            '  "decisions": [\n'
            '    {"decision": "Deploy to staging first", "category": "Confirmed"}\n'
            '  ],\n'
            '  "action_items": [\n'
            '    {"description": "Run integration tests", "owner": "Milind"},\n'
            '    {"description": "Prepare release notes", "owner": "Alex"},\n'
            '    {"description": ""\n'
        )

        parsed = LocalLLMEngine._parse_json_safely(truncated_raw)
        self.assertIsNotNone(parsed)
        self.assertIn("executive_summary", parsed)
        self.assertIn("decisions", parsed)
        self.assertIn("action_items", parsed)

        # The trailing incomplete action item with empty description should have been pruned
        self.assertEqual(len(parsed["action_items"]), 2)
        self.assertEqual(parsed["action_items"][0]["owner"], "Milind")
        self.assertEqual(parsed["action_items"][1]["owner"], "Alex")

        # Dangling key colon truncation
        key_colon_truncated = (
            '{\n'
            '  "executive_summary": "Quarterly goals review.",\n'
            '  "topics": [\n'
            '    {"title": "Revenue", "summary": "Up 20%"}\n'
            '  ],\n'
            '  "pending_tasks":'
        )
        parsed2 = LocalLLMEngine._parse_json_safely(key_colon_truncated)
        self.assertIsNotNone(parsed2)
        self.assertEqual(len(parsed2["topics"]), 1)

    def test_ask_vox_cross_meeting_retrieval_scoping(self):
        """Verify cross-meeting citations retrieval across multiple distinct meetings."""
        m1_id = self.db.create_meeting(title="Everlane Linesheet Sync", capture_mode="both")
        self.db.add_transcript_segment(m1_id, 0, 10.0, 20.0, "Mike", "We will export the linesheet in CSV format.")
        self.db.add_decision(m1_id, "Linesheet format approved as CSV", "Client preference", 15.0)

        m2_id = self.db.create_meeting(title="Budget Planning 2026", capture_mode="both")
        self.db.add_transcript_segment(m2_id, 0, 5.0, 15.0, "Sarah", "Cloud infrastructure cap set to forty thousand.")
        self.db.add_decision(m2_id, "AWS cloud budget capped at $40,000", "Cost control", 10.0)

        intel = MeetingIntelligenceEngine(self.db, llm_engine=None)
        all_meetings = self.db.list_meetings()

        # Scoped query to m1
        cits_scoped = intel._retrieve_citations(
            question="What format for the linesheet?",
            tokens=["linesheet", "format"],
            intent=QueryIntent.DECISIONS,
            target_meeting_id=m1_id,
            all_meetings=all_meetings,
        )
        self.assertTrue(len(cits_scoped) > 0)
        for c in cits_scoped:
            self.assertEqual(c["meeting_id"], m1_id)

        # Unscoped / cross-meeting query
        cits_cross = intel._retrieve_citations(
            question="What is the AWS budget cap?",
            tokens=["budget", "cap", "aws"],
            intent=QueryIntent.DECISIONS,
            target_meeting_id=None,
            all_meetings=all_meetings,
        )
        self.assertTrue(len(cits_cross) > 0)
        found_budget = any(c["meeting_id"] == m2_id for c in cits_cross)
        self.assertTrue(found_budget, "Cross-meeting retrieval must locate evidence in meeting #2")

    def test_ask_vox_ui_citations_callback_signature(self):
        """Verify MainWindow._on_ask_vox_finished calls _on_ask_vox_citations with query_id without TypeError."""
        import inspect
        from voxlocal.ui.main_window import MainWindow

        # Verify signature has query_id and citations
        sig = inspect.signature(MainWindow._on_ask_vox_citations)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["self", "query_id", "citations"])

        # Test simulated call via mock
        dummy = MagicMock()
        dummy._current_ask_vox_id = 42
        dummy._ask_vox_buffer = ""
        dummy.citations_list.count.return_value = 0
        dummy._on_ask_vox_citations = MagicMock()

        # Invoke real unbound _on_ask_vox_finished
        res = {"answer": "Summary text", "citations": [{"meeting_id": 1, "text": "Quote"}]}
        MainWindow._on_ask_vox_finished(dummy, 42, res)

        dummy._on_ask_vox_citations.assert_called_once_with(42, res["citations"])


if __name__ == "__main__":
    unittest.main()

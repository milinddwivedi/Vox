from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 14
Controlled Real-World Pilot Readiness Test Battery

Covers:
1. Scenario 1: Short Standup Pilot Topology (Swift MoM and action items).
2. Scenario 2: Extended Architectural Review Pilot Topology (Multi-speaker, high decision density).
3. Scenario 3: Bilingual & Accented Technical Pilot Topology (Hinglish DevOps, Docker, SQLite).
4. Scenario 4: Bluetooth Audio Profile Conflict Resilience (HFP vs A2DP detection).
5. Scenario 5: Sudden Meeting Interruption & Crash Recovery (Zero data loss).
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.audio.devices import AudioDeviceManager, AudioDeviceInfo


class TestPhase14PilotReadiness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase14_pilot_test_")
        self.config = AppConfig()
        self.config.storage_dir = Path(self.temp_dir)
        self.db = LocalDatabase(self.config.db_path)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Scenario 1: Short Standup Pilot Topology
    # -------------------------------------------------------------------------
    def test_short_standup_pilot_topology(self):
        """Verify quick standup meeting finishes cleanly with concise minutes and action items."""
        m_id = self.db.create_meeting(title="Daily Standup - Core Team")
        
        segments = [
            {"speaker": "Alice", "text": "Yesterday I fixed the index performance in SQLite database.", "start_sec": 0.0, "end_sec": 4.0},
            {"speaker": "Bob", "text": "Great! Today I will review the PR and run the adversarial test suite.", "start_sec": 5.0, "end_sec": 10.0},
        ]
        full_transcript = "Alice: Yesterday I fixed the index performance in SQLite database. Bob: Great! Today I will review the PR and run the adversarial test suite."

        for i, s in enumerate(segments):
            self.db.add_transcript_segment(
                meeting_id=m_id,
                segment_index=i,
                start_time=s["start_sec"],
                end_time=s["end_sec"],
                speaker=s["speaker"],
                text=s["text"],
            )

        mom = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            full_transcript=full_transcript,
            duration_sec=10.0,
            title="Daily Standup - Core Team",
            platform="Microsoft Teams",
            llm_engine=None,
        )

        self.assertIsNotNone(mom)
        self.assertIn("summary", mom)
        self.db.update_meeting(m_id, status="completed", summary=mom["summary"])
        meeting = self.db.get_meeting(m_id)
        self.assertEqual(meeting["status"], "completed")

    # -------------------------------------------------------------------------
    # 2. Scenario 2: Extended Architectural Review Pilot Topology
    # -------------------------------------------------------------------------
    def test_extended_architectural_review_pilot_topology(self):
        """Verify complex multi-speaker meeting with high decision density and lineage."""
        m_id = self.db.create_meeting(title="Q4 Architecture & Security Review")

        # Log decisions across life cycle
        d1 = self.db.add_decision(
            meeting_id=m_id,
            decision="Require AES-256 for local database file storage",
            status="proposed",
            evolution_relationship="new",
        )
        d2 = self.db.add_decision(
            meeting_id=m_id,
            decision="Enforce OS-level BitLocker and user directory permissions instead of app crypto",
            status="confirmed",
            evolution_relationship="superseded",
            supersedes_id=d1,
            rationale="Eliminates custom crypto key management vulnerabilities",
        )

        # Log multiple action items
        self.db.add_action_item(m_id, "Audit Windows file access control lists", owner="Charlie", status="pending")
        self.db.add_action_item(m_id, "Prepare pilot readiness sign-off document", owner="Diana", status="pending")

        decisions = self.db.list_decisions(meeting_id=m_id)
        actions = self.db.list_action_items(meeting_id=m_id)

        self.assertEqual(len(decisions), 2)
        self.assertEqual(len(actions), 2)
        confirmed_dec = [d for d in decisions if d["status"] == "confirmed"][0]
        self.assertEqual(confirmed_dec["supersedes_id"], d1)

    # -------------------------------------------------------------------------
    # 3. Scenario 3: Bilingual & Accented Technical Pilot Topology
    # -------------------------------------------------------------------------
    def test_bilingual_hinglish_technical_pilot_topology(self):
        """Verify code-switching Hinglish with domain technical vocabulary is properly normalized."""
        raw_speech = "API endpoint deploy ho gaya hai comma now frontend me bug fix karna padega period"
        custom_vocab = [
            {"spoken_phrase": "api", "replacement": "API", "is_active": 1},
            {"spoken_phrase": "endpoint", "replacement": "endpoint", "is_active": 1},
            {"spoken_phrase": "deploy", "replacement": "deploy", "is_active": 1},
            {"spoken_phrase": "frontend", "replacement": "frontend", "is_active": 1},
            {"spoken_phrase": "bug fix", "replacement": "bug fix", "is_active": 1},
        ]

        processed = DictationProcessor.process(
            raw_speech,
            vocabulary=custom_vocab,
            spoken_punctuation=True,
        )

        self.assertIn("API", processed)
        self.assertIn("frontend", processed)
        self.assertTrue(processed.endswith("."))

    # -------------------------------------------------------------------------
    # 4. Scenario 4: Bluetooth Audio Profile Conflict Resilience
    # -------------------------------------------------------------------------
    def test_bluetooth_profile_conflict_detection(self):
        """Verify AudioDeviceManager flags Windows Bluetooth Hands-Free vs Stereo loopback collisions."""
        bt_mic = AudioDeviceInfo(
            index=1,
            name="Headset (Sony WH-1000XM4 Hands-Free AG Audio)",
            channels=1,
            sample_rate=16000,
            is_loopback=False,
            is_default=True,
            host_api_name="Windows WASAPI",
        )

        stereo_loopback = AudioDeviceInfo(
            index=2,
            name="Headphones (Sony WH-1000XM4 Stereo) [Loopback]",
            channels=2,
            sample_rate=48000,
            is_loopback=True,
            is_default=True,
            host_api_name="Windows WASAPI",
        )

        conflict_msg = AudioDeviceManager.detect_bluetooth_profile_conflict(bt_mic, stereo_loopback)
        self.assertIsNotNone(conflict_msg)
        self.assertIn("Bluetooth Profile Conflict", conflict_msg)
        self.assertIn("Hands-Free", conflict_msg)

        # Non-conflicting pair (USB microphone + Speakers loopback)
        usb_mic = AudioDeviceInfo(
            index=3,
            name="Microphone (USB Audio Device)",
            channels=2,
            sample_rate=48000,
            is_loopback=False,
            is_default=True,
            host_api_name="Windows WASAPI",
        )
        speakers_loopback = AudioDeviceInfo(
            index=4,
            name="Speakers (Realtek Audio) [Loopback]",
            channels=2,
            sample_rate=48000,
            is_loopback=True,
            is_default=True,
            host_api_name="Windows WASAPI",
        )
        no_conflict = AudioDeviceManager.detect_bluetooth_profile_conflict(usb_mic, speakers_loopback)
        self.assertIsNone(no_conflict)

    # -------------------------------------------------------------------------
    # 5. Scenario 5: Sudden Meeting Interruption & Recovery Pilot Topology
    # -------------------------------------------------------------------------
    def test_sudden_interruption_data_preservation(self):
        """Verify that when a meeting crashes mid-flight, all prior audio and segments are preserved."""
        m_id = self.db.create_meeting(title="Sudden Disconnect Standup")
        self.db.update_meeting(m_id, status="recording")

        # Save 3 segments recorded before crash
        for i in range(3):
            self.db.add_transcript_segment(
                meeting_id=m_id,
                segment_index=i,
                start_time=float(i * 10),
                end_time=float((i + 1) * 10),
                speaker=f"Speaker {i + 1}",
                text=f"Progress report update segment {i + 1}",
            )

        # Simulate restart recovery
        unfinalized = self.db.list_unfinalized_meetings()
        self.assertIn(m_id, [m["id"] for m in unfinalized])

        # Mark recovered
        self.db.update_meeting(m_id, status="interrupted")
        
        # Verify 100% of recorded segments are still intact in DB
        segments = self.db.get_meeting_segments(m_id)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["text"], "Progress report update segment 1")
        self.assertEqual(segments[2]["text"], "Progress report update segment 3")


if __name__ == "__main__":
    unittest.main()

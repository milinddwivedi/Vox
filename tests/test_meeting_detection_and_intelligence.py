from __future__ import annotations
"""
Unit & Integration Tests for:
1. Meeting Audio Conditioning & Mastered Loudness Harmonization (Real-Time DSP).
2. Layered Automatic Meeting Detection State Machine & Session Deduplication.
3. Interactive Bottom-Right Meeting Prompt Popup Lifecycle.
4. 7-Stage Minutes of Meeting (MoM) Intelligence Engine & 10-Section Output Contract.
"""

from unittest import mock

import os
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    clean_meeting_title,
)
from voxlocal.audio.devices import (
    StudioVocalDSP,
    clean_speech_microphone_audio,
    compute_rms_level,
)
from voxlocal.audio.meeting_recorder import MeetingRecorder, _write_mixed
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline


class TestMeetingAudioOptimization(unittest.TestCase):
    """Verify that Meeting Recording microphone audio is clean, quiet in pauses, and properly leveled."""

    def test_clean_speech_microphone_audio_limits_excessive_peak(self):
        """Microphone audio should be leveled to broadcast dialog standards (~0.62 peak) and not clip."""
        fs = 16000
        t = np.linspace(0, 1.0, fs, endpoint=False)
        # Synthesize loud spoken sentence with peak 1.0
        hot_audio = np.sin(2 * np.pi * 300 * t) * 0.98

        cleaned = clean_speech_microphone_audio(hot_audio, sample_rate=fs, hp_cutoff=85.0, target_peak=0.62)
        peak = float(np.max(np.abs(cleaned)))
        rms = float(np.sqrt(np.mean(cleaned**2)))

        self.assertLessEqual(peak, 0.75, f"Peak {peak} exceeded broadcast comfort ceiling")
        self.assertGreaterEqual(peak, 0.40, f"Peak {peak} was overly attenuated")
        self.assertGreaterEqual(rms, 0.10, "RMS should remain audible")

    def test_low_rumble_and_dc_rejection(self):
        """85Hz highpass filter should eliminate low-frequency rumble (desk thumps, fan vibrations)."""
        fs = 16000
        t = np.linspace(0, 1.0, fs, endpoint=False)
        # 30Hz mechanical vibration + 300Hz vocal formant
        rumble = np.sin(2 * np.pi * 30 * t) * 0.60
        voice = np.sin(2 * np.pi * 300 * t) * 0.30
        noisy_signal = rumble + voice

        cleaned = clean_speech_microphone_audio(noisy_signal, sample_rate=fs, hp_cutoff=85.0)

        # Measure remaining 30Hz energy
        sin_30 = np.sum(cleaned * np.sin(2 * np.pi * 30 * t))
        cos_30 = np.sum(cleaned * np.cos(2 * np.pi * 30 * t))
        energy_30 = (sin_30**2 + cos_30**2) / fs

        sin_voice = np.sum(cleaned * np.sin(2 * np.pi * 300 * t))
        cos_voice = np.sum(cleaned * np.cos(2 * np.pi * 300 * t))
        energy_voice = (sin_voice**2 + cos_voice**2) / fs

        self.assertGreater(energy_voice, energy_30 * 10.0, "Vocal energy must dominate over low rumble")

    def test_mixing_harmonization_preserves_remote_speaker(self):
        """Meeting mixed track must balance local mic and remote attendees without drowning them out."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            mic_path = tmp / "mic_track.wav"
            sys_path = tmp / "system_track.wav"
            mixed_path = tmp / "meeting_mixed.wav"

            fs = 16000
            dur = 2.0
            n_samples = int(fs * dur)
            t = np.linspace(0, dur, n_samples, endpoint=False)

            # Local mic: loud speech
            mic_audio = (np.sin(2 * np.pi * 250 * t) * 0.90 * 32767).astype(np.int16)
            # Remote attendee: normal speech
            sys_audio = (np.sin(2 * np.pi * 500 * t) * 0.50 * 32767).astype(np.int16)

            import wave
            with wave.open(str(mic_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(fs)
                wf.writeframes(mic_audio.tobytes())

            with wave.open(str(sys_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(fs)
                wf.writeframes(sys_audio.tobytes())

            dur_result = _write_mixed(mic_path, sys_path, mixed_path)
            self.assertAlmostEqual(dur_result, dur, delta=0.1)
            self.assertTrue(mixed_path.exists())

            with wave.open(str(mixed_path), "rb") as wf:
                mixed_data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

            peak = float(np.max(np.abs(mixed_data)))
            clipping = np.mean(np.abs(mixed_data) >= 0.98)

            self.assertLessEqual(peak, 0.96, "Mixed audio must not exceed soft saturation ceiling")
            self.assertEqual(clipping, 0.0, "Mixed audio must have zero digital clipping")


class TestLayeredMeetingDetector(unittest.TestCase):
    """Verify state machine transitions, session deduplication, and window title cleaning."""

    def test_clean_meeting_title(self):
        self.assertEqual(
            clean_meeting_title("Daily Standup - Sprint 42 | Microsoft Teams", "Microsoft Teams"),
            "Daily Standup - Sprint 42",
        )
        self.assertEqual(
            clean_meeting_title("Meet - xyz-abcd-uvw - Google Chrome", "Google Meet"),
            "Google Meet: xyz-abcd-uvw",
        )
        self.assertEqual(
            clean_meeting_title("Zoom Meeting", "Zoom"),
            "Zoom Meeting",
        )
        self.assertEqual(
            clean_meeting_title("", "Microsoft Teams"),
            "Microsoft Teams Meeting",
        )

    @mock.patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[(101, "Microsoft Teams", "Teams Meeting | Project Sync", (0, 0, 800, 600))])
    def test_detector_state_machine_and_session_deduplication(self, mock_wins):
        detector = CallDetector()
        self.assertEqual(detector.state, MeetingDetectorState.NOT_RUNNING)

        # 1. Simulate process acquiring microphone
        t0 = 100.0
        users = ["msteams_8wekyb3d8bbwe!teams"]

        # First poll: seen, but below threshold
        ev = detector.update(users, t0)
        self.assertIsNone(ev)

        # Second poll: threshold reached -> triggers "started"
        t1 = t0 + 3.0
        ev = detector.update(users, t1)
        self.assertEqual(ev, "started")
        self.assertEqual(detector.state, MeetingDetectorState.AWAITING_USER_DECISION)
        self.assertIsNotNone(detector.active_session)

        sess_id = detector.active_session.session_id
        self.assertIn(sess_id, detector.prompted_session_ids)

        # 3. Subsequent poll while call continues must NOT re-emit "started" (stable deduplication)
        t2 = t1 + 2.0
        ev2 = detector.update(users, t2)
        self.assertIsNone(ev2, "Ongoing call must not re-trigger new prompt")

        # 4. User marks decision as dismissed ("Not Now")
        detector.mark_decision(sess_id, "dismissed")
        self.assertIn(sess_id, detector.dismissed_session_ids)

        # 5. Microphone released and meeting window closed
        mock_wins.return_value = []
        t3 = t2 + 5.0
        ev3 = detector.update([], t3)
        self.assertIsNone(ev3)  # In grace period

        # 6. Grace period expires -> triggers "ended"
        t4 = t3 + 13.0
        ev4 = detector.update([], t4)
        self.assertEqual(ev4, "ended")
        self.assertEqual(detector.state, MeetingDetectorState.MEETING_ENDED)
        self.assertIsNone(detector.active_session)


class TestMeetingMinutesPipeline(unittest.TestCase):
    """Verify 7-stage MoM intelligence pipeline and 10-section contract generation."""

    def setUp(self):
        self.sample_segments = [
            {
                "speaker": "Milind",
                "text": "Welcome everyone. The agenda for today is architecture review and deployment planning.",
                "start_time": 0.0,
                "end_time": 5.2,
            },
            {
                "speaker": "Priya",
                "text": "Thanks Milind. We agreed to proceed with the local SQLite storage engine for high performance.",
                "start_time": 6.0,
                "end_time": 12.5,
            },
            {
                "speaker": "Alex",
                "text": "That's great. What is our strategy for memory safety during three hour calls?",
                "start_time": 13.0,
                "end_time": 18.0,
            },
            {
                "speaker": "Milind",
                "text": "I will prepare the bounded buffer pipeline by Friday to keep memory under 35 MB.",
                "start_time": 18.5,
                "end_time": 24.0,
            },
            {
                "speaker": "Alex",
                "text": "We have a potential risk with disk write latency if the user records 4K video.",
                "start_time": 25.0,
                "end_time": 30.0,
            },
            {
                "speaker": "Priya",
                "text": "Tentatively we decided to cap video recording at 1080p 15 FPS pending benchmark results.",
                "start_time": 31.0,
                "end_time": 37.0,
            },
            {
                "speaker": "Milind",
                "text": "We should check with Sarah regarding the final UI styling before shipping.",
                "start_time": 38.0,
                "end_time": 43.0,
            },
        ]

    def test_pipeline_generates_10_sections(self):
        result = MeetingMinutesPipeline.generate_minutes(
            segments=self.sample_segments,
            duration_sec=45.0,
            title="PLM Architecture Sync",
            platform="Microsoft Teams",
            session_id="teams_test_001",
        )

        md = result["mom_markdown"]
        self.assertIn("MINUTES OF MEETING (MoM)", md)
        self.assertIn("1. MEETING DETAILS", md)
        self.assertIn("2. EXECUTIVE SUMMARY", md)
        self.assertIn("3. PARTICIPANTS & ROLES", md)
        self.assertIn("4. AGENDA & DISCUSSION BREAKDOWN", md)
        self.assertIn("5. DECISIONS LOG", md)
        self.assertIn("6. ACTION PLAN", md)
        self.assertIn("7. PENDING ITEMS & OPEN QUESTIONS", md)
        self.assertIn("8. RISKS, BLOCKERS & ISSUES", md)
        self.assertIn("9. FOLLOW-UP & NEXT STEPS", md)
        self.assertIn("10. TRANSCRIPT EVIDENCE & VERIFICATION NOTES", md)
        # Verify no lines start with '#' or '*'
        for line in md.splitlines():
            trimmed = line.strip()
            self.assertFalse(trimmed.startswith(("#", "*")), f"Line should not start with '#' or '*': {trimmed}")

    def test_decisions_categorization(self):
        result = MeetingMinutesPipeline.generate_minutes(
            segments=self.sample_segments,
            duration_sec=45.0,
            title="PLM Architecture Sync",
        )
        decisions = result["mom_json"]["decisions"]
        categories = {d["category"] for d in decisions}
        statuses = {d.get("status") for d in decisions}

        self.assertIn("Technical", categories, "Technical decisions category must be detected")
        self.assertTrue("confirmed" in statuses or "Confirmed" in categories, "Confirmed decisions status must be detected")

        # Verify citation quote is preserved
        confirmed_dec = [d for d in decisions if "SQLite" in d.get("evidence_quote", "") or "SQLite" in d.get("decision", "")][0]
        self.assertIn("SQLite", confirmed_dec["evidence_quote"])

    def test_action_item_ownership_and_deadline(self):
        result = MeetingMinutesPipeline.generate_minutes(
            segments=self.sample_segments,
            duration_sec=45.0,
            title="PLM Architecture Sync",
        )
        actions = result["mom_json"]["action_items"]
        self.assertTrue(len(actions) >= 1)

        milind_action = [a for a in actions if a["owner"] == "Milind"][0]
        self.assertEqual(milind_action["owner"], "Milind")
        self.assertEqual(milind_action["due_date"], "By friday")
        self.assertTrue(milind_action["verified"])
        self.assertIn("prepare", milind_action["description"].lower())

    def test_evidence_verification_pass(self):
        result = MeetingMinutesPipeline.generate_minutes(
            segments=self.sample_segments,
            duration_sec=45.0,
            title="PLM Architecture Sync",
        )
        verification = result["mom_json"]["verification"]
        self.assertEqual(verification["confidence_score"], 100.0)
        self.assertEqual(verification["unverified_claims"], 0)
        self.assertGreater(verification["verified_claims"], 0)

    def test_participant_registry_detects_referenced_stakeholder(self):
        result = MeetingMinutesPipeline.generate_minutes(
            segments=self.sample_segments,
            duration_sec=45.0,
            title="PLM Architecture Sync",
        )
        participants = result["mom_json"]["participants"]
        active = [p["name"] for p in participants["active_speakers"]]
        self.assertIn("Milind", active)
        self.assertIn("Priya", active)
        self.assertIn("Alex", active)

        mentioned = [m["name"] for m in participants["mentioned_stakeholders"]]
        self.assertIn("Sarah", mentioned, "Sarah should be identified as a referenced stakeholder")


if __name__ == "__main__":
    unittest.main()

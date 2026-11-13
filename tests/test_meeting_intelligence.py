"""
Unit tests for VoxLocal's Meeting Intelligence Engine.
Verifies offline AI & algorithmic query classification, retrieval,
synthesis, and crash immunity.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine, QueryIntent
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    extract_speaker_embedding,
    speaker_cosine_distance,
)
from voxlocal.storage.database import LocalDatabase
import numpy as np


class TestMeetingIntelligence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_voxlocal.db"
        cls.db = LocalDatabase(cls.db_path)
        cls.engine = MeetingIntelligenceEngine(cls.db)

        # Seed sample meeting data
        cls.m1_id = cls.db.create_meeting(title="Q4 Product Strategy")
        cls.db.update_meeting(
            cls.m1_id,
            duration_sec=300.0,
            summary="Strategic roadmap alignment for mobile client and voice assistant features.",
            topics="Product, Mobile, Audio, Roadmap",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.m1_id,
            segment_index=0,
            start_time=12.5,
            end_time=18.0,
            speaker="Alex",
            text="We must ensure the microphone audio enhancement suppresses all background HVAC hum.",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.m1_id,
            segment_index=1,
            start_time=25.0,
            end_time=32.0,
            speaker="Jordan",
            text="We agreed to launch the 60 Hz notch filter in the next desktop release.",
        )
        cls.db.add_decision(
            meeting_id=cls.m1_id,
            decision="Deploy native 60 Hz ground hum notch filters",
            rationale="Client recordings had background electrical noise",
            timestamp_sec=25.0,
        )
        cls.db.add_action_item(
            meeting_id=cls.m1_id,
            description="Verify AAC audio stream-copy muxing for meeting exports",
            owner="Jordan",
            due_date="Friday",
        )

        cls.m2_id = cls.db.create_meeting(title="Design System Atelier")
        cls.db.update_meeting(
            cls.m2_id,
            duration_sec=120.0,
            summary="Flaky quiet luxury aesthetic review for light and dark modes.",
            topics="Design, Tokens, Themes",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.m2_id,
            segment_index=0,
            start_time=5.0,
            end_time=10.0,
            speaker="Elena",
            text="The Quiet Luxury Obsidian theme should use deep #0c0c0e canvas color.",
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_intent_classification(self):
        intent, tokens, scope = self.engine._classify_query("What were the key decisions in Q4 Product Strategy?")
        self.assertEqual(intent, QueryIntent.DECISIONS)

        intent, tokens, scope = self.engine._classify_query("List all action items and tasks")
        self.assertEqual(intent, QueryIntent.ACTION_ITEMS)

        intent, tokens, scope = self.engine._classify_query("Summarize recent discussions")
        self.assertEqual(intent, QueryIntent.SUMMARY)

        intent, tokens, scope = self.engine._classify_query("What did Elena say about themes?")
        self.assertIn(intent, (QueryIntent.SPEAKER_QA, QueryIntent.TOPIC_QA))

    def test_query_decisions(self):
        res = self.engine.query("What were the key decisions in Q4 Product Strategy?")
        self.assertIn("decisions", res["intent"])
        self.assertTrue(len(res["answer"]) > 20)
        self.assertIn("60 Hz", res["answer"])
        self.assertTrue(len(res["citations"]) > 0)

    def test_query_action_items(self):
        res = self.engine.query("What are the action items?")
        self.assertEqual(res["intent"], "action_items")
        self.assertIn("Jordan", res["answer"])
        self.assertIn("AAC", res["answer"])

    def test_query_topic_qa(self):
        res = self.engine.query("What did we discuss about HVAC hum?")
        self.assertEqual(res["intent"], "topic_qa")
        self.assertIn("Alex", res["answer"])
        self.assertTrue(len(res["citations"]) > 0)

    def test_query_scoped_meeting(self):
        res = self.engine.query("What was discussed?", meeting_id=self.m2_id)
        self.assertIn("Elena", res["answer"])
        self.assertIn("Obsidian", res["answer"])

    def test_crash_immunity_empty_or_special_chars(self):
        # Empty
        res = self.engine.query("")
        self.assertIn("Please ask", res["answer"])

        # Special symbols and non-existent words
        res = self.engine.query("!@#$%^&*()_+ ??? nonexistentword12345")
        self.assertIn("No Direct Matches", res["answer"])
        self.assertEqual(len(res["citations"]), 0)

    def test_speaker_embedding_variation_stability(self):
        sr = 16000
        dur = 0.6
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)

        # Speaker A base voice: 150 Hz harmonic signal (f0 + harmonics)
        spk_a_base = (
            0.4 * np.sin(2 * np.pi * 150 * t)
            + 0.25 * np.sin(2 * np.pi * 300 * t)
            + 0.15 * np.sin(2 * np.pi * 450 * t)
            + 0.10 * np.sin(2 * np.pi * 600 * t)
        ).astype(np.float32)

        # Speaker A mid-sentence pitch modulation (inflection to 170 Hz)
        spk_a_pitch_shift = (
            0.4 * np.sin(2 * np.pi * 170 * t)
            + 0.25 * np.sin(2 * np.pi * 340 * t)
            + 0.15 * np.sin(2 * np.pi * 510 * t)
            + 0.10 * np.sin(2 * np.pi * 680 * t)
        ).astype(np.float32)

        # Speaker B distinctly higher pitch & different resonance: 260 Hz
        spk_b_voice = (
            0.4 * np.sin(2 * np.pi * 260 * t)
            + 0.3 * np.sin(2 * np.pi * 780 * t)
            + 0.2 * np.sin(2 * np.pi * 1300 * t)
            + 0.1 * np.sin(2 * np.pi * 2200 * t)
        ).astype(np.float32)

        emb_a1 = extract_speaker_embedding(spk_a_base)
        emb_a2 = extract_speaker_embedding(spk_a_pitch_shift)
        emb_b = extract_speaker_embedding(spk_b_voice)

        self.assertIsNotNone(emb_a1)
        self.assertIsNotNone(emb_a2)
        self.assertIsNotNone(emb_b)

        dist_intra_speaker = speaker_cosine_distance(emb_a1, emb_a2)
        dist_inter_speaker = speaker_cosine_distance(emb_a1, emb_b)

        # Same speaker pitch change must have very low distance (< 0.15)
        self.assertLess(dist_intra_speaker, 0.15)
        # Distinct speaker must have clear separation (> 0.25)
        self.assertGreater(dist_inter_speaker, 0.25)
        # Ratio of inter to intra must be strong
        self.assertGreater(dist_inter_speaker, dist_intra_speaker * 2.0)

    def test_reconcile_meeting_transcript_glitch_filtering(self):
        transcriber = LocalWhisperTranscriber(models_dir=self.temp_dir.name)
        segs = [
            {"start_time": 0.0, "end_time": 3.0, "speaker": "Speaker 1", "source_track": "system", "text": "Hello everyone, welcome.", "confidence": 0.9},
            {"start_time": 3.2, "end_time": 3.8, "speaker": "Speaker 2", "source_track": "system", "text": "to today's demo", "confidence": 0.85},
            {"start_time": 4.0, "end_time": 7.0, "speaker": "Speaker 1", "source_track": "system", "text": "where we demonstrate accuracy.", "confidence": 0.95},
        ]
        reconciled = transcriber.reconcile_meeting_transcript(segs)

        # The momentary 0.6s middle glitch must be unified into Speaker 1 and merged!
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["speaker"], "Speaker 1")
        self.assertIn("demo", reconciled[0]["text"])
        self.assertEqual(reconciled[0]["end_time"], 7.0)


if __name__ == "__main__":
    unittest.main()

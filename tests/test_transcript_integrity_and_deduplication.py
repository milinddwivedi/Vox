"""
Automated End-to-End Test Suite for VOX Meeting Transcript Integrity & Deduplication.
Validates:
1. Deduplication of overlapping progressive chunks and retry events.
2. In-place extension of boundary fragments without duplicate rows.
3. Preservation of legitimate repeated speech across distinct time intervals and tracks (e.g. affirmations "okay", "yes").
4. Strict non-decreasing chronological ordering of timestamps in DB, progressive feed, and reports.
5. Elimination of Whisper autoregressive prompt hallucinations (e.g. 'We speak in English and Hindi.').
6. Speech physiology & duration rate guard against micro-slice hallucinations (< 0.45s).
7. Fresh meeting transcript isolation (live feed cleared and reset on meeting start).
"""

import os
import sys
import unittest
import tempfile
import numpy as np
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import is_hallucination, LocalWhisperTranscriber
from voxlocal.storage.database import LocalDatabase
from voxlocal.audio.meeting_recorder import MeetingRecorder, TARGET_SAMPLE_RATE


class TestPromptAndSpeechRateGuard(unittest.TestCase):
    def test_build_contextual_prompt_has_no_hallucination_phrases(self):
        """Whisper conditioning prompt must never inject full conversational sentences."""
        prompt_bilingual = DictationProcessor.build_contextual_prompt(
            window_title="Teams Meeting",
            process_name="ms-teams.exe",
            recent_history=[],
            language="en+hi",
        )
        self.assertNotIn("We speak in English", prompt_bilingual)
        self.assertNotIn("नमस्ते, hello!", prompt_bilingual)
        self.assertNotIn("हिंदी", prompt_bilingual)

    def test_extract_window_keywords_filters_ole_artifacts(self):
        """OLE/COM class names and office window artifacts must be filtered from context hotwords."""
        keywords = DictationProcessor.extract_window_keywords(
            window_title="Document1 - Microsoft Office Word MSWordDoc Word.Document.8",
            process_name="WINWORD.EXE",
        )
        for kw in keywords:
            kw_low = kw.lower()
            self.assertNotIn("msworddoc", kw_low)
            self.assertNotIn("word.document", kw_low)
            self.assertNotIn(kw_low, ("winword", "office", "word", "document", "microsoft"))

    def test_hallucination_phrases_rejection(self):
        """Prompt echoes and known silence artifacts must be detected as hallucinations."""
        self.assertTrue(is_hallucination("We speak in English and Hindi."))
        self.assertTrue(is_hallucination("We speak in English and"))
        self.assertTrue(is_hallucination("we speak in english"))
        self.assertTrue(is_hallucination("Hum English aur Hindi me baat karte hain"))
        self.assertTrue(is_hallucination("thank you for watching."))
        self.assertTrue(is_hallucination("Microsoft Office Word Document"))

    def test_speech_rate_guard_on_microslices(self):
        """Physiologically impossible speech rates (< 0.45s with multi-word sentence) must be rejected."""
        # 7 words in 0.25 seconds is > 28 words/sec (impossible human speech)
        self.assertTrue(is_hallucination("We speak in English and Hindi.", duration_sec=0.25))
        self.assertTrue(is_hallucination("I will update the master list right now.", duration_sec=0.35))

        # Legitimate affirmations on valid durations must be preserved
        self.assertFalse(is_hallucination("Okay.", duration_sec=0.40))
        self.assertFalse(is_hallucination("Yes.", duration_sec=0.30))
        self.assertFalse(is_hallucination("Haan.", duration_sec=0.35))
        self.assertFalse(is_hallucination("Bye.", duration_sec=0.30))
        self.assertFalse(is_hallucination("Okay, you guys discuss.", duration_sec=1.80))


class TestDatabaseTranscriptDeduplication(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db = LocalDatabase(self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            try:
                os.remove(self.temp_file.name)
            except Exception:
                pass

    def test_duplicate_chunks_are_deduplicated(self):
        """Identical segments within +- 1.5s on the same track must not create duplicate rows."""
        mtg_id = self.db.create_meeting(title="Deduplication Test")

        # First progressive segment arrives
        id1 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=1,
            start_time=10.0,
            end_time=12.5,
            speaker="Speaker 1",
            text="Let us review the sprint tickets.",
            source_track="system",
        )
        self.assertGreater(id1, 0)

        # Overlapping progressive worker or retry produces identical segment at start_time 10.2s
        id2 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=2,
            start_time=10.2,
            end_time=12.5,
            speaker="Speaker 1",
            text="Let us review the sprint tickets.",
            source_track="system",
        )
        # Should return existing id without inserting a duplicate row
        self.assertEqual(id2, id1)

        segs = self.db.get_meeting_segments(mtg_id)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "Let us review the sprint tickets.")

    def test_chunk_boundary_expansion_updates_in_place(self):
        """When chunk 2 extends a partially cut sentence from chunk 1, it updates in place."""
        mtg_id = self.db.create_meeting(title="Expansion Test")

        # Chunk 1 cut off sentence
        id1 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=1,
            start_time=8.5,
            end_time=10.0,
            speaker="You (Mic)",
            text="We need to deploy",
            source_track="mic",
        )

        # Chunk 2 overlaps and captures the full utterance
        id2 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=2,
            start_time=8.6,
            end_time=12.0,
            speaker="You (Mic)",
            text="We need to deploy the update to production.",
            source_track="mic",
        )
        self.assertEqual(id2, id1)

        segs = self.db.get_meeting_segments(mtg_id)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["text"], "We need to deploy the update to production.")
        self.assertEqual(segs[0]["end_time"], 12.0)

    def test_legitimate_repeated_affirmations_are_preserved(self):
        """Real spoken affirmations ('okay', 'yes') separated in time must be preserved."""
        mtg_id = self.db.create_meeting(title="Repetition Preservation Test")

        # User says 'Okay.' at 5.0s
        id1 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=1,
            start_time=5.0,
            end_time=5.6,
            speaker="You (Mic)",
            text="Okay.",
            source_track="mic",
        )
        # Remote speaker says 'Okay.' at 5.2s (different track -> both preserved)
        id2 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=2,
            start_time=5.2,
            end_time=5.8,
            speaker="Speaker 1",
            text="Okay.",
            source_track="system",
        )
        # User says 'Okay.' again at 15.0s (distinct conversational turn -> preserved)
        id3 = self.db.add_transcript_segment(
            meeting_id=mtg_id,
            segment_index=3,
            start_time=15.0,
            end_time=15.5,
            speaker="You (Mic)",
            text="Okay.",
            source_track="mic",
        )

        self.assertNotEqual(id1, id2)
        self.assertNotEqual(id1, id3)
        segs = self.db.get_meeting_segments(mtg_id)
        self.assertEqual(len(segs), 3)

    def test_get_meeting_segments_strictly_chronological(self):
        """Returned segments must be strictly ordered by start_time ASC, end_time ASC."""
        mtg_id = self.db.create_meeting(title="Sorting Test")

        # Insert out of order
        self.db.add_transcript_segment(mtg_id, 3, 20.0, 25.0, "Speaker 1", "Third turn", "system")
        self.db.add_transcript_segment(mtg_id, 1, 5.0, 8.0, "You (Mic)", "First turn", "mic")
        self.db.add_transcript_segment(mtg_id, 2, 12.0, 15.0, "Speaker 1", "Second turn", "system")

        segs = self.db.get_meeting_segments(mtg_id)
        timestamps = [s["start_time"] for s in segs]
        self.assertEqual(timestamps, [5.0, 12.0, 20.0])


class TestMeetingRecorderWindowSynchronization(unittest.TestCase):
    def test_audio_window_returns_exact_actual_start(self):
        """get_audio_window must return actual_start_sec synchronized to audio sample position."""
        rec = MeetingRecorder()
        from voxlocal.audio.meeting_recorder import RING_SECONDS
        self.assertEqual(RING_SECONDS, 300)

        # Query audio window on empty recorder returns actual start time
        mic, sys_, actual_start = rec.get_audio_window(0.0, 10.0)
        self.assertIsInstance(mic, np.ndarray)
        self.assertIsInstance(sys_, np.ndarray)
        self.assertIsInstance(actual_start, float)
        self.assertEqual(actual_start, 0.0)


class TestTranscriberReconciliationDeduplication(unittest.TestCase):
    def test_reconcile_meeting_transcript_deduplicates_overlapping_turns(self):
        """reconcile_meeting_transcript must suppress duplicate chunk overlaps and sort strictly."""
        transcriber = LocalWhisperTranscriber()

        raw_segments = [
            {"start_time": 10.0, "end_time": 12.0, "speaker": "Speaker 1", "text": "Deployment is complete.", "source_track": "system"},
            {"start_time": 10.2, "end_time": 12.0, "speaker": "Speaker 1", "text": "Deployment is complete.", "source_track": "system"},
            {"start_time": 5.0, "end_time": 7.0, "speaker": "You (Mic)", "text": "Starting the sync.", "source_track": "mic"},
            {"start_time": 14.0, "end_time": 14.3, "speaker": "Speaker 1", "text": "We speak in English and Hindi.", "source_track": "system"},  # micro-slice hallucination
            {"start_time": 16.0, "end_time": 17.5, "speaker": "Speaker 1", "text": "All services healthy.", "source_track": "system"},
        ]

        result = transcriber.reconcile_meeting_transcript(raw_segments)
        # The micro-slice hallucination (0.3s) must be discarded
        # The duplicate at 10.2s must be deduplicated
        # The order must be strictly chronological: 5.0s -> 10.0s -> 16.0s
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["start_time"], 5.0)
        self.assertEqual(result[1]["start_time"], 10.0)
        self.assertEqual(result[2]["start_time"], 16.0)
        self.assertFalse(any("We speak in English" in s["text"] for s in result))


class TestMainWindowLiveFeedIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from voxlocal.core.config import AppConfig
        from voxlocal.core.controller import AppController
        from voxlocal.ui.floating_widget import FloatingWidget
        from voxlocal.ui.main_window import MainWindow

        self.temp_dir = tempfile.mkdtemp(prefix="vox_test_live_")
        self.config = AppConfig(
            storage_dir=self.temp_dir,
            auto_record_calls=False,
            prompt_on_call=False,
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(self.config)
        self.floating_widget = FloatingWidget(
            self.controller,
            on_open_dashboard=lambda tab_idx=0, maximize=False: None,
            on_exit_app=lambda: None,
        )
        self.window = MainWindow(
            self.controller,
            self.floating_widget,
            on_exit_app=lambda: None,
        )
        self.app.processEvents()

        from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
        self.drawer = VoxNotetakerDrawer(self.controller)
        self.app.processEvents()

    def tearDown(self):
        try:
            self.drawer.close()
            self.window.close()
            self.floating_widget.close()
        except Exception:
            pass

    def test_live_feed_cleared_on_recording_start(self):
        """Starting a recording must clear any leftover text and isolate meeting state."""
        from PyQt6.QtWidgets import QListWidgetItem
        self.drawer.list_transcript.addItem(QListWidgetItem("[00:01] Old Speaker: Stale previous meeting text"))
        self.drawer._active_displayed_meeting_id = 999

        # Controller transitions to recording
        self.controller._active_meeting_id = 1000
        self.drawer._on_meeting_state_changed("recording", "Meeting recording active")

        # Live feed must be completely cleared and isolated to new meeting ID
        self.assertEqual(self.drawer.list_transcript.count(), 0)
        self.assertEqual(self.drawer._active_displayed_meeting_id, 1000)

    def test_progressive_segments_rendered_in_order_and_deduplicated(self):
        """Progressive segments must be displayed in chronological order without duplicates."""
        self.controller._active_meeting_id = 1000
        self.drawer._clear_live_transcript(1000)

        # Send segment at 5.0s
        self.drawer._on_progressive_segment(1000, {
            "start_time": 5.0,
            "end_time": 7.0,
            "speaker": "You (Mic)",
            "text": "Hello team.",
            "source_track": "mic",
        })
        # Send duplicate segment at 5.2s
        self.drawer._on_progressive_segment(1000, {
            "start_time": 5.2,
            "end_time": 7.0,
            "speaker": "You (Mic)",
            "text": "Hello team.",
            "source_track": "mic",
        })
        # Send next turn at 10.0s, then an earlier out-of-order delayed segment at 7.5s
        self.drawer._on_progressive_segment(1000, {
            "start_time": 10.0,
            "end_time": 12.0,
            "speaker": "Speaker 1",
            "text": "Hi, let's start.",
            "source_track": "system",
        })
        self.drawer._on_progressive_segment(1000, {
            "start_time": 7.5,
            "end_time": 9.0,
            "speaker": "Speaker 2",
            "text": "Quick audio check.",
            "source_track": "system",
        })

        feed_lines = [self.drawer.list_transcript.item(i).text() for i in range(self.drawer.list_transcript.count())]
        feed_text = "\n".join(feed_lines)
        # "Hello team." must only appear once
        self.assertEqual(feed_text.count("Hello team."), 1)
        # Strict chronological ordering regardless of arrival order
        idx1 = feed_text.find("Hello team.")
        idx_mid = feed_text.find("Quick audio check.")
        idx2 = feed_text.find("Hi, let's start.")
        self.assertGreaterEqual(idx1, 0)
        self.assertLess(idx1, idx_mid)
        self.assertLess(idx_mid, idx2)


if __name__ == "__main__":
    unittest.main()

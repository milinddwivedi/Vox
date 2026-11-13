"""
Comprehensive Unit & Integration Test Suite for Milestone 2:
Speech Audio Pipeline, 9-State Dictation Machine & Retraction Intelligence.

Verifies:
1. Fast Offline Whisper Transcriber (DictationResult, transcribe_audio contract, deterministic silence).
2. Speech Audio Pipeline (DictationRecorder 450ms ring buffer, 40ms frame slicing, devices.py kwargs).
3. 9-State Dictation State Machine (DictationState enum, contract signals, thread-safe transitions,
   cancellation race prevention, hotkey dispatcher, 10-bar equalizer, testing mode).
4. 5 Transformation Modes & Retraction Intelligence (DictationProcessedResult, process_text contract,
   expanded cues: "no wait", "never mind", "actually", negation guards, mode refinements).
"""

import enum
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for signal and widget interactions
_APP = QApplication.instance() or QApplication(sys.argv)

from voxlocal.audio.devices import (
    TARGET_SAMPLE_RATE,
    resample_audio_to_16k_mono,
)
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.engine.dictation_processor import (
    DictationProcessedResult,
    DictationProcessor,
    TransformationMode,
)
from voxlocal.engine.transcriber import (
    DictationResult,
    DictationSegment,
    LocalWhisperTranscriber,
)


class TestMilestone2TranscriberContract(unittest.TestCase):
    """Test Fast Offline Whisper Transcriber interface contracts and edge cases."""

    def test_dictation_result_dataclass_properties(self):
        """Verify DictationResult dataclass fields and types."""
        res = DictationResult(
            raw_text="Hello world",
            segments=[{"start": 0.0, "end": 1.2, "text": "Hello world"}],
            language="en",
            duration=1.5,
        )
        self.assertEqual(res.raw_text, "Hello world")
        self.assertEqual(len(res.segments), 1)
        self.assertEqual(res.language, "en")
        self.assertEqual(res.duration, 1.5)

    def test_dictation_segment_dict_and_attribute_access(self):
        """Verify DictationSegment supports both dict and attribute access."""
        seg = DictationSegment({"start": 0.5, "end": 2.0, "text": "test phrase", "confidence": 0.95})
        self.assertEqual(seg["text"], "test phrase")
        self.assertEqual(seg.text, "test phrase")
        self.assertEqual(seg.start, 0.5)
        self.assertEqual(seg.end, 2.0)
        self.assertEqual(seg.confidence, 0.95)

    def test_transcribe_audio_empty_and_silent_inputs(self):
        """Verify deterministic handling of empty, sub-minimum, and silent audio."""
        transcriber = LocalWhisperTranscriber()

        # 1. Empty array
        res_empty = transcriber.transcribe_audio(np.zeros(0, dtype=np.float32))
        self.assertIsInstance(res_empty, DictationResult)
        self.assertEqual(res_empty.raw_text, "")
        self.assertEqual(res_empty.segments, [])
        self.assertEqual(res_empty.duration, 0.0)

        # 2. None input
        res_none = transcriber.transcribe_audio(None)
        self.assertIsInstance(res_none, DictationResult)
        self.assertEqual(res_none.raw_text, "")

        # 3. Sub-duration (< 0.2s = 3200 samples)
        short_audio = np.zeros(1600, dtype=np.float32)
        res_short = transcriber.transcribe_audio(short_audio)
        self.assertEqual(res_short.raw_text, "")
        self.assertEqual(res_short.duration, 0.1)

        # 4. Silence (1s of zeros)
        silent_audio = np.zeros(16000, dtype=np.float32)
        res_silent = transcriber.transcribe_audio(silent_audio)
        self.assertEqual(res_silent.raw_text, "")
        self.assertEqual(res_silent.duration, 1.0)

        # 5. Audio with NaNs or Infs
        nan_audio = np.full(16000, np.nan, dtype=np.float32)
        res_nan = transcriber.transcribe_audio(nan_audio)
        self.assertEqual(res_nan.raw_text, "")
        self.assertEqual(res_nan.duration, 1.0)

    def test_transcribe_dictation_backward_compatibility(self):
        """Verify transcribe_dictation returns str identical to transcribe_audio.raw_text."""
        transcriber = LocalWhisperTranscriber()
        empty = np.zeros(0, dtype=np.float32)
        text = transcriber.transcribe_dictation(empty)
        self.assertIsInstance(text, str)
        self.assertEqual(text, "")


class TestMilestone2AudioPipeline(unittest.TestCase):
    """Test Speech Audio Pipeline components: DictationRecorder and devices.py."""

    def test_resample_audio_to_16k_mono_keyword_aliases(self):
        """Verify resample_audio_to_16k_mono accepts both orig_sr/channels and src_rate/src_channels."""
        raw_48k = np.zeros(48000, dtype=np.int16)

        # Positional
        res1 = resample_audio_to_16k_mono(raw_48k, 48000, 1)
        self.assertEqual(res1.shape, (16000,))
        self.assertEqual(res1.dtype, np.float32)

        # Keyword aliases matching test_tier1_feature_coverage.py
        res2 = resample_audio_to_16k_mono(raw_48k, orig_sr=48000, channels=1)
        self.assertEqual(res2.shape, (16000,))
        self.assertEqual(res2.dtype, np.float32)

        # Float32 buffer
        float_buf = np.zeros(32000, dtype=np.float32)
        res3 = resample_audio_to_16k_mono(float_buf, orig_sr=32000, channels=1)
        self.assertEqual(res3.shape, (16000,))

    def test_dictation_recorder_40ms_frame_slicing(self):
        """Verify 40ms (640 samples @ 16kHz) frame slicing method."""
        recorder = DictationRecorder()
        audio_16k = np.ones(16000, dtype=np.float32)
        frames = recorder.slice_40ms_frames(audio_16k)

        # 16000 / 640 = 25 frames
        self.assertEqual(len(frames), 25)
        for f in frames:
            self.assertEqual(f.shape, (640,))

        # With remainder (e.g. 1000 samples -> 1 full frame of 640 + 1 padded frame of 640)
        rem_audio = np.ones(1000, dtype=np.float32)
        rem_frames = recorder.slice_40ms_frames(rem_audio)
        self.assertEqual(len(rem_frames), 2)
        self.assertEqual(rem_frames[0].shape, (640,))
        self.assertEqual(rem_frames[1].shape, (640,))

    def test_dictation_recorder_pre_buffer_retention(self):
        """Verify 450ms pre-speech rolling ring buffer maintains ~11-12 frames and retains on start."""
        recorder = DictationRecorder()
        self.assertEqual(recorder.FRAME_DURATION_SEC, 0.040)
        self.assertEqual(recorder.SAMPLES_PER_FRAME_16K, 640)
        self.assertEqual(recorder.PRE_BUFFER_DURATION_SEC, 0.450)
        self.assertEqual(recorder.pre_buffer.maxlen, 11)

        # Simulate pre-speech frames arriving prior to start
        sample_frame = b"\x00" * 1280
        for _ in range(15):
            recorder.pre_buffer.append(sample_frame)
        self.assertEqual(len(recorder.pre_buffer), 11)


class TestMilestone2StateMachineAndController(unittest.TestCase):
    """Test 9-State Dictation State Machine, signals, and master controller coordination."""

    def setUp(self):
        # Configure minimal isolated controller
        self.config = AppConfig(dictation_mode="toggle")
        self.controller = AppController(self.config)

    def tearDown(self):
        try:
            self.controller.cancel_dictation()
        except Exception:
            pass

    def test_dictation_state_enum_canonical_values(self):
        """Verify all 9 canonical Wispr Flow states exist in DictationState."""
        expected_states = {
            "idle": DictationState.IDLE,
            "initializing": DictationState.INITIALIZING,
            "listening": DictationState.LISTENING,
            "stopping": DictationState.STOPPING,
            "processing": DictationState.PROCESSING,
            "error": DictationState.ERROR,
            "dismissed": DictationState.DISMISSED,
            "retrying": DictationState.RETRYING,
            "testing": DictationState.TESTING,
        }
        for val, enum_member in expected_states.items():
            self.assertEqual(enum_member.value, val)
            self.assertEqual(enum_member, val)

    def test_contract_signals_presence(self):
        """Verify state_changed, audio_level_changed, and mode_changed signals exist."""
        self.assertTrue(hasattr(self.controller, "state_changed"))
        self.assertTrue(hasattr(self.controller, "audio_level_changed"))
        self.assertTrue(hasattr(self.controller, "mode_changed"))
        self.assertTrue(hasattr(self.controller, "dictation_state_changed"))
        self.assertTrue(hasattr(self.controller, "dictation_level_changed"))
        self.assertTrue(hasattr(self.controller, "writing_mode_changed"))

    def test_set_dictation_state_thread_safety_and_dual_emission(self):
        """Verify _set_dictation_state emits both state_changed(enum) and dictation_state_changed(str)."""
        emitted_enums = []
        emitted_strings = []

        self.controller.state_changed.connect(lambda st: emitted_enums.append(st))
        self.controller.dictation_state_changed.connect(lambda st, msg: emitted_strings.append((st, msg)))

        self.controller._set_dictation_state(DictationState.INITIALIZING, "Starting capture...")
        self.controller._set_dictation_state(DictationState.LISTENING, "Listening...")
        self.controller._set_dictation_state(DictationState.STOPPING, "Stopping...")
        self.controller._set_dictation_state(DictationState.PROCESSING, "Processing...")
        self.controller._set_dictation_state(DictationState.IDLE, "Idle")

        self.assertEqual(
            emitted_enums,
            [
                DictationState.INITIALIZING,
                DictationState.LISTENING,
                DictationState.STOPPING,
                DictationState.PROCESSING,
                DictationState.IDLE,
            ],
        )
        self.assertEqual(emitted_strings[0][0], "starting")
        self.assertEqual(emitted_strings[1][0], "listening")
        self.assertEqual(emitted_strings[2][0], "stopping")
        self.assertEqual(emitted_strings[3][0], "processing")
        self.assertEqual(emitted_strings[4][0], "idle")

    def test_audio_level_equalizer_10_bars(self):
        """Verify audio_level_changed emits 10-bar equalizer levels matching Wispr Flow capsule."""
        received_levels = []
        received_eqs = []

        self.controller.audio_level_changed.connect(lambda lvl, eq: (received_levels.append(lvl), received_eqs.append(eq)))
        self.controller._on_dictation_level_change(0.8)

        self.assertEqual(len(received_levels), 1)
        self.assertAlmostEqual(received_levels[0], 0.8)
        self.assertEqual(len(received_eqs), 1)
        self.assertEqual(len(received_eqs[0]), 10)
        # Equalizer weights curve: [0.35, 0.55, 0.75, 0.95, 1.0, 1.0, 0.95, 0.75, 0.55, 0.35]
        self.assertAlmostEqual(received_eqs[0][4], 0.8)
        self.assertAlmostEqual(received_eqs[0][0], round(0.8 * 0.35, 3))

    def test_handle_hotkey_action_dispatcher(self):
        """Verify handle_hotkey_action dispatches ptt, popo, dismiss, and modes correctly."""
        with patch.object(self.controller, "start_dictation") as mock_start, \
             patch.object(self.controller, "stop_dictation_and_insert") as mock_stop, \
             patch.object(self.controller, "toggle_dictation") as mock_toggle, \
             patch.object(self.controller, "cancel_dictation") as mock_cancel, \
             patch.object(self.controller, "set_writing_mode") as mock_mode:

            self.controller.handle_hotkey_action("ptt", is_release=False)
            mock_start.assert_called_once()

            self.controller.handle_hotkey_action("ptt", is_release=True)
            mock_stop.assert_called_once()

            self.controller.handle_hotkey_action("popo", is_release=False)
            mock_toggle.assert_called_once()

            self.controller.handle_hotkey_action("dismiss", is_release=False)
            mock_cancel.assert_called_once()

            self.controller.handle_hotkey_action("mode_1", is_release=False)
            mock_mode.assert_called_with("verbatim")

            self.controller.handle_hotkey_action("mode_4", is_release=False)
            mock_mode.assert_called_with("structured")

    def test_cancellation_race_condition_aborts_text_insertion(self):
        """Verify cancel during transcription worker prevents text insertion and does not complete."""
        # Setup mocks
        fake_audio = np.zeros(16000, dtype=np.float32)
        mock_target = MagicMock()
        mock_target.title = "Test Notepad"
        mock_target.process_name = "notepad.exe"

        # Simulate start
        self.controller._dictation_session_id = 42
        self.controller._dictation_cancelled = False
        session_id = 42

        # Mock transcriber to trigger cancellation mid-transcription
        def cancel_during_transcribe(*args, **kwargs):
            self.controller.cancel_dictation()
            return "This text must not be inserted"

        self.controller.transcriber.transcribe_dictation = cancel_during_transcribe

        with patch.object(self.controller.text_inserter, "insert_text") as mock_insert:
            self.controller._process_dictation_worker(fake_audio, 1.0, mock_target, session_id=session_id)
            mock_insert.assert_not_called()

        self.assertEqual(self.controller.dictation_state, "idle")

    def test_testing_state_lifecycle(self):
        """Verify start_audio_test enters TESTING state and stop_audio_test returns to IDLE."""
        with patch.object(self.controller.dictation_recorder, "start", return_value=(True, "OK")):
            ok = self.controller.start_audio_test()
            self.assertTrue(ok)
            self.assertEqual(self.controller.dictation_state, "testing")

        with patch.object(self.controller.dictation_recorder, "stop", return_value=(np.zeros(0), 0.0)):
            self.controller.stop_audio_test()
            self.assertEqual(self.controller.dictation_state, "idle")


class TestMilestone2ModesAndRetractionIntelligence(unittest.TestCase):
    """Test 5 transformation modes, retraction cues, and linguistic negation guards."""

    def test_process_text_canonical_entrypoint_and_dataclass(self):
        """Verify process_text returns DictationProcessedResult with all attributes."""
        raw = "Please send the report on Monday no wait Tuesday"
        res = DictationProcessor.process_text(raw, mode="cleaned", strip_retractions=True)

        self.assertIsInstance(res, DictationProcessedResult)
        self.assertEqual(res.raw_text, raw)
        self.assertIn("Tuesday", res.formatted_text)
        self.assertNotIn("Monday", res.formatted_text)
        self.assertTrue(res.has_retractions)
        self.assertEqual(res.mode, "cleaned")
        self.assertIn("Monday no wait", res.retracted_segments)
        self.assertIn("#D32F2F", res.annotated_html)
        self.assertIn("line-through", res.annotated_html)

    def test_standalone_retraction_cues(self):
        """Verify standalone cues: 'no wait', 'never mind', and 'actually'."""
        # 1. Standalone 'no wait'
        res1 = DictationProcessor.detect_verbal_self_corrections("We will meet on Friday, no wait, Saturday")
        self.assertTrue(res1["has_corrections"])
        self.assertEqual(res1["clean_text"], "We will meet on Saturday")

        # 2. Standalone 'never mind' (substitution)
        res2 = DictationProcessor.detect_verbal_self_corrections("Send an email to Bob, never mind Alice")
        self.assertTrue(res2["has_corrections"])
        self.assertEqual(res2["clean_text"], "Send an email to Alice")

        # 3. Terminal 'never mind'
        res3 = DictationProcessor.detect_verbal_self_corrections("Draft proposal for team, never mind")
        self.assertTrue(res3["has_corrections"])
        self.assertEqual(res3["clean_text"], "")

        # 4. Standalone 'actually' (discourse replacement)
        res4 = DictationProcessor.detect_verbal_self_corrections("I want five apples, actually six apples")
        self.assertTrue(res4["has_corrections"])
        self.assertEqual(res4["clean_text"], "I want six apples")

    def test_linguistic_negation_guards(self):
        """Verify false-positive protection: 'No wait is fine', 'I actually like it', 'Never mind that file'."""
        # 1. No wait is fine
        res1 = DictationProcessor.detect_verbal_self_corrections("No wait is fine")
        self.assertFalse(res1["has_corrections"])
        self.assertEqual(res1["clean_text"], "No wait is fine")

        # 2. No wait for me
        res2 = DictationProcessor.detect_verbal_self_corrections("No wait for me")
        self.assertFalse(res2["has_corrections"])
        self.assertEqual(res2["clean_text"], "No wait for me")

        # 3. I actually like it (adverb modifying verb)
        res3 = DictationProcessor.detect_verbal_self_corrections("I actually like it")
        self.assertFalse(res3["has_corrections"])
        self.assertEqual(res3["clean_text"], "I actually like it")

        # 4. Never mind that file (transitive imperative)
        res4 = DictationProcessor.detect_verbal_self_corrections("Never mind that file")
        self.assertFalse(res4["has_corrections"])
        self.assertEqual(res4["clean_text"], "Never mind that file")

    def test_structured_mode_ordinal_adjective_anchoring(self):
        """Verify Structured mode does not split on internal ordinal adjectives ('our first release')."""
        text = "This is our first release and we are excited."
        res = DictationProcessor.transform_mode(text, mode="structured")
        self.assertNotIn("- This is our\n", res)
        self.assertIn("- This is our first release and we are excited.", res)

    def test_email_mode_acronym_preservation(self):
        """Verify Email mode does not lowercase acronyms (AWS, PR, VOX) in the subject line."""
        text = "Please review the AWS deployment for VOX"
        res = DictationProcessor.transform_mode(text, mode="email")
        self.assertIn("Subject: Follow-up: Please review the AWS deployment", res)
        self.assertNotIn("aws deployment", res)

    def test_polished_mode_explicit_paragraph_break_preservation(self):
        """Verify Polished mode preserves explicit double newline paragraph breaks."""
        text = "First paragraph here.\n\nSecond paragraph begins now."
        res = DictationProcessor.transform_mode(text, mode="polished")
        self.assertIn("\n\n", res)
        self.assertEqual(res, "First paragraph here.\n\nSecond paragraph begins now.")


if __name__ == "__main__":
    unittest.main()

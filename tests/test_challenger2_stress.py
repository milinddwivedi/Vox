"""
Challenger 2 Empirical Stress Test Harness for Milestone 2.
Adversarially attacks:
1. Cancellation race conditions in AppController during background Whisper transcription.
2. Concurrent cancellation storm & session interleaving.
3. 9-state machine transitions (out-of-order, illegal transitions, concurrent cancellation storms).
4. Retraction parser adversarial intelligence (negation guards, active retractions, chained retractions).
5. Transformation modes (Structured mode ordinal shielding).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for Qt signals and widgets
app = QApplication.instance() or QApplication(sys.argv)

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.engine.dictation_processor import DictationProcessedResult, DictationProcessor
from voxlocal.win32.window_focus import TargetWindowInfo


class TestRetractionIntelligenceAdversarial(unittest.TestCase):
    """Stress-tests for Retraction Parser intelligence and linguistic negation guards."""

    def test_negation_guard_no_wait_is_fine(self):
        """'No wait is fine' must NOT trigger retraction."""
        text = "No wait is fine"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"], f"Should not retract for '{text}', got {res}")
        self.assertEqual(res["clean_text"], text)
        self.assertEqual(res["retracted_segments"], [])

    def test_negation_guard_i_actually_like_it(self):
        """'I actually like it' must NOT trigger retraction."""
        text = "I actually like it"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"], f"Should not retract for '{text}', got {res}")
        self.assertEqual(res["clean_text"], text)
        self.assertEqual(res["retracted_segments"], [])

    def test_negation_guard_she_actually_prefers_coffee(self):
        """'She actually prefers coffee' must NOT trigger retraction."""
        text = "She actually prefers coffee"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"])
        self.assertEqual(res["clean_text"], text)

    def test_negation_guard_never_mind_that_file(self):
        """'Never mind that file' must NOT trigger retraction (transitive imperative)."""
        text = "Never mind that file"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"], f"Should not retract for '{text}', got {res}")
        self.assertEqual(res["clean_text"], text)
        self.assertEqual(res["retracted_segments"], [])

    def test_negation_guard_nevermind_the_invoice(self):
        """'Nevermind the invoice' must NOT trigger retraction."""
        text = "Nevermind the invoice"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"])
        self.assertEqual(res["clean_text"], text)

    def test_negation_guard_do_not_delete_that_file(self):
        """'Do not delete that file' must NOT trigger retraction."""
        text = "Do not delete that file"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"])
        self.assertEqual(res["clean_text"], text)

    def test_retraction_meeting_on_monday_no_wait_tuesday(self):
        """'Meeting on Monday no wait Tuesday' MUST retract 'Monday'."""
        text = "Meeting on Monday no wait Tuesday"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], f"Must detect retraction for '{text}'")
        self.assertNotIn("Monday", res["clean_text"])
        self.assertIn("Tuesday", res["clean_text"])
        self.assertEqual(res["clean_text"], "Meeting on Tuesday")
        self.assertIn("#D32F2F", res["annotated_html"])
        self.assertIn("line-through", res["annotated_html"])

    def test_retraction_send_to_alice_never_mind_bob(self):
        """'Send to Alice never mind Bob' MUST retract 'Alice'."""
        text = "Send to Alice never mind Bob"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], f"Must detect retraction for '{text}'")
        self.assertNotIn("Alice", res["clean_text"])
        self.assertIn("Bob", res["clean_text"])
        self.assertEqual(res["clean_text"], "Send to Bob")
        self.assertIn("#D32F2F", res["annotated_html"])

    def test_retraction_draft_report_actually_summary(self):
        """'Draft report actually summary' MUST retract 'report'."""
        text = "Draft report actually summary"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], f"Must detect retraction for '{text}'")
        self.assertNotIn("report", res["clean_text"])
        self.assertIn("summary", res["clean_text"])
        self.assertEqual(res["clean_text"], "Draft summary")
        self.assertIn("#D32F2F", res["annotated_html"])

    def test_chained_retraction_quad(self):
        """'Monday no wait Tuesday scratch that Wednesday actually Thursday' MUST retract sequentially."""
        text = "Monday no wait Tuesday scratch that Wednesday actually Thursday"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], f"Must detect corrections for '{text}'")
        self.assertNotIn("Monday", res["clean_text"])
        self.assertNotIn("Tuesday", res["clean_text"])
        self.assertNotIn("Wednesday", res["clean_text"])
        self.assertIn("Thursday", res["clean_text"])
        self.assertEqual(res["clean_text"], "Thursday")
        self.assertIn("#D32F2F", res["annotated_html"])

    def test_structured_mode_ordinal_shield(self):
        """'Our first priority is customer delight' must NOT split into multiple bullet items."""
        text = "Our first priority is customer delight"
        formatted = DictationProcessor.transform_mode(text, mode="structured")
        lines = [line.strip() for line in formatted.strip().split("\n") if line.strip()]
        # Must not split into Outline bullets like "Key Points & Outline:" with multiple items
        self.assertNotIn("Key Points & Outline:", formatted)
        # Should be a single bullet item: "- Our first priority is customer delight"
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("- Our first priority is customer delight"))

    def test_structured_mode_ordinal_the_second_option(self):
        """'The second option is better' must NOT split."""
        text = "The second option is better"
        formatted = DictationProcessor.transform_mode(text, mode="structured")
        self.assertNotIn("Key Points & Outline:", formatted)
        self.assertTrue(formatted.startswith("- The second option is better"))

    def test_process_text_contract_signature(self):
        """Validate process_text return structure."""
        res = DictationProcessor.process_text("Meeting on Monday no wait Tuesday", mode="clean")
        self.assertIsInstance(res, DictationProcessedResult)
        self.assertTrue(res.has_retractions)
        self.assertIn("Meeting on Tuesday", res.formatted_text)
        self.assertIn("#D32F2F", res.annotated_html)


class TestStateMachineAdversarial(unittest.TestCase):
    """Adversarially tests 9-state machine transitions, invalid transitions, and concurrency."""

    def setUp(self):
        self.config = AppConfig()
        self.controller = AppController(self.config)
        # Mock external dependencies that touch hardware
        self.controller.dictation_recorder = MagicMock()
        self.controller.dictation_recorder.is_recording = False
        self.controller.dictation_recorder.start.return_value = (True, "OK")
        self.controller.dictation_recorder.stop.return_value = (np.zeros(16000, dtype=np.float32), 1.0)
        self.controller.transcriber = MagicMock()
        self.controller.text_inserter = MagicMock()
        self.controller.text_inserter.insert_text.return_value = (True, "clipboard_paste")
        self.controller.db = MagicMock()
        self.controller.db.list_vocabulary.return_value = []
        self.controller.db.list_snippets.return_value = []
        self.controller.db.list_dictation_history.return_value = []
        self.controller.db.add_dictation_entry.return_value = 1

    def test_all_9_canonical_states_exist(self):
        expected_states = {
            "idle", "initializing", "listening", "stopping", "processing",
            "error", "dismissed", "retrying", "testing"
        }
        actual_states = {s.value for s in DictationState}
        self.assertEqual(actual_states, expected_states)

    def test_out_of_order_state_transitions(self):
        """Fire non-linear out-of-order state transitions and verify state consistency and signals."""
        received_states = []
        self.controller.state_changed.connect(lambda s: received_states.append(s))

        # 1. STOPPING while IDLE via _set_dictation_state
        self.controller._set_dictation_state(DictationState.STOPPING, "Direct test")
        self.assertEqual(self.controller.dictation_state, "stopping")
        self.assertEqual(received_states[-1], DictationState.STOPPING)

        # 2. PROCESSING while LISTENING
        self.controller._set_dictation_state(DictationState.LISTENING)
        self.assertEqual(self.controller.dictation_state, "listening")
        self.controller._set_dictation_state(DictationState.PROCESSING)
        self.assertEqual(self.controller.dictation_state, "processing")

        # 3. TESTING while LISTENING
        self.controller._set_dictation_state(DictationState.LISTENING)
        self.controller._set_dictation_state(DictationState.TESTING)
        self.assertEqual(self.controller.dictation_state, "testing")

        # 4. RETRYING
        self.controller._set_dictation_state(DictationState.RETRYING)
        self.assertEqual(self.controller.dictation_state, "retrying")

        # 5. ERROR
        self.controller._set_dictation_state(DictationState.ERROR)
        self.assertEqual(self.controller.dictation_state, "error")

        # 6. DISMISSED then IDLE
        self.controller._set_dictation_state(DictationState.DISMISSED)
        self.assertEqual(self.controller.dictation_state, "dismissed")
        self.controller._set_dictation_state(DictationState.IDLE)
        self.assertEqual(self.controller.dictation_state, "idle")

    def test_illegal_out_of_order_stop_while_idle(self):
        """Calling stop_dictation_and_insert while in IDLE must be a no-op."""
        self.controller._set_dictation_state(DictationState.IDLE)
        self.controller.stop_dictation_and_insert()
        self.assertEqual(self.controller.dictation_state, "idle")

    def test_double_start_dictation_guard(self):
        """Calling start_dictation when already LISTENING or INITIALIZING must return False."""
        target_info = TargetWindowInfo(hwnd=1234, title="Notepad", process_id=100, thread_id=200, process_name="notepad.exe")
        self.controller._get_current_dictation_target = lambda: target_info
        self.controller._resolve_dictation_target = lambda: target_info

        ok1 = self.controller.start_dictation()
        self.assertTrue(ok1)
        self.assertEqual(self.controller.dictation_state, "listening")

        # Second start attempt while already listening
        ok2 = self.controller.start_dictation()
        self.assertFalse(ok2, "Double start_dictation must be rejected")
        self.assertEqual(self.controller.dictation_state, "listening")

        self.controller.cancel_dictation()
        self.assertEqual(self.controller.dictation_state, "idle")

    def test_audio_test_transitions(self):
        """Audio test mode must transition to TESTING and back to IDLE without text insertion."""
        self.controller._set_dictation_state(DictationState.IDLE)
        ok = self.controller.start_audio_test()
        self.assertTrue(ok)
        self.assertEqual(self.controller.dictation_state, "testing")

        self.controller.stop_audio_test()
        self.assertEqual(self.controller.dictation_state, "idle")
        self.controller.text_inserter.insert_text.assert_not_called()

    def test_equalizer_parabolic_levels_emission(self):
        """Verify that audio level change emits 10-bar parabolic center-weighted equalizer levels."""
        emitted_levels = []
        self.controller.audio_level_changed.connect(lambda lvl, eq: emitted_levels.append((lvl, eq)))

        self.controller._on_dictation_level_change(0.8)
        self.assertEqual(len(emitted_levels), 1)
        lvl, eq = emitted_levels[0]
        self.assertEqual(lvl, 0.8)
        self.assertEqual(len(eq), 10)
        # Center bars (index 4, 5) must be higher than outer bars (index 0, 9)
        self.assertGreater(eq[4], eq[0])
        self.assertGreater(eq[5], eq[9])

    def test_concurrent_cancel_calls_storm(self):
        """Multiple concurrent cancel calls must not deadlock or corrupt state."""
        self.controller._set_dictation_state(DictationState.LISTENING)

        errors = []
        def call_cancel():
            try:
                for _ in range(20):
                    self.controller.cancel_dictation()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_cancel) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.controller.dictation_state, "idle")

    def test_cancellation_race_during_whisper_transcription(self):
        """
        Adversarial Race Condition:
        Trigger start_dictation(), then stop_dictation_and_insert() which spawns worker thread.
        Inside worker thread, simulate slow Whisper inference.
        Immediately fire cancel_dictation().
        Verify with 100% certainty that NO text is injected into text inserter.
        """
        transcription_event = threading.Event()
        finish_transcribe_event = threading.Event()
        inserted_texts = []

        def slow_transcribe(*args, **kwargs):
            transcription_event.set()
            # Wait for main thread to cancel dictation
            finish_transcribe_event.wait(timeout=2.0)
            return "This text should NEVER be inserted"

        self.controller.transcriber.transcribe_dictation = slow_transcribe

        def fake_insert(text, *args, **kwargs):
            inserted_texts.append(text)
            return True, "test_method"

        self.controller.text_inserter.insert_text = fake_insert

        target_info = TargetWindowInfo(hwnd=1234, title="Notepad", process_id=100, thread_id=200, process_name="notepad.exe")
        self.controller._get_current_dictation_target = lambda: target_info
        self.controller._resolve_dictation_target = lambda: target_info

        # 1. Start dictation
        ok = self.controller.start_dictation()
        self.assertTrue(ok)
        self.assertEqual(self.controller.dictation_state, "listening")

        # 2. Stop dictation (triggers background worker)
        self.controller.stop_dictation_and_insert()

        # 3. Wait until worker thread is inside transcribe_dictation
        self.assertTrue(transcription_event.wait(timeout=2.0), "Worker failed to enter transcriber")

        # 4. Fire cancel_dictation while Whisper is actively working
        self.controller.cancel_dictation()
        self.assertTrue(self.controller._dictation_cancelled)
        self.assertEqual(self.controller.dictation_state, "idle")

        # 5. Allow slow transcribe to complete
        finish_transcribe_event.set()

        # Give worker thread a moment to finish
        time.sleep(0.1)

        # 6. Verify zero insertions occurred!
        self.assertEqual(inserted_texts, [], "CRITICAL: Text was injected despite cancellation!")
        self.assertEqual(self.controller.dictation_state, "idle")


class TestCancellationStressStorm(unittest.TestCase):
    """Rapid repeated start-then-cancel cycles to ensure zero leaking insertions under timing jitter."""

    def setUp(self):
        self.config = AppConfig()
        self.controller = AppController(self.config)
        self.controller.dictation_recorder = MagicMock()
        self.controller.dictation_recorder.is_recording = False
        self.controller.dictation_recorder.start.return_value = (True, "OK")
        self.controller.dictation_recorder.stop.return_value = (np.ones(16000, dtype=np.float32), 1.0)
        self.controller.db = MagicMock()
        self.controller.db.list_vocabulary.return_value = []
        self.controller.db.list_snippets.return_value = []
        self.controller.db.list_dictation_history.return_value = []
        self.controller.db.add_dictation_entry.return_value = 1

    def test_rapid_cancellation_jitter_storm(self):
        """Run 50 rapid start-stop-cancel iterations with varied jitter."""
        inserted_texts = []
        self.controller.text_inserter = MagicMock()
        self.controller.text_inserter.insert_text.side_effect = lambda text, **kw: (inserted_texts.append(text) or (True, "mock"))

        def worker_transcribe(*args, **kwargs):
            time.sleep(0.005)  # 5ms simulated inference
            return "Adversarial text injection attempt"

        self.controller.transcriber = MagicMock()
        self.controller.transcriber.transcribe_dictation = worker_transcribe

        target_info = TargetWindowInfo(hwnd=1234, title="Notepad", process_id=100, thread_id=200, process_name="notepad.exe")
        self.controller._get_current_dictation_target = lambda: target_info
        self.controller._resolve_dictation_target = lambda: target_info

        for i in range(50):
            self.controller.start_dictation()
            self.controller.stop_dictation_and_insert()
            # Random jitter before cancel: 0 to 4ms
            if i % 3 == 0:
                time.sleep(0.002)
            elif i % 3 == 1:
                time.sleep(0.004)
            self.controller.cancel_dictation()

        # Wait for all background threads to drain
        time.sleep(0.3)

        self.assertEqual(inserted_texts, [], f"Leaked {len(inserted_texts)} insertions during cancellation storm!")
        self.assertEqual(self.controller.dictation_state, "idle")

    def test_session_interleaving_new_session_supersedes_stale_worker(self):
        """
        Verify session interleaving:
        Session 1 starts, worker begins transcribe.
        User cancels Session 1 and starts Session 2.
        Session 1 worker must NOT inject text into Session 2's target or overwrite state!
        """
        session_1_event = threading.Event()
        session_1_release = threading.Event()
        inserted_texts = []

        self.controller.text_inserter = MagicMock()
        self.controller.text_inserter.insert_text.side_effect = lambda text, **kw: (inserted_texts.append(text) or (True, "mock"))

        def transcribe_hook(audio, *args, **kwargs):
            if audio[0] == 1.0:  # Session 1 audio
                session_1_event.set()
                session_1_release.wait(timeout=2.0)
                return "Session 1 Stale Transcription"
            else:  # Session 2 audio
                return "Session 2 Valid Transcription"

        self.controller.transcriber = MagicMock()
        self.controller.transcriber.transcribe_dictation = transcribe_hook

        target_info = TargetWindowInfo(hwnd=1234, title="Notepad", process_id=100, thread_id=200, process_name="notepad.exe")
        self.controller._get_current_dictation_target = lambda: target_info
        self.controller._resolve_dictation_target = lambda: target_info

        # 1. Start & Stop Session 1 (audio marked with 1.0)
        self.controller.dictation_recorder.stop.return_value = (np.ones(16000, dtype=np.float32) * 1.0, 1.0)
        self.controller.start_dictation()
        self.controller.stop_dictation_and_insert()

        # 2. Wait until Session 1 is in transcribe
        self.assertTrue(session_1_event.wait(timeout=2.0))

        # 3. User cancels Session 1
        self.controller.cancel_dictation()
        self.assertEqual(self.controller.dictation_state, "idle")

        # 4. User starts Session 2 (audio marked with 2.0)
        self.controller.dictation_recorder.stop.return_value = (np.ones(16000, dtype=np.float32) * 2.0, 1.0)
        self.controller.start_dictation()
        self.controller.stop_dictation_and_insert()

        # 5. Now release Session 1
        session_1_release.set()

        # Wait for workers to settle
        time.sleep(0.3)

        # 6. Verify Session 1 was aborted, and only Session 2 succeeded
        self.assertNotIn("Session 1 Stale Transcription", inserted_texts)
        self.assertTrue(any("Session 2 Valid Transcription" in t for t in inserted_texts))


if __name__ == "__main__":
    unittest.main()

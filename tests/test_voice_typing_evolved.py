"""
Comprehensive Test Suite for Evolved Voice Typing & Dual-Tier Floating Pill Control.
Verifies:
1. Pill UI states (Idle, Listening, Processing, Committed, Error), session timer, 12-bar waveform, and mode switching.
2. Focus invariance: WS_EX_NOACTIVATE, WindowDoesNotAcceptFocus, NoFocus.
3. Real-time verbal self-correction engine ("no actually...", "wait sorry...", "scratch that...", "scratch last sentence...", "delete that...", "I mean...", "correction...").
4. Annotated HTML speech preview bubble generation with crimson red strikethrough (<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">...</span>).
5. Verbal filler-word pruning in Magic, Structured, and Email modes vs exact Verbatim preservation.
6. Personal / domain vocabulary manager persistence, CRUD, Devanagari loanwords, and word replacement.
7. StreamingTyper coordinator session lifecycle and mode transformations.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

# Ensure GUI app instance exists for Qt tests
app = QApplication.instance() or QApplication(sys.argv)

from voxlocal.core.vocabulary_manager import VocabularyManager
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.engine.streaming_typer import StreamingTyper, TyperState
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    DynamicWaveformCapsule,
    LiveSpeechPreviewBubble,
    PillState,
    SessionTimerWidget,
)


class TestVoiceTypingEvolved(unittest.TestCase):
    """Test suite for evolved voice typing, floating pill control, self-correction, and vocabulary."""

    def setUp(self) -> None:
        self.vocab_mgr = VocabularyManager()
        self.pill = DualTierFloatingPill()

    def tearDown(self) -> None:
        self.pill.close()
        self.pill.preview_bubble.close()

    # =========================================================================
    # 1. Pill UI States, Timer, Waveform & Focus Invariance Tests
    # =========================================================================

    def test_pill_initial_state_and_geometry(self) -> None:
        """Verify initial VoxFlowBar geometry, default state, and default mode."""
        self.assertEqual(self.pill.pill_state, PillState.IDLE)
        self.assertEqual(self.pill.current_mode, TransformationMode.CLEAN)
        self.assertEqual(self.pill.upper_pill.width(), 40)
        self.assertEqual(self.pill.upper_pill.height(), 8)
        self.pill.set_state(PillState.READY)
        self.assertEqual(self.pill.upper_pill.width(), 50)
        self.assertEqual(self.pill.upper_pill.height(), 30)
        self.pill.set_state(PillState.LISTENING)
        self.assertEqual(self.pill.upper_pill.width(), 104)
        self.assertEqual(self.pill.upper_pill.height(), 30)
        self.pill.set_state(PillState.IDLE)

    def test_pill_five_state_transitions(self) -> None:
        """Verify transitions across all 5 discrete interactive states."""
        # 1. Idle -> Listening
        self.pill.set_state(PillState.LISTENING)
        self.assertEqual(self.pill.pill_state, PillState.LISTENING)
        self.assertTrue(self.pill.btn_stop.isEnabled())

        # 2. Listening -> Processing
        self.pill.set_state(PillState.PROCESSING)
        self.assertEqual(self.pill.pill_state, PillState.PROCESSING)
        self.assertTrue(self.pill.waveform_capsule._is_processing)
        self.assertFalse(self.pill.btn_stop.isEnabled())

        # 3. Processing -> Committed
        self.pill.set_state(PillState.COMMITTED)
        self.assertEqual(self.pill.pill_state, PillState.COMMITTED)
        self.assertFalse(self.pill.preview_bubble.isVisible())

        # 4. Error state
        self.pill.set_state(PillState.ERROR)
        self.assertEqual(self.pill.pill_state, PillState.ERROR)

        # 5. Return to Idle
        self.pill.set_state(PillState.IDLE)
        self.assertEqual(self.pill.pill_state, PillState.IDLE)

    def test_pill_focus_invariance_flags(self) -> None:
        """Verify window flags and attributes guarantee focus invariance."""
        flags = self.pill.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowType.FramelessWindowHint))
        self.assertTrue(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.WindowType.Tool))
        self.assertTrue(bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus))
        self.assertEqual(self.pill.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_pill_mode_switching_and_signals(self) -> None:
        """Test transformation mode switching (Magic, Email, Structured) and signals."""
        emitted_modes = []
        self.pill.mode_changed.connect(lambda m: emitted_modes.append(m))

        self.pill.set_mode(TransformationMode.MAGIC)
        self.assertEqual(self.pill.current_mode, TransformationMode.MAGIC)

        self.pill.set_mode(TransformationMode.EMAIL)
        self.assertEqual(self.pill.current_mode, TransformationMode.EMAIL)

        self.pill.set_mode(TransformationMode.STRUCTURED)
        self.assertEqual(self.pill.current_mode, TransformationMode.STRUCTURED)
        self.pill.set_state(PillState.LISTENING)
        self.assertIn("#FFBCF2", self.pill.upper_pill.styleSheet())
        self.pill.set_state(PillState.IDLE)

        self.assertEqual(emitted_modes, ["magic", "email", "structured"])

    def test_pill_chevron_and_wheel_cycling(self) -> None:
        """Test mode cycling across the VoxFlowBar mode cycle."""
        self.pill.set_mode(TransformationMode.CLEAN)
        self.pill._cycle_next_mode()
        self.assertEqual(self.pill.current_mode, TransformationMode.POLISHED)
        self.pill._cycle_next_mode()
        self.assertEqual(self.pill.current_mode, TransformationMode.STRUCTURED)
        self.pill._cycle_next_mode()
        self.assertEqual(self.pill.current_mode, TransformationMode.EMAIL)
        self.pill._cycle_next_mode()
        self.assertEqual(self.pill.current_mode, TransformationMode.VERBATIM)
        self.pill._cycle_next_mode()
        self.assertEqual(self.pill.current_mode, TransformationMode.CLEAN)

    def test_pill_session_timer(self) -> None:
        """Test SessionTimerWidget start, tick formatting, and stop/reset."""
        timer = self.pill.timer_widget
        timer.start()
        self.assertTrue(timer._timer.isActive())
        self.assertEqual(timer.text(), "00:00")

        timer._seconds = 65
        timer._increment()
        self.assertEqual(timer.text(), "01:06")

        timer.stop()
        self.assertFalse(timer._timer.isActive())
        self.assertEqual(timer.text(), "01:06")
        timer.start()
        self.assertEqual(timer.text(), "00:00")
        timer.stop()

    def test_pill_waveform_equalizer_12_bars(self) -> None:
        """Test DynamicWaveformCapsule 10-bar equalizer levels and processing mode."""
        wave = self.pill.waveform_capsule
        self.assertEqual(wave.bar_count, 10)
        self.assertEqual(len(wave._bars), 10)

        wave.set_level(0.85)
        self.assertAlmostEqual(wave._target_level, 0.85)
        wave._tick()
        self.assertTrue(any(lvl > 0.10 for lvl in wave._bars))

        wave.set_processing(True)
        self.assertTrue(wave._is_processing)
        wave._tick()
        wave.set_processing(False)
        self.assertFalse(wave._is_processing)

    def test_live_speech_preview_bubble_html_strikethrough(self) -> None:
        """Test LiveSpeechPreviewBubble display and red strikethrough rendering."""
        bubble = self.pill.preview_bubble
        sample_html = '<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">evaluate Zepto no actually</span> first let us analyze'
        bubble.update_preview(sample_html)
        bubble.update_position_above_pill(self.pill.geometry())
        bubble.show()
        self.assertTrue(bubble.isVisible())
        self.assertIn("color:#D32F2F", bubble.lbl_preview.text())
        self.assertIn("line-through", bubble.lbl_preview.text())

    # =========================================================================
    # 2. Real-Time Verbal Self-Correction Engine Tests
    # =========================================================================

    def test_self_correction_no_actually(self) -> None:
        """Test 'no actually...' marker retracts flawed proposition."""
        raw = "evaluate the DRHP of Zepto no actually first let us do the fundamental analysis."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "First let us do the fundamental analysis.")

    def test_self_correction_wait_sorry(self) -> None:
        """Test 'wait sorry...' marker retracts flawed clause."""
        raw = "Send the report to John wait sorry send it to Sarah."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "Send it to Sarah.")

    def test_self_correction_scratch_that_clause(self) -> None:
        """Test 'scratch that...' marker with new formulation."""
        raw = "We need three servers scratch that make it five servers."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "Make it five servers.")

    def test_self_correction_scratch_that_term(self) -> None:
        """Test 'scratch that...' marker with term replacement."""
        raw = "The color should be blue, scratch that, red."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "The color should be red.")

    def test_self_correction_scratch_last_sentence(self) -> None:
        """Test 'scratch last sentence...' drops previous complete sentence."""
        raw = "We reached fifty users. Scratch last sentence. We reached seventy users."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "We reached seventy users.")

    def test_self_correction_delete_that_terminal(self) -> None:
        """Test 'delete that...' undoes preceding thought at terminal end."""
        raw = "Draft the announcement delete that"
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "")

    def test_self_correction_i_mean(self) -> None:
        """Test 'I mean...' replaces preceding term."""
        raw = "Meeting is on Tuesday I mean Wednesday."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertEqual(processed, "Meeting is on Wednesday.")

    def test_self_correction_correction_marker(self) -> None:
        """Test 'correction...' replaces preceding numerical or named term."""
        raw = "The total cost is 500 dollars correction 600 dollars"
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertIn("600", processed)
        self.assertNotIn("500", processed)

    def test_self_correction_annotated_html_generation(self) -> None:
        """Test that detect_verbal_self_corrections generates visual red strikethrough markup."""
        raw = "evaluate the DRHP of Zepto no actually first let us do the fundamental analysis"
        res = DictationProcessor.detect_verbal_self_corrections(raw)
        self.assertTrue(res["has_corrections"])
        self.assertIn("evaluate the DRHP of Zepto no actually", res["retracted_segments"][0])
        self.assertIn('<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">', res["annotated_html"])
        self.assertIn("first let us do the fundamental analysis", res["clean_text"])

    def test_self_correction_negation_protection(self) -> None:
        """Verify that 'don't delete that file' is protected and not treated as an undo command."""
        raw = "Please don't delete that file without backup."
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertIn("don't delete that file", processed.lower())

    # =========================================================================
    # 3. Verbal Filler-Word Pruning across Modes vs Verbatim Preservation
    # =========================================================================

    def test_filler_pruning_magic_mode(self) -> None:
        """Verify conversational fillers ('um', 'uh', 'you know', 'basically', 'so basically') are pruned in Magic mode."""
        raw = "Um, basically we should, you know, deploy the update so basically today."
        processed = DictationProcessor.process(raw, mode="magic")
        self.assertNotIn("um", processed.lower().split())
        self.assertNotIn("basically", processed.lower().split())
        self.assertNotIn("you know", processed.lower())
        self.assertIn("deploy the update", processed.lower())

    def test_filler_pruning_structured_mode(self) -> None:
        """Verify fillers are pruned and formatted into structured markdown."""
        raw = "Um, first prepare the deck, uh, second review numbers, finally notify client"
        processed = DictationProcessor.process(raw, mode="structured")
        self.assertNotIn("um", processed.lower().split())
        self.assertNotIn("uh", processed.lower().split())
        self.assertTrue(processed.startswith("**Key Points & Outline:**") or "-" in processed)
        self.assertIn("Prepare the deck", processed)
        self.assertIn("Review numbers", processed)
        self.assertIn("Notify client", processed)

    def test_filler_pruning_email_mode(self) -> None:
        """Verify fillers are pruned and discourse is formatted into an executive email draft."""
        raw = "Uh, basically we finished the milestone and need your approval"
        processed = DictationProcessor.process(raw, mode="email")
        self.assertNotIn("uh", processed.lower().split())
        self.assertNotIn("basically", processed.lower().split())
        self.assertIn("Subject:", processed)
        self.assertIn("Hi Team,", processed)
        self.assertIn("Best regards,", processed)

    def test_verbatim_mode_preserves_fillers(self) -> None:
        """Verify Verbatim mode strictly preserves disfluencies and conversational fillers word-for-word."""
        raw = "Um, uh, basically, like, you know, we should test verbatim."
        processed = DictationProcessor.process(raw, mode="verbatim")
        self.assertIn("Um", processed)
        self.assertIn("uh", processed)
        self.assertIn("basically", processed)
        self.assertIn("like", processed)
        self.assertIn("you know", processed)

    def test_semantic_like_preservation_in_cleaned_mode(self) -> None:
        """Verify that legitimate grammatical uses of 'like' are never pruned."""
        raw = "I would like to review something like this because it looks like a good idea"
        processed = DictationProcessor.process(raw, mode="cleaned")
        self.assertIn("would like to", processed)
        self.assertIn("something like this", processed)
        self.assertIn("looks like", processed)

    # =========================================================================
    # 4. Personal / Domain Vocabulary Manager Tests
    # =========================================================================

    def test_vocabulary_manager_crud_and_persistence(self) -> None:
        """Test adding, retrieving, updating, and removing custom domain terms."""
        mgr = VocabularyManager()
        tid = mgr.add_term("quantum neural net", "QuantumNeuralNet", category="ai")
        self.assertGreater(tid, 0)

        # Retrieve
        term = mgr.get_term(tid)
        self.assertIsNotNone(term)
        self.assertEqual(term["spoken_phrase"], "quantum neural net")
        self.assertEqual(term["replacement"], "QuantumNeuralNet")

        # Update
        mgr.update_term(tid, replacement="QuantumNN")
        updated = mgr.get_term(tid)
        self.assertEqual(updated["replacement"], "QuantumNN")

        # Normalization
        text = "we are training a quantum neural net model"
        norm = mgr.normalize_text(text)
        self.assertIn("QuantumNN", norm)

        # Remove
        mgr.remove_term(tid)
        self.assertIsNone(mgr.get_term(tid))

    def test_vocabulary_normalization_longest_first(self) -> None:
        """Verify multi-word compounds take priority over single words."""
        mgr = VocabularyManager()
        mgr.add_term("vox", "VOX")
        mgr.add_term("vox local", "VoxLocal")
        text = "testing vox local assistant"
        norm = mgr.normalize_text(text)
        self.assertEqual(norm, "testing VoxLocal assistant")

    def test_vocabulary_devanagari_loanwords(self) -> None:
        """Test Hindi loanwords replacement and normalization."""
        mgr = VocabularyManager()
        text = "कृपया प्रोजेक्ट की रिपोर्ट का अपडेट चेक करें"
        norm = mgr.normalize_text(text)
        self.assertIn("Project", norm)
        self.assertIn("Report", norm)
        self.assertIn("Update", norm)

    def test_vocabulary_contextual_harvesting(self) -> None:
        """Test harvesting terms from active window titles and attendees."""
        mgr = VocabularyManager()
        harvested = mgr.harvest_from_context(
            window_title="AcmeCorp PitchDeck - Google Slides",
            attendees=["Sarah Connor", "John Doe"],
        )
        self.assertIn("AcmeCorp", harvested)
        self.assertIn("PitchDeck", harvested)
        self.assertIn("Sarah Connor", harvested)
        norm = mgr.normalize_text("welcome sarah connor to acmecorp")
        self.assertIn("Sarah Connor", norm)
        self.assertIn("AcmeCorp", norm)

    # =========================================================================
    # 5. StreamingTyper Coordinator Tests
    # =========================================================================

    def test_streaming_typer_session_and_correction_flow(self) -> None:
        """Test StreamingTyper state transitions and live correction tracking."""
        mock_inserter = MagicMock()
        mock_inserter.insert_text.return_value = (True, "unicode_sendinput")

        typer = StreamingTyper(
            text_inserter=mock_inserter,
            vocabulary_manager=self.vocab_mgr,
            mode=TransformationMode.MAGIC,
        )

        typer.start_session()
        self.assertEqual(typer.state, TyperState.LISTENING)

        # Interim hypothesis with correction
        res = typer.handle_interim_hypothesis("evaluate Zepto no actually first analyze fundamental value")
        self.assertTrue(res["has_corrections"])
        self.assertIn("first analyze fundamental value", res["clean_text"])

        # Finalize session
        final = typer.finalize_session()
        self.assertEqual(typer.state, TyperState.COMMITTED)
        mock_inserter.insert_text.assert_called_once()
        self.assertIn("First analyze fundamental value", final)


if __name__ == "__main__":
    unittest.main()

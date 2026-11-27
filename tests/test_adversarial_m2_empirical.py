"""
Adversarial Stress Test Suite for Milestone 2: Voice Dictation Engine & Floating UI.
Executed by Teamwork Challenger to empirically verify:
1. Five Writing Modes & Invariants
2. Verbal Self-Correction Detection & Negation Guards
3. Multilingual & Hinglish Robustness
4. Win32 Focus Invariance & Geometry
"""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

# Initialize native application if not already initialized
app = QApplication.instance() or QApplication(sys.argv)

from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.engine.streaming_typer import StreamingTyper, TyperState
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    LiveSpeechPreviewBubble,
    PillState,
)
from voxlocal.win32.window_focus import (
    GetWindowLongPtrW,
    GWL_EXSTYLE,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
)


class TestAdversarialM2Empirical(unittest.TestCase):
    """Empirical adversarial stress harness for M2 dictation engine and floating UI."""

    # =========================================================================
    # VECTOR 1: Five Writing Modes & Boundary Edge Cases
    # =========================================================================

    def test_mode_rapid_switching_raw_invariant(self):
        """Stress: Rapidly switch modes 100 times and verify raw text is never corrupted."""
        raw_acoustic = "We need to deploy the new PR merge to staging by tomorrow morning."
        modes = [
            TransformationMode.VERBATIM,
            TransformationMode.CLEAN,
            TransformationMode.POLISHED,
            TransformationMode.STRUCTURED,
            TransformationMode.EMAIL,
        ]

        typer = StreamingTyper()
        for i in range(100):
            chosen_mode = modes[i % len(modes)]
            typer.mode = chosen_mode
            self.assertEqual(typer.mode, chosen_mode)

            # Process without mutating raw
            out = DictationProcessor.process(raw_acoustic, mode=chosen_mode.value)
            self.assertIsNotNone(out)
            self.assertGreater(len(out), 0)
            # Core key terms must remain in output across all modes
            self.assertIn("deploy", out.lower())
            self.assertIn("staging", out.lower())

    def test_compound_spoken_punctuation_and_numbers(self):
        """Stress: Punctuation commands and spoken currency/numbers across modes."""
        speech = "Invoice total is fifty dollars and twenty five cents comma please pay immediately full stop new line Thank you period"

        # Verbatim mode behavior test
        verbatim_out = DictationProcessor.process(speech, mode="verbatim")
        clean_out = DictationProcessor.process(speech, mode="clean")

        # In verbatim mode, exact spoken words and currency are preserved literally
        self.assertIn("fifty dollars", verbatim_out)
        self.assertIn("comma", verbatim_out)
        self.assertIn("full stop", verbatim_out)
        self.assertIn("period", verbatim_out)

        # Direct verification of verbatim mode dispatch requirement
        verbatim_hello = DictationProcessor.process("hello period how are you", mode="verbatim")
        self.assertEqual(verbatim_hello, "hello period how are you")

        # In clean mode, comma and period should be converted
        self.assertIn("$50", clean_out)
        self.assertIn(",", clean_out)
        self.assertIn("\n", clean_out)

    def test_boundary_empty_and_single_char_inputs(self):
        """Stress: Empty inputs, whitespace, None, and single character utterances."""
        empty_cases = ["", "   ", "\t\n\r", "   \n  "]
        for ec in empty_cases:
            res = DictationProcessor.process(ec, mode="clean")
            self.assertEqual(res, "")
            corr = DictationProcessor.detect_verbal_self_corrections(ec)
            self.assertEqual(corr["clean_text"], ec)
            self.assertFalse(corr["has_corrections"])

        single_chars = ["a", "I", "x", "5", "!", ".", "?"]
        for sc in single_chars:
            res = DictationProcessor.process(sc, mode="clean")
            self.assertIsNotNone(res)
            # Capitalization or formatting should not crash
            corr = DictationProcessor.detect_verbal_self_corrections(sc)
            self.assertFalse(corr["has_corrections"])

    # =========================================================================
    # VECTOR 2: Verbal Self-Correction Detection & Negation Guards
    # =========================================================================

    def test_retraction_compound_no_wait_scratch_that(self):
        """Stress: Compound retraction 'no wait scratch that'."""
        input_text = "I will attend on Monday no wait scratch that Tuesday"
        res = DictationProcessor.detect_verbal_self_corrections(input_text)
        print("\n[STRESS] 'no wait scratch that' ->", res)
        # Check if 'Monday' is retracted
        self.assertNotIn("Monday", res["clean_text"], "Preceding target 'Monday' should be retracted")
        self.assertIn("Tuesday", res["clean_text"])

    def test_retraction_scratch_last_sentence(self):
        """Stress: 'scratch last sentence' removes preceding sentence."""
        input_text = "The server is completely broken. Scratch last sentence. The server is healthy."
        res = DictationProcessor.detect_verbal_self_corrections(input_text)
        self.assertNotIn("completely broken", res["clean_text"])
        self.assertIn("healthy", res["clean_text"])
        self.assertTrue(res["has_corrections"])
        self.assertIn('style="color:#D32F2F', res["annotated_html"])

    def test_retraction_negation_guard_false_positives(self):
        """Stress: Negation guard 'this is not a scratch on the car'."""
        guard_cases = [
            "This is not a scratch on the car",
            "Do not delete that file",
            "Please don't scratch that table",
            "Never delete that record from the database",
        ]
        for gc in guard_cases:
            res = DictationProcessor.detect_verbal_self_corrections(gc)
            print(f"\n[STRESS NEGATION] '{gc}' -> has_corrections={res['has_corrections']}, clean='{res['clean_text']}'")
            self.assertFalse(res["has_corrections"], f"Falsely marked as correction: {gc}")
            self.assertEqual(res["clean_text"], gc)

    def test_retraction_negation_guard_edge_has_no_scratch_that(self):
        """Stress: 'This file has no scratch that can be seen'."""
        text = "This file has no scratch that can be seen"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        print(f"\n[STRESS NO SCRATCH] '{text}' -> has_corrections={res['has_corrections']}, clean='{res['clean_text']}'")
        self.assertFalse(res["has_corrections"], "Attributive 'no scratch that' must not trigger retraction")
        self.assertEqual(res["clean_text"], text)
        self.assertEqual(res["retracted_segments"], [])

    def test_retraction_numerical_and_compound_phrases(self):
        """Stress: Numerical and currency retractions with compound markers."""
        cases = [
            ("The price is fifty dollars no wait scratch that sixty dollars", "The price is sixty dollars"),
            ("10 dollars scratch that 20 dollars", "20 dollars"),
            ("fifty dollars no wait scratch that sixty dollars", "sixty dollars"),
            ("I will attend on Monday scratch that no actually Tuesday", "I will attend on Tuesday"),
            ("I will attend on Monday no actually scratch that Tuesday", "I will attend on Tuesday"),
            ("I will attend on Monday wait scratch that Tuesday", "I will attend on Tuesday"),
            ("I will attend on Monday actually wait scratch that Tuesday", "I will attend on Tuesday"),
        ]
        for inp, expected in cases:
            res = DictationProcessor.detect_verbal_self_corrections(inp)
            self.assertTrue(res["has_corrections"], f"Should detect correction in: {inp}")
            self.assertEqual(res["clean_text"], expected, f"Failed on: {inp}")

    def test_red_strikethrough_markup_generation(self):
        """Verify red strikethrough HTML format in LiveSpeechPreviewBubble."""
        text = "Deploy to production wait sorry deploy to staging"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"])
        html = res["annotated_html"]
        self.assertIn('<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">', html)

        bubble = LiveSpeechPreviewBubble()
        try:
            bubble.update_preview(res["annotated_html"])
            # Label should contain the HTML
            self.assertIn("text-decoration:line-through", bubble.lbl_preview.text())
        finally:
            bubble.close()

    # =========================================================================
    # VECTOR 3: Multilingual & Hinglish Robustness
    # =========================================================================

    def test_hinglish_phrases_preservation_and_normalization(self):
        """Stress: Mixed Hindi/English phrases and corporate Hinglish vocabulary."""
        phrases = [
            ("aaj ka meeting prepone karo", "prepone"),
            ("PR merge ho gaya kya", "PR merge"),
            ("screen share dekh rahe ho", "screen share"),
            ("Windchill PLM me BOM check karo", "Windchill PLM"),
            ("maine pull request review kar diya hai", "review"),
        ]
        for phrase, expected_term in phrases:
            clean = DictationProcessor.process(phrase, mode="clean")
            print(f"\n[HINGLISH] '{phrase}' -> '{clean}'")
            self.assertIn(expected_term.lower(), clean.lower())

    def test_devanagari_to_hinglish_transliteration(self):
        """Stress: Devanagari Hindi transliterates into Roman Hinglish with loanword restoration."""
        deva = "स्क्रीन शेयर करके कोड रिव्यू करते हैं"
        roman = DictationProcessor.devanagari_to_roman_hinglish(deva)
        print(f"\n[DEVA->ROMAN] '{deva}' -> '{roman}'")
        self.assertIn("screen share", roman.lower())
        self.assertIn("code review", roman.lower())

    # =========================================================================
    # VECTOR 4: Win32 Focus Invariance & Geometry
    # =========================================================================

    def test_win32_extended_window_styles(self):
        """Verify WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW on pill and bubble."""
        import os
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self.skipTest("Win32 HWND extended styles require native Windows QPA platform.")
        pill = DualTierFloatingPill()
        bubble = LiveSpeechPreviewBubble()
        try:
            pill.show()
            bubble.show()
            app.processEvents()

            hwnd_pill = int(pill.winId())
            hwnd_bubble = int(bubble.winId())

            style_pill = GetWindowLongPtrW(hwnd_pill, GWL_EXSTYLE)
            style_bubble = GetWindowLongPtrW(hwnd_bubble, GWL_EXSTYLE)

            expected_mask = WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
            print(f"\n[WIN32 STYLES] Pill: 0x{style_pill:08X}, Bubble: 0x{style_bubble:08X}")
            print(f"[WIN32 STYLES] Pill has NOACTIVATE: {bool(style_pill & WS_EX_NOACTIVATE)}")
            print(f"[WIN32 STYLES] Bubble has NOACTIVATE: {bool(style_bubble & WS_EX_NOACTIVATE)}")

            self.assertTrue(bool(style_pill & WS_EX_NOACTIVATE), "Pill must have WS_EX_NOACTIVATE")
            self.assertTrue(bool(style_pill & WS_EX_TOPMOST), "Pill must have WS_EX_TOPMOST")
            self.assertTrue(bool(style_pill & WS_EX_TOOLWINDOW), "Pill must have WS_EX_TOOLWINDOW")

            self.assertTrue(bool(style_bubble & WS_EX_NOACTIVATE), "Bubble must have WS_EX_NOACTIVATE")
            self.assertTrue(bool(style_bubble & WS_EX_TOPMOST), "Bubble must have WS_EX_TOPMOST")
            self.assertTrue(bool(style_bubble & WS_EX_TOOLWINDOW), "Bubble must have WS_EX_TOOLWINDOW")
        finally:
            pill.close()
            bubble.close()

    def test_focus_invariance_on_interactions(self):
        """Stress: Verify child controls do not acquire keyboard focus on clicks or drags."""
        pill = DualTierFloatingPill()
        try:
            pill.show()
            app.processEvents()

            # Check Qt focus policy on FlowBar and all interactive child buttons
            self.assertEqual(pill.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.btn_cancel.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.btn_stop.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.btn_check.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.btn_scratchpad.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.btn_undo_toast.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(pill.meeting_chip.focusPolicy(), Qt.FocusPolicy.NoFocus)

            # Programmatically trigger mode changes and clicks
            pill.set_mode(TransformationMode.VERBATIM)
            self.assertEqual(pill.current_mode, TransformationMode.VERBATIM)
            self.assertFalse(pill.hasFocus())

            pill.set_mode(TransformationMode.EMAIL)
            self.assertEqual(pill.current_mode, TransformationMode.EMAIL)
            self.assertFalse(pill.hasFocus())

            pill._cycle_next_mode()
            self.assertEqual(pill.current_mode, TransformationMode.VERBATIM)
            pill.btn_scratchpad.click()
            self.assertFalse(pill.btn_scratchpad.hasFocus())

            # Simulate mouse drag
            from PyQt6.QtCore import QPointF
            press_evt = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(10.0, 10.0),
                QPointF(10.0, 10.0),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pill.mousePressEvent(press_evt)
            self.assertFalse(pill.hasFocus())
        finally:
            pill.close()


if __name__ == "__main__":
    unittest.main()

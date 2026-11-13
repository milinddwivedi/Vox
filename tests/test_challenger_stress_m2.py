"""
Adversarial Challenger Verification Suite for Milestone 2 Remediation.
Tests compound retractions, negation guards, verbatim spoken punctuation,
and floating UI Win32 HWND extended styles.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists
app = QApplication.instance() or QApplication(sys.argv)

from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.floating_pill import DualTierFloatingPill, LiveSpeechPreviewBubble
from voxlocal.win32.window_focus import (
    GetWindowLongPtrW,
    GWL_EXSTYLE,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
)


class TestMilestone2RemediationChallenger(unittest.TestCase):
    """Rigorous empirical stress test harness for M2 remediation."""

    # -------------------------------------------------------------------------
    # Target 3: Compound Verbal Retractions
    # -------------------------------------------------------------------------
    def test_compound_retraction_monday_tuesday(self):
        text = "I will attend on Monday no wait scratch that Tuesday"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], "Must detect correction")
        self.assertNotIn("Monday", res["clean_text"], "Monday must be retracted")
        self.assertIn("Tuesday", res["clean_text"], "Tuesday must be present")
        self.assertEqual(res["clean_text"], "I will attend on Tuesday")
        self.assertTrue(any("Monday no wait scratch that" in seg for seg in res["retracted_segments"]))

    def test_compound_retraction_fifty_dollars_sixty_dollars(self):
        text = "fifty dollars no wait scratch that sixty dollars"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], "Must detect correction")
        self.assertEqual(res["clean_text"], "sixty dollars")
        self.assertTrue(any("fifty dollars no wait scratch that" in seg for seg in res["retracted_segments"]))

    def test_compound_retraction_prefixed_fifty_sixty(self):
        text = "The price is fifty dollars no wait scratch that sixty dollars"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"])
        self.assertEqual(res["clean_text"], "The price is sixty dollars")

    def test_compound_retraction_scratch_that_no_actually(self):
        text = "The meeting is at 2 PM scratch that no actually 4 PM"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"])
        self.assertNotIn("2 PM", res["clean_text"])
        self.assertIn("4 PM", res["clean_text"])

    def test_compound_retraction_actually_wait_scratch_that(self):
        text = "Send it to Bob actually wait scratch that Alice"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"])
        self.assertEqual(res["clean_text"], "Send it to Alice")

    def test_chained_sequential_retractions(self):
        text = "Price is 10 dollars scratch that 20 dollars no wait scratch that 30 dollars"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"])
        self.assertEqual(res["clean_text"], "Price is 30 dollars")
        self.assertGreaterEqual(len(res["retracted_segments"]), 2)

    # -------------------------------------------------------------------------
    # Target 4: Negation Guards & Attributive False-Positive Resistance
    # -------------------------------------------------------------------------
    def test_negation_guard_attributive_has_no_scratch_that(self):
        text = "This file has no scratch that can be seen"
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertFalse(res["has_corrections"], "Attributive 'no scratch that' must not trigger retraction")
        self.assertEqual(res["clean_text"], text)
        self.assertEqual(res["retracted_segments"], [])

    def test_negation_guard_existential_variants(self):
        cases = [
            "There is no scratch that I can detect",
            "The screen has a scratch that bothers me",
            "We found a scratch that needs polishing",
            "I saw no scratch that would cause issues",
        ]
        for c in cases:
            res = DictationProcessor.detect_verbal_self_corrections(c)
            self.assertFalse(res["has_corrections"], f"Falsely triggered correction on: {c}")
            self.assertEqual(res["clean_text"], c)

    def test_negation_guard_modal_prohibitions(self):
        cases = [
            "Please do not delete that folder",
            "Don't scratch that car",
            "Never delete that record from the database",
            "You cannot undo that operation",
        ]
        for c in cases:
            res = DictationProcessor.detect_verbal_self_corrections(c)
            self.assertFalse(res["has_corrections"], f"Falsely triggered correction on: {c}")
            self.assertEqual(res["clean_text"], c)

    def test_valid_retraction_following_prior_negation(self):
        text = "I do not want this. Scratch that, I will take it."
        res = DictationProcessor.detect_verbal_self_corrections(text)
        self.assertTrue(res["has_corrections"], "Retraction after sentence boundary must work")
        self.assertIn("I will take it", res["clean_text"])

    # -------------------------------------------------------------------------
    # Target 5: Verbatim Mode Spoken Punctuation and Literal Preservation
    # -------------------------------------------------------------------------
    def test_verbatim_spoken_punctuation_literal(self):
        text = "hello period how are you"
        res = DictationProcessor.process(text, mode="verbatim")
        self.assertEqual(res, "hello period how are you")

    def test_verbatim_spoken_punctuation_comprehensive(self):
        text = "open quote hello comma world question mark close quote new line next paragraph"
        res = DictationProcessor.process(text, mode="verbatim")
        self.assertEqual(res, text, "Verbatim mode must not transform any spoken punctuation keywords")

    def test_verbatim_numeric_entities_literal(self):
        text = "fifty dollars and twenty five cents"
        res = DictationProcessor.process(text, mode="verbatim")
        self.assertEqual(res, "fifty dollars and twenty five cents")

    def test_verbatim_filler_words_literal(self):
        text = "um uh basically we should start like right now"
        res = DictationProcessor.process(text, mode="verbatim")
        self.assertEqual(res, "um uh basically we should start like right now")

    def test_verbatim_retraction_disabled(self):
        text = "Monday no wait scratch that Tuesday"
        res = DictationProcessor.process(text, mode="verbatim")
        self.assertIn("Monday", res)
        self.assertIn("scratch that", res)
        self.assertIn("Tuesday", res)

    # -------------------------------------------------------------------------
    # Target 1 & Win32 HWND Extended Styles
    # -------------------------------------------------------------------------
    def test_win32_floating_pill_and_bubble_hwnd_styles(self):
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

            self.assertTrue(bool(style_pill & WS_EX_NOACTIVATE), "Pill missing WS_EX_NOACTIVATE")
            self.assertTrue(bool(style_pill & WS_EX_TOPMOST), "Pill missing WS_EX_TOPMOST")
            self.assertTrue(bool(style_pill & WS_EX_TOOLWINDOW), "Pill missing WS_EX_TOOLWINDOW")

            self.assertTrue(bool(style_bubble & WS_EX_NOACTIVATE), "Bubble missing WS_EX_NOACTIVATE")
            self.assertTrue(bool(style_bubble & WS_EX_TOPMOST), "Bubble missing WS_EX_TOPMOST")
            self.assertTrue(bool(style_bubble & WS_EX_TOOLWINDOW), "Bubble missing WS_EX_TOOLWINDOW")
        finally:
            pill.close()
            bubble.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()

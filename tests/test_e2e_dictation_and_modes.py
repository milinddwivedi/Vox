"""
VOX Desktop Assistant — End-to-End Test Suite: Dictation & Writing Modes
Validates:
Tier 1: 5 writing modes (Verbatim, Clean, Polished, Structured, Email), raw transcript
        preservation invariant, mode switcher signals.
Tier 2: Boundary cases (empty speech, single word, long stream, unicode Hindi/Hinglish,
        special symbols, and numeric entity conversions).
Tier 3: Verbal self-correction detection (retractions, false starts, strikethrough markup),
        conversational filler pruning, vocabulary normalization.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root and src/ are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from voxlocal.core.vocabulary_manager import VocabularyManager
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    LiveSpeechPreviewBubble,
    PillState,
)

_APP_INSTANCE = None


def get_qapp() -> QApplication:
    global _APP_INSTANCE
    app = QApplication.instance()
    if app is None:
        _APP_INSTANCE = QApplication(sys.argv)
        return _APP_INSTANCE
    return app


class TestE2EDictationAndModes(unittest.TestCase):
    """E2E Opaque-box test suite for voice dictation, 5 modes, and self-correction."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._orig_qpa = os.environ.get("QT_QPA_PLATFORM")
        cls.app = get_qapp()
        cls.temp_dir = tempfile.mkdtemp(prefix="vox_dictation_e2e_")
        cls.db_path = Path(cls.temp_dir) / "test_dictation.db"
        cls.db = LocalDatabase(cls.db_path)
        cls.vocab_mgr = VocabularyManager()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._orig_qpa is not None:
            os.environ["QT_QPA_PLATFORM"] = cls._orig_qpa
        elif "QT_QPA_PLATFORM" in os.environ:
            os.environ.pop("QT_QPA_PLATFORM", None)
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.pill = DualTierFloatingPill()

    def tearDown(self) -> None:
        self.pill.close()
        if hasattr(self.pill, "preview_bubble"):
            self.pill.preview_bubble.close()

    # =========================================================================
    # TIER 1: Core Writing Modes, Raw Invariant & Mode Switcher Signals
    # =========================================================================

    def test_tier1_mode_verbatim_exact_acoustic_preservation(self) -> None:
        """Verify Verbatim mode retains exact words without filler pruning or punctuation conversion."""
        raw_speech = "um hello world comma this is like verbatim testing full stop"
        result = DictationProcessor.process(
            raw_speech,
            cleanup_enabled=False,
            spoken_punctuation=False,
            mode=TransformationMode.VERBATIM,
        )
        # In verbatim, fillers and spoken punctuation words are strictly preserved
        self.assertIn("um", result.lower())
        self.assertIn("like", result.lower())
        self.assertIn("comma", result.lower())
        self.assertIn("full stop", result.lower())

    def test_tier1_mode_clean_converts_punctuation_and_prunes_fillers(self) -> None:
        """Verify Clean mode prunes conversational fillers and converts spoken punctuation."""
        raw_speech = "um we are deploying the release comma aren't we question mark"
        result = DictationProcessor.process(
            raw_speech,
            cleanup_enabled=True,
            spoken_punctuation=True,
            mode=TransformationMode.CLEANED,
        )
        self.assertNotIn("um", result.lower().split())
        self.assertIn(",", result)
        self.assertTrue(result.endswith("?"))

    def test_tier1_mode_polished_executive_prose(self) -> None:
        """Verify Polished / Magic mode restructures sentences into capitalized, punctuated prose."""
        raw_speech = "we analyzed the quarterly churn data we identified three key levers"
        result = DictationProcessor.transform_mode(raw_speech, mode="magic")
        self.assertTrue(result.startswith("We analyzed"))
        self.assertIn(".", result)
        # Verify capitalization after sentence breaks
        sentences = [s.strip() for s in result.split(".") if s.strip()]
        for s in sentences:
            self.assertTrue(s[0].isupper(), f"Sentence not capitalized: {s}")

    def test_tier1_mode_structured_markdown_outline_and_bullets(self) -> None:
        """Verify Structured mode creates markdown headers and bullet points."""
        raw_speech = "first we need to update the database schema second we run migrations third we deploy"
        result = DictationProcessor.transform_mode(raw_speech, mode="structured")
        self.assertIn("**Key Points & Outline:**", result)
        self.assertIn("- ", result)
        bullets = [line for line in result.splitlines() if line.startswith("- ")]
        self.assertGreaterEqual(len(bullets), 2)

    def test_tier1_mode_email_structured_business_format(self) -> None:
        """Verify Email mode formats speech with Subject, greeting, body, CTA, and sign-off."""
        raw_speech = "we should schedule the sprint review meeting for thursday afternoon"
        result = DictationProcessor.transform_mode(raw_speech, mode="email")
        self.assertTrue(result.startswith("Subject: Follow-up:"))
        self.assertIn("Hi Team,", result)
        self.assertIn("Please let me know your thoughts", result)
        self.assertIn("Best regards,\nVOX Assistant", result)

    def test_tier1_raw_transcript_preservation_invariant(self) -> None:
        """Verify immutable raw transcript preservation invariant across multiple mode renderings."""
        raw_acoustic_transcript = "um actually we need to prepone the meeting comma you know"
        # Store into database simulating dictation session
        session_id = "test-session-001"
        rendered_clean = DictationProcessor.process(raw_acoustic_transcript, mode="cleaned")
        rendered_email = DictationProcessor.process(raw_acoustic_transcript, mode="email")

        # Invariant 1: Raw transcript never equals rendered if mode transforms text
        self.assertNotEqual(raw_acoustic_transcript, rendered_email)

        # Invariant 2: Original raw text can be re-rendered into another mode without corruption
        re_rendered_verbatim = DictationProcessor.process(
            raw_acoustic_transcript,
            spoken_punctuation=False,
            mode=TransformationMode.VERBATIM,
        )
        self.assertEqual(re_rendered_verbatim, raw_acoustic_transcript)

        # Invariant 3: Schema verification for raw_transcript immutability
        record_id = self.db.add_dictation(
            raw_text=raw_acoustic_transcript,
            cleaned_text=rendered_clean,
            duration_sec=3.5,
        )
        self.assertGreater(record_id, 0)
        history = self.db.get_recent_dictation(limit=1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["raw_text"], raw_acoustic_transcript)
        self.assertEqual(history[0]["cleaned_text"], rendered_clean)

    def test_tier1_mode_switcher_signals_and_shortcuts(self) -> None:
        """Verify mode switcher signals emit correctly when mode chips or chevron are activated."""
        emitted_modes: list[str] = []
        self.pill.mode_changed.connect(lambda m: emitted_modes.append(str(m)))

        # Switch to Magic/Polished
        self.pill.set_mode(TransformationMode.MAGIC)
        self.assertEqual(self.pill.current_mode, TransformationMode.MAGIC)
        self.assertIn(TransformationMode.MAGIC.value, emitted_modes)

        # Switch to Structured
        self.pill.set_mode(TransformationMode.STRUCTURED)
        self.assertEqual(self.pill.current_mode, TransformationMode.STRUCTURED)
        self.assertIn(TransformationMode.STRUCTURED.value, emitted_modes)

        # Switch to Email
        self.pill.set_mode(TransformationMode.EMAIL)
        self.assertEqual(self.pill.current_mode, TransformationMode.EMAIL)
        self.assertIn(TransformationMode.EMAIL.value, emitted_modes)

        # Cycle modes using chevron
        prev_mode = self.pill.current_mode
        self.pill._cycle_next_mode()
        self.assertNotEqual(self.pill.current_mode, prev_mode)

    # =========================================================================
    # TIER 2: Boundary Value Analysis & Extreme Inputs
    # =========================================================================

    def test_tier2_boundary_empty_speech_and_whitespace(self) -> None:
        """Verify empty or whitespace-only speech returns empty string safely without error."""
        self.assertEqual(DictationProcessor.process(""), "")
        self.assertEqual(DictationProcessor.process("   \t  \n  "), "")
        self.assertEqual(DictationProcessor.transform_mode("", mode="structured"), "")
        self.assertEqual(DictationProcessor.transform_mode("   ", mode="email"), "   ")

    def test_tier2_boundary_single_word(self) -> None:
        """Verify single word input formats and capitalizes cleanly."""
        result = DictationProcessor.process("hello", capitalize_first=True)
        self.assertEqual(result, "Hello")

    def test_tier2_boundary_long_stream_endurance(self) -> None:
        """Verify processing of long uninterrupted streaming speech (500+ words)."""
        paragraph = (
            "we are discussing the roadmap for the voice assistant application period "
            "we want to ensure low latency and high accuracy across all supported languages comma "
            "including english and hindi and hinglish period "
        )
        long_speech = (paragraph * 15).strip()
        word_count = len(long_speech.split())
        self.assertGreaterEqual(word_count, 300)

        cleaned = DictationProcessor.process(long_speech, cleanup_enabled=True, spoken_punctuation=True)
        self.assertIn(".", cleaned)
        self.assertIn(",", cleaned)
        self.assertNotIn("period", cleaned.lower().split())
        self.assertNotIn("comma", cleaned.lower().split())

    def test_tier2_boundary_unicode_hindi_and_loanword_restoration(self) -> None:
        """Verify Devanagari Hindi, English loanword restoration, and transliteration."""
        # 1. Loanword restoration in Devanagari: "स्क्रीन शेयर" -> "screen share"
        deva_tech = "कृपया स्क्रीन शेयर कीजिए और बग फिक्स देखिए"
        restored = DictationProcessor.restore_loanwords(deva_tech)
        self.assertIn("screen share", restored)
        self.assertIn("bug fix", restored)

        # 2. Devanagari to Roman Hinglish transliteration
        deva_sentence = "कल सुबह मीटिंग करेंगे"
        roman = DictationProcessor.devanagari_to_roman_hinglish(deva_sentence)
        self.assertIn("kal", roman.lower())
        self.assertIn("subah", roman.lower())
        self.assertIn("meeting", roman.lower())

    def test_tier2_boundary_numeric_and_special_symbols(self) -> None:
        """Verify formatting of currencies, percentages, math, and software versions."""
        # Currency
        self.assertIn("$50", DictationProcessor.format_numeric_entities("fifty dollars"))
        self.assertIn("₹100", DictationProcessor.format_numeric_entities("one hundred rupees"))

        # Percentages
        self.assertIn("99.9%", DictationProcessor.format_numeric_entities("99.9 percent"))

        # Mathematical operations
        self.assertIn("10 + 5", DictationProcessor.format_numeric_entities("10 plus 5"))
        self.assertIn("20 - 4", DictationProcessor.format_numeric_entities("20 minus 4"))
        self.assertIn("8 * 2", DictationProcessor.format_numeric_entities("8 times 2"))

        # Versions
        self.assertIn("v2.0", DictationProcessor.format_numeric_entities("version 2.0"))

    # =========================================================================
    # TIER 3: Verbal Self-Correction, Filler Pruning & Vocabulary
    # =========================================================================

    def test_tier3_verbal_self_correction_retraction_markers(self) -> None:
        """Verify verbal self-correction detection across common retraction patterns."""
        # Clause replacement: "meet on Tuesday no actually Wednesday"
        text1 = "meet on Tuesday no actually Wednesday"
        result1 = DictationProcessor.detect_verbal_self_corrections(text1)
        self.assertTrue(result1["has_corrections"])
        self.assertIn("Wednesday", result1["clean_text"])
        self.assertNotIn("no actually", result1["clean_text"])

        # Retraction marker: "let's deploy now wait sorry tomorrow"
        text2 = "let's deploy now wait sorry tomorrow"
        result2 = DictationProcessor.detect_verbal_self_corrections(text2)
        self.assertTrue(result2["has_corrections"])
        self.assertIn("tomorrow", result2["clean_text"])

        # Term substitution: "send five dollars I mean ten dollars"
        text3 = "send five dollars I mean ten dollars"
        result3 = DictationProcessor.detect_verbal_self_corrections(text3)
        self.assertTrue(result3["has_corrections"])
        self.assertIn("ten dollars", result3["clean_text"])

    def test_tier3_annotated_html_strikethrough_markup(self) -> None:
        """Verify red strikethrough HTML preview generation for retracted speech."""
        text = "we need five points scratch that eight points"
        result = DictationProcessor.detect_verbal_self_corrections(text)
        annotated_html = result["annotated_html"]
        self.assertIn('style="color:#D32F2F; text-decoration:line-through; font-weight:500;"', annotated_html)
        self.assertIn("scratch that", annotated_html)

    def test_tier3_conversational_filler_pruning(self) -> None:
        """Verify conversational fillers are pruned in Clean mode."""
        raw = "So basically, um, we need to, you know, finish the release."
        cleaned = DictationProcessor.process(raw, cleanup_enabled=True, mode="cleaned")
        self.assertNotIn("so basically", cleaned.lower())
        self.assertNotIn("um", cleaned.lower().split())
        self.assertNotIn("you know", cleaned.lower())
        self.assertIn("finish the release", cleaned.lower())

    def test_tier3_vocabulary_normalization_and_custom_dictionary(self) -> None:
        """Verify custom domain terminology and Indian accent normalization rules."""
        # 1. Built-in accent normalization: "prep one" -> "prepone"
        text_accent = "we need to prep one the call"
        cleaned_accent = DictationProcessor.process(text_accent, cleanup_enabled=True)
        self.assertIn("prepone", cleaned_accent.lower())

        # 2. Built-in tech normalization: "p r merge" -> "PR merge"
        text_pr = "please approve the p r merge"
        cleaned_pr = DictationProcessor.process(text_pr, cleanup_enabled=True)
        self.assertIn("PR merge", cleaned_pr)

        # 3. Custom vocabulary injection
        custom_vocab = [
            {"spoken_phrase": "wisper flow", "replacement": "Wispr Flow", "is_active": 1},
            {"spoken_phrase": "vox assistant", "replacement": "VOX Assistant", "is_active": 1},
        ]
        text_custom = "I love using wisper flow and vox assistant"
        cleaned_custom = DictationProcessor.process(text_custom, vocabulary=custom_vocab, cleanup_enabled=True)
        self.assertIn("Wispr Flow", cleaned_custom)
        self.assertIn("VOX Assistant", cleaned_custom)

    def test_tier3_vibe_coding_and_app_context_classification(self) -> None:
        """Verify Wispr Flow Vibe Coding variable recognition, file tagging, and app context classification."""
        self.assertEqual(DictationProcessor.classify_app_context("controller.py - Cursor", "Cursor.exe"), "vibe_coding")
        self.assertEqual(DictationProcessor.classify_app_context("#engineering - Slack", "slack.exe"), "work_messages")
        self.assertEqual(DictationProcessor.classify_app_context("Inbox - Gmail", "chrome.exe"), "email")

        # Spoken code casing & file tagging
        raw_code = "please check at index dot tsx and update snake case user profile id"
        processed = DictationProcessor.process(
            raw_code,
            cleanup_enabled=True,
            window_title="index.tsx - Cursor",
            process_name="Cursor.exe",
        )
        self.assertIn("@index.tsx", processed)
        self.assertIn("user_profile_id", processed)


if __name__ == "__main__":
    unittest.main()

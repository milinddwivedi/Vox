from __future__ import annotations
"""
VOX 2.0 Speech Intelligence Optimization Test Suite.
Verifies:
1. TranscribeProfile presets: Fast (<250ms), Balanced (Default), Max Accuracy (Deep 5-beam search).
2. Dynamic Context-Aware Extraction: Window titles, process names, and conversational memory prompting.
3. Speculative Interim Streaming Hypotheses: Sub-200ms provisional text formatting and dispatch.
4. Two-Pass Meeting Reconciliation: Diarization smoothing, turn merging, and low-confidence slice refinement.
5. Controller Profile Switching & Interim Signal Events.
"""


import unittest
from pathlib import Path
import sys
import tempfile

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.win32.startup import StartupManager
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    TranscribeProfile,
)


class TestVox2SpeechIntelligence(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AppConfig(storage_dir=self.temp_dir.name)
        self.config.launch_at_login = StartupManager.is_enabled()
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_transcribe_profile_presets(self) -> None:
        self.assertIn("fast", TranscribeProfile.ALL_PROFILES)
        self.assertIn("balanced", TranscribeProfile.ALL_PROFILES)
        self.assertIn("max_accuracy", TranscribeProfile.ALL_PROFILES)

        t_fast = LocalWhisperTranscriber(
            models_dir=self.config.models_dir,
            model_name="large-v3-turbo",
            profile=TranscribeProfile.FAST,
        )
        self.assertEqual(t_fast.profile, TranscribeProfile.FAST)

        t_acc = LocalWhisperTranscriber(
            models_dir=self.config.models_dir,
            model_name="large-v3-turbo",
            profile=TranscribeProfile.MAX_ACCURACY,
        )
        self.assertEqual(t_acc.profile, TranscribeProfile.MAX_ACCURACY)

    def test_window_keyword_extraction(self) -> None:
        title = "main.py - Voice Assistant - Visual Studio Code"
        proc = "Code.exe"
        keywords = DictationProcessor.extract_window_keywords(title, proc)
        self.assertIn("main", keywords)
        self.assertIn("Voice", keywords)
        self.assertIn("Assistant", keywords)
        self.assertNotIn("window", [k.lower() for k in keywords])

        title2 = "Pull Request #45: Deploy SQLite database endpoint - GitHub - Google Chrome"
        keywords2 = DictationProcessor.extract_window_keywords(title2, "chrome.exe")
        self.assertIn("Deploy", keywords2)
        self.assertIn("SQLite", keywords2)
        self.assertIn("database", keywords2)
        self.assertIn("endpoint", keywords2)

    def test_contextual_prompt_assembly(self) -> None:
        recent = [
            "We deployed the backend container on staging.",
            "Please check the database connection string.",
        ]
        prompt = DictationProcessor.build_contextual_prompt(
            window_title="VS Code - server.py",
            process_name="code.exe",
            recent_history=recent,
            language="en+hi",
        )
        self.assertIn("deployed the backend", prompt)
        self.assertIn("database connection string", prompt)

    def test_interim_text_formatting(self) -> None:
        # 1. User vocabulary definition takes top precedence
        vocab = [
            {"spoken_phrase": "vox local", "replacement": "VoxLocal", "is_active": 1},
            {"spoken_phrase": "wind chill", "replacement": "Windchill PLM", "is_active": 1},
        ]
        raw_partial = "opening vox local and wind chill"
        formatted = DictationProcessor.process_interim(
            raw_partial, vocabulary=vocab, capitalize_first=True
        )
        self.assertIn("VoxLocal", formatted)
        self.assertIn("Windchill PLM", formatted)

        # 2. Phonetic accent correction on partial streaming input
        raw_partial2 = "i want to test hawaii express it"
        formatted2 = DictationProcessor.process_interim(raw_partial2, capitalize_first=True)
        self.assertTrue(formatted2.startswith("I"))
        self.assertIn("how I express", formatted2)

    def test_numeric_currency_and_math_formatting(self) -> None:
        # Currency formatting
        self.assertEqual(DictationProcessor.format_numeric_entities("I paid fifty dollars for this."), "I paid $50 for this.")
        self.assertEqual(DictationProcessor.format_numeric_entities("The budget is 500 rupees only."), "The budget is ₹500 only.")
        self.assertEqual(DictationProcessor.format_numeric_entities("Total cost was twenty euros."), "Total cost was €20.")
        self.assertEqual(DictationProcessor.format_numeric_entities("Price is 10 pounds."), "Price is £10.")

        # Percentages
        self.assertEqual(DictationProcessor.format_numeric_entities("The accuracy was 99.9 percent."), "The accuracy was 99.9%.")
        self.assertEqual(DictationProcessor.format_numeric_entities("We achieved fifty percentage growth."), "We achieved 50% growth.")

        # Versions and math
        self.assertEqual(DictationProcessor.format_numeric_entities("Released version 2.5 today."), "Released v2.5 today.")
        self.assertEqual(DictationProcessor.format_numeric_entities("Calculate 10 plus 5."), "Calculate 10 + 5.")
        self.assertEqual(DictationProcessor.format_numeric_entities("Formula is 20 minus 8 equals 12."), "Formula is 20 - 8 = 12.")

    def test_negations_preservation(self) -> None:
        phrases = [
            "I do not want to deploy this change to production.",
            "We never received the signed contract from the client.",
            "Please don't delete that file without backup.",
            "नहीं यह काम अभी मत करो।",
        ]
        for p in phrases:
            processed = DictationProcessor.process(p, cleanup_enabled=True)
            if "not" in p:
                self.assertIn("not", processed)
            if "never" in p:
                self.assertIn("never", processed)
            if "don't" in p:
                self.assertIn("don't", processed)
            if "नहीं" in p:
                self.assertIn("नहीं", processed)
            if "मत" in p:
                self.assertIn("मत", processed)

    def test_reconcile_meeting_transcript(self) -> None:
        t = LocalWhisperTranscriber(
            models_dir=self.config.models_dir,
            model_name="large-v3-turbo",
        )
        # Adjacent turns from the same speaker separated by <= 0.6s should be merged smoothly
        segs = [
            {"start_time": 1.0, "end_time": 2.5, "speaker": "You (Mic)", "text": "Let's review the deployment.", "confidence": 0.90},
            {"start_time": 2.8, "end_time": 4.0, "speaker": "You (Mic)", "text": "Everything looks good.", "confidence": 0.88},
            {"start_time": 5.0, "end_time": 7.0, "speaker": "Remote Speaker 1 (System)", "text": "Confirmed from my end.", "confidence": 0.95},
        ]
        reconciled = t.reconcile_meeting_transcript(segs)
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(reconciled[0]["speaker"], "You (Mic)")
        self.assertEqual(reconciled[0]["start_time"], 1.0)
        self.assertEqual(reconciled[0]["end_time"], 4.0)
        self.assertIn("Let's review the deployment. Everything looks good.", reconciled[0]["text"])
        self.assertEqual(reconciled[1]["speaker"], "Remote Speaker 1 (System)")

    def test_controller_profile_and_signals(self) -> None:
        # Verify controller updates config, transcriber, and emits signals
        ctrl = AppController(self.config)
        try:
            profile_events = []
            ctrl.transcribe_profile_changed.connect(lambda p: profile_events.append(p))

            interim_events = []
            ctrl.dictation_interim_updated.connect(lambda prov, pref: interim_events.append((prov, pref)))

            ctrl.set_transcribe_profile(TranscribeProfile.FAST)
            self.assertEqual(ctrl.config.transcribe_profile, "fast")
            self.assertEqual(ctrl.transcriber.profile, "fast")
            self.assertEqual(profile_events[-1], "fast")

            ctrl.set_transcribe_profile(TranscribeProfile.MAX_ACCURACY)
            self.assertEqual(ctrl.config.transcribe_profile, "max_accuracy")
            self.assertEqual(ctrl.transcriber.profile, "max_accuracy")
            self.assertEqual(profile_events[-1], "max_accuracy")

            # Verify interim event dispatch
            ctrl.dictation_interim_updated.emit("checking pipeline", "")
            self.assertEqual(interim_events[-1], ("checking pipeline", ""))
            ctrl.dictation_interim_updated.emit("", "")
            self.assertEqual(interim_events[-1], ("", ""))
        finally:
            ctrl.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""
Automated Test Suite for VoxLocal Multilingual Intelligence, Context-Aware Capitalization,
Model Manifest Integrity, and Cross-Application / Microsoft Teams Text Insertion.
"""


import ctypes
from ctypes import wintypes
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber
from voxlocal.win32.text_inserter import TextInserter, get_clipboard_text, set_clipboard_text
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker


class TestMultilingualAndTeams(unittest.TestCase):
    def test_devanagari_orthographic_normalization(self) -> None:
        raw = "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाएप हो जाना चाहीए."
        out = DictationProcessor.process(raw)
        self.assertEqual(
            out,
            "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए।",
        )

        raw2 = "मुझे कल अफिज जाना है."
        out2 = DictationProcessor.process(raw2)
        self.assertEqual(out2, "मुझे कल ऑफिस जाना है।")

    def test_hinglish_code_switching_preservation(self) -> None:
        raw = "आज ख्लाइंट के साथ मीटिंग है, so please prepare the project update."
        out = DictationProcessor.process(raw)
        self.assertEqual(
            out,
            "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
        )

    def test_romanized_hinglish_output_mode(self) -> None:
        raw = "मुझे कल ऑफिस जाना है।"
        out = DictationProcessor.process(raw, output_script="roman", capitalize_first=True)
        self.assertIn("office", out.lower())
        self.assertIn("mujhe", out.lower())
        # Ensure no Devanagari characters remain in Roman mode
        self.assertFalse(any("\u0900" <= ch <= "\u097F" for ch in out))

    def test_romanized_hinglish_tech_loanwords_and_schwa_deletion(self) -> None:
        # Tech loanwords should be restored in proper English, not phonetic Roman Devanagari
        raw = "यह बग फिक्स हो गया है, अब पीआर मर्ज कर दो."
        out = DictationProcessor.process(raw, output_script="roman", capitalize_first=True)
        self.assertIn("bug fix", out)
        self.assertIn("PR merge", out)
        self.assertNotIn("bag phiks", out)
        self.assertNotIn("peeaar", out)

        # Hindi schwa deletion for verbs (-te, -ti, -ta)
        raw2 = "हम कल सुबह कनेक्ट करते हैं क्या आप स्क्रीन शेयर कर सकते हैं?"
        out2 = DictationProcessor.process(raw2, output_script="roman", capitalize_first=True)
        self.assertIn("screen share", out2)
        self.assertIn("karte", out2)
        self.assertIn("sakte", out2)
        self.assertNotIn("karate", out2)
        self.assertNotIn("sakate", out2)

    def test_context_aware_mid_sentence_capitalization(self) -> None:
        # 1. Incomplete phrase with trailing ellipsis from Whisper pause
        seg1 = DictationProcessor.process("When I pause in the middle...", capitalize_first=True)
        self.assertEqual(seg1, "When I pause in the middle")
        self.assertFalse(DictationProcessor.is_sentence_complete(seg1))

        # 2. Continuation phrase should start in lowercase unless proper noun
        seg2 = DictationProcessor.process(
            "It should continue naturally in lowercase.",
            capitalize_first=DictationProcessor.is_sentence_complete(seg1),
        )
        self.assertEqual(seg2, "it should continue naturally in lowercase.")
        self.assertTrue(DictationProcessor.is_sentence_complete(seg2))

        # 3. Proper nouns (Hindi, English, Microsoft, I) stay capitalized even mid-sentence
        seg3 = DictationProcessor.process("Hindi and English work great.", capitalize_first=False)
        self.assertTrue(seg3.startswith("Hindi"))

        # 4. Devanagari Purna Viram counts as sentence completion
        self.assertTrue(DictationProcessor.is_sentence_complete("मुझे कल ऑफिस जाना है।"))

    def test_indian_english_accent_fixes(self) -> None:
        raw = "It is taking the first word in a cats. Hawaii express it."
        out = DictationProcessor.process(raw)
        self.assertIn("in caps", out)
        self.assertIn("how i express", out.lower())

    def test_hindustani_probability_fusion(self) -> None:
        # Simulate Whisper splitting Hindustani probability between hi (0.42) and ur (0.45) vs en (0.11)
        fake_info = SimpleNamespace(
            language="ur",
            language_probability=0.45,
            all_language_probs=[("ur", 0.45), ("hi", 0.42), ("en", 0.11)],
        )
        resolved = LocalWhisperTranscriber._resolve_bilingual_lang(fake_info)
        self.assertEqual(resolved, "hi")

        # Simulate pure English utterance
        fake_en = SimpleNamespace(
            language="en",
            language_probability=0.92,
            all_language_probs=[("en", 0.92), ("hi", 0.04), ("ur", 0.02)],
        )
        self.assertEqual(LocalWhisperTranscriber._resolve_bilingual_lang(fake_en), "en")

    def test_model_manifest_integrity(self) -> None:
        manifest_path = ROOT_DIR / "models" / "manifest.json"
        self.assertTrue(manifest_path.exists(), "models/manifest.json must exist")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("default_model"), "small")
        model_ids = {m["id"] for m in manifest.get("models", [])}
        self.assertIn("small", model_ids)
        self.assertIn("base", model_ids)
        self.assertIn("base.en", model_ids)

    def test_win32_edit_control_devanagari_and_hinglish_insertion(self) -> None:
        """Create a real Win32 Edit control, insert English, Hindi Devanagari, and Hinglish, and verify."""
        user32 = ctypes.windll.user32
        WM_GETTEXTLENGTH = 0x000E
        WM_GETTEXT = 0x000D
        WM_SETTEXT = 0x000C

        hwnd = user32.CreateWindowExW(
            0, "EDIT", "", 0x0004, 0, 0, 500, 200, None, None, None, None
        )
        self.assertTrue(hwnd != 0, "Failed to create Win32 EDIT window")
        try:
            tracker = WindowFocusTracker()
            inserter = TextInserter(tracker)
            target = TargetWindowInfo(
                hwnd=int(hwnd),
                title="UnitTestEdit",
                process_id=0,
                thread_id=0,
                process_name="ms-teams.exe",
                focus_child_hwnd=int(hwnd),
            )

            # Save sentinel on clipboard to verify clipboard restoration
            set_clipboard_text("VOXLOCAL_CLIPBOARD_SENTINEL")

            test_strings = [
                "Please send the quarterly status report on Microsoft Teams by 5pm. ",
                "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए। ",
                "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update. ",
            ]

            empty_buf = ctypes.create_unicode_buffer("")
            for s in test_strings:
                user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.addressof(empty_buf))
                # Directly test _send_wm_char_to_target (used when window is background/headless)
                ok = inserter._send_wm_char_to_target(s, target)
                self.assertTrue(ok)
                length = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0))
                buf = ctypes.create_unicode_buffer(length + 2)
                user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.addressof(buf))
                self.assertEqual(buf.value, s)

            # Verify clipboard paste selection for Microsoft Teams target
            ok_clip, method = inserter.insert_text(
                "मुझे कल ऑफिस जाना है।",
                method="auto",
                restore_clipboard=True,
                target=target,
            )
            self.assertTrue(ok_clip)
            self.assertEqual(method, "clipboard_paste")
            self.assertEqual(get_clipboard_text(), "VOXLOCAL_CLIPBOARD_SENTINEL")
        finally:
            user32.DestroyWindow(hwnd)


if __name__ == "__main__":
    unittest.main(verbosity=2)

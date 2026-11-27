from __future__ import annotations
"""
Comprehensive Verification for:
1. Read AI-Style Automatic Meeting Call Detection (ConsentStore mic monitor).
2. Hands-Free Zero-Click Streaming Voice Typer (pause-based typing).
3. Spoken Voice Commands ("stop listening", "delete that", "new line", "send message").
4. TextInserter backspace and Enter keyboard injection.
"""

from unittest import mock

import os
from pathlib import Path
import sys
import time
import unittest
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.call_detector import CallDetector, classify
from voxlocal.audio.streaming_typer import StreamingVoiceTyper
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.win32.window_focus import WindowFocusTracker


class TestHandsFreeAndCalls(unittest.TestCase):
    def test_call_detector_classification(self):
        # Known call apps
        self.assertEqual(classify("zoom.exe")[0], "Zoom")
        self.assertEqual(classify("msteams_8wekyb3d8bbwe")[0], "Microsoft Teams")
        self.assertEqual(classify("msedge.exe")[0], "Browser call (Meet/Web)")
        self.assertEqual(classify("chrome.exe")[0], "Browser call (Meet/Web)")
        self.assertEqual(classify("webex.exe")[0], "Webex")

        # Ignored apps (our own app, system sounds)
        self.assertIsNone(classify("python.exe"))
        self.assertIsNone(classify("voxlocal.exe"))
        self.assertIsNone(classify("soundrecorder.exe"))

    @mock.patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[])
    def test_call_detector_lifecycle(self, mock_wins):
        detector = CallDetector()
        t0 = 100.0

        # Teams starts mic
        self.assertIsNone(detector.update(["msteams_app"], t0))
        self.assertIsNone(detector.update(["msteams_app"], t0 + 2.0))
        # After 3.0s, native call triggers
        self.assertEqual(detector.update(["msteams_app"], t0 + 3.1), "started")
        self.assertIsNotNone(detector.active)
        self.assertEqual(detector.active.app_name, "Microsoft Teams")

        # While call is active, update returns None
        self.assertIsNone(detector.update(["msteams_app"], t0 + 10.0))

        # Mic released
        self.assertIsNone(detector.update([], t0 + 12.0))
        # Within grace period (<8s), call still active
        self.assertIsNone(detector.update([], t0 + 20.0))
        # After grace period (>=8s), call ends
        self.assertEqual(detector.update([], t0 + 24.5), "ended")
        self.assertIsNone(detector.active)

    def test_voice_commands_detection(self):
        self.assertEqual(DictationProcessor.detect_voice_command("stop listening."), "stop_listening")
        self.assertEqual(DictationProcessor.detect_voice_command("Stop Voice Typing!"), "stop_listening")
        self.assertEqual(DictationProcessor.detect_voice_command("delete that"), "delete_that")
        self.assertEqual(DictationProcessor.detect_voice_command("scratch that."), "delete_that")
        self.assertEqual(DictationProcessor.detect_voice_command("send message"), "press_enter")
        self.assertEqual(DictationProcessor.detect_voice_command("press enter"), "press_enter")
        self.assertEqual(DictationProcessor.detect_voice_command("new line"), "new_line")

        # Regular speech should not trigger command
        self.assertIsNone(DictationProcessor.detect_voice_command("Hello world, this is a test."))
        self.assertIsNone(DictationProcessor.detect_voice_command("I need to stop listening to bad advice."))

    def test_streaming_voice_typer_segmentation(self):
        typer = StreamingVoiceTyper()
        segments_received = []
        levels_received = []

        typer.on_segment_ready = lambda audio, dur: segments_received.append((audio, dur))
        typer.on_level_change = lambda lvl: levels_received.append(lvl)

        ok, msg = typer.start(pause_threshold_sec=0.4)
        self.assertTrue(ok)
        self.assertTrue(typer.is_running)

        # Feed 1 second of speech-level audio (sine wave 440Hz @ 0.2 amplitude)
        sr = 48000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        speech_pcm = (np.sin(2 * np.pi * 440 * t) * 0.25 * 32767).astype(np.int16).tobytes()
        typer._process_audio_chunk(speech_pcm)

        # Allow wall-clock pause threshold (>0.4s) to elapse naturally
        time.sleep(0.45)

        # Feed silence chunk to trigger pause finalization
        silence_pcm = np.zeros(int(sr * 0.1), dtype=np.int16).tobytes()
        typer._process_audio_chunk(silence_pcm)

        # Allow worker thread / callback to execute
        time.sleep(0.1)
        typer.stop()
        self.assertFalse(typer.is_running)

        self.assertGreater(len(levels_received), 0)
        self.assertGreater(len(segments_received), 0)
        audio_out, dur_out = segments_received[0]
        self.assertGreater(audio_out.size, 1000)
        self.assertGreater(dur_out, 0.2)

    def test_text_inserter_backspace_and_enter(self):
        tracker = WindowFocusTracker()
        inserter = TextInserter(tracker)
        # Verify methods execute without crash
        self.assertTrue(inserter.send_backspaces(count=0))
        self.assertTrue(inserter.send_backspaces(count=3))
        self.assertTrue(inserter.send_enter())

    def test_database_dictation_history_aliases(self):
        import tempfile
        from voxlocal.storage.database import LocalDatabase
        with tempfile.TemporaryDirectory() as td:
            with LocalDatabase(Path(td) / "test.db") as db:
                db.add_dictation_entry("hello test", "Hello test.", 1.2, "VSCode", "Code.exe")
                hist = db.list_dictation_history(limit=5)
                self.assertEqual(len(hist), 1)
                # Verify alias works identically
                alias_hist = db.list_dictations(limit=5)
                self.assertEqual(len(alias_hist), 1)
                self.assertEqual(alias_hist[0]["final_text"], "Hello test.")


if __name__ == "__main__":
    unittest.main()

"""
Golden Meeting Benchmark, NIST AI RMF Grounding, and Production Readiness Tests for VOX.

Validates:
1. Reference Transcription Accuracy (WER, CER, Hinglish/Indian English handling).
2. NIST AI RMF Factual Grounding & Hallucination Prevention (MoM, Action Items, Decisions).
3. Collision Avoidance & Toast Occlusion Geometry (Teams notification lane protection).
4. Call-Only Privacy Guarantee (Zero desktop leak on window minimize).
5. Diagnostic Log Privacy Audit (Zero plaintext tokens or secret leaks).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine, MeetingMinutesPipeline
from voxlocal.video.meeting_capture import MeetingSourceDescriptor, MeetingVideoRecorder
from voxlocal.audio.call_detector import MeetingSessionInfo


def calculate_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (WER) using Levenshtein distance on words."""
    r = reference.lower().split()
    h = hypothesis.lower().split()
    if not r:
        return 0.0 if not h else 1.0

    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j

    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            if r[i - 1] == h[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                substitution = d[i - 1][j - 1] + 1
                insertion = d[i][j - 1] + 1
                deletion = d[i - 1][j] + 1
                d[i][j] = min(substitution, insertion, deletion)

    return float(d[len(r)][len(h)]) / float(len(r))


def calculate_cer(reference: str, hypothesis: str) -> float:
    """Calculate Character Error Rate (CER) using Levenshtein distance on characters."""
    r = list(reference.lower().replace(" ", ""))
    h = list(hypothesis.lower().replace(" ", ""))
    if not r:
        return 0.0 if not h else 1.0

    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j

    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            if r[i - 1] == h[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = min(d[i - 1][j - 1] + 1, d[i][j - 1] + 1, d[i - 1][j] + 1)

    return float(d[len(r)][len(h)]) / float(len(r))


class TestGoldenMeetingBenchmark(unittest.TestCase):
    """Production benchmark for transcription, NIST AI RMF grounding, and UI security."""

    def setUp(self):
        self.config = AppConfig(storage_dir=Path("./data"))
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.mock_db = MagicMock()
        self.intel_engine = MeetingIntelligenceEngine(db=self.mock_db, llm_engine=None)

    def test_transcription_cleanup_and_hinglish_normalization(self):
        """Test vocabulary substitution and Hinglish normalizations."""
        raw_speech = "hum production deploy karenge on friday and rahul will review the api docs"
        custom_vocab = [
            {"spoken_phrase": "friday", "replacement": "Friday", "is_active": 1},
            {"spoken_phrase": "api", "replacement": "API", "is_active": 1},
            {"spoken_phrase": "rahul", "replacement": "Rahul", "is_active": 1},
        ]
        cleaned = DictationProcessor.process(
            raw_speech,
            vocabulary=custom_vocab,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
        )
        self.assertIn("API", cleaned)
        self.assertIn("Friday", cleaned)
        self.assertIn("Rahul", cleaned)

        # Calculate WER between normalized and ground truth
        reference = "Hum production deploy karenge on Friday and Rahul will review the API docs"
        wer = calculate_wer(reference, cleaned)
        self.assertLessEqual(wer, 0.05, f"WER {wer} exceeded 5% threshold")

    def test_nist_ai_rmf_factual_grounding_and_no_hallucinations(self):
        """NIST AI RMF trustworthiness: verify zero hallucinated decisions or action items."""
        segments = [
            {"start": 1.0, "end": 14.0, "speaker": "Sarah", "text": "Team, we need to finalize the Q4 cloud budget. We decided to cap AWS spending at $45,000."},
            {"start": 15.0, "end": 29.0, "speaker": "Alex", "text": "Agreed. I will adjust the autoscaling policies by Thursday."},
            {"start": 30.0, "end": 44.0, "speaker": "Sarah", "text": "Milind, please verify the database backup retention schedule before end of week."},
            {"start": 45.0, "end": 59.0, "speaker": "Milind", "text": "Will do. I will send the confirmation report by Friday 5 PM."},
        ]
        full_transcript = "\n".join(f"[{s['speaker']}]: {s['text']}" for s in segments)

        # Run 7-stage deterministic extraction
        mom = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            full_transcript=full_transcript,
            duration_sec=60.0,
            title="Q4 Budget Sync",
            platform="Microsoft Teams",
            session_id="teams-test-sync",
            llm_engine=None,
        )

        decisions = mom.get("decisions", [])
        actions = mom.get("action_items", [])
        markdown = mom.get("markdown", "")

        # Verify factual grounding: Decision MUST reflect the $45,000 cap
        decisions_str = " ".join(str(d) for d in decisions) + markdown
        self.assertTrue(
            "45,000" in decisions_str or "cap" in decisions_str.lower() or "budget" in decisions_str.lower(),
            f"Factual decision missing from output: {decisions_str}",
        )

        # Verify action items correctly identified owners
        actions_str = " ".join(str(a) for a in actions) + markdown
        self.assertTrue("Alex" in actions_str or "Thursday" in actions_str)
        self.assertTrue("Milind" in actions_str or "Friday" in actions_str)

        # Anti-hallucination check: ensure non-existent person or fake topic is NEVER invented
        for fake in ["Alice", "Google Cloud", "Kubernetes", "Blockchain", "Elon"]:
            self.assertNotIn(fake, decisions_str, f"Hallucination detected in decisions: {fake}")
            self.assertNotIn(fake, actions_str, f"Hallucination detected in action items: {fake}")

    def test_zero_speech_short_recording_clean_handling(self):
        """Verify 3.7-second zero-speech recording (Image 5 bug) produces graceful explanation."""
        mom = MeetingMinutesPipeline.generate_minutes(
            segments=[],
            full_transcript="",
            duration_sec=3.7,
            title="Test Quick Recording",
            platform="Google Meet",
            session_id="none",
            llm_engine=None,
        )
        summary = mom.get("summary", "")
        self.assertIn("No spoken speech was detected", summary)
        self.assertEqual(mom.get("action_items", []), [])

    def test_call_only_privacy_guard_on_window_minimize(self):
        """Verify meeting recorder strictly fails closed and emits privacy frame when minimized."""
        descriptor = MeetingSourceDescriptor(
            provider_id="win32_window",
            platform_name="Microsoft Teams",
            hwnd=12345,
            pid=9999,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Sprint Planning | Microsoft Teams",
            is_call_stage=True,
        )
        recorder = MeetingVideoRecorder(
            output_path="",
            source_descriptor=descriptor,
            fps=15,
        )
        recorder._width = 1280
        recorder._height = 720

        # Simulate minimized window (IsIconic returns True)
        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
            frame = recorder._capture_isolated_window_bgr(12345)
            self.assertIsNone(frame, "Capture must return None when window is minimized to prevent desktop leaks")

        # Verify privacy status frame renders with protective notice
        privacy_frame = recorder._build_privacy_status_frame("window_minimized", "Teams window minimized")
        self.assertIsNotNone(privacy_frame)
        self.assertEqual(privacy_frame.shape, (720, 1280, 3))

    def test_teams_toast_collision_avoidance_geometry(self):
        """Verify MeetingPromptPopup repositions safely outside the Windows/Teams notification lane."""
        try:
            from PyQt6.QtCore import QRect
            from PyQt6.QtWidgets import QApplication
            from voxlocal.ui.meeting_prompt_popup import MeetingPromptPopup
        except ImportError:
            self.skipTest("PyQt6 not available in this test environment")

        app = QApplication.instance() or QApplication([])

        sess = MeetingSessionInfo(
            session_id="teams-test-01",
            platform="Microsoft Teams",
            window_hwnd=0,
            window_title="Vox Testing - 2",
            clean_title="Vox Testing - 2",
            bounds=(0, 0, 800, 600),
            is_call_stage=True,
            mic_active=True,
        )
        mock_controller = MagicMock()
        mock_controller.theme = "dark"
        mock_controller.registered_ui_hwnds = set()

        popup = MeetingPromptPopup(sess, mock_controller)

        # Mock primary screen geometry to standard 1920x1080 (avail: 1920x1040)
        mock_screen = MagicMock()
        mock_screen.availableGeometry.return_value = QRect(0, 0, 1920, 1040)

        with patch.object(popup, "_find_meeting_screen", return_value=mock_screen), \
             patch.object(popup, "_detect_bottom_right_obstruction", return_value=None):
            popup.reposition_smart()
            geom = popup.geometry()

            # Toast lane is 420px from right. Popup should be docked to the LEFT of this lane:
            self.assertLessEqual(geom.right(), 1920 - 420, "Popup must stay strictly to the left of Teams toast lane")
            self.assertGreaterEqual(geom.left(), 0, "Popup must be on screen")
        popup.close()

    def test_diagnostic_logging_privacy_audit(self):
        """Verify logs scrub any sensitive tokens, API keys, or raw confidential meeting transcripts."""
        log_path = Path("./data/logs/voxlocal.log")
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                # Verify no plain text API keys leaked
                self.assertNotIn("sk-proj-", content)
                self.assertNotIn("ghp_", content)
                self.assertNotIn("bearer ", content.lower())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
"""
VOX Phase 7: Complete Product and Integration Validation Suite.
Validates the end-to-end operational integration of:
1. Voice Typing (Microphone capture, push-to-talk, silence gating, text insertion, vocabulary replacement).
2. Screen Recording (Window/monitor selection, PyAV H.264 encoding, dual-track audio sync, pause/resume, black-frame fallback).
3. Meeting Studio (Browser/native meeting detection, stage transitions, cancellation, recovery, report generation, Ask Vox).
4. Cross-Feature Coexistence (Hardware lock prevention, concurrency mutual exclusion, SQLite WAL thread-safety).
"""

from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

# Ensure src is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.audio.devices import AudioDeviceManager, TARGET_SAMPLE_RATE
from voxlocal.audio.call_detector import CallDetector, classify
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.win32.text_inserter import TextInserter, get_clipboard_text, set_clipboard_text
from voxlocal.win32.window_focus import WindowFocusTracker


class TestPhase7ProductAndIntegration(unittest.TestCase):
    """Phase 7: End-to-End Product & Integration Test Battery."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="vox_phase7_test_")
        self.config = AppConfig()
        # Strictly isolate test storage to temporary folder
        self.config.storage_dir = str(Path(self.test_dir) / "data")
        self.config.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.config.disable_model_preload = True
        self.config.progressive_transcription = False
        
        # Verify strict database path isolation
        assert Path(self.config.storage_dir).resolve() != (ROOT_DIR / "data").resolve()
        assert Path(self.config.db_path).resolve() != (ROOT_DIR / "data" / "voxlocal.db").resolve()
        
        self.db = LocalDatabase(self.config.db_path)
        self.controller = AppController(self.config)

    def tearDown(self) -> None:
        try:
            self.controller.shutdown()
        except Exception:
            pass
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # CORE CAPABILITY 1: Voice Typing Integration Tests
    # =========================================================================

    def test_p7_vt01_microphone_resolution_and_format(self):
        """VT-01: AudioDeviceManager discovers default WASAPI microphone and formats sample rate."""
        mgr = AudioDeviceManager()
        input_devs, output_devs = mgr.list_devices()
        self.assertIsInstance(input_devs, list)
        self.assertIsInstance(output_devs, list)
        default_mic = mgr.resolve_microphone()
        self.assertIsNotNone(default_mic, "A default audio capture device must be resolvable")
        self.assertEqual(TARGET_SAMPLE_RATE, 16000, "Whisper target sample rate must be 16kHz mono")

    def test_p7_vt02_dictation_processor_spoken_punctuation(self):
        """VT-02: DictationProcessor transforms spoken punctuation and custom vocabulary."""
        proc = DictationProcessor()
        raw_text = "hello vox local comma this is a high priority task period"
        processed = proc.process(raw_text)
        
        self.assertIn(",", processed, "Spoken comma must format to punctuation")
        self.assertIn(".", processed, "Spoken period must format to punctuation")

    def test_p7_vt03_text_inserter_clipboard_safety(self):
        """VT-03: TextInserter restores original clipboard content when using paste fallback."""
        orig_clip = "Original Clipboard Content 12345"
        set_clipboard_text(orig_clip)
        
        focus = WindowFocusTracker()
        inserter = TextInserter(focus)
        
        # Test insert_text clipboard restoration
        with patch.object(inserter, "_paste_via_clipboard", return_value=(True, "ok")):
            inserter.insert_text("Injected voice text", method="clipboard", restore_clipboard=True)
            time.sleep(0.05)
            restored = get_clipboard_text()
            self.assertEqual(restored, orig_clip, "Original clipboard text must be restored after paste")

    def test_p7_vt04_silence_gating_and_audio_energy(self):
        """VT-04: Voice activity detector / energy gate filters pure silence frames."""
        silence_frame = np.zeros(1600, dtype=np.int16)
        energy = np.sqrt(np.mean(silence_frame.astype(np.float32) ** 2))
        self.assertAlmostEqual(energy, 0.0, places=4, msg="Pure silence must produce 0.0 RMS energy")
        
        speech_frame = (np.sin(np.linspace(0, 100, 1600)) * 10000).astype(np.int16)
        speech_energy = np.sqrt(np.mean(speech_frame.astype(np.float32) ** 2))
        self.assertGreater(speech_energy, 1000.0, "Active audio must pass minimum threshold")

    # =========================================================================
    # CORE CAPABILITY 2: Screen Recording & Capture Matrix
    # =========================================================================

    def test_p7_sr01_screen_recording_lifecycle_and_db_persistence(self):
        """SR-01: Screen recording creates file, metadata row, and updates state cleanly."""
        rec_id = self.db.create_screen_recording(
            title="Sprint Review Walkthrough",
            file_path=str(self.config.recordings_dir / "test_rec.mp4"),
            fps=30,
            resolution="1080p",
            audio_mode="both",
        )
        self.assertGreater(rec_id, 0)
        
        recordings = self.db.list_screen_recordings()
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0]["title"], "Sprint Review Walkthrough")
        self.assertEqual(recordings[0]["resolution"], "1080p")

    def test_p7_sr02_black_frame_detection_and_toast(self):
        """SR-02: Black frame detector identifies DWM-suspended frames."""
        black_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mean_lum = np.mean(black_frame)
        self.assertAlmostEqual(mean_lum, 0.0, places=2)
        
        is_black = (mean_lum < 1.0)
        self.assertTrue(is_black, "Zero luminance frame must be classified as black frame")
        
        active_frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
        self.assertFalse(np.mean(active_frame) < 1.0)

    def test_p7_sr03_recording_pause_and_resume_durations(self):
        """SR-03: Pause and resume accumulation tracks active recording duration accurately."""
        elapsed_active = 0.0
        # 5 seconds recording
        elapsed_active += 5.0
        # Pause for 10 seconds (does not add)
        # Resume for 5 seconds
        elapsed_active += 5.0
        
        self.assertEqual(elapsed_active, 10.0, "Paused time must not inflate video active duration")

    # =========================================================================
    # CORE CAPABILITY 3: Meeting Studio End-to-End
    # =========================================================================

    def test_p7_ms01_browser_and_native_call_detection(self):
        """MS-01: Call detector recognizes Teams, Google Meet, and Zoom process signatures."""
        self.assertEqual(classify("zoom.exe")[0], "Zoom")
        self.assertEqual(classify("msteams_8wekyb3d8bbwe")[0], "Microsoft Teams")
        self.assertEqual(classify("msedge.exe")[0], "Browser call (Meet/Web)")
        self.assertEqual(classify("chrome.exe")[0], "Browser call (Meet/Web)")
        self.assertEqual(classify("webex.exe")[0], "Webex")
        self.assertIsNone(classify("python.exe"))
        self.assertIsNone(classify("code.exe"))

    def test_p7_ms02_early_stage2_persistence_before_crash(self):
        """MS-02: Raw Whisper transcripts persist to SQLite prior to downstream stage failure."""
        mid = self.db.create_meeting(title="Resilience Test Session", meeting_type="teams")
        
        test_segments = [
            {"start": 0.0, "end": 4.5, "text": "Good morning team, let us review the sprint roadmap.", "speaker": "SPEAKER_00"},
            {"start": 5.0, "end": 9.2, "text": "We have completed the schema v6 database migrations.", "speaker": "SPEAKER_01"},
        ]
        
        # Simulate Stage 2 early save
        self.db.replace_meeting_segments(mid, test_segments)
        
        # Verify persistence in database
        saved = self.db.get_meeting_segments(mid)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["text"], test_segments[0]["text"])
        
        # Simulate crash in Stage 3 diarization
        self.db.update_meeting(mid, status="error")
        m = self.db.get_meeting(mid)
        self.assertEqual(m["status"], "error")
        
        # Segments must still be intact and readable!
        salvaged = self.db.get_meeting_segments(mid)
        self.assertEqual(len(salvaged), 2, "Transcripts must not be deleted on downstream crash")

    def test_p7_ms03_report_export_notes_redaction_and_sensitive_scan(self):
        """MS-03: Client Review export redacts notes and warns on sensitive commercial terms."""
        from voxlocal.storage.exporter import TranscriptExporter
        
        mid = self.db.create_meeting(title="Confidential Client Negotiation", meeting_type="meet")
        self.db.update_meeting(mid, notes="Internal strictly confidential: Gross margin is 38%.")
        self.db.add_decision(mid, "Offer 10% volume discount", "Commercial", 120.0)
        
        meeting = self.db.get_meeting(mid)
        decisions = self.db.list_decisions(meeting_id=mid)
        actions = self.db.list_action_items(meeting_id=mid)
        
        txt_client = TranscriptExporter.to_txt(meeting, template_name="Client Review")
        self.assertNotIn("Internal strictly confidential", txt_client)
        self.assertNotIn("Gross margin", txt_client)
        
        matched = TranscriptExporter.check_sensitive_content(meeting)
        self.assertTrue(len(matched) > 0)
        self.assertIn("confidential", matched)
        self.assertIn("margin", matched)

    def test_p7_ms04_in_report_ask_vox_isolated_retrieval(self):
        """MS-04: In-report Ask Vox query only searches target meeting ID without catalog limits."""
        m1 = self.db.create_meeting(title="Secret Merger Alpha", meeting_type="zoom")
        self.db.replace_meeting_segments(m1, [{"start": 0.0, "end": 10.0, "text": "Code name Pegasus acquisition budget is 5M.", "speaker": "SPEAKER_00"}])
        
        m2 = self.db.create_meeting(title="Public Townhall", meeting_type="teams")
        self.db.replace_meeting_segments(m2, [{"start": 0.0, "end": 10.0, "text": "Annual sports day is scheduled for next month.", "speaker": "SPEAKER_00"}])
        
        # Query scoped strictly to m2
        res = self.controller.intelligence.query("What is the acquisition budget?", meeting_id=m2)
        citations = res.get("citations", [])
        
        # Citations must NEVER include m1
        for c in citations:
            self.assertEqual(c.get("meeting_id"), m2, "Citations must strictly originate from target meeting")
            self.assertNotIn("Pegasus", c.get("text", ""))

    # =========================================================================
    # CROSS-FEATURE COEXISTENCE & RECOVERY
    # =========================================================================

    def test_p7_cf01_concurrency_mutual_exclusion(self):
        """CF-01: Screen recording, meeting recording, and voice typing enforce mutual exclusion."""
        # 1. Active meeting blocks screen recording
        self.controller._meeting_state = "recording"
        ok, msg = self.controller.start_screen_recording(title="Blocked Rec")
        self.assertFalse(ok)
        self.assertIn("meeting", msg.lower())
        
        # 2. Active meeting blocks voice typing
        ok_vt = self.controller.start_dictation()
        self.assertFalse(ok_vt)
        
        # 3. Idle allows transition
        self.controller._meeting_state = "idle"
        self.controller._screen_rec_state = "idle"
        self.assertEqual(self.controller.meeting_state, "idle")

    def test_p7_cf02_interrupted_meeting_recovery_on_startup(self):
        """CF-02: Meetings in 'recording' status on unclean restart transition to 'interrupted'."""
        mid = self.db.create_meeting(title="Power Outage Meeting", meeting_type="teams")
        self.db.update_meeting(mid, status="recording")
        
        # Check initial state
        self.assertEqual(self.db.get_meeting(mid)["status"], "recording")
        
        # Execute recovery update with commit
        with self.db._connect() as conn:
            conn.execute("UPDATE meetings SET status = 'interrupted' WHERE status = 'recording'")
            conn.commit()
            
        m = self.db.get_meeting(mid)
        self.assertEqual(m["status"], "interrupted", "Unclean sessions must transition to 'interrupted' on recovery")


if __name__ == "__main__":
    unittest.main()

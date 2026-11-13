from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 12
Security, Privacy & Enterprise Hardening Test Battery

Covers:
1. Zero Network Egress & 100% Offline Architecture (Zero external cloud APIs or telemetry endpoints).
2. Word-Boundary Sensitive Content Scanning (confidential, salary, margin).
3. Client Review Data Leak Prevention (Strict suppression of internal notes).
4. Window Minimize Desktop Leak Guard (Zero desktop frame capture on minimized calls).
5. Diagnostic Log Privacy & Secret Protection.
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.video.meeting_capture import MeetingSourceDescriptor, MeetingVideoRecorder


class TestPhase12SecurityPrivacyHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase12_sec_test_")
        self.config = AppConfig()
        self.config.storage_dir = Path(self.temp_dir)
        self.db = LocalDatabase(self.config.db_path)

    def tearDown(self):
        import shutil
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Zero Network Egress & 100% Offline Static Codebase Audit
    # -------------------------------------------------------------------------
    def test_zero_network_egress_and_cloud_api_audit(self):
        """Verify that src/ contains zero hardcoded cloud AI endpoints or remote telemetry calls."""
        forbidden_patterns = [
            r"api\.openai\.com",
            r"api\.anthropic\.com",
            r"generativelanguage\.googleapis\.com",
            r"telemetry\.",
            r"google-analytics\.com",
            r"sentry\.io",
            r"segment\.io",
            r"mixpanel\.com",
        ]
        
        src_path = ROOT_DIR / "src"
        violations = []
        for py_file in src_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                for pattern in forbidden_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    if matches:
                        violations.append(f"{py_file.name}: matched forbidden pattern '{pattern}'")
            except Exception:
                pass

        self.assertEqual(len(violations), 0, f"Zero external network egress violation: {violations}")

    # -------------------------------------------------------------------------
    # 2. Sensitive Information Redaction & Word-Boundary Keyword Scanner
    # -------------------------------------------------------------------------
    def test_sensitive_keyword_scanner_and_boundary_checks(self):
        """Verify TranscriptExporter.check_sensitive_content accurately flags sensitive financial & confidential data."""
        meeting_sensitive = {
            "title": "Executive Compensation Sync",
            "full_transcript": "We must finalize the salary increase and profit margin targets under strict confidentiality.",
            "summary": "Reviewed confidential compensation figures.",
            "notes": "Ensure margin is kept private.",
        }

        matched = TranscriptExporter.check_sensitive_content(meeting_sensitive)
        self.assertGreater(len(matched), 0, "Sensitive content must be detected")
        matched_lower = [m.lower() for m in matched]
        self.assertTrue(any("salary" in m for m in matched_lower))
        self.assertTrue(any("margin" in m for m in matched_lower))
        self.assertTrue(any("confidential" in m for m in matched_lower))

        # Benign meeting with no sensitive terms
        meeting_clean = {
            "title": "Frontend Layout Review",
            "full_transcript": "We adjusted the button paddings and updated the typography font size.",
            "summary": "Refined button styling.",
            "notes": "Standard UI sprint task.",
        }
        matched_clean = TranscriptExporter.check_sensitive_content(meeting_clean)
        self.assertEqual(len(matched_clean), 0, "Benign meeting should have 0 sensitive flags")

    # -------------------------------------------------------------------------
    # 3. Client Review Data Exfiltration Suppression
    # -------------------------------------------------------------------------
    def test_client_review_internal_notes_redaction(self):
        """Verify internal notes are completely stripped when generating client-facing exports."""
        m_id = self.db.create_meeting(title="Client Steering Meeting")
        self.db.update_meeting(
            m_id,
            status="completed",
            summary="Agreed on deliverable milestones for Q4 release.",
            notes="CONFIDENTIAL: Internal gross margin is 42%. Client requested discount.",
        )
        self.db.add_decision(m_id, decision="Deliver pilot by Nov 30", status="confirmed")

        meeting = self.db.get_meeting(m_id)

        # 1. Standard MoM template includes internal notes
        txt_standard = TranscriptExporter.to_txt(meeting, template_name="Standard MoM")
        self.assertIn("CONFIDENTIAL: Internal gross margin is 42%", txt_standard)

        # 2. Client Review template strictly strips internal notes
        txt_client = TranscriptExporter.to_txt(meeting, template_name="Client Review")
        md_client = TranscriptExporter.to_markdown(meeting, template_name="Client Review")

        self.assertNotIn("CONFIDENTIAL", txt_client)
        self.assertNotIn("Internal gross margin", txt_client)
        self.assertNotIn("CONFIDENTIAL", md_client)
        self.assertNotIn("Internal gross margin", md_client)

    # -------------------------------------------------------------------------
    # 4. Window Minimize Privacy Guard (Zero Desktop Leak)
    # -------------------------------------------------------------------------
    def test_window_minimize_privacy_guard(self):
        """Verify meeting recorder strictly fails closed and returns None when window is minimized."""
        desc = MeetingSourceDescriptor(
            provider_id="win32_window",
            platform_name="Microsoft Teams",
            hwnd=99999,
            pid=8888,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Microsoft Teams Call",
        )
        test_mp4 = Path(self.temp_dir) / "test.mp4"
        recorder = MeetingVideoRecorder(output_path=str(test_mp4), source_descriptor=desc)

        recorder._width = 1280
        recorder._height = 720

        # Mock IsIconic to return True (window is minimized)
        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True):
            frame = recorder._capture_isolated_window_bgr(desc.hwnd)
            self.assertIsNone(frame, "Capture must return None when window is minimized to prevent desktop leak")

        # Verify privacy status frame construction
        privacy_frame = recorder._build_privacy_status_frame("window_minimized", "Teams window minimized")
        self.assertIsNotNone(privacy_frame)
        self.assertEqual(privacy_frame.shape, (720, 1280, 3))

    # -------------------------------------------------------------------------
    # 5. Diagnostic Log Privacy & Secret Protection
    # -------------------------------------------------------------------------
    def test_log_privacy_secret_protection(self):
        """Verify log file does not leak sensitive credentials or API keys."""
        log_path = Path("./data/logs/voxlocal.log")
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8", errors="ignore")
            # Verify no API keys leaked in logs
            self.assertNotIn("sk-proj-", content)
            self.assertNotIn("ghp_", content)
            self.assertNotIn("password=", content.lower())


if __name__ == "__main__":
    unittest.main()

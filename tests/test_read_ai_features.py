"""
Unit test suite verifying Read AI-grade capabilities in VoxLocal:
- Folders & Smart auto-classification
- Structured Action Items with Owners & Due Dates
- Key Decisions with Rationale & Timestamps
- LocalMeetingSummarizer chapters, metrics, and suggested prompts
- Cross-meeting "Ask Vox" semantic memory retrieval with grounded citations
- Multi-format exports (MD, TXT, SRT, VTT)
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import LocalMeetingSummarizer, TranscriptExporter


class TestReadAIFeatures(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_voxlocal.db"
        self.db = LocalDatabase(self.db_path)

    def tearDown(self):
        if getattr(self, "db", None) is not None:
            self.db.close()
        self.temp_dir.cleanup()

    def test_default_folders_and_crud(self):
        folders = self.db.list_folders()
        self.assertGreaterEqual(len(folders), 5)
        names = [f["name"] for f in folders]
        self.assertIn("Customer Support", names)
        self.assertIn("Status Update", names)

        # Create custom personal folder
        fid = self.db.create_folder("Client Alpha", folder_type="personal", color="#A86F3E")
        self.assertIsInstance(fid, int)
        folders_updated = self.db.list_folders()
        self.assertIn("Client Alpha", [f["name"] for f in folders_updated])

        # Delete folder
        self.assertTrue(self.db.delete_folder(fid))

    def test_meeting_analysis_full_read_ai(self):
        sample_segments = [
            {"speaker": "You (Mic)", "text": "Hi everyone, let's start with raw-material BOM source visibility in the Where-Used view.", "start_time": 0.0, "end_time": 6.0},
            {"speaker": "Remote Audio", "text": "Can suppliers see ordinary in-progress cost sheets or target cost entries?", "start_time": 7.0, "end_time": 14.0},
            {"speaker": "You (Mic)", "text": "We decided that suppliers will only see approved cost sheets, not draft sheets.", "start_time": 15.0, "end_time": 22.0},
            {"speaker": "Remote Audio", "text": "Understood. Abhilash will review the vendor search enhancement in the PO system by Friday.", "start_time": 23.0, "end_time": 30.0},
            {"speaker": "You (Mic)", "text": "Great, Lacy please loop in the engineering team by tomorrow.", "start_time": 31.0, "end_time": 36.0},
        ]

        analysis = LocalMeetingSummarizer.analyze_full(sample_segments, duration_sec=36.0)

        # 1. Executive Summary
        self.assertIn("Overview:", analysis["summary"])

        # 2. Key Decisions
        self.assertGreaterEqual(len(analysis["decisions"]), 1)
        self.assertTrue(any("decided" in d["decision"].lower() for d in analysis["decisions"]))

        # 3. Action Items with Owner and Due Date
        self.assertGreaterEqual(len(analysis["action_items"]), 1)
        abhilash_act = next((a for a in analysis["action_items"] if "Abhilash" in (a.get("owner") or "") or "Abhilash" in (a.get("description") or "")), None)
        self.assertIsNotNone(abhilash_act)
        self.assertIn("friday", (abhilash_act.get("due_date") or "").lower())

        # 4. Questions & Suggested Prompts
        self.assertGreaterEqual(len(analysis["questions"]), 1)
        self.assertGreaterEqual(len(analysis["suggested_prompts"]), 2)

        # 5. Engagement Score & Speaker Metrics
        self.assertGreater(analysis["engagement_score"], 60)
        self.assertIn("You (Mic)", analysis["speaker_metrics"])
        self.assertIn("Remote Audio", analysis["speaker_metrics"])

    def test_meeting_persistence_and_ask_vox(self):
        # Create meeting
        m_id = self.db.create_meeting(
            title="JS Group x WFX - Weekly Review",
            capture_mode="both",
            meeting_type="Meeting Reports",
            source_platform="Microsoft Teams",
            engagement_score=88,
        )

        # Add segments
        self.db.add_transcript_segment(m_id, 0, 14.0, 20.0, "Sangah Ryoo", "Can suppliers see in-progress target-cost entries without seeing status?")
        self.db.add_transcript_segment(m_id, 1, 21.0, 28.0, "Abhilash Mishra", "We confirmed that vendor search will be added to the PO system by end of month.")

        # Add decision & action item
        self.db.add_decision(m_id, "Enable vendor search in the PO system", rationale="Agreed by Abhilash", timestamp_sec=21.0)
        self.db.add_action_item(m_id, "Deploy PO system search filter", owner="Abhilash", due_date="by Friday", status="pending", timestamp_sec=21.0)

        # Search Meeting Memory ("Ask Vox")
        results = self.db.search_meeting_memory("vendor search", limit=5)
        self.assertGreaterEqual(len(results), 1)
        first_hit = results[0]
        self.assertEqual(first_hit["meeting_id"], m_id)
        self.assertEqual(first_hit["meeting_title"], "JS Group x WFX - Weekly Review")
        self.assertIn("vendor search", first_hit["text"].lower())

        # Action items listing & toggle
        acts = self.db.list_action_items(meeting_id=m_id)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["status"], "pending")

        new_st = self.db.toggle_action_item_status(acts[0]["id"])
        self.assertEqual(new_st, "completed")
        acts_after = self.db.list_action_items(meeting_id=m_id)
        self.assertEqual(acts_after[0]["status"], "completed")

    def test_multi_format_exports(self):
        meeting = {
            "title": "Strategy Sync",
            "created_at": "2026-10-19 10:00:00",
            "duration_sec": 120.0,
            "capture_mode": "both",
            "mic_device": "Microphone",
            "loopback_device": "Speakers",
            "audio_path": "audio.wav",
            "summary": "• Discussed Q4 product launch and marketing campaign.",
            "decisions": "• Launch date settled on November 15.",
            "action_items": "- [ ] [You] Finalize architecture document by Friday.",
            "notes": "Private user notes here.",
            "segments": [
                {"start_time": 0.0, "end_time": 4.0, "speaker": "You (Mic)", "text": "Welcome everyone to our sync."},
                {"start_time": 5.0, "end_time": 10.0, "speaker": "Remote", "text": "The launch date is settled on November 15."},
            ]
        }

        md_content = TranscriptExporter.to_markdown(meeting)
        self.assertIn("# Strategy Sync", md_content)
        self.assertIn("## Executive Summary", md_content)
        self.assertIn("## Key Decisions", md_content)
        self.assertIn("## Action Items", md_content)

        txt_content = TranscriptExporter.to_txt(meeting)
        self.assertIn("MEETING TRANSCRIPT: Strategy Sync", txt_content)

        srt_content = TranscriptExporter.to_srt(meeting)
        self.assertIn("00:00:00,000 -->", srt_content)

        vtt_content = TranscriptExporter.to_vtt(meeting)
        self.assertIn("WEBVTT", vtt_content)


if __name__ == "__main__":
    unittest.main()

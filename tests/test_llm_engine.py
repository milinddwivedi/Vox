"""
Unit tests for VoxLocal's Local LLM Engine (Llama-3.2-1B-Instruct-ct2-int8).
Verifies 100% offline, local generative AI capabilities for MoM and Ask Vox.
"""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline


class TestLocalLLMEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = AppConfig()
        cls.engine = LocalLLMEngine(
            model_dir=cls.config.llm_model_path,
            device="auto",
        )

    def test_model_availability_and_info(self):
        info = self.engine.get_model_info()
        self.assertIn("name", info)
        self.assertIn("Llama-3.2", info["name"])
        self.assertTrue(self.engine.is_available(), "Local LLM weights must be available on disk")

    def test_json_safe_parser(self):
        # Clean JSON
        valid = '{"executive_summary": "Test summary", "decisions": []}'
        res = LocalLLMEngine._parse_json_safely(valid)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("executive_summary"), "Test summary")

        # Markdown-wrapped JSON
        wrapped = '```json\n{"executive_summary": "Wrapped summary", "topics": []}\n```'
        res = LocalLLMEngine._parse_json_safely(wrapped)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("executive_summary"), "Wrapped summary")

        # Trailing comma glitch
        glitch = '{"executive_summary": "Glitch", "items": [1, 2, ], }'
        res = LocalLLMEngine._parse_json_safely(glitch)
        self.assertIsNotNone(res)

    def test_grounded_answer_synthesis(self):
        citations = [
            {
                "meeting_title": "Architecture Review",
                "timestamp_sec": 42.0,
                "speaker": "Principal Architect",
                "quote": "We approved the offline INT8 quantized model for all meeting intelligence.",
            }
        ]
        ans = self.engine.answer_query(
            question="What model was approved?",
            citations=citations,
            context_meeting_title="Architecture Review",
        )
        self.assertIsNotNone(ans)
        self.assertTrue(len(ans) > 10)
        self.assertIn("INT8", ans)

    def test_meeting_analysis_pipeline_integration(self):
        sample_segments = [
            {"speaker": "Alice", "start_time": 0.0, "end_time": 10.0, "text": "We need to update the product mapping before release."},
            {"speaker": "Bob", "start_time": 12.0, "end_time": 25.0, "text": "I will handle the WFX data export and review it with Rhea today."},
            {"speaker": "Alice", "start_time": 26.0, "end_time": 40.0, "text": "Great, let's agree to finalize the checklist by EOD."},
        ]

        mom = MeetingMinutesPipeline.generate_minutes(
            segments=sample_segments,
            full_transcript="Alice: We need to update the product mapping... Bob: I will handle the WFX data export...",
            duration_sec=40.0,
            title="Sprint Standup",
            platform="Microsoft Teams",
            llm_engine=self.engine,
        )

        self.assertIn("mom_markdown", mom)
        self.assertIn("mom_json", mom)
        self.assertTrue(len(mom["action_items"]) > 0 or len(mom["decisions"]) > 0)
        self.assertIn("Sprint Standup", mom["mom_markdown"])


if __name__ == "__main__":
    unittest.main()

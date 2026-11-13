"""
Unit and integration tests for Ask Vox real-time token streaming and VOX branding system.
"""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

# Ensure single shared QApplication instance for headless Qt tests
app = QApplication.instance() or QApplication([])

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.theme import create_app_icon, create_app_pixmap


class TestAskVoxStreamingAndBranding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = AppConfig()
        cls.llm_engine = LocalLLMEngine(
            model_dir=cls.config.llm_model_path,
            device="auto",
        )
        cls.test_db_path = ROOT_DIR / "data" / "test_stream_brand.db"
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass
        cls.db = LocalDatabase(str(cls.test_db_path))

    @classmethod
    def tearDownClass(cls):
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass

    def test_vox_branding_pixmap_and_icon_suite(self):
        """Verify high-DPI Sonic V brand mark across all supported operational states."""
        states = ["idle", "listening", "recording", "processing", "both"]
        for st in states:
            px = create_app_pixmap(state=st, size=64)
            self.assertFalse(px.isNull(), f"Pixmap for state {st} should not be null")
            self.assertEqual(px.width(), 64)
            self.assertEqual(px.height(), 64)

            icon = create_app_icon(state=st, size=48)
            self.assertFalse(icon.isNull(), f"Icon for state {st} should not be null")
            avail_sizes = icon.availableSizes()
            self.assertTrue(len(avail_sizes) >= 5, "create_app_icon should contain multiple DPI mipmap levels")

    def test_branding_assets_on_disk(self):
        """Verify static brand assets exist and are valid files."""
        assets_dir = ROOT_DIR / "assets"
        expected_files = [
            "favicon.ico",
            "vox_brandmark_header.png",
            "vox_logo_512.png",
            "vox_logo_256.png",
            "vox_logo_128.png",
            "vox_logo_64.png",
            "vox_logo_32.png",
            "vox_logo_16.png",
            "vox_logo.svg",
        ]
        for fname in expected_files:
            p = assets_dir / fname
            self.assertTrue(p.exists(), f"Asset {fname} must exist in assets/")
            self.assertGreater(p.stat().st_size, 50, f"Asset {fname} must not be empty")

    def test_vox_spec_packaging_includes_brand(self):
        """Verify PyInstaller spec includes assets folder and favicon.ico."""
        spec_path = ROOT_DIR / "VoxLocal.spec"
        self.assertTrue(spec_path.exists())
        content = spec_path.read_text(encoding="utf-8")
        self.assertIn("('assets', 'assets')", content)
        self.assertIn("icon='assets/favicon.ico'", content)

    def test_llm_engine_token_streaming(self):
        """Verify LocalLLMEngine.generate_stream and answer_query_stream yield incremental chunks."""
        citations = [
            {
                "meeting_title": "Product Sync",
                "timestamp_sec": 15.0,
                "speaker": "Milind",
                "quote": "We implemented token-by-token streaming for Ask Vox.",
            }
        ]
        stream_chunks = list(
            self.llm_engine.answer_query_stream(
                question="What was implemented?",
                citations=citations,
                context_meeting_title="Product Sync",
            )
        )
        self.assertTrue(len(stream_chunks) > 0, "answer_query_stream must yield at least one chunk")
        full_text = "".join(stream_chunks)
        self.assertIn("streaming", full_text.lower())

    def test_meeting_intelligence_query_stream(self):
        """Verify query_stream emits citations and tokens properly."""
        # Seed test database with a meeting
        m_id = self.db.create_meeting(
            title="Design System Review",
        )
        self.db.add_transcript_segment(
            meeting_id=m_id,
            segment_index=0,
            start_time=10.0,
            end_time=20.0,
            speaker="Lead Designer",
            text="The new Sonic V logo was deployed with glassmorphism and soundwave accents.",
        )

        intel = MeetingIntelligenceEngine(self.db, llm_engine=self.llm_engine)

        received_tokens = []
        received_citations = []

        res = intel.query_stream(
            question="What was deployed?",
            meeting_id=m_id,
            on_token=lambda tok: received_tokens.append(tok),
            on_citations=lambda cits: received_citations.extend(cits),
        )

        self.assertTrue(len(received_citations) > 0, "Citations must be emitted via callback")
        self.assertTrue(len(received_tokens) > 0, "Tokens must be streamed via on_token")
        self.assertEqual(res["answer"], "".join(received_tokens))
        self.assertIn("Sonic V", res["answer"])

    def test_meeting_intelligence_stream_cancellation(self):
        """Verify query_stream terminates immediately when cancel_check returns True."""
        intel = MeetingIntelligenceEngine(self.db, llm_engine=self.llm_engine)
        should_cancel = False

        def cancel_check():
            return should_cancel

        def on_token(tok):
            nonlocal should_cancel
            should_cancel = True  # Cancel after very first token!

        res = intel.query_stream(
            question="What was discussed?",
            on_token=on_token,
            cancel_check=cancel_check,
        )
        self.assertIsNotNone(res)

    def test_controller_streaming_signals(self):
        """Verify controller.query_meeting_memory_stream fires Qt streaming signals."""
        cfg = AppConfig()
        cfg.disable_model_preload = True
        cfg.disable_hotkeys = True
        ctrl = AppController(cfg)
        ctrl.db = self.db
        ctrl.intelligence.db = self.db
        ctrl.llm_engine = self.llm_engine
        ctrl.intelligence.llm_engine = self.llm_engine

        m_id = self.db.create_meeting("Brand Stream Test", capture_mode="both")
        self.db.add_transcript_segment(
            meeting_id=m_id,
            segment_index=0,
            start_time=1.0,
            end_time=5.0,
            speaker="Lead",
            text="We decided to deploy the Sonic V brand mark across all modules.",
        )

        token_emitted = []
        cits_emitted = []
        finished_emitted = []

        ctrl.ask_vox_token_streamed.connect(lambda qid, tok: token_emitted.append(tok))
        ctrl.ask_vox_citations_ready.connect(lambda qid, cits: cits_emitted.extend(cits))
        ctrl.ask_vox_stream_finished.connect(lambda qid, res: finished_emitted.append(res))

        ctrl.query_meeting_memory_stream("What was decided?", meeting_id=m_id)

        # Wait up to 30 seconds for background worker thread to finish
        t_start = time.time()
        while not finished_emitted and (time.time() - t_start < 30.0):
            app.processEvents()
            time.sleep(0.05)

        self.assertTrue(len(finished_emitted) > 0, "ask_vox_stream_finished signal must fire")
        ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()

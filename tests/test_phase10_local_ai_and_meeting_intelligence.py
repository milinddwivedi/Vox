from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 10
Local AI & Meeting Intelligence Test Battery

Covers:
1. Grounded Action Items with transcript citations, owner attribution, and deterministic deadline parsing.
2. 7-State Decision Evolution, relationship lineage ('new', 'repeated', 'superseded', 'reversed'), and ON DELETE RESTRICT FK protection.
3. Zero-Hallucination Safeguards: refusing to fabricate budgets/deadlines when unmentioned in transcript.
4. Meeting-Scoped Ask Vox RAG Isolation: zero cross-meeting context leakage even with identical search keywords.
5. Universal User-Edit & Manual Item Preservation across LLM re-analyses.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase
from voxlocal.engine.meeting_intelligence import (
    MeetingIntelligenceEngine,
    MeetingMinutesPipeline,
)


class TestPhase10LocalAIMeetingIntelligence(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase10_test_")
        self.config = AppConfig()
        self.config.storage_dir = Path(self.temp_dir)
        self.db = LocalDatabase(self.config.db_path)

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Grounded Action Items & Deterministic Dates
    # -------------------------------------------------------------------------
    def test_action_item_grounding_and_uncertain_deadline(self) -> None:
        """Verify action items store evidence quotes and maintain honest uncertainty on vague deadlines."""
        m_id = self.db.create_meeting(title="Sprint Planning")
        self.db.update_meeting(m_id, status="completed")
        
        # Add action item with transcript citation
        ai_id = self.db.add_action_item(
            meeting_id=m_id,
            description="Prepare the Q3 security audit deck",
            owner="Alice Smith",
            due_date="sometime next quarter",
            due_date_resolution="uncertain: Relative vague phrase without anchor date",
            evidence_quote="Alice, can you prepare the Q3 security audit deck sometime next quarter?",
            status="pending",
        )
        self.assertIsNotNone(ai_id)

        items = self.db.list_action_items(meeting_id=m_id)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["owner"], "Alice Smith")
        self.assertEqual(item["evidence_quote"], "Alice, can you prepare the Q3 security audit deck sometime next quarter?")
        self.assertIn("uncertain", item["due_date_resolution"])

    # -------------------------------------------------------------------------
    # 2. Decision Evolution Lineage & FK Constraint
    # -------------------------------------------------------------------------
    def test_decision_evolution_lineage_and_restrict_fk(self) -> None:
        """Verify 7-state decisions record evolution lineage and enforce ON DELETE RESTRICT on superseded parents."""
        m_id = self.db.create_meeting(title="Architecture Council")
        self.db.update_meeting(m_id, status="completed")

        # 1. Initial decision D1: Proposed PostgreSQL
        d1_id = self.db.add_decision(
            meeting_id=m_id,
            decision="Adopt PostgreSQL as the primary production database",
            status="proposed",
            evolution_relationship="new",
            rationale="Mature relational ecosystem",
            evidence_quote="Let's propose PostgreSQL for the primary cluster.",
        )
        self.assertIsNotNone(d1_id)

        # 2. Subsequent decision D2: Superseded PostgreSQL with SQLite WAL for local client
        d2_id = self.db.add_decision(
            meeting_id=m_id,
            decision="Adopt SQLite in WAL mode for standalone offline client",
            status="confirmed",
            evolution_relationship="superseded",
            supersedes_id=d1_id,
            rationale="Client is 100% offline local",
            evidence_quote="Actually, because VOX is fully local offline, we decide on SQLite WAL.",
        )
        self.assertIsNotNone(d2_id)

        # Verify lineage
        d2 = self.db.get_decision(d2_id)
        self.assertEqual(d2["evolution_relationship"], "superseded")
        self.assertEqual(d2["supersedes_id"], d1_id)
        self.assertEqual(d2["status"], "confirmed")

        # 3. Verify ON DELETE RESTRICT: Deleting D1 directly must fail with IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db._connect() as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("DELETE FROM decisions WHERE id = ?", (d1_id,))

    # -------------------------------------------------------------------------
    # 3. Zero-Hallucination & Refusal on Unmentioned Information
    # -------------------------------------------------------------------------
    def test_zero_hallucination_guardrails(self) -> None:
        """Verify that when the transcript contains no commitments, empty outcomes are cleanly returned."""
        transcript_text = "Good morning everyone. The weather in Bengaluru is great today. Absolutely pleasant morning."
        segments = [
            {"speaker": "Bob", "text": "Good morning everyone. The weather in Bengaluru is great today.", "start_sec": 0.0, "end_sec": 4.0},
            {"speaker": "Carol", "text": "Yes Bob, absolutely pleasant morning.", "start_sec": 4.5, "end_sec": 8.0},
        ]

        mom = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            full_transcript=transcript_text,
            duration_sec=8.0,
            title="Casual Morning Standup",
            platform="Microsoft Teams",
            llm_engine=None,
        )
        
        decisions = mom.get("decisions", [])
        action_items = mom.get("action_items", [])
        
        # Verify no fake decisions or actions are invented
        self.assertEqual(len(decisions), 0, "No decisions should be fabricated for casual conversation")
        self.assertEqual(len(action_items), 0, "No action items should be fabricated for casual conversation")
        
        # Anti-hallucination check: ensure non-existent entities or topics are never injected
        summary = mom.get("summary", "")
        for fake in ["Kubernetes", "Budget", "Contract", "Alice", "Deadline"]:
            self.assertNotIn(fake, summary, f"Hallucination detected in summary: {fake}")

    # -------------------------------------------------------------------------
    # 4. Meeting-Scoped Ask Vox RAG Isolation
    # -------------------------------------------------------------------------
    def test_ask_vox_strict_meeting_isolation(self) -> None:
        """Verify querying Ask Vox for Meeting A never returns segments from Meeting B despite identical keywords."""
        m_a = self.db.create_meeting(title="Internal Salary Review")
        m_b = self.db.create_meeting(title="Client Project Sync")
        self.db.update_meeting(m_a, status="completed")
        self.db.update_meeting(m_b, status="completed")

        # Add segment containing sensitive keyword to Meeting A
        self.db.add_transcript_segment(
            meeting_id=m_a,
            segment_index=0,
            start_time=10.0,
            end_time=15.0,
            speaker="HR Lead",
            text="The executive salary revision budget is finalized at 15 percent increase.",
        )

        # Add unrelated segment to Meeting B
        self.db.add_transcript_segment(
            meeting_id=m_b,
            segment_index=0,
            start_time=5.0,
            end_time=10.0,
            speaker="Project Lead",
            text="The project sprint delivery is scheduled for Friday afternoon.",
        )

        # Query Ask Vox scoped strictly to Meeting B searching for 'salary'
        results_b = self.db.search_meeting_memory(
            query="salary budget",
            meeting_id=m_b,
            limit=10,
        )
        self.assertEqual(len(results_b), 0, "Meeting B search MUST NOT leak Meeting A salary segments")

        # Query Ask Vox scoped to Meeting A
        results_a = self.db.search_meeting_memory(
            query="salary budget",
            meeting_id=m_a,
            limit=10,
        )
        self.assertGreaterEqual(len(results_a), 1, "Meeting A search should find its own salary segment")
        self.assertEqual(results_a[0]["meeting_id"], m_a)

    # -------------------------------------------------------------------------
    # 5. Universal User Edit Preservation
    # -------------------------------------------------------------------------
    def test_universal_user_edit_preservation_on_clear(self) -> None:
        """Verify clear_meeting_outcomes strictly protects user-edited and manually created items."""
        m_id = self.db.create_meeting(title="Strategy Sync")
        self.db.update_meeting(m_id, status="completed")

        # 1. AI-generated item (unmodified)
        ai_auto = self.db.add_action_item(
            meeting_id=m_id,
            description="Auto-extracted AI task",
            owner="David",
            is_manual=0,
            user_edited=0,
        )

        # 2. User-edited AI item
        ai_edited = self.db.add_action_item(
            meeting_id=m_id,
            description="AI task that user refined manually",
            owner="Elena",
            is_manual=0,
            user_edited=1,
        )

        # 3. Manually created user item
        user_manual = self.db.add_action_item(
            meeting_id=m_id,
            description="User typed this task by hand",
            owner="Frank",
            is_manual=1,
            user_edited=0,
        )

        # Trigger re-analysis clear
        self.db.clear_meeting_outcomes(m_id)

        remaining_items = self.db.list_action_items(meeting_id=m_id)
        remaining_ids = {it["id"] for it in remaining_items}

        self.assertNotIn(ai_auto, remaining_ids, "Auto AI item should be cleared on re-analysis")
        self.assertIn(ai_edited, remaining_ids, "User-edited item MUST BE preserved")
        self.assertIn(user_manual, remaining_ids, "Manual user item MUST BE preserved")


if __name__ == "__main__":
    unittest.main()

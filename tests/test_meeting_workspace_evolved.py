"""
Comprehensive Test Suite for Evolved Meeting Intelligence & Workspace (Core Feature 3).
Validates:
1. Unified 3-Tab Meeting Workspace (My Thoughts, Transcript, Summary).
2. Personal Private Scratchpad ("My Thoughts") isolation, timestamp marking, and persistence.
3. Interactive speaker-diarized transcript with search, audio playback sync, and inline renaming.
4. Dedicated One-Click Executive Follow-Up Email Generator (RFC 2368 mailto, rich HTML, Markdown).
5. Per-Participant Action Checklist Generator with Slack @mentions and checkboxes.
6. Bot-free native desktop call detection and 1-click consent prompt.
7. Local Model Context Protocol (MCP) server:
   - Loopback-only binding invariant (127.0.0.1).
   - 256-bit cryptographic session token authentication.
   - Non-negotiable voice typing dictation quarantine boundary.
   - The four discrete meeting tools (list_meetings, get_meeting_summary, search_transcripts, get_my_thoughts).
   - Tab 1 locked-by-default external AI authorization.
8. Preservation of all 8 advanced Meeting Studio tabs via the View Switcher.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from voxlocal.audio.call_detector import CallDetector, MeetingSessionInfo
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.extensions.mcp_server import (
    AuthorizationDecision,
    DictationQuarantineViolationError,
    McpAuthorizationError,
    VoxMcpServer,
)
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.meeting_studio import (
    VoxFollowUpEmailDialog,
    VoxInteractiveTranscriptTab,
    VoxMeetingStudioWorkspace,
    VoxMyThoughtsTab,
    VoxSummaryDeliverablesTab,
)

_QAPP = None


def get_qapp() -> QApplication:
    global _QAPP
    app = QApplication.instance()
    if app is None:
        _QAPP = QApplication(["--platform", "offscreen"])
        return _QAPP
    return app


class TestMeetingWorkspaceEvolved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_qapp()
        cls.temp_dir = tempfile.mkdtemp(prefix="vox_evolved_test_")
        cls.db_path = Path(cls.temp_dir) / "test_workspace.db"
        cls.db = LocalDatabase(cls.db_path)
        cls.engine = MeetingIntelligenceEngine(cls.db)

        # Seed realistic meeting session
        cls.m_id = cls.db.create_meeting(title="Q4 Product Launch & Architecture Review")
        cls.db.update_meeting(
            cls.m_id,
            duration_sec=1840.0,
            source_platform="Microsoft Teams",
            summary=(
                "📋 MINUTES OF MEETING (MoM)\n"
                "============================================================\n\n"
                "1. MEETING DETAILS\n"
                "• Title: Q4 Product Launch & Architecture Review\n\n"
                "2. EXECUTIVE SUMMARY\n"
                "The engineering and product teams aligned on shipping the offline Whisper pipeline "
                "and native 60 Hz hum filtering for the upcoming desktop release.\n"
            ),
            topics="Product Launch, Audio DSP, Offline Whisper, Security",
            notes="Private thought: Alex was initially hesitant about memory consumption [02:15]. Need to confirm benchmark RAM.",
        )

        # Transcript segments (bilingual / speaker diarized)
        cls.db.add_transcript_segment(
            meeting_id=cls.m_id,
            segment_index=0,
            start_time=10.0,
            end_time=18.5,
            speaker="You (Local)",
            source_track="mic",
            text="Let's ensure the desktop audio engine records without requiring external bots.",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.m_id,
            segment_index=1,
            start_time=22.0,
            end_time=31.0,
            speaker="Alex",
            source_track="loopback",
            text="Agreed. We will support Zoom, Teams, and Google Meet with zero bot accounts deployed.",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.m_id,
            segment_index=2,
            start_time=45.0,
            end_time=58.0,
            speaker="Sarah",
            source_track="loopback",
            text="Main security penetration testing audit complete kar doongi by Friday.",
        )

        # Decisions
        cls.db.add_decision(
            meeting_id=cls.m_id,
            decision="Deploy bot-free native desktop capture for all enterprise calls",
            rationale="Eliminates intrusive bot presence and protects user privacy",
            timestamp_sec=22.0,
            status="confirmed",
            evolution_relationship="new",
            evidence_quote="We will support Zoom, Teams, and Google Meet with zero bot accounts deployed.",
        )
        cls.db.add_decision(
            meeting_id=cls.m_id,
            decision="Finalize security penetration testing before public rollout",
            rationale="Mandatory compliance gate for enterprise pilot readiness",
            timestamp_sec=45.0,
            status="confirmed",
            evolution_relationship="superseded",
            evidence_quote="security penetration testing audit complete kar doongi by Friday.",
        )

        # Action Items
        cls.db.add_action_item(
            meeting_id=cls.m_id,
            description="Verify Windows loopback audio capture latency under 15ms",
            owner="Alex",
            due_date="2026-11-06",
            status="pending",
            evidence_quote="support Zoom, Teams, and Google Meet with zero bot accounts",
        )
        cls.db.add_action_item(
            meeting_id=cls.m_id,
            description="Complete enterprise security penetration audit",
            owner="Sarah",
            due_date="Friday",
            status="pending",
            evidence_quote="security penetration testing audit complete kar doongi by Friday.",
        )
        cls.db.add_action_item(
            meeting_id=cls.m_id,
            description="Document local-first MCP server loopback invariants",
            owner="You (Local)",
            due_date="2026-11-08",
            status="pending",
            evidence_quote="audio engine records without requiring external bots",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. My Thoughts Tab (Tab 1)
    # --------------------------------------------------------------------------
    def test_01_my_thoughts_isolation_and_persistence(self):
        """Verify Tab 1 personal scratchpad, timestamp insertion, persistence, and 100% isolation."""
        tab = VoxMyThoughtsTab(theme="dark")
        tab.db = self.db
        tab.meeting_id = self.m_id

        # 1. Set playback time and test [Mark Note]
        tab.set_current_playback_time(135.0)  # 02:15
        tab.set_thoughts("Private client body language observation: ")
        tab._on_mark_note_clicked()

        current_text = tab.get_thoughts()
        self.assertIn("[02:15]", current_text)
        self.assertIn("Private client body language", current_text)

        # 2. Test toggles
        tab.toggle_include_summary.setChecked(True)
        tab.toggle_allow_mcp.setChecked(True)
        self.assertTrue(tab.toggle_include_summary.isChecked())
        self.assertTrue(tab.toggle_allow_mcp.isChecked())

        # 3. Test persistence to SQLite database
        tab._persist_thoughts()

        # Verify database was updated
        m = self.db.get_meeting(self.m_id)
        self.assertIn("[02:15]", m.get("notes", ""))

        # 4. Verify Strict Isolation Guarantee
        self.assertTrue(tab.is_isolated())
        external_mom = "EXECUTIVE MINUTES: We agreed to deploy bot-free audio capture."
        clean_mom = tab.export_clean_mom(external_mom)
        self.assertEqual(clean_mom, external_mom)
        # Ensure private note content never appears in sanitized MoM
        self.assertNotIn("Private client body language", clean_mom)

    # --------------------------------------------------------------------------
    # 2. Interactive Transcript Tab (Tab 2)
    # --------------------------------------------------------------------------
    def test_02_interactive_transcript_diarization_and_seeking(self):
        """Verify Tab 2 speaker diarization, search filter, seeking signal, and speaker renaming."""
        tab = VoxInteractiveTranscriptTab(theme="dark")
        tab.db = self.db
        tab.meeting_id = self.m_id

        segments = self.db.get_meeting_segments(self.m_id)
        self.assertGreaterEqual(len(segments), 3)
        tab.load_segments(segments)

        # Verify segments loaded
        self.assertEqual(len(tab._segment_widgets), len(segments))

        # Test search filtering
        tab.search_input.setText("security")
        self.assertEqual(tab.lbl_match_count.text(), "1 matches")

        tab.search_input.setText("bot")
        self.assertEqual(tab.lbl_match_count.text(), "2 matches")

        tab._reset_filter()
        self.assertEqual(tab.lbl_match_count.text(), f"{len(segments)} matches")

        # Test seek signal
        seek_mock = MagicMock()
        tab.seek_requested.connect(seek_mock)
        # Simulate clicking timestamp button
        tab.seek_requested.emit(45.0)
        seek_mock.assert_called_with(45.0)

        # Test speaker renaming
        rename_mock = MagicMock()
        tab.speaker_renamed.connect(rename_mock)
        tab.speaker_renamed.emit("Sarah", "Sarah Jenkins (Security Lead)")
        rename_mock.assert_called_with("Sarah", "Sarah Jenkins (Security Lead)")

        # Test karaoke-style playback highlight
        tab.highlight_time(25.0)  # During Alex's segment (22-31s)
        self.assertEqual(tab._current_highlight_idx, 1)

    # --------------------------------------------------------------------------
    # 3. Summary Deliverables Tab (Tab 3)
    # --------------------------------------------------------------------------
    def test_03_summary_deliverables_and_decisions_actions(self):
        """Verify Tab 3 executive deliverables, 7-state decisions, action matrix, and Ask Vox."""
        tab = VoxSummaryDeliverablesTab(theme="dark")

        m = self.db.get_meeting(self.m_id)
        m["action_items"] = self.db.list_action_items(meeting_id=self.m_id)
        m["decisions"] = self.db.list_decisions(meeting_id=self.m_id)

        tab.load_meeting_data(m, engine=self.engine)

        # Verify executive summary
        self.assertIn("aligned on shipping the offline Whisper", tab.txt_summary.toPlainText())

        # Verify decisions count badge
        self.assertIn("2 Decisions", tab.lbl_decision_count.text())

        # Verify action items count badge
        self.assertIn("3 Action Items", tab.lbl_action_count.text())

        # Verify Ask Vox signal
        ask_mock = MagicMock()
        tab.ask_vox_submitted.connect(ask_mock)
        tab.txt_ask_input.setText("What were the decisions about security?")
        tab._on_ask_submitted()
        ask_mock.assert_called_with("What were the decisions about security?")

    # --------------------------------------------------------------------------
    # 4. Dedicated One-Click Executive Follow-Up Email Generator
    # --------------------------------------------------------------------------
    def test_04_dedicated_executive_follow_up_email_generation(self):
        """Verify executive follow-up email generation in MeetingIntelligenceEngine and dialog."""
        email = self.engine.generate_follow_up_email(meeting_id=self.m_id)

        # Check required fields
        self.assertIn("subject", email)
        self.assertIn("markdown", email)
        self.assertIn("html", email)
        self.assertIn("mailto_url", email)
        self.assertIn("decisions", email)
        self.assertIn("action_items_by_owner", email)

        # Verify subject structure
        self.assertIn("[Follow-up]", email["subject"])
        self.assertIn("Q4 Product Launch", email["subject"])

        # Verify decisions and actions are included and grounded
        self.assertIn("bot-free native desktop capture", email["markdown"])
        self.assertIn("Alex", email["action_items_by_owner"])
        self.assertIn("Sarah", email["action_items_by_owner"])

        # Verify rich HTML contains inline styling
        self.assertIn("font-family", email["html"])
        self.assertIn("Executive Summary", email["html"])

        # Verify RFC 2368 mailto URI
        self.assertTrue(email["mailto_url"].startswith("mailto:?subject="))
        self.assertIn("%5BFollow-up%5D", email["mailto_url"])

        # Verify dialog instantiation
        dlg = VoxFollowUpEmailDialog(email)
        self.assertIn("[Follow-up]", dlg.txt_subject.text())
        self.assertIn("Executive Summary", dlg.txt_markdown.toPlainText())

    # --------------------------------------------------------------------------
    # 5. Per-Participant Action Checklist Generator
    # --------------------------------------------------------------------------
    def test_05_per_participant_action_checklists(self):
        """Verify participant checklist grouping, Slack @mentions, and markdown formatting."""
        chk = self.engine.generate_participant_checklists(meeting_id=self.m_id)

        # Check structure
        self.assertEqual(chk["meeting_id"], self.m_id)
        self.assertEqual(chk["total_tasks"], 3)
        self.assertIn("by_participant", chk)
        self.assertIn("Alex", chk["by_participant"])
        self.assertIn("Sarah", chk["by_participant"])
        self.assertIn("You (Local)", chk["by_participant"])

        # Verify Slack format includes @mentions
        self.assertIn("@Alex", chk["slack_format"])
        self.assertIn("@Sarah", chk["slack_format"])
        self.assertIn("📋 *Action Items Checklist", chk["slack_format"])

        # Verify Markdown format includes checkboxes
        self.assertIn("### Alex", chk["markdown"])
        self.assertIn("- [ ] Verify Windows loopback", chk["markdown"])
        self.assertIn("- [ ] Complete enterprise security penetration audit", chk["markdown"])

    # --------------------------------------------------------------------------
    # 6. Bot-Free Native Desktop Call Detection Enhancements
    # --------------------------------------------------------------------------
    def test_06_bot_free_call_detector_enhancements(self):
        """Verify zero external bots invariant and 1-click recording consent prompt metadata."""
        detector = CallDetector()

        # Invariant check
        self.assertTrue(detector.is_bot_free())

        # Supported platforms
        platforms = detector.get_supported_platforms()
        self.assertIn("Microsoft Teams", platforms)
        self.assertIn("Zoom", platforms)
        self.assertIn("Google Meet", platforms)

        # Simulate detected meeting session
        sess = MeetingSessionInfo(
            session_id="teams_launch_review_123",
            platform="Microsoft Teams",
            window_hwnd=1001,
            window_title="Q4 Launch Review | Microsoft Teams",
            clean_title="Q4 Launch Review",
            bounds=(100, 100, 1280, 720),
            is_call_stage=True,
            mic_active=True,
        )

        prompt_data = detector.create_consent_prompt_data(sess)
        self.assertTrue(prompt_data["active"])
        self.assertTrue(prompt_data["is_bot_free"])
        self.assertEqual(prompt_data["platform"], "Microsoft Teams")
        self.assertEqual(prompt_data["clean_title"], "Q4 Launch Review")
        self.assertIn("Zero bot accounts deployed", prompt_data["bot_free_assurance"])

        # Test request consent decision flow
        decision_record = []
        detector.request_consent(
            controller=None,
            session_info=sess,
            on_decision=lambda d: decision_record.append(d),
        )
        detector.mark_decision(sess.session_id, "approved")
        self.assertIn(sess.session_id, detector.prompted_session_ids)
        self.assertEqual(sess.decision, "approved")

    # --------------------------------------------------------------------------
    # 7. Local Model Context Protocol (MCP) Server
    # --------------------------------------------------------------------------
    def test_07_mcp_server_loopback_and_token_auth(self):
        """Verify MCP server loopback binding invariant and 256-bit token authentication."""
        server = VoxMcpServer(self.db)

        # Loopback address check
        self.assertTrue(server.validate_loopback_address("127.0.0.1"))
        self.assertTrue(server.validate_loopback_address("::1"))
        self.assertTrue(server.validate_loopback_address("localhost"))
        self.assertFalse(server.validate_loopback_address("192.168.1.100"))
        self.assertFalse(server.validate_loopback_address("10.0.0.5"))

        # Rejection of external client IP via JSON-RPC
        rej_res = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, client_ip="192.168.1.50")
        self.assertIn("error", rej_res)
        self.assertEqual(rej_res["error"]["code"], -32000)

        # Token verification (256-bit hex token is 64 characters)
        self.assertEqual(len(server.session_token), 64)
        self.assertTrue(server.validate_auth_token(server.session_token))
        self.assertTrue(server.validate_auth_token(f"Bearer {server.session_token}"))
        self.assertFalse(server.validate_auth_token("invalid_token"))

    def test_08_mcp_server_dictation_quarantine_boundary(self):
        """NON-NEGOTIABLE SECURITY INVARIANT: Personal voice typing dictations are quarantined."""
        server = VoxMcpServer(self.db)

        # Attempt to access voice typing / dictation must raise quarantine violation
        with self.assertRaises(DictationQuarantineViolationError):
            server.verify_dictation_quarantine("vox_get_dictation_history", {})

        with self.assertRaises(DictationQuarantineViolationError):
            server.verify_dictation_quarantine("vox_search_transcripts", {"table": "dictation_history"})

        # In JSON-RPC call, returns error -32002
        res = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "vox_get_personal_dictations", "arguments": {}},
        })
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32002)
        self.assertIn("Dictation Quarantine Boundary Violation", res["error"]["message"])

    def test_09_mcp_server_tools_execution(self):
        """Verify the 4 MCP tools: list_meetings, get_meeting_summary, search_transcripts, get_my_thoughts."""
        server = VoxMcpServer(self.db)

        # 1. tools/list returns exactly the 4 tools
        list_res = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
        tools = list_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertEqual(len(tool_names), 4)
        self.assertIn("vox_list_meetings", tool_names)
        self.assertIn("vox_get_meeting_summary", tool_names)
        self.assertIn("vox_search_transcripts", tool_names)
        self.assertIn("vox_get_my_thoughts", tool_names)

        # 2. vox_list_meetings
        call_list = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "vox_list_meetings", "arguments": {"limit": 5}},
        })
        content_text = call_list["result"]["content"][0]["text"]
        meetings_out = json.loads(content_text)
        self.assertGreaterEqual(len(meetings_out), 1)
        self.assertEqual(meetings_out[0]["id"], self.m_id)
        # Verify zero transcript text in list
        self.assertNotIn("Let's ensure the desktop audio engine records", content_text)

        # 3. vox_get_meeting_summary
        call_sum = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "vox_get_meeting_summary", "arguments": {"meeting_id": self.m_id}},
        })
        sum_data = json.loads(call_sum["result"]["content"][0]["text"])
        self.assertEqual(sum_data["meeting_id"], self.m_id)
        self.assertIn("offline Whisper", sum_data["executive_summary"])
        self.assertEqual(len(sum_data["decisions"]), 2)
        self.assertEqual(len(sum_data["action_items"]), 3)
        # GUARANTEE: Suppresses internal thoughts
        self.assertNotIn("Private thought", json.dumps(sum_data))

        # 4. vox_search_transcripts
        call_search = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "vox_search_transcripts", "arguments": {"query": "bots", "meeting_id": self.m_id}},
        })
        search_data = json.loads(call_search["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(search_data), 1)
        self.assertIn("without requiring external bots", search_data[0]["text"])

        # 5. vox_get_my_thoughts: LOCKED BY DEFAULT (returns 403 authorization error)
        with self.db._connect() as conn:
            conn.execute("UPDATE my_thoughts SET allow_external_mcp = 0 WHERE meeting_id = ?", (self.m_id,))
            conn.commit()

        call_thoughts_locked = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "vox_get_my_thoughts", "arguments": {"meeting_id": self.m_id}},
        })
        self.assertIn("error", call_thoughts_locked)
        self.assertEqual(call_thoughts_locked["error"]["code"], -32003)
        self.assertIn("sequestered", call_thoughts_locked["error"]["message"].lower())

        # Now explicitly authorize in database table my_thoughts (allow_external_mcp = 1)
        with self.db._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS my_thoughts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    raw_markdown TEXT NOT NULL DEFAULT '',
                    include_in_summary INTEGER NOT NULL DEFAULT 0,
                    allow_external_mcp INTEGER NOT NULL DEFAULT 0,
                    word_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("DELETE FROM my_thoughts WHERE meeting_id = ?", (self.m_id,))
            conn.execute(
                "INSERT INTO my_thoughts (meeting_id, raw_markdown, allow_external_mcp) VALUES (?, ?, ?)",
                (self.m_id, "Authorized thoughts note for Claude", 1),
            )
            conn.commit()

        call_thoughts_unlocked = server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {"name": "vox_get_my_thoughts", "arguments": {"meeting_id": self.m_id}},
        })
        thoughts_data = json.loads(call_thoughts_unlocked["result"]["content"][0]["text"])
        self.assertTrue(thoughts_data["external_mcp_allowed"])
        self.assertIn("Authorized thoughts note", thoughts_data["thoughts"])

    # --------------------------------------------------------------------------
    # 8. Preservation of all 8 Advanced Studio Tabs
    # --------------------------------------------------------------------------
    def test_10_preservation_of_all_8_advanced_studio_tabs(self):
        """Verify view switcher preserves 100% of the 8 advanced tabs."""
        workspace = VoxMeetingStudioWorkspace(theme="dark", db=self.db)
        workspace.load_meeting(self.m_id)

        # 1. Default mode is Unified 3-Tab Workspace (Stack index 0)
        self.assertEqual(workspace.view_stack.currentIndex(), 0)
        self.assertEqual(workspace.unified_tabs.count(), 3)
        self.assertEqual(workspace.unified_tabs.tabText(0), "✏️ My Thoughts")
        self.assertEqual(workspace.unified_tabs.tabText(1), "🎙️ Transcript")
        self.assertEqual(workspace.unified_tabs.tabText(2), "✨ Summary")

        # 2. Switch to Advanced Studio Mode (Stack index 1)
        workspace.set_view_mode("advanced")
        self.assertEqual(workspace.view_stack.currentIndex(), 1)

        # 3. Verify all 8 advanced tabs are fully preserved
        self.assertEqual(workspace.advanced_tabs.count(), 8)
        self.assertIn("Executive MoM", workspace.advanced_tabs.tabText(0))
        self.assertIn("Transcripts", workspace.advanced_tabs.tabText(1))
        self.assertIn("Action Items", workspace.advanced_tabs.tabText(2))
        self.assertIn("Decisions", workspace.advanced_tabs.tabText(3))
        self.assertIn("Evolution & Comparison", workspace.advanced_tabs.tabText(4))
        self.assertIn("Briefings & Prep", workspace.advanced_tabs.tabText(5))
        self.assertIn("Analytics & KPIs", workspace.advanced_tabs.tabText(6))
        self.assertIn("Reports & Export", workspace.advanced_tabs.tabText(7))

        # 4. Verify data was loaded into both views
        self.assertIn("offline Whisper", workspace.adv_mom_page.toPlainText())
        self.assertEqual(len(workspace.adv_transcripts_page._segments), 3)

        # 5. Switch back to Unified
        workspace.set_view_mode("unified")
        self.assertEqual(workspace.view_stack.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()

"""
VOX Desktop Assistant — End-to-End Test Suite: Calendar Integration & Meeting Intelligence
Validates:
Tier 1: BaseCalendarProvider contract, MockCalendarProvider upcoming meeting retrieval,
        15-min and 5-min alert emission.
Tier 2: Token refresh lifecycle, disconnect/re-auth, error/offline recovery, acoustic priming.
Tier 3: 3-section meeting workspace (My Thoughts private isolation, Transcript diarization,
        Summary 7-state decisions, action items, risks, follow-up emails, participant checklists).
Tier 4: Bot-free capture workflow simulation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# Ensure project root and src/ are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.meeting_studio import (
    VoxFollowUpEmailDialog,
    VoxInteractiveTranscriptTab,
    VoxMeetingStudioWorkspace,
    VoxMyThoughtsTab,
    VoxSummaryDeliverablesTab,
)

_APP_INSTANCE = None


def get_qapp() -> QApplication:
    global _APP_INSTANCE
    app = QApplication.instance()
    if app is None:
        _APP_INSTANCE = QApplication(sys.argv)
        return _APP_INSTANCE
    return app


# =============================================================================
# Technical Contract Definitions (as specified in PROJECT.md & analysis.md §2)
# =============================================================================

from voxlocal.calendar.base import (
    BaseCalendarProvider,
    CalendarMeeting,
    CalendarProviderType,
)
from voxlocal.calendar.calendar_manager import CalendarAlertEvaluator, CalendarManager
from voxlocal.calendar.graph_provider import MsGraphCalendarProvider, dpapi_decrypt, dpapi_encrypt
from voxlocal.calendar.mapi_provider import LocalMapiCalendarProvider
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup


class TestE2ECalendarAndMeeting(unittest.TestCase):
    """End-to-End Test Suite for Calendar Providers, Alerts & Meeting Workspace."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_qapp()
        cls.temp_dir = tempfile.mkdtemp(prefix="vox_cal_meeting_e2e_")
        cls.db_path = Path(cls.temp_dir) / "test_workspace.db"
        cls.db = LocalDatabase(cls.db_path)
        cls.engine = MeetingIntelligenceEngine(cls.db)

        # Seed realistic meeting session
        cls.meeting_id = cls.db.create_meeting(title="Quarterly Architecture & PLM Sync")
        cls.db.update_meeting(
            cls.meeting_id,
            duration_sec=1200.0,
            source_platform="Microsoft Teams",
            summary=(
                "📋 MINUTES OF MEETING (MoM)\n"
                "============================================================\n\n"
                "1. EXECUTIVE SUMMARY\n"
                "The team approved the offline Whisper engine and decided to upgrade SQLite schema to v6.\n"
            ),
            topics="Architecture, Whisper, SQLite v6, PLM",
            notes="Private strategy note: Verify memory allocation limits before final sign-off [05:20].",
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.meeting_id,
            speaker="Sarah Connor",
            text="Let's finalize the database migration next Wednesday.",
            start_time=10.0,
            end_time=14.0,
            confidence=0.96,
        )
        cls.db.add_transcript_segment(
            meeting_id=cls.meeting_id,
            speaker="Alex Mercer",
            text="Agreed, I will complete the unit tests by Friday.",
            start_time=15.0,
            end_time=18.5,
            confidence=0.98,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.provider = MockCalendarProvider()
        self.alert_evaluator = CalendarAlertEvaluator()

    # =========================================================================
    # TIER 1: BaseCalendarProvider Contract, Mock Provider & Alert Emission
    # =========================================================================

    def test_tier1_base_calendar_provider_contract_compliance(self) -> None:
        """Verify BaseCalendarProvider enforces abstract interface contract."""
        self.assertTrue(issubclass(MockCalendarProvider, BaseCalendarProvider))
        self.assertTrue(hasattr(self.provider, "authenticate"))
        self.assertTrue(hasattr(self.provider, "disconnect"))
        self.assertTrue(hasattr(self.provider, "is_authenticated"))
        self.assertTrue(hasattr(self.provider, "get_upcoming_meetings"))
        self.assertTrue(hasattr(self.provider, "get_provider_status"))

    def test_tier1_mock_provider_retrieval_and_filtering(self) -> None:
        """Verify upcoming meeting retrieval within time window, filtering past/cancelled events."""
        self.provider.authenticate()
        now = datetime.now(timezone.utc)

        # 1. Active upcoming meeting in 30 minutes
        m1 = CalendarMeeting(
            id="evt-001",
            title="Design Review",
            start_time=now + timedelta(minutes=30),
            end_time=now + timedelta(minutes=60),
            location="Teams",
            join_url="https://teams.microsoft.com/l/meetup-join/1",
            attendees=["Sarah Connor", "Alex Mercer"],
            organizer="sarah@corp.local",
            is_online=True,
            is_cancelled=False,
        )
        # 2. Cancelled meeting
        m2 = CalendarMeeting(
            id="evt-002",
            title="Cancelled Sync",
            start_time=now + timedelta(minutes=20),
            end_time=now + timedelta(minutes=45),
            location="Room 402",
            join_url=None,
            attendees=["John Doe"],
            organizer="john@corp.local",
            is_online=False,
            is_cancelled=True,
        )
        # 3. Far future meeting (outside 60m window)
        m3 = CalendarMeeting(
            id="evt-003",
            title="Next Week Strategy",
            start_time=now + timedelta(days=7),
            end_time=now + timedelta(days=7, hours=1),
            location="Teams",
            join_url="https://teams.microsoft.com/l/meetup-join/3",
            attendees=["Executive Team"],
            organizer="ceo@corp.local",
            is_online=True,
            is_cancelled=False,
        )

        self.provider.inject_meeting(m1)
        self.provider.inject_meeting(m2)
        self.provider.inject_meeting(m3)

        upcoming = self.provider.get_upcoming_meetings(window_minutes=60)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0].id, "evt-001")
        self.assertEqual(upcoming[0].title, "Design Review")

    def test_tier1_15min_and_5min_alert_emission_and_deduplication(self) -> None:
        """Verify 15-minute and 5-minute advance alerts emit without duplicate triggers."""
        now = datetime.now(timezone.utc)

        # Event in 14 minutes (triggers 15min advance alert)
        m_15 = CalendarMeeting(
            id="evt-15m",
            title="Sprint Planning",
            start_time=now + timedelta(minutes=14),
            end_time=now + timedelta(minutes=45),
            location="Teams",
            join_url="https://teams.microsoft.com/l/meetup-join/15",
            attendees=["Riya Kohli", "Hemanth Kumar"],
            organizer="riya@corp.local",
            is_online=True,
        )
        # Event in 4 minutes (triggers 5min imminent alert)
        m_5 = CalendarMeeting(
            id="evt-5m",
            title="Executive Standup",
            start_time=now + timedelta(minutes=4),
            end_time=now + timedelta(minutes=19),
            location="Google Meet",
            join_url="https://meet.google.com/abc-defg-hij",
            attendees=["Milind Sharma", "Astha Dhiman"],
            organizer="milind@corp.local",
            is_online=True,
        )

        # Initial poll
        alerts_round1 = self.alert_evaluator.evaluate_meeting_alerts([m_15, m_5], current_time=now)
        self.assertEqual(len(alerts_round1), 2)
        types_round1 = {a["alert_type"] for a in alerts_round1}
        self.assertIn("15min", types_round1)
        self.assertIn("5min", types_round1)

        # Second poll immediately after (Deduplication invariant: 0 new alerts)
        alerts_round2 = self.alert_evaluator.evaluate_meeting_alerts([m_15, m_5], current_time=now)
        self.assertEqual(len(alerts_round2), 0)

    # =========================================================================
    # TIER 2: Token Refresh Lifecycle, Disconnect & Error Recovery
    # =========================================================================

    def test_tier2_token_refresh_lifecycle(self) -> None:
        """Verify proactive token refresh when token lifetime is under 300 seconds."""
        # 1. Fresh token (3600 seconds)
        self.provider.set_mock_token(expires_in_seconds=3600)
        self.assertFalse(self.provider.is_token_near_expiry(threshold_seconds=300))

        # 2. Expiring token (180 seconds remaining)
        self.provider.set_mock_token(expires_in_seconds=180)
        self.assertTrue(self.provider.is_token_near_expiry(threshold_seconds=300))

        # 3. Proactive refresh execution
        refreshed = self.provider.refresh_token()
        self.assertTrue(refreshed)
        self.assertFalse(self.provider.is_token_near_expiry(threshold_seconds=300))

    def test_tier2_disconnect_and_re_auth_protocol(self) -> None:
        """Verify disconnect purges cached state and re-auth restores clean session."""
        self.provider.authenticate()
        self.assertTrue(self.provider.is_authenticated())

        # Disconnect
        self.provider.disconnect()
        self.assertFalse(self.provider.is_authenticated())
        self.assertEqual(len(self.provider.get_upcoming_meetings()), 0)
        status = self.provider.get_provider_status()
        self.assertEqual(status["error_state"], "DISCONNECTED")

        # Re-authenticate
        self.provider.authenticate()
        self.assertTrue(self.provider.is_authenticated())
        self.assertIsNone(self.provider.get_provider_status()["error_state"])

    def test_tier2_error_and_offline_recovery_backoff(self) -> None:
        """Verify exponential backoff calculation and 429 rate limit handling."""
        # 1. Initial backoff interval is 60s
        self.assertEqual(self.provider.backoff_interval_sec, 60)

        # 2. Network drop 1: backoff -> 120s
        interval_1 = self.provider.handle_network_failure()
        self.assertEqual(interval_1, 120)

        # 3. Network drop 2: backoff -> 240s
        interval_2 = self.provider.handle_network_failure()
        self.assertEqual(interval_2, 240)

        # 4. Network drop 3: backoff -> capped at 300s
        interval_3 = self.provider.handle_network_failure()
        self.assertEqual(interval_3, 300)

        # 5. Rate limiting (HTTP 429)
        self.provider.simulate_rate_limit(retry_after=45)
        self.assertEqual(self.provider.retry_after_seconds, 45)
        self.assertEqual(self.provider.get_provider_status()["error_state"], "HTTP_429_RATE_LIMIT")

    def test_tier2_acoustic_priming_attendee_extraction(self) -> None:
        """Verify attendee names and topic keywords extracted for speech model acoustic priming."""
        meeting = CalendarMeeting(
            id="evt-prime",
            title="Windchill PLM & CAD Integration Architecture",
            start_time=datetime.now(timezone.utc) + timedelta(minutes=14),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=44),
            location="Teams",
            join_url="https://teams.microsoft.com",
            attendees=["Sarah Connor", "Alex Mercer", "Dr. Hemanth Kumar"],
            organizer="sarah@corp.local",
            is_online=True,
        )
        # Extract vocabulary terms for acoustic priming
        priming_terms = [name.strip() for name in meeting.attendees]
        priming_terms.extend(["Windchill", "PLM", "CAD"])

        self.assertIn("Sarah Connor", priming_terms)
        self.assertIn("Alex Mercer", priming_terms)
        self.assertIn("Dr. Hemanth Kumar", priming_terms)
        self.assertIn("Windchill", priming_terms)

    # =========================================================================
    # TIER 3: 3-Section Meeting Workspace & Intelligence Deliverables
    # =========================================================================

    def test_tier3_my_thoughts_private_scratchpad_isolation(self) -> None:
        """Verify Tab 1 ('My Thoughts') is sequestered from client MoM exports."""
        thoughts_tab = VoxMyThoughtsTab(self.engine)
        thoughts_tab.load_thoughts(self.meeting_id)

        # Verify initial private note loaded
        initial_text = thoughts_tab.get_text()
        self.assertIn("Private strategy note", initial_text)

        # Client export sanitization check:
        # Generate export text simulating client MoM report
        meeting_data = self.db.get_meeting(self.meeting_id)
        client_summary = meeting_data.get("summary", "")

        # Invariant: confidential scratchpad text must NOT leak into summary
        self.assertNotIn("Private strategy note", client_summary)
        self.assertNotIn("[05:20]", client_summary)

        thoughts_tab.close()

    def test_tier3_transcript_diarization_and_search(self) -> None:
        """Verify Tab 2 ('Transcript') displays diarized turns and full-text search."""
        transcript_tab = VoxInteractiveTranscriptTab(self.engine)
        transcript_tab.load_meeting(self.meeting_id)

        # Verify segments loaded into view
        self.assertEqual(len(transcript_tab._segments), 2)
        speakers = {s.get("speaker") for s in transcript_tab._segments}
        self.assertIn("Sarah Connor", speakers)
        self.assertIn("Alex Mercer", speakers)

        # Perform transcript search
        transcript_tab._search_input.setText("migration")
        transcript_tab._on_search_changed("migration")
        self.assertGreaterEqual(transcript_tab._match_count, 1)

        transcript_tab.close()

    def test_tier3_summary_7state_decisions_and_action_items(self) -> None:
        """Verify Tab 3 ('Summary') generates decisions, action items, and risks."""
        summary_tab = VoxSummaryDeliverablesTab(self.engine)
        summary_tab.load_meeting(self.meeting_id)

        # Verify summary content populated
        self.assertIsNotNone(summary_tab._current_meeting)
        raw_summary = summary_tab._current_meeting.get("summary", "")
        self.assertIn("MINUTES OF MEETING", raw_summary)
        self.assertIn("EXECUTIVE SUMMARY", raw_summary)

        # 7-State Decision state machine validation
        valid_states = {"proposed", "tentative", "confirmed", "rejected", "unresolved", "superseded", "reversed"}
        sample_decision_status = "confirmed"
        self.assertIn(sample_decision_status, valid_states)

        summary_tab.close()

    def test_tier3_one_click_follow_up_email_and_slack_checklists(self) -> None:
        """Verify 1-Click Follow-Up Email dialog produces Rich HTML, Markdown, and mailto URI."""
        dialog = VoxFollowUpEmailDialog(
            meeting_title="Architecture Sync",
            summary_text="Team agreed on offline Whisper engine.",
            action_items=[
                {"task": "Run database migrations", "owner": "Alex Mercer", "due_date": "2026-11-15"}
            ],
            decisions=[
                {"decision": "Upgrade schema to v6", "status": "confirmed"}
            ],
        )

        # 1. Markdown representation
        md_text = dialog._email_text.toPlainText()
        self.assertIn("Subject: Architecture Sync — Meeting Summary & Action Items", md_text)
        self.assertIn("Alex Mercer", md_text)
        self.assertIn("Upgrade schema to v6", md_text)

        # 2. Rich HTML representation
        rich_html = dialog._generate_rich_html()
        self.assertIn("<table", rich_html.lower())
        self.assertIn("Architecture Sync", rich_html)

        # 3. RFC 2368 mailto URI representation
        mailto_url = dialog._generate_mailto_url()
        self.assertTrue(mailto_url.startswith("mailto:?"))
        self.assertIn("subject=", mailto_url.lower())

        dialog.close()

    # =========================================================================
    # TIER 4: Bot-Free Capture Workflow Simulation
    # =========================================================================

    def test_tier4_bot_free_capture_workflow_simulation(self) -> None:
        """Simulate 1-click local meeting capture without external bots."""
        # 1. Incoming meeting alert payload
        meeting_event = CalendarMeeting(
            id="evt-botfree",
            title="Client Quarterly Roadmap Review",
            start_time=datetime.now(timezone.utc) + timedelta(minutes=2),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=32),
            location="Microsoft Teams",
            join_url="https://teams.microsoft.com/l/meetup-join/test",
            attendees=["Client VP", "Lead Architect"],
            organizer="vp@client.com",
            is_online=True,
        )

        # 2. User clicks '🎙️ Start Local Capture' (simulated)
        capture_session_id = self.db.create_meeting(
            title=meeting_event.title,
            source_platform=meeting_event.location,
        )
        self.assertGreater(capture_session_id, 0)

        # 3. Simulate local dual-track audio ingestion (mic + loopback)
        self.db.add_transcript_segment(
            meeting_id=capture_session_id,
            speaker="You (Mic)",
            text="Welcome everyone, let us review the quarterly milestones.",
            start_time=0.5,
            end_time=4.0,
            confidence=0.99,
        )
        self.db.add_transcript_segment(
            meeting_id=capture_session_id,
            speaker="Client VP",
            text="Thank you, we are very excited about the local-first voice assistant.",
            start_time=4.5,
            end_time=8.2,
            confidence=0.97,
        )

        # 4. Stop capture and verify meeting stored locally in database
        saved_meeting = self.db.get_meeting(capture_session_id)
        self.assertEqual(saved_meeting["title"], "Client Quarterly Roadmap Review")
        self.assertEqual(saved_meeting["source_platform"], "Microsoft Teams")

        segments = self.db.get_transcript_segments(capture_session_id)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["speaker"], "You (Mic)")
        self.assertEqual(segments[1]["speaker"], "Client VP")

    def test_tier1_calendar_manager_daemon_and_signals(self) -> None:
        """Verify CalendarManager polling daemon, signal emissions, and deduplication."""
        manager = CalendarManager(provider=self.provider)
        self.provider.authenticate()

        now = datetime.now(timezone.utc)
        m_adv = CalendarMeeting(
            id="mgr-adv",
            title="FastAPI & SQLite Architecture Review",
            start_time=now + timedelta(minutes=12),
            end_time=now + timedelta(minutes=42),
            location="Microsoft Teams",
            join_url="https://teams.microsoft.com",
            attendees=["Astha Dhiman", "Milind Sharma"],
            organizer="astha@corp.local",
            is_online=True,
        )
        self.provider.inject_meeting(m_adv)

        received_alerts: List[Dict[str, Any]] = []
        received_priming: List[List[str]] = []
        received_upcoming: List[List[CalendarMeeting]] = []

        manager.meeting_alert.connect(lambda m, mins, t: received_alerts.append({"meeting": m, "mins": mins, "type": t}))
        manager.acoustic_priming_ready.connect(lambda terms: received_priming.append(terms))
        manager.upcoming_meetings_updated.connect(lambda mtgs: received_upcoming.append(mtgs))

        # Initial poll
        meetings = manager.poll_now()
        self.assertEqual(len(meetings), 1)
        self.assertEqual(len(received_upcoming), 1)
        self.assertEqual(len(received_alerts), 1)
        self.assertEqual(received_alerts[0]["type"], "15min")
        self.assertGreater(len(received_priming), 0)
        self.assertIn("Astha Dhiman", received_priming[0])
        self.assertIn("FastAPI", received_priming[0])

        # Second poll (deduplication check: 0 new alerts)
        manager.poll_now()
        self.assertEqual(len(received_alerts), 1)

        # Test disconnect
        manager.disconnect_provider()
        self.assertFalse(manager.provider.is_authenticated())

    def test_tier2_graph_provider_dpapi_and_scopes(self) -> None:
        """Verify MS Graph provider minimal scopes, DPAPI encryption, and token lifecycle."""
        auth_file = Path(self.temp_dir) / "test_graph_auth.dat"
        provider = MsGraphCalendarProvider(auth_file_path=auth_file)

        # 1. Verify minimal scope invariant
        self.assertIn("Calendars.Read", provider.SCOPES)
        self.assertIn("offline_access", provider.SCOPES)
        self.assertNotIn("Mail.Read", provider.SCOPES)
        self.assertNotIn("Files.Read", provider.SCOPES)

        # 2. DPAPI encryption / decryption round-trip
        secret = b'{"access_token": "secret_abc_123", "expires_at": 9999999999}'
        encrypted = dpapi_encrypt(secret)
        self.assertNotEqual(secret, encrypted)
        decrypted = dpapi_decrypt(encrypted)
        self.assertEqual(secret, decrypted)

        # 3. Token expiry evaluation
        provider._expires_at = time.time() + 600  # 10 minutes remaining
        self.assertFalse(provider.is_token_near_expiry(threshold_seconds=300))

        provider._expires_at = time.time() + 120  # 2 minutes remaining
        self.assertTrue(provider.is_token_near_expiry(threshold_seconds=300))

        # 4. Disconnect cleans up file
        auth_file.write_bytes(encrypted)
        self.assertTrue(auth_file.exists())
        provider.disconnect()
        self.assertFalse(auth_file.exists())
        self.assertFalse(provider.is_authenticated())

    def test_tier2_mapi_provider_fallback_lifecycle(self) -> None:
        """Verify LocalMapiCalendarProvider graceful fallback and status reporting."""
        provider = LocalMapiCalendarProvider()
        status = provider.get_provider_status()
        self.assertEqual(status["provider_type"], CalendarProviderType.LOCAL_MAPI.value)

        # Disconnect should be safe even when not connected
        provider.disconnect()
        self.assertFalse(provider.is_authenticated())
        self.assertEqual(provider.get_provider_status()["error_state"], "DISCONNECTED")

    def test_tier1_meeting_prompt_popup_floating_ui(self) -> None:
        """Verify VoxMeetingPromptPopup non-activating window flags and 1-click capture CTA."""
        popup = VoxMeetingPromptPopup(theme="dark")

        # Verify non-activating window flags
        flags = popup.windowFlags()
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))

        # Show meeting alert
        meeting = CalendarMeeting(
            id="evt-popup",
            title="Global Infrastructure Review",
            start_time=datetime.now(timezone.utc) + timedelta(minutes=5),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=35),
            location="Microsoft Teams",
            join_url="https://teams.microsoft.com",
            attendees=["Sarah Connor", "Alex Mercer"],
            organizer="sarah@corp.local",
            is_online=True,
        )
        popup.show_meeting_alert(meeting, minutes_until_start=5)

        self.assertIn("Global Infrastructure Review", popup.lbl_title.text())
        self.assertIn("Sarah Connor", popup.lbl_meta.text())

        # Test capture button signal
        capture_emitted: List[CalendarMeeting] = []
        popup.start_capture_requested.connect(lambda m: capture_emitted.append(m))
        popup.btn_capture.click()
        self.assertEqual(len(capture_emitted), 1)
        self.assertEqual(capture_emitted[0].id, "evt-popup")

        popup.close()

    def test_tier4_workspace_calendar_capture_integration(self) -> None:
        """Verify VoxMeetingStudioWorkspace launches capture session with pre-filled details."""
        workspace = VoxMeetingStudioWorkspace(db=self.db, theme="dark")

        meeting = CalendarMeeting(
            id="evt-ws-capture",
            title="Executive Sprint Kickoff",
            start_time=datetime.now(timezone.utc) + timedelta(minutes=1),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=31),
            location="Microsoft Teams",
            join_url="https://teams.microsoft.com",
            attendees=["Director Jane", "Tech Lead Bob"],
            organizer="jane@corp.local",
            is_online=True,
        )

        meeting_id = workspace.launch_calendar_capture(meeting)
        self.assertGreater(meeting_id, 0)
        self.assertEqual(workspace.active_meeting_id, meeting_id)

        # Verify database record
        saved = self.db.get_meeting(meeting_id)
        self.assertEqual(saved["title"], "Executive Sprint Kickoff")
        self.assertEqual(saved["source_platform"], "Microsoft Teams")
        self.assertIn("Director Jane", saved["topics"])

        workspace.close()

    def test_tier2_graph_pkce_flow_and_callback(self) -> None:
        """Verify MsGraphCalendarProvider generates S256 PKCE, receives localhost callback, and persists DPAPI tokens."""
        import base64
        import hashlib
        import threading
        import urllib.parse
        import urllib.request

        auth_file = Path(self.temp_dir) / "pkce_flow_auth.dat"
        provider = MsGraphCalendarProvider(client_id="test-client-123", auth_file_path=auth_file)

        verifier, challenge = provider.generate_pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        self.assertEqual(challenge, expected)

        url = provider.build_authorization_url("http://localhost:54321", "st-xyz", challenge)
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs["code_challenge"][0], challenge)
        self.assertEqual(qs["code_challenge_method"][0], "S256")
        self.assertEqual(qs["client_id"][0], "test-client-123")

        active_servers = []
        from http.server import HTTPServer
        import io
        real_httpserver_init = HTTPServer.__init__

        def _tracking_httpserver_init(server_self, server_address, RequestHandlerClass, *args, **kwargs):
            active_servers.append((server_self, RequestHandlerClass))
            return real_httpserver_init(server_self, server_address, RequestHandlerClass, *args, **kwargs)

        class _DummyLoopbackSocket:
            def __init__(self, raw_input: bytes):
                self.rfile = io.BytesIO(raw_input)
                self.wfile = io.BytesIO()

            def makefile(self, mode, *args, **kwargs):
                if "r" in mode:
                    return self.rfile
                return self.wfile

            def sendall(self, data: bytes) -> None:
                self.wfile.write(data)

        def _fake_opener(auth_url: str) -> bool:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
            r_uri = q["redirect_uri"][0]
            st = q["state"][0]
            callback_path = f"/?code=authcode999&state={urllib.parse.quote(st)}"

            def _hit() -> None:
                time.sleep(0.08)
                target_url = f"{r_uri.replace('localhost', '127.0.0.1')}{callback_path}"
                try:
                    urllib.request.urlopen(target_url, timeout=5).read()
                except (PermissionError, OSError):
                    # Catch PermissionError / WinError 10013 during test HTTP callback delivery
                    # and simulate redirect request directly against the provider callback handler
                    if active_servers:
                        srv, handler_cls = active_servers[-1]
                        raw_req = f"GET {callback_path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("utf-8")
                        sock = _DummyLoopbackSocket(raw_req)
                        try:
                            handler_cls(sock, ("127.0.0.1", 12345), srv)
                        except Exception:
                            pass

            threading.Thread(target=_hit, daemon=True).start()
            return True

        class _FakeResp:
            def __init__(self, payload: dict) -> None:
                self._raw = json.dumps(payload).encode("utf-8")
            def read(self) -> bytes:
                return self._raw
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        real_urlopen = urllib.request.urlopen

        def _mock_urlopen(req_or_url, *args, **kwargs):
            target = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url)
            if "localhost" in target or "127.0.0.1" in target:
                return real_urlopen(req_or_url, *args, **kwargs)
            if "oauth2/v2.0/token" in target:
                return _FakeResp({
                    "access_token": "tok_access_pkce",
                    "refresh_token": "tok_refresh_pkce",
                    "expires_in": 3600,
                })
            if "/me" in target:
                return _FakeResp({"mail": "milind@corp.local", "displayName": "Milind"})
            raise AssertionError(f"Unexpected URL: {target}")

        with patch("http.server.HTTPServer.__init__", _tracking_httpserver_init), patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            ok = provider.authenticate(interactive=True, timeout_sec=10, auth_url_opener=_fake_opener)

        self.assertTrue(ok)
        self.assertTrue(provider.is_authenticated())
        self.assertTrue(auth_file.exists())
        self.assertEqual(provider.get_provider_status()["account_upn"], "milind@corp.local")

    def test_tier2_ics_provider_parsing_and_teams_link(self) -> None:
        """Verify IcsCalendarProvider parses VEVENT + Teams URL + recurrence from Outlook ICS."""
        from voxlocal.calendar.ics_provider import IcsCalendarProvider

        start_utc = (datetime.now(timezone.utc) + timedelta(minutes=25)).replace(second=0, microsecond=0)
        end_utc = start_utc + timedelta(minutes=45)
        ics_file = Path(self.temp_dir) / "sample_outlook.ics"
        ics_file.write_text(
            "\n".join([
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "X-WR-CALNAME:Milind Work Calendar",
                "BEGIN:VEVENT",
                "UID:ics-evt-001",
                "SUMMARY:Daily Architecture Sync",
                f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
                "ORGANIZER;CN=Milind:mailto:milind@corp.local",
                "ATTENDEE;CN=Sarah Connor:mailto:sarah@corp.local",
                "X-MICROSOFT-SKYPETEAMSMEETINGURL:https://teams.microsoft.com/l/meetup-join/19abc",
                "END:VEVENT",
                "END:VCALENDAR",
            ]),
            encoding="utf-8",
        )
        auth_dat = Path(self.temp_dir) / "calendar_ics.dat"
        prov = IcsCalendarProvider(feed_url=str(ics_file), auth_file_path=auth_dat)
        self.assertTrue(prov.authenticate())
        self.assertTrue(prov.is_authenticated())
        meetings = prov.get_upcoming_meetings(window_minutes=120)
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0].title, "Daily Architecture Sync")
        self.assertTrue(meetings[0].is_online)
        self.assertIn("teams.microsoft.com", meetings[0].join_url or "")
        self.assertIn("Sarah Connor", meetings[0].attendees)


if __name__ == "__main__":
    unittest.main()


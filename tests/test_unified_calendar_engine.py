"""Comprehensive Test Suite for VOX Unified Calendar Engine & Multi-Provider Integration."""

import json
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType, BaseCalendarProvider
from voxlocal.calendar.unified_engine import (
    UnifiedCalendarEngine,
    normalize_meeting_join_id,
    normalize_title,
)
from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
from voxlocal.calendar.google_provider import GoogleCalendarProvider
from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
from voxlocal.storage.database import LocalDatabase
from voxlocal.core.config import AppConfig


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ----------------------------------------------------------------------
# Test 1: CalendarMeeting Dataclass & Platform Detection
# ----------------------------------------------------------------------
def test_calendar_meeting_dataclass_and_platform_detection():
    now_utc = datetime.now(timezone.utc)
    st = now_utc + timedelta(hours=1)
    et = st + timedelta(minutes=45)

    m_teams = CalendarMeeting(
        id="teams-123",
        title="Engineering Sync",
        start_time=st,
        end_time=et,
        join_url="https://teams.microsoft.com/l/meetup-join/19%3ameeting_xyz",
        attendees=["alice@example.com", "bob@example.com"],
        organizer="charlie@example.com",
    )
    assert m_teams.platform == "teams"
    assert m_teams.duration_minutes == 45
    assert m_teams.is_today is True
    assert "alice@example.com" in m_teams.attendees_str

    m_zoom = CalendarMeeting(
        id="zoom-123",
        title="Client Demo",
        start_time=st + timedelta(days=1),
        end_time=st + timedelta(days=1, minutes=30),
        join_url="https://us02web.zoom.us/j/1234567890?pwd=abc",
    )
    assert m_zoom.platform == "zoom"
    assert m_zoom.duration_minutes == 30
    assert m_zoom.is_tomorrow is True

    m_meet = CalendarMeeting(
        id="meet-123",
        title="Design Review",
        start_time=st + timedelta(days=5),
        end_time=st + timedelta(days=5, hours=1),
        join_url="https://meet.google.com/abc-defg-hij",
    )
    assert m_meet.platform == "meet"
    assert m_meet.duration_minutes == 60
    assert m_meet.is_today is False
    assert m_meet.is_tomorrow is False


# ----------------------------------------------------------------------
# Test 2: Cross-Calendar Deduplication (Outlook + Google + Zoom)
# ----------------------------------------------------------------------
def test_cross_calendar_deduplication(qapp, tmp_path):
    engine = UnifiedCalendarEngine(parent=None)

    now_utc = datetime.now(timezone.utc)
    st = now_utc + timedelta(hours=2)
    et = st + timedelta(minutes=30)

    # Same meeting from Outlook and Google Calendar with same Zoom join URL
    m_outlook = CalendarMeeting(
        id="outlook-evt-001",
        title="Sprint Planning",
        start_time=st,
        end_time=et,
        join_url="https://zoom.us/j/9876543210",
        attendees=["milind@wfx.com"],
        organizer="scrummaster@wfx.com",
        provider_type="ms_graph",
    )
    m_google = CalendarMeeting(
        id="google-evt-999",
        title="Sprint Planning (Engineering)",
        start_time=st,
        end_time=et,
        join_url="https://zoom.us/j/9876543210?pwd=secret",
        attendees=["milind@gmail.com", "colleague@gmail.com"],
        organizer="colleague@gmail.com",
        provider_type="google",
    )

    deduped = engine._deduplicate_meetings([m_outlook, m_google])
    assert len(deduped) == 1
    united = deduped[0]
    assert "outlook-evt-001" in united.source_event_ids
    assert "google-evt-999" in united.source_event_ids
    # Merged attendees
    assert "milind@wfx.com" in united.attendees
    assert "colleague@gmail.com" in united.attendees
    assert united.platform == "zoom"

    # Fuzzy title & start-time deduplication when URL is missing
    m_fuzzy1 = CalendarMeeting(
        id="ics-1",
        title="Weekly 1:1 with Alex",
        start_time=st + timedelta(days=2),
        end_time=st + timedelta(days=2, minutes=30),
        provider_type="ics",
    )
    m_fuzzy2 = CalendarMeeting(
        id="mapi-2",
        title="Weekly 1:1 with Alex",
        start_time=st + timedelta(days=2, minutes=2),  # 2 min difference
        end_time=st + timedelta(days=2, minutes=32),
        provider_type="mapi",
    )
    deduped_fuzzy = engine._deduplicate_meetings([m_fuzzy1, m_fuzzy2])
    assert len(deduped_fuzzy) == 1


# ----------------------------------------------------------------------
# Test 3: Zoom Dual-Endpoint Query (24h 'upcoming' + 6-month 'scheduled')
# ----------------------------------------------------------------------
def test_zoom_dual_endpoint_and_pagination(tmp_path):
    import time
    auth_file = tmp_path / "zoom_auth.dat"
    provider = ZoomCalendarProvider(client_id="zoom_id", client_secret="zoom_secret", auth_file_path=auth_file)
    provider._access_token = "mock_zoom_token"
    provider._expires_at = time.time() + 3600

    now_utc = datetime.now(timezone.utc)
    t1_iso = (now_utc + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t2_iso = (now_utc + timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def mock_urlopen(req, timeout=15):
        url = req.full_url
        if "type=upcoming" in url:
            body = {
                "meetings": [
                    {
                        "id": "111111111",
                        "topic": "Immediate 24h Standup",
                        "start_time": t1_iso,
                        "duration": 30,
                        "join_url": "https://zoom.us/j/111111111",
                    }
                ],
                "next_page_token": "",
            }
        elif "type=scheduled" in url and "page_token" not in url:
            body = {
                "meetings": [
                    {
                        "id": "222222222",
                        "topic": "Future Q3 Strategy Review",
                        "start_time": t2_iso,
                        "duration": 60,
                        "join_url": "https://zoom.us/j/222222222",
                    }
                ],
                "next_page_token": "token_page2",
            }
        else:
            body = {"meetings": [], "next_page_token": ""}

        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        meetings = provider.get_upcoming_meetings(window_minutes=43200 * 3)  # 90-day window
        assert len(meetings) == 2
        topics = [m.title for m in meetings]
        assert "Immediate 24h Standup" in topics
        assert "Future Q3 Strategy Review" in topics


# ----------------------------------------------------------------------
# Test 4: Microsoft Graph RFC 8628 Device Code Flow
# ----------------------------------------------------------------------
def test_microsoft_graph_device_code_flow(tmp_path):
    auth_file = tmp_path / "ms_auth.dat"
    provider = MsGraphCalendarProvider(auth_file_path=auth_file)

    def mock_urlopen(req, timeout=15):
        url = req.full_url
        if "/devicecode" in url:
            body = {
                "device_code": "dev_code_abc",
                "user_code": "WDJB-ABCD",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 1,
                "message": "To sign in, use a web browser to open https://microsoft.com/devicelogin",
            }
        elif "/token" in url:
            body = {
                "access_token": "graph_mock_access_token",
                "refresh_token": "graph_mock_refresh_token",
                "expires_in": 3600,
            }
        elif "/me" in url:
            body = {
                "userPrincipalName": "milind@wfx.com",
                "displayName": "Milind Dwivedi",
            }
        else:
            body = {"value": []}

        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        info = provider.request_device_code()
        assert info is not None
        assert info["user_code"] == "WDJB-ABCD"
        assert info["verification_uri"] == "https://microsoft.com/devicelogin"

        ok, msg = provider.poll_device_code_tokens("dev_code_abc", interval=0, expires_in=5)
        assert ok is True
        assert provider.is_authenticated() is True
        assert provider.get_provider_status()["account_upn"] == "milind@wfx.com"


def test_microsoft_graph_office_preauthorized_scopes_and_aadsts65002_handling(tmp_path):
    import urllib.parse

    # 1. Default / Personal account uses unified /common endpoint and GRAPH_CLI_CLIENT_ID
    personal_provider = MsGraphCalendarProvider(
        client_id="04b07795-8ddb-461a-bbee-02f9e1bf7b46",
        account_hint="user@outlook.com",
        auth_file_path=tmp_path / "ms_auth_personal.dat",
    )
    assert personal_provider.client_id == MsGraphCalendarProvider.GRAPH_CLI_CLIENT_ID
    assert personal_provider.tenant_id == "common"

    # 2. Work account hint or organizational tenant automatically diversifies to OFFICE_CLIENT_ID
    auth_file = tmp_path / "ms_auth_office.dat"
    provider = MsGraphCalendarProvider(
        account_hint="milind@wfx.com",
        auth_file_path=auth_file,
    )
    assert provider.client_id == MsGraphCalendarProvider.OFFICE_CLIENT_ID
    assert provider.tenant_id == "organizations"

    captured_payloads = []

    def mock_urlopen(req, timeout=15):
        if req.data:
            captured_payloads.append(urllib.parse.parse_qs(req.data.decode("utf-8")))
        body = {
            "device_code": "dev_123",
            "user_code": "CODE-9999",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        personal_provider.request_device_code()
        provider.request_device_code()
        assert len(captured_payloads) == 2
        assert captured_payloads[0]["client_id"] == [MsGraphCalendarProvider.GRAPH_CLI_CLIENT_ID]
        assert captured_payloads[0]["scope"] == ["Calendars.Read User.Read offline_access"]
        assert captured_payloads[1]["client_id"] == [MsGraphCalendarProvider.OFFICE_CLIENT_ID]
        assert captured_payloads[1]["scope"] == ["https://graph.microsoft.com/.default offline_access"]

    # Verify _parse_aad_error translates AADSTS65002 into a clear actionable guide
    err_body = json.dumps({
        "error": "invalid_request",
        "error_description": "AADSTS65002: Consent between first party application '04b07795-8ddb-461a-bbee-02f9e1bf7b46' and first party resource '00000003-0000-0000-c000-000000000000' must be configured via preauthorization",
    })
    parsed_msg = MsGraphCalendarProvider._parse_aad_error(err_body, "Fallback")
    assert "Sign in with Microsoft Code" in parsed_msg
    assert "Outlook ICS / MAPI" in parsed_msg


# ----------------------------------------------------------------------
# Test 5: Google Calendar API Link Extraction & Single Events
# ----------------------------------------------------------------------
def test_google_calendar_link_extraction_and_meet(tmp_path):
    import time
    auth_file = tmp_path / "google_auth.dat"
    provider = GoogleCalendarProvider(auth_file_path=auth_file)
    provider._access_token = "google_mock_token"
    provider._expires_at = time.time() + 3600

    now_utc = datetime.now(timezone.utc)
    t1_iso = (now_utc + timedelta(hours=1)).isoformat()
    t2_iso = (now_utc + timedelta(hours=2)).isoformat()

    def mock_urlopen(req, timeout=15):
        body = {
            "items": [
                {
                    "id": "google-evt-101",
                    "summary": "Product Architecture",
                    "start": {"dateTime": t1_iso},
                    "end": {"dateTime": t2_iso},
                    "conferenceData": {
                        "entryPoints": [
                            {"entryPointType": "video", "uri": "https://meet.google.com/xyz-uvwx-rst"}
                        ]
                    },
                    "attendees": [{"email": "lead@google.com", "displayName": "Lead"}],
                    "organizer": {"email": "lead@google.com"},
                }
            ]
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        meetings = provider.get_upcoming_meetings(window_minutes=600)
        assert len(meetings) == 1
        m = meetings[0]
        assert m.title == "Product Architecture"
        assert m.platform == "meet"
        assert m.join_url == "https://meet.google.com/xyz-uvwx-rst"
        assert "lead@google.com" in m.attendees_str


# ----------------------------------------------------------------------
# Test 6: Unified Calendar Engine Horizon Categorization
# ----------------------------------------------------------------------
def test_unified_engine_horizon_categorization(qapp):
    engine = UnifiedCalendarEngine(parent=None)

    now_utc = datetime.now(timezone.utc)

    m_today = CalendarMeeting(
        id="t1",
        title="Today's Meeting",
        start_time=now_utc + timedelta(hours=2),
        end_time=now_utc + timedelta(hours=3),
    )
    m_tomorrow = CalendarMeeting(
        id="t2",
        title="Tomorrow's Meeting",
        start_time=now_utc + timedelta(days=1, hours=2),
        end_time=now_utc + timedelta(days=1, hours=3),
    )
    m_future = CalendarMeeting(
        id="t3",
        title="Future Meeting",
        start_time=now_utc + timedelta(days=7),
        end_time=now_utc + timedelta(days=7, hours=1),
    )

    with engine._lock:
        engine._cached_meetings = [m_today, m_tomorrow, m_future]

    assert len(engine.get_today_meetings()) == 1
    assert engine.get_today_meetings()[0].title == "Today's Meeting"

    assert len(engine.get_tomorrow_meetings()) == 1
    assert engine.get_tomorrow_meetings()[0].title == "Tomorrow's Meeting"

    assert len(engine.get_future_meetings(days=30)) == 1
    assert engine.get_future_meetings(days=30)[0].title == "Future Meeting"


# ----------------------------------------------------------------------
# Test 7: 1-Click Join & Notetaker Persistence in Database
# ----------------------------------------------------------------------
def test_database_persistence_of_calendar_fields(tmp_path):
    db_file = tmp_path / "test_vox.db"
    db = LocalDatabase(str(db_file))

    meeting_id = db.create_meeting(
        title="WFX Strategic Review",
        capture_mode="both",
        calendar_event_id="cal-evt-999",
        calendar_join_url="https://teams.microsoft.com/l/meetup-join/123",
        calendar_attendees="milind@wfx.com, ceo@wfx.com",
        calendar_organizer="ceo@wfx.com",
        calendar_provider="ms_graph",
    )

    mtg = db.get_meeting(meeting_id)
    assert mtg is not None
    assert mtg["title"] == "WFX Strategic Review"
    assert mtg["calendar_event_id"] == "cal-evt-999"
    assert mtg["calendar_join_url"] == "https://teams.microsoft.com/l/meetup-join/123"
    assert "milind@wfx.com" in mtg["calendar_attendees"]
    assert mtg["calendar_provider"] == "ms_graph"

    # Update meeting calendar fields
    db.update_meeting(
        meeting_id,
        calendar_event_id="cal-evt-updated",
        calendar_join_url="https://zoom.us/j/999",
    )
    updated = db.get_meeting(meeting_id)
    assert updated["calendar_event_id"] == "cal-evt-updated"
    assert updated["calendar_join_url"] == "https://zoom.us/j/999"


# ----------------------------------------------------------------------
# Test 8: Controller Lifecycle & Consecutive Meetings without restart
# ----------------------------------------------------------------------
def test_controller_start_meeting_from_calendar_lifecycle(qapp, tmp_path):
    from voxlocal.core.controller import AppController

    cfg = AppConfig()
    cfg.storage_dir = str(tmp_path)
    cfg.disable_hotkeys = True

    ctrl = AppController(cfg)
    now_utc = datetime.now(timezone.utc)

    m1 = CalendarMeeting(
        id="cal-m1",
        title="Session 1: Roadmap Review",
        start_time=now_utc,
        end_time=now_utc + timedelta(minutes=30),
        join_url="https://teams.microsoft.com/l/meetup-join/1",
        attendees=["dev@wfx.com"],
        organizer="lead@wfx.com",
        platform="teams",
        provider_type="ms_graph",
    )

    with patch.object(ctrl.meeting_recorder, "start", return_value=(True, "Recording started")), \
         patch.object(ctrl.meeting_recorder, "stop", return_value=True), \
         patch("webbrowser.open"):

        # 1. Start meeting from calendar
        ok1, msg1 = ctrl.start_meeting_from_calendar(m1)
        assert ok1 is True
        assert ctrl._active_meeting_calendar_event_id == "cal-m1"
        assert ctrl._capture_state == "recording"
        m1_id = ctrl._active_meeting_id
        assert m1_id is not None

        # Verify DB has calendar fields
        saved_m1 = ctrl.db.get_meeting(m1_id)
        assert saved_m1["calendar_event_id"] == "cal-m1"
        assert saved_m1["calendar_join_url"] == "https://teams.microsoft.com/l/meetup-join/1"
        assert saved_m1["calendar_provider"] == "ms_graph"

        # Attempting duplicate start must be rejected
        ok_dup, dup_msg = ctrl.start_meeting_from_calendar(m1)
        assert ok_dup is False
        assert "already active" in dup_msg.lower()

        # 2. Finalize session 1
        ctrl.stop_meeting_recording()
        assert ctrl._capture_state == "idle"

        assert ctrl._active_meeting_calendar_event_id == ""

        # 3. Consecutive meeting 2 without app restart
        m2 = CalendarMeeting(
            id="cal-m2",
            title="Session 2: Architecture Deep Dive",
            start_time=now_utc + timedelta(hours=1),
            end_time=now_utc + timedelta(hours=2),
            join_url="https://zoom.us/j/2",
            attendees=["arch@wfx.com"],
            organizer="vp@wfx.com",
            platform="zoom",
            provider_type="zoom",
        )

        ok2, msg2 = ctrl.start_meeting_from_calendar(m2)
        assert ok2 is True
        assert ctrl._active_meeting_calendar_event_id == "cal-m2"
        assert ctrl._capture_state == "recording"
        m2_id = ctrl._active_meeting_id
        assert m2_id is not None
        assert m2_id != m1_id

        saved_m2 = ctrl.db.get_meeting(m2_id)
        assert saved_m2["calendar_event_id"] == "cal-m2"
        assert saved_m2["calendar_join_url"] == "https://zoom.us/j/2"
        assert saved_m2["calendar_provider"] == "zoom"

        ctrl.stop_meeting_recording()
        assert ctrl._capture_state == "idle"


# ----------------------------------------------------------------------
# Test 10: Multi-User DB Isolation, Secret Scrubbing & OAuth Sanitization
# ----------------------------------------------------------------------
def test_multi_user_isolation_secret_scrubbing_and_sanitization(tmp_path):
    from voxlocal.calendar.auth_service import (
        CalendarAuthBroker,
        classify_account_kind,
        sanitize_oauth_error_for_user,
    )

    db = LocalDatabase(tmp_path / "multi_user.db")
    broker = CalendarAuthBroker(db=db)

    # Secrets must never persist into plaintext config.json
    cfg = AppConfig()
    cfg.storage_dir = str(tmp_path)
    cfg_path = cfg.config_file_path()
    cfg.google_calendar_client_secret = "gsec-top-secret"
    cfg.zoom_client_secret = "zsec-top-secret"
    broker.save_app_secret("google_calendar_client_secret", cfg.google_calendar_client_secret, user_id="user_alice")
    broker.save_app_secret("zoom_client_secret", cfg.zoom_client_secret, user_id="user_alice")
    cfg.save()

    raw_cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "gsec-top-secret" not in raw_cfg_text
    assert "zsec-top-secret" not in raw_cfg_text
    assert broker.load_app_secret("google_calendar_client_secret", user_id="user_alice") == "gsec-top-secret"
    assert broker.load_app_secret("google_calendar_client_secret", user_id="user_bob") == ""

    # User-scoped accounts, encrypted vault tokens, events & meeting links stay isolated
    now_utc = datetime.now(timezone.utc)
    db.upsert_calendar_account(
        user_id="user_alice",
        account_id="ms_graph:alice@contoso.com",
        provider_type="ms_graph",
        account_email="alice@contoso.com",
        display_name="Alice",
        account_kind="work",
        tenant_id="contoso-tid",
    )
    db.upsert_calendar_account(
        user_id="user_bob",
        account_id="google:bob@gmail.com",
        provider_type="google",
        account_email="bob@gmail.com",
        display_name="Bob",
        account_kind="personal",
    )
    broker.save_credentials("user_alice", "ms_graph:alice@contoso.com", "ms_graph", {"refresh_token": "alice-rt"})
    broker.save_credentials("user_bob", "google:bob@gmail.com", "google", {"refresh_token": "bob-rt"})

    db.upsert_calendar_events(
        [
            CalendarMeeting(
                id="ev-alice-1",
                title="Alice Executive Brief",
                start_time=now_utc + timedelta(hours=1),
                end_time=now_utc + timedelta(hours=2),
                account_id="ms_graph:alice@contoso.com",
                provider_type="ms_graph",
            )
        ],
        user_id="user_alice",
        account_id="ms_graph:alice@contoso.com",
    )
    db.upsert_calendar_events(
        [
            CalendarMeeting(
                id="ev-bob-1",
                title="Bob Dentist",
                start_time=now_utc + timedelta(hours=1),
                end_time=now_utc + timedelta(hours=2),
                account_id="google:bob@gmail.com",
                provider_type="google",
            )
        ],
        user_id="user_bob",
        account_id="google:bob@gmail.com",
    )

    alice_accts = db.list_calendar_accounts(user_id="user_alice")
    bob_accts = db.list_calendar_accounts(user_id="user_bob")
    assert [a["account_id"] for a in alice_accts] == ["ms_graph:alice@contoso.com"]
    assert [a["account_id"] for a in bob_accts] == ["google:bob@gmail.com"]
    assert len(db.list_calendar_events(user_id="user_alice")) == 1
    assert db.list_calendar_events(user_id="user_alice")[0]["title"] == "Alice Executive Brief"
    assert len(db.list_calendar_events(user_id="user_bob")) == 1
    assert db.list_calendar_events(user_id="user_bob")[0]["title"] == "Bob Dentist"
    assert broker.load_credentials("user_alice", "google:bob@gmail.com") is None

    db.create_meeting(
        title="Alice Recorded Call",
        user_id="user_alice",
        calendar_event_id="ev-alice-1",
        calendar_account_id="ms_graph:alice@contoso.com",
    )
    assert "ev-alice-1" in db.list_meetings_by_calendar_event_ids(user_id="user_alice")
    assert "ev-alice-1" not in db.list_meetings_by_calendar_event_ids(user_id="user_bob")

    # Technical errors must sanitize cleanly without leaking AADSTS/OAuth JSON
    code_admin, msg_admin = sanitize_oauth_error_for_user(
        '{"error":"consent_required","error_description":"AADSTS65001: user has not consented trace=999"}',
        "ms_graph",
    )
    assert code_admin == "ADMIN_APPROVAL_REQUIRED"
    assert "AADSTS65001" not in msg_admin
    assert "IT administrator" in msg_admin

    code_rev, msg_rev = sanitize_oauth_error_for_user("HTTP 401 invalid_grant token revoked", "google")
    assert code_rev == "AUTH_EXPIRED"
    assert "invalid_grant" not in msg_rev

    code_den, msg_den = sanitize_oauth_error_for_user("access_denied by resource owner", "zoom")
    assert code_den == "AUTH_DENIED"
    assert "access_denied" not in msg_den

    code_net, msg_net = sanitize_oauth_error_for_user("URLError: [WinError 11001] getaddrinfo failed", "google")
    assert code_net == "NETWORK_OFFLINE"
    assert "WinError" not in msg_net

    assert classify_account_kind("ms_graph", "u@outlook.com", "9188040d-6c67-4c5b-b112-36a304b66dad") == "personal"
    assert classify_account_kind("ms_graph", "u@acme.com", "tenant-guid-123") == "work"


# ----------------------------------------------------------------------
# Test 11: Microsoft Graph Delta Sync, @removed & 410 Fallback
# ----------------------------------------------------------------------
def test_ms_graph_delta_sync_removed_410_and_tid_classification(tmp_path):
    import base64

    prov = MsGraphCalendarProvider(auth_file_path=tmp_path / "ms_delta.dat")
    # Build mock JWT with consumer MSA tid vs enterprise tid check
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"tid": "enterprise-tenant-abc", "upn": "alex@enterprise.com"}).encode()
    ).decode().rstrip("=")
    fake_jwt = f"{hdr}.{payload}.sig"
    prov._fetch_user_profile_upn = lambda: "alex@enterprise.com"
    prov._record_token_response({
        "access_token": fake_jwt,
        "refresh_token": "rt-ms",
        "expires_in": 3600,
    })
    assert prov.get_account_info()["account_kind"] == "work"
    assert prov.get_account_info()["tenant_id"] == "enterprise-tenant-abc"

    st_dt = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    en_dt = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    delta_responses = [
        # Page 1 initial sync -> nextLink -> page 2 deltaLink
        (
            {
                "value": [
                    {
                        "id": "ms-ev-1",
                        "iCalUId": "uid-ms-1",
                        "subject": "Initial Architecture Call",
                        "start": {"dateTime": st_dt, "timeZone": "UTC"},
                        "end": {"dateTime": en_dt, "timeZone": "UTC"},
                        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/19%3adelta1"},
                        "organizer": {"emailAddress": {"name": "Alex", "address": "alex@enterprise.com"}},
                    }
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$skiptoken=p2",
            },
            200,
            None,
        ),
        (
            {
                "value": [
                    {
                        "id": "ms-ev-2",
                        "iCalUId": "uid-ms-2",
                        "subject": "Vendor Review",
                        "start": {"dateTime": st_dt, "timeZone": "UTC"},
                        "end": {"dateTime": en_dt, "timeZone": "UTC"},
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=dtok1",
            },
            200,
            None,
        ),
        # Incremental delta call returning modified ms-ev-1 and @removed ms-ev-2
        (
            {
                "value": [
                    {
                        "id": "ms-ev-1",
                        "iCalUId": "uid-ms-1",
                        "subject": "Updated Architecture Call",
                        "start": {"dateTime": st_dt, "timeZone": "UTC"},
                        "end": {"dateTime": en_dt, "timeZone": "UTC"},
                    },
                    {"id": "ms-ev-2", "@removed": {"reason": "deleted"}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=dtok2",
            },
            200,
            None,
        ),
        # Simulated 410 Gone on stale deltaLink followed by fresh resync
        (None, 410, "HTTP_410"),
        (
            {
                "value": [
                    {
                        "id": "ms-ev-1",
                        "subject": "Full Reset Architecture Call",
                        "start": {"dateTime": st_dt, "timeZone": "UTC"},
                        "end": {"dateTime": en_dt, "timeZone": "UTC"},
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=dtok3",
            },
            200,
            None,
        ),
    ]
    called_urls = []

    def _fake_get(url):
        called_urls.append(url)
        return delta_responses.pop(0)

    prov._http_get_json_with_recovery = _fake_get

    res1 = prov.sync_events_delta(window_minutes=1440, use_delta=True)
    assert res1["ok"] is True
    assert res1["is_incremental"] is False
    assert len(res1["meetings"]) == 2
    assert prov._delta_links["default"].endswith("dtok1")

    res2 = prov.sync_events_delta(window_minutes=1440, use_delta=True)
    assert res2["ok"] is True
    assert res2["is_incremental"] is True
    assert [m.title for m in res2["meetings"]] == ["Updated Architecture Call"]
    assert res2["removed_ids"] == ["ms-ev-2"]
    assert "dtok1" in called_urls[2]

    res3 = prov.sync_events_delta(window_minutes=1440, use_delta=True)
    assert res3["ok"] is True
    assert res3["is_incremental"] is False
    assert [m.title for m in res3["meetings"]] == ["Full Reset Architecture Call"]


# ----------------------------------------------------------------------
# Test 12: Google Incremental syncToken, Cancelled Deletion & pageToken Fix
# ----------------------------------------------------------------------
def test_google_sync_token_pagetoken_and_workspace_classification(tmp_path):
    prov = GoogleCalendarProvider(auth_file_path=tmp_path / "g_sync.dat")
    prov._apply_token_data({
        "access_token": "gtok",
        "refresh_token": "grt",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        "account_email": "maya@acme.io",
        "hosted_domain": "acme.io",
    })
    assert prov.get_account_info()["account_kind"] == "work"

    # Verify pageToken URL replacement never duplicates query params
    u1 = GoogleCalendarProvider._with_query_param(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?singleEvents=true&pageToken=old",
        "pageToken",
        "new_tok",
    )
    assert u1.count("pageToken=") == 1
    assert "pageToken=new_tok" in u1

    st_dt = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    en_dt = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    g_responses = [
        # Initial 2 pages with nextPageToken + nextSyncToken
        (
            {
                "items": [
                    {
                        "id": "g-ev-1",
                        "iCalUID": "guid-1@google.com",
                        "summary": "Design Review",
                        "start": {"dateTime": st_dt},
                        "end": {"dateTime": en_dt},
                        "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
                    }
                ],
                "nextPageToken": "p2tok",
            },
            200,
            None,
        ),
        (
            {
                "items": [
                    {
                        "id": "g-ev-2",
                        "summary": "Cancelled Event",
                        "status": "cancelled",
                    }
                ],
                "nextSyncToken": "sync-tok-1",
            },
            200,
            None,
        ),
        # Incremental 410 Gone fallback to full resync
        (None, 410, "HTTP_410"),
        (
            {
                "items": [
                    {
                        "id": "g-ev-1",
                        "summary": "Design Review Resynced",
                        "start": {"dateTime": st_dt},
                        "end": {"dateTime": en_dt},
                    }
                ],
                "nextSyncToken": "sync-tok-2",
            },
            200,
            None,
        ),
    ]
    g_urls = []

    def _fake_g_get(url):
        g_urls.append(url)
        return g_responses.pop(0)

    prov._http_get_json_with_recovery = _fake_g_get

    r1 = prov.sync_events_incremental(window_minutes=1440, use_sync_token=True)
    assert r1["ok"] is True
    assert len(r1["meetings"]) == 1
    assert r1["meetings"][0].title == "Design Review"
    assert r1["removed_ids"] == ["g-ev-2"]
    assert "pageToken=p2tok" in g_urls[1]
    assert g_urls[1].count("pageToken=") == 1
    assert prov._sync_tokens["primary"] == "sync-tok-1"

    r2 = prov.sync_events_incremental(window_minutes=1440, use_sync_token=True)
    assert r2["ok"] is True
    assert r2["is_incremental"] is False
    assert r2["meetings"][0].title == "Design Review Resynced"
    assert prov._sync_tokens["primary"] == "sync-tok-2"


# ----------------------------------------------------------------------
# Test 13: Zoom Recurring Occurrences & ICS RECURRENCE-ID Override
# ----------------------------------------------------------------------
def test_zoom_recurring_occurrences_and_ics_recurrence_override(tmp_path):
    from voxlocal.calendar.ics_provider import IcsCalendarProvider, parse_ics_events

    zprov = ZoomCalendarProvider(auth_file_path=tmp_path / "zoom_rec.dat")
    zprov._apply_token_data({
        "access_token": "ztok",
        "refresh_token": "zrt",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        "account_email": "host@acme.com",
        "zoom_account_type": 2,
    })
    assert zprov.get_account_info()["account_kind"] == "work"

    # Zoom PKCE S256 check
    url = zprov.build_authorization_url("http://127.0.0.1:9999/cb", state="st1", code_challenge="pkce_s256_challenge")
    assert "code_challenge=pkce_s256_challenge" in url
    assert "code_challenge_method=S256" in url

    t1 = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00Z")
    t2 = (datetime.now(timezone.utc) + timedelta(days=8)).strftime("%Y-%m-%dT10:00:00Z")

    def _fake_zoom_get(req_url):
        if "type=upcoming" in req_url:
            return (
                {
                    "meetings": [
                        {
                            "id": 99001122,
                            "topic": "Weekly Product Sync",
                            "type": 8,
                            "join_url": "https://zoom.us/j/99001122",
                            "occurrences": [
                                {"occurrence_id": "101", "start_time": t1, "duration": 45, "status": "available"},
                                {"occurrence_id": "102", "start_time": t2, "duration": 45, "status": "available"},
                                {"occurrence_id": "103", "start_time": t2, "duration": 45, "status": "deleted"},
                            ],
                        }
                    ]
                },
                200,
                None,
            )
        return ({"meetings": []}, 200, None)

    zprov._http_get_json_with_recovery = _fake_zoom_get
    z_mtgs = zprov.get_upcoming_meetings(window_minutes=60 * 24 * 30)
    assert len(z_mtgs) == 2
    assert all(m.is_recurring for m in z_mtgs)
    assert all(m.series_master_id == "99001122" for m in z_mtgs)

    # ICS provider: provider_type must be "ics", and RECURRENCE-ID overrides original series instance
    ics_p = IcsCalendarProvider(auth_file_path=tmp_path / "ics.dat")
    assert ics_p.provider_type == "ics"

    base_utc = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    day1_str = base_utc.strftime("%Y%m%dT%H%M%SZ")
    day2_str = (base_utc + timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
    day2_mod_start = (base_utc + timedelta(days=1, hours=2)).strftime("%Y%m%dT%H%M%SZ")
    day2_mod_end = (base_utc + timedelta(days=1, hours=3)).strftime("%Y%m%dT%H%M%SZ")
    end1_str = (base_utc + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")

    ics_text = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "X-WR-CALNAME:Team ICS",
        "BEGIN:VEVENT",
        "UID:series-ics-1@vox",
        f"DTSTART:{day1_str}",
        f"DTEND:{end1_str}",
        "RRULE:FREQ=DAILY;COUNT=3",
        "SUMMARY:Daily Huddle",
        "DESCRIPTION:Join https://teams.microsoft.com/l/meetup-join/19%3aicsseries",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:series-ics-1@vox",
        f"RECURRENCE-ID:{day2_str}",
        f"DTSTART:{day2_mod_start}",
        f"DTEND:{day2_mod_end}",
        "SUMMARY:Daily Huddle (Moved)",
        "END:VEVENT",
        "END:VCALENDAR",
    ])
    cal_name, parsed_ics = parse_ics_events(
        ics_text,
        window_start=base_utc - timedelta(hours=1),
        window_end=base_utc + timedelta(days=5),
        account_id="ics:abc",
    )
    assert cal_name == "Team ICS"
    assert len(parsed_ics) == 3
    moved = [m for m in parsed_ics if m.is_modified_occurrence]
    assert len(moved) == 1
    assert moved[0].title == "Daily Huddle (Moved)"
    assert moved[0].start_time == (base_utc + timedelta(days=1, hours=2))


# ----------------------------------------------------------------------
# Test 14: Recurring Dedup, Timezone Boundaries, Fault Isolation & Restart Recovery
# ----------------------------------------------------------------------
def test_recurring_series_not_collapsed_fault_isolation_and_restart_recovery(tmp_path):
    db = LocalDatabase(tmp_path / "engine_restart.db")
    engine = UnifiedCalendarEngine(db=db, user_id="default")

    now_utc = datetime.now(timezone.utc)
    day1 = now_utc + timedelta(hours=2)
    day2 = now_utc + timedelta(days=7, hours=2)

    class _GoodProvider(BaseCalendarProvider):
        def __init__(self, pid: str, mtgs: list, pt: str = "google", email: str = "a@acme.com"):
            self._id = pid
            self._mtgs = mtgs
            self._pt = pt
            self._email = email
            self.selected_cals = ["primary"]

        @property
        def provider_type(self) -> str:
            return self._pt

        def authenticate(self, interactive: bool = True) -> bool:
            return True

        def disconnect(self) -> bool:
            return True

        def is_authenticated(self) -> bool:
            return True

        def get_upcoming_meetings(self, window_minutes: int = 60):
            return list(self._mtgs)

        def get_provider_status(self):
            return {
                "provider_type": self._pt,
                "account_id": self._id,
                "account_email": self._email,
                "display_name": "Good Account",
                "account_kind": "work",
                "is_authenticated": True,
                "is_healthy": True,
                "connection_state": "connected",
                "error_state": None,
                "friendly_error": None,
                "selected_calendars": self.selected_cals,
            }

    class _FailingProvider(_GoodProvider):
        def get_upcoming_meetings(self, window_minutes: int = 60):
            raise RuntimeError("HTTP 429 rate_limit exceeded")

        def get_provider_status(self):
            st = super().get_provider_status()
            st.update({
                "is_healthy": False,
                "connection_state": "failed",
                "error_state": "HTTP_429_RATE_LIMIT",
                "friendly_error": "Rate limited",
            })
            return st

    # 2 occurrences of SAME recurring Zoom link on different days must NOT collapse,
    # whereas same timestamp meeting across 2 accounts MUST merge source_accounts.
    occ1 = CalendarMeeting(
        id="rec-occ-1",
        title="Weekly Architecture",
        start_time=day1,
        end_time=day1 + timedelta(minutes=45),
        join_url="https://zoom.us/j/555444333",
        platform="zoom",
        provider_type="google",
        account_id="google:good@acme.com",
        provider_account="good@acme.com",
        is_recurring=True,
    )
    occ2 = CalendarMeeting(
        id="rec-occ-2",
        title="Weekly Architecture",
        start_time=day2,
        end_time=day2 + timedelta(minutes=45),
        join_url="https://zoom.us/j/555444333",
        platform="zoom",
        provider_type="google",
        account_id="google:good@acme.com",
        provider_account="good@acme.com",
        is_recurring=True,
    )
    dup_ms = CalendarMeeting(
        id="ms-dup-occ-1",
        title="Weekly Architecture",
        start_time=day1 + timedelta(minutes=1),
        end_time=day1 + timedelta(minutes=46),
        join_url="https://zoom.us/j/555444333",
        platform="zoom",
        provider_type="ms_graph",
        account_id="ms_graph:good@contoso.com",
        provider_account="good@contoso.com",
        is_recurring=True,
    )

    p_good1 = _GoodProvider("google:good@acme.com", [occ1, occ2], pt="google", email="good@acme.com")
    p_good2 = _GoodProvider("ms_graph:good@contoso.com", [dup_ms], pt="ms_graph", email="good@contoso.com")
    p_bad = _FailingProvider("zoom:bad@acme.com", [], pt="zoom", email="bad@acme.com")

    engine.register_provider("google:good@acme.com", p_good1, sync_immediately=False)
    engine.register_provider("ms_graph:good@contoso.com", p_good2, sync_immediately=False)
    engine.register_provider("zoom:bad@acme.com", p_bad, sync_immediately=False)

    merged = engine.poll_all_sync(force=True)
    # 2 distinct weekly occurrences preserved; day1 across Google + MS merged into 1
    assert len(merged) == 2
    first = min(merged, key=lambda m: m.start_time)
    assert len(first.source_accounts) == 2
    assert set(first.source_providers) == {"google", "ms_graph"}
    assert "zoom:bad@acme.com" in engine._account_backoff_until  # failing account backed off without blocking others

    # Offline / application restart recovery from DB cache
    db_only_engine = UnifiedCalendarEngine(db=db, user_id="default")
    db_only_engine.hydrate_from_database()
    assert len(db_only_engine.get_upcoming_meetings()) == 2
    # Local timezone property works cleanly across tz-aware timestamps
    assert db_only_engine.get_upcoming_meetings()[0].local_start_time.tzinfo is not None


# ----------------------------------------------------------------------
# Test 15: Notetaker Smart Overlapping Match & Prepare-Notetaker Bridge
# ----------------------------------------------------------------------
def test_controller_overlapping_meeting_match_and_prepare_notetaker(tmp_path):
    from voxlocal.core.controller import AppController

    cfg = AppConfig()
    cfg.storage_dir = str(tmp_path)
    cfg.disable_hotkeys = True
    cfg.ensure_directories()
    ctrl = AppController(cfg)

    now_utc = datetime.now(timezone.utc)
    m_teams = CalendarMeeting(
        id="ov-teams",
        title="Product Alignment",
        start_time=now_utc - timedelta(minutes=2),
        end_time=now_utc + timedelta(minutes=30),
        join_url="https://teams.microsoft.com/l/meetup-join/19%3ateamsov",
        platform="teams",
        provider_type="ms_graph",
        account_id="ms_graph:a@contoso.com",
        attendees=["Priya Sharma", "Sam Lee"],
        organizer="Alex Chen",
    )
    m_zoom = CalendarMeeting(
        id="ov-zoom",
        title="Customer Demo Call",
        start_time=now_utc - timedelta(minutes=1),
        end_time=now_utc + timedelta(minutes=45),
        join_url="https://zoom.us/j/123456789",
        platform="zoom",
        provider_type="zoom",
        account_id="zoom:a@acme.com",
        attendees=["Dana White"],
        organizer="Chris Doe",
    )
    ctrl.calendar_engine._cached_meetings = [m_teams, m_zoom]

    matched_zoom = ctrl.match_active_call_to_calendar_meeting(
        detected_platform="zoom",
        window_title="Zoom Meeting - Customer Demo",
    )
    assert matched_zoom is not None
    assert matched_zoom.id == "ov-zoom"

    matched_teams = ctrl.match_active_call_to_calendar_meeting(
        detected_platform="teams",
        window_title="Microsoft Teams",
    )
    assert matched_teams is not None
    assert matched_teams.id == "ov-teams"

    emitted = []
    ctrl.calendar_prepare_notetaker_requested.connect(lambda m: emitted.append(m))
    ok, _msg = ctrl.prepare_notetaker_for_meeting(m_zoom, start_recording=False)
    assert ok is True
    assert len(emitted) == 1
    assert ctrl._capture_state == "idle"  # Preserve recording consent: preparing never auto-records
    assert ctrl._active_meeting_calendar_event_id == "ov-zoom"
    assert getattr(ctrl, "_prepared_priming_terms", None) is not None


# ----------------------------------------------------------------------
# Test 16: Dedicated CalendarPage, Meeting Details & MainWindow /calendar UI
# ----------------------------------------------------------------------
def test_calendar_page_and_main_window_calendar_routing_ui(qapp, tmp_path):
    from voxlocal.core.controller import AppController
    from voxlocal.ui.calendar_page import CalendarMeetingDetailsDialog, CalendarPage
    from voxlocal.ui.main_window import MainWindow

    cfg = AppConfig()
    cfg.storage_dir = str(tmp_path)
    cfg.disable_hotkeys = True
    cfg.ensure_directories()
    ctrl = AppController(cfg)
    db = ctrl.db

    now_utc = datetime.now(timezone.utc)
    today_m = CalendarMeeting(
        id="ui-today-1",
        title="Q4 Roadmap Review",
        start_time=now_utc + timedelta(hours=1),
        end_time=now_utc + timedelta(hours=2),
        join_url="https://meet.google.com/abc-defg-hij",
        platform="meet",
        provider_type="google",
        account_id="google:work@acme.com",
        provider_account="work@acme.com",
        organizer="Alex",
        attendees=["Jordan", "Taylor"],
    )
    tomorrow_m = CalendarMeeting(
        id="ui-tom-1",
        title="Sprint Retro",
        start_time=now_utc + timedelta(days=1, hours=2),
        end_time=now_utc + timedelta(days=1, hours=3),
        join_url="https://teams.microsoft.com/l/meetup-join/19%3auitom1",
        platform="teams",
        provider_type="ms_graph",
        account_id="ms_graph:work@contoso.com",
        provider_account="work@contoso.com",
    )
    db.upsert_calendar_account(
        user_id="default",
        account_id="google:work@acme.com",
        provider_type="google",
        account_email="work@acme.com",
        display_name="Work Google",
        account_kind="work",
        is_healthy=True,
        connection_state="connected",
    )
    db.upsert_calendar_events([today_m], user_id="default", account_id="google:work@acme.com")
    ctrl.calendar_engine._cached_meetings = [today_m, tomorrow_m]

    win = MainWindow(controller=ctrl)
    # Must preserve 8 WORKSPACES and clamping integer 999 -> 7 (Settings) for existing E2E contracts
    assert len(MainWindow.WORKSPACES) == 8
    win.navigate_to_workspace(999)
    assert win.workspace_stack.currentIndex() == 7

    # Route "/calendar" activates CalendarPage at index 8 and checks sidebar btn_calendar
    win.navigate_to_workspace("/calendar")
    assert win.workspace_stack.currentIndex() == 8
    assert isinstance(win.calendar_page, CalendarPage)
    assert win.btn_calendar.isChecked() is True
    assert win.calendar_page.lbl_kpi_accounts.text().isdigit()

    # Filter modes & search work cleanly
    win.calendar_page.set_time_filter("today")
    filtered_today = win.calendar_page._filter_meetings([today_m, tomorrow_m])
    assert any(m.id == "ui-today-1" for m in filtered_today)

    win.calendar_page._on_search_changed("Sprint")
    filtered_search = win.calendar_page._filter_meetings([today_m, tomorrow_m])
    assert filtered_search == [] or all("sprint" in m.title.lower() for m in filtered_search)

    # Meeting Details dialog renders safely in both Light & Dark themes
    dlg = CalendarMeetingDetailsDialog(today_m, controller=ctrl, theme="dark")
    assert "Q4 Roadmap Review" in dlg.windowTitle()
    dlg.close()

    # Prepare Notetaker from Calendar switches cleanly to Notetaker and shows prepared banner
    win._on_prepare_notetaker_from_calendar(today_m)
    assert win.workspace_stack.currentIndex() == 1
    assert win.notetaker_page.prepared_banner.isVisibleTo(win.notetaker_page) or "Q4 Roadmap Review" in win.notetaker_page.lbl_prepared_summary.text()

    win.close()



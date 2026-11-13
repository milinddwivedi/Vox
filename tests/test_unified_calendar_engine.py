"""Comprehensive Test Suite for VOX Unified Calendar Engine & Multi-Provider Integration."""

import json
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QCoreApplication

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
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
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


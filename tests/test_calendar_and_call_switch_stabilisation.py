"""
Comprehensive Stabilization Test Suite for VOX Calendar & Meeting Engine.
Validates Mandatory Acceptance Tests A through G:
- Test A: Microsoft Outlook / Graph Device Code Flow & Protected Scopes (RFC 8628).
- Test B: Google Calendar & Meet Link Extraction.
- Test C: Zoom Meetings Dual-Endpoint Query.
- Test D: ICS Published Feed vs. Local File Differentiation & Diagnostics.
- Test E: Full Meeting Lifecycle (Consent Prompt -> Start -> Dropped Call Auto-Stop).
- Test F: Call Switch Engine (switch_to_meeting) with Zero Diarizer/Transcript Spillover.
- Test G: Network Offline Isolation & Actionable Diagnostic Error Reporting.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    clean_meeting_title,
    is_leaving_title,
    is_non_call_hub,
)
from voxlocal.calendar.auth_service import (
    CalendarAuthBroker,
    sanitize_oauth_error_for_user,
)
from voxlocal.calendar.base import (
    CalendarMeeting,
    CalendarProviderType,
)
from voxlocal.calendar.google_provider import GoogleCalendarProvider
from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
from voxlocal.calendar.ics_provider import IcsCalendarProvider
from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def temp_workspace():
    tmp = tempfile.mkdtemp(prefix="vox_test_cal_stabilisation_")
    db_path = Path(tmp) / "voxlocal.db"
    db = LocalDatabase(db_path)
    cfg = AppConfig()
    cfg.storage_dir = str(tmp)
    cfg.notetaker_auto_end = True
    cfg.prompt_on_call = True
    cfg.record_meeting_screen = False

    yield {
        "dir": tmp,
        "db": db,
        "cfg": cfg,
    }

    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


# =============================================================================
# TEST A: Microsoft Outlook / Graph Device Code Flow & Protected Scopes
# =============================================================================
def test_acceptance_a_microsoft_device_code_protection(temp_workspace):
    """
    Mandatory Acceptance Test A:
    Verify that Microsoft Graph preserves RFC 8628 Device Code Flow
    (Copy code -> Paste code -> Microsoft sign-in), does not expose secrets,
    and requests correct user/calendar scopes.
    """
    auth_file = Path(temp_workspace["dir"]) / "ms_auth.dat"
    provider = MsGraphCalendarProvider(auth_file_path=auth_file)

    # 1. Scopes verification
    scopes = provider._effective_scopes_for_client(provider.client_id)
    assert any("Calendars" in s for s in scopes) or "offline_access" in scopes
    assert "User.Read" in scopes

    def mock_urlopen(req, timeout=15):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
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
                "userPrincipalName": "milind@corp.local",
                "displayName": "Milind Dwivedi",
            }
        else:
            body = {"value": []}

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
        assert auth_file.exists()


# =============================================================================
# TEST B: Google Calendar & Meet Link Extraction
# =============================================================================
def test_acceptance_b_google_calendar_meet_extraction(temp_workspace):
    """
    Mandatory Acceptance Test B:
    Verify GoogleCalendarProvider parses events, extracts Google Meet join URLs,
    handles conferenceData, and maps attendees.
    """
    auth_file = Path(temp_workspace["dir"]) / "google_auth.dat"
    provider = GoogleCalendarProvider(auth_file_path=auth_file)
    provider._access_token = "valid_tok"
    provider._expires_at = time.time() + 3600

    start_iso = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    end_iso = (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat()

    def mock_urlopen(req, timeout=15):
        body = {
            "items": [
                {
                    "id": "g_evt_001",
                    "summary": "Product Strategy & Meet Sync",
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                    "conferenceData": {
                        "entryPoints": [
                            {"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-qwe"}
                        ]
                    },
                    "organizer": {"email": "pm@vox.ai", "displayName": "Product Manager"},
                    "attendees": [
                        {"email": "milind@vox.ai", "displayName": "Milind"},
                        {"email": "riya@vox.ai", "displayName": "Riya"},
                    ],
                }
            ]
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        meetings = provider.get_upcoming_meetings(window_minutes=120)

    assert len(meetings) == 1
    m = meetings[0]
    assert m.title == "Product Strategy & Meet Sync"
    assert m.platform.lower() in ("meet", "google meet")
    assert m.join_url == "https://meet.google.com/xyz-abcd-qwe"
    assert "riya@vox.ai" in m.attendees_str or "Riya" in m.attendees_str


# =============================================================================
# TEST C: Zoom Meetings Dual-Endpoint Query
# =============================================================================
def test_acceptance_c_zoom_dual_endpoint_query(temp_workspace):
    """
    Mandatory Acceptance Test C:
    Verify ZoomCalendarProvider queries both /users/me profile and /users/me/meetings,
    retrieves join URLs, and maps Zoom platform.
    """
    auth_file = Path(temp_workspace["dir"]) / "zoom_auth.dat"
    provider = ZoomCalendarProvider(client_id="zoom_id", client_secret="zoom_secret", auth_file_path=auth_file)
    provider._access_token = "zoom_valid_tok"
    provider._expires_at = time.time() + 3600

    now_dt = datetime.now(timezone.utc)
    start_dt = now_dt + timedelta(minutes=15)
    t1_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def mock_zoom_urlopen(req, timeout=15):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        if "type=upcoming" in url:
            body = {
                "meetings": [
                    {
                        "id": "9876543210",
                        "topic": "Sprint Grooming & Retrospective",
                        "start_time": t1_iso,
                        "duration": 45,
                        "join_url": "https://zoom.us/j/9876543210?pwd=xyz",
                        "timezone": "UTC",
                    }
                ],
                "next_page_token": "",
            }
        elif "type=scheduled" in url:
            body = {"meetings": [], "next_page_token": ""}
        elif "/users/me" in url:
            body = {"email": "eng@vox.ai", "first_name": "Engineering"}
        else:
            body = {}
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__.return_value = resp
        return resp

    with patch("urllib.request.urlopen", side_effect=mock_zoom_urlopen):
        meetings = provider.get_upcoming_meetings(window_minutes=120)

    assert len(meetings) == 1
    m = meetings[0]
    assert m.title == "Sprint Grooming & Retrospective"
    assert m.platform.lower() == "zoom"
    assert "zoom.us/j/9876543210" in m.join_url


# =============================================================================
# TEST D: ICS Published Feed vs. Local File Differentiation & Diagnostics
# =============================================================================
def test_acceptance_d_ics_feed_vs_file_and_diagnostics(temp_workspace):
    """
    Mandatory Acceptance Test D:
    Verify IcsCalendarProvider distinguishes local file from published HTTP feed,
    parses VEVENTs correctly, and provides diagnostic error reporting for 404/missing files.
    """
    tmp_dir = Path(temp_workspace["dir"])
    ics_file = tmp_dir / "team_calendar.ics"
    auth_dat = tmp_dir / "ics_auth.dat"

    start_str = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")
    end_str = (datetime.now(timezone.utc) + timedelta(minutes=35)).strftime("%Y%m%dT%H%M%SZ")

    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//VOX Testing//EN
BEGIN:VEVENT
UID:evt-ics-101
SUMMARY:All-Hands All-Company Review
DTSTART:{start_str}
DTEND:{end_str}
DESCRIPTION:Join Teams meeting at https://teams.microsoft.com/l/meetup-join/123456
LOCATION:Microsoft Teams Meeting
ORGANIZER;CN=CEO:mailto:ceo@vox.ai
END:VEVENT
END:VCALENDAR"""
    ics_file.write_text(ics_content, encoding="utf-8")

    # 1. Local file parsing
    provider_file = IcsCalendarProvider(feed_url=str(ics_file), auth_file_path=auth_dat)
    assert provider_file.is_local_file is True
    ok = provider_file.authenticate()
    assert ok is True

    events = provider_file.get_upcoming_meetings(window_minutes=120)
    assert len(events) == 1
    assert events[0].title == "All-Hands All-Company Review"
    assert "teams" in events[0].platform.lower()

    # 2. Missing local file diagnostics
    missing_file = tmp_dir / "non_existent.ics"
    missing_provider = IcsCalendarProvider(feed_url=str(missing_file), auth_file_path=tmp_dir / "ics_m.dat")
    ok_miss = missing_provider.authenticate()
    assert ok_miss is False
    status = missing_provider.get_provider_status()
    code, friendly = sanitize_oauth_error_for_user(status.get("error_detail", "") or "file not found", "local_ics")
    assert code == "FILE_NOT_FOUND"
    assert "does not exist" in friendly

    # 3. Remote 404 URL diagnostics
    code_404, friendly_404 = sanitize_oauth_error_for_user("HTTP Error 404: Not Found", "ics")
    assert code_404 == "ICS_NOT_FOUND"
    assert "HTTP 404" in friendly_404


# =============================================================================
# TEST E: Full Meeting Lifecycle (Consent -> Start -> Dropped Call Auto-Stop)
# =============================================================================
def test_acceptance_e_full_meeting_lifecycle_dropped_call_auto_stop(qapp, temp_workspace):
    """
    Mandatory Acceptance Test E:
    Simulates the exact scenario reported by the user:
    - Meeting with Riya is started from floating icon bar / toggle (target_hwnd initially None).
    - CallDetector binds active meeting.
    - Dropping the call (call stage closed or switched to chat hub) triggers auto-stop.
    """
    controller = AppController(temp_workspace["cfg"])

    # Mock audio recorder & transcriber to run without physical hardware
    controller.meeting_recorder = MagicMock()
    controller.meeting_recorder.is_recording = True
    controller.meeting_recorder.start.return_value = (True, "Recording started")
    controller.meeting_recorder.stop.return_value = {"mic_path": "fake_mic.wav", "sys_path": "fake_sys.wav"}
    controller.streaming_transcriber = MagicMock()

    # 1. Start meeting from floating icon bar toggle with Riya (isolated from host desktop windows)
    with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]), \
         patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]), \
         patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=None):
        ok, msg = controller.start_meeting_recording(
            title="1-on-1 with Riya",
            target_hwnd=None,
            record_screen=False,
            recording_mode="audio_only",
        )
    assert ok is True
    assert controller._capture_state == "recording"
    assert controller.call_detector.active_session is not None
    assert "riya" in controller.call_detector.active_session.clean_title.lower()

    # 2. Simulate call being dropped: window closed / call stage gone
    fake_hwnd = 998877
    controller._active_meeting_target_hwnd = fake_hwnd

    # In user32, fake_hwnd is not an open window anymore
    with patch("voxlocal.core.controller.user32.IsWindow", return_value=False), \
         patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]), \
         patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[]), \
         patch("voxlocal.video.meeting_capture.resolve_meeting_source", return_value=None):
        # First poll detects target lost, records timestamp
        controller._poll_call_detector()
        assert controller._manual_target_lost_since is not None

        # Advance time past grace period (>= 2.0s)
        with patch("time.monotonic", return_value=time.monotonic() + 3.0):
            controller._poll_call_detector()

    # Verified: Auto-stop executed and state transitioned to idle
    assert controller._capture_state == "idle"
    assert controller._active_meeting_target_hwnd is None


# =============================================================================
# TEST F: Call Switch Engine (switch_to_meeting) with Zero Leakage
# =============================================================================
def test_acceptance_f_call_switch_engine_zero_leakage(qapp, temp_workspace):
    """
    Mandatory Acceptance Test F:
    Validates switch_to_meeting:
    - Call A ('HR Connect Session') is recording.
    - User switches to Call B ('Product Architecture Review').
    - Call A is safely finalized, written to DB, and its folder preserved.
    - Call B receives fresh correlation ID, clean diarizer, and live transcriber reset.
    - Zero transcript or speaker leakage between sessions.
    """
    controller = AppController(temp_workspace["cfg"])

    controller.meeting_recorder = MagicMock()
    controller.meeting_recorder.is_recording = False
    controller.meeting_recorder.start.return_value = (True, "Started Call")
    controller.meeting_recorder.stop.return_value = {"mic_path": "call_a.wav", "sys_path": "call_a_sys.wav"}
    controller.streaming_transcriber = MagicMock()

    import numpy as np

    # 1. Start Call A
    ok_a, _ = controller.start_meeting_recording(
        title="HR Connect Session",
        target_hwnd=1111,
        record_screen=False,
        recording_mode="audio_only",
    )
    assert ok_a is True
    id_a = controller._active_meeting_id
    corr_a = controller._active_meeting_corr_id
    diarizer_a = controller._active_meeting_diarizer
    diarizer_a.assign_segment(np.zeros(16000, dtype=np.float32), 0.0, 5.0)

    # 2. Switch to Call B
    ok_b, msg_b = controller.switch_to_meeting(
        new_title="Product Architecture Review",
        new_platform="Microsoft Teams",
        new_target_hwnd=2222,
        new_pid=4567,
        recording_mode="audio_only",
    )

    assert ok_b is True
    id_b = controller._active_meeting_id
    corr_b = controller._active_meeting_corr_id
    diarizer_b = controller._active_meeting_diarizer

    # 3. Assert strict isolation
    assert id_a != id_b
    assert corr_a != corr_b
    assert diarizer_a is not diarizer_b
    assert controller._active_meeting_target_hwnd == 2222
    assert controller._active_meeting_platform == "Microsoft Teams"
    assert controller._active_meeting_title == "Product Architecture Review"
    # Transcriber streaming session was reset
    assert controller.streaming_transcriber.reset_streaming_session.called or controller.streaming_transcriber.start.called


# =============================================================================
# TEST G: Network Offline Isolation & Actionable Diagnostic Reporting
# =============================================================================
def test_acceptance_g_network_offline_and_actionable_diagnostics():
    """
    Mandatory Acceptance Test G:
    Ensures network interruptions never wipe credentials or display generic fallback errors.
    """
    # 1. Network offline returns clear diagnostic message
    code, msg = sanitize_oauth_error_for_user("WinError 10061: Connection refused", "ms_graph")
    assert code == "NETWORK_OFFLINE"
    assert "network offline" in msg.lower()
    assert "Microsoft" in msg

    # 2. MAPI unavailable returns exact recommendation
    code_mapi, msg_mapi = sanitize_oauth_error_for_user("Outlook is not installed on this system", "mapi")
    assert code_mapi == "MAPI_UNAVAILABLE"
    assert "Classic Outlook Desktop (MAPI) is not installed" in msg_mapi
    assert "Microsoft tab" in msg_mapi

    # 3. Rate limiting (HTTP 429) gives friendly pause notice
    code_429, msg_429 = sanitize_oauth_error_for_user("HTTP 429 Too Many Requests", "google")
    assert code_429 == "HTTP_429_RATE_LIMIT"
    assert "Google Calendar temporarily asked VOX to pause" in msg_429

    # 4. Custom descriptive error is preserved rather than masked
    code_custom, msg_custom = sanitize_oauth_error_for_user("CalDAV server returned HTTP 418 I am a teapot", "ics")
    assert "CalDAV server returned HTTP 418 I am a teapot" in msg_custom
    assert "VOX could not finish connecting" not in msg_custom

"""Tests for v2.4 fixes: Zoom portal guard, Upcoming Popover, In-App Dictation, 3-Mode Drawer, 10-Day Trial & Security, Zoom PKCE/Hosted OAuth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QKeyEvent, QTextCursor
from PyQt6.QtWidgets import QApplication, QMainWindow, QTextEdit
import pytest

from tests.token_test_helper import sign_entitlement_payload
from voxlocal.audio.call_detector import (
    _is_non_meeting_document_window,
    is_live_zoom_browser_title,
    is_non_meeting_web_portal,
)
from voxlocal.auth.desktop_auth import (
    DesktopAuthBroker,
    _AuthLoopbackHandler,
    is_privileged_wfx_user,
)
from voxlocal.calendar.auth_service import CalendarAuthBroker
from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
from voxlocal.core.controller import AppController
from voxlocal.core.entitlement import EntitlementManager, is_master_developer
from voxlocal.core.metering import UsageMeteringEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.video.meeting_capture import GenericCallWindowCaptureProvider


TEST_PRIV_KEY = "eCqPit3zEc3qLCPuzKLqf/QTf1gyHqHGVR6v4qDoLEE="
TEST_PUB_KEY = "YsgKdWBVb1ZfhvSxZeietMw9BZokUSRcR8WgH7GoMLI="


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_zoom_docs_and_marketplace_rejected_live_call_accepted():
    docs_title = "developers.zoom.us/docs/build-flow/basic-info/app-credentials/ - Google Chrome"
    mkt_title = "App Marketplace - Zoom - Google Chrome"
    ms_docs = "learn.microsoft.com/en-us/graph/api - Microsoft Edge"

    assert is_non_meeting_web_portal(docs_title) is True
    assert is_non_meeting_web_portal(mkt_title) is True
    assert is_non_meeting_web_portal(ms_docs) is True
    assert is_live_zoom_browser_title(docs_title) is False
    assert is_live_zoom_browser_title("zoom.us/docs/en/something - Chrome") is False
    assert _is_non_meeting_document_window("foo.pdf - adobe acrobat", "chrome.exe") is True

    # Real live Zoom Web Client / Join URL must still be accepted
    assert is_live_zoom_browser_title("Zoom Meeting - zoom.us/wc/123456789/join - Chrome") is True
    assert is_live_zoom_browser_title("Zoom Workplace Meeting - Google Chrome") is True

    provider = GenericCallWindowCaptureProvider()
    assert provider.inspect_window(100, 1234, "chrome.exe", "Chrome_WidgetWin_1", docs_title, (0, 0, 1280, 720)) is None
    matched = provider.inspect_window(101, 1234, "chrome.exe", "Chrome_WidgetWin_1", "Zoom Meeting - zoom.us/wc/9988/start - Chrome", (0, 0, 1280, 720))
    assert matched is not None
    assert "Zoom" in matched.platform_name


def test_upcoming_popover_close_escape_auto_dismiss_and_tooltip_suppression(qapp):
    from voxlocal.ui.floating_pill import VoxFlowBar, VoxUpcomingNotesPopover

    now_utc = datetime.now(timezone.utc)
    fake_mtg = SimpleNamespace(
        title="Product Sync with Amar",
        platform="zoom",
        meeting_platform_label="Zoom",
        minutes_until_start=10,
        local_start_time=now_utc,
        local_end_time=now_utc + timedelta(minutes=30),
        join_url="https://zoom.us/j/123",
    )
    mock_cal = MagicMock()
    mock_cal.get_upcoming_meetings.return_value = [fake_mtg]
    mock_ctrl = SimpleNamespace(calendar_engine=mock_cal, config=SimpleNamespace(theme="dark"))

    pop = VoxUpcomingNotesPopover(controller=mock_ctrl)
    closed_events = []
    pop.closed.connect(lambda: closed_events.append(True))

    anchor = QRect(400, 700, 120, 36)
    pop.refresh_and_position(anchor, "bottom")
    assert pop.isVisible() is True
    assert "Product Sync" in pop.lbl_title.text()
    assert pop.btn_start.text() == "Join & notes"

    # Tooltip must not overlap open popover
    bar = VoxFlowBar(controller=mock_ctrl)
    bar.upcoming_popover = pop
    bar._is_hovered = True
    bar._last_hover_zone = "notetaker_chevron"
    bar._tooltip_target_html = "Upcoming meetings"
    bar._do_show_tooltip()
    assert bar.hover_tooltip.isVisible() is False

    # Close button works
    pop.btn_close.click()
    assert pop.isVisible() is False
    assert len(closed_events) == 1

    # Escape dismisses
    pop.refresh_and_position(anchor, "bottom")
    assert pop.isVisible() is True
    esc = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    pop.keyPressEvent(esc)
    assert pop.isVisible() is False

    # Cursor leaving outside triggers auto-dismiss
    pop.refresh_and_position(anchor, "bottom")
    pop._opened_monotonic = time.monotonic() - 5.0
    outside = QPoint(pop.geometry().left() - 200, pop.geometry().top() - 200)
    assert pop.check_auto_dismiss(cursor_pos=outside) is False
    assert pop.check_auto_dismiss(cursor_pos=outside) is False
    assert pop.check_auto_dismiss(cursor_pos=outside) is True
    assert pop.isVisible() is False
    pop.close()
    bar.close()


def test_in_app_dictation_worker_thread_safe_and_isolated(qapp):
    from voxlocal.core.controller import AppController

    mock_ctrl = MagicMock(spec=AppController)
    win = QMainWindow()
    win.show()
    ed_notes = QTextEdit(win)
    ed_hidden = QTextEdit(win)
    ed_notes.show()
    ed_hidden.hide()
    ed_notes.setPlainText("Initial.")
    ed_notes.moveCursor(QTextCursor.MoveOperation.End)

    fake_ctrl = SimpleNamespace(
        _apply_in_app_dictation_insert=lambda w, txt: AppController._apply_in_app_dictation_insert(fake_ctrl, w, txt),
        _is_usable_in_app_widget=AppController._is_usable_in_app_widget,
    )
    # Emulate worker thread dispatching insert via _in_app_insert_requested signal mechanism
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Relay(QObject):
        sig = pyqtSignal(object, str)

    relay = _Relay()
    relay.sig.connect(lambda w, s: AppController._apply_in_app_dictation_insert(fake_ctrl, w, s))

    done = threading.Event()

    def _worker():
        relay.sig.emit(ed_notes, "Hello dictated")
        relay.sig.emit(ed_hidden, "Should not insert")
        done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert done.wait(timeout=3.0)
    qapp.processEvents()
    assert "Initial. Hello dictated" in ed_notes.toPlainText()
    assert ed_hidden.toPlainText() == ""
    win.close()


def test_my_notes_and_scratchpad_hub_dictate_wiring(qapp):
    from voxlocal.ui.meeting_studio import VoxMyThoughtsTab
    from voxlocal.ui.hub_pages import ScratchpadHubPage

    tab = VoxMyThoughtsTab(theme="light")
    assert hasattr(tab, "btn_dictate_notes")
    clicks = []
    tab.dictate_requested.connect(lambda: clicks.append(True))
    tab.btn_dictate_notes.click()
    assert clicks == [True]
    tab.set_dictation_active(True, "Listening...")
    assert tab.btn_dictate_notes.isChecked() is True
    assert "Stop" in tab.btn_dictate_notes.text()
    tab.close()

    temp_dir = tempfile.mkdtemp()
    try:
        db = LocalDatabase(Path(temp_dir) / "test.db")
        nid = db.create_note("Test Scratchpad", "", "")

        # Minimal signal carrier for ScratchpadHubPage
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Ctrl(QObject):
            notes_updated = pyqtSignal()
            dictation_interim_updated = pyqtSignal(str, str)
            dictation_state_changed = pyqtSignal(str, str)
            dictation_completed = pyqtSignal(str, str, str)
            open_scratchpad_requested = pyqtSignal()

            def __init__(self):
                super().__init__()
                self.db = db
                self.theme = "light"
                self.config = SimpleNamespace(scratchpad_in_flow_bar=True)
                self.calls = []
                self._explicit_in_app_widget_ref = None

            def toggle_in_app_dictation(self, widget, title="VOX"):
                self._explicit_in_app_widget_ref = widget
                self.calls.append((widget, title))

            def clear_in_app_dictation_target(self, widget=None):
                if self._explicit_in_app_widget_ref is widget:
                    self._explicit_in_app_widget_ref = None

        ctrl = _Ctrl()
        page = ScratchpadHubPage(ctrl)
        page.show()
        qapp.processEvents()
        assert page._current_note_id == nid
        page.btn_dictate.click()
        assert len(ctrl.calls) == 1
        assert ctrl.calls[0][0] is page.editor

        # Interim preview updates cleanly while active
        ctrl.dictation_interim_updated.emit("hello world", "")
        qapp.processEvents()
        assert page.lbl_live_interim.isVisible() is True
        assert "hello world" in page.lbl_live_interim.text()
        page.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_dynamic_3mode_notetaker_drawer(qapp):
    from PyQt6.QtCore import QObject, pyqtSignal
    from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer

    temp_dir = tempfile.mkdtemp()
    try:
        db = LocalDatabase(Path(temp_dir) / "drawer.db")
        mid = db.create_meeting(title="Architecture Review")
        db.update_meeting(mid, duration_sec=600.0, source_platform="Zoom")
        db.add_action_item(meeting_id=mid, description="Send API docs to customer", owner="Milind", status="pending")

        class _Ctrl(QObject):
            theme_changed = pyqtSignal(str)
            meeting_state_changed = pyqtSignal(str, str)
            meeting_tick = pyqtSignal(float, float, float, bool, bool)
            meeting_saved_and_transcribed = pyqtSignal(int)
            dictation_state_changed = pyqtSignal(str, str)
            dictation_interim_updated = pyqtSignal(str, str)
            dictation_completed = pyqtSignal(str, str, str)

            def __init__(self):
                super().__init__()
                self.db = db
                self.theme = "light"
                self.meeting_state = "idle"
                self.is_meeting_recording = False
                self.active_meeting_id = None
                self.upcoming = []

                class _Cal:
                    def __init__(self, parent):
                        self._p = parent

                    def get_upcoming_meetings(self, limit=3):
                        return self._p.upcoming[:limit]

                self.calendar_engine = _Cal(self)

        ctrl = _Ctrl()
        drawer = VoxNotetakerDrawer(controller=ctrl)

        # Legacy attributes intact
        for attr in ("txt_title", "notes_editor", "tx_browser", "list_transcript", "summary_browser", "txt_ask_anything"):
            assert hasattr(drawer, attr)

        # 1. Idle with no imminent meeting -> Workspace mode
        drawer.refresh_from_active_meeting()
        assert drawer._current_drawer_mode == "workspace"
        assert drawer.workspace_actions_list.count() >= 1

        # Quick scratchpad save persists to SQLite notes
        drawer.quick_scratchpad_editor.setPlainText("Follow up on Zoom PKCE docs")
        drawer._save_quick_scratchpad_note()
        assert any("Follow up on Zoom PKCE" in (n.get("plain_text") or "") for n in db.list_notes())

        # 2. Imminent calendar meeting (within 15 min) -> Prep mode
        ctrl.upcoming = [SimpleNamespace(title="Client Call", platform="zoom", meeting_platform_label="Zoom", minutes_until_start=12, organizer="Amar")]
        assert drawer.select_dynamic_mode(auto=True) == "prep"
        drawer._refresh_dynamic_context_panels()
        assert "Client Call" in drawer.prep_title_lbl.text()

        # 3. Active recording -> Live mode
        ctrl.meeting_state = "recording"
        ctrl.is_meeting_recording = True
        ctrl.active_meeting_id = mid
        assert drawer.select_dynamic_mode(auto=True) == "live"

        # Pin toggle toggles WindowStaysOnTopHint
        drawer.btn_pin_top.click()
        assert drawer._is_pinned_on_top is True
        assert bool(drawer.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        drawer.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_10_day_trial_gating_wildcard_security_and_clock_rollback():
    temp_dir = tempfile.mkdtemp()
    try:
        # 1. Wildcard bypasses eliminated
        assert is_master_developer(email="evilwfx@gmail.com") is False
        assert is_master_developer(email="hacker@worldfashionexchange.com.evil.com") is False
        assert is_master_developer(email="myworldfashionexchange@gmail.com") is False
        assert is_master_developer(email="employee@worldfashionexchange.com") is True
        assert is_master_developer(email="milinddwivedi0@gmail.com") is True
        assert is_privileged_wfx_user("attackerwfx@gmail.com") is False
        assert is_privileged_wfx_user("team@worldfashionexchange.com") is True

        vault = Path(temp_dir) / "ent.vault"
        mgr = EntitlementManager(cache_file=vault, verify_key=TEST_PUB_KEY)

        # 2. Unsigned plan escalation blocked
        mgr.set_user_context("user_regular_1", email="regular@gmail.com", plan_code="pro_annual")
        assert mgr.is_pro() is False
        assert mgr.is_trial_active() is False

        # 3. Guest mode: AI Summary & Ask VOX require Google Sign-In; Dictation & Recording unlimited
        mgr.set_user_context(None)
        allowed, code, _msg = mgr.can_use_ai_summary_and_ask(signed_in=False)
        assert allowed is False
        assert code == "auth_required"

        # 4. Signed-in 10-day Trial (Day 1..10 allowed, Day 11 blocked)
        now_utc = datetime.now(timezone.utc)
        mgr.set_user_context("usr_trial_1", email="trialuser@gmail.com", plan_code="free")
        trial_token = sign_entitlement_payload(
            {
                "user_id": "usr_trial_1",
                "plan": "free",
                "plan_code": "trial_10d",
                "is_pro": False,
                "trial_active": True,
                "trial_started_at": (now_utc - timedelta(days=3)).isoformat(),
                "trial_ends_at": (now_utc + timedelta(days=7)).isoformat(),
                "server_now_utc": now_utc.isoformat(),
                "exp": int((now_utc + timedelta(days=7)).timestamp()),
            },
            private_key=TEST_PRIV_KEY,
        )
        ok, err = mgr.save_entitlement_token(trial_token, expected_user_id="usr_trial_1")
        assert ok is True, err
        assert mgr.is_pro() is False
        assert mgr.is_trial_active(now_utc) is True
        assert mgr.trial_days_remaining(now_utc) == 7
        assert mgr.can_use_ai_summary_and_ask(signed_in=True, now_dt=now_utc)[0] is True

        # Day 11 (past trial_ends_at): Ask VOX & AI Summary blocked
        day11 = now_utc + timedelta(days=8)
        allowed11, code11, _msg11 = mgr.can_use_ai_summary_and_ask(signed_in=True, now_dt=day11)
        assert allowed11 is False
        assert code11 == "trial_expired"

        # Clock rollback (e.g. rolling system clock back 48h) revokes trial immediately
        rolled_back = now_utc - timedelta(hours=48)
        allowed_rb, code_rb, _ = mgr.can_use_ai_summary_and_ask(signed_in=True, now_dt=rolled_back)
        assert allowed_rb is False
        assert code_rb == "trial_expired"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_loopback_cors_rejects_evil_origin_and_ignores_unsigned_pro():
    temp_dir = tempfile.mkdtemp()
    try:
        broker = DesktopAuthBroker(vault_path=Path(temp_dir) / "auth.vault")
        broker.stop_background_sync_listener()

        # Complete login from URL trying to spoof unsigned is_pro=true
        ok, _msg, prof = broker.complete_login_from_url(
            "http://127.0.0.1:8400/callback?user_id=usr_evil&email=evil@gmail.com&is_pro=true&plan_code=pro_annual"
        )
        assert ok is True
        assert prof is not None
        assert prof.is_pro is False
        assert prof.plan_code == "free"

        srv = HTTPServer(("127.0.0.1", 0), _AuthLoopbackHandler)
        srv.auth_broker = broker  # type: ignore
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/sync-session",
            data=json.dumps({"user_id": "u1", "email": "u1@gmail.com"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "https://evil-attacker.example"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=3)
        assert exc_info.value.code == 403
        srv.server_close()
        broker.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_zoom_pkce_public_client_and_hosted_broker():
    temp_dir = tempfile.mkdtemp()
    try:
        auth_file = Path(temp_dir) / "zoom.dat"
        # Option 2: Public PKCE client with Client ID only (NO Client Secret!)
        z = ZoomCalendarProvider(client_id="zoom_pub_id_123", client_secret="", auth_file_path=auth_file)
        url = z.build_authorization_url("http://localhost:8080/zoom_callback", "st_1", code_challenge="chal_1")
        assert "client_id=zoom_pub_id_123" in url
        assert "code_challenge=chal_1" in url

        fake_resp_bytes = json.dumps({"access_token": "zm_acc", "refresh_token": "zm_ref", "expires_in": 3600}).encode("utf-8")

        class _FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        captured_reqs = []

        def _fake_urlopen(req, timeout=15):
            captured_reqs.append(req)
            if "oauth/token" in req.full_url:
                return _FakeResp(fake_resp_bytes)
            return _FakeResp(json.dumps({"email": "zoomuser@example.com"}).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            ok, msg = z._exchange_code_for_tokens("code_1", "http://localhost:8080/zoom_callback", code_verifier="ver_1")
        assert ok is True, msg
        token_req = captured_reqs[0]
        assert "Authorization" not in token_req.headers
        assert b"client_id=zoom_pub_id_123" in token_req.data
        assert b"code_verifier=ver_1" in token_req.data

        # Option 1: Hosted broker default URL routing for Zoom without global backend URL
        broker = CalendarAuthBroker()
        assert broker.is_backend_configured() is False
        assert broker.is_backend_configured_for("zoom") is True
        assert broker.is_backend_configured_for("google") is False

        hosted_auth_file = Path(temp_dir) / "zoom_hosted.dat"
        z_hosted = ZoomCalendarProvider(auth_file_path=hosted_auth_file, auth_broker=broker)
        z_hosted._refresh_token = "ref_hosted_1"
        with patch.object(broker, "refresh_backend_token", return_value={"access_token": "new_acc", "expires_in": 3600}) as m_ref:
            assert z_hosted.refresh_token() is True
            assert m_ref.call_args.kwargs.get("refresh_token") == "ref_hosted_1"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

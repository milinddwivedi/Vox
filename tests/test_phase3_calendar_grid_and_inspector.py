"""
Phase 3 Verification Suite: Calendar Grid, Health Strip & Contextual Inspector.

Covers:
1. Top clickable account health pills for all providers (Outlook, Google, Zoom, ICS)
   with 1-click reconnect routing and preserving Outlook Copy -> Paste tab 0.
2. True 5-day / 7-day Week Grid + < Prev | Today | Next > date navigation + Day Flow Week fix.
3. Contextual Meeting Inspector with offline AI Pre-Meeting Carry-Over Brief from SQLite,
   clipboard copy, Prepare Notetaker, Join Meeting, and View Recording links.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from voxlocal.calendar.base import CalendarMeeting
from voxlocal.calendar.google_provider import GoogleCalendarProvider
from voxlocal.calendar.graph_provider import MsGraphCalendarProvider
from voxlocal.calendar.ics_provider import IcsCalendarProvider
from voxlocal.calendar.zoom_provider import ZoomCalendarProvider
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.calendar_connect_dialog import VoxCalendarConnectDialog
from voxlocal.ui.calendar_page import CalendarPage
from voxlocal.win32.startup import StartupManager


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "--platform", "offscreen"])
    return app


_RETIRED_CONTROLLERS: list = []


class _NoOpThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _shutdown_controller(ctrl: AppController, *widgets) -> None:
    app = _get_qapp()
    for w in widgets:
        if w is not None:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    eng = getattr(ctrl, "calendar_engine", None)
    if eng is not None and hasattr(eng, "stop_polling"):
        try:
            eng.stop_polling()
        except Exception:
            pass
    ctrl.shutdown()
    _RETIRED_CONTROLLERS.append(ctrl)
    app.processEvents()


def _create_controller(td_name: str) -> AppController:
    cfg = AppConfig(storage_dir=td_name)
    cfg.launch_at_login = StartupManager.is_enabled()
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    real_thread = threading.Thread

    def _safe_thread(*args, **kwargs):
        if kwargs.get("name") == "StartupUpdateCheck":
            return _NoOpThread()
        return real_thread(*args, **kwargs)

    with patch("threading.Thread", side_effect=_safe_thread):
        return AppController(cfg)


class TestPhase3CalendarGridAndInspector(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_qapp()

    # ------------------------------------------------------------------
    # 1. All 4 Calendar Providers Health Strip + Reconnect Accessibility
    # ------------------------------------------------------------------
    def test_01_all_four_providers_health_pills_and_reconnect_accessibility(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p3_acc_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                accounts = [
                    {
                        "account_id": "ms_graph:alice@outlook.com",
                        "provider_type": "ms_graph",
                        "account_email": "alice@outlook.com",
                        "enabled": True,
                        "is_authenticated": True,
                        "is_healthy": True,
                        "connection_state": "connected",
                    },
                    {
                        "account_id": "google:alice@gmail.com",
                        "provider_type": "google",
                        "account_email": "alice@gmail.com",
                        "enabled": True,
                        "is_authenticated": False,
                        "is_healthy": False,
                        "connection_state": "expired",
                    },
                    {
                        "account_id": "zoom:alice@zoom.us",
                        "provider_type": "zoom",
                        "account_email": "alice@zoom.us",
                        "enabled": False,
                        "is_authenticated": True,
                        "is_healthy": False,
                        "connection_state": "disabled",
                    },
                    {
                        "account_id": "ics:company-feed",
                        "provider_type": "ics",
                        "display_name": "Team ICS",
                        "enabled": True,
                        "is_authenticated": True,
                        "is_healthy": True,
                        "connection_state": "connected",
                    },
                ]
                with patch.object(ctrl, "list_connected_calendar_accounts", return_value=accounts), \
                     patch.object(ctrl, "get_calendar_sync_health", return_value={"state": "partial"}):
                    page = CalendarPage(ctrl)
                    page.show()
                    self.app.processEvents()

                    pills = [
                        page.accounts_health_lay.itemAt(i).widget()
                        for i in range(page.accounts_health_lay.count())
                        if isinstance(page.accounts_health_lay.itemAt(i).widget(), QPushButton)
                    ]
                    self.assertEqual(len(pills), 4)
                    pill_texts = [p.text() for p in pills]
                    self.assertTrue(any("Outlook" in t and "✓" in t for t in pill_texts))
                    self.assertTrue(any("Google" in t and "Reconnect" in t for t in pill_texts))
                    self.assertTrue(any("Zoom" in t and "Paused" in t for t in pill_texts))
                    self.assertTrue(any("ICS" in t and "✓" in t for t in pill_texts))
                    self.assertEqual(page.btn_toggle_accounts_panel.text(), "Accounts (4)")

                    # Clicking expired Google pill routes to Google tab (index 1); Outlook reconnect preserves tab 0
                    with patch.object(page, "_open_connect_dialog") as mock_open_dlg:
                        google_pill = next(p for p in pills if "Google" in p.text())
                        google_pill.click()
                        mock_open_dlg.assert_called_once_with(1)

                        mock_open_dlg.reset_mock()
                        page._on_reconnect_account("ms_graph:alice@outlook.com", "ms_graph")
                        mock_open_dlg.assert_called_once_with(0)

                    # Clicking healthy Outlook pill opens Accounts Manager slide-over cleanly
                    self.assertEqual(page.right_panel_stack.currentIndex(), 0)
                    self.assertFalse(page.btn_toggle_accounts_panel.isChecked())
                    outlook_pill = next(p for p in pills if "Outlook" in p.text())
                    outlook_pill.click()
                    self.assertEqual(page.right_panel_stack.currentIndex(), 1)
                    self.assertTrue(page.btn_toggle_accounts_panel.isChecked())

                    page.btn_toggle_accounts_panel.click()
                    self.assertEqual(page.right_panel_stack.currentIndex(), 0)
                    self.assertFalse(page.btn_toggle_accounts_panel.isChecked())
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 2. True Multi-Day Week Grid & Date Navigation
    # ------------------------------------------------------------------
    def test_02_week_grid_and_date_navigation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p3_week_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                now_local = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
                monday = now_local - timedelta(days=now_local.weekday())
                wednesday = monday + timedelta(days=2)
                friday = monday + timedelta(days=4)
                next_tue = monday + timedelta(days=8)

                wed_rec_id = ctrl.db.create_meeting(title="Wed Recorded Sync")

                meetings = [
                    CalendarMeeting(id="m-mon", title="Monday Kickoff", start_time=monday, end_time=monday + timedelta(hours=1), platform="teams"),
                    CalendarMeeting(id="m-wed", title="Wednesday Arch", start_time=wednesday, end_time=wednesday + timedelta(hours=1), platform="zoom"),
                    CalendarMeeting(id="m-fri", title="Friday Demo", start_time=friday, end_time=friday + timedelta(hours=1), platform="meet"),
                    CalendarMeeting(id="m-next", title="Next Week Planning", start_time=next_tue, end_time=next_tue + timedelta(hours=1), platform="teams"),
                ]

                fake_acc = [{"account_id": "ms:1", "provider_type": "ms_graph", "enabled": True, "is_authenticated": True, "is_healthy": True, "connection_state": "connected"}]
                with patch.object(ctrl, "list_connected_calendar_accounts", return_value=fake_acc), \
                     patch.object(ctrl.calendar_engine, "get_upcoming_meetings", return_value=meetings), \
                     patch.object(ctrl.db, "list_meetings_by_calendar_event_ids", return_value={"m-wed": wed_rec_id}):
                    page = CalendarPage(ctrl)
                    page.show()
                    page.set_view_mode("week")
                    self.app.processEvents()

                    self.assertTrue(page.btn_view_week.isChecked())
                    self.assertTrue(page.date_nav_bar.isVisible())
                    self.assertTrue(page.btn_week_weekend.isVisible())
                    self.assertEqual(len(page._week_day_columns), 5)

                    # Verify events landed in exact weekday columns & Wednesday shows recorded badge
                    mon_col = page._week_day_columns[0][1]
                    wed_col = page._week_day_columns[2][1]
                    fri_col = page._week_day_columns[4][1]
                    self.assertTrue(any("Monday Kickoff" in b.text() for b in mon_col.findChildren(QPushButton)))
                    self.assertTrue(any("Wednesday Arch" in b.text() and "✓ Recorded" in b.text() for b in wed_col.findChildren(QPushButton)))
                    self.assertTrue(any("Friday Demo" in b.text() for b in fri_col.findChildren(QPushButton)))

                    # Navigate forward 1 week -> next week's Tuesday event renders in col 1 (Tuesday)
                    orig_anchor = page._anchor_date
                    page.btn_nav_next.click()
                    self.app.processEvents()
                    self.assertEqual(page._anchor_date, orig_anchor + timedelta(days=7))
                    next_tue_col = page._week_day_columns[1][1]
                    self.assertTrue(any("Next Week Planning" in b.text() for b in next_tue_col.findChildren(QPushButton)))

                    # Reset to Today
                    page.btn_nav_today.click()
                    self.app.processEvents()
                    self.assertEqual(page._anchor_date, datetime.now().astimezone().date())

                    # Toggle 7-day weekend view
                    page.btn_week_weekend.click()
                    self.app.processEvents()
                    self.assertEqual(len(page._week_day_columns), 7)

                    # Selecting This Week + Day Flow delegates to true multi-day Week Grid (not Today fallback)
                    page.set_view_mode("timeline")
                    page.set_time_filter("week")
                    self.app.processEvents()
                    self.assertGreaterEqual(len(page._week_day_columns), 5)

                    # Switching back to Today + Day Flow supports +-1 day stepping
                    page.set_time_filter("today")
                    self.app.processEvents()
                    day_anchor = page._anchor_date
                    page.btn_nav_next.click()
                    self.assertEqual(page._anchor_date, day_anchor + timedelta(days=1))
                    page.btn_nav_prev.click()
                    self.assertEqual(page._anchor_date, day_anchor)
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 3. Contextual Meeting Inspector + Offline AI Carry-Over Brief
    # ------------------------------------------------------------------
    def test_03_contextual_inspector_and_local_ai_carryover_brief(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p3_insp_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                past_mid = ctrl.db.create_meeting(title="Weekly Product Architecture")
                ctrl.db.update_meeting(
                    past_mid,
                    status="completed",
                    summary="Agreed on modular local inference and zero-cloud audio retention.",
                )
                ctrl.db.add_decision(
                    meeting_id=past_mid,
                    decision="Adopt ONNX runtime on Windows",
                    status="confirmed",
                    timestamp_sec=125.0,
                )
                ctrl.db.add_action_item(
                    meeting_id=past_mid,
                    description="Benchmark Whisper latency",
                    owner="Milind",
                    due_date="Friday",
                )

                start_dt = datetime.now().astimezone() + timedelta(hours=2)
                upcoming_m = CalendarMeeting(
                    id="evt-arch-next",
                    title="Weekly Product Architecture",
                    start_time=start_dt,
                    end_time=start_dt + timedelta(minutes=45),
                    join_url="https://teams.microsoft.com/l/meetup-join/test-arch",
                    platform="teams",
                    organizer="Alice",
                    attendees=["alice@example.com", "bob@example.com"],
                    account_id="ms:alice@outlook.com",
                )

                page = CalendarPage(ctrl)
                page.show()
                prep_spy = []
                rec_spy = []
                page.prepare_notetaker_requested.connect(lambda m: prep_spy.append(m.id))
                page.open_meeting_recording_requested.connect(lambda rid: rec_spy.append(rid))

                page.inspect_meeting(upcoming_m, recording_id=past_mid)
                self.app.processEvents()

                self.assertEqual(page.right_panel_stack.currentIndex(), 0)
                self.assertEqual(page.lbl_insp_title.text(), "Weekly Product Architecture")
                self.assertIn("Microsoft Teams", page.lbl_insp_platform.text())
                self.assertIn("alice@example.com", page.lbl_insp_attendees.text())
                self.assertTrue(page.btn_insp_join.isVisible())
                self.assertTrue(page.btn_insp_prepare.isEnabled())
                self.assertTrue(page.btn_insp_recording.isVisible())

                brief_txt = page.txt_insp_brief.text()
                self.assertIn("Adopt ONNX runtime on Windows", brief_txt)
                self.assertIn("Benchmark Whisper latency", brief_txt)

                page.btn_insp_copy_brief.click()
                self.assertIn("Adopt ONNX runtime on Windows", QApplication.clipboard().text())

                page.btn_insp_prepare.click()
                self.assertEqual(prep_spy, ["evt-arch-next"])

                page.btn_insp_recording.click()
                self.assertEqual(rec_spy, [past_mid])

                # Dark theme compatibility
                page.apply_theme("dark")
                self.app.processEvents()
                self.assertEqual(page._theme, "dark")
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 4. Real 4-Provider Integration: ICS + Google + Outlook + Zoom
    # ------------------------------------------------------------------
    def test_04_real_provider_integration_ics_google_outlook_zoom(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p3_providers_") as td:
            ctrl = _create_controller(td)
            page = None
            dlg = None
            try:
                now_utc = datetime.now(timezone.utc)
                ev_start = (now_utc + timedelta(hours=2)).replace(second=0, microsecond=0)
                ev_end = ev_start + timedelta(minutes=45)

                ics_path = Path(td) / "team_feed.ics"
                ics_path.write_text(
                    "\n".join(
                        [
                            "BEGIN:VCALENDAR",
                            "VERSION:2.0",
                            "PRODID:-//VOX Test//Calendar//EN",
                            "X-WR-CALNAME:Team ICS",
                            "BEGIN:VEVENT",
                            "UID:ics-arch-sync-1",
                            f"DTSTART:{ev_start.strftime('%Y%m%dT%H%M%SZ')}",
                            f"DTEND:{ev_end.strftime('%Y%m%dT%H%M%SZ')}",
                            "SUMMARY:ICS Architecture Sync",
                            "DESCRIPTION:Join via https://meet.google.com/abc-defg-hij",
                            "ORGANIZER;CN=Team Lead:mailto:lead@example.com",
                            "END:VEVENT",
                            "END:VCALENDAR",
                        ]
                    ),
                    encoding="utf-8",
                )

                ics_prov = IcsCalendarProvider(
                    feed_url=str(ics_path),
                    auth_file_path=Path(td) / "ics.dat",
                    account_id="ics:local-team",
                    user_id=ctrl.config.current_user_id or "default",
                )
                self.assertTrue(ics_prov.authenticate(interactive=False))

                ms_prov = MsGraphCalendarProvider(
                    auth_file_path=Path(td) / "ms.dat",
                    account_hint="alice@outlook.com",
                    account_id="ms_graph:alice@outlook.com",
                    user_id=ctrl.config.current_user_id or "default",
                )
                ms_prov._access_token = "tok-ms"
                ms_prov._expires_at = time.time() + 3600
                ms_prov._account_upn = "alice@outlook.com"
                ms_prov._status["connection_state"] = "connected"
                ms_prov._status["is_healthy"] = True
                ms_prov._sync_status_identity()

                g_prov = GoogleCalendarProvider(
                    client_id="cid-g",
                    auth_file_path=Path(td) / "google.dat",
                    account_id="google:alice@gmail.com",
                    user_id=ctrl.config.current_user_id or "default",
                )
                g_prov._access_token = "tok-g"
                g_prov._expires_at = time.time() + 3600
                g_prov._account_email = "alice@gmail.com"
                g_prov._status["connection_state"] = "connected"
                g_prov._status["is_healthy"] = True
                g_prov._sync_status_identity()

                z_prov = ZoomCalendarProvider(
                    client_id="cid-z",
                    auth_file_path=Path(td) / "zoom.dat",
                    account_id="zoom:alice@zoom.us",
                    user_id=ctrl.config.current_user_id or "default",
                )
                z_prov._access_token = "tok-z"
                z_prov._expires_at = time.time() + 3600
                z_prov._account_email = "alice@zoom.us"
                z_prov._status["connection_state"] = "connected"
                z_prov._status["is_healthy"] = True
                z_prov._sync_status_identity()

                ms_mtg = CalendarMeeting(
                    id="ms-evt-1",
                    title="Outlook Product Sync",
                    start_time=ev_start + timedelta(hours=1),
                    end_time=ev_start + timedelta(hours=2),
                    join_url="https://teams.microsoft.com/l/meetup-join/outlook-sync",
                    platform="teams",
                    provider_type="ms_graph",
                    account_id="ms_graph:alice@outlook.com",
                )
                g_mtg = CalendarMeeting(
                    id="g-evt-1",
                    title="Google Design Review",
                    start_time=ev_start + timedelta(hours=2),
                    end_time=ev_start + timedelta(hours=3),
                    join_url="https://meet.google.com/xyz-uvwx-rst",
                    platform="meet",
                    provider_type="google",
                    account_id="google:alice@gmail.com",
                )
                z_mtg = CalendarMeeting(
                    id="z-evt-1",
                    title="Zoom Customer Demo",
                    start_time=ev_start + timedelta(hours=3),
                    end_time=ev_start + timedelta(hours=4),
                    join_url="https://zoom.us/j/9988776655",
                    platform="zoom",
                    provider_type="zoom",
                    account_id="zoom:alice@zoom.us",
                )

                with patch.object(ms_prov, "get_upcoming_meetings", return_value=[ms_mtg]), \
                     patch.object(g_prov, "get_upcoming_meetings", return_value=[g_mtg]), \
                     patch.object(z_prov, "get_upcoming_meetings", return_value=[z_mtg]):
                    ctrl.calendar_engine.register_provider("ms_graph:alice@outlook.com", ms_prov, sync_immediately=False)
                    ctrl.calendar_engine.register_provider("google:alice@gmail.com", g_prov, sync_immediately=False)
                    ctrl.calendar_engine.register_provider("zoom:alice@zoom.us", z_prov, sync_immediately=False)
                    ctrl.calendar_engine.register_provider("ics:local-team", ics_prov, sync_immediately=False)

                    merged = ctrl.calendar_engine.poll_all_sync(force=True, use_incremental=False)
                    titles = [m.title for m in merged]
                    self.assertIn("ICS Architecture Sync", titles)
                    self.assertIn("Outlook Product Sync", titles)
                    self.assertIn("Google Design Review", titles)
                    self.assertIn("Zoom Customer Demo", titles)

                    page = CalendarPage(ctrl)
                    page.show()
                    page.set_view_mode("week")
                    self.app.processEvents()

                    pills = [
                        page.accounts_health_lay.itemAt(i).widget()
                        for i in range(page.accounts_health_lay.count())
                        if isinstance(page.accounts_health_lay.itemAt(i).widget(), QPushButton)
                    ]
                    pill_texts = [p.text() for p in pills]
                    self.assertEqual(len(pills), 4)
                    self.assertTrue(any("Outlook" in t for t in pill_texts))
                    self.assertTrue(any("Google" in t for t in pill_texts))
                    self.assertTrue(any("Zoom" in t for t in pill_texts))
                    self.assertTrue(any("ICS" in t for t in pill_texts))

                    week_buttons = [
                        b.text()
                        for _d, col_frame in page._week_day_columns
                        for b in col_frame.findChildren(QPushButton)
                    ]
                    joined_week = "\n".join(week_buttons)
                    self.assertIn("ICS Architecture Sync", joined_week)
                    self.assertIn("Outlook Product Sync", joined_week)
                    self.assertIn("Google Design Review", joined_week)
                    self.assertIn("Zoom Customer Demo", joined_week)

                    ics_mtg = next(m for m in merged if m.title == "ICS Architecture Sync")
                    page.inspect_meeting(ics_mtg)
                    self.app.processEvents()
                    self.assertEqual(page.lbl_insp_title.text(), "ICS Architecture Sync")
                    self.assertIn("Google Meet", page.lbl_insp_platform.text())
                    self.assertTrue(page.btn_insp_join.isVisible())

                    # Verify VoxCalendarConnectDialog tabs preserve Outlook Copy -> Paste tab 0 + Google/Zoom/ICS tabs
                    dlg = VoxCalendarConnectDialog(ctrl, theme="light", parent=page)
                    dlg._select_tab(0)
                    self.assertEqual(dlg.stack.currentIndex(), 0)
                    self.assertTrue(hasattr(dlg, "btn_ms_start"))
                    self.assertTrue(hasattr(dlg, "_device_code_received"))
                    self.assertTrue(hasattr(dlg, "btn_copy_and_open"))
                    dlg._select_tab(1)
                    self.assertEqual(dlg.stack.currentIndex(), 1)
                    dlg._select_tab(2)
                    self.assertEqual(dlg.stack.currentIndex(), 2)
                    dlg._select_tab(3)
                    self.assertEqual(dlg.stack.currentIndex(), 3)
            finally:
                if dlg is not None:
                    try:
                        dlg.close()
                        dlg.deleteLater()
                    except Exception:
                        pass
                _shutdown_controller(ctrl, page)


if __name__ == "__main__":
    unittest.main()

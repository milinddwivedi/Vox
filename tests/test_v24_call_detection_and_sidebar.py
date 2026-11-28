"""Verification tests for v2.4 call-detection false-positive guards and Meetings sidebar redesign."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    is_live_google_meet_title,
    is_live_teams_browser_title,
    is_live_zoom_browser_title,
    is_non_call_hub,
    is_non_meeting_web_portal,
    is_pre_join_or_non_live_title,
)
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.notetaker_page import NotetakerPage, VoxFlatComboBox
from voxlocal.video.meeting_capture import GenericCallWindowCaptureProvider, GoogleMeetTabCaptureProvider


def _ensure_qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _DummySignal:
    def connect(self, *_args, **_kwargs) -> None:
        return None


def _make_notetaker(tmp_path) -> tuple[NotetakerPage, LocalDatabase, SimpleNamespace]:
    _ensure_qapp()
    db = LocalDatabase(str(tmp_path / "test_sidebar.db"))
    cfg = SimpleNamespace(
        notetaker_library_compact=False,
        notetaker_library_collapsed=False,
        notetaker_ask_rail_open=False,
        notetaker_split_mode="off",
        notetaker_auto_start_banner_dismissed=True,
        auto_record_calls=True,
        prompt_on_call=True,
    )
    noop = lambda *_a, **_k: None
    ctrl = SimpleNamespace(
        theme="light",
        config=cfg,
        db=db,
        meeting_state="idle",
        is_meeting_recording=False,
        active_meeting_id=None,
        calendar_engine=None,
        call_detector=None,
        meeting_saved_and_transcribed=_DummySignal(),
        meeting_state_changed=_DummySignal(),
        meeting_tick=_DummySignal(),
        calendar_upcoming_changed=_DummySignal(),
        dictation_interim_updated=_DummySignal(),
        dictation_state_changed=_DummySignal(),
        dictation_completed=_DummySignal(),
        knowledge_updated=_DummySignal(),
        get_upcoming_calendar_meetings=lambda **kwargs: [],
        start_meeting_recording=noop,
        stop_meeting_recording=noop,
        pause_meeting_recording=noop,
        resume_meeting_recording=noop,
        reprocess_meeting=noop,
        transcribe_audio_file=noop,
        query_meeting_memory=lambda *a, **k: {"answer": "", "citations": []},
        get_calendar_accounts=lambda: [],
        sync_calendar_events=noop,
    )
    page = NotetakerPage(ctrl)
    return page, db, ctrl


class TestFalseMeetingDetectionGuards:
    def test_zoom_developer_docs_and_portals_rejected(self) -> None:
        bad_titles = [
            "developers.zoom.us/docs/build-flow/basic-info/app-credentials/",
            "App Credentials | Zoom Developers - Google Chrome",
            "marketplace.zoom.us/develop/applications/qS6t7_pHSCCl1L04iYpIYg/information",
            "zoom.us/signin - Google Chrome",
            "https://zoom.us/j/9876543210 - Google Chrome",
            "Launch Meeting - Zoom",
            "Waiting Room - Zoom",
            "Zoom Workplace",
            "Zoom - Free Account",
            "Schedule Meeting - Zoom",
        ]
        for title in bad_titles:
            assert is_live_zoom_browser_title(title) is False, f"Falsely accepted Zoom title: {title}"
            assert is_non_call_hub("Zoom", title) is True, f"Zoom hub/portal not rejected: {title}"

        assert is_non_meeting_web_portal("developers.zoom.us/docs/build-flow/basic-info/app-credentials/") is True
        prov = GenericCallWindowCaptureProvider()
        desc = prov.inspect_window(
            hwnd=1234,
            pid=999,
            process_name="chrome.exe",
            class_name="Chrome_WidgetWin_1",
            title="developers.zoom.us/docs/build-flow/basic-info/app-credentials/",
            bounds=(0, 0, 1280, 800),
        )
        assert desc is None

    def test_google_meet_landing_and_pre_join_rejected(self) -> None:
        bad_meet = [
            "meet.google.com - Google Chrome",
            "Google Meet - Google Chrome",
            "Meet - Google Chrome",
            "Ready to join? - Meet - abc-defg-hij - Google Chrome",
            "Getting ready... - Meet - abc-defg-hij - Google Chrome",
            "Ask to join - Meet - abc-defg-hij - Microsoft Edge",
        ]
        for title in bad_meet:
            assert is_live_google_meet_title(title) is False, f"Falsely accepted Meet landing/pre-join: {title}"
            assert GoogleMeetTabCaptureProvider.is_google_meet_title(title) is False

        assert is_live_google_meet_title("Meet - abc-defg-hij - Google Chrome") is True
        assert GoogleMeetTabCaptureProvider.is_google_meet_title("Meet - abc-defg-hij - Google Chrome") is True
        assert is_pre_join_or_non_live_title("Ready to join? - Meet - abc-defg-hij") is True

    def test_teams_browser_chat_and_calendar_hubs_rejected(self) -> None:
        bad_teams = [
            "Microsoft Teams - Google Chrome",
            "Chat | Alice Bob | Microsoft Teams - Google Chrome",
            "Calendar | Microsoft Teams - Microsoft Edge",
            "Meet | Microsoft Teams - Google Chrome",
            "New meeting | Microsoft Teams",
            "Teams | Engineering | Microsoft Teams",
        ]
        for title in bad_teams:
            assert is_live_teams_browser_title(title) is False, f"Falsely accepted Teams hub: {title}"
            assert is_non_call_hub("Microsoft Teams", title) is True

        assert is_live_teams_browser_title("Weekly Product Sync | Microsoft Teams - Google Chrome") is True

    def test_browser_call_window_requires_active_browser_mic(self) -> None:
        detector = CallDetector()
        now = 5000.0
        meet_win = [(9101, "Google Meet", "Meet - xyz-abcd-efg - Google Chrome", (0, 0, 1280, 720))]
        with (
            patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=meet_win),
            patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(4242, "chrome.exe")),
        ):
            # Tab open silently before call audio/mic starts must NOT fire 'started'
            assert detector.update(users=[], now=now) is None
            assert detector.update(users=[], now=now + 2.0) is None
            assert detector.active_session is None
            assert detector.state == MeetingDetectorState.MEETING_SUSPECTED

            # Once browser mic is active, detection confirms after debounce
            assert detector.update(users=[r"c:\program files\google\chrome\application\chrome.exe"], now=now + 3.0) is None
            assert detector.update(users=[r"c:\program files\google\chrome\application\chrome.exe"], now=now + 4.2) == "started"
            assert detector.active_session is not None
            assert detector.active_session.mic_active is True

    def test_prompt_on_call_disables_silent_pill_auto_start(self, tmp_path) -> None:
        _ensure_qapp()
        dummy_ctrl = SimpleNamespace(
            config=SimpleNamespace(auto_record_calls=True, prompt_on_call=True),
            theme="dark",
            dock_edge="right",
            _capture_state="idle",
            is_meeting_recording=False,
        )
        pill = DualTierFloatingPill(controller=dummy_ctrl)
        dummy_ctrl.floating_pill = pill
        sess = MeetingSessionInfo(
            session_id="teams_sync_101",
            platform="Microsoft Teams",
            window_hwnd=101,
            window_title="Daily Sync | Microsoft Teams",
            clean_title="Daily Sync",
            bounds=(0, 0, 1280, 720),
            is_call_stage=True,
            mic_active=True,
        )
        try:
            AppController.show_meeting_prompt_popup(dummy_ctrl, sess)
            assert pill.pill_state == PillState.CARD_NOTIFICATION
            assert not pill._auto_start_timer.isActive()
            assert pill.auto_start_progress == 0.0
        finally:
            pill.close()
            pill.deleteLater()


class TestNotetakerSidebarRedesignAndFolders:
    def test_folder_create_rename_delete_preserves_meetings(self, tmp_path) -> None:
        page, db, _ctrl = _make_notetaker(tmp_path)
        try:
            fid = db.create_folder(name="One-on-One", folder_type="personal")
            m1 = db.create_meeting(title="1:1 with Sarah")
            m2 = db.create_meeting(title="Sprint Planning")
            db.assign_meeting_folder(m1, fid)
            db.update_meeting(m1, status="completed")
            db.update_meeting(m2, status="completed")

            page.refresh_all()
            idx = page.combo_folders.findData(fid)
            assert idx > 0
            assert page.combo_folders.itemText(idx) == "One-on-One"
            assert not page.btn_delete_active_folder.isVisible()

            # Selecting folder reveals contextual Rename & Delete controls
            page.combo_folders.setCurrentIndex(idx)
            assert page._active_folder_id == fid
            assert page.meetings_list.count() == 1
            assert page.btn_delete_active_folder.isHidden() is False
            assert page.btn_rename_active_folder.isHidden() is False

            # Rename via mocked dialog input
            with patch.object(page, "_themed_input_text", return_value=("Executive 1:1", True)):
                page._on_rename_folder_clicked()
            idx_renamed = page.combo_folders.findData(fid)
            assert idx_renamed > 0
            assert page.combo_folders.itemText(idx_renamed) == "Executive 1:1"

            # Delete folder removes folder cleanly while preserving ALL meetings
            assert page._on_delete_folder_clicked(skip_confirm=True) is True
            assert page.combo_folders.findData(fid) == -1
            assert page._active_folder_id is None
            assert page.btn_delete_active_folder.isHidden() is True
            assert page.meetings_list.count() == 2
            remaining = db.get_meeting(m1)
            assert remaining is not None
            assert remaining.get("folder_id") is None
        finally:
            page.close()
            page.deleteLater()

    def test_density_toggle_visibly_changes_delegate_height_and_rescued_chip(self, tmp_path) -> None:
        page, db, _ctrl = _make_notetaker(tmp_path)
        try:
            m1 = db.create_meeting(title="Architecture Review")
            db.update_meeting(m1, status="completed")
            page.refresh_all()

            assert page.meetings_list.count() == 1
            assert page.btn_density.isHidden() is True  # Moved cleanly to library '...' menu
            assert page._lib_filter_btns["recoverable"].isHidden() is True

            comfy_h = page.meetings_delegate.sizeHint(
                QStyleOptionViewItem(), page.meetings_list.model().index(0, 0)
            ).height()
            page._toggle_library_density(True)
            assert page._library_compact is True
            assert page.meetings_delegate.compact is True
            compact_h = page.meetings_delegate.sizeHint(
                QStyleOptionViewItem(), page.meetings_list.model().index(0, 0)
            ).height()
            assert compact_h < comfy_h
            assert comfy_h - compact_h == 24
            assert compact_h == 56  # 36px compact row + 20px TODAY group header

            page._toggle_library_density(False)
            assert page._library_compact is False
            assert page.meetings_delegate.compact is False
        finally:
            page.close()
            page.deleteLater()

    def test_flat_combobox_paints_in_light_and_dark_themes(self) -> None:
        _ensure_qapp()
        cb = VoxFlatComboBox(leading_icon_name="folder", theme="light")
        cb.addItem("All Meetings", None)
        cb.resize(180, 30)
        pm = QPixmap(180, 30)
        for theme in ("light", "dark"):
            cb.set_theme(theme)
            cb.render(pm)
            assert not pm.isNull()
        cb.deleteLater()

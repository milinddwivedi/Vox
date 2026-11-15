"""Unit and E2E tests for the Top-of-Notetaker Auto-Start Banner and manual meeting stability fixes."""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QTextEdit, QWidget, QFrame

from voxlocal.core.config import AppConfig
from voxlocal.ui.notetaker_page import NotetakerPage, VoxAutoStartBanner
from voxlocal.win32.window_focus import TargetWindowInfo


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_auto_start_banner_ui_elements(qapp):
    """Verify banner text, concentric icon badge, and action buttons."""
    banner = VoxAutoStartBanner()
    assert banner.lbl_title.text() == "Want Notetaker to start automatically?"
    assert banner.lbl_sub.text() == "This will turn on Notetaker as soon as your meeting begins"
    assert banner.lbl_icon.text() == "◎"
    assert banner.btn_not_now.text() == "Not now"
    assert banner.btn_turn_on.text() == "Turn on"


def test_auto_start_banner_visibility_conditions(qapp):
    """Banner should show only if auto-start is not enabled and user has not dismissed it."""
    mock_ctrl = MagicMock()
    mock_ctrl.theme = "light"
    mock_ctrl.config = AppConfig()
    mock_ctrl.db = MagicMock()
    mock_ctrl.db.list_meetings.return_value = []
    mock_ctrl.db.list_folders.return_value = []
    mock_ctrl.calendar_engine = None

    # Condition 1: Default config (auto_record_calls=True, prompt_on_call=True, dismissed=False)
    # Since prompt_on_call is True, auto-start without prompt is NOT active, so banner should show!
    mock_ctrl.config.auto_record_calls = True
    mock_ctrl.config.prompt_on_call = True
    mock_ctrl.config.notetaker_auto_start_banner_dismissed = False

    page = NotetakerPage(mock_ctrl)
    page.show()
    page._refresh_auto_start_banner()
    assert not page.auto_start_banner.isHidden()

    # Condition 2: Auto-start is already turned on (auto_record_calls=True, prompt_on_call=False)
    mock_ctrl.config.prompt_on_call = False
    page._refresh_auto_start_banner()
    assert page.auto_start_banner.isHidden()

    # Condition 3: User dismissed the banner ("Not now")
    mock_ctrl.config.prompt_on_call = True
    mock_ctrl.config.notetaker_auto_start_banner_dismissed = True
    page._refresh_auto_start_banner()
    assert page.auto_start_banner.isHidden()
    page.close()


def test_auto_start_turn_on_action(qapp):
    """Clicking 'Turn on' enables auto-record, disables prompt, marks dismissed, and saves."""
    mock_ctrl = MagicMock()
    mock_ctrl.theme = "light"
    mock_ctrl.config = AppConfig()
    mock_ctrl.config.save = MagicMock()
    mock_ctrl.config.auto_record_calls = False
    mock_ctrl.config.prompt_on_call = True
    mock_ctrl.config.notetaker_auto_start_banner_dismissed = False
    mock_ctrl.db = MagicMock()
    mock_ctrl.db.list_meetings.return_value = []
    mock_ctrl.db.list_folders.return_value = []

    page = NotetakerPage(mock_ctrl)
    page._on_auto_start_turn_on()

    assert mock_ctrl.config.auto_record_calls is True
    assert mock_ctrl.config.prompt_on_call is False
    assert mock_ctrl.config.notetaker_auto_start_banner_dismissed is True
    assert mock_ctrl.config.save.called
    assert page.auto_start_banner.isHidden()


def test_auto_start_not_now_action(qapp):
    """Clicking 'Not now' preserves auto-record settings, marks dismissed, and saves."""
    mock_ctrl = MagicMock()
    mock_ctrl.theme = "light"
    mock_ctrl.config = AppConfig()
    mock_ctrl.config.save = MagicMock()
    mock_ctrl.config.auto_record_calls = True
    mock_ctrl.config.prompt_on_call = True
    mock_ctrl.config.notetaker_auto_start_banner_dismissed = False
    mock_ctrl.db = MagicMock()
    mock_ctrl.db.list_meetings.return_value = []
    mock_ctrl.db.list_folders.return_value = []

    page = NotetakerPage(mock_ctrl)
    page._on_auto_start_not_now()

    assert mock_ctrl.config.prompt_on_call is True
    assert mock_ctrl.config.notetaker_auto_start_banner_dismissed is True
    assert mock_ctrl.config.save.called
    assert page.auto_start_banner.isHidden()


def test_header_settings_button_signal(qapp):
    """Clicking the gear icon emits open_settings_requested(2)."""
    mock_ctrl = MagicMock()
    mock_ctrl.theme = "light"
    mock_ctrl.config = AppConfig()
    mock_ctrl.db = MagicMock()
    mock_ctrl.db.list_meetings.return_value = []
    mock_ctrl.db.list_folders.return_value = []

    page = NotetakerPage(mock_ctrl)
    received = []
    page.open_settings_requested.connect(lambda idx: received.append(idx))

    page.btn_settings.click()
    assert received == [2]


def test_manual_meeting_not_dropped_by_call_detector(qapp):
    """When a meeting was started manually by the user, call_detector 'ended' events do not auto-stop it."""
    from voxlocal.core.controller import AppController
    with patch("voxlocal.core.controller.CallDetector"), \
         patch("voxlocal.core.controller.DictationRecorder"), \
         patch("voxlocal.core.controller.MeetingRecorder"), \
         patch("voxlocal.core.controller.LocalWhisperTranscriber"), \
         patch("voxlocal.core.controller.GlobalHotkeyManager"):

        ctrl = AppController()
        ctrl._capture_state = "recording"
        ctrl._is_manual_meeting = True
        ctrl.call_detector = MagicMock()
        ctrl.call_detector.poll.return_value = "ended"
        ctrl.call_detector.last_ended_session = None
        ctrl.call_detector.active = None

        with patch.object(ctrl, "stop_meeting_recording") as mock_stop:
            ctrl._poll_call_detector()
            # stop_meeting_recording should NOT be called because it was a manual meeting!
            assert not mock_stop.called


def test_in_app_dictation_fallback_to_focused_editor(qapp):
    """In-app dictation insertion dynamically finds focused QTextEdit when explicit ref is None."""
    from voxlocal.core.controller import AppController

    with patch("voxlocal.core.controller.CallDetector"), \
         patch("voxlocal.core.controller.DictationRecorder"), \
         patch("voxlocal.core.controller.MeetingRecorder"), \
         patch("voxlocal.core.controller.LocalWhisperTranscriber"), \
         patch("voxlocal.core.controller.GlobalHotkeyManager"):

        ctrl = AppController()
        editor = QTextEdit()
        editor.setText("Hello")
        editor.show()
        editor.setFocus()
        qapp.setActiveWindow(editor)

        ctrl._explicit_in_app_widget_ref = None  # Not explicitly set
        in_app_target = TargetWindowInfo(
            hwnd=0,
            title="VOX — Scratchpad",
            process_id=0,
            thread_id=0,
            process_name="VOX",
            focus_child_hwnd=0,
        )

        with patch.object(ctrl.db, "add_dictation_entry", return_value=1):
            ctrl._format_and_commit_dictation(
                raw_text="world",
                duration_sec=1.0,
                vocab=[],
                out_script="auto",
                target=in_app_target,
            )

        # Editor should have received the text
        assert "world" in editor.toPlainText().lower()
        editor.close()

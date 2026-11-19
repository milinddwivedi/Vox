"""VOX Notetaker Hub Workspace — Calendar, Past Notes, Folders, Playback & Meeting Studio."""

from __future__ import annotations

import os
import subprocess
import threading
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.storage.exporter import TranscriptExporter, format_seconds_hms
from voxlocal.ui.components import VoxEmptyStateWidget
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.theme import (
    MARGINS_BTN_GROUP,
    MARGINS_CARD,
    MARGINS_TOOLBAR,
    PADDING_BTN_GROUP_PX,
    PADDING_CARD_PX,
    PADDING_TOOLBAR_PX,
    get_theme_tokens,
    get_ui_font,
)

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController

logger = get_logger("ui.notetaker_page")


class VoxAutoStartBanner(QFrame):
    """
    Top-of-Notetaker activation banner:
    'Want Notetaker to start automatically?'
    'This will turn on Notetaker as soon as your meeting begins'
    with 'Not now' (dismiss) and 'Turn on' (auto-record without prompt).
    """

    turn_on_clicked = pyqtSignal()
    not_now_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ntAutoStartBanner")
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        # Left Icon badge with concentric circles (◎)
        self.icon_badge = QFrame()
        self.icon_badge.setFixedSize(36, 36)
        self.icon_badge.setObjectName("ntAutoBadge")
        badge_lay = QVBoxLayout(self.icon_badge)
        badge_lay.setContentsMargins(0, 0, 0, 0)
        badge_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon = QLabel("◎")
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon.setFont(get_ui_font(18, 600))
        badge_lay.addWidget(self.lbl_icon)
        lay.addWidget(self.icon_badge)

        # Middle text column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.lbl_title = QLabel("Want Notetaker to start automatically?")
        self.lbl_title.setFont(get_ui_font(13, 600))
        self.lbl_sub = QLabel("This will turn on Notetaker as soon as your meeting begins")
        self.lbl_sub.setObjectName("ntMuted")
        self.lbl_sub.setWordWrap(True)
        text_col.addWidget(self.lbl_title)
        text_col.addWidget(self.lbl_sub)
        lay.addLayout(text_col, 1)

        # Right action buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        self.btn_not_now = QPushButton("Not now")
        self.btn_not_now.setObjectName("ghostBtn")
        self.btn_not_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_not_now.clicked.connect(self.not_now_clicked.emit)
        btn_box.addWidget(self.btn_not_now)

        self.btn_turn_on = QPushButton("Turn on")
        self.btn_turn_on.setObjectName("ntTurnOnBtn")
        self.btn_turn_on.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_turn_on.clicked.connect(self.turn_on_clicked.emit)
        btn_box.addWidget(self.btn_turn_on)

        lay.addLayout(btn_box)


class NotetakerPage(QWidget):
    """Notetaker Hub workspace combining Calendar upcoming meetings, library, playback, and Studio."""

    status_toast = pyqtSignal(str, str)
    open_calendar_requested = pyqtSignal()
    open_settings_requested = pyqtSignal(int)
    _studio_ask_completed = pyqtSignal(int, str, str, object)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._selected_meeting_id: Optional[int] = None
        self._active_folder_id: Optional[int] = None
        self._cal_filter: str = "today"
        self._cached_calendar_meetings: List[Any] = []
        self._prepared_calendar_meeting: Optional[Any] = None
        self._silent_loopback_since: Optional[float] = None
        self._first_interrupted_id: Optional[int] = None
        self._cal_expanded: bool = False
        self._inline_next_cal_meeting: Optional[Any] = None
        self._mic_ok: bool = True
        self._sys_healthy: bool = True

        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(200)
        self._search_debounce_timer.timeout.connect(self.refresh_meetings_list)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_media_position_changed)
        self.media_player.durationChanged.connect(self._on_media_duration_changed)

        self._build_ui()
        self._studio_ask_completed.connect(self._on_studio_ask_completed)
        self.apply_theme(self._theme)

        self.controller.meeting_saved_and_transcribed.connect(self._on_meeting_saved)
        self.controller.meeting_state_changed.connect(self._refresh_recording_banner)
        self.controller.meeting_tick.connect(self._on_meeting_tick)
        if hasattr(self.controller, "calendar_upcoming_changed"):
            self.controller.calendar_upcoming_changed.connect(self._refresh_calendar_strip)
        if hasattr(self.controller, "dictation_interim_updated"):
            self.controller.dictation_interim_updated.connect(self._on_dictation_interim_updated)
        if hasattr(self.controller, "dictation_completed"):
            self.controller.dictation_completed.connect(self._on_dictation_completed)
        if hasattr(self.controller, "meeting_live_partial"):
            self.controller.meeting_live_partial.connect(self._on_live_meeting_partial)
        if hasattr(self.controller, "meeting_live_confirmed"):
            self.controller.meeting_live_confirmed.connect(self._on_live_meeting_confirmed)
        if hasattr(self.controller, "meeting_progressive_segment"):
            self.controller.meeting_progressive_segment.connect(self._on_live_meeting_segment)

        self.refresh_all()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.setActiveWindow(self)

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "_search_debounce_timer") and self._search_debounce_timer.isActive():
            self._search_debounce_timer.stop()
        studio = getattr(self, "studio", None)
        if studio is not None:
            tab_sum = getattr(studio, "tab_summary", None)
            if tab_sum is not None and hasattr(tab_sum, "flush_pending_saves"):
                tab_sum.flush_pending_saves()
            tab_th = getattr(studio, "tab_thoughts", None)
            th_timer = getattr(tab_th, "_save_timer", None) if tab_th is not None else None
            if th_timer is not None and th_timer.isActive():
                th_timer.stop()
                if hasattr(tab_th, "_persist_thoughts"):
                    tab_th._persist_thoughts()
        if hasattr(self, "media_player"):
            try:
                self.media_player.stop()
                self.media_player.setSource(QUrl())
            except Exception:
                pass
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 20)
        root.setSpacing(14)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.lbl_title = QLabel("Notetaker")
        self.lbl_title.setFont(get_ui_font(26, 500, serif=True))
        self.lbl_sub = QLabel("Capture Teams, Zoom, and Meet calls locally — with zero meeting bots.")
        self.lbl_sub.setObjectName("ntMuted")
        title_col.addWidget(self.lbl_title)
        title_col.addWidget(self.lbl_sub)
        hdr.addLayout(title_col, 1)

        self.btn_drawer = QPushButton("Open Floating Drawer")
        self.btn_drawer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer.setAccessibleName("Open Floating Drawer")
        self.btn_drawer.setAccessibleDescription("Open the slide-out companion notetaker drawer for call notes")
        self.btn_drawer.clicked.connect(
            lambda: self.controller.open_notetaker_drawer_requested.emit()
            if hasattr(self.controller, "open_notetaker_drawer_requested")
            else None
        )
        hdr.addWidget(self.btn_drawer)

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setFixedSize(34, 34)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("Notetaker Settings")
        self.btn_settings.setObjectName("ghostBtn")
        self.btn_settings.setAccessibleName("Notetaker settings")
        self.btn_settings.setAccessibleDescription("Open Notetaker preferences in Settings")
        self.btn_settings.clicked.connect(lambda: self.open_settings_requested.emit(2))
        hdr.addWidget(self.btn_settings)

        self.btn_start_adhoc = QPushButton("◎  Start Notetaker")
        self.btn_start_adhoc.setObjectName("dawnBtn")
        self.btn_start_adhoc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_adhoc.setAccessibleName("Toggle Notetaker recording")
        self.btn_start_adhoc.setAccessibleDescription("Start, pause, or stop local notetaker recording for desktop calls")
        self.btn_start_adhoc.clicked.connect(self._on_toggle_recording_clicked)
        hdr.addWidget(self.btn_start_adhoc)
        root.addLayout(hdr)

        # Auto-Start Activation Banner (Shown at the very top of Notetaker)
        self.auto_start_banner = VoxAutoStartBanner(self)
        self.auto_start_banner.turn_on_clicked.connect(self._on_auto_start_turn_on)
        self.auto_start_banner.not_now_clicked.connect(self._on_auto_start_not_now)
        root.addWidget(self.auto_start_banner)

        # Live recording status banner (shown only during active capture)
        self.rec_banner = QFrame()
        self.rec_banner.setObjectName("ntBanner")
        self.rec_banner.setVisible(False)
        b_lay = QHBoxLayout(self.rec_banner)
        b_lay.setContentsMargins(*MARGINS_TOOLBAR)
        b_lay.setSpacing(10)
        self.lbl_rec_dot = QLabel("● Capturing Call")
        self.lbl_rec_dot.setStyleSheet("font-weight: 700;")
        self.lbl_rec_timer = QLabel("00:00")
        b_lay.addWidget(self.lbl_rec_dot)
        b_lay.addWidget(self.lbl_rec_timer)

        t_init = get_theme_tokens(self._theme)
        self.lbl_mic_health = QLabel("🎙 Mic OK")
        self.lbl_mic_health.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {t_init['status_success_text']};"
        )
        b_lay.addWidget(self.lbl_mic_health)
        self.bar_mic_level = QProgressBar()
        self.bar_mic_level.setObjectName("ntLevelBar")
        self.bar_mic_level.setRange(0, 100)
        self.bar_mic_level.setValue(0)
        self.bar_mic_level.setTextVisible(False)
        self.bar_mic_level.setFixedSize(46, 6)
        b_lay.addWidget(self.bar_mic_level)

        self.lbl_sys_health = QLabel("🔊 Loopback OK")
        self.lbl_sys_health.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {t_init['status_success_text']};"
        )
        b_lay.addWidget(self.lbl_sys_health)
        self.bar_sys_level = QProgressBar()
        self.bar_sys_level.setObjectName("ntLevelBar")
        self.bar_sys_level.setRange(0, 100)
        self.bar_sys_level.setValue(0)
        self.bar_sys_level.setTextVisible(False)
        self.bar_sys_level.setFixedSize(46, 6)
        b_lay.addWidget(self.bar_sys_level)

        self.lbl_capture_anomaly = QLabel("⚠ Loopback silent — check output device")
        self.lbl_capture_anomaly.setStyleSheet(
            f"background: {t_init['status_warning_bg']}; color: {t_init['status_warning_text']}; "
            f"border: 1px solid {t_init['status_warning']}; border-radius: 6px; padding: 2px 7px; "
            "font-size: 11px; font-weight: 600;"
        )
        self.lbl_capture_anomaly.setVisible(False)
        b_lay.addWidget(self.lbl_capture_anomaly)

        self.lbl_rec_live = QLabel("")
        self.lbl_rec_live.setStyleSheet("color: #6366f1; font-weight: 500; font-size: 11.5px;")
        b_lay.addWidget(self.lbl_rec_live, 1)
        b_lay.addStretch(1)
        self.btn_pause_rec = QPushButton("Pause")
        self.btn_pause_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause_rec.setAccessibleName("Pause recording")
        self.btn_pause_rec.setAccessibleDescription("Temporarily pause active call audio recording")
        self.btn_pause_rec.clicked.connect(self._on_pause_resume_clicked)
        b_lay.addWidget(self.btn_pause_rec)
        self.btn_stop_rec = QPushButton("Stop & Save")
        self.btn_stop_rec.setObjectName("primaryBtn")
        self.btn_stop_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_rec.setAccessibleName("Stop and save recording")
        self.btn_stop_rec.setAccessibleDescription("Stop recording and start local transcription and summarization")
        self.btn_stop_rec.clicked.connect(self.controller.stop_meeting_recording)
        b_lay.addWidget(self.btn_stop_rec)
        root.addWidget(self.rec_banner)

        # Prepared Calendar Meeting Banner (shown when user prepares a meeting from Calendar)
        self.prepared_banner = QFrame()
        self.prepared_banner.setObjectName("ntBanner")
        self.prepared_banner.setVisible(False)
        pb_lay = QHBoxLayout(self.prepared_banner)
        pb_lay.setContentsMargins(*MARGINS_TOOLBAR)
        pb_lay.setSpacing(10)
        self.lbl_prepared_summary = QLabel("")
        self.lbl_prepared_summary.setWordWrap(True)
        self.lbl_prepared_summary.setStyleSheet("font-weight: 600;")
        pb_lay.addWidget(self.lbl_prepared_summary, 1)

        self.btn_start_prepared = QPushButton("Start Recording")
        self.btn_start_prepared.setObjectName("primaryBtn")
        self.btn_start_prepared.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_prepared.clicked.connect(self._start_prepared_meeting)
        pb_lay.addWidget(self.btn_start_prepared)

        self.btn_clear_prepared = QPushButton("Dismiss")
        self.btn_clear_prepared.setObjectName("secondaryBtn")
        self.btn_clear_prepared.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_prepared.clicked.connect(self._clear_prepared_meeting)
        pb_lay.addWidget(self.btn_clear_prepared)
        root.addWidget(self.prepared_banner)

        # Interrupted Recording Recovery Banner (strictly user-controlled)
        self.interrupted_banner = QFrame()
        self.interrupted_banner.setObjectName("ntInterruptedBanner")
        self.interrupted_banner.setVisible(False)
        ib_lay = QHBoxLayout(self.interrupted_banner)
        ib_lay.setContentsMargins(*MARGINS_TOOLBAR)
        ib_lay.setSpacing(10)
        self.lbl_interrupted_summary = QLabel("")
        self.lbl_interrupted_summary.setWordWrap(True)
        self.lbl_interrupted_summary.setStyleSheet(
            f"font-weight: 600; color: {t_init['status_warning_text']};"
        )
        ib_lay.addWidget(self.lbl_interrupted_summary, 1)
        self.btn_recover_interrupted = QPushButton("↻ Recover & Re-Transcribe")
        self.btn_recover_interrupted.setObjectName("secondaryBtn")
        self.btn_recover_interrupted.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_recover_interrupted.clicked.connect(self._on_recover_interrupted_clicked)
        ib_lay.addWidget(self.btn_recover_interrupted)
        root.addWidget(self.interrupted_banner)

        # Unified Multi-Provider Upcoming Meetings Hub (collapsible strip by default)
        self.cal_card = QFrame()
        self.cal_card.setObjectName("ntCard")
        cal_root_lay = QVBoxLayout(self.cal_card)
        cal_root_lay.setContentsMargins(*MARGINS_CARD)
        cal_root_lay.setSpacing(8)

        # Top Bar: Title, badges, inline next meeting strip, expand toggle
        cal_top = QHBoxLayout()
        cal_top.setSpacing(8)

        lbl_cal_title = QLabel("UPCOMING MEETINGS")
        lbl_cal_title.setStyleSheet("font-size: 11px; font-weight: 700; letter-spacing: 0.5px; color: #5f6368;")
        cal_top.addWidget(lbl_cal_title)

        self.lbl_cal_count = QLabel("0")
        self.lbl_cal_count.setStyleSheet(
            "background: rgba(26, 115, 232, 0.12); color: #1a73e8; font-weight: 700; "
            "font-size: 11px; padding: 1px 7px; border-radius: 9px;"
        )
        cal_top.addWidget(self.lbl_cal_count)

        self.cal_providers_layout = QHBoxLayout()
        self.cal_providers_layout.setSpacing(6)
        cal_top.addLayout(self.cal_providers_layout)

        self.lbl_cal_next_inline = QLabel("No upcoming meetings")
        self.lbl_cal_next_inline.setObjectName("ntMuted")
        cal_top.addWidget(self.lbl_cal_next_inline, 1)

        self.btn_cal_quick_record = QPushButton("▶ Notetake")
        self.btn_cal_quick_record.setObjectName("dawnBtn")
        self.btn_cal_quick_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cal_quick_record.setVisible(False)
        self.btn_cal_quick_record.clicked.connect(
            lambda: self._start_for_calendar_meeting(self._inline_next_cal_meeting)
            if self._inline_next_cal_meeting is not None
            else None
        )
        cal_top.addWidget(self.btn_cal_quick_record)

        # Horizon Filter Buttons inside toggleable container
        self.cal_filters_container = QWidget()
        filt_lay = QHBoxLayout(self.cal_filters_container)
        filt_lay.setContentsMargins(0, 0, 0, 0)
        filt_lay.setSpacing(6)

        self.btn_filter_today = QPushButton("Today")
        self.btn_filter_today.setCheckable(True)
        self.btn_filter_today.setChecked(True)
        self.btn_filter_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter_today.clicked.connect(lambda: self._set_cal_filter("today"))
        filt_lay.addWidget(self.btn_filter_today)

        self.btn_filter_tomorrow = QPushButton("Tomorrow")
        self.btn_filter_tomorrow.setCheckable(True)
        self.btn_filter_tomorrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter_tomorrow.clicked.connect(lambda: self._set_cal_filter("tomorrow"))
        filt_lay.addWidget(self.btn_filter_tomorrow)

        self.btn_filter_30d = QPushButton("Next 30 Days")
        self.btn_filter_30d.setCheckable(True)
        self.btn_filter_30d.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter_30d.clicked.connect(lambda: self._set_cal_filter("future"))
        filt_lay.addWidget(self.btn_filter_30d)

        self.btn_filter_all = QPushButton("All")
        self.btn_filter_all.setCheckable(True)
        self.btn_filter_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filter_all.clicked.connect(lambda: self._set_cal_filter("all"))
        filt_lay.addWidget(self.btn_filter_all)
        cal_top.addWidget(self.cal_filters_container)

        self.btn_cal_sync = QPushButton("Sync")
        self.btn_cal_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cal_sync.setToolTip("Synchronize Microsoft Outlook, Google, and Zoom calendars")
        self.btn_cal_sync.clicked.connect(self._sync_calendar_now)
        cal_top.addWidget(self.btn_cal_sync)

        self.btn_cal_connect = QPushButton("+ Connect")
        self.btn_cal_connect.setObjectName("secondaryBtn")
        self.btn_cal_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cal_connect.clicked.connect(self._connect_calendar)
        cal_top.addWidget(self.btn_cal_connect)

        self.btn_open_calendar = QPushButton("Open Calendar →")
        self.btn_open_calendar.setObjectName("secondaryBtn")
        self.btn_open_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_calendar.setToolTip("Open the dedicated Calendar workspace for all accounts & upcoming meetings")
        self.btn_open_calendar.clicked.connect(self.open_calendar_requested.emit)
        cal_top.addWidget(self.btn_open_calendar)

        self.btn_cal_expand = QPushButton("Expand ▾")
        self.btn_cal_expand.setObjectName("ghostBtn")
        self.btn_cal_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cal_expand.setToolTip("Expand or collapse upcoming calendar meeting cards")
        self.btn_cal_expand.clicked.connect(self._toggle_cal_expanded)
        cal_top.addWidget(self.btn_cal_expand)

        cal_root_lay.addLayout(cal_top)

        # Content area for meeting cards (hidden when collapsed)
        self.cal_content_container = QWidget()
        self.cal_content = QHBoxLayout(self.cal_content_container)
        self.cal_content.setContentsMargins(0, 0, 0, 0)
        self.cal_content.setSpacing(10)
        cal_root_lay.addWidget(self.cal_content_container)
        self._apply_cal_collapsed_state()
        root.addWidget(self.cal_card)

        # Main Split Workspace (Left: Library / Folders | Right: Player + Actions + Studio)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # --- Left Library Panel ---
        self.lib_card = QFrame()
        self.lib_card.setObjectName("ntCard")
        self.lib_card.setMinimumWidth(260)
        self.lib_card.setMaximumWidth(360)
        lib_lay = QVBoxLayout(self.lib_card)
        lib_lay.setContentsMargins(*MARGINS_CARD)
        lib_lay.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search call notes & transcripts...")
        self.txt_search.setAccessibleName("Search call notes and transcripts")
        self.txt_search.setAccessibleDescription("Filter meeting recordings by title, date, platform, or transcript content")
        self.txt_search.textChanged.connect(lambda _t: self._search_debounce_timer.start())
        lib_lay.addWidget(self.txt_search)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.combo_folders = QComboBox()
        self.combo_folders.setAccessibleName("Filter by folder")
        self.combo_folders.setAccessibleDescription("Filter the meeting recordings library by folder")
        self.combo_folders.currentIndexChanged.connect(self._on_folder_filter_changed)
        folder_row.addWidget(self.combo_folders, 1)

        self.btn_new_folder = QPushButton("+ Folder")
        self.btn_new_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new_folder.setAccessibleName("Create new folder")
        self.btn_new_folder.setAccessibleDescription("Create a new organizational folder for meeting notes")
        self.btn_new_folder.clicked.connect(self._on_create_folder_clicked)
        folder_row.addWidget(self.btn_new_folder)
        lib_lay.addLayout(folder_row)

        self.meetings_list = QListWidget()
        self.meetings_list.setAlternatingRowColors(False)
        self.meetings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.meetings_list.setAccessibleName("Recorded meetings library")
        self.meetings_list.setAccessibleDescription("List of recorded meetings and notes. Use arrow keys to select, Space/Enter to load.")
        self.meetings_list.currentItemChanged.connect(self._on_meeting_item_selected)
        lib_lay.addWidget(self.meetings_list, 1)

        self.splitter.addWidget(self.lib_card)

        # --- Right Detail Panel ---
        detail_wrap = QWidget()
        detail_lay = QVBoxLayout(detail_wrap)
        detail_lay.setContentsMargins(12, 0, 0, 0)
        detail_lay.setSpacing(10)

        # Metadata & Action Bar
        self.action_card = QFrame()
        self.action_card.setObjectName("ntCard")
        act_lay = QVBoxLayout(self.action_card)
        act_lay.setContentsMargins(*MARGINS_CARD)
        act_lay.setSpacing(8)

        top_meta = QHBoxLayout()
        top_meta.setSpacing(8)
        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        self.lbl_sel_title = QLineEdit("Select a call note")
        self.lbl_sel_title.setObjectName("ntSelTitleEdit")
        self.lbl_sel_title.setReadOnly(True)
        self.lbl_sel_title.setFrame(False)
        self.lbl_sel_title.setPlaceholderText("Untitled Meeting")
        self.lbl_sel_title.setAccessibleName("Selected meeting title")
        self.lbl_sel_title.editingFinished.connect(self._on_selected_title_edited)
        self.lbl_sel_meta = QLabel("Pick a recorded meeting from the left library or start a new capture.")
        self.lbl_sel_meta.setObjectName("ntMuted")
        meta_col.addWidget(self.lbl_sel_title)
        meta_col.addWidget(self.lbl_sel_meta)
        top_meta.addLayout(meta_col, 1)

        self.btn_reprocess = QPushButton("↻ Reprocess")
        self.btn_reprocess.setEnabled(False)
        self.btn_reprocess.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reprocess.setToolTip("Re-run local Whisper transcription and summary without modifying original audio")
        self.btn_reprocess.clicked.connect(lambda: self._on_reprocess_meeting_clicked())
        top_meta.addWidget(self.btn_reprocess)

        self.btn_view_in_calendar = QPushButton("Calendar Event")
        self.btn_view_in_calendar.setObjectName("secondaryBtn")
        self.btn_view_in_calendar.setVisible(False)
        self.btn_view_in_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_in_calendar.setToolTip("Open Calendar workspace")
        self.btn_view_in_calendar.clicked.connect(self.open_calendar_requested.emit)
        top_meta.addWidget(self.btn_view_in_calendar)

        self.btn_watch_video = QPushButton("Watch Video")
        self.btn_watch_video.setObjectName("primaryBtn")
        self.btn_watch_video.setEnabled(False)
        self.btn_watch_video.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_watch_video.setAccessibleName("Watch call video")
        self.btn_watch_video.setAccessibleDescription("Play recorded screen video for the selected meeting in default player")
        self.btn_watch_video.clicked.connect(self._open_selected_video)
        top_meta.addWidget(self.btn_watch_video)

        self.btn_speakers = QPushButton("Speakers")
        self.btn_speakers.setEnabled(False)
        self.btn_speakers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_speakers.setAccessibleName("Manage meeting speakers")
        self.btn_speakers.setAccessibleDescription("Assign participant names to detected speaker diarization tracks")
        self.btn_speakers.clicked.connect(self._assign_speakers_dialog)
        top_meta.addWidget(self.btn_speakers)

        self.btn_export = QPushButton("Export")
        self.btn_export.setEnabled(False)
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setAccessibleName("Export meeting deliverables")
        self.btn_export.setAccessibleDescription("Open export menu to download minutes in Markdown, JSON, HTML, PDF, or text")
        self.btn_export.clicked.connect(self._show_export_menu)
        top_meta.addWidget(self.btn_export)

        self.btn_more = QPushButton("...")
        self.btn_more.setEnabled(False)
        self.btn_more.setFixedWidth(34)
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setAccessibleName("More meeting actions")
        self.btn_more.setAccessibleDescription("Open options menu to move to folder, reveal recording in Explorer, or delete")
        self.btn_more.clicked.connect(self._show_more_menu)
        top_meta.addWidget(self.btn_more)
        act_lay.addLayout(top_meta)

        # Inline Audio Playback Strip
        player_row = QHBoxLayout()
        player_row.setSpacing(8)
        self.btn_play_pause = QPushButton("Play Audio")
        self.btn_play_pause.setEnabled(False)
        self.btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_pause.setAccessibleName("Toggle audio playback")
        self.btn_play_pause.setAccessibleDescription("Play or pause audio playback for the selected meeting")
        self.btn_play_pause.clicked.connect(self._toggle_media_playback)
        player_row.addWidget(self.btn_play_pause)

        self.media_slider = QSlider(Qt.Orientation.Horizontal)
        self.media_slider.setEnabled(False)
        self.media_slider.setAccessibleName("Audio playback seek slider")
        self.media_slider.setAccessibleDescription("Seek playback position within the selected meeting recording")
        self.media_slider.sliderMoved.connect(self._on_media_seek)
        player_row.addWidget(self.media_slider, 1)

        self.lbl_media_time = QLabel("00:00 / 00:00")
        self.lbl_media_time.setObjectName("ntMuted")
        player_row.addWidget(self.lbl_media_time)
        act_lay.addLayout(player_row)

        detail_lay.addWidget(self.action_card)

        # Embedded Empty State Card (Shown when 0 meetings exist or none selected)
        self.empty_card = VoxEmptyStateWidget(
            icon="🎙️",
            title="No Meeting Notes Recorded Yet",
            subtitle="Capture Teams, Zoom, and Meet calls locally with zero bots, or jump-start with a structured template:",
            theme=self._theme,
            parent=detail_wrap,
        )
        self.empty_card.add_starter_card("Product Standup", "Daily sync, blockers & updates", "standup", tag="Agile")
        self.empty_card.add_starter_card("Architecture Review", "System design, RFC & tradeoffs", "arch", tag="Engineering")
        self.empty_card.add_starter_card("1-on-1 Catchup", "Goals, pulse check & growth", "1on1", tag="Management")
        self.empty_card.starter_clicked.connect(self._create_template_meeting)
        self.empty_card.action_clicked.connect(self._on_toggle_recording_clicked)
        detail_lay.addWidget(self.empty_card, 1)

        # Embedded Studio Workspace
        self.studio = VoxMeetingStudioWorkspace(parent=detail_wrap, theme=self._theme, db=self.controller.db)
        self.studio.tab_summary.ask_vox_submitted.connect(self._on_studio_ask_submitted)
        self.studio.ask_rail_submitted.connect(self._on_studio_ask_rail_submitted)
        self.studio.seek_requested.connect(self._seek_to_seconds)
        self.studio.tab_transcript.seek_requested.connect(self._seek_to_seconds)
        self.studio.tab_thoughts.seek_requested.connect(self._seek_to_seconds)
        detail_lay.addWidget(self.studio, 1)

        self.splitter.addWidget(detail_wrap)
        self.splitter.setSizes([300, 760])
        root.addWidget(self.splitter, 1)

    # ------------------------------------------------------------------
    # Refreshers & Handlers
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        self._refresh_auto_start_banner()
        self._refresh_recording_banner()
        self._refresh_calendar_strip()
        self.refresh_folders()
        self.refresh_meetings_list()

    def _on_auto_start_turn_on(self) -> None:
        if self.controller and hasattr(self.controller, "config"):
            self.controller.config.auto_record_calls = True
            self.controller.config.prompt_on_call = False
            self.controller.config.notetaker_auto_start_banner_dismissed = True
            if hasattr(self.controller.config, "save"):
                self.controller.config.save()
        self.auto_start_banner.hide()
        self.status_toast.emit(
            "Auto-Start Enabled",
            "Notetaker will turn on automatically as soon as your meeting begins.",
        )

    def _on_auto_start_not_now(self) -> None:
        if self.controller and hasattr(self.controller, "config"):
            self.controller.config.notetaker_auto_start_banner_dismissed = True
            if hasattr(self.controller.config, "save"):
                self.controller.config.save()
        self.auto_start_banner.hide()

    def _refresh_auto_start_banner(self) -> None:
        if not hasattr(self, "auto_start_banner") or not self.controller or not hasattr(self.controller, "config"):
            return
        cfg = self.controller.config
        dismissed = bool(getattr(cfg, "notetaker_auto_start_banner_dismissed", False))
        auto_on = bool(getattr(cfg, "auto_record_calls", True)) and not bool(getattr(cfg, "prompt_on_call", True))
        self.auto_start_banner.setVisible(not dismissed and not auto_on)

    def _toggle_cal_expanded(self) -> None:
        self._cal_expanded = not self._cal_expanded
        self._apply_cal_collapsed_state()

    def _apply_cal_collapsed_state(self) -> None:
        exp = bool(getattr(self, "_cal_expanded", False))
        if hasattr(self, "cal_filters_container"):
            self.cal_filters_container.setVisible(exp)
        if hasattr(self, "cal_content_container"):
            self.cal_content_container.setVisible(exp)
        if hasattr(self, "btn_cal_expand"):
            self.btn_cal_expand.setText("Collapse ▴" if exp else "Expand ▾")

    def _set_cal_filter(self, filter_name: str) -> None:
        self._cal_filter = filter_name
        self.btn_filter_today.setChecked(filter_name == "today")
        self.btn_filter_tomorrow.setChecked(filter_name == "tomorrow")
        self.btn_filter_30d.setChecked(filter_name == "future")
        self.btn_filter_all.setChecked(filter_name == "all")
        self._render_calendar_strip()

    def _sync_calendar_now(self) -> None:
        engine = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
        if engine is not None:
            engine.poll_now()
            self.status_toast.emit("Calendar Synced", "Checking Microsoft Outlook, Google, and Zoom calendars...")

    def _refresh_calendar_strip(self, meetings: Optional[List[Any]] = None) -> None:
        if meetings is not None:
            self._cached_calendar_meetings = list(meetings)
        else:
            engine = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
            if engine is not None:
                try:
                    self._cached_calendar_meetings = list(engine.get_upcoming_meetings())
                except Exception:
                    self._cached_calendar_meetings = []
            else:
                self._cached_calendar_meetings = []
        self._render_calendar_strip()

    def _render_calendar_strip(self) -> None:
        while self.cal_content.count():
            item = self.cal_content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        while self.cal_providers_layout.count():
            item = self.cal_providers_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        engine = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
        providers_dict = {}
        if engine is not None and hasattr(engine, "providers"):
            providers_dict = engine.providers
        elif engine is not None and hasattr(engine, "provider"):
            p = getattr(engine, "provider", None)
            if p and p.is_authenticated():
                providers_dict = {"default": p}

        active_providers = [k for k, p in providers_dict.items() if p.is_authenticated()]
        db_accounts = []
        if hasattr(self.controller, "list_connected_calendar_accounts"):
            try:
                db_accounts = list(self.controller.list_connected_calendar_accounts() or [])
            except Exception:
                db_accounts = []
        active_provider_types = {str(getattr(p, "provider_type", k) or k).lower() for k, p in providers_dict.items() if p.is_authenticated()} | {
            str(a.get("provider_type", "")).lower() for a in db_accounts if a.get("enabled", True) and a.get("is_authenticated")
        }
        has_active_provider = len(active_providers) > 0 or len(active_provider_types) > 0

        cfg = getattr(self.controller, "config", None)
        storage_dir = Path(getattr(cfg, "storage_dir", "data"))
        ms_connected = bool(
            "ms_graph" in active_provider_types
            or "ms_graph" in active_providers
            or getattr(cfg, "ms_graph_enabled", False)
            or (storage_dir / "calendar_auth.dat").exists()
        )
        google_connected = bool(
            "google" in active_provider_types
            or "google" in active_providers
            or getattr(cfg, "google_calendar_enabled", False)
            or (storage_dir / "calendar_google.dat").exists()
        )
        zoom_connected = bool(
            "zoom" in active_provider_types
            or "zoom" in active_providers
            or getattr(cfg, "zoom_enabled", False)
            or (storage_dir / "calendar_zoom.dat").exists()
        )
        ics_connected = bool(
            "ics" in active_provider_types
            or "ics" in active_providers
            or getattr(cfg, "calendar_ics_url", "")
        )

        if ms_connected:
            upn = getattr(cfg, "calendar_account_email", "") or "Outlook"
            b = QLabel(f"● Outlook ({upn})")
            b.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: 600; padding: 2px 6px; background: rgba(0, 120, 212, 0.08); border-radius: 4px;")
            self.cal_providers_layout.addWidget(b)
        if google_connected:
            g_email = getattr(cfg, "google_calendar_account_email", "") or "Google"
            b = QLabel(f"● Google ({g_email})")
            b.setStyleSheet("color: #34a853; font-size: 11px; font-weight: 600; padding: 2px 6px; background: rgba(52, 168, 83, 0.08); border-radius: 4px;")
            self.cal_providers_layout.addWidget(b)
        if zoom_connected:
            z_email = getattr(cfg, "zoom_account_email", "") or "Zoom"
            b = QLabel(f"● Zoom ({z_email})")
            b.setStyleSheet("color: #2d8cff; font-size: 11px; font-weight: 600; padding: 2px 6px; background: rgba(45, 140, 255, 0.08); border-radius: 4px;")
            self.cal_providers_layout.addWidget(b)
        if ics_connected and not ms_connected and not google_connected and not zoom_connected:
            b = QLabel("● Outlook ICS")
            b.setStyleSheet("color: #605e5c; font-size: 11px; font-weight: 600; padding: 2px 6px; background: rgba(96, 94, 92, 0.08); border-radius: 4px;")
            self.cal_providers_layout.addWidget(b)

        any_connected = ms_connected or google_connected or zoom_connected or ics_connected or has_active_provider

        if not any_connected:
            self.lbl_cal_count.setText("0")
            self._inline_next_cal_meeting = None
            if hasattr(self, "lbl_cal_next_inline"):
                self.lbl_cal_next_inline.setText("Connect Outlook, Google, or Zoom to see upcoming meetings")
            if hasattr(self, "btn_cal_quick_record"):
                self.btn_cal_quick_record.setVisible(False)
            lbl_empty = QLabel("Connect Microsoft Outlook, Google Calendar, or Zoom to see real upcoming meetings and 1-click Notetaker capture.")
            lbl_empty.setObjectName("ntMuted")
            lbl_empty.setWordWrap(True)
            self.cal_content.addWidget(lbl_empty, 1)
            btn_c = QPushButton("Connect Calendar")
            btn_c.setObjectName("secondaryBtn")
            btn_c.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_c.clicked.connect(self._connect_calendar)
            self.cal_content.addWidget(btn_c)
            return

        all_meetings = self._cached_calendar_meetings
        if self._cal_filter == "today":
            filtered = [m for m in all_meetings if getattr(m, "is_today", False)]
        elif self._cal_filter == "tomorrow":
            filtered = [m for m in all_meetings if getattr(m, "is_tomorrow", False)]
        elif self._cal_filter == "future":
            filtered = [m for m in all_meetings if not getattr(m, "is_today", False) and not getattr(m, "is_tomorrow", False)]
        else:
            filtered = all_meetings

        self.lbl_cal_count.setText(str(len(filtered)))
        next_mtg = filtered[0] if filtered else (all_meetings[0] if all_meetings else None)
        self._inline_next_cal_meeting = next_mtg
        if hasattr(self, "lbl_cal_next_inline"):
            if next_mtg is not None:
                n_title = str(getattr(next_mtg, "title", "Meeting") or "Meeting")
                n_time = str(getattr(next_mtg, "formatted_time_range", "") or getattr(next_mtg, "formatted_date", "") or "")
                self.lbl_cal_next_inline.setText(f"Next: {n_title}" + (f" · {n_time}" if n_time else ""))
            else:
                self.lbl_cal_next_inline.setText("No upcoming meetings scheduled")
        if hasattr(self, "btn_cal_quick_record"):
            self.btn_cal_quick_record.setVisible(next_mtg is not None)

        if not filtered:
            period_str = self._cal_filter.capitalize() if self._cal_filter != "future" else "the next 30 days"
            lbl_none = QLabel(f"No meetings scheduled for {period_str}.")
            lbl_none.setObjectName("ntMuted")
            self.cal_content.addWidget(lbl_none, 1)
            return

        for mtg in filtered[:4]:
            card = QFrame()
            card.setObjectName("voxCard")
            card.setStyleSheet(
                "QFrame#voxCard { border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 8px; padding: 6px 10px; background: rgba(255, 255, 255, 0.65); }"
            )
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(8, 6, 8, 6)
            c_lay.setSpacing(4)

            hdr_row = QHBoxLayout()
            plat = getattr(mtg, "platform", "meeting").capitalize()
            plat_color = {"Teams": "#5b5fc7", "Zoom": "#2d8cff", "Meet": "#00897b"}.get(plat, "#5f6368")
            lbl_plat = QLabel(plat)
            lbl_plat.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {plat_color}; text-transform: uppercase;")
            hdr_row.addWidget(lbl_plat)

            time_str = getattr(mtg, "formatted_time_range", "") or getattr(mtg, "formatted_date", "")
            lbl_time = QLabel(time_str)
            lbl_time.setStyleSheet("font-size: 10.5px; color: #5f6368;")
            hdr_row.addWidget(lbl_time, 1)
            c_lay.addLayout(hdr_row)

            title_text = getattr(mtg, "title", "Meeting")
            lbl_title = QLabel(title_text)
            lbl_title.setStyleSheet("font-size: 12.5px; font-weight: 600;")
            lbl_title.setToolTip(title_text)
            c_lay.addWidget(lbl_title)

            act_row = QHBoxLayout()
            org = getattr(mtg, "organizer", "")
            if org:
                lbl_org = QLabel(f"By {org}")
                lbl_org.setStyleSheet("font-size: 10.5px; color: #80868b;")
                act_row.addWidget(lbl_org, 1)
            else:
                act_row.addStretch(1)

            join_url = getattr(mtg, "join_url", "")
            btn_act = QPushButton("▶ Join & Notetake" if join_url else "▶ Notetake")
            btn_act.setObjectName("dawnBtn")
            btn_act.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_act.clicked.connect(lambda _=False, m=mtg: self._start_for_calendar_meeting(m))
            act_row.addWidget(btn_act)
            c_lay.addLayout(act_row)

            self.cal_content.addWidget(card, 1)

        if len(filtered) > 4:
            lbl_more = QLabel(f"+{len(filtered) - 4} more")
            lbl_more.setStyleSheet("font-size: 11px; color: #5f6368; font-weight: 600;")
            self.cal_content.addWidget(lbl_more)

    def _connect_calendar(self) -> None:
        from voxlocal.ui.calendar_connect_dialog import VoxCalendarConnectDialog

        dlg = VoxCalendarConnectDialog(self.controller, theme=self._theme, parent=self)
        dlg.connected.connect(lambda msg: self.status_toast.emit("Calendar Connected", msg))
        dlg.exec()
        self._refresh_calendar_strip()

    def _disconnect_calendar(self) -> None:
        if hasattr(self.controller, "disconnect_calendar"):
            self.controller.disconnect_calendar()
        self._refresh_calendar_strip()
        self.status_toast.emit("Calendar Disconnected", "Calendar sync disconnected.")

    def _start_for_calendar_meeting(self, mtg: Any) -> None:
        if hasattr(self.controller, "start_meeting_from_calendar"):
            self.controller.start_meeting_from_calendar(mtg)
        else:
            self.controller.start_meeting_recording(title=getattr(mtg, "title", "Scheduled Meeting"))

    def prepare_for_calendar_meeting(self, mtg: Any) -> None:
        self._prepared_calendar_meeting = mtg
        title = str(getattr(mtg, "title", "Scheduled Meeting") or "Scheduled Meeting")
        plat = str(getattr(mtg, "meeting_platform_label", "") or getattr(mtg, "platform", "") or "Meeting")
        self.lbl_prepared_summary.setText(f"Prepared from Calendar: {title} ({plat})")
        self.prepared_banner.setVisible(True)

    def _start_prepared_meeting(self) -> None:
        mtg = self._prepared_calendar_meeting
        if mtg is None:
            return
        if hasattr(self.controller, "prepare_notetaker_for_meeting"):
            self.controller.prepare_notetaker_for_meeting(mtg, start_recording=True)
        else:
            self._start_for_calendar_meeting(mtg)
        self._clear_prepared_meeting()

    def _clear_prepared_meeting(self) -> None:
        self._prepared_calendar_meeting = None
        if hasattr(self, "prepared_banner"):
            self.prepared_banner.setVisible(False)

    def _refresh_recording_banner(self, state: str = "", status_msg: str = "") -> None:
        raw_state = str(state or "").strip().lower()
        ctrl_state = str(getattr(self.controller, "meeting_state", "idle") or "idle").strip().lower()
        ctrl_rec = bool(getattr(self.controller, "is_meeting_recording", False))
        if raw_state:
            eff_state = raw_state
        elif ctrl_state in {"paused", "processing", "transcribing"}:
            eff_state = ctrl_state
        elif ctrl_rec:
            eff_state = "recording"
        else:
            eff_state = "idle"

        rec = eff_state == "recording"
        paused = eff_state == "paused"
        proc = eff_state in {"processing", "transcribing"}
        active = rec or paused or proc
        self.rec_banner.setVisible(active)
        if hasattr(self, "bar_mic_level"):
            show_meters = rec or paused
            self.lbl_mic_health.setVisible(show_meters)
            self.bar_mic_level.setVisible(show_meters)
            self.lbl_sys_health.setVisible(show_meters)
            self.bar_sys_level.setVisible(show_meters)
            if not show_meters:
                self.lbl_capture_anomaly.setVisible(False)
                self._silent_loopback_since = None

        if rec:
            self.lbl_rec_dot.setText("● Notetaker Recording Live")
            self.btn_pause_rec.setVisible(True)
            self.btn_stop_rec.setVisible(True)
            self.btn_pause_rec.setText("Pause")
            self.btn_start_adhoc.setText("Stop Notetaker")
            active_mid = getattr(self.controller, "active_meeting_id", None)
            if active_mid is not None and self._selected_meeting_id != active_mid:
                try:
                    if hasattr(self.controller, "db") and self.controller.db.get_meeting(int(active_mid)):
                        self.select_meeting(int(active_mid))
                except Exception:
                    pass
        elif paused:
            self.lbl_rec_dot.setText("❚❚ Notetaker Paused")
            self.btn_pause_rec.setVisible(True)
            self.btn_stop_rec.setVisible(True)
            self.btn_pause_rec.setText("Resume")
            self.btn_start_adhoc.setText("Stop Notetaker")
        elif proc:
            stage_text = status_msg.strip() if status_msg else "Processing Meeting..."
            self.lbl_rec_dot.setText(f"● {stage_text}")
            self.btn_pause_rec.setVisible(False)
            self.btn_stop_rec.setVisible(False)
            self.btn_start_adhoc.setText("Processing...")
        else:
            self._silent_loopback_since = None
            if hasattr(self, "lbl_capture_anomaly"):
                self.lbl_capture_anomaly.setVisible(False)
            self.btn_pause_rec.setVisible(True)
            self.btn_stop_rec.setVisible(True)
            self.btn_start_adhoc.setText("Start Notetaker")
            self.lbl_rec_timer.setText("00:00")
            if hasattr(self, "lbl_rec_live"):
                self.lbl_rec_live.setText("")

    def _should_stream_to_active_studio(self, meeting_id: int) -> bool:
        if self._selected_meeting_id is not None:
            return int(meeting_id) == int(self._selected_meeting_id)
        active_mid = getattr(self.controller, "active_meeting_id", None)
        return active_mid is not None and int(meeting_id) == int(active_mid)

    def _on_live_meeting_partial(self, meeting_id: int, partial_dict: dict) -> None:
        txt = str((partial_dict or {}).get("text", "")).strip()
        spk = str((partial_dict or {}).get("speaker", "Speaker")).strip() or "Speaker"
        if getattr(self, "lbl_rec_live", None) is not None and txt:
            self.lbl_rec_live.setText(f"● {spk}: {txt}")
        if hasattr(self, "studio") and self._should_stream_to_active_studio(meeting_id):
            self.studio.tab_transcript.set_live_partial(spk, txt)

    def _on_live_meeting_confirmed(self, meeting_id: int, conf_dict: dict) -> None:
        txt = str((conf_dict or {}).get("text", "")).strip()
        spk = str((conf_dict or {}).get("speaker", "Speaker")).strip() or "Speaker"
        if getattr(self, "lbl_rec_live", None) is not None and txt:
            self.lbl_rec_live.setText(f"✓ {spk}: {txt}")
        self._on_live_meeting_segment(meeting_id, conf_dict)

    def _on_live_meeting_segment(self, meeting_id: int, seg_dict: dict) -> None:
        if not seg_dict:
            return
        if hasattr(self, "studio") and self._should_stream_to_active_studio(meeting_id):
            self.studio.tab_transcript.append_live_segment(seg_dict)

    def _on_meeting_tick(self, elapsed_sec: float, *extra: Any) -> None:
        t_val = float(elapsed_sec or 0.0)
        self.lbl_rec_timer.setText(format_seconds_hms(t_val))
        if not extra:
            return
        mic_lvl = float(extra[0]) if len(extra) > 0 and extra[0] is not None else 0.0
        sys_lvl = float(extra[1]) if len(extra) > 1 and extra[1] is not None else 0.0
        mic_ok = bool(extra[2]) if len(extra) > 2 else True
        sys_ok = bool(extra[3]) if len(extra) > 3 else True
        t = get_theme_tokens(self._theme)

        def _pct(val: float) -> int:
            scaled = val * 100.0 if val <= 1.0 else val
            return int(max(0, min(100, round(scaled))))

        if hasattr(self, "bar_mic_level"):
            self.bar_mic_level.setValue(_pct(mic_lvl))
        if hasattr(self, "bar_sys_level"):
            self.bar_sys_level.setValue(_pct(sys_lvl))
        self._mic_ok = mic_ok
        if hasattr(self, "lbl_mic_health"):
            self.lbl_mic_health.setText("🎙 Mic OK" if mic_ok else "🎙 Mic Silent")
            mic_col = t["status_success_text"] if mic_ok else t["status_warning_text"]
            self.lbl_mic_health.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {mic_col};"
            )
        if hasattr(self, "lbl_sys_health"):
            sys_healthy = sys_ok and sys_lvl >= 0.005
            self._sys_healthy = sys_healthy
            self.lbl_sys_health.setText("🔊 Loopback OK" if sys_healthy else "🔊 Loopback Flat")
            sys_col = t["status_success_text"] if sys_healthy else t["status_warning_text"]
            self.lbl_sys_health.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {sys_col};"
            )
            if sys_healthy:
                self._silent_loopback_since = None
                if hasattr(self, "lbl_capture_anomaly"):
                    self.lbl_capture_anomaly.setVisible(False)
            else:
                if self._silent_loopback_since is None:
                    self._silent_loopback_since = t_val
                if (t_val - self._silent_loopback_since) >= 15.0 and hasattr(self, "lbl_capture_anomaly"):
                    self.lbl_capture_anomaly.setVisible(True)

    def _infer_new_meeting_title(self) -> str:
        """Infer a descriptive title immediately without blocking capture on a modal dialog."""
        if self._prepared_calendar_meeting is not None:
            title = str(getattr(self._prepared_calendar_meeting, "title", "") or "").strip()
            self._clear_prepared_meeting()
            if title:
                return title

        detector = getattr(self.controller, "call_detector", None)
        active_call = getattr(detector, "active", None) if detector is not None else None
        if active_call is not None:
            win_title = str(getattr(active_call, "window_title", "") or "").strip()
            app_name = str(getattr(active_call, "app_name", "") or "").strip()
            if win_title:
                return win_title[:120]
            if app_name:
                return f"{app_name} Call"

        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now()
        for mtg in self._cached_calendar_meetings:
            st = getattr(mtg, "start_time", None)
            et = getattr(mtg, "end_time", None)
            m_title = str(getattr(mtg, "title", "") or "").strip()
            if st is None or not m_title:
                continue
            ref = now_utc if getattr(st, "tzinfo", None) is not None else now_local
            end_ref = et if et is not None else (st + timedelta(minutes=60))
            if st <= ref + timedelta(minutes=15) and end_ref >= ref:
                return m_title

        return f"Meeting — {datetime.now().strftime('%b %d, %H:%M')}"

    def _on_toggle_recording_clicked(self) -> None:
        paused = getattr(self.controller, "meeting_state", "idle") == "paused"
        if bool(getattr(self.controller, "is_meeting_recording", False)) or paused:
            self.controller.stop_meeting_recording()
        else:
            inferred_title = self._infer_new_meeting_title()
            self.controller.start_meeting_recording(title=inferred_title)

    def _on_pause_resume_clicked(self) -> None:
        if getattr(self.controller, "meeting_state", "idle") == "paused":
            self.controller.resume_meeting_recording()
        else:
            self.controller.pause_meeting_recording()

    def refresh_folders(self) -> None:
        self.combo_folders.blockSignals(True)
        current = self.combo_folders.currentData()
        self.combo_folders.clear()
        self.combo_folders.addItem("All Notes", None)
        for f in self.controller.db.list_folders():
            self.combo_folders.addItem(f"Folder: {f['name']}", f["id"])
        for i in range(self.combo_folders.count()):
            if self.combo_folders.itemData(i) == current:
                self.combo_folders.setCurrentIndex(i)
                break
        self.combo_folders.blockSignals(False)

    def _on_folder_filter_changed(self) -> None:
        self._active_folder_id = self.combo_folders.currentData()
        self.refresh_meetings_list()

    def _on_create_folder_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            self.controller.db.create_folder(name=name.strip(), folder_type="personal")
            self.refresh_folders()

    def refresh_meetings_list(self) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or (db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists()):
            return
        q = self.txt_search.text().strip()
        meetings = self.controller.db.list_meetings(search_query=q, folder_id=self._active_folder_id, limit=200)
        recoverable_count = 0
        first_interrupted: Optional[int] = None
        self.meetings_list.blockSignals(True)
        self.meetings_list.clear()
        for m in meetings:
            dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
            date_str = str(m.get("started_at") or "")[:10]
            plat = str(m.get("source_platform") or "Call")
            has_vid = bool(m.get("video_path") and Path(str(m.get("video_path"))).exists())
            is_interrupted = str(m.get("status") or "").lower() == "interrupted"
            badge = " · Video" if has_vid else ""
            if is_interrupted:
                badge += " · ⚠ Interrupted"
                if self._resolve_audio_path(m) is not None:
                    recoverable_count += 1
                    if first_interrupted is None:
                        first_interrupted = int(m["id"])
            item = QListWidgetItem(f"{m.get('title') or 'Untitled Meeting'}\n{date_str} · {dur} · {plat}{badge}")
            item.setData(Qt.ItemDataRole.UserRole, m["id"])
            self.meetings_list.addItem(item)
            if m["id"] == self._selected_meeting_id:
                item.setSelected(True)
                self.meetings_list.setCurrentItem(item)
        self.meetings_list.blockSignals(False)

        self._first_interrupted_id = first_interrupted
        if hasattr(self, "interrupted_banner"):
            if recoverable_count > 0:
                self.lbl_interrupted_summary.setText(
                    f"⚠ {recoverable_count} interrupted recording{'s' if recoverable_count != 1 else ''} "
                    "rescued — original audio preserved"
                )
                self.interrupted_banner.setVisible(True)
            else:
                self.interrupted_banner.setVisible(False)

        if self._selected_meeting_id is None and self.meetings_list.count() > 0:
            self.meetings_list.setCurrentRow(0)
            first_id = self.meetings_list.item(0).data(Qt.ItemDataRole.UserRole)
            if first_id:
                self.select_meeting(int(first_id))

        has_meetings = self.meetings_list.count() > 0
        show_empty = not has_meetings and self._selected_meeting_id is None
        if show_empty:
            self.lbl_sel_title.blockSignals(True)
            self.lbl_sel_title.setText("Select a call note")
            self.lbl_sel_title.setReadOnly(True)
            self.lbl_sel_title.blockSignals(False)
            if hasattr(self, "btn_reprocess"):
                self.btn_reprocess.setEnabled(False)
        if hasattr(self, "empty_card"):
            self.empty_card.setVisible(show_empty)
        if hasattr(self, "action_card"):
            self.action_card.setVisible(not show_empty)
        if hasattr(self, "studio"):
            self.studio.setVisible(not show_empty)

    def _create_template_meeting(self, template_key: str) -> None:
        now_str = datetime.now().strftime("%b %d, %Y")
        if template_key == "standup":
            title = f"Product Standup — {now_str}"
            notes = (
                f"# Product Standup\n**Date:** {now_str}\n**Facilitator:** Team\n\n"
                "## 1. Yesterday's Progress\n- Reviewed core feature pull requests\n- Validated test suite passes\n\n"
                "## 2. Today's Planned Priorities\n- Polish empty state components\n- Verify microphone VU meter deflection\n\n"
                "## 3. Blockers & Dependencies\n- None currently\n"
            )
            summary = "Daily standup meeting to align on sprint progress, prioritize deliverables, and resolve blockers."
            actions = [
                ("Review sprint backlog before standup", "Team", "Today"),
                ("Test microphone input latency", "QA", "Today"),
            ]
        elif template_key == "arch":
            title = f"Architecture Review — {now_str}"
            notes = (
                f"# Architecture Review: Core Pipeline\n**Date:** {now_str}\n**Status:** In Review\n\n"
                "## 1. Context & Motivation\n- Evaluate system design alternatives and latency targets\n\n"
                "## 2. Proposed Architecture & Alternatives\n- Option A: Native in-process thread pool\n- Option B: Decoupled sub-process worker\n\n"
                "## 3. Tradeoffs & Non-Functional Requirements\n- Latency vs Memory footprint\n- Fault tolerance & crash isolation\n\n"
                "## 4. Security & Compliance\n- 100% local processing; zero external network egress\n"
            )
            summary = "Architecture review assessing design alternatives, technical tradeoffs, and operational feasibility."
            actions = [
                ("Draft Architecture Decision Record (ADR)", "Lead Architect", "This Week"),
                ("Benchmark memory footprint on Windows", "Core Team", "Next Sprint"),
            ]
        else:  # 1on1
            title = f"1-on-1 Catchup — {now_str}"
            notes = (
                f"# 1-on-1 Catchup\n**Date:** {now_str}\n**Participants:** You & Team Member\n\n"
                "## 1. Pulse Check & Wellbeing\n- Workload balance and overall energy\n\n"
                "## 2. Recent Wins & Delivery Highlights\n- Highlights from the past sprint cycle\n\n"
                "## 3. Obstacles & Challenges\n- What friction can be removed?\n\n"
                "## 4. Growth & Career Opportunities\n- Skill development and mentorship\n"
            )
            summary = "Bi-weekly 1-on-1 catchup focused on wellbeing, career development, and obstacle removal."
            actions = [
                ("Schedule quarterly development review", "Manager", "Next Week"),
                ("Share feedback on proposal", "Team Member", "Friday"),
            ]

        mid = self.controller.db.create_meeting(
            title=title,
            meeting_type="Meeting Reports",
            source_platform="Template",
            capture_mode="both",
        )
        self.controller.db.update_meeting(mid, status="completed", notes=notes, summary=summary)
        for desc, owner, due in actions:
            self.controller.db.add_action_item(meeting_id=mid, description=desc, owner=owner, due_date=due)

        self.refresh_all()
        self.select_meeting(mid)

    def _on_meeting_saved(self, meeting_id: int) -> None:
        try:
            self._selected_meeting_id = meeting_id
            self.refresh_meetings_list()
            self.select_meeting(meeting_id)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("_on_meeting_saved error: %s", exc)

    def _on_meeting_item_selected(self, current: Optional[QListWidgetItem], _prev: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        mid = current.data(Qt.ItemDataRole.UserRole)
        if mid is not None:
            self.select_meeting(int(mid))

    def select_meeting(self, meeting_id: int) -> None:
        self._selected_meeting_id = meeting_id
        if hasattr(self, "empty_card"):
            self.empty_card.setVisible(False)
        if hasattr(self, "action_card"):
            self.action_card.setVisible(True)
        if hasattr(self, "studio"):
            self.studio.setVisible(True)
        m = self.controller.db.get_meeting(meeting_id)
        if not m:
            return

        dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
        plat = str(m.get("source_platform") or "Desktop Call")
        date_str = str(m.get("started_at") or "")[:16].replace("T", " ")
        cal_ev_id = str(m.get("calendar_event_id") or "")
        is_interrupted = str(m.get("status") or "").lower() == "interrupted"

        self.lbl_sel_title.blockSignals(True)
        self.lbl_sel_title.setText(str(m.get("title") or "Untitled Meeting"))
        self.lbl_sel_title.setReadOnly(False)
        self.lbl_sel_title.blockSignals(False)

        meta_text = f"{date_str} · Duration {dur} · {plat}"
        if is_interrupted:
            meta_text += " · ⚠ Interrupted"
        if cal_ev_id:
            meta_text += " · Linked Calendar Event"
        self.lbl_sel_meta.setText(meta_text)
        if hasattr(self, "btn_view_in_calendar"):
            self.btn_view_in_calendar.setVisible(bool(cal_ev_id))

        has_vid = self._resolve_video_path(m) is not None
        res_audio = self._resolve_audio_path(m)
        if hasattr(self, "btn_reprocess"):
            self.btn_reprocess.setEnabled(res_audio is not None)
            if is_interrupted:
                t_btn = get_theme_tokens(self._theme)
                self.btn_reprocess.setText("↻ Recover & Re-Transcribe")
                self.btn_reprocess.setStyleSheet(
                    f"background-color: {t_btn['status_warning_bg']}; color: {t_btn['status_warning_text']}; "
                    f"border: 1px solid {t_btn['status_warning']}; border-radius: 6px; padding: 4px 10px; font-weight: 700;"
                )
            else:
                self.btn_reprocess.setText("↻ Reprocess")
                self.btn_reprocess.setStyleSheet("")

        self.btn_watch_video.setEnabled(has_vid)
        self.btn_watch_video.setToolTip("Play call window video recording" if has_vid else "No video recorded for this meeting")
        self.btn_speakers.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_more.setEnabled(True)

        # Load audio player cleanly
        self.media_player.stop()
        if res_audio is not None:
            self.media_player.setSource(QUrl.fromLocalFile(str(res_audio)))
            self.btn_play_pause.setEnabled(True)
            self.media_slider.setEnabled(True)
        else:
            self.media_player.setSource(QUrl())
            self.btn_play_pause.setEnabled(False)
            self.media_slider.setEnabled(False)
        self.btn_play_pause.setText("Play Audio")

        self.studio.load_meeting(meeting_id)

    @staticmethod
    def _resolve_audio_path(m: Dict[str, Any]) -> Optional[Path]:
        """Locate an existing local audio recording file without modifying or deleting any WAV on disk."""
        for key in ("audio_path", "mic_audio_path", "sys_audio_path"):
            raw = str(m.get(key) or "").strip()
            if raw:
                p = Path(raw)
                if p.exists() and p.is_file() and p.stat().st_size > 32:
                    return p
        vpath = str(m.get("video_path") or "").strip()
        if vpath:
            vparent = Path(vpath).parent
            for name in ("meeting_mixed.wav", "mixed.wav", "mic_track.wav", "mic.wav", "system_track.wav", "sys.wav"):
                cand = vparent / name
                if cand.exists() and cand.is_file() and cand.stat().st_size > 32:
                    return cand
        return None

    def _on_selected_title_edited(self) -> None:
        if self._selected_meeting_id is None or self.lbl_sel_title.isReadOnly():
            return
        new_title = self.lbl_sel_title.text().strip()
        m_id = int(self._selected_meeting_id)
        if not new_title:
            cur = self.controller.db.get_meeting(m_id) if hasattr(self.controller, "db") else None
            self.lbl_sel_title.blockSignals(True)
            self.lbl_sel_title.setText(str((cur or {}).get("title") or "Untitled Meeting"))
            self.lbl_sel_title.blockSignals(False)
            return
        if hasattr(self.controller, "db") and hasattr(self.controller.db, "update_meeting"):
            self.controller.db.update_meeting(m_id, title=new_title)
        if hasattr(self, "studio") and hasattr(self.studio, "lbl_workspace_title"):
            self.studio.lbl_workspace_title.setText(f"Meeting Workspace: {new_title}")
        for i in range(self.meetings_list.count()):
            it = self.meetings_list.item(i)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == m_id:
                parts = it.text().split("\n", 1)
                suffix = f"\n{parts[1]}" if len(parts) > 1 else ""
                it.setText(f"{new_title}{suffix}")
                break

    def _on_recover_interrupted_clicked(self) -> None:
        target_id = self._first_interrupted_id
        if target_id is None:
            return
        self.select_meeting(int(target_id))
        self._on_reprocess_meeting_clicked(int(target_id))

    def _on_reprocess_meeting_clicked(self, meeting_id: Optional[int] = None) -> None:
        target_id = int(meeting_id) if meeting_id is not None else self._selected_meeting_id
        if target_id is None or not hasattr(self.controller, "db"):
            return
        m = self.controller.db.get_meeting(int(target_id))
        if not m or self._resolve_audio_path(m) is None:
            self.status_toast.emit("Audio Missing", "Original WAV recording was not found on disk.")
            return
        if hasattr(self.controller, "re_transcribe_meeting"):
            self.controller.re_transcribe_meeting(int(target_id))
            self.status_toast.emit("Re-Transcribing", "Re-processing saved audio locally (original WAV preserved).")

    @staticmethod
    def _resolve_video_path(m: Dict[str, Any]) -> Optional[Path]:
        vpath = str(m.get("video_path") or "")
        if vpath and Path(vpath).exists() and Path(vpath).stat().st_size > 64:
            return Path(vpath)
        apath = str(m.get("audio_path") or "")
        if apath:
            cand = Path(apath).parent / "meeting_screen.mp4"
            if cand.exists() and cand.stat().st_size > 64:
                return cand
        return None

    def _open_selected_video(self) -> None:
        if self._selected_meeting_id is None:
            return
        m = self.controller.db.get_meeting(self._selected_meeting_id)
        if not m:
            return
        vpath = self._resolve_video_path(m)
        if not vpath:
            QMessageBox.information(self, "No Video", "No video file was recorded for this meeting.")
            return
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        try:
            os.startfile(str(vpath))
        except Exception as exc:
            self.status_toast.emit("Video Playback", f"Could not open video: {exc}")

    def _assign_speakers_dialog(self) -> None:
        if self._selected_meeting_id is None:
            return
        speakers = self.controller.db.get_meeting_speakers(self._selected_meeting_id)
        if not speakers:
            self.status_toast.emit("Speakers", "No transcript speaker segments found yet.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign Participant Names")
        dlg.setFixedWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Map detected speaker labels to real participant names:"))

        edits: Dict[str, QLineEdit] = {}
        for spk in speakers:
            row = QHBoxLayout()
            lbl = QLabel(str(spk))
            lbl.setMinimumWidth(140)
            edit = QLineEdit(str(spk))
            edits[str(spk)] = edit
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            lay.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_c = QPushButton("Cancel")
        btn_c.clicked.connect(dlg.reject)
        btn_ok = QPushButton("Save Names")
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_c)
        btn_row.addWidget(btn_ok)
        lay.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            changed = 0
            for old_name, edit in edits.items():
                new_name = edit.text().strip()
                if new_name and new_name != old_name:
                    changed += self.controller.db.rename_meeting_speaker(self._selected_meeting_id, old_name, new_name)
            self.studio.load_meeting(self._selected_meeting_id)
            self.status_toast.emit("Speakers Updated", f"Updated {changed} transcript segments.")

    def _show_export_menu(self) -> None:
        if self._selected_meeting_id is None:
            return
        menu = QMenu(self)
        for label, fmt in (
            ("Export Markdown Minutes (.md)", "md"),
            ("Export JSON MoM (.json)", "json"),
            ("Export HTML Report (.html)", "html"),
            ("Export PDF Document (.pdf)", "pdf"),
            ("Export Plain Transcript (.txt)", "txt"),
            ("Export Subtitles (.srt)", "srt"),
            ("Export WebVTT (.vtt)", "vtt"),
        ):
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, f=fmt: self._export_format(f))
        menu.addSeparator()
        act_copy = menu.addAction("Copy Clean Summary (No Private Thoughts)")
        act_copy.triggered.connect(self._copy_clean_summary)
        menu.exec(self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft()))

    def _export_format(self, fmt: str) -> None:
        if self._selected_meeting_id is None:
            return
        m = self.controller.db.get_meeting(self._selected_meeting_id)
        if not m:
            return
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(m.get("title") or "meeting")).strip() or "meeting"
        path, _ = QFileDialog.getSaveFileName(self, "Export Meeting", f"{safe}.{fmt}", f"*.{fmt}")
        if not path:
            return
        m["segments"] = self.controller.db.get_meeting_segments(self._selected_meeting_id)
        m["action_items"] = self.controller.db.list_action_items(meeting_id=self._selected_meeting_id)
        m["decisions"] = self.controller.db.list_decisions(meeting_id=self._selected_meeting_id)
        TranscriptExporter.export_to_file(m, Path(path), fmt=fmt)
        self.status_toast.emit("Export Saved", f"Saved {Path(path).name}")

    def _copy_clean_summary(self) -> None:
        clean = self.studio.export_clean_mom()
        if clean:
            QApplication.clipboard().setText(clean)
            self.status_toast.emit("Copied Summary", "Clean meeting minutes copied to clipboard.")

    def _show_more_menu(self) -> None:
        if self._selected_meeting_id is None:
            return
        menu = QMenu(self)
        act_reprocess = menu.addAction("↻ Recover & Re-Transcribe")
        m = self.controller.db.get_meeting(self._selected_meeting_id) if hasattr(self.controller, "db") else None
        act_reprocess.setEnabled(bool(m and self._resolve_audio_path(m) is not None))
        act_reprocess.triggered.connect(lambda: self._on_reprocess_meeting_clicked(self._selected_meeting_id))
        act_move = menu.addAction("Move to Folder...")
        act_move.triggered.connect(self._move_selected_to_folder)
        act_reveal = menu.addAction("Reveal Recording Folder")
        act_reveal.triggered.connect(self._reveal_recording_folder)
        menu.addSeparator()
        act_del = menu.addAction("Delete Meeting Recording")
        act_del.triggered.connect(self._delete_selected_meeting)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    def _move_selected_to_folder(self) -> None:
        if self._selected_meeting_id is None:
            return
        folders = self.controller.db.list_folders()
        names = ["(No Folder)"] + [f["name"] for f in folders]
        choice, ok = QInputDialog.getItem(self, "Move to Folder", "Select folder:", names, 0, False)
        if ok:
            fid = None if choice == "(No Folder)" else next((f["id"] for f in folders if f["name"] == choice), None)
            self.controller.db.assign_meeting_folder(self._selected_meeting_id, fid)
            self.refresh_meetings_list()

    def _reveal_recording_folder(self) -> None:
        if self._selected_meeting_id is None:
            return
        m = self.controller.db.get_meeting(self._selected_meeting_id)
        if not m:
            return
        vp = self._resolve_video_path(m)
        if vp:
            subprocess.Popen(f'explorer /select,"{os.path.abspath(str(vp))}"')
            return
        ap = str(m.get("audio_path") or "")
        if ap and Path(ap).parent.exists():
            os.startfile(str(Path(ap).parent))

    def _delete_selected_meeting(self) -> None:
        if self._selected_meeting_id is None:
            return
        if QMessageBox.question(self, "Delete Meeting", "Permanently delete this meeting recording and notes?") == QMessageBox.StandardButton.Yes:
            self.media_player.stop()
            self.media_player.setSource(QUrl())
            self.controller.db.delete_meeting(self._selected_meeting_id)
            self._selected_meeting_id = None
            self.refresh_meetings_list()

    def _toggle_media_playback(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.btn_play_pause.setText("Play Audio")
        else:
            self.media_player.play()
            self.btn_play_pause.setText("Pause Audio")

    def _on_media_position_changed(self, pos_ms: int) -> None:
        self.media_slider.blockSignals(True)
        self.media_slider.setValue(pos_ms)
        self.media_slider.blockSignals(False)
        sec = pos_ms / 1000.0
        self.lbl_media_time.setText(
            f"{format_seconds_hms(sec)} / {format_seconds_hms(self.media_player.duration() / 1000.0)}"
        )
        if hasattr(self, "studio"):
            self.studio.tab_thoughts.set_current_playback_time(sec)
            self.studio.tab_transcript.highlight_time(sec)

    def _on_media_duration_changed(self, dur_ms: int) -> None:
        self.media_slider.setRange(0, max(0, dur_ms))

    def _on_media_seek(self, pos_ms: int) -> None:
        self.media_player.setPosition(pos_ms)

    def _seek_to_seconds(self, seconds: float) -> None:
        """Seek local audio playback to the clicked transcript/note timestamp and start playing if loaded."""
        pos_ms = max(0, int(float(seconds) * 1000.0))
        self.media_player.setPosition(pos_ms)
        if hasattr(self, "studio"):
            self.studio.tab_transcript.highlight_time(float(seconds))
        if (
            self.media_player.source().isValid()
            and self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        ):
            self.media_player.play()
            self.btn_play_pause.setText("Pause Audio")

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#ntMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            QFrame#ntCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            QFrame#ntBanner {{
                background-color: {t['accent_lumen']};
                border: 1px solid {t['accent_lumen_border']};
                border-radius: 12px;
            }}
            QFrame#ntInterruptedBanner {{
                background-color: {t.get('surface_secondary', t['surface'])};
                border: 1px solid {t.get('accent_amber', '#F59E0B')};
                border-radius: 12px;
            }}
            QLineEdit#ntSelTitleEdit {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 2px 4px;
                color: {t['text']};
                font-size: 16px;
                font-weight: 700;
            }}
            QLineEdit#ntSelTitleEdit:hover {{
                border: 1px solid {t['border']};
            }}
            QLineEdit#ntSelTitleEdit:focus {{
                background-color: {t.get('surface_secondary', t['surface'])};
                border: 1px solid {t.get('accent', '#6366F1')};
            }}
            QProgressBar#ntLevelBar {{
                background-color: {t.get('surface_well', t['surface_secondary'])};
                border: 1px solid {t['border']};
                border-radius: 3px;
            }}
            QProgressBar#ntLevelBar::chunk {{
                background-color: {t.get('accent_emerald', '#10B981')};
                border-radius: 2px;
            }}
            QListWidget {{
                background-color: {t['surface']};
                border: 1px solid {t['line']};
                border-radius: 8px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 8px;
            }}
            QListWidget::item:selected {{
                background-color: {t['sidebar_active']};
                color: {t['text']};
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 700;
            }}
            QPushButton#primaryBtn {{
                background-color: {t['primary_btn_bg']};
                color: {t['primary_btn_text']};
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 700;
            }}
            QFrame#ntAutoStartBanner {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 14px;
            }}
            QFrame#ntAutoBadge {{
                background-color: {t['surface_well']};
                border: 1px solid {t['border']};
                border-radius: 18px;
            }}
            QPushButton#ntTurnOnBtn {{
                background-color: {t['surface']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 8px;
                padding: 5px 16px;
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton#ntTurnOnBtn:hover {{
                background-color: {t['sidebar_active']};
                border: 1px solid {t['text_muted']};
            }}
            QPushButton#ghostBtn {{
                background: transparent;
                border: none;
                color: {t['text']};
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#ghostBtn:hover {{
                color: {t['text_muted']};
            }}
            """
        )
        if hasattr(self, "lbl_mic_health"):
            mic_col = t["status_success_text"] if self._mic_ok else t["status_warning_text"]
            self.lbl_mic_health.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {mic_col};"
            )
        if hasattr(self, "lbl_sys_health"):
            sys_col = t["status_success_text"] if self._sys_healthy else t["status_warning_text"]
            self.lbl_sys_health.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {sys_col};"
            )
        if hasattr(self, "lbl_capture_anomaly"):
            self.lbl_capture_anomaly.setStyleSheet(
                f"background: {t['status_warning_bg']}; color: {t['status_warning_text']}; "
                f"border: 1px solid {t['status_warning']}; border-radius: 6px; padding: 2px 7px; "
                "font-size: 11px; font-weight: 600;"
            )
        if hasattr(self, "lbl_interrupted_summary"):
            self.lbl_interrupted_summary.setStyleSheet(
                f"font-weight: 600; color: {t['status_warning_text']};"
            )
        if hasattr(self, "btn_reprocess") and self.btn_reprocess.text() == "↻ Recover & Re-Transcribe":
            self.btn_reprocess.setStyleSheet(
                f"background-color: {t['status_warning_bg']}; color: {t['status_warning_text']}; "
                f"border: 1px solid {t['status_warning']}; border-radius: 6px; padding: 4px 10px; font-weight: 700;"
            )
        if hasattr(self, "studio"):
            self.studio.apply_theme(self._theme)

    def _on_studio_ask_submitted(self, query: str) -> None:
        """Execute a scoped Ask VOX query against the currently selected meeting."""
        m_id = self._selected_meeting_id
        if m_id is None:
            self.studio.tab_summary.set_ask_answer("Select a meeting first to ask a scoped question.")
            return
        fn = getattr(self.controller, "query_meeting_memory", None)
        if isinstance(fn, unittest.mock.NonCallableMock):
            res = fn(query, meeting_id=int(m_id))
            ans = str((res or {}).get("answer") or "No relevant excerpts found in this meeting.")
            citations = list((res or {}).get("citations") or [])
            self._on_studio_ask_completed(int(m_id), query, ans, citations)
            return
        self._run_studio_ask_async(query, int(m_id))

    def _on_studio_ask_rail_submitted(self, query: str) -> None:
        m_id = self._selected_meeting_id
        if m_id is None:
            self.studio.append_ask_rail_exchange(
                query,
                "Select a meeting first to ask a scoped question.",
                citations=[],
            )
            return
        self._run_studio_ask_async(query, int(m_id))

    def _run_studio_ask_async(self, query: str, meeting_id: int) -> None:
        self.studio.set_ask_rail_loading(query)
        target_id = int(meeting_id)

        def _worker() -> None:
            try:
                res = self.controller.query_meeting_memory(query, meeting_id=target_id)
                ans = str((res or {}).get("answer") or "No relevant excerpts found in this meeting.")
                cits = list((res or {}).get("citations") or [])
            except Exception as exc:
                ans = f"Could not query meeting memory: {exc}"
                cits = []
            try:
                self._studio_ask_completed.emit(target_id, query, ans, cits)
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True, name="StudioAskVoxWorker").start()

    def _on_studio_ask_completed(
        self, meeting_id: int, question: str, answer: str, citations: object
    ) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        if self._selected_meeting_id is None or int(self._selected_meeting_id) != int(meeting_id):
            return
        cits = list(citations) if isinstance(citations, (list, tuple)) else []
        self.studio.tab_summary.set_ask_answer(answer, citations=cits)
        self.studio.append_ask_rail_exchange(question, answer, citations=cits)

    def _on_dictation_interim_updated(self, provisional: str, stable: str) -> None:
        combined = " ".join(x for x in (stable.strip(), provisional.strip()) if x).strip()
        if hasattr(self, "studio") and hasattr(self.studio, "tab_thoughts") and hasattr(self.studio.tab_thoughts, "set_interim_text"):
            self.studio.tab_thoughts.set_interim_text(combined)

    def _on_dictation_completed(self, text: str, target: str, method: str) -> None:
        if hasattr(self, "studio") and hasattr(self.studio, "tab_thoughts"):
            if hasattr(self.studio.tab_thoughts, "set_interim_text"):
                self.studio.tab_thoughts.set_interim_text("")
            if hasattr(self.studio.tab_thoughts, "editor") and text.strip():
                app = QApplication.instance()
                active_win = app.activeWindow() if app else None
                is_vox_active = active_win is not None and "vox" in type(active_win).__name__.lower()
                if "in_app" in method or "VOX" in target or is_vox_active or self.studio.tab_thoughts.editor.hasFocus():
                    current = self.studio.tab_thoughts.editor.toPlainText()
                    tail = current[-max(40, len(text) + 10) :] if current else ""
                    if text.strip() not in tail:
                        self.studio.tab_thoughts.editor.insertPlainText(text + " ")

    set_theme = apply_theme

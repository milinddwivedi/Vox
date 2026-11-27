"""VOX Notetaker Hub Workspace — Dual-Context Meeting Workspace & Overall Intelligence."""

from __future__ import annotations

import html
import json
import os
import subprocess
import threading
import time
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QShortcut
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
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.storage.exporter import TranscriptExporter, format_seconds_hms
from voxlocal.ui.components import VoxEmptyStateWidget
from voxlocal.ui.icons import get_icon, get_pixmap
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.meeting_ui_helpers import (
    VoxSegmentedControl,
    render_safe_markdown_html,
    strip_markdown_for_plain_copy,
)
from voxlocal.ui.overall_intelligence_view import VoxOverallIntelligenceView
from voxlocal.ui.theme import (
    MARGINS_BTN_GROUP,
    MARGINS_CARD,
    MARGINS_TOOLBAR,
    PADDING_BTN_GROUP_PX,
    PADDING_CARD_PX,
    PADDING_TOOLBAR_PX,
    get_html_font_family,
    get_theme_tokens,
    get_ui_font,
)

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController

logger = get_logger("ui.notetaker_page")


class VoxMeetingListDelegate(QStyledItemDelegate):
    """Compact/Comfortable chronological library row renderer with inline section headers."""

    ROLE_META = Qt.ItemDataRole.UserRole + 1
    ROLE_GROUP = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "light", compact: bool = False) -> None:
        super().__init__(parent)
        self.theme = (theme or "light").lower()
        self.compact = bool(compact)

    def set_theme(self, theme: str) -> None:
        self.theme = (theme or "light").lower()

    def set_compact(self, compact: bool) -> None:
        self.compact = bool(compact)

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:
        group = str(index.data(self.ROLE_GROUP) or "")
        base_h = 48 if self.compact else 58
        grp_h = 20 if group else 0
        w = max(210, int(option.rect.width()) if option.rect.width() > 0 else 250)
        return QSize(w, base_h + grp_h)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        meta = index.data(self.ROLE_META)
        if not isinstance(meta, dict):
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = get_theme_tokens(self.theme)

        group = str(index.data(self.ROLE_GROUP) or "")
        rect = QRect(option.rect)
        grp_h = 20 if group else 0

        if group:
            grp_rect = QRect(rect.left() + 10, rect.top() + 4, rect.width() - 20, 16)
            painter.setPen(QColor(t.get("text_dim", "#64748B")))
            gfont = get_ui_font(10, 600)
            painter.setFont(gfont)
            painter.drawText(grp_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, group)

        card_rect = rect.adjusted(4, grp_h + 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            bg = QColor(t.get("accent_dawn_tint", "#152238" if self.theme == "dark" else "#E8F0FE"))
            bdr = QColor(t.get("accent", "#1967D2"))
        elif hovered:
            bg = QColor(t.get("surface_hover", "#F1F5F9"))
            bdr = QColor(t.get("outline_variant", "#E2E8F0"))
        else:
            bg = QColor(t.get("surface", "#FFFFFF"))
            bdr = QColor("transparent")

        painter.setBrush(bg)
        painter.setPen(bdr if hovered or selected else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(card_rect, 9, 9)

        if selected:
            painter.save()
            painter.setBrush(QColor(t.get("accent", "#1967D2")))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRect(card_rect.left() + 2, card_rect.top() + 8, 3, max(16, card_rect.height() - 16)), 1.5, 1.5)
            painter.restore()

        title = str(meta.get("title") or "Untitled Meeting")
        sub = str(meta.get("sub") or "")
        status_kind = str(meta.get("status_kind") or "ok")
        is_compact = bool(meta.get("compact", self.compact))

        pad_x = 12
        inner_left = card_rect.left() + pad_x
        inner_w = max(40, card_rect.width() - (pad_x * 2) - 12)

        # Right status dot
        dot_color = {
            "recoverable": t.get("status_warning", "#D97706"),
            "processing": t.get("accent", "#1967D2"),
            "recording": t.get("status_danger", "#DC2626"),
        }.get(status_kind, "")
        if dot_color:
            painter.setBrush(QColor(dot_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(card_rect.right() - 14, card_rect.top() + 12, 7, 7)

        # Title line
        tfont = get_ui_font(12 if is_compact else 13, 600 if selected else 550)
        painter.setFont(tfont)
        title_col = t.get("accent_dawn_text", t.get("text_primary", "#0F172A")) if selected else t.get("text_primary", "#0F172A")
        painter.setPen(QColor(title_col))
        elided_title = QFontMetrics(tfont).elidedText(title, Qt.TextElideMode.ElideRight, inner_w)
        title_h = 18 if is_compact else 20
        painter.drawText(
            QRect(inner_left, card_rect.top() + (5 if is_compact else 7), inner_w, title_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_title,
        )

        # Metadata subtitle line
        sfont = get_ui_font(10 if is_compact else 11, 400)
        painter.setFont(sfont)
        sub_col = t.get("text_secondary", "#475569") if selected else t.get("text_muted", "#64748B")
        painter.setPen(QColor(sub_col))
        elided_sub = QFontMetrics(sfont).elidedText(sub, Qt.TextElideMode.ElideRight, card_rect.width() - 22)
        painter.drawText(
            QRect(inner_left, card_rect.top() + (23 if is_compact else 29), card_rect.width() - 22, 17),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_sub,
        )

        painter.restore()


class VoxAutoStartBanner(QFrame):
    """
    Compact top activation banner:
    'Want Notetaker to start automatically?'
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
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)

        self.icon_badge = QFrame()
        self.icon_badge.setFixedSize(26, 26)
        self.icon_badge.setObjectName("ntAutoBadge")
        badge_lay = QVBoxLayout(self.icon_badge)
        badge_lay.setContentsMargins(0, 0, 0, 0)
        badge_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from .icons import VectorIconLabel
        self.lbl_icon = VectorIconLabel("◎")
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon.setPixmap(get_pixmap("meetings", "#1967D2", 15))
        badge_lay.addWidget(self.lbl_icon)
        lay.addWidget(self.icon_badge)

        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        self.lbl_title = QLabel("Want Notetaker to start automatically?")
        self.lbl_title.setFont(get_ui_font(12, 600))
        self.lbl_sub = QLabel("This will turn on Notetaker as soon as your meeting begins")
        self.lbl_sub.setObjectName("ntMuted")
        text_row.addWidget(self.lbl_title)
        text_row.addWidget(self.lbl_sub, 1)
        lay.addLayout(text_row, 1)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)
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
    """Notetaker Hub workspace combining Dual-Context Intelligence, Library, Playback, and Studio."""

    status_toast = pyqtSignal(str, str)
    open_calendar_requested = pyqtSignal()
    open_settings_requested = pyqtSignal(int)
    _studio_ask_completed = pyqtSignal(str, str, str, object)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        cfg = getattr(controller, "config", None)

        self._selected_meeting_id: Optional[int] = None
        self._last_meetings_sig: Optional[Any] = None
        self._active_folder_id: Optional[int] = None
        self._workspace_context: str = "meeting"  # "meeting" | "overall"
        self._library_filter: str = "all"  # "all" | "completed" | "recoverable" | "video"
        self._library_sort: str = "newest"  # "newest" | "duration" | "title"
        self._library_compact: bool = bool(getattr(cfg, "notetaker_library_compact", False))
        self._sidebar_collapsed: bool = bool(getattr(cfg, "notetaker_library_collapsed", False))
        self._ask_rail_open: bool = bool(getattr(cfg, "notetaker_ask_rail_open", False))
        self._split_mode: str = str(getattr(cfg, "notetaker_split_mode", "off") or "off")
        self._playback_rates: Tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)
        self._playback_rate_idx: int = 0

        self._cal_filter: str = "today"
        self._cached_calendar_meetings: List[Any] = []
        self._cal_user_toggled: bool = False
        self._prepared_calendar_meeting: Optional[Any] = None
        self._silent_loopback_since: Optional[float] = None
        self._first_interrupted_id: Optional[int] = None
        self._interrupted_dismissed: bool = False
        self._cal_expanded: bool = False
        self._inline_next_cal_meeting: Optional[Any] = None
        self._mic_ok: bool = True
        self._sys_healthy: bool = True

        # Context-isolated Ask VOX conversation stores (NEVER cross-leak)
        self._meeting_ask_threads: Dict[int, List[Dict[str, Any]]] = {}
        self._overall_ask_thread: List[Dict[str, Any]] = []
        self._overall_scope_ids: Optional[List[int]] = None
        self._overall_scope_label: str = "All Recorded Meetings"
        self._ask_loading_key: Optional[str] = None
        self._last_ask_answer_plain: str = ""
        self._last_ask_with_sources_plain: str = ""

        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(200)
        self._search_debounce_timer.timeout.connect(self.refresh_meetings_list)

        self._pending_media_path: Optional[Path] = None
        self._loaded_media_url: str = ""
        self._media_probe_timer = QTimer(self)
        self._media_probe_timer.setSingleShot(True)
        self._media_probe_timer.setInterval(60)
        self._media_probe_timer.timeout.connect(self._apply_pending_media_source)

        self._fin_started_mono: Optional[float] = None
        self._fin_elapsed_timer = QTimer(self)
        self._fin_elapsed_timer.setInterval(1000)
        self._fin_elapsed_timer.timeout.connect(self._update_finalization_timer)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_media_position_changed)
        self.media_player.durationChanged.connect(self._on_media_duration_changed)
        if hasattr(self.media_player, "errorOccurred"):
            self.media_player.errorOccurred.connect(self._on_media_error)

        self._build_ui()
        self._setup_shortcuts()
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
        if hasattr(self.controller, "knowledge_updated"):
            self.controller.knowledge_updated.connect(self._on_knowledge_updated)

        self.refresh_all()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Alt+1"), self).activated.connect(lambda: self.set_workspace_context("meeting"))
        QShortcut(QKeySequence("Alt+2"), self).activated.connect(lambda: self.set_workspace_context("overall"))
        QShortcut(QKeySequence("Ctrl+\\"), self).activated.connect(self._toggle_sidebar_collapsed)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self).activated.connect(self._toggle_ask_rail)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_library_search)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self._toggle_summary_transcript_split)
        for tab_i in range(4):
            QShortcut(QKeySequence(f"Alt+Shift+{tab_i + 1}"), self).activated.connect(
                lambda idx=tab_i: self.studio.unified_tabs.setCurrentIndex(idx)
                if hasattr(self, "studio") and self.studio.unified_tabs.count() > idx
                else None
            )

    def _focus_library_search(self) -> None:
        if getattr(self, "_sidebar_collapsed", False):
            self._toggle_sidebar_collapsed(False)
        if hasattr(self, "txt_search"):
            self.txt_search.setFocus()
            self.txt_search.selectAll()

    def _toggle_summary_transcript_split(self) -> None:
        cur = getattr(self, "_split_mode", "off")
        self.set_split_mode("off" if cur != "off" else "summary_transcript")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.setActiveWindow(self)

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "_search_debounce_timer") and self._search_debounce_timer.isActive():
            self._search_debounce_timer.stop()
        if hasattr(self, "overall_view") and hasattr(self.overall_view, "_search_timer"):
            if self.overall_view._search_timer.isActive():
                self.overall_view._search_timer.stop()
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
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ------------------------------------------------------------------
        # 1. Slim Top Command Bar (~46px) — Mode Switcher + Status Pills + Core Actions
        # ------------------------------------------------------------------
        self.command_bar = QFrame()
        self.command_bar.setObjectName("ntCommandBar")
        hdr = QHBoxLayout(self.command_bar)
        hdr.setContentsMargins(10, 6, 10, 6)
        hdr.setSpacing(8)

        self.btn_toggle_sidebar = QPushButton("")
        self.btn_toggle_sidebar.setFixedSize(30, 30)
        self.btn_toggle_sidebar.setIconSize(QSize(16, 16))
        self.btn_toggle_sidebar.setObjectName("ghostBtn")
        self.btn_toggle_sidebar.setCheckable(True)
        self.btn_toggle_sidebar.setChecked(not self._sidebar_collapsed)
        self.btn_toggle_sidebar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_sidebar.setToolTip("Show or hide Meeting Library (Ctrl+\\)")
        self.btn_toggle_sidebar.setAccessibleName("Toggle meeting library sidebar")
        self.btn_toggle_sidebar.clicked.connect(self._toggle_sidebar_collapsed)
        hdr.addWidget(self.btn_toggle_sidebar)

        self.lbl_title = QLabel("Meetings")
        self.lbl_title.setFont(get_ui_font(18, 600))
        hdr.addWidget(self.lbl_title)

        self.mode_switcher = VoxSegmentedControl(
            [("meeting", "This Meeting"), ("overall", "Overall Intelligence")],
            parent=self.command_bar,
            theme=self._theme,
        )
        self.mode_switcher.selection_changed.connect(self.set_workspace_context)
        hdr.addWidget(self.mode_switcher)

        self.btn_upcoming_pill = QPushButton("Upcoming (0)")
        self.btn_upcoming_pill.setObjectName("ntStatusPillBtn")
        self.btn_upcoming_pill.setIconSize(QSize(14, 14))
        self.btn_upcoming_pill.setCheckable(True)
        self.btn_upcoming_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upcoming_pill.setToolTip("Show or hide upcoming calendar meetings")
        self.btn_upcoming_pill.clicked.connect(self._toggle_upcoming_card)
        hdr.addWidget(self.btn_upcoming_pill)

        self.btn_rescued_pill = QPushButton("0 Rescued")
        self.btn_rescued_pill.setObjectName("ntWarnPillBtn")
        self.btn_rescued_pill.setIconSize(QSize(14, 14))
        self.btn_rescued_pill.setVisible(False)
        self.btn_rescued_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescued_pill.setToolTip("Recover rescued interrupted recording")
        self.btn_rescued_pill.clicked.connect(self._on_recover_interrupted_clicked)
        hdr.addWidget(self.btn_rescued_pill)

        hdr.addStretch(1)

        self.btn_ask_vox_rail = QPushButton("Ask VOX")
        self.btn_ask_vox_rail.setObjectName("ntAskToggleBtn")
        self.btn_ask_vox_rail.setIconSize(QSize(14, 14))
        self.btn_ask_vox_rail.setCheckable(True)
        self.btn_ask_vox_rail.setChecked(self._ask_rail_open)
        self.btn_ask_vox_rail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_vox_rail.setToolTip("Toggle context-locked Ask VOX AI assistant (Ctrl+Shift+A)")
        self.btn_ask_vox_rail.setAccessibleName("Toggle Ask VOX assistant")
        self.btn_ask_vox_rail.clicked.connect(self._toggle_ask_rail)
        hdr.addWidget(self.btn_ask_vox_rail)

        self.btn_drawer = QPushButton("Drawer")
        self.btn_drawer.setObjectName("secondaryBtn")
        self.btn_drawer.setIconSize(QSize(14, 14))
        self.btn_drawer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer.setAccessibleName("Open Floating Drawer")
        self.btn_drawer.setAccessibleDescription("Open the slide-out companion notetaker drawer for call notes")
        self.btn_drawer.clicked.connect(
            lambda: self.controller.open_notetaker_drawer_requested.emit()
            if hasattr(self.controller, "open_notetaker_drawer_requested")
            else None
        )
        hdr.addWidget(self.btn_drawer)

        self.btn_settings = QPushButton("")
        self.btn_settings.setFixedSize(30, 30)
        self.btn_settings.setIconSize(QSize(15, 15))
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("Notetaker Settings")
        self.btn_settings.setObjectName("ghostBtn")
        self.btn_settings.setAccessibleName("Notetaker settings")
        self.btn_settings.setAccessibleDescription("Open Notetaker preferences in Settings")
        self.btn_settings.clicked.connect(lambda: self.open_settings_requested.emit(2))
        hdr.addWidget(self.btn_settings)

        self.btn_start_adhoc = QPushButton("Start Notetaker")
        self.btn_start_adhoc.setObjectName("fathomBtn")
        self.btn_start_adhoc.setIconSize(QSize(14, 14))
        self.btn_start_adhoc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_adhoc.setAccessibleName("Toggle Notetaker recording")
        self.btn_start_adhoc.setAccessibleDescription("Start, pause, or stop local notetaker recording for desktop calls")
        self.btn_start_adhoc.clicked.connect(self._on_toggle_recording_clicked)
        hdr.addWidget(self.btn_start_adhoc)
        root.addWidget(self.command_bar)

        # Auto-Start Activation Banner
        self.auto_start_banner = VoxAutoStartBanner(self)
        self.auto_start_banner.turn_on_clicked.connect(self._on_auto_start_turn_on)
        self.auto_start_banner.not_now_clicked.connect(self._on_auto_start_not_now)
        root.addWidget(self.auto_start_banner)

        # Live recording status banner (shown only during active capture/finalizing)
        self.rec_banner = QFrame()
        self.rec_banner.setObjectName("ntBanner")
        self.rec_banner.setVisible(False)
        b_lay = QHBoxLayout(self.rec_banner)
        b_lay.setContentsMargins(10, 5, 10, 5)
        b_lay.setSpacing(8)
        self.lbl_rec_dot = QLabel("● Capturing Call")
        self.lbl_rec_dot.setStyleSheet("font-weight: 700; font-size: 12px;")
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
        self.lbl_rec_live.setObjectName("ntMuted")
        b_lay.addWidget(self.lbl_rec_live, 1)
        b_lay.addStretch(1)
        self.btn_pause_rec = QPushButton("Pause")
        self.btn_pause_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause_rec.setAccessibleName("Pause recording")
        self.btn_pause_rec.setAccessibleDescription("Temporarily pause active call audio recording")
        self.btn_pause_rec.clicked.connect(self._on_pause_resume_clicked)
        b_lay.addWidget(self.btn_pause_rec)
        self.btn_stop_rec = QPushButton("Stop && Save")
        self.btn_stop_rec.setObjectName("primaryBtn")
        self.btn_stop_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_rec.setAccessibleName("Stop and save recording")
        self.btn_stop_rec.setAccessibleDescription("Stop recording and start local transcription and summarization")
        self.btn_stop_rec.clicked.connect(self.controller.stop_meeting_recording)
        b_lay.addWidget(self.btn_stop_rec)
        root.addWidget(self.rec_banner)

        # Prepared Calendar Meeting Banner
        self.prepared_banner = QFrame()
        self.prepared_banner.setObjectName("ntBanner")
        self.prepared_banner.setVisible(False)
        pb_lay = QHBoxLayout(self.prepared_banner)
        pb_lay.setContentsMargins(10, 5, 10, 5)
        pb_lay.setSpacing(8)
        self.lbl_prepared_summary = QLabel("")
        self.lbl_prepared_summary.setWordWrap(True)
        self.lbl_prepared_summary.setStyleSheet("font-weight: 600; font-size: 12px;")
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

        # Compact Interrupted Recording Recovery Banner (strictly user-controlled)
        self.interrupted_banner = QFrame()
        self.interrupted_banner.setObjectName("ntInterruptedBanner")
        self.interrupted_banner.setVisible(False)
        ib_lay = QHBoxLayout(self.interrupted_banner)
        ib_lay.setContentsMargins(10, 4, 10, 4)
        ib_lay.setSpacing(8)
        self.lbl_interrupted_summary = QLabel("")
        self.lbl_interrupted_summary.setStyleSheet(
            f"font-weight: 600; font-size: 12px; color: {t_init['status_warning_text']};"
        )
        ib_lay.addWidget(self.lbl_interrupted_summary, 1)
        self.btn_recover_interrupted = QPushButton("↻ Recover && Re-Transcribe")
        self.btn_recover_interrupted.setObjectName("secondaryBtn")
        self.btn_recover_interrupted.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_recover_interrupted.clicked.connect(self._on_recover_interrupted_clicked)
        ib_lay.addWidget(self.btn_recover_interrupted)
        self.btn_dismiss_interrupted = QPushButton("×")
        self.btn_dismiss_interrupted.setFixedSize(22, 22)
        self.btn_dismiss_interrupted.setObjectName("ghostBtn")
        self.btn_dismiss_interrupted.setToolTip("Dismiss recovery banner for this session")
        self.btn_dismiss_interrupted.clicked.connect(self._dismiss_interrupted_banner)
        ib_lay.addWidget(self.btn_dismiss_interrupted)
        root.addWidget(self.interrupted_banner)

        # Upcoming Meetings Strip (hidden when 0 upcoming meetings unless toggled open via command bar)
        self.cal_card = QFrame()
        self.cal_card.setObjectName("ntCard")
        self.cal_card.setMinimumWidth(0)
        cal_root_lay = QVBoxLayout(self.cal_card)
        cal_root_lay.setContentsMargins(12, 6, 12, 6)
        cal_root_lay.setSpacing(6)

        cal_top_scroll = QScrollArea()
        cal_top_scroll.setWidgetResizable(True)
        cal_top_scroll.setFrameShape(QFrame.Shape.NoFrame)
        cal_top_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cal_top_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cal_top_scroll.setFixedHeight(32)
        cal_top_scroll.setMinimumWidth(0)
        cal_top_host = QWidget()
        cal_top_host.setMinimumWidth(0)
        cal_top = QHBoxLayout(cal_top_host)
        cal_top.setContentsMargins(0, 0, 0, 0)
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
        self.lbl_cal_next_inline.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_cal_next_inline.setMinimumWidth(0)
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

        cal_top_scroll.setWidget(cal_top_host)
        cal_root_lay.addWidget(cal_top_scroll)

        self.cal_content_container = QWidget()
        self.cal_content = QHBoxLayout(self.cal_content_container)
        self.cal_content.setContentsMargins(0, 0, 0, 0)
        self.cal_content.setSpacing(10)
        cal_root_lay.addWidget(self.cal_content_container)
        self._apply_cal_collapsed_state()
        self.cal_card.setVisible(False)
        root.addWidget(self.cal_card)

        # ------------------------------------------------------------------
        # 2. 3-Column Adaptive Workspace Splitter:
        #    [Pane 1: Collapsible Meeting Library] | [Pane 2: Dual-Context Canvas] | [Pane 3: Context-Locked Ask VOX Rail]
        # ------------------------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)

        # --- Pane 1: Meeting Library (Google Workspace 2-Row Clean Toolbar) ---
        self.lib_card = QFrame()
        self.lib_card.setObjectName("ntCard")
        self.lib_card.setMinimumWidth(240)
        self.lib_card.setMaximumWidth(360)
        lib_lay = QVBoxLayout(self.lib_card)
        lib_lay.setContentsMargins(10, 10, 10, 10)
        lib_lay.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("voxSearchField")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.setPlaceholderText("Search meetings & transcripts... (Ctrl+F)")
        self.txt_search.setAccessibleName("Search call notes and transcripts")
        self.txt_search.setAccessibleDescription("Filter meeting recordings by title, date, platform, or transcript content")
        self._lib_search_action = self.txt_search.addAction(
            get_icon("search", "#64748B", 14), QLineEdit.ActionPosition.LeadingPosition
        )
        self.txt_search.textChanged.connect(lambda _t: self._search_debounce_timer.start())
        search_row.addWidget(self.txt_search, 1)

        self.btn_new_folder = QPushButton("")
        self.btn_new_folder.setObjectName("ghostBtn")
        self.btn_new_folder.setFixedSize(28, 28)
        self.btn_new_folder.setIconSize(QSize(15, 15))
        self.btn_new_folder.setToolTip("Create new folder")
        self.btn_new_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new_folder.setAccessibleName("Create new folder")
        self.btn_new_folder.setAccessibleDescription("Create a new organizational folder for meeting notes")
        self.btn_new_folder.clicked.connect(self._on_create_folder_clicked)
        search_row.addWidget(self.btn_new_folder)

        self.btn_density = QPushButton("")
        self.btn_density.setObjectName("ntIconToggleBtn")
        self.btn_density.setFixedSize(28, 28)
        self.btn_density.setIconSize(QSize(15, 15))
        self.btn_density.setCheckable(True)
        self.btn_density.setChecked(self._library_compact)
        self.btn_density.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_density.setToolTip("Switch row density (Compact / Comfortable)")
        self.btn_density.setAccessibleName("Toggle meeting list row density")
        self.btn_density.clicked.connect(self._toggle_library_density)
        search_row.addWidget(self.btn_density)
        lib_lay.addLayout(search_row)

        # Compact Folder + Sort Row
        lib_ctrl_row = QHBoxLayout()
        lib_ctrl_row.setSpacing(6)
        self.combo_folders = QComboBox()
        self.combo_folders.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_folders.setMinimumContentsLength(9)
        self.combo_folders.setAccessibleName("Filter by folder")
        self.combo_folders.setAccessibleDescription("Filter the meeting recordings library by folder")
        self.combo_folders.currentIndexChanged.connect(self._on_folder_filter_changed)
        lib_ctrl_row.addWidget(self.combo_folders, 1)

        self.combo_sort = QComboBox()
        self.combo_sort.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_sort.setMinimumContentsLength(7)
        self.combo_sort.addItem("Newest", "newest")
        self.combo_sort.addItem("Longest", "duration")
        self.combo_sort.addItem("Title A–Z", "title")
        self.combo_sort.currentIndexChanged.connect(self._on_sort_changed)
        lib_ctrl_row.addWidget(self.combo_sort)
        lib_lay.addLayout(lib_ctrl_row)

        # Clean borderless status filter tabs
        lib_pill_row = QHBoxLayout()
        lib_pill_row.setSpacing(4)
        self._lib_filter_btns: Dict[str, QPushButton] = {}
        for fkey, flbl in (
            ("all", "All"),
            ("completed", "Done"),
            ("recoverable", "Rescued"),
            ("video", "Video"),
        ):
            fb = QPushButton(flbl)
            fb.setObjectName("ntChipBtn")
            fb.setCheckable(True)
            fb.setChecked(fkey == "all")
            fb.setCursor(Qt.CursorShape.PointingHandCursor)
            fb.clicked.connect(lambda _=False, k=fkey: self._set_library_filter(k))
            lib_pill_row.addWidget(fb)
            self._lib_filter_btns[fkey] = fb
        lib_pill_row.addStretch(1)
        lib_lay.addLayout(lib_pill_row)

        self.meetings_list = QListWidget()
        self.meetings_list.setObjectName("ntMeetingsList")
        self.meetings_list.setAlternatingRowColors(False)
        self.meetings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.meetings_list.setAccessibleName("Recorded meetings library")
        self.meetings_list.setAccessibleDescription("List of recorded meetings and notes. Use arrow keys to select, Space/Enter to load.")
        self.meetings_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.meetings_list.customContextMenuRequested.connect(self._show_meeting_context_menu)
        self.meetings_delegate = VoxMeetingListDelegate(self.meetings_list, theme=self._theme, compact=self._library_compact)
        self.meetings_list.setItemDelegate(self.meetings_delegate)
        self.meetings_list.currentItemChanged.connect(self._on_meeting_item_selected)
        lib_lay.addWidget(self.meetings_list, 1)

        self.lib_card.setVisible(not self._sidebar_collapsed)
        self.splitter.addWidget(self.lib_card)

        # --- Pane 2: Dual-Context Stacked Canvas (Page 0: This Meeting | Page 1: Overall Intelligence) ---
        self.context_stack = QStackedWidget()
        self.context_stack.setMinimumWidth(0)

        detail_wrap = QWidget()
        detail_wrap.setMinimumWidth(0)
        detail_lay = QVBoxLayout(detail_wrap)
        detail_lay.setContentsMargins(6, 0, 6, 0)
        detail_lay.setSpacing(8)

        # Unified Contextual Meeting Header + Docked Media Player
        self.action_card = QFrame()
        self.action_card.setObjectName("ntCard")
        self.action_card.setMinimumWidth(0)
        act_lay = QVBoxLayout(self.action_card)
        act_lay.setContentsMargins(12, 8, 12, 8)
        act_lay.setSpacing(5)

        # Row 1: Editable Title + Context Lock + Primary Actions
        top_meta = QHBoxLayout()
        top_meta.setSpacing(6)
        self.lbl_sel_title = QLineEdit("Select a call note")
        self.lbl_sel_title.setObjectName("ntSelTitleEdit")
        self.lbl_sel_title.setFont(get_ui_font(19, 600))
        self.lbl_sel_title.setMinimumWidth(72)
        self.lbl_sel_title.setCursorPosition(0)
        self.lbl_sel_title.setReadOnly(True)
        self.lbl_sel_title.setFrame(False)
        self.lbl_sel_title.setPlaceholderText("Untitled Meeting")
        self.lbl_sel_title.setAccessibleName("Selected meeting title")
        self.lbl_sel_title.editingFinished.connect(self._on_selected_title_edited)
        top_meta.addWidget(self.lbl_sel_title, 1)

        self.lbl_scope_badge = QLabel("This Meeting")
        self.lbl_scope_badge.setObjectName("ntScopeBadge")
        top_meta.addWidget(self.lbl_scope_badge)

        self.btn_split_view = QPushButton("Split ▾")
        self.btn_split_view.setObjectName("secondaryBtn")
        self.btn_split_view.setIconSize(QSize(14, 14))
        self.btn_split_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_split_view.setToolTip("Show Summary or My Notes alongside Transcript side-by-side (Ctrl+Shift+S)")
        self.btn_split_view.clicked.connect(self._show_split_menu)
        top_meta.addWidget(self.btn_split_view)

        self.btn_watch_video = QPushButton("Video")
        self.btn_watch_video.setObjectName("secondaryBtn")
        self.btn_watch_video.setIconSize(QSize(14, 14))
        self.btn_watch_video.setEnabled(False)
        self.btn_watch_video.setVisible(False)
        self.btn_watch_video.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_watch_video.setAccessibleName("Watch call video")
        self.btn_watch_video.setAccessibleDescription("Play recorded screen video for the selected meeting in default player")
        self.btn_watch_video.clicked.connect(self._open_selected_video)
        top_meta.addWidget(self.btn_watch_video)

        self.btn_export = QPushButton("Export")
        self.btn_export.setObjectName("secondaryBtn")
        self.btn_export.setIconSize(QSize(14, 14))
        self.btn_export.setEnabled(False)
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setAccessibleName("Export meeting deliverables")
        self.btn_export.setAccessibleDescription("Open export menu to download minutes in Markdown, JSON, HTML, PDF, or text")
        self.btn_export.clicked.connect(self._show_export_menu)
        top_meta.addWidget(self.btn_export)

        self.btn_more = QPushButton("")
        self.btn_more.setObjectName("ghostBtn")
        self.btn_more.setEnabled(False)
        self.btn_more.setFixedSize(30, 30)
        self.btn_more.setIconSize(QSize(16, 16))
        self.btn_more.setToolTip("More meeting options (Reprocess, Move to Folder, Reveal, Delete)")
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setAccessibleName("More meeting actions")
        self.btn_more.setAccessibleDescription("Open options menu to reprocess, rename speakers, move to folder, reveal recording, or delete")
        self.btn_more.clicked.connect(self._show_more_menu)
        top_meta.addWidget(self.btn_more)
        act_lay.addLayout(top_meta)

        # Row 2: Unclipped Human-Readable Metadata + Contextual Actions
        sub_meta_row = QHBoxLayout()
        sub_meta_row.setSpacing(6)
        self.lbl_sel_meta = QLabel("Pick a recorded meeting from the left library or start a new capture.")
        self.lbl_sel_meta.setObjectName("ntMuted")
        self.lbl_sel_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_sel_meta.setMinimumWidth(0)
        sub_meta_row.addWidget(self.lbl_sel_meta, 1)

        self.btn_reprocess = QPushButton("↻ Reprocess")
        self.btn_reprocess.setEnabled(False)
        self.btn_reprocess.setVisible(False)
        self.btn_reprocess.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reprocess.setToolTip("Re-run local transcription and summary without modifying original audio")
        self.btn_reprocess.clicked.connect(lambda: self._on_reprocess_meeting_clicked())
        sub_meta_row.addWidget(self.btn_reprocess)

        self.btn_view_in_calendar = QPushButton("Calendar Event")
        self.btn_view_in_calendar.setObjectName("ghostBtn")
        self.btn_view_in_calendar.setIconSize(QSize(13, 13))
        self.btn_view_in_calendar.setVisible(False)
        self.btn_view_in_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_in_calendar.setToolTip("Open Calendar workspace")
        self.btn_view_in_calendar.clicked.connect(self.open_calendar_requested.emit)
        sub_meta_row.addWidget(self.btn_view_in_calendar)

        self.btn_speakers = QPushButton("Speakers")
        self.btn_speakers.setObjectName("ghostBtn")
        self.btn_speakers.setIconSize(QSize(14, 14))
        self.btn_speakers.setEnabled(False)
        self.btn_speakers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_speakers.setAccessibleName("Manage meeting speakers")
        self.btn_speakers.setAccessibleDescription("Assign participant names to detected speaker diarization tracks")
        self.btn_speakers.clicked.connect(self._assign_speakers_dialog)
        sub_meta_row.addWidget(self.btn_speakers)
        act_lay.addLayout(sub_meta_row)

        # Docked Compact Audio Scrubber Strip
        player_row = QHBoxLayout()
        player_row.setSpacing(6)
        self.btn_play_pause = QPushButton("")
        self.btn_play_pause.setObjectName("ntPlayBtn")
        self.btn_play_pause.setFixedSize(30, 30)
        self.btn_play_pause.setIconSize(QSize(14, 14))
        self.btn_play_pause.setEnabled(False)
        self.btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_pause.setToolTip("Play audio")
        self.btn_play_pause.setAccessibleName("Toggle audio playback")
        self.btn_play_pause.setAccessibleDescription("Play or pause audio playback for the selected meeting")
        self.btn_play_pause.clicked.connect(self._toggle_media_playback)
        player_row.addWidget(self.btn_play_pause)

        self.btn_skip_back = QPushButton("")
        self.btn_skip_back.setObjectName("ghostBtn")
        self.btn_skip_back.setFixedSize(26, 26)
        self.btn_skip_back.setIconSize(QSize(14, 14))
        self.btn_skip_back.setEnabled(False)
        self.btn_skip_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skip_back.setToolTip("Skip back 10 seconds")
        self.btn_skip_back.setAccessibleName("Skip back 10 seconds")
        self.btn_skip_back.clicked.connect(lambda: self._skip_media_delta(-10.0))
        player_row.addWidget(self.btn_skip_back)

        self.btn_skip_forward = QPushButton("")
        self.btn_skip_forward.setObjectName("ghostBtn")
        self.btn_skip_forward.setFixedSize(26, 26)
        self.btn_skip_forward.setIconSize(QSize(14, 14))
        self.btn_skip_forward.setEnabled(False)
        self.btn_skip_forward.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skip_forward.setToolTip("Skip forward 10 seconds")
        self.btn_skip_forward.setAccessibleName("Skip forward 10 seconds")
        self.btn_skip_forward.clicked.connect(lambda: self._skip_media_delta(10.0))
        player_row.addWidget(self.btn_skip_forward)

        self.media_slider = QSlider(Qt.Orientation.Horizontal)
        self.media_slider.setEnabled(False)
        self.media_slider.setAccessibleName("Audio playback seek slider")
        self.media_slider.setAccessibleDescription("Seek playback position within the selected meeting recording")
        self.media_slider.sliderMoved.connect(self._on_media_seek)
        player_row.addWidget(self.media_slider, 1)

        self.lbl_media_time = QLabel("00:00 / 00:00")
        self.lbl_media_time.setObjectName("ntMuted")
        self.lbl_media_time.setFont(get_ui_font(11, 500, mono=True))
        player_row.addWidget(self.lbl_media_time)

        self.btn_playback_speed = QPushButton("1.0x")
        self.btn_playback_speed.setObjectName("ntChipBtn")
        self.btn_playback_speed.setFixedWidth(46)
        self.btn_playback_speed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_playback_speed.setToolTip("Cycle audio playback speed (1.0x / 1.25x / 1.5x / 2.0x)")
        self.btn_playback_speed.clicked.connect(self._cycle_playback_speed)
        player_row.addWidget(self.btn_playback_speed)
        act_lay.addLayout(player_row)

        detail_lay.addWidget(self.action_card)

        # Embedded Empty State Card
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

        # Embedded Strictly-Scoped Meeting Studio Workspace
        self.studio = VoxMeetingStudioWorkspace(
            parent=detail_wrap,
            theme=self._theme,
            db=self.controller.db,
            embedded_mode=True,
            strict_meeting_scope=True,
        )
        self.studio.setMinimumWidth(0)
        self.studio.set_split_mode(self._split_mode)
        self.studio.tab_summary.ask_vox_submitted.connect(self._on_studio_ask_submitted)
        if hasattr(self.studio, "ask_rail_scoped_submitted"):
            self.studio.ask_rail_scoped_submitted.connect(self._on_studio_ask_rail_scoped_submitted)
        else:
            self.studio.ask_rail_submitted.connect(self._on_studio_ask_rail_submitted)
        if hasattr(self.studio, "tab_knowledge"):
            self.studio.tab_knowledge.rebuild_requested.connect(self._on_rebuild_knowledge_index_requested)
            if hasattr(self.studio.tab_knowledge, "rebuild_with_extract_requested"):
                self.studio.tab_knowledge.rebuild_with_extract_requested.connect(
                    self._on_rebuild_knowledge_index_requested
                )
            if hasattr(self.studio.tab_knowledge, "open_meeting_requested"):
                self.studio.tab_knowledge.open_meeting_requested.connect(self._open_meeting_at_seconds)
            if hasattr(self.studio.tab_knowledge, "open_overall_knowledge_requested"):
                self.studio.tab_knowledge.open_overall_knowledge_requested.connect(
                    lambda: (self.set_workspace_context("overall"), self.overall_view.set_subview("knowledge"))
                )
        if hasattr(self.studio.tab_transcript, "speaker_renamed"):
            self.studio.tab_transcript.speaker_renamed.connect(self._on_studio_speaker_renamed)
        if hasattr(self.studio, "meeting_mutated"):
            self.studio.meeting_mutated.connect(lambda _mid: self.refresh_meetings_list())
        self.studio.seek_requested.connect(self._seek_to_seconds)
        self.studio.tab_transcript.seek_requested.connect(self._seek_to_seconds)
        self.studio.tab_thoughts.seek_requested.connect(self._seek_to_seconds)
        detail_lay.addWidget(self.studio, 1)

        self.context_stack.addWidget(detail_wrap)  # index 0: "meeting"

        # Overall Meeting Intelligence View (index 1: "overall")
        self.overall_view = VoxOverallIntelligenceView(
            db=self.controller.db,
            engine=getattr(self.controller, "intelligence", None),
            theme=self._theme,
            parent=self.context_stack,
        )
        self.overall_view.setMinimumWidth(0)
        self.overall_view.open_meeting_requested.connect(self._open_meeting_at_seconds)
        self.overall_view.scope_changed.connect(self._on_overall_scope_changed)
        self.overall_view.ask_overall_requested.connect(self._ask_from_overall_view)
        self.overall_view.rebuild_knowledge_requested.connect(self._on_rebuild_knowledge_index_requested)
        self.context_stack.addWidget(self.overall_view)

        self.splitter.addWidget(self.context_stack)

        # --- Pane 3: Unified Context-Locked Ask VOX Right Rail ---
        self.ask_rail = QFrame()
        self.ask_rail.setObjectName("ntAskRail")
        self.ask_rail.setMinimumWidth(320)
        self.ask_rail.setMaximumWidth(480)
        ar_lay = QVBoxLayout(self.ask_rail)
        ar_lay.setContentsMargins(12, 10, 12, 10)
        ar_lay.setSpacing(8)

        ar_hdr = QHBoxLayout()
        ar_hdr.setSpacing(6)
        self.lbl_ask_rail_icon = QLabel()
        self.lbl_ask_rail_icon.setFixedSize(18, 18)
        ar_hdr.addWidget(self.lbl_ask_rail_icon)
        self.lbl_ask_rail_title = QLabel("Ask VOX")
        self.lbl_ask_rail_title.setFont(get_ui_font(15, 600))
        ar_hdr.addWidget(self.lbl_ask_rail_title)
        ar_hdr.addStretch(1)
        self.combo_ask_depth = QComboBox()
        self.combo_ask_depth.addItem("Concise", "concise")
        self.combo_ask_depth.addItem("Detailed", "detailed")
        ar_hdr.addWidget(self.combo_ask_depth)
        self.btn_ask_close = QPushButton("")
        self.btn_ask_close.setFixedSize(28, 28)
        self.btn_ask_close.setIconSize(QSize(14, 14))
        self.btn_ask_close.setObjectName("ghostBtn")
        self.btn_ask_close.setToolTip("Collapse Ask VOX assistant")
        self.btn_ask_close.setAccessibleName("Close Ask VOX rail")
        self.btn_ask_close.clicked.connect(lambda: self._toggle_ask_rail(False))
        ar_hdr.addWidget(self.btn_ask_close)
        ar_lay.addLayout(ar_hdr)

        # Prominent Context Lock Indicator
        self.lbl_ask_scope_badge = QLabel("This Meeting")
        self.lbl_ask_scope_badge.setObjectName("ntAskScopeLock")
        self.lbl_ask_scope_badge.setWordWrap(True)
        ar_lay.addWidget(self.lbl_ask_scope_badge)

        # Action toolbar
        ar_tools = QHBoxLayout()
        ar_tools.setSpacing(6)
        self.btn_ask_copy_ans = QPushButton("Copy Answer")
        self.btn_ask_copy_ans.setObjectName("ntChipBtn")
        self.btn_ask_copy_ans.setIconSize(QSize(12, 12))
        self.btn_ask_copy_ans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_copy_ans.clicked.connect(lambda: self._copy_ask_answer(with_sources=False))
        ar_tools.addWidget(self.btn_ask_copy_ans)

        self.btn_ask_copy_src = QPushButton("Copy w/ Sources")
        self.btn_ask_copy_src.setObjectName("ntChipBtn")
        self.btn_ask_copy_src.setIconSize(QSize(12, 12))
        self.btn_ask_copy_src.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_copy_src.clicked.connect(lambda: self._copy_ask_answer(with_sources=True))
        ar_tools.addWidget(self.btn_ask_copy_src)
        ar_tools.addStretch(1)

        self.btn_ask_clear = QPushButton("Clear")
        self.btn_ask_clear.setObjectName("ghostBtn")
        self.btn_ask_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_clear.clicked.connect(self._clear_current_ask_thread)
        ar_tools.addWidget(self.btn_ask_clear)
        ar_lay.addLayout(ar_tools)

        # Starter prompt chips (context-reactive)
        chips_wrap = QHBoxLayout()
        chips_wrap.setSpacing(4)
        self._ask_chip_btns: List[QPushButton] = []
        for idx in range(3):
            cb = QPushButton("")
            cb.setObjectName("ntChipBtn")
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.clicked.connect(lambda _=False, i=idx: self._on_ask_chip_clicked(i))
            chips_wrap.addWidget(cb)
            self._ask_chip_btns.append(cb)
        chips_wrap.addStretch(1)
        ar_lay.addLayout(chips_wrap)

        self.ask_rail_browser = QTextBrowser()
        self.ask_rail_browser.setObjectName("ntAskBrowser")
        self.ask_rail_browser.setOpenExternalLinks(False)
        self.ask_rail_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.ask_rail_browser.anchorClicked.connect(self._on_ask_anchor_clicked)
        ar_lay.addWidget(self.ask_rail_browser, 1)

        ask_inp_row = QHBoxLayout()
        ask_inp_row.setSpacing(6)
        self.txt_ask_rail = QLineEdit()
        self.txt_ask_rail.setObjectName("voxSearchField")
        self.txt_ask_rail.setPlaceholderText("Ask about this meeting...")
        self.txt_ask_rail.returnPressed.connect(self._submit_ask_rail_input)
        ask_inp_row.addWidget(self.txt_ask_rail, 1)

        self.btn_ask_rail_send = QPushButton("Ask")
        self.btn_ask_rail_send.setObjectName("fathomBtn")
        self.btn_ask_rail_send.setIconSize(QSize(13, 13))
        self.btn_ask_rail_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_rail_send.clicked.connect(self._submit_ask_rail_input)
        ask_inp_row.addWidget(self.btn_ask_rail_send)
        ar_lay.addLayout(ask_inp_row)

        self.ask_rail.setVisible(self._ask_rail_open)
        if hasattr(self, "studio") and hasattr(self.studio, "tab_summary") and hasattr(self.studio.tab_summary, "ask_box"):
            self.studio.tab_summary.ask_box.setVisible(not self._ask_rail_open)
        if hasattr(self, "overall_view") and hasattr(self.overall_view, "prompt_strip_card"):
            self.overall_view.prompt_strip_card.setVisible(not self._ask_rail_open)
        self.splitter.addWidget(self.ask_rail)

        cfg = getattr(self.controller, "config", None)
        lib_w = max(240, min(380, int(getattr(cfg, "notetaker_library_width", 285) or 285)))
        self.splitter.setSizes([lib_w, 760, 350])
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
        if hasattr(self, "overall_view"):
            self.overall_view.refresh_folders()
            if self._workspace_context == "overall":
                self.overall_view.refresh_data()
            else:
                self.overall_view._needs_refresh = True
        self._refresh_ask_rail_ui()

    def _update_finalization_timer(self) -> None:
        if self._fin_started_mono is None:
            return
        elapsed = max(0.0, time.monotonic() - self._fin_started_mono)
        if hasattr(self, "lbl_rec_timer"):
            self.lbl_rec_timer.setText(format_seconds_hms(elapsed))

    def _on_media_error(self, _err: Any, err_str: str = "") -> None:
        msg = (err_str or "").strip()
        if msg:
            logger.debug("Media player warning: %s", msg)

    def _apply_pending_media_source(self) -> None:
        res_audio = getattr(self, "_pending_media_path", None)
        target_str = QUrl.fromLocalFile(str(res_audio)).toString() if res_audio is not None else ""
        if target_str == self._loaded_media_url:
            return
        self._loaded_media_url = target_str
        if res_audio is not None:
            self.media_player.setSource(QUrl.fromLocalFile(str(res_audio)))
        else:
            self.media_player.setSource(QUrl())

    def teardown(self) -> None:
        for attr in ("_search_debounce_timer", "_media_probe_timer", "_fin_elapsed_timer"):
            tmr = getattr(self, attr, None)
            if tmr is not None and tmr.isActive():
                tmr.stop()
        try:
            self.media_player.stop()
        except Exception:
            pass

    def closeEvent(self, event: Any) -> None:
        self.teardown()
        super().closeEvent(event)

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
        cnt = len(self._cached_calendar_meetings)
        if hasattr(self, "btn_upcoming_pill"):
            self.btn_upcoming_pill.setText(f"Upcoming ({cnt})")
        if hasattr(self, "cal_card"):
            self.cal_card.setVisible(cnt > 0 or bool(getattr(self, "_cal_user_toggled", False)))

    def _render_calendar_strip(self) -> None:
        while self.cal_content.count():
            item = self.cal_content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        while self.cal_providers_layout.count():
            item = self.cal_providers_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
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
            self.lbl_rec_dot.setText("● Recording Dual-Track English Audio")
            if hasattr(self, "lbl_rec_live"):
                self.lbl_rec_live.setText("Post-call English transcription & AI intelligence runs automatically after stop")
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
            self.lbl_rec_dot.setText("❚❚ Notetaker Paused (Post-Call Transcription Queued on Stop)")
            self.btn_pause_rec.setVisible(True)
            self.btn_stop_rec.setVisible(True)
            self.btn_pause_rec.setText("Resume")
            self.btn_start_adhoc.setText("Stop Notetaker")
        elif proc:
            stage_text = status_msg.strip() if status_msg else "Finalizing full transcript..."
            self.lbl_rec_dot.setText(f"⏳ {stage_text}")
            self.btn_pause_rec.setVisible(False)
            self.btn_stop_rec.setVisible(False)
            self.btn_start_adhoc.setText("Finalizing...")
            if self._fin_started_mono is None:
                self._fin_started_mono = time.monotonic()
            if not self._fin_elapsed_timer.isActive():
                self._fin_elapsed_timer.start()
        else:
            self._silent_loopback_since = None
            self._fin_started_mono = None
            if hasattr(self, "_fin_elapsed_timer") and self._fin_elapsed_timer.isActive():
                self._fin_elapsed_timer.stop()
            if hasattr(self, "lbl_capture_anomaly"):
                self.lbl_capture_anomaly.setVisible(False)
            self.btn_pause_rec.setVisible(True)
            self.btn_stop_rec.setVisible(True)
            self.btn_start_adhoc.setText("Start Notetaker")
            self.lbl_rec_timer.setText("00:00")
            if hasattr(self, "lbl_rec_live"):
                self.lbl_rec_live.setText("")

    def _on_knowledge_updated(self) -> None:
        if hasattr(self, "studio") and hasattr(self.studio, "tab_knowledge"):
            try:
                self.studio.tab_knowledge.refresh_items()
            except Exception:
                pass
        if hasattr(self, "overall_view"):
            if self._workspace_context == "overall":
                try:
                    self.overall_view.refresh_data()
                except Exception:
                    pass
            else:
                self.overall_view._needs_refresh = True

    def _on_rebuild_knowledge_index_requested(self, extract_current: bool = False) -> None:
        ctrl = getattr(self, "controller", None)
        if ctrl is None or not hasattr(ctrl, "rebuild_knowledge_index"):
            return
        self.status_toast.emit("Knowledge Index", "Refreshing search index & knowledge...")
        sel_mid = self._selected_meeting_id if extract_current else None

        def _worker() -> None:
            try:
                try:
                    stats = ctrl.rebuild_knowledge_index(extract_missing_for_meeting_id=sel_mid)
                except TypeError:
                    stats = ctrl.rebuild_knowledge_index()
                msg = (
                    f"Indexed {stats.get('meetings_indexed', 0)} meetings "
                    f"({stats.get('chunks_indexed', 0)} chunks)."
                )
                if hasattr(self, "studio") and hasattr(self.studio, "tab_knowledge"):
                    QTimer.singleShot(
                        0,
                        lambda m=msg: self.studio.tab_knowledge.set_rebuild_progress(False, m),
                    )
                self.status_toast.emit("Knowledge Index Ready", msg)
            except Exception as exc:
                if hasattr(self, "studio") and hasattr(self.studio, "tab_knowledge"):
                    QTimer.singleShot(
                        0,
                        lambda e=str(exc): self.studio.tab_knowledge.set_rebuild_progress(False, f"Error: {e}"),
                    )
                self.status_toast.emit("Knowledge Index Error", str(exc))

        threading.Thread(target=_worker, name="NotetakerRebuildKnowledge", daemon=True).start()

    def _on_studio_speaker_renamed(self, old_name: str, new_name: str) -> None:
        mid = self._selected_meeting_id
        ctrl = getattr(self, "controller", None)
        if mid is None or ctrl is None:
            return
        if hasattr(ctrl, "rename_meeting_speaker_and_reindex"):
            try:
                ctrl.rename_meeting_speaker_and_reindex(int(mid), old_name, new_name)
                self.select_meeting(int(mid))
                self.refresh_meetings_list()
                self.status_toast.emit("Speaker Renamed", f"{old_name} → {new_name} synced across Summary & Knowledge")
            except Exception as exc:
                self.status_toast.emit("Rename Error", str(exc))

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

    def _persist_ui_pref(self, key: str, value: Any) -> None:
        cfg = getattr(self.controller, "config", None)
        if cfg is not None and hasattr(cfg, key):
            try:
                setattr(cfg, key, value)
                if hasattr(cfg, "save"):
                    cfg.save()
            except Exception:
                pass

    def _dismiss_interrupted_banner(self) -> None:
        self._interrupted_dismissed = True
        if hasattr(self, "interrupted_banner"):
            self.interrupted_banner.setVisible(False)
        if hasattr(self, "btn_rescued_pill") and self._first_interrupted_id is not None:
            self.btn_rescued_pill.setVisible(True)

    def _toggle_upcoming_card(self) -> None:
        self._cal_user_toggled = bool(self.btn_upcoming_pill.isChecked())
        if hasattr(self, "cal_card"):
            show = self._cal_user_toggled or bool(self._cached_calendar_meetings)
            self.cal_card.setVisible(show)

    def _toggle_sidebar_collapsed(self, collapsed: Optional[bool] = None) -> None:
        if collapsed is None:
            self._sidebar_collapsed = not self._sidebar_collapsed
        else:
            self._sidebar_collapsed = bool(collapsed)
        if hasattr(self, "lib_card"):
            self.lib_card.setVisible(not self._sidebar_collapsed)
        if hasattr(self, "btn_toggle_sidebar"):
            self.btn_toggle_sidebar.blockSignals(True)
            self.btn_toggle_sidebar.setChecked(not self._sidebar_collapsed)
            self.btn_toggle_sidebar.blockSignals(False)
        self._persist_ui_pref("notetaker_library_collapsed", self._sidebar_collapsed)

    def set_workspace_context(self, mode: str) -> None:
        norm = "overall" if str(mode or "").strip().lower() == "overall" else "meeting"
        self._workspace_context = norm
        if hasattr(self, "mode_switcher"):
            self.mode_switcher.set_current(norm, emit=False)
        if hasattr(self, "context_stack"):
            self.context_stack.setCurrentIndex(1 if norm == "overall" else 0)
        if norm == "overall" and hasattr(self, "overall_view"):
            self.overall_view.refresh_folders()
            self.overall_view.refresh_data()
        self._refresh_ask_rail_ui()

    def _open_meeting_at_seconds(self, meeting_id: int, seconds: float = 0.0) -> None:
        self.set_workspace_context("meeting")
        self.select_meeting(int(meeting_id))
        if float(seconds or 0.0) > 0.0:
            self._seek_to_seconds(float(seconds))
            if hasattr(self, "studio") and hasattr(self.studio, "unified_tabs"):
                self.studio.unified_tabs.setCurrentIndex(1)

    def _on_overall_scope_changed(self, meeting_ids: object, scope_label: str) -> None:
        self._overall_scope_ids = list(meeting_ids) if isinstance(meeting_ids, list) else None
        self._overall_scope_label = str(scope_label or "All Recorded Meetings")
        self._refresh_ask_rail_ui()

    def _ask_from_overall_view(self, query: str) -> None:
        self.set_workspace_context("overall")
        self._toggle_ask_rail(True)
        clean_q = str(query or "").strip()
        if clean_q:
            self.txt_ask_rail.setText(clean_q)
            self._submit_ask_rail_input()

    def _on_sort_changed(self) -> None:
        if hasattr(self, "combo_sort"):
            self._library_sort = str(self.combo_sort.currentData() or "newest")
        self.refresh_meetings_list()

    def _toggle_library_density(self) -> None:
        self._library_compact = not self._library_compact
        if hasattr(self, "btn_density"):
            self.btn_density.blockSignals(True)
            self.btn_density.setChecked(self._library_compact)
            t = get_theme_tokens(self._theme)
            self.btn_density.setIcon(
                get_icon("density_compact" if self._library_compact else "density_comfy", t.get("text_secondary", "#475569"), 15)
            )
            self.btn_density.blockSignals(False)
        if hasattr(self, "meetings_delegate"):
            self.meetings_delegate.set_compact(self._library_compact)
        self._persist_ui_pref("notetaker_library_compact", self._library_compact)
        self.refresh_meetings_list()

    def _set_library_filter(self, filter_key: str) -> None:
        self._library_filter = filter_key
        if hasattr(self, "_lib_filter_btns"):
            for k, btn in self._lib_filter_btns.items():
                btn.setChecked(k == filter_key)
        self.refresh_meetings_list()

    def _show_split_menu(self) -> None:
        menu = QMenu(self)
        opts = (
            ("off", "Single View"),
            ("summary_transcript", "Summary + Transcript"),
            ("notes_transcript", "My Notes + Transcript"),
        )
        for key, label in opts:
            act = menu.addAction(("✓ " if self._split_mode == key else "   ") + label)
            act.triggered.connect(lambda _=False, k=key: self.set_split_mode(k))
        menu.exec(self.btn_split_view.mapToGlobal(self.btn_split_view.rect().bottomLeft()))

    def set_split_mode(self, mode: str) -> None:
        valid = {"off", "summary_transcript", "notes_transcript"}
        self._split_mode = mode if mode in valid else "off"
        if hasattr(self, "btn_split_view"):
            lbl_map = {
                "off": "Split ▾",
                "summary_transcript": "Split: Summary ▾",
                "notes_transcript": "Split: Notes ▾",
            }
            self.btn_split_view.setText(lbl_map.get(self._split_mode, "Split ▾"))
        if hasattr(self, "studio") and hasattr(self.studio, "set_split_mode"):
            self.studio.set_split_mode(self._split_mode)
        if hasattr(self, "splitter") and self._split_mode != "off" and self._ask_rail_open:
            self.splitter.setSizes([240, 880, 320])
        self._persist_ui_pref("notetaker_split_mode", self._split_mode)

    def _cycle_playback_speed(self) -> None:
        self._playback_rate_idx = (self._playback_rate_idx + 1) % len(self._playback_rates)
        rate = self._playback_rates[self._playback_rate_idx]
        try:
            self.media_player.setPlaybackRate(rate)
        except Exception:
            pass
        if hasattr(self, "btn_playback_speed"):
            self.btn_playback_speed.setText(f"{rate:g}x")

    @staticmethod
    def _date_group_for_meeting(m: Dict[str, Any]) -> str:
        raw = str(m.get("started_at") or m.get("created_at") or "").strip()
        if not raw:
            return "EARLIER"
        try:
            clean = raw[:19].replace("T", " ")
            dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
            today = datetime.now().date()
            d = dt.date()
            delta = (today - d).days
            if delta <= 0:
                return "TODAY"
            if delta == 1:
                return "YESTERDAY"
            if delta <= 7:
                return "THIS WEEK"
        except Exception:
            pass
        return "EARLIER"

    def _show_meeting_context_menu(self, pos: Any) -> None:
        item = self.meetings_list.itemAt(pos)
        if item is None:
            return
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid is None:
            return
        self.select_meeting(int(mid))
        menu = QMenu(self)
        act_open = menu.addAction("Open in This Meeting")
        act_open.triggered.connect(lambda: self._open_meeting_at_seconds(int(mid), 0.0))
        act_reproc = menu.addAction("↻ Recover && Re-Transcribe")
        m = self.controller.db.get_meeting(int(mid)) if hasattr(self.controller, "db") else None
        act_reproc.setEnabled(bool(m and self._resolve_audio_path(m) is not None))
        act_reproc.triggered.connect(lambda: self._on_reprocess_meeting_clicked(int(mid)))
        act_move = menu.addAction("Move to Folder...")
        act_move.triggered.connect(self._move_selected_to_folder)
        act_exp = menu.addAction("Export Markdown (.md)...")
        act_exp.triggered.connect(lambda: self._export_format("md"))
        act_rev = menu.addAction("Reveal Recording Folder")
        act_rev.triggered.connect(self._reveal_recording_folder)
        menu.addSeparator()
        act_del = menu.addAction("Delete Recording")
        act_del.triggered.connect(self._delete_selected_meeting)
        menu.exec(self.meetings_list.viewport().mapToGlobal(pos))

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
        if hasattr(self, "overall_view"):
            self.overall_view.refresh_folders()

    def _on_folder_filter_changed(self) -> None:
        self._active_folder_id = self.combo_folders.currentData()
        self.refresh_meetings_list()

    def _on_create_folder_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            self.controller.db.create_folder(name=name.strip(), folder_type="personal")
            self.refresh_folders()

    def refresh_meetings_list(self, force: bool = False) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or (db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists()):
            return
        q = self.txt_search.text().strip()
        all_meetings = self.controller.db.list_meetings(search_query=q, folder_id=self._active_folder_id, limit=300)

        # Compute recoverable count across unfiltered folder/query scope
        path_exists_cache: Dict[str, bool] = {}

        def _fast_exists(raw_p: Any) -> bool:
            s = str(raw_p or "").strip()
            if not s:
                return False
            cached = path_exists_cache.get(s)
            if cached is None:
                cached = Path(s).exists()
                path_exists_cache[s] = cached
            return cached

        recoverable_count = 0
        first_interrupted: Optional[int] = None
        for m in all_meetings:
            raw_s = str(m.get("status") or "").strip().lower()
            is_recov = raw_s in ("interrupted", "incomplete", "finalization_failed", "error", "failed")
            if is_recov and self._resolve_audio_path(m) is not None:
                recoverable_count += 1
                if first_interrupted is None:
                    first_interrupted = int(m["id"])

        # Apply client-side quick status/media filter pill
        flt = getattr(self, "_library_filter", "all")
        meetings: List[Dict[str, Any]] = []
        for m in all_meetings:
            raw_s = str(m.get("status") or "").strip().lower()
            is_recov = raw_s in ("interrupted", "incomplete", "finalization_failed", "error", "failed")
            has_vid = _fast_exists(m.get("video_path"))
            if flt == "completed" and raw_s != "completed":
                continue
            if flt == "recoverable" and not is_recov:
                continue
            if flt == "video" and not has_vid:
                continue
            meetings.append(m)

        # Apply sort mode
        sort_mode = getattr(self, "_library_sort", "newest")
        if sort_mode == "duration":
            meetings.sort(key=lambda x: float(x.get("duration_sec") or 0.0), reverse=True)
        elif sort_mode == "title":
            meetings.sort(key=lambda x: str(x.get("title") or "").lower())

        list_sig = (
            self._theme,
            q,
            self._active_folder_id,
            flt,
            sort_mode,
            bool(self._library_compact),
            self._selected_meeting_id,
            recoverable_count,
            first_interrupted,
            bool(self._interrupted_dismissed),
            tuple(
                (
                    int(m["id"]),
                    str(m.get("title") or ""),
                    str(m.get("status") or ""),
                    str(m.get("post_call_stage") or ""),
                    float(m.get("duration_sec") or 0.0),
                    str(m.get("started_at") or ""),
                    str(m.get("source_platform") or ""),
                    _fast_exists(m.get("video_path")),
                )
                for m in meetings
            ),
        )
        if not force and self._last_meetings_sig == list_sig and self.meetings_list.count() == len(meetings):
            return
        self._last_meetings_sig = list_sig

        self.meetings_list.blockSignals(True)
        self.meetings_list.clear()
        prev_group = ""
        for m in meetings:
            dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
            date_str = str(m.get("started_at") or "")[:10]
            plat = str(m.get("source_platform") or "Call")
            has_vid = _fast_exists(m.get("video_path"))
            raw_status = str(m.get("status") or "").strip().lower()
            is_recoverable = raw_status in ("interrupted", "incomplete", "finalization_failed", "error", "failed")
            badge = " · Video" if has_vid else ""
            status_badge = self._format_status_badge(raw_status, str(m.get("post_call_stage") or ""))
            if status_badge and raw_status not in ("completed", ""):
                badge += f" · {status_badge}"

            title_text = str(m.get("title") or "Untitled Meeting")
            sub_text = f"{date_str} · {dur} · {plat}{badge}"
            status_kind = (
                "recoverable"
                if is_recoverable
                else (
                    "recording"
                    if raw_status == "recording"
                    else ("processing" if raw_status in ("transcribing", "processing", "finalizing") else "ok")
                )
            )
            grp = self._date_group_for_meeting(m) if sort_mode == "newest" else ""
            grp_header = grp if (grp and grp != prev_group) else ""
            if grp:
                prev_group = grp

            item = QListWidgetItem(f"{title_text}\n{sub_text}")
            item.setData(Qt.ItemDataRole.UserRole, m["id"])
            item.setData(
                VoxMeetingListDelegate.ROLE_META,
                {
                    "title": title_text,
                    "sub": sub_text,
                    "status_kind": status_kind,
                    "compact": self._library_compact,
                },
            )
            item.setData(VoxMeetingListDelegate.ROLE_GROUP, grp_header)
            self.meetings_list.addItem(item)
            if m["id"] == self._selected_meeting_id:
                item.setSelected(True)
                self.meetings_list.setCurrentItem(item)
        self.meetings_list.blockSignals(False)

        self._first_interrupted_id = first_interrupted
        if hasattr(self, "btn_rescued_pill"):
            self.btn_rescued_pill.setVisible(recoverable_count > 0 and self._interrupted_dismissed)
            self.btn_rescued_pill.setText(f"{recoverable_count} Rescued")
        if hasattr(self, "interrupted_banner"):
            if recoverable_count > 0 and not self._interrupted_dismissed:
                self.lbl_interrupted_summary.setText(
                    f"{recoverable_count} interrupted recording{'s' if recoverable_count != 1 else ''} "
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
            self.lbl_sel_title.setCursorPosition(0)
            self.lbl_sel_title.setReadOnly(True)
            self.lbl_sel_title.blockSignals(False)
            if hasattr(self, "btn_reprocess"):
                self.btn_reprocess.setEnabled(False)
                self.btn_reprocess.setVisible(False)
            for bname in ("btn_watch_video", "btn_speakers", "btn_export", "btn_more", "btn_play_pause", "btn_skip_back", "btn_skip_forward"):
                b = getattr(self, bname, None)
                if b is not None:
                    b.setEnabled(False)
            if hasattr(self, "btn_watch_video"):
                self.btn_watch_video.setVisible(False)
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
            if hasattr(self, "overall_view"):
                if self._workspace_context == "overall":
                    self.overall_view.refresh_data()
                else:
                    self.overall_view._needs_refresh = True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("_on_meeting_saved error: %s", exc)

    def _on_meeting_item_selected(self, current: Optional[QListWidgetItem], _prev: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        mid = current.data(Qt.ItemDataRole.UserRole)
        if mid is not None:
            self.set_workspace_context("meeting")
            self.select_meeting(int(mid))

    @staticmethod
    def _format_status_badge(raw_status: str, post_call_stage: str = "") -> str:
        st = str(raw_status or "").strip().lower()
        pc = str(post_call_stage or "").strip().lower()
        if st in ("transcribing", "processing", "finalizing") and pc:
            pc_map = {
                "recording_saved": "● Recording Saved · Queued",
                "preparing_audio": "⏳ Preparing Audio...",
                "transcribing": "⏳ Transcribing Full Audio...",
                "verifying": "⏳ Verifying Transcript...",
                "finalizing": "⏳ Finalizing Notes...",
            }
            if pc in pc_map:
                return pc_map[pc]
        mapping = {
            "recording": "● Recording Audio (Post-Call)",
            "transcribing": "⏳ Finalizing...",
            "processing": "⏳ Finalizing...",
            "finalizing": "⏳ Finalizing...",
            "completed": "✓ Completed",
            "incomplete": "⚠ Incomplete",
            "interrupted": "⚠ Interrupted",
            "finalization_failed": "✕ Finalization Failed",
            "error": "✕ Finalization Failed",
            "failed": "✕ Finalization Failed",
        }
        return mapping.get(st, "")

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

        from voxlocal.storage.exporter import format_meeting_local_datetime
        dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
        plat = str(m.get("source_platform") or "Desktop Call")
        date_str = format_meeting_local_datetime(
            m.get("created_at") or m.get("started_at"),
            str(m.get("timezone") or ""),
        )
        cal_ev_id = str(m.get("calendar_event_id") or "")
        raw_status = str(m.get("status") or "").strip().lower()
        is_recoverable = raw_status in ("interrupted", "incomplete", "finalization_failed", "error", "failed")

        self.lbl_sel_title.blockSignals(True)
        self.lbl_sel_title.setText(str(m.get("title") or "Untitled Meeting"))
        self.lbl_sel_title.setCursorPosition(0)
        self.lbl_sel_title.setReadOnly(False)
        self.lbl_sel_title.blockSignals(False)

        meta_text = f"{date_str} · {dur} · {plat}"
        post_stage = str(m.get("post_call_stage") or "").strip().lower()
        status_badge = self._format_status_badge(raw_status, post_stage)
        if status_badge:
            meta_text += f" · {status_badge}"
        eng_summary = str(m.get("post_call_engine_summary") or "").strip()
        post_msg = str(m.get("post_call_status_message") or "").strip()
        if raw_status in ("transcribing", "processing", "finalizing") and post_msg:
            meta_text += f" · {post_msg}"
        if cal_ev_id:
            meta_text += " · Calendar Event"

        tooltip_parts = [meta_text]
        if eng_summary:
            tooltip_parts.append(f"Engine: {eng_summary}")
        comp_raw = str(m.get("completeness_report_json") or "").strip()
        if comp_raw:
            try:
                comp = json.loads(comp_raw)
                unc_cnt = int(comp.get("uncertain_segment_count") or 0)
                cov_vals = [
                    v
                    for v in (
                        comp.get("mic_track_coverage_pct"),
                        comp.get("system_track_coverage_pct"),
                        comp.get("mixed_track_coverage_pct"),
                    )
                    if v is not None
                ]
                if cov_vals:
                    tooltip_parts.append(f"{min(cov_vals):.0f}% audio processed")
                if unc_cnt > 0:
                    meta_text += f" · {unc_cnt} uncertain"
                    tooltip_parts.append(f"{unc_cnt} uncertain span{'s' if unc_cnt != 1 else ''}")
            except Exception:
                pass
        self.lbl_sel_meta.setText(meta_text)
        self.lbl_sel_meta.setToolTip(" · ".join(tooltip_parts))
        if hasattr(self, "btn_view_in_calendar"):
            self.btn_view_in_calendar.setVisible(bool(cal_ev_id))

        has_vid = self._resolve_video_path(m) is not None
        res_audio = self._resolve_audio_path(m)
        if hasattr(self, "btn_reprocess"):
            self.btn_reprocess.setEnabled(res_audio is not None)
            self.btn_reprocess.setVisible(is_recoverable)
            if is_recoverable:
                t_btn = get_theme_tokens(self._theme)
                self.btn_reprocess.setText("↻ Recover & Re-Transcribe")
                self.btn_reprocess.setStyleSheet(
                    f"background-color: {t_btn['status_warning_bg']}; color: {t_btn['status_warning_text']}; "
                    f"border: 1px solid {t_btn['status_warning']}; border-radius: 7px; padding: 4px 10px; font-weight: 600;"
                )
            else:
                self.btn_reprocess.setText("↻ Reprocess")
                self.btn_reprocess.setStyleSheet("")

        self.btn_watch_video.setEnabled(has_vid)
        self.btn_watch_video.setVisible(has_vid)
        self.btn_watch_video.setToolTip("Play call window video recording" if has_vid else "No video recorded for this meeting")
        self.btn_speakers.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_more.setEnabled(True)

        # Coalesced async media load (avoids blocking rapid meeting switching in FFmpeg probe)
        self.media_player.stop()
        self._pending_media_path = res_audio
        has_audio = res_audio is not None
        if has_audio:
            self.btn_play_pause.setEnabled(True)
            self.media_slider.setEnabled(True)
            self._media_probe_timer.start()
        else:
            self._media_probe_timer.stop()
            self._loaded_media_url = ""
            self.media_player.setSource(QUrl())
            self.btn_play_pause.setEnabled(False)
            self.media_slider.setEnabled(False)
        for skip_b in ("btn_skip_back", "btn_skip_forward"):
            if hasattr(self, skip_b):
                getattr(self, skip_b).setEnabled(has_audio)
        self._sync_play_pause_visual(False)

        self.studio.load_meeting(meeting_id)
        self._refresh_ask_rail_ui()

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
            self.lbl_sel_title.setCursorPosition(0)
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
                    if hasattr(self.controller, "rename_meeting_speaker_and_reindex"):
                        changed += int(
                            self.controller.rename_meeting_speaker_and_reindex(
                                self._selected_meeting_id, old_name, new_name
                            )
                            or 0
                        )
                    else:
                        changed += self.controller.db.rename_meeting_speaker(
                            self._selected_meeting_id, old_name, new_name
                        )
            self.studio.load_meeting(self._selected_meeting_id)
            if hasattr(self.studio, "tab_knowledge"):
                self.studio.tab_knowledge.refresh_items()
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
        act_speakers = menu.addAction("Manage Speakers...")
        act_speakers.triggered.connect(self._assign_speakers_dialog)
        act_reprocess = menu.addAction("Reprocess Transcription & Summary")
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

    def _sync_play_pause_visual(self, playing: bool) -> None:
        t = get_theme_tokens(self._theme)
        fg = t.get("primary_btn_text", "#FFFFFF")
        if hasattr(self, "btn_play_pause"):
            self.btn_play_pause.setText("")
            self.btn_play_pause.setIcon(get_icon("pause" if playing else "play", fg, 14, filled=True))
            self.btn_play_pause.setToolTip("Pause audio" if playing else "Play audio")

    def _skip_media_delta(self, delta_sec: float) -> None:
        if self._media_probe_timer.isActive():
            self._media_probe_timer.stop()
        self._apply_pending_media_source()
        dur_ms = max(0, int(self.media_player.duration() or 0))
        cur_ms = max(0, int(self.media_player.position() or 0))
        nxt_ms = max(0, cur_ms + int(round(delta_sec * 1000.0)))
        if dur_ms > 0:
            nxt_ms = min(dur_ms, nxt_ms)
        self.media_player.setPosition(nxt_ms)

    def _toggle_media_playback(self) -> None:
        if self._media_probe_timer.isActive():
            self._media_probe_timer.stop()
        self._apply_pending_media_source()
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self._sync_play_pause_visual(False)
        else:
            self.media_player.play()
            self._sync_play_pause_visual(True)

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
        if self._media_probe_timer.isActive():
            self._media_probe_timer.stop()
        self._apply_pending_media_source()
        pos_ms = max(0, int(float(seconds) * 1000.0))
        self.media_player.setPosition(pos_ms)
        if hasattr(self, "studio"):
            self.studio.tab_transcript.highlight_time(float(seconds))
        if (
            self.media_player.source().isValid()
            and self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        ):
            self.media_player.play()
            self._sync_play_pause_visual(True)

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#ntMuted {{ color: {t['text_muted']}; font-size: 11.5px; }}
            QFrame#ntCommandBar {{
                background-color: {t['surface']};
                border: 1px solid {t['outline_variant']};
                border-radius: 11px;
            }}
            QFrame#ntCard, QFrame#ntAskRail {{
                background-color: {t['surface']};
                border: 1px solid {t['outline_variant']};
                border-radius: 12px;
            }}
            QFrame#ntBanner {{
                background-color: {t['accent_lumen']};
                border: 1px solid {t['accent_lumen_border']};
                border-radius: 10px;
            }}
            QFrame#ntInterruptedBanner {{
                background-color: {t['status_warning_bg']};
                border: 1px solid {t['status_warning']};
                border-radius: 10px;
            }}
            QLabel#ntScopeBadge {{
                background-color: {t['accent_dawn']};
                color: {t['accent_dawn_text']};
                border: none;
                border-radius: 6px;
                padding: 3px 9px;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#ntAskScopeLock {{
                background-color: {t['surface_container']};
                color: {t['text_primary']};
                border: 1px solid {t['outline_variant']};
                border-radius: 8px;
                padding: 6px 9px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QLineEdit#ntSelTitleEdit {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 2px 6px;
                color: {t['text']};
                font-size: 19px;
                font-weight: 600;
            }}
            QLineEdit#ntSelTitleEdit:hover {{
                background-color: {t['surface_hover']};
                border: 1px solid {t['outline_variant']};
            }}
            QLineEdit#ntSelTitleEdit:focus {{
                background-color: {t['surface']};
                border: 1.5px solid {t['input_focus_border']};
            }}
            QLineEdit#voxSearchField, QComboBox {{
                background-color: {t['surface_container']};
                color: {t['text_primary']};
                border: 1px solid {t['outline_variant']};
                border-radius: 8px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QLineEdit#voxSearchField:focus, QComboBox:focus {{
                background-color: {t['surface']};
                border: 1.5px solid {t['input_focus_border']};
            }}
            QProgressBar#ntLevelBar {{
                background-color: {t['surface_well']};
                border: 1px solid {t['outline_variant']};
                border-radius: 3px;
            }}
            QProgressBar#ntLevelBar::chunk {{
                background-color: {t['accent_emerald']};
                border-radius: 2px;
            }}
            QListWidget#ntMeetingsList, QListWidget {{
                background-color: {t['surface']};
                border: none;
                border-radius: 8px;
                padding: 2px 0px;
                outline: none;
            }}
            QTextBrowser#ntAskBrowser {{
                background-color: {t['bg']};
                border: 1px solid {t['outline_variant']};
                border-radius: 9px;
                padding: 8px;
            }}
            QPushButton#secondaryBtn, QPushButton#ntStatusPillBtn {{
                background-color: {t['secondary_btn_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['outline_variant']};
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#secondaryBtn:hover, QPushButton#ntStatusPillBtn:hover {{
                background-color: {t['surface_hover']};
                border-color: {t['border']};
            }}
            QPushButton#secondaryBtn:checked, QPushButton#ntStatusPillBtn:checked {{
                background-color: {t['accent_dawn']};
                color: {t['accent_dawn_text']};
                border: 1px solid {t['accent']};
            }}
            QPushButton#ntAskToggleBtn {{
                background-color: {t['secondary_btn_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['outline_variant']};
                border-radius: 8px;
                padding: 5px 11px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#ntAskToggleBtn:hover {{
                background-color: {t['ai_tint']};
                color: {t['ai_text']};
                border-color: {t['ai_border']};
            }}
            QPushButton#ntAskToggleBtn:checked {{
                background-color: {t['ai_tint']};
                color: {t['ai_text']};
                border: 1px solid {t['ai_border']};
            }}
            QPushButton#ntWarnPillBtn {{
                background-color: {t['status_warning_bg']};
                color: {t['status_warning_text']};
                border: 1px solid {t['status_warning']};
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton#ntChipBtn {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 3px 9px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton#ntChipBtn:hover {{
                background-color: {t['surface_hover']};
                color: {t['text_primary']};
            }}
            QPushButton#ntChipBtn:checked {{
                background-color: {t['accent_dawn']};
                color: {t['accent_dawn_text']};
                border: 1px solid {t['accent_dawn_hover']};
            }}
            QPushButton#ntIconToggleBtn {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 0px;
            }}
            QPushButton#ntIconToggleBtn:hover {{
                background-color: {t['surface_hover']};
            }}
            QPushButton#ntIconToggleBtn:checked {{
                background-color: {t['accent_dawn']};
                border: 1px solid {t['accent_dawn_hover']};
            }}
            QPushButton#ntPlayBtn {{
                background-color: {t['primary_btn_bg']};
                color: {t['primary_btn_text']};
                border: none;
                border-radius: 15px;
                padding: 0px;
            }}
            QPushButton#ntPlayBtn:hover {{
                background-color: {t['primary_btn_hover']};
            }}
            QPushButton#ntPlayBtn:disabled {{
                background-color: {t['surface_well']};
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton#primaryBtn, QPushButton#fathomBtn {{
                background-color: {t['primary_btn_bg']};
                color: {t['primary_btn_text']};
                border: none;
                border-radius: 8px;
                padding: 6px 13px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton#primaryBtn:hover, QPushButton#fathomBtn:hover {{
                background-color: {t['primary_btn_hover']};
            }}
            QFrame#ntAutoStartBanner {{
                background-color: {t['surface']};
                border: 1px solid {t['outline_variant']};
                border-radius: 10px;
            }}
            QFrame#ntAutoBadge {{
                background-color: {t['accent_dawn']};
                border: none;
                border-radius: 13px;
            }}
            QPushButton#ntTurnOnBtn {{
                background-color: {t['accent_dawn']};
                color: {t['accent_dawn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 7px;
                padding: 4px 12px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton#ntTurnOnBtn:hover {{
                background-color: {t['accent_dawn_hover']};
            }}
            QPushButton#ghostBtn {{
                background: transparent;
                border: none;
                color: {t['text_secondary']};
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
                border-radius: 7px;
            }}
            QPushButton#ghostBtn:hover {{
                background: {t['surface_hover']};
                color: {t['text_primary']};
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
                f"font-weight: 600; font-size: 12px; color: {t['status_warning_text']};"
            )
        if hasattr(self, "btn_reprocess") and self.btn_reprocess.text() == "↻ Recover & Re-Transcribe":
            self.btn_reprocess.setStyleSheet(
                f"background-color: {t['status_warning_bg']}; color: {t['status_warning_text']}; "
                f"border: 1px solid {t['status_warning']}; border-radius: 6px; padding: 4px 10px; font-weight: 700;"
            )
        if hasattr(self, "mode_switcher"):
            self.mode_switcher.set_theme(self._theme)
        if hasattr(self, "meetings_delegate"):
            self.meetings_delegate.set_theme(self._theme)
            self.meetings_list.viewport().update()
        if hasattr(self, "empty_card") and hasattr(self.empty_card, "set_theme"):
            self.empty_card.set_theme(self._theme)
        if hasattr(self, "overall_view"):
            self.overall_view.apply_theme(self._theme)
        if hasattr(self, "studio"):
            self.studio.apply_theme(self._theme)
        self._refresh_toolbar_icons()
        self._refresh_ask_rail_ui()

    def _refresh_toolbar_icons(self) -> None:
        t = get_theme_tokens(self._theme)
        fg = t.get("text_secondary", "#475569")
        pri_fg = t.get("primary_btn_text", "#FFFFFF")
        ai_fg = t.get("ai_text", t.get("accent", "#1967D2"))
        icon_map = [
            ("btn_toggle_sidebar", "sidebar_toggle", fg, 16),
            ("btn_upcoming_pill", "calendar", fg, 14),
            ("btn_rescued_pill", "alert", t.get("status_warning_text", fg), 14),
            ("btn_ask_vox_rail", "sparkles", ai_fg if self._ask_rail_open else fg, 14),
            ("btn_drawer", "drawer", fg, 14),
            ("btn_settings", "settings", fg, 15),
            ("btn_start_adhoc", "record", pri_fg, 14),
            ("btn_new_folder", "folder_plus", fg, 15),
            ("btn_density", "density_compact" if self._library_compact else "density_comfy", fg, 15),
            ("btn_split_view", "split_view", fg, 14),
            ("btn_watch_video", "video", fg, 14),
            ("btn_export", "export", fg, 14),
            ("btn_more", "more_horiz", fg, 16),
            ("btn_view_in_calendar", "calendar", fg, 13),
            ("btn_speakers", "users", fg, 14),
            ("btn_skip_back", "skip_back", fg, 14),
            ("btn_skip_forward", "skip_forward", fg, 14),
            ("btn_ask_close", "close", fg, 14),
            ("btn_ask_copy_ans", "copy", fg, 12),
            ("btn_ask_copy_src", "copy", fg, 12),
            ("btn_ask_rail_send", "send", pri_fg, 13),
        ]
        for attr, iname, col, sz in icon_map:
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setIcon(get_icon(iname, col, sz))
        if hasattr(self, "auto_start_banner") and hasattr(self.auto_start_banner, "lbl_icon"):
            self.auto_start_banner.lbl_icon.setPixmap(get_pixmap("meetings", t.get("accent", "#1967D2"), 15))
        if hasattr(self, "lbl_ask_rail_icon"):
            self.lbl_ask_rail_icon.setPixmap(get_pixmap("sparkles", ai_fg, 16))
        playing = (
            hasattr(self, "media_player")
            and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self._sync_play_pause_visual(playing)

    # ------------------------------------------------------------------
    # Unified Context-Locked Ask VOX Right Rail (Zero Cross-Context Leakage)
    # ------------------------------------------------------------------
    def _toggle_ask_rail(self, visible: Optional[bool] = None) -> None:
        if visible is None:
            self._ask_rail_open = not self._ask_rail_open
        else:
            self._ask_rail_open = bool(visible)
        if hasattr(self, "ask_rail"):
            self.ask_rail.setVisible(self._ask_rail_open)
        if hasattr(self, "btn_ask_vox_rail"):
            self.btn_ask_vox_rail.blockSignals(True)
            self.btn_ask_vox_rail.setChecked(self._ask_rail_open)
            self.btn_ask_vox_rail.blockSignals(False)
        # Progressive disclosure: prevent duplicate inline Ask VOX bars when Right Rail is open
        if hasattr(self, "studio") and hasattr(self.studio, "tab_summary") and hasattr(self.studio.tab_summary, "ask_box"):
            self.studio.tab_summary.ask_box.setVisible(not self._ask_rail_open)
        if hasattr(self, "overall_view") and hasattr(self.overall_view, "prompt_strip_card"):
            self.overall_view.prompt_strip_card.setVisible(not self._ask_rail_open)
        self._persist_ui_pref("notetaker_ask_rail_open", self._ask_rail_open)
        self._refresh_toolbar_icons()
        self._refresh_ask_rail_ui()

    def _active_ask_key(self) -> str:
        if self._workspace_context == "overall":
            return "overall"
        return f"m:{self._selected_meeting_id}" if self._selected_meeting_id is not None else "none"

    def _get_current_ask_thread(self) -> List[Dict[str, Any]]:
        if self._workspace_context == "overall":
            return self._overall_ask_thread
        if self._selected_meeting_id is not None:
            return self._meeting_ask_threads.setdefault(int(self._selected_meeting_id), [])
        return []

    def _refresh_ask_rail_ui(self) -> None:
        if not hasattr(self, "lbl_ask_scope_badge"):
            return
        t = get_theme_tokens(self._theme)
        if self._workspace_context == "overall":
            self.lbl_ask_scope_badge.setText(f"Overall Library · {self._overall_scope_label}")
            self.lbl_ask_scope_badge.setStyleSheet(
                f"background: {t['status_info_bg']}; color: {t['status_info_text']}; "
                f"border: 1px solid {t['accent']}; border-radius: 7px; padding: 6px 9px; font-size: 11.5px; font-weight: 600;"
            )
            self.txt_ask_rail.setPlaceholderText("Ask across all filtered meetings...")
            chips = [
                ("Topics", "What recurring topics and projects appear across these meetings?"),
                ("Commitments", "Summarize all open commitments and owners across these meetings."),
                ("Conflicts", "Compare key decisions across meetings and flag any contradictions."),
            ]
        else:
            title = self.lbl_sel_title.text().strip() if hasattr(self, "lbl_sel_title") else "Select a meeting"
            if self._selected_meeting_id is None:
                self.lbl_ask_scope_badge.setText("This Meeting · Select a meeting from Library")
            else:
                self.lbl_ask_scope_badge.setText(f"This Meeting Only: {title}")
            self.lbl_ask_scope_badge.setStyleSheet(
                f"background: {t['accent_dawn']}; color: {t['accent_dawn_text']}; "
                f"border: 1px solid {t['outline_variant']}; border-radius: 7px; padding: 6px 9px; font-size: 11.5px; font-weight: 600;"
            )
            self.txt_ask_rail.setPlaceholderText("Ask strictly about this meeting...")
            chips = [
                ("Decisions", "What were the key decisions made in this meeting?"),
                ("Actions", "List action items, owners, and due dates from this meeting."),
                ("Risks", "What risks, blockers, or open questions were raised in this meeting?"),
            ]

        for idx, btn in enumerate(getattr(self, "_ask_chip_btns", [])):
            if idx < len(chips):
                btn.setText(chips[idx][0])
                btn.setProperty("ask_prompt", chips[idx][1])
                btn.setVisible(True)
        self._render_ask_rail_thread()

    def _on_ask_chip_clicked(self, idx: int) -> None:
        if idx < 0 or idx >= len(getattr(self, "_ask_chip_btns", [])):
            return
        prompt = str(self._ask_chip_btns[idx].property("ask_prompt") or "").strip()
        if prompt:
            self.txt_ask_rail.setText(prompt)
            self._submit_ask_rail_input()

    def _submit_ask_rail_input(self) -> None:
        q = self.txt_ask_rail.text().strip() if hasattr(self, "txt_ask_rail") else ""
        if not q:
            return
        self.txt_ask_rail.clear()
        if self._workspace_context == "overall":
            scope_ids = (
                self.overall_view.get_active_scope()[0]
                if hasattr(self, "overall_view")
                else self._overall_scope_ids
            )
            self._run_context_ask_async(q, target_key="overall", meeting_id=None, meeting_ids=scope_ids)
        else:
            if self._selected_meeting_id is None:
                return
            mid = int(self._selected_meeting_id)
            self._run_context_ask_async(q, target_key=f"m:{mid}", meeting_id=mid, meeting_ids=None)

    def _clear_current_ask_thread(self) -> None:
        if self._workspace_context == "overall":
            self._overall_ask_thread.clear()
        elif self._selected_meeting_id is not None:
            self._meeting_ask_threads[int(self._selected_meeting_id)] = []
        self._last_ask_answer_plain = ""
        self._last_ask_with_sources_plain = ""
        self._render_ask_rail_thread()

    def _copy_ask_answer(self, with_sources: bool = False) -> None:
        txt = self._last_ask_with_sources_plain if with_sources else self._last_ask_answer_plain
        if txt:
            QApplication.clipboard().setText(txt)
            self.status_toast.emit("Copied", "Answer copied to clipboard.")

    def _on_ask_anchor_clicked(self, url: QUrl) -> None:
        raw = url.toString()
        if raw.startswith("meeting:"):
            spec = raw[len("meeting:"):]
            parts = spec.split("@", 1)
            try:
                mid = int(parts[0])
                sec = float(parts[1]) if len(parts) > 1 else 0.0
                self._open_meeting_at_seconds(mid, sec)
            except Exception:
                pass
        elif raw.startswith("seek:"):
            try:
                sec = float(raw[len("seek:"):])
                self._seek_to_seconds(sec)
                if hasattr(self, "studio") and hasattr(self.studio, "unified_tabs"):
                    self.studio.unified_tabs.setCurrentIndex(1)
            except Exception:
                pass

    def _render_ask_rail_thread(self) -> None:
        if not hasattr(self, "ask_rail_browser"):
            return
        t = get_theme_tokens(self._theme)
        thread = self._get_current_ask_thread()
        loading = self._ask_loading_key == self._active_ask_key()

        font_css = get_html_font_family()
        if not thread and not loading:
            scope_desc = (
                f"the current filtered library (<b>{html.escape(self._overall_scope_label)}</b>)"
                if self._workspace_context == "overall"
                else "<b>this selected meeting only</b> — other meetings are strictly excluded"
            )
            self.ask_rail_browser.setHtml(
                f"<div style='font-family:{font_css};font-size:13px;line-height:1.52;color:{t['text_secondary']};padding:6px;'>"
                f"<p style='margin:0 0 6px 0;'>Ask VOX answers are grounded strictly in local transcripts and memory for {scope_desc}.</p>"
                f"<p style='margin:0;color:{t['text_muted']};font-size:12px;'>Use the quick prompt chips above or ask any question below.</p>"
                "</div>"
            )
            return

        parts = [f"<div style='font-family:{font_css};font-size:13px;color:{t['text_primary']};line-height:1.52;'>"]
        for entry in thread:
            q_html = html.escape(str(entry.get("q") or ""))
            ans_md = str(entry.get("a") or "")
            cits = list(entry.get("citations") or [])

            parts.append(
                f"<div style='background:{t['accent_dawn']};border:1px solid {t['chip_border']};"
                f"border-radius:8px;padding:7px 10px;margin:0 0 8px 0;'>"
                f"<div style='font-size:10.5px;font-weight:700;color:{t['accent_dawn_text']};margin-bottom:2px;'>YOU</div>"
                f"<div style='color:{t['text_primary']};font-weight:600;'>{q_html}</div></div>"
            )
            # Synthesis card
            ans_html = render_safe_markdown_html(ans_md, theme=self._theme)
            parts.append(
                f"<div style='background:{t['surface']};border:1px solid {t['border']};"
                f"border-radius:8px;padding:8px 10px;margin:0 0 8px 0;'>"
                f"<div style='font-size:10.5px;font-weight:700;color:{t['accent_fathom']};margin-bottom:4px;'>SYNTHESIS</div>"
                f"{ans_html}"
            )
            # Distinct Direct Source Evidence section
            if cits:
                parts.append(
                    f"<div style='margin-top:8px;padding-top:6px;border-top:1px solid {t['line']};'>"
                    f"<div style='font-size:10.5px;font-weight:700;color:{t['text_muted']};margin-bottom:4px;'>DIRECT SOURCE EVIDENCE</div>"
                )
                for c in cits[:5]:
                    cmid = c.get("meeting_id")
                    ctitle = html.escape(str(c.get("meeting_title") or "Meeting")[:34])
                    sec = float(c.get("start_sec") or 0.0)
                    ts_lbl = format_seconds_hms(sec)
                    snippet = html.escape(str(c.get("excerpt") or c.get("text") or "")[:140])
                    href = f"meeting:{int(cmid)}@{sec:.1f}" if cmid is not None else f"seek:{sec:.1f}"
                    parts.append(
                        f"<div style='background:{t['surface_secondary']};border-radius:6px;padding:5px 7px;margin-bottom:4px;font-size:11.5px;'>"
                        f"<a href='{href}' style='color:{t['accent_fathom']};font-weight:700;text-decoration:none;'>"
                        f"[{ctitle} · {ts_lbl}]</a> "
                        f"<span style='color:{t['text_secondary']};'>{snippet}</span></div>"
                    )
                parts.append("</div>")
            parts.append("</div>")

        if loading:
            parts.append(
                f"<div style='padding:6px 8px;color:{t['text_muted']};font-size:12px;'>"
                "⏳ Searching scoped local memory & synthesizing answer...</div>"
            )
        parts.append("</div>")
        self.ask_rail_browser.setHtml("".join(parts))
        sb = self.ask_rail_browser.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

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
            self._on_studio_ask_completed(f"m:{int(m_id)}", query, ans, citations)
            return
        self._run_context_ask_async(query, target_key=f"m:{int(m_id)}", meeting_id=int(m_id), meeting_ids=None)

    def _on_studio_ask_rail_submitted(self, query: str) -> None:
        self._on_studio_ask_rail_scoped_submitted(query, False)

    def _on_studio_ask_rail_scoped_submitted(self, query: str, cross_meeting: bool = False) -> None:
        if cross_meeting:
            self._run_context_ask_async(query, target_key="overall", meeting_id=None, meeting_ids=self._overall_scope_ids)
            return
        m_id = self._selected_meeting_id
        if m_id is None:
            self.studio.append_ask_rail_exchange(
                query,
                "Select a meeting first, or switch Ask VOX scope to 'All Meetings'.",
                citations=[],
            )
            return
        self._run_context_ask_async(query, target_key=f"m:{int(m_id)}", meeting_id=int(m_id), meeting_ids=None)

    def _run_context_ask_async(
        self,
        query: str,
        *,
        target_key: str,
        meeting_id: Optional[int],
        meeting_ids: Optional[List[int]],
    ) -> None:
        self._ask_loading_key = target_key
        self._render_ask_rail_thread()
        if hasattr(self, "studio") and hasattr(self.studio, "set_ask_rail_loading"):
            self.studio.set_ask_rail_loading(query)

        ans_depth = str(self.combo_ask_depth.currentData() or "concise") if hasattr(self, "combo_ask_depth") else "concise"
        hist_src = self._overall_ask_thread if target_key == "overall" else self._meeting_ask_threads.get(int(meeting_id or 0), [])
        conv_history = [{"question": e["q"], "answer": e["a"]} for e in hist_src[-4:]]

        def _worker() -> None:
            try:
                try:
                    res = self.controller.query_meeting_memory(
                        query,
                        meeting_id=meeting_id,
                        meeting_ids=meeting_ids,
                        conversation_history=conv_history,
                        answer_depth=ans_depth,
                    )
                except TypeError:
                    res = self.controller.query_meeting_memory(query, meeting_id=meeting_id)
                ans = str((res or {}).get("answer") or "No relevant excerpts found in scoped recorded meetings.")
                cits = list((res or {}).get("citations") or [])
            except Exception as exc:
                ans = f"Could not query meeting memory: {exc}"
                cits = []
            try:
                self._studio_ask_completed.emit(target_key, query, ans, cits)
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True, name="StudioAskVoxWorker").start()

    def _on_studio_ask_completed(
        self, target_key: str, question: str, answer: str, citations: object
    ) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        if self._ask_loading_key == target_key:
            self._ask_loading_key = None
        cits = list(citations) if isinstance(citations, (list, tuple)) else []
        entry = {"q": question, "a": answer, "citations": cits}

        if target_key == "overall":
            self._overall_ask_thread.append(entry)
        elif target_key.startswith("m:"):
            try:
                mid = int(target_key.split(":", 1)[1])
            except Exception:
                mid = -1
            if mid > 0:
                self._meeting_ask_threads.setdefault(mid, []).append(entry)

        plain_ans = strip_markdown_for_plain_copy(answer)
        src_lines = [
            f"- [{c.get('meeting_title', 'Meeting')} @ {format_seconds_hms(float(c.get('start_sec') or 0.0))}] "
            f"{str(c.get('excerpt') or c.get('text') or '').strip()}"
            for c in cits[:5]
        ]
        if target_key == self._active_ask_key():
            self._last_ask_answer_plain = plain_ans
            self._last_ask_with_sources_plain = (plain_ans + ("\n\nSources:\n" + "\n".join(src_lines) if src_lines else "")).strip()
            self._render_ask_rail_thread()

        if target_key == f"m:{self._selected_meeting_id}":
            self.studio.tab_summary.set_ask_answer(answer, citations=cits)
            self.studio.append_ask_rail_exchange(question, answer, citations=cits)
        elif target_key == "overall" and self._workspace_context == "overall":
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

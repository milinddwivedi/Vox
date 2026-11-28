"""VOX Dedicated Calendar Workspace — Multi-account overview, sync health, and Notetaker bridge."""

from __future__ import annotations

import html
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal.calendar.auth_service import sanitize_oauth_error_for_user
from voxlocal.calendar.base import CalendarMeeting
from voxlocal.calendar.graph_provider import copy_to_clipboard_win32
from voxlocal.core.logger import get_logger
from voxlocal.ui.theme import get_dialog_stylesheet, get_theme_tokens, get_ui_font

logger = get_logger("ui.calendar_page")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", html.unescape(text))
    return re.sub(r"\s+", " ", cleaned).strip()


def _provider_display_info(provider_type: str) -> Tuple[str, str]:
    pt = (provider_type or "").lower()
    mapping = {
        "ms_graph": ("M", "Microsoft Outlook"),
        "google": ("G", "Google Calendar"),
        "zoom": ("Z", "Zoom"),
        "ics": ("ICS", "Calendar Feed (.ics)"),
        "outlook_mapi": ("O", "Classic Outlook (MAPI)"),
    }
    return mapping.get(pt, ("C", pt.replace("_", " ").title() or "Calendar"))


def _platform_display_info(platform: str, tokens: Dict[str, Any]) -> Tuple[str, str]:
    p = (platform or "generic").lower()
    mapping = {
        "teams": ("Microsoft Teams", str(tokens.get("platform_teams", "#6264A7"))),
        "meet": ("Google Meet", str(tokens.get("platform_meet", "#0F9D58"))),
        "zoom": ("Zoom", str(tokens.get("platform_zoom", "#2D8CFF"))),
        "webex": ("Cisco Webex", str(tokens.get("platform_webex", "#00A0D1"))),
        "in_person": ("In Person", str(tokens.get("text_muted", "#736E62"))),
    }
    return mapping.get(p, ("Calendar Event", str(tokens.get("accent_fathom", "#034F46"))))


def _account_kind_label(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k == "personal":
        return "Personal"
    if k in {"work", "corporate", "business"}:
        return "Work / Org"
    if k == "school":
        return "School"
    return ""


def _state_display_info(state: str, is_healthy: bool, enabled: bool) -> Tuple[str, str]:
    if not enabled or (state or "").lower() in {"disabled", "paused"}:
        return ("Paused", "muted")
    st = (state or "").strip().lower()
    if st == "syncing":
        return ("Syncing...", "info")
    if st in {"offline_cached", "network_offline"}:
        return ("Offline (Cached)", "muted")
    if st == "expired":
        return ("Expired — Reconnect", "warn")
    if st in {"admin_approval_required", "admin_blocked"}:
        return ("Admin Approval Needed", "warn")
    if st in {"failed", "sync_error"}:
        return ("Sync Error", "warn")
    if st == "disconnected":
        return ("Disconnected", "muted")
    if st == "connected" or is_healthy:
        return ("Connected", "ok")
    return ("Connected", "ok") if is_healthy else ("Attention Needed", "warn")


def _format_last_sync(raw_ts: Any) -> str:
    if not raw_ts:
        return "Not synced yet"
    dt: Optional[datetime] = None
    if isinstance(raw_ts, datetime):
        dt = raw_ts
    elif isinstance(raw_ts, str):
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except Exception:
            return "Synced recently"
    if dt is None:
        return "Not synced yet"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))
    if diff < 45:
        return "Synced just now"
    if diff < 3600:
        return f"Synced {diff // 60}m ago"
    if diff < 86400:
        return f"Synced {diff // 3600}h ago"
    return f"Synced {diff // 86400}d ago"


def _format_time_window(m: CalendarMeeting) -> Tuple[str, str]:
    """Return (time_range_str, countdown_or_day_str) in the user's local timezone."""
    try:
        ls = m.local_start_time
        le = m.local_end_time
        tz_abbr = ls.tzname() or ""
        tz_short = f" {tz_abbr}" if tz_abbr and len(tz_abbr) <= 6 else ""
        if bool(getattr(m, "is_all_day", False)):
            time_str = "All day"
        else:
            start_fmt = ls.strftime("%I:%M %p").lstrip("0")
            end_fmt = le.strftime("%I:%M %p").lstrip("0")
            time_str = f"{start_fmt} – {end_fmt}{tz_short}"
    except Exception:
        time_str = "Scheduled"
        ls = datetime.now().astimezone()
        le = ls

    now_local = datetime.now().astimezone()
    try:
        if ls <= now_local <= le:
            mins_left = max(1, int((le - now_local).total_seconds() // 60))
            rel = f"Live now · {mins_left}m left"
        elif ls > now_local:
            delta_sec = int((ls - now_local).total_seconds())
            if delta_sec < 3600:
                rel = f"In {max(1, delta_sec // 60)}m"
            elif m.is_today:
                hrs = delta_sec // 3600
                mins = (delta_sec % 3600) // 60
                rel = f"In {hrs}h {mins}m" if mins else f"In {hrs}h"
            elif m.is_tomorrow:
                rel = f"Tomorrow · {ls.strftime('%b %d')}"
            else:
                rel = ls.strftime("%a, %b %d")
        else:
            rel = ls.strftime("%a, %b %d")
    except Exception:
        rel = ""
    return time_str, rel


class CalendarSelectionDialog(QDialog):
    """Modal allowing users to pick which calendars within a connected account to synchronize."""

    _loaded = pyqtSignal(list)

    def __init__(
        self,
        controller: Any,
        account: Dict[str, Any],
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.account = dict(account)
        self._theme = "dark" if (theme or "").lower() == "dark" else "light"
        self._checkboxes: List[Tuple[str, QCheckBox]] = []
        self._selected_ids: List[str] = list(self.account.get("selected_calendars") or [])

        ident = self.account.get("account_email") or self.account.get("display_name") or self.account.get("account_id", "")
        self.setWindowTitle(f"Select Calendars — {ident}")
        self.setModal(True)
        self.setMinimumSize(460, 420)
        self._loaded.connect(self._populate_calendars)

        self._build_ui()
        self.apply_theme(self._theme)
        self._load_async()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        lbl_title = QLabel("Choose Calendars to Sync")
        lbl_title.setFont(get_ui_font(18, 600, serif=True))
        root.addWidget(lbl_title)

        ident = self.account.get("account_email") or self.account.get("display_name") or "Account"
        lbl_sub = QLabel(f"Only checked calendars from {ident} will appear in VOX Calendar and Notetaker.")
        lbl_sub.setWordWrap(True)
        lbl_sub.setObjectName("onbSub")
        root.addWidget(lbl_sub)

        sel_row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.setObjectName("secondaryBtn")
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        sel_row.addWidget(btn_all)

        btn_none = QPushButton("Clear")
        btn_none.setObjectName("secondaryBtn")
        btn_none.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        sel_row.addWidget(btn_none)
        sel_row.addStretch(1)
        root.addLayout(sel_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(4, 4, 4, 4)
        self._list_lay.setSpacing(8)
        self._lbl_loading = QLabel("Loading calendars from your account...")
        self._list_lay.addWidget(self._lbl_loading)
        self._list_lay.addStretch(1)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        self.btn_save = QPushButton("Save Selection")
        self.btn_save.setObjectName("fathomBtn")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save)
        footer.addWidget(self.btn_save)
        root.addLayout(footer)

    def _load_async(self) -> None:
        cached = list(self.account.get("available_calendars") or [])
        if cached:
            self._populate_calendars(cached)

        account_id = str(self.account.get("account_id") or "")
        if not account_id or not hasattr(self.controller, "fetch_account_calendars"):
            if not cached:
                self._populate_calendars([])
            return

        def _worker() -> None:
            try:
                cals = self.controller.fetch_account_calendars(account_id) or []
            except Exception as exc:
                logger.debug("fetch_account_calendars failed: %s", exc)
                cals = cached
            try:
                self._loaded.emit(list(cals))
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True, name="VoxCalPicker").start()

    def _populate_calendars(self, calendars: List[Dict[str, Any]]) -> None:
        self._checkboxes.clear()
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not calendars:
            lbl = QLabel("No selectable sub-calendars returned. Your primary calendar will be synced automatically.")
            lbl.setWordWrap(True)
            self._list_lay.addWidget(lbl)
            self._list_lay.addStretch(1)
            return

        selected_set = set(self._selected_ids or [])
        for cal in calendars:
            cid = str(cal.get("id") or "")
            name = str(cal.get("name") or cid or "Calendar")
            is_primary = bool(cal.get("primary"))
            if is_primary:
                name = f"{name} (Primary)"
            cb = QCheckBox(name)
            checked = (cid in selected_set) if selected_set else (bool(cal.get("selected", True)) or is_primary)
            cb.setChecked(checked)
            self._checkboxes.append((cid, cb))
            self._list_lay.addWidget(cb)
        self._list_lay.addStretch(1)

    def _set_all_checked(self, state: bool) -> None:
        for _cid, cb in self._checkboxes:
            cb.setChecked(state)

    def selected_calendar_ids(self) -> List[str]:
        return [cid for cid, cb in self._checkboxes if cb.isChecked() and cid]

    def _save(self) -> None:
        ids = self.selected_calendar_ids()
        account_id = str(self.account.get("account_id") or "")
        if account_id and hasattr(self.controller, "set_account_selected_calendars"):
            self.controller.set_account_selected_calendars(account_id, ids)
        self.accept()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        self.setStyleSheet(get_dialog_stylesheet(self._theme))


class CalendarMeetingDetailsDialog(QDialog):
    """Detailed inspection view for a synchronized calendar meeting."""

    prepare_notetaker_clicked = pyqtSignal(object)
    open_recording_clicked = pyqtSignal(int)

    def __init__(
        self,
        meeting: CalendarMeeting,
        controller: Any,
        recording_id: Optional[int] = None,
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.meeting = meeting
        self.controller = controller
        self.recording_id = recording_id
        self._theme = "dark" if (theme or "").lower() == "dark" else "light"

        self.setWindowTitle(f"Meeting Details — {meeting.title or 'Untitled'}")
        self.setModal(True)
        self.setMinimumSize(560, 480)
        self._build_ui()
        self.apply_theme(self._theme)

    def _build_ui(self) -> None:
        t = get_theme_tokens(self._theme)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        plat_label, _plat_color = _platform_display_info(self.meeting.platform, t)
        eyebrow = QLabel(f"{plat_label.upper()}  ·  {self.meeting.calendar_provider_label.upper()}")
        eyebrow.setObjectName("onbEyebrow")
        root.addWidget(eyebrow)

        lbl_title = QLabel(self.meeting.title or "Untitled Meeting")
        lbl_title.setFont(get_ui_font(20, 600, serif=True))
        lbl_title.setWordWrap(True)
        root.addWidget(lbl_title)

        time_str, rel_str = _format_time_window(self.meeting)
        tz_label = self.meeting.timezone_name or (self.meeting.local_start_time.tzname() or "Local Time")
        date_str = self.meeting.local_start_time.strftime("%A, %B %d, %Y")
        tags = []
        if self.meeting.is_recurring:
            tags.append("Modified Occurrence" if self.meeting.is_modified_occurrence else "Recurring")
        if rel_str:
            tags.append(rel_str)
        tag_suffix = f"  ·  {' · '.join(tags)}" if tags else ""

        lbl_when = QLabel(f"{date_str}  ·  {time_str} ({tz_label}){tag_suffix}")
        lbl_when.setWordWrap(True)
        lbl_when.setStyleSheet(f"color: {t['text_secondary']}; font-size: 13px;")
        root.addWidget(lbl_when)

        card = QFrame()
        card.setObjectName("voxCard")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(16, 14, 16, 14)
        c_lay.setSpacing(10)

        def _add_row(label: str, value: str) -> None:
            if not value:
                return
            row = QHBoxLayout()
            row.setSpacing(10)
            k = QLabel(label)
            k.setFixedWidth(120)
            k.setStyleSheet(f"color: {t['text_muted']}; font-size: 12px; font-weight: 600;")
            k.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setStyleSheet(f"color: {t['text_primary']}; font-size: 13px;")
            row.addWidget(k)
            row.addWidget(v, 1)
            c_lay.addLayout(row)

        src_accounts = ", ".join(self.meeting.source_accounts or []) or self.meeting.account_id or self.meeting.calendar_provider_label
        if self.meeting.calendar_name:
            src_accounts = f"{src_accounts} ({self.meeting.calendar_name})"
        _add_row("Calendar Source", src_accounts)
        _add_row("Platform", plat_label)
        org = self.meeting.organizer_name or self.meeting.organizer
        if org and self.meeting.organizer and self.meeting.organizer_name and org != self.meeting.organizer:
            org = f"{self.meeting.organizer_name} <{self.meeting.organizer}>"
        _add_row("Organizer", org)
        if self.meeting.attendees:
            att_preview = ", ".join(self.meeting.attendees[:12])
            if len(self.meeting.attendees) > 12:
                att_preview += f" (+{len(self.meeting.attendees) - 12} more)"
            _add_row("Attendees", att_preview)
        _add_row("Location", self.meeting.location)
        _add_row("Join URL", self.meeting.join_url)

        desc = _strip_html(self.meeting.description)
        if desc:
            _add_row("Agenda / Notes", desc[:1200] + ("..." if len(desc) > 1200 else ""))

        root.addWidget(card)
        root.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        if self.meeting.join_url:
            btn_join = QPushButton("Join Meeting")
            btn_join.setObjectName("fathomBtn")
            btn_join.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_join.clicked.connect(self._on_join)
            footer.addWidget(btn_join)

        if self.recording_id:
            btn_rec = QPushButton("Open Recording")
            btn_rec.setObjectName("dawnBtn")
            btn_rec.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_rec.clicked.connect(self._on_open_recording)
            footer.addWidget(btn_rec)

        btn_prep = QPushButton("Prepare Notetaker")
        btn_prep.setObjectName("secondaryBtn")
        btn_prep.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_prep.clicked.connect(self._on_prepare)
        footer.addWidget(btn_prep)

        footer.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.setObjectName("secondaryBtn")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        root.addLayout(footer)

    def _on_join(self) -> None:
        if hasattr(self.controller, "start_meeting_from_calendar"):
            self.controller.start_meeting_from_calendar(self.meeting)

    def _on_prepare(self) -> None:
        self.prepare_notetaker_clicked.emit(self.meeting)
        self.accept()

    def _on_open_recording(self) -> None:
        if self.recording_id:
            self.open_recording_clicked.emit(int(self.recording_id))
            self.accept()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        self.setStyleSheet(get_dialog_stylesheet(self._theme))


class CalendarPage(QWidget):
    """Dedicated Calendar Overview, Connected Accounts, Upcoming Meetings & Notetaker Bridge."""

    status_toast = pyqtSignal(str, str)
    prepare_notetaker_requested = pyqtSignal(object)
    open_meeting_recording_requested = pyqtSignal(int)

    def __init__(self, controller: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = str(getattr(controller, "theme", "light") or "light").lower()
        self._active_filter: str = "upcoming"  # today | tomorrow | week | upcoming
        self._view_mode: str = "agenda"  # agenda | timeline
        self._briefing_expanded: bool = False
        self._daily_summary_text: str = ""
        self._selected_account_filter: str = ""
        self._search_text: str = ""
        self._filter_buttons: Dict[str, QPushButton] = {}
        self._current_spotlight: Optional[CalendarMeeting] = None
        self._anchor_date: date = datetime.now().astimezone().date()
        self._week_show_weekends: bool = False
        self._inspected_meeting: Optional[CalendarMeeting] = None
        self._inspected_recording_id: Optional[int] = None
        self._inspected_brief_md: str = ""
        self._right_panel_mode: str = "inspector"
        self._user_pinned_right_mode: bool = False
        self._week_day_columns: List[Tuple[date, QFrame]] = []

        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(200)
        self._search_debounce_timer.timeout.connect(self._refresh_meetings_only)

        self._build_ui()
        self.apply_theme(self._theme)

        if hasattr(controller, "calendar_upcoming_changed"):
            controller.calendar_upcoming_changed.connect(lambda *_: self.refresh())
        if hasattr(controller, "calendar_accounts_changed"):
            controller.calendar_accounts_changed.connect(lambda *_: self.refresh())

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(60000)
        self._tick_timer.timeout.connect(self.refresh)
        self._tick_timer.start()

    def closeEvent(self, event: Any) -> None:
        for tmr_attr in ("_tick_timer", "_search_debounce_timer"):
            tmr = getattr(self, tmr_attr, None)
            if tmr is not None and tmr.isActive():
                tmr.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------ UI Build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(16)

        # ---- Page Header ----
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        lbl_eye = QLabel("CALENDAR ECOSYSTEM")
        lbl_eye.setObjectName("calEyebrow")
        title_col.addWidget(lbl_eye)

        self.lbl_title = QLabel("Calendar")
        self.lbl_title.setFont(get_ui_font(26, 600, serif=True))
        self.lbl_title.setAccessibleName("Calendar Workspace")
        title_col.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel(
            "Manage Outlook, Google Calendar, and Zoom accounts, inspect upcoming schedules, and connect meetings to Notetaker."
        )
        self.lbl_subtitle.setObjectName("calSub")
        self.lbl_subtitle.setWordWrap(True)
        title_col.addWidget(self.lbl_subtitle)
        hdr.addLayout(title_col, 1)

        self.btn_sync_now = QPushButton("Sync Now")
        self.btn_sync_now.setObjectName("calSecondaryBtn")
        self.btn_sync_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync_now.setAccessibleName("Sync all calendars now")
        self.btn_sync_now.clicked.connect(self._on_sync_all_clicked)
        hdr.addWidget(self.btn_sync_now)

        self.btn_add_account = QPushButton("+ Add Account")
        self.btn_add_account.setObjectName("calPrimaryBtn")
        self.btn_add_account.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_account.setAccessibleName("Add calendar account")
        self.btn_add_account.clicked.connect(lambda: self._open_connect_dialog())
        hdr.addWidget(self.btn_add_account)
        outer.addLayout(hdr)

        # ---- Top Clickable Account Health Pills Strip ----
        self.accounts_health_bar = QFrame()
        self.accounts_health_bar.setObjectName("calHealthStrip")
        health_outer = QHBoxLayout(self.accounts_health_bar)
        health_outer.setContentsMargins(12, 8, 12, 8)
        health_outer.setSpacing(8)
        self.accounts_health_lay = QHBoxLayout()
        self.accounts_health_lay.setSpacing(8)
        health_outer.addLayout(self.accounts_health_lay, 1)
        self.btn_toggle_accounts_panel = QPushButton("Accounts (0)")
        self.btn_toggle_accounts_panel.setCheckable(True)
        self.btn_toggle_accounts_panel.setObjectName("calMiniBtn")
        self.btn_toggle_accounts_panel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_accounts_panel.clicked.connect(
            lambda: self.set_right_panel_mode(
                "accounts" if self._right_panel_mode != "accounts" else "inspector",
                user_initiated=True,
            )
        )
        health_outer.addWidget(self.btn_toggle_accounts_panel)
        outer.addWidget(self.accounts_health_bar)

        # ---- Overview KPI Strip + Next Meeting Spotlight ----
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)

        # KPI 1: Today's Meetings
        self.card_kpi_today, self.lbl_kpi_today, self.lbl_kpi_today_sub = self._make_kpi_card(
            "TODAY'S MEETINGS", "0", "No events remaining today"
        )
        kpi_row.addWidget(self.card_kpi_today, 1)

        # KPI 2: Connected Accounts
        self.card_kpi_accounts, self.lbl_kpi_accounts, self.lbl_kpi_accounts_sub = self._make_kpi_card(
            "CONNECTED ACCOUNTS", "0", "Add Microsoft, Google, or Zoom"
        )
        kpi_row.addWidget(self.card_kpi_accounts, 1)

        # KPI 3: Sync Health
        self.card_kpi_health, self.lbl_kpi_health, self.lbl_kpi_health_sub = self._make_kpi_card(
            "SYNC HEALTH", "Idle", "Not synced yet"
        )
        kpi_row.addWidget(self.card_kpi_health, 1)

        # KPI 4 (Wide): Next Upcoming Meeting Spotlight
        self.spotlight_card = QFrame()
        self.spotlight_card.setObjectName("calSpotlightCard")
        sp_lay = QVBoxLayout(self.spotlight_card)
        sp_lay.setContentsMargins(16, 12, 16, 12)
        sp_lay.setSpacing(6)

        sp_top = QHBoxLayout()
        lbl_sp_eye = QLabel("NEXT UPCOMING MEETING")
        lbl_sp_eye.setObjectName("calKpiLabel")
        sp_top.addWidget(lbl_sp_eye)
        sp_top.addStretch(1)
        self.lbl_next_platform = QLabel("")
        self.lbl_next_platform.setObjectName("calPlatformPill")
        self.lbl_next_platform.setVisible(False)
        sp_top.addWidget(self.lbl_next_platform)
        sp_lay.addLayout(sp_top)

        self.lbl_next_title = QLabel("No upcoming meetings scheduled")
        self.lbl_next_title.setFont(get_ui_font(15, 600))
        self.lbl_next_title.setWordWrap(True)
        sp_lay.addWidget(self.lbl_next_title)

        self.lbl_next_meta = QLabel("Connect a calendar to see your schedule here.")
        self.lbl_next_meta.setObjectName("calMutedText")
        self.lbl_next_meta.setWordWrap(True)
        sp_lay.addWidget(self.lbl_next_meta)

        sp_act = QHBoxLayout()
        sp_act.setSpacing(8)
        self.btn_next_join = QPushButton("Join Meeting")
        self.btn_next_join.setObjectName("calFathomBtn")
        self.btn_next_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_join.setVisible(False)
        self.btn_next_join.clicked.connect(self._on_spotlight_join)
        sp_act.addWidget(self.btn_next_join)

        self.btn_next_prepare = QPushButton("Prepare Notetaker")
        self.btn_next_prepare.setObjectName("calDawnBtn")
        self.btn_next_prepare.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next_prepare.setVisible(False)
        self.btn_next_prepare.clicked.connect(self._on_spotlight_prepare)
        sp_act.addWidget(self.btn_next_prepare)
        sp_act.addStretch(1)
        sp_lay.addLayout(sp_act)

        kpi_row.addWidget(self.spotlight_card, 2)
        outer.addLayout(kpi_row)

        # ---- Main Two-Column Content Area ----
        body = QHBoxLayout()
        body.setSpacing(16)

        # LEFT COLUMN: Filter Bar + Search Row + Upcoming Meetings Feed
        left_col = QVBoxLayout()
        left_col.setSpacing(10)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)
        for fid, label in (
            ("today", "Today"),
            ("tomorrow", "Tomorrow"),
            ("week", "This Week"),
            ("upcoming", "Upcoming"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("calFilterBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, key=fid: self.set_time_filter(key))
            filter_bar.addWidget(btn)
            self._filter_buttons[fid] = btn

        filter_bar.addStretch(1)

        # View Mode Switcher: Agenda (Chronological List) vs Day Flow (Hourly Timeline)
        self.btn_view_agenda = QPushButton("Agenda")
        self.btn_view_agenda.setCheckable(True)
        self.btn_view_agenda.setChecked(True)
        self.btn_view_agenda.setObjectName("calFilterBtnActive")
        self.btn_view_agenda.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_agenda.clicked.connect(lambda: self.set_view_mode("agenda"))
        filter_bar.addWidget(self.btn_view_agenda)

        self.btn_view_timeline = QPushButton("Day Flow")
        self.btn_view_timeline.setCheckable(True)
        self.btn_view_timeline.setChecked(False)
        self.btn_view_timeline.setObjectName("calFilterBtn")
        self.btn_view_timeline.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_timeline.clicked.connect(lambda: self.set_view_mode("timeline"))
        filter_bar.addWidget(self.btn_view_timeline)

        self.btn_view_week = QPushButton("Week")
        self.btn_view_week.setCheckable(True)
        self.btn_view_week.setChecked(False)
        self.btn_view_week.setObjectName("calFilterBtn")
        self.btn_view_week.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_week.clicked.connect(lambda: self.set_view_mode("week"))
        filter_bar.addWidget(self.btn_view_week)

        self.btn_toggle_briefing = QPushButton("✦ Daily Pulse")
        self.btn_toggle_briefing.setCheckable(True)
        self.btn_toggle_briefing.setChecked(False)
        self.btn_toggle_briefing.setObjectName("calMiniBtn")
        self.btn_toggle_briefing.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_briefing.clicked.connect(self._toggle_briefing)
        filter_bar.addWidget(self.btn_toggle_briefing)

        left_col.addLayout(filter_bar)

        # ---- Date Navigation Bar (Day Flow & Week modes) ----
        self.date_nav_bar = QFrame()
        self.date_nav_bar.setObjectName("calNavStrip")
        self.date_nav_bar.setVisible(False)
        nav_lay = QHBoxLayout(self.date_nav_bar)
        nav_lay.setContentsMargins(8, 4, 8, 4)
        nav_lay.setSpacing(6)

        self.btn_nav_prev = QPushButton("‹")
        self.btn_nav_prev.setObjectName("calMiniBtn")
        self.btn_nav_prev.setFixedWidth(28)
        self.btn_nav_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_prev.clicked.connect(lambda: self._shift_anchor_date(-1))
        nav_lay.addWidget(self.btn_nav_prev)

        self.btn_nav_today = QPushButton("Today")
        self.btn_nav_today.setObjectName("calMiniBtn")
        self.btn_nav_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_today.clicked.connect(self._reset_anchor_today)
        nav_lay.addWidget(self.btn_nav_today)

        self.btn_nav_next = QPushButton("›")
        self.btn_nav_next.setObjectName("calMiniBtn")
        self.btn_nav_next.setFixedWidth(28)
        self.btn_nav_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_nav_next.clicked.connect(lambda: self._shift_anchor_date(1))
        nav_lay.addWidget(self.btn_nav_next)

        self.lbl_nav_range = QLabel("")
        self.lbl_nav_range.setFont(get_ui_font(12, 600))
        nav_lay.addWidget(self.lbl_nav_range)
        nav_lay.addStretch(1)

        self.btn_week_weekend = QPushButton("7-Day")
        self.btn_week_weekend.setCheckable(True)
        self.btn_week_weekend.setObjectName("calMiniBtn")
        self.btn_week_weekend.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_week_weekend.setVisible(False)
        self.btn_week_weekend.clicked.connect(self._toggle_week_weekends)
        nav_lay.addWidget(self.btn_week_weekend)

        left_col.addWidget(self.date_nav_bar)

        # ---- Daily Pulse & Executive Intelligence Strip ----
        self.pulse_container = QFrame()
        self.pulse_container.setObjectName("calCard")
        self.pulse_lay = QVBoxLayout(self.pulse_container)
        self.pulse_lay.setContentsMargins(14, 10, 14, 10)
        self.pulse_lay.setSpacing(8)

        self.pulse_metrics_row = QHBoxLayout()
        self.pulse_metrics_row.setSpacing(12)

        self.lbl_flow_time = QLabel("0h 0m Scheduled Today")
        self.lbl_flow_time.setFont(get_ui_font(12, 600))
        self.pulse_metrics_row.addWidget(self.lbl_flow_time)

        self.lbl_focus_time = QLabel("Focus: All Day")
        self.lbl_focus_time.setObjectName("calMutedText")
        self.pulse_metrics_row.addWidget(self.lbl_focus_time)

        self.lbl_longest_chunk = QLabel("Uninterrupted Deep Work")
        self.lbl_longest_chunk.setObjectName("calMutedText")
        self.pulse_metrics_row.addWidget(self.lbl_longest_chunk)

        self.lbl_buffer_status = QLabel("")
        self.lbl_buffer_status.setVisible(False)
        self.pulse_metrics_row.addWidget(self.lbl_buffer_status)

        self.pulse_metrics_row.addStretch(1)
        self.pulse_lay.addLayout(self.pulse_metrics_row)

        # Expandable Executive Daily Briefing & Recap Panel
        self.briefing_box = QWidget()
        self.briefing_box.setVisible(False)
        b_lay = QVBoxLayout(self.briefing_box)
        b_lay.setContentsMargins(0, 6, 0, 2)
        b_lay.setSpacing(8)

        b_hdr = QHBoxLayout()
        self.lbl_briefing_title = QLabel("DAILY EXECUTIVE BRIEFING & RECAP")
        self.lbl_briefing_title.setObjectName("calKpiLabel")
        b_hdr.addWidget(self.lbl_briefing_title)
        b_hdr.addStretch(1)

        self.btn_copy_briefing = QPushButton("Copy Summary")
        self.btn_copy_briefing.setObjectName("calMiniBtn")
        self.btn_copy_briefing.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_briefing.clicked.connect(self._copy_daily_summary)
        b_hdr.addWidget(self.btn_copy_briefing)
        b_lay.addLayout(b_hdr)

        self.lbl_briefing_content = QLabel("Analyzing your daily flow...")
        self.lbl_briefing_content.setObjectName("calMutedText")
        self.lbl_briefing_content.setWordWrap(True)
        b_lay.addWidget(self.lbl_briefing_content)

        self.pulse_lay.addWidget(self.briefing_box)
        left_col.addWidget(self.pulse_container)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.cmb_account_filter = QComboBox()
        self.cmb_account_filter.setObjectName("calCombo")
        self.cmb_account_filter.setMinimumWidth(190)
        self.cmb_account_filter.setMaximumWidth(260)
        self.cmb_account_filter.addItem("All Accounts", "")
        self.cmb_account_filter.currentIndexChanged.connect(self._on_account_filter_changed)
        search_row.addWidget(self.cmb_account_filter)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("calSearchInput")
        self.txt_search.setPlaceholderText("Search title, host, or platform...")
        self.txt_search.setMinimumWidth(180)
        self.txt_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.txt_search, 1)

        left_col.addLayout(search_row)

        self.meetings_scroll = QScrollArea()
        self.meetings_scroll.setWidgetResizable(True)
        self.meetings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.meetings_host = QWidget()
        self.meetings_host.setObjectName("calScrollHost")
        self.meetings_lay = QVBoxLayout(self.meetings_host)
        self.meetings_lay.setContentsMargins(0, 2, 6, 8)
        self.meetings_lay.setSpacing(10)
        self.meetings_scroll.setWidget(self.meetings_host)
        left_col.addWidget(self.meetings_scroll, 1)

        body.addLayout(left_col, 7)

        # RIGHT COLUMN: Contextual Meeting Inspector / Accounts Manager Stack
        right_card = QFrame()
        right_card.setObjectName("calSidePanel")
        right_card.setMinimumWidth(300)
        right_card.setMaximumWidth(400)
        r_lay = QVBoxLayout(right_card)
        r_lay.setContentsMargins(16, 14, 16, 14)
        r_lay.setSpacing(10)

        r_hdr = QHBoxLayout()
        r_hdr.setSpacing(6)
        self.btn_right_inspector = QPushButton("Inspector")
        self.btn_right_inspector.setCheckable(True)
        self.btn_right_inspector.setChecked(True)
        self.btn_right_inspector.setObjectName("calFilterBtnActive")
        self.btn_right_inspector.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_right_inspector.clicked.connect(lambda: self.set_right_panel_mode("inspector", user_initiated=True))
        r_hdr.addWidget(self.btn_right_inspector)

        self.btn_right_accounts = QPushButton("Accounts")
        self.btn_right_accounts.setCheckable(True)
        self.btn_right_accounts.setChecked(False)
        self.btn_right_accounts.setObjectName("calFilterBtn")
        self.btn_right_accounts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_right_accounts.clicked.connect(lambda: self.set_right_panel_mode("accounts", user_initiated=True))
        r_hdr.addWidget(self.btn_right_accounts)

        r_hdr.addStretch(1)
        self.btn_side_add = QPushButton("+ Connect")
        self.btn_side_add.setObjectName("calDawnBtn")
        self.btn_side_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_side_add.clicked.connect(lambda: self._open_connect_dialog())
        r_hdr.addWidget(self.btn_side_add)
        r_lay.addLayout(r_hdr)

        self.right_panel_stack = QStackedWidget()

        # Page 0: Contextual Meeting Inspector + Local AI Carry-Over Brief
        self.inspector_page = QWidget()
        insp_outer = QVBoxLayout(self.inspector_page)
        insp_outer.setContentsMargins(0, 0, 0, 0)
        insp_outer.setSpacing(8)

        insp_scroll = QScrollArea()
        insp_scroll.setWidgetResizable(True)
        insp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        insp_host = QWidget()
        insp_host.setObjectName("calScrollHost")
        insp_lay = QVBoxLayout(insp_host)
        insp_lay.setContentsMargins(0, 2, 4, 6)
        insp_lay.setSpacing(10)

        insp_meta_card = QFrame()
        insp_meta_card.setObjectName("calInspectorCard")
        im_lay = QVBoxLayout(insp_meta_card)
        im_lay.setContentsMargins(14, 12, 14, 12)
        im_lay.setSpacing(6)

        self.lbl_insp_platform = QLabel("")
        self.lbl_insp_platform.setVisible(False)
        im_lay.addWidget(self.lbl_insp_platform, 0, Qt.AlignmentFlag.AlignLeft)

        self.lbl_insp_title = QLabel("Select a meeting to inspect")
        self.lbl_insp_title.setFont(get_ui_font(15, 600))
        self.lbl_insp_title.setWordWrap(True)
        im_lay.addWidget(self.lbl_insp_title)

        self.lbl_insp_time = QLabel("Click any meeting in Agenda, Day Flow, or Week Grid.")
        self.lbl_insp_time.setObjectName("calMutedText")
        self.lbl_insp_time.setWordWrap(True)
        im_lay.addWidget(self.lbl_insp_time)

        self.lbl_insp_source = QLabel("")
        self.lbl_insp_source.setObjectName("calMutedText")
        self.lbl_insp_source.setWordWrap(True)
        self.lbl_insp_source.setVisible(False)
        im_lay.addWidget(self.lbl_insp_source)

        self.lbl_insp_attendees = QLabel("")
        self.lbl_insp_attendees.setObjectName("calMutedText")
        self.lbl_insp_attendees.setWordWrap(True)
        self.lbl_insp_attendees.setVisible(False)
        im_lay.addWidget(self.lbl_insp_attendees)

        insp_btns = QHBoxLayout()
        insp_btns.setSpacing(6)
        self.btn_insp_join = QPushButton("Join Meeting")
        self.btn_insp_join.setObjectName("calFathomBtn")
        self.btn_insp_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insp_join.setVisible(False)
        self.btn_insp_join.clicked.connect(
            lambda: self._on_join_meeting(self._inspected_meeting) if self._inspected_meeting else None
        )
        insp_btns.addWidget(self.btn_insp_join)

        self.btn_insp_prepare = QPushButton("Prepare Notetaker")
        self.btn_insp_prepare.setObjectName("calDawnBtn")
        self.btn_insp_prepare.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insp_prepare.setEnabled(False)
        self.btn_insp_prepare.clicked.connect(
            lambda: self._on_prepare_notetaker(self._inspected_meeting) if self._inspected_meeting else None
        )
        insp_btns.addWidget(self.btn_insp_prepare)
        im_lay.addLayout(insp_btns)

        insp_btns2 = QHBoxLayout()
        insp_btns2.setSpacing(6)
        self.btn_insp_recording = QPushButton("View Recording")
        self.btn_insp_recording.setObjectName("calSecondaryBtn")
        self.btn_insp_recording.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insp_recording.setVisible(False)
        self.btn_insp_recording.clicked.connect(
            lambda: self.open_meeting_recording_requested.emit(int(self._inspected_recording_id))
            if self._inspected_recording_id
            else None
        )
        insp_btns2.addWidget(self.btn_insp_recording)

        self.btn_insp_details = QPushButton("Full Details")
        self.btn_insp_details.setObjectName("calMiniBtn")
        self.btn_insp_details.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insp_details.setEnabled(False)
        self.btn_insp_details.clicked.connect(
            lambda: self._open_meeting_details(self._inspected_meeting, self._inspected_recording_id)
            if self._inspected_meeting
            else None
        )
        insp_btns2.addWidget(self.btn_insp_details)
        insp_btns2.addStretch(1)
        im_lay.addLayout(insp_btns2)
        insp_lay.addWidget(insp_meta_card)

        self.insp_brief_card = QFrame()
        self.insp_brief_card.setObjectName("calInspectorCard")
        ib_lay = QVBoxLayout(self.insp_brief_card)
        ib_lay.setContentsMargins(14, 12, 14, 12)
        ib_lay.setSpacing(8)

        ib_hdr = QHBoxLayout()
        lbl_brief_eye = QLabel("AI PRE-MEETING BRIEF")
        lbl_brief_eye.setObjectName("calKpiLabel")
        ib_hdr.addWidget(lbl_brief_eye)
        ib_hdr.addStretch(1)
        self.btn_insp_copy_brief = QPushButton("Copy Brief")
        self.btn_insp_copy_brief.setObjectName("calMiniBtn")
        self.btn_insp_copy_brief.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insp_copy_brief.setEnabled(False)
        self.btn_insp_copy_brief.clicked.connect(self._copy_inspected_brief)
        ib_hdr.addWidget(self.btn_insp_copy_brief)
        ib_lay.addLayout(ib_hdr)

        self.lbl_insp_brief_meta = QLabel("Prior context from your local SQLite meetings.")
        self.lbl_insp_brief_meta.setObjectName("calMutedText")
        self.lbl_insp_brief_meta.setWordWrap(True)
        ib_lay.addWidget(self.lbl_insp_brief_meta)

        self.txt_insp_brief = QLabel("Select an upcoming meeting to generate a local carry-over brief.")
        self.txt_insp_brief.setWordWrap(True)
        self.txt_insp_brief.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.txt_insp_brief.setFont(get_ui_font(12, 400))
        ib_lay.addWidget(self.txt_insp_brief)

        insp_lay.addWidget(self.insp_brief_card)
        insp_lay.addStretch(1)
        insp_scroll.setWidget(insp_host)
        insp_outer.addWidget(insp_scroll, 1)
        self.right_panel_stack.addWidget(self.inspector_page)

        # Page 1: Connected Accounts Manager (preserved cleanly)
        self.accounts_page = QWidget()
        acc_p_lay = QVBoxLayout(self.accounts_page)
        acc_p_lay.setContentsMargins(0, 0, 0, 0)
        self.accounts_scroll = QScrollArea()
        self.accounts_scroll.setWidgetResizable(True)
        self.accounts_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.accounts_host = QWidget()
        self.accounts_host.setObjectName("calScrollHost")
        self.accounts_lay = QVBoxLayout(self.accounts_host)
        self.accounts_lay.setContentsMargins(0, 2, 4, 6)
        self.accounts_lay.setSpacing(10)
        self.accounts_scroll.setWidget(self.accounts_host)
        acc_p_lay.addWidget(self.accounts_scroll, 1)
        self.right_panel_stack.addWidget(self.accounts_page)

        r_lay.addWidget(self.right_panel_stack, 1)
        body.addWidget(right_card, 4)
        outer.addLayout(body, 1)

        self._sync_filter_button_states()

    def _make_kpi_card(self, label: str, value: str, sub: str) -> Tuple[QFrame, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("calCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        lbl_k = QLabel(label)
        lbl_k.setObjectName("calKpiLabel")
        lbl_v = QLabel(value)
        lbl_v.setFont(get_ui_font(22, 600, serif=True))
        lbl_s = QLabel(sub)
        lbl_s.setObjectName("calMutedText")
        lbl_s.setWordWrap(True)
        lay.addWidget(lbl_k)
        lay.addWidget(lbl_v)
        lay.addWidget(lbl_s)
        lay.addStretch(1)
        return card, lbl_v, lbl_s

    # ------------------------------------------------------------------ Filters
    def set_time_filter(self, filter_id: str) -> None:
        if filter_id not in self._filter_buttons:
            filter_id = "upcoming"
        self._active_filter = filter_id
        today_d = datetime.now().astimezone().date()
        if filter_id == "tomorrow":
            self._anchor_date = today_d + timedelta(days=1)
        else:
            self._anchor_date = today_d
        self._sync_filter_button_states()
        if hasattr(self, "btn_week_weekend"):
            self.btn_week_weekend.setVisible(self._view_mode == "week" or (self._view_mode == "timeline" and filter_id == "week"))
        self.refresh()

    def _sync_filter_button_states(self) -> None:
        for fid, btn in self._filter_buttons.items():
            active = (fid == self._active_filter)
            btn.setChecked(active)
            btn.setObjectName("calFilterBtnActive" if active else "calFilterBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _style_toggle_btn(self, btn: Optional[QPushButton], active: bool) -> None:
        if btn is None:
            return
        btn.setChecked(active)
        btn.setObjectName("calFilterBtnActive" if active else "calFilterBtn")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def set_view_mode(self, mode: str) -> None:
        if mode not in ("agenda", "timeline", "week"):
            mode = "agenda"
        self._view_mode = mode
        self._style_toggle_btn(getattr(self, "btn_view_agenda", None), mode == "agenda")
        self._style_toggle_btn(getattr(self, "btn_view_timeline", None), mode == "timeline")
        self._style_toggle_btn(getattr(self, "btn_view_week", None), mode == "week")
        if hasattr(self, "date_nav_bar"):
            self.date_nav_bar.setVisible(mode in ("timeline", "week"))
        if hasattr(self, "btn_week_weekend"):
            self.btn_week_weekend.setVisible(mode == "week" or (mode == "timeline" and self._active_filter == "week"))
        self.refresh()

    def _shift_anchor_date(self, delta_steps: int) -> None:
        is_week = (self._view_mode == "week") or (self._view_mode == "timeline" and self._active_filter == "week")
        days = (7 * delta_steps) if is_week else delta_steps
        self._anchor_date = self._anchor_date + timedelta(days=days)
        self._refresh_meetings_only()

    def _reset_anchor_today(self) -> None:
        self._anchor_date = datetime.now().astimezone().date()
        self._refresh_meetings_only()

    def _toggle_week_weekends(self) -> None:
        if hasattr(self, "btn_week_weekend"):
            self._week_show_weekends = bool(self.btn_week_weekend.isChecked())
        self._refresh_meetings_only()

    def set_right_panel_mode(self, mode: str, user_initiated: bool = True) -> None:
        clean_mode = "accounts" if str(mode).lower() == "accounts" else "inspector"
        self._right_panel_mode = clean_mode
        if user_initiated:
            self._user_pinned_right_mode = True
        if hasattr(self, "right_panel_stack"):
            self.right_panel_stack.setCurrentIndex(1 if clean_mode == "accounts" else 0)
        self._style_toggle_btn(getattr(self, "btn_right_inspector", None), clean_mode == "inspector")
        self._style_toggle_btn(getattr(self, "btn_right_accounts", None), clean_mode == "accounts")
        toggle_btn = getattr(self, "btn_toggle_accounts_panel", None)
        if toggle_btn is not None:
            is_acc = (clean_mode == "accounts")
            toggle_btn.setChecked(is_acc)
            toggle_btn.setObjectName("calFilterBtnActive" if is_acc else "calMiniBtn")
            toggle_btn.style().unpolish(toggle_btn)
            toggle_btn.style().polish(toggle_btn)

    def inspect_meeting(self, meeting: Optional[CalendarMeeting], recording_id: Optional[int] = None) -> None:
        """Select a calendar meeting in the Contextual Inspector and show the Inspector tab."""
        self._populate_inspector(meeting, recording_id)
        self.set_right_panel_mode("inspector", user_initiated=True)

    def _populate_inspector(self, meeting: Optional[CalendarMeeting], recording_id: Optional[int] = None) -> None:
        self._inspected_meeting = meeting
        self._inspected_recording_id = int(recording_id) if recording_id else None
        self._inspected_brief_md = ""
        if not hasattr(self, "lbl_insp_title"):
            return

        t = get_theme_tokens(self._theme)
        if meeting is None:
            self.lbl_insp_platform.setVisible(False)
            self.lbl_insp_title.setText("Select a meeting to inspect")
            self.lbl_insp_time.setText("Click any meeting in Agenda, Day Flow, or Week Grid.")
            self.lbl_insp_source.setVisible(False)
            self.lbl_insp_attendees.setVisible(False)
            self.btn_insp_join.setVisible(False)
            self.btn_insp_prepare.setEnabled(False)
            self.btn_insp_recording.setVisible(False)
            self.btn_insp_details.setEnabled(False)
            self.btn_insp_copy_brief.setEnabled(False)
            self.lbl_insp_brief_meta.setText("Prior context from your local SQLite meetings.")
            self.txt_insp_brief.setText("Select an upcoming meeting to generate a local carry-over brief.")
            return

        plat_name, plat_color = _platform_display_info(meeting.platform, t)
        self.lbl_insp_platform.setText(plat_name)
        self.lbl_insp_platform.setStyleSheet(
            f"background: {plat_color}; color: #FFFFFF; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        self.lbl_insp_platform.setVisible(True)
        self.lbl_insp_title.setText(meeting.title or "Untitled Meeting")

        time_str, rel_str = _format_time_window(meeting)
        date_str = meeting.local_start_time.strftime("%a, %b %d, %Y")
        self.lbl_insp_time.setText(f"{date_str} · {time_str}" + (f" ({rel_str})" if rel_str else ""))

        src_parts = []
        src_acc = (meeting.source_accounts[0] if meeting.source_accounts else "") or meeting.calendar_provider_label
        if src_acc:
            src_parts.append(src_acc)
        org = meeting.organizer_name or meeting.organizer
        if org:
            src_parts.append(f"Host: {org}")
        if src_parts:
            self.lbl_insp_source.setText(" · ".join(src_parts))
            self.lbl_insp_source.setVisible(True)
        else:
            self.lbl_insp_source.setVisible(False)

        atts = list(meeting.attendees or [])
        if atts:
            preview = ", ".join(atts[:8]) + (f" (+{len(atts) - 8} more)" if len(atts) > 8 else "")
            self.lbl_insp_attendees.setText(f"Attendees ({len(atts)}): {preview}")
            self.lbl_insp_attendees.setVisible(True)
        else:
            self.lbl_insp_attendees.setVisible(False)

        self.btn_insp_join.setVisible(bool(meeting.join_url))
        self.btn_insp_prepare.setEnabled(True)
        self.btn_insp_recording.setVisible(bool(recording_id))
        self.btn_insp_details.setEnabled(True)

        # Generate offline AI Pre-Meeting Carry-Over Brief from local SQLite history
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        has_real_db = bool(
            db is not None
            and hasattr(db, "list_meetings")
            and isinstance(db_path, (str, os.PathLike if hasattr(os, "PathLike") else str))
            and (":memory:" in str(db_path).lower() or Path(str(db_path)).exists())
        )
        if db is not None and hasattr(db, "list_meetings") and not has_real_db:
            try:
                sample = db.list_meetings(limit=1)
                has_real_db = isinstance(sample, list)
            except Exception:
                has_real_db = False

        if not has_real_db:
            self.lbl_insp_brief_meta.setText("Local SQLite history ready after your first recorded call.")
            self.txt_insp_brief.setText("Prior decisions and unresolved action items for matching calls will appear here.")
            self.btn_insp_copy_brief.setEnabled(False)
            return

        try:
            from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine

            brief = MeetingIntelligenceEngine(db).generate_pre_meeting_briefing(meeting.title or "")
            rel_mtgs = brief.get("relevant_meetings") or []
            decs = brief.get("key_decisions") or brief.get("prior_decisions") or []
            acts = brief.get("open_action_items") or []
            self._inspected_brief_md = str(brief.get("briefing_markdown") or "").strip()

            self.lbl_insp_brief_meta.setText(
                f"Matched {len(rel_mtgs)} prior meeting{'s' if len(rel_mtgs) != 1 else ''} · "
                f"{len(decs)} decision{'s' if len(decs) != 1 else ''} · "
                f"{len(acts)} open action{'s' if len(acts) != 1 else ''}"
            )
            lines: List[str] = []
            if decs:
                lines.append("Prior Decisions:")
                for d in decs[:3]:
                    lines.append(f"• {d.get('decision', '')}")
            if acts:
                lines.append("Open Action Items:")
                for a in acts[:4]:
                    owner = a.get("owner") or "Team"
                    lines.append(f"• [{owner}] {a.get('description', '')}")
            if not lines:
                lines.append("No prior decisions or open action items matched this meeting title.")
            self.txt_insp_brief.setText("\n".join(lines))
            self.btn_insp_copy_brief.setEnabled(bool(self._inspected_brief_md))
        except Exception as exc:
            logger.debug("Inspector carry-over brief skipped: %s", exc)
            self.lbl_insp_brief_meta.setText("Pre-meeting brief unavailable.")
            self.txt_insp_brief.setText("Click 'Prepare Notetaker' to record this meeting locally.")
            self.btn_insp_copy_brief.setEnabled(False)

    def _copy_inspected_brief(self) -> None:
        txt = self._inspected_brief_md or (self.txt_insp_brief.text() if hasattr(self, "txt_insp_brief") else "")
        if not txt:
            return
        try:
            QGuiApplication.clipboard().setText(txt)
            ok = True
        except Exception:
            ok = copy_to_clipboard_win32(txt)
        if ok:
            self.status_toast.emit("Brief Copied", "Pre-Meeting Brief copied to clipboard.")
        else:
            self.status_toast.emit("Copy Failed", "Could not copy Pre-Meeting Brief to clipboard.")

    def _toggle_briefing(self) -> None:
        self._briefing_expanded = not self._briefing_expanded
        if hasattr(self, "briefing_box"):
            self.briefing_box.setVisible(self._briefing_expanded)
        if hasattr(self, "btn_toggle_briefing"):
            self.btn_toggle_briefing.setChecked(self._briefing_expanded)
            self.btn_toggle_briefing.setObjectName("calFilterBtnActive" if self._briefing_expanded else "calMiniBtn")
            self.btn_toggle_briefing.style().unpolish(self.btn_toggle_briefing)
            self.btn_toggle_briefing.style().polish(self.btn_toggle_briefing)

    def _copy_daily_summary(self) -> None:
        txt = self._daily_summary_text or getattr(self.lbl_briefing_content, "text", lambda: "")()
        ok = False
        try:
            QGuiApplication.clipboard().setText(txt)
            ok = True
        except Exception:
            ok = copy_to_clipboard_win32(txt)
        if ok:
            self.status_toast.emit("Copied Daily Executive Briefing to clipboard!", "success")
        else:
            self.status_toast.emit("Failed copying briefing to clipboard.", "error")

    def _on_account_filter_changed(self, _idx: int) -> None:
        self._selected_account_filter = str(self.cmb_account_filter.currentData() or "")
        self.refresh()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._search_debounce_timer.start()

    def _refresh_meetings_only(self) -> None:
        """Re-render only the filtered meetings panel without rebuilding accounts, KPIs, or Daily Pulse."""
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        accounts = self._get_accounts()
        meetings = self._get_all_meetings()
        recording_map = self._get_recording_map()
        self._render_meetings_panel(accounts, meetings, recording_map)

    # ------------------------------------------------------------------ Data & Render
    def _get_accounts(self) -> List[Dict[str, Any]]:
        if hasattr(self.controller, "list_connected_calendar_accounts"):
            try:
                return list(self.controller.list_connected_calendar_accounts() or [])
            except Exception as exc:
                logger.debug("list_connected_calendar_accounts error: %s", exc)
        return []

    def _get_sync_health(self) -> Dict[str, Any]:
        if hasattr(self.controller, "get_calendar_sync_health"):
            try:
                return dict(self.controller.get_calendar_sync_health() or {})
            except Exception as exc:
                logger.debug("get_calendar_sync_health error: %s", exc)
        return {}

    def _get_all_meetings(self) -> List[CalendarMeeting]:
        engine = getattr(self.controller, "calendar_engine", None)
        if engine is not None and hasattr(engine, "get_upcoming_meetings"):
            try:
                return list(engine.get_upcoming_meetings() or [])
            except Exception as exc:
                logger.debug("calendar_engine.get_upcoming_meetings error: %s", exc)
        return []

    def _get_recording_map(self) -> Dict[str, int]:
        db = getattr(self.controller, "db", None)
        if db is None or not hasattr(db, "list_meetings_by_calendar_event_ids"):
            return {}
        try:
            user_id = getattr(getattr(self.controller, "config", None), "current_user_id", "default") or "default"
            return dict(db.list_meetings_by_calendar_event_ids(user_id=user_id) or {})
        except Exception:
            return {}

    def refresh(self) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        accounts = self._get_accounts()
        health = self._get_sync_health()
        meetings = self._get_all_meetings()
        recording_map = self._get_recording_map()

        self._render_kpis_and_spotlight(accounts, health, meetings)
        self._render_daily_pulse(meetings)
        self._sync_account_dropdown(accounts)
        self._render_accounts_panel(accounts)
        self._render_meetings_panel(accounts, meetings, recording_map)

        if not self._user_pinned_right_mode:
            target_mode = "accounts" if (not accounts and not meetings) else "inspector"
            self.set_right_panel_mode(target_mode, user_initiated=False)

        target_insp = self._inspected_meeting
        preserved_rid = self._inspected_recording_id
        if self._inspected_meeting is not None:
            for m in meetings:
                if m.id == self._inspected_meeting.id:
                    target_insp = m
                    break
        if target_insp is None:
            target_insp = self._current_spotlight or (meetings[0] if meetings else None)
        resolved_rid = (
            recording_map.get(target_insp.id, preserved_rid if target_insp is self._inspected_meeting else None)
            if target_insp
            else None
        )
        self._populate_inspector(target_insp, resolved_rid)

    def _render_daily_pulse(self, meetings: List[CalendarMeeting]) -> None:
        if not hasattr(self, "pulse_container"):
            return
        today_m = [m for m in meetings if m.is_today and not m.is_cancelled]
        today_m.sort(key=lambda m: m.local_start_time)
        now_local = datetime.now().astimezone()
        cur_hour = now_local.hour

        if not today_m:
            self.lbl_flow_time.setText("0h 0m Scheduled Today")
            self.lbl_focus_time.setText("Focus: Entire Day (Deep Work)")
            self.lbl_longest_chunk.setText("No meeting interruptions")
            self.lbl_buffer_status.setVisible(False)
            self.lbl_briefing_title.setText("DAILY EXECUTIVE BRIEFING")
            self.lbl_briefing_content.setText(
                "Your schedule is completely clear today! Great opportunity for uninterrupted deep focus, "
                "writing, strategy, or catching up on offline tasks."
            )
            self._daily_summary_text = (
                f"Daily Executive Briefing ({now_local.strftime('%A, %B %d, %Y')}):\n"
                "- Schedule: Clear day. No meetings scheduled.\n"
                "- Focus Time: Full workday available for deep work."
            )
            return

        total_mins = sum(max(5, int((m.local_end_time - m.local_start_time).total_seconds() // 60)) for m in today_m)
        hrs = total_mins // 60
        mins = total_mins % 60
        dur_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
        self.lbl_flow_time.setText(f"{dur_str} Scheduled ({len(today_m)} call{'s' if len(today_m) != 1 else ''})")

        # Focus time in 8-hour workday (480 mins)
        focus_mins = max(0, 480 - total_mins)
        f_hrs = focus_mins // 60
        f_mins = focus_mins % 60
        self.lbl_focus_time.setText(f"Focus: {f_hrs}h {f_mins}m Available")

        # Longest focus block between 9 AM and 6 PM
        day_start = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
        day_end = now_local.replace(hour=18, minute=0, second=0, microsecond=0)
        free_intervals: List[Tuple[datetime, datetime]] = []
        cur_marker = day_start

        for m in today_m:
            ms = max(day_start, m.local_start_time)
            me = min(day_end, m.local_end_time)
            if ms > cur_marker:
                free_intervals.append((cur_marker, ms))
            cur_marker = max(cur_marker, me)
        if cur_marker < day_end:
            free_intervals.append((cur_marker, day_end))

        if free_intervals:
            longest_span = max(free_intervals, key=lambda iv: (iv[1] - iv[0]).total_seconds())
            span_mins = int((longest_span[1] - longest_span[0]).total_seconds() // 60)
            s_h = span_mins // 60
            s_m = span_mins % 60
            span_str = f"{s_h}h {s_m}m" if s_h else f"{s_m}m"
            self.lbl_longest_chunk.setText(f"Longest Focus: {span_str} ({longest_span[0].strftime('%I:%M %p').lstrip('0')} – {longest_span[1].strftime('%I:%M %p').lstrip('0')})")
        else:
            self.lbl_longest_chunk.setText("Dense meeting schedule across today")

        # Back-to-back detection (< 5 min buffer)
        b2b_alerts = []
        for i in range(len(today_m) - 1):
            m1 = today_m[i]
            m2 = today_m[i + 1]
            gap = (m2.local_start_time - m1.local_end_time).total_seconds() / 60.0
            if 0 <= gap < 5:
                b2b_alerts.append(f"'{m1.title[:16]}' & '{m2.title[:16]}' ({int(gap)}m gap)")

        if b2b_alerts:
            self.lbl_buffer_status.setText(f"⚠️ Back-to-back: {', '.join(b2b_alerts[:2])}")
            self.lbl_buffer_status.setStyleSheet("color: #E06C75; font-size: 11px; font-weight: 600;")
            self.lbl_buffer_status.setVisible(True)
        else:
            self.lbl_buffer_status.setVisible(False)

        # Executive Briefing & Recap text
        if cur_hour < 12:
            phase_title = "☀️ MORNING EXECUTIVE BRIEFING"
            phase_intro = f"You have {len(today_m)} meeting{'s' if len(today_m) != 1 else ''} scheduled today totaling {dur_str}."
        elif cur_hour < 17:
            phase_title = "⚡ MIDDAY FLOW & PRIORITIES"
            completed = sum(1 for m in today_m if m.local_end_time < now_local)
            phase_intro = f"Midday update: {completed} of {len(today_m)} meetings completed. Total meeting load is {dur_str}."
        else:
            phase_title = "🌙 EVENING RECAP & WRAP-UP"
            phase_intro = f"Daily wrap-up: {len(today_m)} meeting{'s' if len(today_m) != 1 else ''} on today's calendar."

        self.lbl_briefing_title.setText(phase_title)

        brief_lines = [phase_intro, ""]
        summary_markdown_lines = [
            f"# {phase_title.title()} — {now_local.strftime('%A, %B %d, %Y')}",
            f"{phase_intro}\n",
            "### Scheduled Meetings:",
        ]

        all_attendees: List[str] = []
        for idx, m in enumerate(today_m, 1):
            t_str, r_str = _format_time_window(m)
            p_label = m.meeting_platform_label
            org = m.organizer_name or m.organizer or "Host"
            line = f"{idx}. {t_str} — {m.title} ({p_label}, Host: {org})"
            brief_lines.append(f"• {m.local_start_time.strftime('%I:%M %p').lstrip('0')} — {m.title} ({p_label})")
            summary_markdown_lines.append(f"- **{t_str}**: {m.title} [{p_label}]")
            for att in (m.attendees or []):
                if att not in all_attendees:
                    all_attendees.append(att)

        if all_attendees:
            brief_lines.append(f"\nKey Attendees ({len(all_attendees)}): {', '.join(all_attendees[:8])}{'...' if len(all_attendees) > 8 else ''}")
            brief_lines.append("VOX Notetaker has pre-primed acoustic vocabulary for these participants.")
            summary_markdown_lines.append(f"\n**Key Attendees**: {', '.join(all_attendees[:10])}")

        brief_lines.append("\n💡 1-click Join and Notetaker capture are ready for your upcoming calls.")
        self.lbl_briefing_content.setText("\n".join(brief_lines))
        self._daily_summary_text = "\n".join(summary_markdown_lines)

    def _render_kpis_and_spotlight(
        self,
        accounts: List[Dict[str, Any]],
        health: Dict[str, Any],
        meetings: List[CalendarMeeting],
    ) -> None:
        t = get_theme_tokens(self._theme)
        today_meetings = [m for m in meetings if m.is_today]
        self.lbl_kpi_today.setText(str(len(today_meetings)))
        if today_meetings:
            live_cnt = sum(1 for m in today_meetings if m.is_happening_now)
            self.lbl_kpi_today_sub.setText(
                f"{live_cnt} in progress right now" if live_cnt else f"Next at {today_meetings[0].local_start_time.strftime('%I:%M %p').lstrip('0')}"
            )
        else:
            self.lbl_kpi_today_sub.setText("Clear schedule for today")

        total_accounts = len(accounts)
        active_accounts = [
            a for a in accounts
            if a.get("enabled", True)
            and (a.get("is_authenticated") or str(a.get("connection_state") or "").lower() in {"connected", "syncing", "offline_cached"})
        ]
        paused_accounts = [a for a in accounts if not a.get("enabled", True)]

        if total_accounts == 0:
            self.lbl_kpi_accounts.setText("0")
            self.lbl_kpi_accounts_sub.setText("Connect Outlook, Google, or Zoom")
        elif paused_accounts and not active_accounts:
            self.lbl_kpi_accounts.setText(f"{total_accounts} Paused")
            self.lbl_kpi_accounts_sub.setText("Sync paused by user")
        elif paused_accounts:
            self.lbl_kpi_accounts.setText(f"{len(active_accounts)} Active")
            self.lbl_kpi_accounts_sub.setText(f"{len(paused_accounts)} paused · {total_accounts} total")
        else:
            self.lbl_kpi_accounts.setText(str(len(active_accounts)))
            providers = []
            for a in accounts:
                _, p_name = _provider_display_info(str(a.get("provider_type", "")))
                if p_name not in providers:
                    providers.append(p_name)
            self.lbl_kpi_accounts_sub.setText(", ".join(providers[:3]))

        state = str(health.get("state") or ("connected" if active_accounts else ("paused" if paused_accounts else "not_connected"))).lower()
        health_map = {
            "connected": "Healthy",
            "healthy": "Healthy",
            "offline_cached": "Cached (Offline)",
            "syncing": "Syncing...",
            "paused": "Paused",
            "partial": "Partial Issue",
            "degraded": "Partial Issue",
            "attention_needed": "Reconnect Needed",
            "expired": "Reconnect Needed",
            "failed": "Attention Needed",
            "disconnected": "Not Connected",
            "not_connected": "Not Connected",
        }
        self.lbl_kpi_health.setText(health_map.get(state, state.replace("_", " ").title()))
        if state == "paused":
            self.lbl_kpi_health_sub.setText("Sync paused by user")
        elif state == "offline_cached":
            self.lbl_kpi_health_sub.setText("Working offline with local cache")
        else:
            self.lbl_kpi_health_sub.setText(_format_last_sync(health.get("last_sync_at")))

        # Spotlight next upcoming or currently live meeting
        now_local = datetime.now().astimezone()
        candidates = [m for m in meetings if m.local_end_time >= now_local]
        nxt = candidates[0] if candidates else (meetings[0] if meetings else None)
        self._current_spotlight = nxt

        if nxt is None:
            self.lbl_next_title.setText("No upcoming meetings scheduled")
            self.lbl_next_meta.setText(
                "Connect an Outlook, Google, or Zoom account to see your next call here."
                if not accounts else "Your calendar is clear for the upcoming window."
            )
            self.lbl_next_platform.setVisible(False)
            self.btn_next_join.setVisible(False)
            self.btn_next_prepare.setVisible(False)
            return

        plat_name, plat_color = _platform_display_info(nxt.platform, t)
        time_str, rel_str = _format_time_window(nxt)
        self.lbl_next_title.setText(nxt.title or "Untitled Meeting")
        src = (nxt.source_accounts[0] if nxt.source_accounts else "") or nxt.calendar_provider_label
        meta_parts = [p for p in (rel_str, time_str, src) if p]
        self.lbl_next_meta.setText("  ·  ".join(meta_parts))

        self.lbl_next_platform.setText(plat_name)
        self.lbl_next_platform.setStyleSheet(
            f"background: {plat_color}; color: #FFFFFF; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        self.lbl_next_platform.setVisible(True)
        self.btn_next_join.setVisible(bool(nxt.join_url))
        self.btn_next_prepare.setVisible(True)

    def _sync_account_dropdown(self, accounts: List[Dict[str, Any]]) -> None:
        current = self._selected_account_filter
        self.cmb_account_filter.blockSignals(True)
        self.cmb_account_filter.clear()
        self.cmb_account_filter.addItem("All Accounts", "")
        valid_ids = {""}
        for a in accounts:
            aid = str(a.get("account_id") or "")
            _, p_short = _provider_display_info(str(a.get("provider_type", "")))
            ident = str(a.get("account_email") or a.get("display_name") or aid)
            self.cmb_account_filter.addItem(f"{p_short}: {ident}", aid)
            valid_ids.add(aid)
        if current in valid_ids:
            idx = self.cmb_account_filter.findData(current)
            if idx >= 0:
                self.cmb_account_filter.setCurrentIndex(idx)
        else:
            self._selected_account_filter = ""
        self.cmb_account_filter.blockSignals(False)

    def _clear_layout(self, lay: Any) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    def _render_account_health_pills(self, accounts: List[Dict[str, Any]], t: Dict[str, Any]) -> None:
        if not hasattr(self, "accounts_health_lay"):
            return
        self._clear_layout(self.accounts_health_lay)
        if hasattr(self, "btn_toggle_accounts_panel"):
            self.btn_toggle_accounts_panel.setText(f"Accounts ({len(accounts)})")

        if not accounts:
            lbl = QLabel("No calendars connected")
            lbl.setObjectName("calMutedText")
            self.accounts_health_lay.addWidget(lbl)
            btn_connect = QPushButton("Connect Outlook / Google / Zoom / ICS")
            btn_connect.setObjectName("calDawnBtn")
            btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_connect.clicked.connect(lambda: self._open_connect_dialog(0))
            self.accounts_health_lay.addWidget(btn_connect)
            self.accounts_health_lay.addStretch(1)
            return

        for acc in accounts:
            aid = str(acc.get("account_id") or "")
            ptype = str(acc.get("provider_type") or "")
            _, p_label = _provider_display_info(ptype)
            p_short = {
                "ms_graph": "Outlook",
                "google": "Google",
                "zoom": "Zoom",
                "ics": "ICS Feed",
                "outlook_mapi": "Outlook MAPI",
            }.get(ptype.lower(), p_label.split()[0])
            full_ident = str(acc.get("account_email") or acc.get("display_name") or p_label)
            ident = full_ident if len(full_ident) <= 18 else (full_ident[:15] + "...")
            enabled = bool(acc.get("enabled", True))
            is_healthy = bool(acc.get("is_healthy", False))
            state = str(acc.get("connection_state") or "connected").lower()
            raw_err = str(acc.get("last_error") or "")
            is_offline_msg = "offline" in raw_err.lower() or "internet" in raw_err.lower() or state in {"offline_cached", "network_offline"}
            needs_reauth = (
                state in {"expired", "admin_approval_required", "admin_blocked"}
                or (enabled and state in {"failed", "sync_error"} and not is_offline_msg)
                or (enabled and not acc.get("is_authenticated", True) and state not in {"connected", "syncing", "disabled", "offline_cached"})
            )

            if not enabled or state in {"disabled", "paused"}:
                icon_txt, suffix = "⏸", "Paused"
                bg, fg, bdr = t["chip_bg"], t["text_muted"], t["border"]
            elif needs_reauth:
                icon_txt, suffix = "⚠", "Reconnect"
                bg, fg, bdr = t["accent_coral_tint"], t["accent_crimson"], t["accent_crimson"]
            elif is_offline_msg:
                icon_txt, suffix = "○", "Offline"
                bg, fg, bdr = t["chip_bg"], t["text_muted"], t["border"]
            else:
                icon_txt, suffix = "✓", p_short
                bg, fg, bdr = t["accent_emerald_tint"], t["accent_emerald"], t["accent_emerald"]

            pill_label = f"{icon_txt} {p_short} · {ident}" + (f" · {suffix}" if suffix != p_short else "")
            pill = QPushButton(pill_label)
            pill.setObjectName("calHealthPillBtn")
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            pill.setStyleSheet(
                f"QPushButton#calHealthPillBtn {{ background: {bg}; color: {fg}; border: 1px solid {bdr}; "
                f"border-radius: 14px; padding: 3px 10px; font-size: 11px; font-weight: 600; }}"
            )
            if needs_reauth:
                pill.setToolTip(f"Click to reconnect {p_label} ({full_ident})")
                pill.clicked.connect(lambda _=False, account_id=aid, pt=ptype: self._on_reconnect_account(account_id, pt))
            else:
                pill.setToolTip(f"Manage {p_label} account ({full_ident})")
                pill.clicked.connect(lambda: self.set_right_panel_mode("accounts", user_initiated=True))
            self.accounts_health_lay.addWidget(pill)
        self.accounts_health_lay.addStretch(1)

    def _render_accounts_panel(self, accounts: List[Dict[str, Any]]) -> None:
        self._clear_layout(self.accounts_lay)
        t = get_theme_tokens(self._theme)
        self._render_account_health_pills(accounts, t)

        if not accounts:
            empty = QFrame()
            empty.setObjectName("calCard")
            e_lay = QVBoxLayout(empty)
            e_lay.setContentsMargins(14, 14, 14, 14)
            e_lay.setSpacing(8)
            lbl = QLabel("No accounts connected yet")
            lbl.setFont(get_ui_font(14, 600))
            e_lay.addWidget(lbl)
            desc = QLabel(
                "Connect one or more personal or work accounts. VOX uses read-only calendar access "
                "and keeps recordings local."
            )
            desc.setObjectName("calMutedText")
            desc.setWordWrap(True)
            e_lay.addWidget(desc)
            for tab_idx, label in (
                (0, "Connect Microsoft Outlook"),
                (1, "Connect Google Calendar"),
                (2, "Connect Zoom"),
                (3, "Add Calendar Feed (.ics)"),
            ):
                b = QPushButton(label)
                b.setObjectName("calSecondaryBtn")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, ti=tab_idx: self._open_connect_dialog(ti))
                e_lay.addWidget(b)
            self.accounts_lay.addWidget(empty)
            self.accounts_lay.addStretch(1)
            return

        for acc in accounts:
            self.accounts_lay.addWidget(self._build_account_card(acc, t))
        self.accounts_lay.addStretch(1)

    def _build_account_card(self, acc: Dict[str, Any], t: Dict[str, Any]) -> QFrame:
        aid = str(acc.get("account_id") or "")
        ptype = str(acc.get("provider_type") or "")
        badge_char, p_label = _provider_display_info(ptype)
        ident = str(acc.get("account_email") or acc.get("display_name") or p_label)
        display_name = str(acc.get("display_name") or "")
        kind_lbl = _account_kind_label(str(acc.get("account_kind") or ""))
        enabled = bool(acc.get("enabled", True))
        is_healthy = bool(acc.get("is_healthy", False))
        state = str(acc.get("connection_state") or "connected")
        state_lbl, state_kind = _state_display_info(state, is_healthy, enabled)
        sel_cals = list(acc.get("selected_calendars") or [])
        last_sync = _format_last_sync(acc.get("last_sync_at"))
        raw_err = str(acc.get("last_error") or "")
        friendly_err = sanitize_oauth_error_for_user(raw_err, provider=ptype)[1] if raw_err else ""
        is_offline_msg = (
            "offline" in friendly_err.lower()
            or "internet" in friendly_err.lower()
            or state in {"offline_cached", "network_offline"}
        )
        if is_offline_msg and enabled and acc.get("is_authenticated", True):
            state_lbl = "Offline (Cached)"
            state_kind = "muted"

        card = QFrame()
        card.setObjectName("calCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Header row: Provider icon + identity + status pill
        top = QHBoxLayout()
        top.setSpacing(10)
        icon = QLabel(badge_char)
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: {t['accent_fathom_tint']}; color: {t['accent_fathom']}; "
            f"border-radius: 8px; font-weight: 700; font-size: 13px;"
        )
        top.addWidget(icon)

        id_col = QVBoxLayout()
        id_col.setSpacing(1)
        lbl_id = QLabel(ident)
        lbl_id.setFont(get_ui_font(13, 600))
        lbl_id.setWordWrap(True)
        id_col.addWidget(lbl_id)

        sub_parts = [p_label]
        if display_name and display_name != ident:
            sub_parts.insert(0, display_name)
        if kind_lbl:
            sub_parts.append(kind_lbl)
        lbl_sub = QLabel(" · ".join(sub_parts))
        lbl_sub.setObjectName("calMutedText")
        lbl_sub.setWordWrap(True)
        id_col.addWidget(lbl_sub)
        top.addLayout(id_col, 1)

        state_pill = QLabel(state_lbl)
        dawn_fg = t["accent_dawn"] if self._theme == "dark" else t["accent_dawn_text"]
        state_colors = {
            "ok": (t["accent_emerald_tint"], t["accent_emerald"]),
            "info": (t["accent_dawn_tint"], dawn_fg),
            "warn": ("rgba(255, 169, 70, 0.16)", t["accent_amber"]),
            "err": (t["accent_coral_tint"], t["accent_crimson"]),
            "muted": (t["chip_bg"], t["text_muted"]),
        }
        s_bg, s_fg = state_colors.get(state_kind, state_colors["muted"])
        state_pill.setStyleSheet(
            f"background: {s_bg}; color: {s_fg}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        top.addWidget(state_pill, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)

        # Metadata row: calendars selected + last sync
        cal_txt = f"{len(sel_cals)} calendar(s)" if sel_cals else "Primary calendar"
        lbl_meta = QLabel(f"{cal_txt}  ·  {last_sync}")
        lbl_meta.setObjectName("calMutedText")
        lay.addWidget(lbl_meta)

        is_offline_msg = (
            "offline" in friendly_err.lower()
            or "internet" in friendly_err.lower()
            or state in {"offline_cached", "network_offline"}
        )
        has_active_error = (
            bool(friendly_err)
            and enabled
            and (not is_healthy or state in {"expired", "failed", "admin_approval_required", "network_offline", "offline_cached"})
        )
        if has_active_error:
            err_box = QLabel(friendly_err)
            err_box.setWordWrap(True)
            if is_offline_msg:
                err_box.setStyleSheet(
                    f"background: {t['chip_bg']}; color: {t['text_secondary']}; "
                    f"border: 1px solid {t['border']}; border-radius: 6px; padding: 6px 8px; font-size: 11px;"
                )
            else:
                err_box.setStyleSheet(
                    f"background: {t['accent_coral_tint']}; color: {t['text_primary']}; "
                    f"border-radius: 6px; padding: 6px 8px; font-size: 11px;"
                )
            lay.addWidget(err_box)

        # Two-row compact action bar so controls never clip inside the side panel
        act_row1 = QHBoxLayout()
        act_row1.setSpacing(6)

        if ptype in {"ms_graph", "google"}:
            btn_cals = QPushButton("Calendars")
            btn_cals.setObjectName("calMiniBtn")
            btn_cals.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_cals.clicked.connect(lambda _=False, a=acc: self._open_calendar_picker(a))
            act_row1.addWidget(btn_cals)

        sync_btn_txt = "Retry Sync" if is_offline_msg else "Sync"
        btn_sync = QPushButton(sync_btn_txt)
        btn_sync.setObjectName("calMiniBtn")
        btn_sync.setEnabled(enabled)
        btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sync.clicked.connect(lambda _=False, account_id=aid: self._on_sync_account(account_id))
        act_row1.addWidget(btn_sync)

        needs_reauth = enabled and not is_offline_msg and (
            state in {"expired", "admin_approval_required"}
            or (not acc.get("is_authenticated") and state not in {"connected", "syncing", "disabled", "offline_cached"})
        )
        if needs_reauth:
            btn_reconn = QPushButton("Reconnect")
            btn_reconn.setObjectName("calDawnBtn")
            btn_reconn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reconn.clicked.connect(lambda _=False, account_id=aid, pt=ptype: self._on_reconnect_account(account_id, pt))
            act_row1.addWidget(btn_reconn)
        elif enabled and not is_healthy and state == "failed" and not is_offline_msg:
            btn_reconn = QPushButton("Reconnect")
            btn_reconn.setObjectName("calMiniBtn")
            btn_reconn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reconn.clicked.connect(lambda _=False, account_id=aid, pt=ptype: self._on_reconnect_account(account_id, pt))
            act_row1.addWidget(btn_reconn)
        act_row1.addStretch(1)
        lay.addLayout(act_row1)

        act_row2 = QHBoxLayout()
        act_row2.setSpacing(6)

        btn_toggle = QPushButton("Pause" if enabled else "Enable")
        btn_toggle.setObjectName("calMiniBtn")
        btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_toggle.clicked.connect(lambda _=False, account_id=aid, en=not enabled: self._on_toggle_account(account_id, en))
        act_row2.addWidget(btn_toggle)

        btn_disc = QPushButton("Remove")
        btn_disc.setObjectName("calMiniDangerBtn")
        btn_disc.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_disc.clicked.connect(lambda _=False, account_id=aid, name=ident: self._on_disconnect_account(account_id, name))
        act_row2.addWidget(btn_disc)

        act_row2.addStretch(1)
        lay.addLayout(act_row2)
        return card

    def _matches_account_and_search(self, m: CalendarMeeting) -> bool:
        if self._selected_account_filter:
            src_match = (m.account_id == self._selected_account_filter) or (
                self._selected_account_filter in (m.source_accounts or [])
            )
            if not src_match and ":" in self._selected_account_filter:
                email_part = self._selected_account_filter.split(":", 1)[1].lower()
                src_match = any(email_part in s.lower() for s in (m.source_accounts or []))
            if not src_match:
                return False

        if self._search_text:
            haystack = " ".join([
                m.title or "",
                m.organizer or "",
                m.organizer_name or "",
                m.platform or "",
                m.meeting_platform_label,
                m.calendar_name or "",
                " ".join(m.attendees or []),
            ]).lower()
            if self._search_text not in haystack:
                return False
        return True

    def _filter_meetings(self, meetings: List[CalendarMeeting]) -> List[CalendarMeeting]:
        filtered: List[CalendarMeeting] = []
        for m in meetings:
            if self._active_filter == "today" and not m.is_today:
                continue
            elif self._active_filter == "tomorrow" and not m.is_tomorrow:
                continue
            elif self._active_filter == "week" and not m.is_this_week:
                continue
            if not self._matches_account_and_search(m):
                continue
            filtered.append(m)
        return filtered

    def _render_meetings_panel(
        self,
        accounts: List[Dict[str, Any]],
        meetings: List[CalendarMeeting],
        recording_map: Dict[str, int],
    ) -> None:
        self._clear_layout(self.meetings_lay)
        self._week_day_columns = []
        t = get_theme_tokens(self._theme)

        if self._view_mode == "week" or (self._view_mode == "timeline" and self._active_filter == "week"):
            self._render_week_grid_panel(accounts, meetings, recording_map, t)
            return

        if self._view_mode == "timeline":
            timeline_filtered = [m for m in meetings if self._matches_account_and_search(m)]
            self._render_timeline_panel(accounts, timeline_filtered, recording_map, t)
            return

        filtered = self._filter_meetings(meetings)

        if not filtered:
            empty = QFrame()
            empty.setObjectName("calCard")
            e_lay = QVBoxLayout(empty)
            e_lay.setContentsMargins(24, 28, 24, 28)
            e_lay.setSpacing(10)
            if not accounts:
                lbl = QLabel("Bring your schedule into VOX")
                lbl.setFont(get_ui_font(18, 600, serif=True))
                e_lay.addWidget(lbl)
                sub = QLabel(
                    "Connect Microsoft Outlook (personal or work), Google Calendar, or Zoom. "
                    "Upcoming calls sync automatically, meeting platforms (Teams, Meet, Zoom) are detected accurately, "
                    "and your recordings stay 100% local."
                )
                sub.setObjectName("calMutedText")
                sub.setWordWrap(True)
                e_lay.addWidget(sub)
                cta = QPushButton("Connect Your First Calendar")
                cta.setObjectName("calFathomBtn")
                cta.setCursor(Qt.CursorShape.PointingHandCursor)
                cta.clicked.connect(lambda: self._open_connect_dialog())
                e_lay.addWidget(cta, 0, Qt.AlignmentFlag.AlignLeft)
            else:
                lbl = QLabel("No matching meetings found")
                lbl.setFont(get_ui_font(16, 600))
                e_lay.addWidget(lbl)
                sub = QLabel(
                    "Try switching to 'Upcoming', clearing your search query, or clicking Sync Now to refresh events."
                )
                sub.setObjectName("calMutedText")
                sub.setWordWrap(True)
                e_lay.addWidget(sub)
            self.meetings_lay.addWidget(empty)
            self.meetings_lay.addStretch(1)
            return

        current_group = ""
        for m in filtered:
            if m.is_today:
                grp = "Today"
            elif m.is_tomorrow:
                grp = "Tomorrow"
            elif m.is_this_week:
                grp = m.local_start_time.strftime("%A, %b %d")
            else:
                grp = m.local_start_time.strftime("%a, %b %d")
            if grp != current_group:
                current_group = grp
                g_lbl = QLabel(grp.upper())
                g_lbl.setObjectName("calSectionHeader")
                self.meetings_lay.addWidget(g_lbl)
            rec_id = recording_map.get(m.id)
            self.meetings_lay.addWidget(self._build_meeting_card(m, rec_id, t))

        self.meetings_lay.addStretch(1)

    def _render_week_grid_panel(
        self,
        accounts: List[Dict[str, Any]],
        all_meetings: List[CalendarMeeting],
        recording_map: Dict[str, int],
        t: Dict[str, Any],
    ) -> None:
        self._week_day_columns = []
        now_local = datetime.now().astimezone()
        today_date = now_local.date()
        week_start = self._anchor_date - timedelta(days=self._anchor_date.weekday())

        candidates = [
            m for m in all_meetings
            if not m.is_cancelled and self._matches_account_and_search(m)
        ]
        week_end = week_start + timedelta(days=6)
        has_weekend_mtg = any(
            week_start <= m.local_start_time.date() <= week_end and m.local_start_time.weekday() >= 5
            for m in candidates
        )
        num_days = 7 if (self._week_show_weekends or has_weekend_mtg) else 5
        days = [week_start + timedelta(days=i) for i in range(num_days)]

        if hasattr(self, "lbl_nav_range"):
            self.lbl_nav_range.setText(f"{days[0].strftime('%b %d')} – {days[-1].strftime('%b %d, %Y')}")

        if not candidates and not accounts:
            empty = QFrame()
            empty.setObjectName("calCard")
            e_lay = QVBoxLayout(empty)
            e_lay.setContentsMargins(24, 28, 24, 28)
            e_lay.setSpacing(10)
            lbl = QLabel("No Calendars Connected")
            lbl.setFont(get_ui_font(16, 600))
            e_lay.addWidget(lbl)
            sub = QLabel("Connect Microsoft Outlook, Google Calendar, or Zoom to view your Week Grid.")
            sub.setObjectName("calMutedText")
            e_lay.addWidget(sub)
            cta = QPushButton("Connect Your First Calendar")
            cta.setObjectName("calFathomBtn")
            cta.setCursor(Qt.CursorShape.PointingHandCursor)
            cta.clicked.connect(lambda: self._open_connect_dialog())
            e_lay.addWidget(cta, 0, Qt.AlignmentFlag.AlignLeft)
            self.meetings_lay.addWidget(empty)
            self.meetings_lay.addStretch(1)
            return

        grid_host = QFrame()
        grid_host.setObjectName("calWeekGridHost")
        cols_lay = QHBoxLayout(grid_host)
        cols_lay.setContentsMargins(4, 4, 4, 4)
        cols_lay.setSpacing(8)

        for d in days:
            is_today = (d == today_date)
            col_frame = QFrame()
            col_frame.setObjectName("calWeekDayColToday" if is_today else "calWeekDayCol")
            col_lay = QVBoxLayout(col_frame)
            col_lay.setContentsMargins(8, 8, 8, 8)
            col_lay.setSpacing(6)

            hdr_row = QHBoxLayout()
            hdr_row.setSpacing(4)
            lbl_day = QLabel(d.strftime("%a %d"))
            lbl_day.setFont(get_ui_font(12, 700))
            hdr_row.addWidget(lbl_day)
            hdr_row.addStretch(1)
            if is_today:
                now_badge = QLabel(f"● NOW {now_local.strftime('%I:%M %p').lstrip('0')}")
                now_badge.setStyleSheet(
                    f"background: {t['accent_coral_tint']}; color: {t['accent_crimson']}; "
                    f"border-radius: 4px; padding: 1px 5px; font-size: 9.5px; font-weight: 700;"
                )
                hdr_row.addWidget(now_badge)
            col_lay.addLayout(hdr_row)

            day_mtgs = sorted(
                [m for m in candidates if m.local_start_time.date() == d],
                key=lambda x: x.local_start_time,
            )
            if not day_mtgs:
                lbl_free = QLabel("Free")
                lbl_free.setObjectName("calMutedText")
                col_lay.addWidget(lbl_free)
            else:
                for m in day_mtgs:
                    rec_id = recording_map.get(m.id)
                    _, plat_color = _platform_display_info(m.platform, t)
                    t_label = m.local_start_time.strftime("%I:%M %p").lstrip("0")
                    text_parts = [f"{t_label} · {m.title or 'Untitled'}"]
                    if rec_id:
                        text_parts.append("✓ Recorded")
                    ev_btn = QPushButton("\n".join(text_parts))
                    ev_btn.setObjectName("calWeekEventBlock")
                    ev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    ev_btn.setToolTip(f"{m.title or 'Meeting'} ({m.meeting_platform_label})")
                    ev_btn.setStyleSheet(
                        f"QPushButton#calWeekEventBlock {{ text-align: left; background: {t['surface_secondary']}; "
                        f"color: {t['text_primary']}; border: 1px solid {t['border']}; border-left: 4px solid {plat_color}; "
                        f"border-radius: 6px; padding: 6px 8px; font-size: 11.5px; font-weight: 600; }}"
                        f"QPushButton#calWeekEventBlock:hover {{ border-color: {plat_color}; background: {t['surface_hover']}; }}"
                    )
                    ev_btn.clicked.connect(lambda _=False, mt=m, rid=rec_id: self.inspect_meeting(mt, rid))
                    col_lay.addWidget(ev_btn)

            col_lay.addStretch(1)
            cols_lay.addWidget(col_frame, 1)
            self._week_day_columns.append((d, col_frame))

        self.meetings_lay.addWidget(grid_host)
        self.meetings_lay.addStretch(1)

    def _render_timeline_panel(
        self,
        accounts: List[Dict[str, Any]],
        filtered: List[CalendarMeeting],
        recording_map: Dict[str, int],
        t: Dict[str, Any],
    ) -> None:
        now_local = datetime.now().astimezone()
        target_date = self._anchor_date
        is_today = (target_date == now_local.date())
        if is_today:
            date_title = f"TODAY'S FLOW — {target_date.strftime('%A, %b %d, %Y')}"
        elif target_date == (now_local + timedelta(days=1)).date():
            date_title = f"TOMORROW'S FLOW — {target_date.strftime('%A, %b %d, %Y')}"
        else:
            date_title = f"DAY FLOW — {target_date.strftime('%A, %b %d, %Y')}"

        if hasattr(self, "lbl_nav_range"):
            self.lbl_nav_range.setText(target_date.strftime("%a, %b %d, %Y"))

        tl_hdr = QHBoxLayout()
        lbl_tl_title = QLabel(date_title.upper())
        lbl_tl_title.setObjectName("calSectionHeader")
        tl_hdr.addWidget(lbl_tl_title)
        tl_hdr.addStretch(1)

        lbl_tl_sub = QLabel("Hourly interactive timeline & live current time indicator")
        lbl_tl_sub.setObjectName("calMutedText")
        tl_hdr.addWidget(lbl_tl_sub)
        self.meetings_lay.addLayout(tl_hdr)

        day_meetings = [m for m in filtered if m.local_start_time.date() == target_date and not m.is_cancelled]
        day_meetings.sort(key=lambda m: m.local_start_time)

        if not day_meetings and not accounts:
            empty = QFrame()
            empty.setObjectName("calCard")
            e_lay = QVBoxLayout(empty)
            e_lay.setContentsMargins(24, 28, 24, 28)
            e_lay.setSpacing(10)
            lbl = QLabel("No Calendars Connected")
            lbl.setFont(get_ui_font(16, 600))
            e_lay.addWidget(lbl)
            sub = QLabel("Connect Microsoft Outlook, Google Calendar, or Zoom to visualize your Day Flow timeline.")
            sub.setObjectName("calMutedText")
            e_lay.addWidget(sub)
            cta = QPushButton("Connect Your First Calendar")
            cta.setObjectName("calFathomBtn")
            cta.setCursor(Qt.CursorShape.PointingHandCursor)
            cta.clicked.connect(lambda: self._open_connect_dialog())
            e_lay.addWidget(cta, 0, Qt.AlignmentFlag.AlignLeft)
            self.meetings_lay.addWidget(empty)
            self.meetings_lay.addStretch(1)
            return

        pre_8am = [m for m in day_meetings if m.local_start_time.hour < 8]
        if pre_8am:
            early_box = QFrame()
            early_box.setObjectName("calCard")
            e_lay = QVBoxLayout(early_box)
            e_lay.setContentsMargins(12, 8, 12, 8)
            lbl_early = QLabel("EARLY MORNING (BEFORE 8:00 AM)")
            lbl_early.setObjectName("calKpiLabel")
            e_lay.addWidget(lbl_early)
            for m in pre_8am:
                rec_id = recording_map.get(m.id)
                e_lay.addWidget(self._build_meeting_card(m, rec_id, t))
            self.meetings_lay.addWidget(early_box)

        grid_frame = QFrame()
        grid_frame.setObjectName("calCard")
        grid_lay = QVBoxLayout(grid_frame)
        grid_lay.setContentsMargins(14, 14, 14, 14)
        grid_lay.setSpacing(8)

        for h in range(8, 21):
            h_time = f"{h % 12 or 12:02d}:00 {'AM' if h < 12 else 'PM'}"
            h_row = QHBoxLayout()
            h_row.setSpacing(12)

            lbl_h = QLabel(h_time)
            lbl_h.setFixedWidth(68)
            lbl_h.setObjectName("calMutedText")
            lbl_h.setFont(get_ui_font(11, 700))
            h_row.addWidget(lbl_h, 0, Qt.AlignmentFlag.AlignTop)

            slot_col = QVBoxLayout()
            slot_col.setSpacing(6)

            # Live NOW indicator
            if is_today and now_local.hour == h:
                now_bar = QHBoxLayout()
                now_bar.setSpacing(6)
                now_pill = QLabel(f"● NOW · {now_local.strftime('%I:%M %p')}")
                now_pill.setStyleSheet(
                    f"background: {t['accent_coral_tint']}; color: {t['accent_crimson']}; "
                    f"border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
                )
                now_bar.addWidget(now_pill)
                now_line = QFrame()
                now_line.setFixedHeight(2)
                now_line.setStyleSheet(f"background: {t['accent_crimson']};")
                now_bar.addWidget(now_line, 1)
                slot_col.addLayout(now_bar)

            h_mtgs = [m for m in day_meetings if m.local_start_time.hour == h]
            if h_mtgs:
                for m in h_mtgs:
                    slot_col.addWidget(self._build_timeline_meeting_block(m, recording_map.get(m.id), t))
            else:
                empty_slot = QFrame()
                empty_slot.setFixedHeight(20)
                empty_slot.setStyleSheet(f"border-top: 1px dashed {t['border']};")
                slot_col.addWidget(empty_slot)

            h_row.addLayout(slot_col, 1)
            grid_lay.addLayout(h_row)

        self.meetings_lay.addWidget(grid_frame)

        post_8pm = [m for m in day_meetings if m.local_start_time.hour >= 21]
        if post_8pm:
            late_box = QFrame()
            late_box.setObjectName("calCard")
            l_lay = QVBoxLayout(late_box)
            l_lay.setContentsMargins(12, 8, 12, 8)
            lbl_late = QLabel("EVENING / LATE (AFTER 8:00 PM)")
            lbl_late.setObjectName("calKpiLabel")
            l_lay.addWidget(lbl_late)
            for m in post_8pm:
                rec_id = recording_map.get(m.id)
                l_lay.addWidget(self._build_meeting_card(m, rec_id, t))
            self.meetings_lay.addWidget(late_box)

        self.meetings_lay.addStretch(1)

    def _build_timeline_meeting_block(
        self,
        m: CalendarMeeting,
        recording_id: Optional[int],
        t: Dict[str, Any],
    ) -> QFrame:
        block = QFrame()
        block.setObjectName("calTimelineBlock")
        plat_label, plat_color = _platform_display_info(m.platform, t)
        is_live = m.is_happening_now

        bg_col = t['surface_secondary']
        border_col = plat_color if is_live else t['border']
        glow = f"border: 2px solid {plat_color};" if is_live else f"border: 1px solid {border_col}; border-left: 4px solid {plat_color};"

        block.setStyleSheet(f"background: {bg_col}; {glow} border-radius: 8px;")
        b_lay = QHBoxLayout(block)
        b_lay.setContentsMargins(12, 8, 12, 8)
        b_lay.setSpacing(10)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        time_str, rel_str = _format_time_window(m)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        lbl_t = QLabel(time_str)
        lbl_t.setFont(get_ui_font(12, 600))
        top_row.addWidget(lbl_t)

        if rel_str:
            lbl_rel = QLabel(rel_str)
            r_bg = t['accent_emerald_tint'] if is_live else t['chip_bg']
            r_fg = t['accent_emerald'] if is_live else t['text_muted']
            lbl_rel.setStyleSheet(f"background: {r_bg}; color: {r_fg}; border-radius: 4px; padding: 1px 5px; font-size: 10px; font-weight: 600;")
            top_row.addWidget(lbl_rel)

        top_row.addStretch(1)
        info_col.addLayout(top_row)

        lbl_tit = QLabel(m.title or "Untitled Meeting")
        lbl_tit.setFont(get_ui_font(14, 600))
        lbl_tit.setWordWrap(True)
        info_col.addWidget(lbl_tit)

        sub_parts = [plat_label]
        org = m.organizer_name or m.organizer
        if org:
            sub_parts.append(f"Host: {org}")
        if m.attendees:
            sub_parts.append(f"{len(m.attendees)} attendee{'s' if len(m.attendees) != 1 else ''}")
        lbl_sub = QLabel(" · ".join(sub_parts))
        lbl_sub.setObjectName("calMutedText")
        info_col.addWidget(lbl_sub)
        b_lay.addLayout(info_col, 1)

        act_col = QHBoxLayout()
        act_col.setSpacing(6)
        if m.join_url:
            btn_join = QPushButton("Join")
            btn_join.setObjectName("calFathomBtn")
            btn_join.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_join.clicked.connect(lambda _=False, mt=m: self._on_join_meeting(mt))
            act_col.addWidget(btn_join)

        btn_prep = QPushButton("Prepare")
        btn_prep.setObjectName("calDawnBtn")
        btn_prep.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_prep.clicked.connect(lambda _=False, mt=m: self._on_prepare_notetaker(mt))
        act_col.addWidget(btn_prep)

        btn_insp = QPushButton("Inspect")
        btn_insp.setObjectName("calMiniBtn")
        btn_insp.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_insp.clicked.connect(lambda _=False, mt=m, rid=recording_id: self.inspect_meeting(mt, rid))
        act_col.addWidget(btn_insp)

        btn_det = QPushButton("Details")
        btn_det.setObjectName("calMiniBtn")
        btn_det.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_det.clicked.connect(lambda _=False, mt=m, rid=recording_id: self._open_meeting_details(mt, rid))
        act_col.addWidget(btn_det)

        b_lay.addLayout(act_col)
        return block

    def _build_meeting_card(
        self,
        m: CalendarMeeting,
        recording_id: Optional[int],
        t: Dict[str, Any],
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("calMeetingCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        # Top row: Time + countdown + Platform badge + Source calendar badge
        top = QHBoxLayout()
        top.setSpacing(8)
        time_str, rel_str = _format_time_window(m)
        lbl_time = QLabel(time_str)
        lbl_time.setFont(get_ui_font(13, 600))
        top.addWidget(lbl_time)

        if rel_str:
            lbl_rel = QLabel(rel_str)
            is_live = m.is_happening_now
            rel_bg = t["accent_emerald_tint"] if is_live else t["chip_bg"]
            rel_fg = t["accent_emerald"] if is_live else t["text_muted"]
            lbl_rel.setStyleSheet(
                f"background: {rel_bg}; color: {rel_fg}; border-radius: 6px; padding: 2px 7px; font-size: 11px; font-weight: 600;"
            )
            top.addWidget(lbl_rel)

        top.addStretch(1)

        plat_label, plat_color = _platform_display_info(m.platform, t)
        lbl_plat = QLabel(plat_label)
        lbl_plat.setStyleSheet(
            f"background: {plat_color}; color: #FFFFFF; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        top.addWidget(lbl_plat)

        src_label = (m.source_accounts[0] if m.source_accounts else "") or m.calendar_provider_label
        lbl_src = QLabel(src_label)
        lbl_src.setStyleSheet(
            f"background: {t['chip_bg']}; color: {t['chip_text']}; border: 1px solid {t['chip_border']}; "
            f"border-radius: 6px; padding: 2px 8px; font-size: 11px;"
        )
        top.addWidget(lbl_src)
        lay.addLayout(top)

        # Title + recurring badge
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        lbl_title = QLabel(m.title or "Untitled Meeting")
        lbl_title.setFont(get_ui_font(15, 600))
        lbl_title.setWordWrap(True)
        title_row.addWidget(lbl_title, 1)

        if m.is_recurring:
            rec_fg = t["accent_dawn"] if self._theme == "dark" else t["accent_dawn_text"]
            rec_lbl = QLabel("Modified" if m.is_modified_occurrence else "Recurring")
            rec_lbl.setStyleSheet(
                f"background: {t['accent_dawn_tint']}; color: {rec_fg}; "
                f"border-radius: 6px; padding: 2px 7px; font-size: 10px; font-weight: 600;"
            )
            title_row.addWidget(rec_lbl)
        lay.addLayout(title_row)

        # Organizer / attendees / location
        meta_parts: List[str] = []
        org = m.organizer_name or m.organizer
        if org:
            meta_parts.append(f"Host: {org}")
        if m.attendees:
            meta_parts.append(f"{len(m.attendees)} attendee{'s' if len(m.attendees) != 1 else ''}")
        if m.location and not str(m.location).startswith("http"):
            meta_parts.append(m.location)
        if meta_parts:
            lbl_meta = QLabel("  ·  ".join(meta_parts))
            lbl_meta.setObjectName("calMutedText")
            lbl_meta.setWordWrap(True)
            lay.addWidget(lbl_meta)

        # Actions row
        act = QHBoxLayout()
        act.setSpacing(8)
        if m.join_url:
            btn_join = QPushButton("Join Meeting")
            btn_join.setObjectName("calFathomBtn")
            btn_join.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_join.clicked.connect(lambda _=False, mt=m: self._on_join_meeting(mt))
            act.addWidget(btn_join)

        btn_prep = QPushButton("Prepare Notetaker")
        btn_prep.setObjectName("calDawnBtn")
        btn_prep.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_prep.clicked.connect(lambda _=False, mt=m: self._on_prepare_notetaker(mt))
        act.addWidget(btn_prep)

        if recording_id:
            btn_rec = QPushButton("View Recording")
            btn_rec.setObjectName("calSecondaryBtn")
            btn_rec.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_rec.clicked.connect(lambda _=False, rid=int(recording_id): self.open_meeting_recording_requested.emit(rid))
            act.addWidget(btn_rec)

        act.addStretch(1)
        btn_inspect = QPushButton("Inspect")
        btn_inspect.setObjectName("calMiniBtn")
        btn_inspect.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_inspect.clicked.connect(lambda _=False, mt=m, rid=recording_id: self.inspect_meeting(mt, rid))
        act.addWidget(btn_inspect)

        btn_details = QPushButton("Details")
        btn_details.setObjectName("calMiniBtn")
        btn_details.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_details.clicked.connect(lambda _=False, mt=m, rid=recording_id: self._open_meeting_details(mt, rid))
        act.addWidget(btn_details)
        lay.addLayout(act)
        return card

    # ------------------------------------------------------------------ Actions
    def _open_connect_dialog(self, tab_index: int = 0) -> None:
        from voxlocal.ui.calendar_connect_dialog import VoxCalendarConnectDialog

        dlg = VoxCalendarConnectDialog(self.controller, theme=self._theme, parent=self)
        if hasattr(dlg, "_select_tab"):
            dlg._select_tab(tab_index)
        dlg.connected.connect(lambda msg: self._on_account_connected(msg))
        dlg.exec()
        self.refresh()

    def _on_account_connected(self, msg: str) -> None:
        self.status_toast.emit(msg or "Calendar connected.", "success")
        self.refresh()

    def _open_calendar_picker(self, account: Dict[str, Any]) -> None:
        dlg = CalendarSelectionDialog(self.controller, account, theme=self._theme, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.status_toast.emit("Calendar selection updated. Syncing events...", "info")
            self.refresh()

    def _open_meeting_details(self, meeting: CalendarMeeting, recording_id: Optional[int]) -> None:
        dlg = CalendarMeetingDetailsDialog(
            meeting, self.controller, recording_id=recording_id, theme=self._theme, parent=self
        )
        dlg.prepare_notetaker_clicked.connect(self._on_prepare_notetaker)
        dlg.open_recording_clicked.connect(lambda rid: self.open_meeting_recording_requested.emit(rid))
        dlg.exec()

    def _on_sync_all_clicked(self) -> None:
        if hasattr(self.controller, "sync_all_calendars"):
            self.controller.sync_all_calendars(force=True)
            self.status_toast.emit("Syncing all connected calendars...", "info")
            QTimer.singleShot(350, self.refresh)

    def _on_sync_account(self, account_id: str) -> None:
        if hasattr(self.controller, "sync_calendar_account"):
            self.controller.sync_calendar_account(account_id)
            self.status_toast.emit("Syncing calendar account...", "info")
            QTimer.singleShot(350, self.refresh)

    def _on_reconnect_account(self, account_id: str, provider_type: str) -> None:
        tab_map = {"ms_graph": 0, "google": 1, "zoom": 2, "ics": 3, "outlook_mapi": 3}
        self._open_connect_dialog(tab_map.get((provider_type or "").lower(), 0))

    def _on_toggle_account(self, account_id: str, enabled: bool) -> None:
        if hasattr(self.controller, "set_calendar_account_enabled"):
            self.controller.set_calendar_account_enabled(account_id, enabled)
            self.status_toast.emit(
                "Account resumed and syncing." if enabled else "Account synchronization paused.",
                "info",
            )
            self.refresh()

    def _on_disconnect_account(self, account_id: str, display_name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Remove Calendar Account",
            f"Remove {display_name} from VOX? Local audio recordings and transcripts will remain untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and hasattr(self.controller, "disconnect_calendar_account"):
            self.controller.disconnect_calendar_account(account_id)
            self.status_toast.emit(f"Removed {display_name}.", "info")
            self.refresh()

    def _on_join_meeting(self, meeting: CalendarMeeting) -> None:
        if hasattr(self.controller, "start_meeting_from_calendar"):
            ok, msg = self.controller.start_meeting_from_calendar(meeting)
            self.status_toast.emit(msg, "info" if ok else "error")

    def _on_prepare_notetaker(self, meeting: CalendarMeeting) -> None:
        if hasattr(self.controller, "prepare_notetaker_for_meeting"):
            ok, msg = self.controller.prepare_notetaker_for_meeting(meeting, start_recording=False)
            self.status_toast.emit(msg, "success" if ok else "info")
        self.prepare_notetaker_requested.emit(meeting)

    def _on_spotlight_join(self) -> None:
        if self._current_spotlight is not None:
            self._on_join_meeting(self._current_spotlight)

    def _on_spotlight_prepare(self) -> None:
        if self._current_spotlight is not None:
            self._on_prepare_notetaker(self._current_spotlight)

    # ------------------------------------------------------------------ Theme
    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            CalendarPage, QWidget#calScrollHost {{
                background: {t['background']};
                color: {t['text_primary']};
            }}
            QLabel#calEyebrow {{
                color: {t['text_muted']};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#calSub, QLabel#calMutedText {{
                color: {t['text_muted']};
                font-size: 12px;
            }}
            QLabel#calKpiLabel, QLabel#calSectionHeader {{
                color: {t['text_muted']};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.6px;
            }}
            QFrame#calCard, QFrame#calMeetingCard, QFrame#calSidePanel, QFrame#calTimelineBlock,
            QFrame#calHealthStrip, QFrame#calNavStrip, QFrame#calInspectorCard, QFrame#calWeekGridHost {{
                background: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            QFrame#calWeekDayCol {{
                background: {t['background']};
                border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QFrame#calWeekDayColToday {{
                background: {t['surface_secondary']};
                border: 1.5px solid {t['accent_fathom']};
                border-radius: 10px;
            }}
            QFrame#calSpotlightCard {{
                background: {t['surface_secondary']};
                border: 1px solid {t['dawn_btn_border'] if self._theme == 'light' else t['border']};
                border-radius: 12px;
            }}
            QPushButton#calPrimaryBtn {{
                background: {t['primary_btn_bg']};
                color: {t['primary_btn_text']};
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton#calPrimaryBtn:hover {{
                background: {t['primary_btn_hover']};
            }}
            QPushButton#calFathomBtn {{
                background: {t['fathom_btn_bg']};
                color: {t['fathom_btn_text']};
                border: 1px solid {t['fathom_btn_border']};
                border-radius: 8px;
                padding: 6px 12px;
                min-height: 22px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton#calFathomBtn:hover {{
                background: {t['fathom_btn_hover']};
            }}
            QPushButton#calDawnBtn {{
                background: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 6px 12px;
                min-height: 22px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton#calSecondaryBtn {{
                background: {t['secondary_btn_bg']};
                color: {t['secondary_btn_text']};
                border: 1px solid {t['secondary_btn_border']};
                border-radius: 8px;
                padding: 7px 12px;
                min-height: 22px;
                font-weight: 500;
                font-size: 12px;
            }}
            QPushButton#calSecondaryBtn:hover {{
                background: {t['secondary_btn_hover']};
            }}
            QPushButton#calFilterBtn {{
                background: transparent;
                color: {t['text_secondary']};
                border: 1px solid {t['border']};
                border-radius: 16px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#calFilterBtn:hover {{
                background: {t['surface_secondary']};
            }}
            QPushButton#calFilterBtnActive {{
                background: {t['primary_btn_bg']};
                color: {t['primary_btn_text']};
                border: 1px solid {t['primary_btn_bg']};
                border-radius: 16px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#calMiniBtn {{
                background: {t['surface_secondary']};
                color: {t['text_secondary']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 4px 9px;
                min-height: 18px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton#calMiniBtn:hover {{
                background: {t['secondary_btn_hover']};
            }}
            QPushButton#calMiniDangerBtn {{
                background: transparent;
                color: {t['accent_crimson']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 4px 9px;
                min-height: 18px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton#calMiniDangerBtn:hover {{
                background: {t['accent_coral_tint']};
            }}
            QLineEdit#calSearchInput, QComboBox#calCombo {{
                background: {t['input_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['input_border']};
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            """
        )
        self.refresh()

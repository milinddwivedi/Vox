"""VOX Hub Workspace Pages: Home, Dictionary, Snippets, Style, Scratchpad & Insights."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.ui.components import VoxEmptyStateWidget, VoxPreMeetingBriefingDialog, VoxToggleSwitch
from voxlocal.ui.theme import get_theme_tokens, get_ui_font

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController

logger = get_logger("ui.hub_pages")


def _safe_date_bucket(ts_str: str) -> str:
    """Group ISO or SQLite timestamp strings into Today / Yesterday / Earlier."""
    raw = str(ts_str or "").strip()
    if not raw:
        return "Earlier"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        d = dt.date()
    except Exception:
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except Exception:
            return "Earlier"
    today = datetime.now().date()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return "Earlier"


# ==============================================================================
# 1. HOME PAGE (KPIs + Upcoming Strip + Chronological Dictation Feed)
# ==============================================================================
class HomePage(QWidget):
    """Editorial Home dashboard with dictation feed, inline corrections & KPIs."""

    navigate_requested = pyqtSignal(str)
    status_toast = pyqtSignal(str, str)
    open_meeting_requested = pyqtSignal(int)
    prepare_meeting_requested = pyqtSignal(object)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._kpi_cards: List[QFrame] = []
        self._next_meeting: Optional[Any] = None
        self._feed_search_timer = QTimer(self)
        self._feed_search_timer.setSingleShot(True)
        self._feed_search_timer.setInterval(200)
        self._feed_search_timer.timeout.connect(self.refresh_feed)
        self._build_ui()
        self.apply_theme(self._theme)

        self.controller.history_updated.connect(self.refresh_all)
        self.controller.dictation_completed.connect(lambda *_: self.refresh_all())
        if hasattr(self.controller, "calendar_upcoming_changed"):
            self.controller.calendar_upcoming_changed.connect(lambda *_: self._refresh_upcoming())
        if hasattr(self.controller, "meeting_saved_and_transcribed"):
            self.controller.meeting_saved_and_transcribed.connect(lambda *_: self.refresh_all())
        if hasattr(self.controller, "meeting_saved"):
            self.controller.meeting_saved.connect(lambda *_: self.refresh_all())

        self.refresh_all()

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "_feed_search_timer") and self._feed_search_timer.isActive():
            self._feed_search_timer.stop()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(16)

        # Editorial header + quick action pills
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        hcol = QVBoxLayout()
        hcol.setSpacing(2)
        self.lbl_hero = QLabel("Don't type, just speak.")
        self.lbl_hero.setFont(get_ui_font(28, 500, serif=True))
        self.lbl_hero_sub = QLabel("Speak naturally in English or Hinglish anywhere — VOX polishes every word locally.")
        self.lbl_hero_sub.setObjectName("hubMuted")
        hcol.addWidget(self.lbl_hero)
        hcol.addWidget(self.lbl_hero_sub)
        hdr.addLayout(hcol, 1)

        self.btn_quick_scratch = QPushButton("Scratchpad")
        self.btn_quick_scratch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_scratch.clicked.connect(
            lambda: self.controller.open_scratchpad_requested.emit()
            if hasattr(self.controller, "open_scratchpad_requested")
            else self.navigate_requested.emit("scratchpad")
        )
        hdr.addWidget(self.btn_quick_scratch)

        self.btn_quick_dictate = QPushButton("Start Dictating")
        self.btn_quick_dictate.setObjectName("dawnBtn")
        self.btn_quick_dictate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_dictate.clicked.connect(self.controller.toggle_dictation)
        hdr.addWidget(self.btn_quick_dictate)
        root.addLayout(hdr)

        # KPI Strip
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self._kpi_words_val, self._kpi_words_sub, c1 = self._make_kpi_card("WORDS SPOKEN", "0", "0 pages drafted")
        self._kpi_wpm_val, self._kpi_wpm_sub, c2 = self._make_kpi_card("AVERAGE SPEED", "0 WPM", "vs ~40 WPM typing")
        self._kpi_streak_val, self._kpi_streak_sub, c3 = self._make_kpi_card("DAY STREAK", "0 days", "Consistent flow")
        self._kpi_saved_val, self._kpi_saved_sub, c4 = self._make_kpi_card("TIME SAVED", "0 min", "Estimated vs keyboard")
        for card in (c1, c2, c3, c4):
            self._kpi_cards.append(card)
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # Next Meeting Spotlight Card (Actionable Join, AI Brief & Local Notetaker)
        self.upcoming_card = QFrame()
        self.upcoming_card.setObjectName("hubCard")
        u_outer = QVBoxLayout(self.upcoming_card)
        u_outer.setContentsMargins(16, 12, 16, 12)
        u_outer.setSpacing(6)

        u_top = QHBoxLayout()
        u_top.setSpacing(8)
        self.lbl_spotlight_badge = QLabel("NOTETAKER READY")
        self.lbl_spotlight_badge.setObjectName("hubSpotlightBadge")
        u_top.addWidget(self.lbl_spotlight_badge)

        self.lbl_spotlight_platform = QLabel("")
        self.lbl_spotlight_platform.setObjectName("hubPlatformPill")
        self.lbl_spotlight_platform.setVisible(False)
        u_top.addWidget(self.lbl_spotlight_platform)
        u_top.addStretch(1)

        self.btn_spotlight_join = QPushButton("Join Meeting")
        self.btn_spotlight_join.setObjectName("hubFathomBtn")
        self.btn_spotlight_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_spotlight_join.setVisible(False)
        self.btn_spotlight_join.clicked.connect(self._on_spotlight_join_clicked)
        u_top.addWidget(self.btn_spotlight_join)

        self.btn_spotlight_brief = QPushButton("AI Brief")
        self.btn_spotlight_brief.setObjectName("hubSecondaryBtn")
        self.btn_spotlight_brief.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_spotlight_brief.setVisible(False)
        self.btn_spotlight_brief.clicked.connect(self._open_spotlight_brief)
        u_top.addWidget(self.btn_spotlight_brief)

        self.btn_spotlight_record = QPushButton("Record Notetaker")
        self.btn_spotlight_record.setObjectName("dawnBtn")
        self.btn_spotlight_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_spotlight_record.setVisible(False)
        self.btn_spotlight_record.clicked.connect(self._on_spotlight_record_clicked)
        u_top.addWidget(self.btn_spotlight_record)

        self.btn_open_calendar = QPushButton("Calendar")
        self.btn_open_calendar.setObjectName("hubSecondaryBtn")
        self.btn_open_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_calendar.clicked.connect(lambda: self.navigate_requested.emit("calendar"))
        u_top.addWidget(self.btn_open_calendar)

        self.btn_open_nt = QPushButton("Open Notetaker")
        self.btn_open_nt.setObjectName("hubSecondaryBtn")
        self.btn_open_nt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_nt.clicked.connect(lambda: self.navigate_requested.emit("notetaker"))
        u_top.addWidget(self.btn_open_nt)
        u_outer.addLayout(u_top)

        self.lbl_upcoming = QLabel("Notetaker ready — detects Teams, Zoom & Meet automatically or start anytime.")
        self.lbl_upcoming.setObjectName("hubMuted")
        self.lbl_upcoming.setWordWrap(True)
        u_outer.addWidget(self.lbl_upcoming)
        root.addWidget(self.upcoming_card)

        # Recent AI Meeting Highlights Strip
        self.recent_meetings_section = QWidget()
        rm_outer = QVBoxLayout(self.recent_meetings_section)
        rm_outer.setContentsMargins(0, 0, 0, 0)
        rm_outer.setSpacing(8)
        rm_hdr = QHBoxLayout()
        rm_hdr.setSpacing(8)
        lbl_rm_title = QLabel("Recent Meeting Notes")
        lbl_rm_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        rm_hdr.addWidget(lbl_rm_title)
        rm_hdr.addStretch(1)
        self.btn_view_all_meetings = QPushButton("View All →")
        self.btn_view_all_meetings.setObjectName("hubSecondaryBtn")
        self.btn_view_all_meetings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_all_meetings.clicked.connect(lambda: self.navigate_requested.emit("notetaker"))
        rm_hdr.addWidget(self.btn_view_all_meetings)
        rm_outer.addLayout(rm_hdr)

        self.recent_meetings_lay = QHBoxLayout()
        self.recent_meetings_lay.setContentsMargins(0, 0, 0, 0)
        self.recent_meetings_lay.setSpacing(10)
        rm_outer.addLayout(self.recent_meetings_lay)
        self.recent_meetings_section.setVisible(False)
        root.addWidget(self.recent_meetings_section)

        # Dictation Feed Search + Header
        feed_hdr = QHBoxLayout()
        feed_hdr.setSpacing(10)
        lbl_feed = QLabel("Recent Dictations")
        lbl_feed.setStyleSheet("font-size: 15px; font-weight: 700;")
        feed_hdr.addWidget(lbl_feed)
        feed_hdr.addStretch(1)
        self.txt_feed_search = QLineEdit()
        self.txt_feed_search.setPlaceholderText("Search recent dictations...")
        self.txt_feed_search.setFixedWidth(280)
        self.txt_feed_search.textChanged.connect(self._schedule_feed_search)
        feed_hdr.addWidget(self.txt_feed_search)
        root.addLayout(feed_hdr)

        # Scrollable Dictation Feed
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.feed_container = QWidget()
        self.feed_layout = QVBoxLayout(self.feed_container)
        self.feed_layout.setContentsMargins(0, 0, 6, 12)
        self.feed_layout.setSpacing(10)
        self.scroll.setWidget(self.feed_container)
        root.addWidget(self.scroll, 1)

    def _make_kpi_card(self, label: str, val: str, sub: str) -> tuple[QLabel, QLabel, QFrame]:
        card = QFrame()
        card.setObjectName("hubCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(3)
        lt = QLabel(label)
        lt.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.08em;")
        lt.setObjectName("hubMuted")
        lv = QLabel(val)
        lv.setStyleSheet("font-size: 22px; font-weight: 700;")
        ls = QLabel(sub)
        ls.setObjectName("hubMuted")
        lay.addWidget(lt)
        lay.addWidget(lv)
        lay.addWidget(ls)
        return lv, ls, card

    def refresh_all(self) -> None:
        self._refresh_kpis()
        self._refresh_upcoming()
        self._refresh_recent_meetings()
        self.refresh_feed()

    def _schedule_feed_search(self, _text: str = "") -> None:
        self._feed_search_timer.start()

    def _refresh_kpis(self) -> None:
        stats = self.controller.db.get_dictation_stats()
        words = int(stats.get("total_words", 0))
        avg_wpm = int(round(float(stats.get("avg_wpm", 0.0))))
        streak = int(stats.get("day_streak", 0))
        saved_min = float(stats.get("minutes_saved", 0.0))

        self._kpi_words_val.setText(f"{words:,}")
        pages = round(words / 250.0, 1)
        self._kpi_words_sub.setText(f"~{pages} pages drafted")
        self._kpi_wpm_val.setText(f"{avg_wpm} WPM" if avg_wpm > 0 else "0 WPM")
        self._kpi_wpm_sub.setText("3.5x faster than typing" if avg_wpm >= 110 else "Hold hotkey & speak naturally")
        self._kpi_streak_val.setText(f"{streak} day" + ("" if streak == 1 else "s"))
        self._kpi_saved_val.setText(f"{int(round(saved_min))} min" if saved_min < 120 else f"{saved_min / 60.0:.1f} hr")

    def _refresh_upcoming(self, meetings: Optional[List[Any]] = None) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        if meetings is None:
            meetings = []
            for attr in ("calendar_engine", "calendar_manager"):
                mgr = getattr(self.controller, attr, None)
                if mgr is not None and hasattr(mgr, "get_upcoming_meetings"):
                    try:
                        res = list(mgr.get_upcoming_meetings() or [])
                        if res:
                            meetings = res
                            break
                    except Exception:
                        pass

        now_local = datetime.now().astimezone()
        chosen = None
        for m in meetings or []:
            if getattr(m, "is_cancelled", False):
                continue
            try:
                end_dt = getattr(m, "local_end_time", None) or getattr(m, "end_time", None)
                if end_dt is not None:
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=now_local.tzinfo)
                    if end_dt >= now_local:
                        chosen = m
                        break
            except Exception:
                pass
        if chosen is None:
            for m in meetings or []:
                if not getattr(m, "is_cancelled", False):
                    chosen = m
                    break

        self._next_meeting = chosen
        if chosen is None:
            self.lbl_spotlight_badge.setText("NOTETAKER READY")
            self.lbl_spotlight_platform.setVisible(False)
            self.btn_spotlight_join.setVisible(False)
            self.btn_spotlight_brief.setVisible(False)
            self.btn_spotlight_record.setVisible(False)
            self.lbl_upcoming.setText("Notetaker ready — auto-detects Teams, Zoom & Meet calls or start anytime.")
            return

        title = str(getattr(chosen, "title", "") or "Upcoming Meeting")
        join_url = str(getattr(chosen, "join_url", "") or "").strip()
        plat = str(getattr(chosen, "meeting_platform_label", "") or getattr(chosen, "platform", "") or "").strip()
        org = str(getattr(chosen, "organizer_name", "") or getattr(chosen, "organizer", "") or "").strip()
        atts = list(getattr(chosen, "attendees", []) or [])

        badge_txt = "NEXT MEETING"
        time_str = ""
        try:
            st = getattr(chosen, "local_start_time", None) or getattr(chosen, "start_time", None)
            et = getattr(chosen, "local_end_time", None) or getattr(chosen, "end_time", None)
            if st is not None:
                if st.tzinfo is None:
                    st = st.replace(tzinfo=now_local.tzinfo)
                if et is not None and et.tzinfo is None:
                    et = et.replace(tzinfo=now_local.tzinfo)
                if et is not None and st <= now_local <= et:
                    badge_txt = "● LIVE NOW"
                elif st > now_local:
                    mins = max(1, int((st - now_local).total_seconds() // 60))
                    if mins < 60:
                        badge_txt = f"NEXT MEETING · IN {mins}M"
                    else:
                        badge_txt = f"NEXT MEETING · {st.strftime('%I:%M %p').lstrip('0')}"
                time_str = st.strftime("%a, %I:%M %p").lstrip("0")
        except Exception:
            pass

        self.lbl_spotlight_badge.setText(badge_txt)
        if plat and plat.lower() not in {"generic", "calendar event"}:
            self.lbl_spotlight_platform.setText(plat)
            self.lbl_spotlight_platform.setVisible(True)
        else:
            self.lbl_spotlight_platform.setVisible(False)

        self.btn_spotlight_join.setVisible(bool(join_url))
        self.btn_spotlight_brief.setVisible(True)
        self.btn_spotlight_record.setVisible(True)

        details: List[str] = [title]
        if time_str:
            details.append(time_str)
        if org:
            details.append(f"Host: {org}")
        if atts:
            details.append(f"{len(atts)} attendee{'s' if len(atts) != 1 else ''}")
        self.lbl_upcoming.setText(" · ".join(details))

    def _on_spotlight_join_clicked(self) -> None:
        m = self._next_meeting
        if m is None:
            return
        if hasattr(self.controller, "start_meeting_from_calendar"):
            ok, msg = self.controller.start_meeting_from_calendar(m)
            self.status_toast.emit("Joining Meeting" if ok else "Meeting Notice", msg or "Opening meeting...")
            return
        join_url = str(getattr(m, "join_url", "") or "").strip()
        if join_url:
            QDesktopServices.openUrl(QUrl(join_url))

    def _open_spotlight_brief(self) -> None:
        db = getattr(self.controller, "db", None)
        if db is None:
            return
        topic = str(getattr(self._next_meeting, "title", "") or "")
        engine = getattr(self.controller, "intelligence", None) or MeetingIntelligenceEngine(db)
        dlg = VoxPreMeetingBriefingDialog(db, engine, theme=self._theme, initial_topic=topic, parent=self)
        dlg.exec()

    def _on_spotlight_record_clicked(self) -> None:
        m = self._next_meeting
        if m is None:
            return
        self.prepare_meeting_requested.emit(m)

    def _clear_layout_widgets(self, lay: Any) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                self._clear_layout_widgets(it.layout())

    def _clean_meeting_snippet(self, summary_raw: str) -> str:
        lines = []
        for raw_ln in str(summary_raw or "").splitlines():
            ln = raw_ln.strip()
            if not ln or set(ln) <= {"=", "-", "*"}:
                continue
            if ln.startswith("#"):
                ln = ln.lstrip("# ").strip()
            if ln:
                lines.append(ln)
            if len(" ".join(lines)) >= 110:
                break
        txt = " ".join(lines).strip()
        if not txt:
            return "Open to review transcript, decisions & follow-ups."
        return txt[:110] + ("..." if len(txt) > 110 else "")

    def _refresh_recent_meetings(self) -> None:
        if not hasattr(self, "recent_meetings_lay"):
            return
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or not hasattr(db, "list_meetings") or (
            db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists()
        ):
            self.recent_meetings_section.setVisible(False)
            return

        self._clear_layout_widgets(self.recent_meetings_lay)
        try:
            meetings = list(db.list_meetings(limit=3) or [])
        except Exception:
            meetings = []

        if not meetings:
            self.recent_meetings_section.setVisible(False)
            return

        t = get_theme_tokens(self._theme)
        for m in meetings[:3]:
            mid = int(m["id"])
            title = str(m.get("title") or "Untitled Meeting").strip()
            status = str(m.get("status") or "completed").lower()
            platform = str(m.get("source_platform") or m.get("platform") or "Local").strip()
            snippet = self._clean_meeting_snippet(str(m.get("summary") or ""))

            open_actions = 0
            if hasattr(db, "list_action_items"):
                try:
                    actions = db.list_action_items(meeting_id=mid) or []
                    open_actions = sum(1 for a in actions if str(a.get("status") or "").lower() != "completed")
                except Exception:
                    open_actions = 0

            card = QFrame()
            card.setObjectName("hubMeetingCard")
            clay = QVBoxLayout(card)
            clay.setContentsMargins(14, 10, 14, 10)
            clay.setSpacing(6)

            top = QHBoxLayout()
            top.setSpacing(6)
            lbl_t = QLabel(title if len(title) <= 32 else (title[:29] + "..."))
            lbl_t.setToolTip(title)
            lbl_t.setStyleSheet("font-size: 13px; font-weight: 700;")
            top.addWidget(lbl_t, 1)

            if status == "interrupted":
                badge = QLabel("⚠ Interrupted")
                badge.setStyleSheet(
                    f"background: {t['status_warning_bg']}; color: {t['status_warning_text']}; "
                    f"border: 1px solid {t['status_warning']}; border-radius: 6px; padding: 1px 7px; "
                    "font-size: 10px; font-weight: 700;"
                )
            else:
                badge = QLabel(platform)
                badge.setStyleSheet(
                    f"background: {t['chip_bg']}; color: {t['text_secondary']}; border: 1px solid {t['chip_border']}; "
                    "border-radius: 6px; padding: 1px 7px; font-size: 10px; font-weight: 600;"
                )
            top.addWidget(badge)
            clay.addLayout(top)

            lbl_snip = QLabel(snippet)
            lbl_snip.setObjectName("hubMuted")
            lbl_snip.setWordWrap(True)
            clay.addWidget(lbl_snip, 1)

            ftr = QHBoxLayout()
            ftr.setSpacing(6)
            act_txt = f"{open_actions} open action{'s' if open_actions != 1 else ''}" if open_actions else "No pending actions"
            lbl_act = QLabel(act_txt)
            lbl_act.setStyleSheet(
                f"color: {t['accent_fathom'] if open_actions else t['text_muted']}; font-size: 11px; font-weight: 600;"
            )
            ftr.addWidget(lbl_act, 1)

            btn_open = QPushButton("Open Notes →")
            btn_open.setObjectName("hubSecondaryBtn")
            btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_open.clicked.connect(lambda _=False, target_id=mid: self.open_meeting_requested.emit(target_id))
            ftr.addWidget(btn_open)
            clay.addLayout(ftr)

            self.recent_meetings_lay.addWidget(card, 1)

        self.recent_meetings_section.setVisible(True)

    def refresh_feed(self) -> None:
        if getattr(self.controller, "_cancel_ask_vox", False):
            return
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or (db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists()):
            return
        while self.feed_layout.count():
            it = self.feed_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        q = self.txt_feed_search.text().strip()
        rows = self.controller.db.list_dictation_history(search_query=q, limit=120)
        if not rows:
            empty = QFrame()
            empty.setObjectName("hubCard")
            el = QVBoxLayout(empty)
            el.setContentsMargins(24, 28, 24, 28)
            el.setSpacing(6)
            t = QLabel("No dictations yet")
            t.setStyleSheet("font-size: 15px; font-weight: 700;")
            s = QLabel(
                f"Hold or tap {str(getattr(self.controller.config, 'ptt_hotkey', 'ctrl+win')).upper()} "
                "anywhere on your PC to speak your first note."
            )
            s.setObjectName("hubMuted")
            el.addWidget(t)
            el.addWidget(s)
            self.feed_layout.addWidget(empty)
            self.feed_layout.addStretch(1)
            return

        current_bucket = None
        for row in rows:
            bucket = _safe_date_bucket(str(row.get("created_at") or ""))
            if bucket != current_bucket:
                current_bucket = bucket
                hdr = QLabel(bucket)
                hdr.setStyleSheet("font-size: 11px; font-weight: 700; letter-spacing: 0.08em; padding-top: 6px;")
                hdr.setObjectName("hubMuted")
                self.feed_layout.addWidget(hdr)
            self.feed_layout.addWidget(self._build_feed_card(row))
        self.feed_layout.addStretch(1)

    def _build_feed_card(self, row: Dict[str, Any]) -> QFrame:
        t = get_theme_tokens(self._theme)
        entry_id = int(row["id"])
        final_text = str(row.get("edited_text") or row.get("final_text") or row.get("raw_text") or "").strip()
        app_cat = str(row.get("app_category") or "other").capitalize()
        proc = str(row.get("target_process") or row.get("target_window") or "").strip()
        wpm = int(round(float(row.get("wpm") or 0.0)))
        created = str(row.get("created_at") or "")[11:16] or "Recent"
        undone = bool(row.get("undone"))

        card = QFrame()
        card.setObjectName("hubCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        meta_txt = f"{created}"
        if proc:
            meta_txt += f" · {proc[:28]}"
        lbl_meta = QLabel(meta_txt)
        lbl_meta.setObjectName("hubMuted")
        top.addWidget(lbl_meta)

        chip = QLabel(app_cat)
        chip.setStyleSheet(
            f"background: {t['chip_bg']}; color: {t['text_secondary']}; border: 1px solid {t['chip_border']}; "
            "border-radius: 6px; padding: 1px 8px; font-size: 10.5px; font-weight: 600;"
        )
        top.addWidget(chip)

        if wpm > 0:
            wchip = QLabel(f"{wpm} WPM")
            wchip.setStyleSheet(
                f"background: {t['accent_fathom_tint']}; color: {t['accent_fathom']}; "
                "border-radius: 6px; padding: 1px 8px; font-size: 10.5px; font-weight: 700;"
            )
            top.addWidget(wchip)

        if undone:
            uchip = QLabel("Original")
            uchip.setStyleSheet(
                f"background: {t['accent_lumen']}; color: {t['text']}; border: 1px solid {t['accent_lumen_border']}; "
                "border-radius: 6px; padding: 1px 8px; font-size: 10.5px;"
            )
            top.addWidget(uchip)

        top.addStretch(1)

        btn_copy = QPushButton("Copy")
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.clicked.connect(lambda _=False, txt=final_text: self._copy_entry(txt))
        top.addWidget(btn_copy)

        btn_edit = QPushButton("Edit")
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.clicked.connect(lambda _=False, eid=entry_id, txt=final_text: self._edit_entry(eid, txt))
        top.addWidget(btn_edit)

        btn_ai = QPushButton("AI Polish")
        btn_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ai.clicked.connect(lambda _=False, eid=entry_id, txt=final_text: self._transform_entry_menu(eid, txt, btn_ai))
        top.addWidget(btn_ai)

        if not undone and row.get("raw_text") and row.get("raw_text") != final_text:
            btn_undo = QPushButton("Undo AI")
            btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_undo.clicked.connect(lambda _=False, eid=entry_id: self._undo_entry(eid))
            top.addWidget(btn_undo)

        btn_del = QPushButton("✕")
        btn_del.setFixedWidth(26)
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda _=False, eid=entry_id: self._delete_entry(eid))
        top.addWidget(btn_del)
        lay.addLayout(top)

        body = QLabel(final_text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size: 13.5px;")
        lay.addWidget(body)
        return card

    def _copy_entry(self, txt: str) -> None:
        QApplication.clipboard().setText(txt)
        self.status_toast.emit("Copied", "Dictation copied to clipboard.")

    def _edit_entry(self, entry_id: int, current_txt: str) -> None:
        new_txt, ok = QInputDialog.getMultiLineText(
            self,
            "Edit & Teach VOX Dictionary",
            "Correct any spelling or name — VOX will automatically learn corrections into your Dictionary:",
            current_txt,
        )
        if ok and new_txt.strip() and new_txt.strip() != current_txt:
            self.controller.apply_dictation_correction(entry_id, new_txt.strip())
            self.refresh_feed()
            self.status_toast.emit("Saved Correction", "Updated entry and checked Dictionary auto-learn.")

    def _transform_entry_menu(self, entry_id: int, txt: str, anchor: QPushButton) -> None:
        menu = QMenu(self)
        for label, inst in (
            ("Polish Clarity", "polish"),
            ("Clean Up Concise", "cleanup"),
            ("Format as Email", "email"),
            ("Format as Bullets", "bullets"),
            ("Casual Tone", "casual"),
            ("Formal Tone", "formal"),
        ):
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, i=inst: self._apply_transform(entry_id, txt, i))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _apply_transform(self, entry_id: int, txt: str, instruction: str) -> None:
        out = self.controller.transform_text(txt, instruction=instruction)
        if out:
            self.controller.db.update_dictation_entry(entry_id, final_text=out)
            self.refresh_feed()
            self.status_toast.emit("AI Transformed", f"Applied '{instruction}' style cleanly.")

    def _undo_entry(self, entry_id: int) -> None:
        raw = self.controller.undo_ai_edit(entry_id)
        if raw is not None:
            self.refresh_feed()
            self.status_toast.emit("Reverted", "Restored original spoken transcript.")

    def _delete_entry(self, entry_id: int) -> None:
        self.controller.db.delete_dictation_entry(entry_id)
        self.refresh_all()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#hubMuted {{ color: {t['text_muted']}; font-size: 11.5px; }}
            QLabel#hubSpotlightBadge {{
                color: {t['accent_fathom']};
                font-size: 10.5px;
                font-weight: 700;
                letter-spacing: 0.08em;
            }}
            QLabel#hubPlatformPill {{
                background: {t['accent_fathom_tint']};
                color: {t['accent_fathom']};
                border: 1px solid {t['accent_fathom']};
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 10.5px;
                font-weight: 600;
            }}
            QFrame#hubCard, QFrame#hubMeetingCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            QPushButton#hubFathomBtn {{
                background-color: {t['fathom_btn_bg']};
                color: {t['fathom_btn_text']};
                border: 1px solid {t['fathom_btn_border']};
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#hubFathomBtn:hover {{
                background-color: {t['fathom_btn_hover']};
            }}
            QPushButton#hubSecondaryBtn {{
                background-color: {t['surface_secondary']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 8px;
                padding: 5px 11px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#hubSecondaryBtn:hover {{
                background-color: {t['sidebar_hover']};
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 6px 13px;
                font-weight: 700;
            }}
            """
        )
        self._refresh_recent_meetings()
        self.refresh_feed()

    set_theme = apply_theme


# ==============================================================================
# 2. DICTIONARY PAGE
# ==============================================================================
class DictionaryPage(QWidget):
    """Custom vocabulary manager supporting auto-learned badges, search, sort & bulk delete."""

    status_toast = pyqtSignal(str, str)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._selected_ids: set[int] = set()
        self._build_ui()
        self.apply_theme(self._theme)
        if hasattr(self.controller, "vocabulary_updated"):
            self.controller.vocabulary_updated.connect(self.refresh)
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(14)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("Dictionary")
        lbl.setFont(get_ui_font(26, 500, serif=True))
        sub = QLabel("Teach VOX names, jargon, acronyms & spellings — or let VOX auto-learn when you edit text.")
        sub.setObjectName("dictMuted")
        col.addWidget(lbl)
        col.addWidget(sub)
        hdr.addLayout(col, 1)

        btn_import = QPushButton("Import")
        btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_import.clicked.connect(self._import_vocab)
        btn_export = QPushButton("Export")
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.clicked.connect(self._export_vocab)
        hdr.addWidget(btn_import)
        hdr.addWidget(btn_export)
        root.addLayout(hdr)

        # Quick add card
        add_card = QFrame()
        add_card.setObjectName("dictCard")
        al = QHBoxLayout(add_card)
        al.setContentsMargins(14, 12, 14, 12)
        al.setSpacing(8)
        self.txt_spoken = QLineEdit()
        self.txt_spoken.setPlaceholderText("Spoken phrase or misspelling (e.g. gorjana, cogs)")
        self.txt_repl = QLineEdit()
        self.txt_repl.setPlaceholderText("Correct replacement (e.g. Gorjana, COGS)")
        self.combo_cat = QComboBox()
        for c in ("General", "People", "Company", "Technical", "Hinglish"):
            self.combo_cat.addItem(c, c)
        btn_add = QPushButton("Add Word")
        btn_add.setObjectName("dawnBtn")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._add_word)
        al.addWidget(self.txt_spoken, 1)
        al.addWidget(QLabel("→"))
        al.addWidget(self.txt_repl, 1)
        al.addWidget(self.combo_cat)
        al.addWidget(btn_add)
        root.addWidget(add_card)

        # Filter + Sort + Bulk Delete bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search dictionary...")
        self.txt_search.textChanged.connect(self.refresh)
        bar.addWidget(self.txt_search, 1)

        self.combo_sort = QComboBox()
        for label, key in (
            ("A – Z", "az"),
            ("Starred First", "starred"),
            ("Most Used", "most_used"),
            ("Newest", "newest"),
            ("Oldest", "oldest"),
        ):
            self.combo_sort.addItem(label, key)
        self.combo_sort.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.combo_sort)

        self.btn_bulk_del = QPushButton("Delete Selected (0)")
        self.btn_bulk_del.setVisible(False)
        self.btn_bulk_del.clicked.connect(self._bulk_delete)
        bar.addWidget(self.btn_bulk_del)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_wrap = QWidget()
        self.list_lay = QVBoxLayout(self.list_wrap)
        self.list_lay.setContentsMargins(0, 0, 6, 10)
        self.list_lay.setSpacing(8)
        self.scroll.setWidget(self.list_wrap)
        root.addWidget(self.scroll, 1)

    def refresh(self) -> None:
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        q = self.txt_search.text().strip()
        sort_by = str(self.combo_sort.currentData() or "az")
        rows = self.controller.db.search_vocabulary(query=q, sort_by=sort_by, active_only=False)
        valid_ids = {int(r["id"]) for r in rows}
        self._selected_ids &= valid_ids
        self._update_bulk_btn()

        if not rows:
            empty = VoxEmptyStateWidget(
                icon="📖",
                title="Your Dictionary is Empty",
                subtitle="Add technical terms, brand names, acronyms, or Hinglish phrases so VOX spells them perfectly.",
                theme=self._theme,
                parent=self,
            )
            empty.add_starter_card("ChatGPT", "AI assistant spelling", "chatgpt", tag="Term")
            empty.add_starter_card("Kubernetes", "DevOps vocabulary", "k8s", tag="Tech")
            empty.add_starter_card("Hinglish", "Bilingual phrase", "hinglish", tag="Slang")

            def _apply_dict_starter(key: str) -> None:
                samples = {
                    "chatgpt": ("ChatGPT", "OpenAI conversational language model", "AI"),
                    "k8s": ("Kubernetes", "Container orchestration system", "DevOps"),
                    "hinglish": ("Accha", "Understood / Okay conversational cue", "General"),
                }
                w, d, c = samples.get(key, ("", "", ""))
                if w:
                    self.txt_word.setText(w)
                    self.txt_notes.setText(d)
                    self.combo_cat.setCurrentText(c)

            empty.starter_clicked.connect(_apply_dict_starter)
            empty.btn_action.setText("Add New Word")
            empty.btn_action.clicked.connect(lambda: self.txt_word.setFocus())
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            return

        t = get_theme_tokens(self._theme)
        for row in rows:
            vid = int(row["id"])
            card = QFrame()
            card.setObjectName("dictCard")
            rl = QHBoxLayout(card)
            rl.setContentsMargins(12, 9, 12, 9)
            rl.setSpacing(10)

            cb = QCheckBox()
            cb.setChecked(vid in self._selected_ids)
            cb.toggled.connect(lambda checked, i=vid: self._toggle_select(i, checked))
            rl.addWidget(cb)

            star = QPushButton("★" if row.get("is_starred") else "☆")
            star.setFixedWidth(26)
            star.setStyleSheet(
                f"background: transparent; border: none; font-size: 15px; "
                f"color: {t['accent_amber'] if row.get('is_starred') else t['text_muted']};"
            )
            star.clicked.connect(lambda _=False, i=vid, cur=bool(row.get("is_starred")): self._toggle_star(i, not cur))
            rl.addWidget(star)

            phrase_lbl = QLabel(f"<b>{row.get('spoken_phrase')}</b>  →  {row.get('replacement')}")
            rl.addWidget(phrase_lbl, 1)

            if row.get("auto_learned"):
                b = QLabel("Auto-learned")
                b.setStyleSheet(
                    f"background: {t['accent_dawn']}; color: {t['accent_dawn_text']}; "
                    "border-radius: 6px; padding: 2px 8px; font-size: 10.5px; font-weight: 700;"
                )
                rl.addWidget(b)

            cat_lbl = QLabel(str(row.get("category") or "General"))
            cat_lbl.setStyleSheet(
                f"background: {t['chip_bg']}; color: {t['text_muted']}; "
                "border-radius: 6px; padding: 2px 8px; font-size: 10.5px;"
            )
            rl.addWidget(cat_lbl)

            btn_del = QPushButton("Delete")
            btn_del.clicked.connect(lambda _=False, i=vid: self._delete_one(i))
            rl.addWidget(btn_del)
            self.list_lay.addWidget(card)
        self.list_lay.addStretch(1)

    def _add_word(self) -> None:
        sp = self.txt_spoken.text().strip()
        rep = self.txt_repl.text().strip() or sp
        if not sp:
            return
        self.controller.db.upsert_vocabulary(sp, rep, category=str(self.combo_cat.currentData() or "General"))
        self.txt_spoken.clear()
        self.txt_repl.clear()
        self.controller.vocabulary_updated.emit()

    def _toggle_star(self, vid: int, starred: bool) -> None:
        self.controller.db.set_vocabulary_starred(vid, starred)
        self.refresh()

    def _toggle_select(self, vid: int, checked: bool) -> None:
        if checked:
            self._selected_ids.add(vid)
        else:
            self._selected_ids.discard(vid)
        self._update_bulk_btn()

    def _update_bulk_btn(self) -> None:
        n = len(self._selected_ids)
        self.btn_bulk_del.setText(f"Delete Selected ({n})")
        self.btn_bulk_del.setVisible(n > 0)

    def _bulk_delete(self) -> None:
        if self._selected_ids:
            self.controller.db.delete_vocabulary_bulk(list(self._selected_ids))
            self._selected_ids.clear()
            self.controller.vocabulary_updated.emit()

    def _delete_one(self, vid: int) -> None:
        self.controller.db.delete_vocabulary(vid)
        self.controller.vocabulary_updated.emit()

    def _import_vocab(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Vocabulary", "", "CSV/TXT (*.csv *.txt *.json)")
        if not path:
            return
        p = Path(path)
        count = 0
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
            for item in (data if isinstance(data, list) else []):
                sp = str(item.get("spoken_phrase") or "").strip()
                rep = str(item.get("replacement") or sp).strip()
                if sp:
                    self.controller.db.upsert_vocabulary(sp, rep, str(item.get("category") or "General"))
                    count += 1
        else:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = [s.strip() for s in (line.split(",") if "," in line else line.split("->"))]
                if parts and parts[0]:
                    self.controller.db.upsert_vocabulary(parts[0], parts[1] if len(parts) > 1 and parts[1] else parts[0])
                    count += 1
        self.controller.vocabulary_updated.emit()
        self.status_toast.emit("Dictionary Imported", f"Added or updated {count} entries.")

    def _export_vocab(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Dictionary", "vox_dictionary.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        rows = self.controller.db.list_vocabulary(active_only=False)
        p = Path(path)
        if p.suffix.lower() == ".json":
            p.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        else:
            with p.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["spoken_phrase", "replacement", "category"])
                for r in rows:
                    w.writerow([r.get("spoken_phrase", ""), r.get("replacement", ""), r.get("category", "General")])
        self.status_toast.emit("Dictionary Exported", f"Saved {len(rows)} words to {p.name}")

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#dictMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            QFrame#dictCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 700;
            }}
            """
        )
        self.refresh()

    set_theme = apply_theme


# ==============================================================================
# 3. SNIPPETS PAGE
# ==============================================================================
class SnippetsPage(QWidget):
    """Voice snippet expansion rules (say short trigger -> insert full template block)."""

    status_toast = pyqtSignal(str, str)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._selected_ids: set[int] = set()
        self._build_ui()
        self.apply_theme(self._theme)
        if hasattr(self.controller, "snippets_updated"):
            self.controller.snippets_updated.connect(self.refresh)
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(14)

        lbl = QLabel("Snippets")
        lbl.setFont(get_ui_font(26, 500, serif=True))
        sub = QLabel("Speak a short voice trigger phrase to automatically insert boilerplate paragraphs, links, or replies.")
        sub.setObjectName("snipMuted")
        root.addWidget(lbl)
        root.addWidget(sub)

        add_card = QFrame()
        add_card.setObjectName("snipCard")
        al = QVBoxLayout(add_card)
        al.setContentsMargins(14, 12, 14, 12)
        al.setSpacing(8)
        top_r = QHBoxLayout()
        self.txt_trig = QLineEdit()
        self.txt_trig.setPlaceholderText("Spoken trigger phrase (e.g. my calendly, intro bio, zoom link)")
        btn_add = QPushButton("Add Snippet")
        btn_add.setObjectName("dawnBtn")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._add_snippet)
        top_r.addWidget(self.txt_trig, 1)
        top_r.addWidget(btn_add)
        al.addLayout(top_r)

        self.txt_exp = QTextEdit()
        self.txt_exp.setPlaceholderText("Expansion text inserted whenever you say the trigger phrase...")
        self.txt_exp.setFixedHeight(68)
        al.addWidget(self.txt_exp)
        root.addWidget(add_card)

        # Aliases for accessible text input bindings
        self.txt_trigger = self.txt_trig
        self.txt_content = self.txt_exp

        bar = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search snippets...")
        self.txt_search.textChanged.connect(self.refresh)
        bar.addWidget(self.txt_search, 1)

        self.combo_sort = QComboBox()
        for l_txt, k in (("Newest", "newest"), ("Most Used", "most_used"), ("Starred", "starred"), ("A – Z", "az")):
            self.combo_sort.addItem(l_txt, k)
        self.combo_sort.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.combo_sort)

        self.btn_bulk_del = QPushButton("Delete Selected (0)")
        self.btn_bulk_del.setVisible(False)
        self.btn_bulk_del.clicked.connect(self._bulk_delete)
        bar.addWidget(self.btn_bulk_del)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_wrap = QWidget()
        self.list_lay = QVBoxLayout(self.list_wrap)
        self.list_lay.setContentsMargins(0, 0, 6, 10)
        self.list_lay.setSpacing(8)
        self.scroll.setWidget(self.list_wrap)
        root.addWidget(self.scroll, 1)

    def refresh(self) -> None:
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        rows = self.controller.db.list_snippets(
            search_query=self.txt_search.text().strip(),
            sort_by=str(self.combo_sort.currentData() or "newest"),
        )
        valid_ids = {int(r["id"]) for r in rows}
        self._selected_ids &= valid_ids
        self.btn_bulk_del.setVisible(bool(self._selected_ids))
        self.btn_bulk_del.setText(f"Delete Selected ({len(self._selected_ids)})")

        if not rows:
            lbl_empty = QLabel("No voice snippets recorded yet. Speak shortcuts or pick a starter template below.")
            lbl_empty.setObjectName("snipEmptyLabel")
            lbl_empty.setWordWrap(True)
            self.list_lay.addWidget(lbl_empty)

            empty = VoxEmptyStateWidget(
                icon="⚡",
                title="No Voice Snippets Created",
                subtitle="Create voice shortcuts that expand short spoken cues into long text blocks or email templates.",
                theme=self._theme,
                parent=self,
            )
            empty.add_starter_card("Meeting Follow-up", "Quick minutes summary cue", "followup", tag="Template")
            empty.add_starter_card("Quick Sign-off", "Professional email closing", "signoff", tag="Email")
            empty.add_starter_card("Bug Report Template", "Structured QA defect report", "bugreport", tag="Dev")

            def _apply_snip_starter(key: str) -> None:
                samples = {
                    "followup": ("Thanks for meeting today. Here are our key takeaways and next steps.", ";followup"),
                    "signoff": ("Best regards,\nSent with VOX local voice intelligence.", ";signoff"),
                    "bugreport": ("**Steps to reproduce:**\n1. \n**Expected:** \n**Actual:** ", ";bug"),
                }
                body, cue = samples.get(key, ("", ""))
                if body:
                    self.txt_exp.setPlainText(body)
                    self.txt_trig.setText(cue)
                    self.txt_content.setPlainText(body)
                    self.txt_trigger.setText(cue)
                    self.controller.db.upsert_snippet(cue, body)
                    self.refresh()

            empty.starter_clicked.connect(_apply_snip_starter)
            empty.btn_action.setText("Create New Snippet")
            empty.btn_action.clicked.connect(lambda: self.txt_trig.setFocus())
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            return

        t = get_theme_tokens(self._theme)
        for row in rows:
            sid = int(row["id"])
            card = QFrame()
            card.setObjectName("snipCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(6)

            top = QHBoxLayout()
            cb = QCheckBox()
            cb.setChecked(sid in self._selected_ids)
            cb.toggled.connect(lambda ch, i=sid: self._toggle_sel(i, ch))
            top.addWidget(cb)

            star = QPushButton("★" if row.get("is_starred") else "☆")
            star.setFixedWidth(24)
            star.setStyleSheet(
                f"background: transparent; border: none; font-size: 15px; "
                f"color: {t['accent_amber'] if row.get('is_starred') else t['text_muted']};"
            )
            star.clicked.connect(lambda _=False, i=sid, s=not bool(row.get("is_starred")): self._toggle_star(i, s))
            top.addWidget(star)

            trig_lbl = QLabel(f"Say \"{row.get('trigger_phrase')}\"")
            trig_lbl.setStyleSheet("font-size: 13.5px; font-weight: 700;")
            top.addWidget(trig_lbl, 1)

            uses = QLabel(f"{int(row.get('use_count') or 0)} uses")
            uses.setObjectName("snipMuted")
            top.addWidget(uses)

            btn_edit = QPushButton("Edit")
            btn_edit.clicked.connect(lambda _=False, r=row: self._edit_snippet(r))
            btn_del = QPushButton("Delete")
            btn_del.clicked.connect(lambda _=False, i=sid: self._delete_snippet(i))
            top.addWidget(btn_edit)
            top.addWidget(btn_del)
            cl.addLayout(top)

            exp_lbl = QLabel(str(row.get("expansion") or ""))
            exp_lbl.setWordWrap(True)
            exp_lbl.setObjectName("snipMuted")
            cl.addWidget(exp_lbl)
            self.list_lay.addWidget(card)
        self.list_lay.addStretch(1)

    def _add_snippet(self) -> None:
        trig = self.txt_trig.text().strip()
        exp = self.txt_exp.toPlainText().strip()
        if not trig or not exp:
            self.status_toast.emit("Incomplete Snippet", "Enter both a spoken trigger phrase and expansion text.")
            return
        self.controller.db.upsert_snippet(trig, exp)
        self.txt_trig.clear()
        self.txt_exp.clear()
        if hasattr(self.controller, "snippets_updated"):
            self.controller.snippets_updated.emit()

    def _edit_snippet(self, row: Dict[str, Any]) -> None:
        new_exp, ok = QInputDialog.getMultiLineText(
            self,
            f"Edit Snippet: {row.get('trigger_phrase')}",
            "Expansion text:",
            str(row.get("expansion") or ""),
        )
        if ok and new_exp.strip():
            self.controller.db.upsert_snippet(
                str(row.get("trigger_phrase") or ""),
                new_exp.strip(),
                is_starred=int(row.get("is_starred") or 0),
                snippet_id=int(row["id"]),
            )
            self.refresh()

    def _toggle_star(self, sid: int, starred: bool) -> None:
        self.controller.db.set_snippet_starred(sid, starred)
        self.refresh()

    def _toggle_sel(self, sid: int, checked: bool) -> None:
        if checked:
            self._selected_ids.add(sid)
        else:
            self._selected_ids.discard(sid)
        self.btn_bulk_del.setVisible(bool(self._selected_ids))
        self.btn_bulk_del.setText(f"Delete Selected ({len(self._selected_ids)})")

    def _bulk_delete(self) -> None:
        if self._selected_ids:
            self.controller.db.delete_snippets_bulk(list(self._selected_ids))
            self._selected_ids.clear()
            self.refresh()

    def _delete_snippet(self, sid: int) -> None:
        self.controller.db.delete_snippet(sid)
        self.refresh()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#snipMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            QFrame#snipCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 700;
            }}
            """
        )
        self.refresh()

    set_theme = apply_theme


# ==============================================================================
# 4. STYLE PAGE (App-Category Tone Cards + Auto Cleanup)
# ==============================================================================
class StylePage(QWidget):
    """Wispr Flow-inspired per-app tone cards and Auto Cleanup intensity picker."""

    status_toast = pyqtSignal(str, str)

    CATEGORIES = [
        ("style_personal", "Personal messages", "WhatsApp, Telegram, Messages, Instagram"),
        ("style_work", "Work messages", "Slack, Microsoft Teams, Discord, Linear"),
        ("style_email", "Email", "Outlook, Gmail, Superhuman"),
        ("style_other", "Other apps", "Notion, Docs, Browsers, Editors"),
    ]

    PRESET_CHOICES = [
        ("formal", "Formal.", "Capitalized, complete sentences with standard business punctuation.", "Hey Alex, just checking in on Thursday's review. Let me know when works!"),
        ("casual", "Casual", "Natural, friendly capitalization and relaxed end punctuation.", "Hey Alex, checking in on Thursday's review, let me know when works"),
        ("very_casual", "very casual", "Lowercase relaxed texting style without trailing periods.", "hey alex just checking in on thursday's review lmk when works"),
        ("excited", "Excited!", "Upbeat conversational energy with expressive punctuation.", "Hey Alex! Just checking in on Thursday's review — let me know when works!"),
    ]

    CLEANUP_LEVELS = [
        ("none", "None", "Keep speech verbatim with minimal edits (light spelling fixes only).", "Um yeah so basically we need to send the draft tomorrow morning."),
        ("light", "Light", "Remove filler words ('um', 'uh', 'like') while preserving your voice.", "Yeah, so basically we need to send the draft tomorrow morning."),
        ("medium", "Medium", "Remove fillers, false starts, and tighten casual phrasing.", "We need to send the draft tomorrow morning."),
        ("high", "High", "Polish grammar, structure lists, and tighten prose cleanly.", "We need to send the finalized draft tomorrow morning."),
    ]

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._active_tab = 0
        self._tab_buttons: List[QPushButton] = []
        self._build_ui()
        self.apply_theme(self._theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(14)

        lbl = QLabel("Style")
        lbl.setFont(get_ui_font(26, 500, serif=True))
        sub = QLabel("VOX automatically adapts formatting and tone to the app you're typing in.")
        sub.setObjectName("styleMuted")
        root.addWidget(lbl)
        root.addWidget(sub)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        for idx, (_field, label, _apps) in enumerate(self.CATEGORIES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self.select_tab(i))
            self._tab_buttons.append(btn)
            tab_row.addWidget(btn)

        btn_clean = QPushButton("Auto cleanup")
        btn_clean.setCheckable(True)
        btn_clean.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clean.clicked.connect(lambda: self.select_tab(len(self.CATEGORIES)))
        self._tab_buttons.append(btn_clean)
        tab_row.addWidget(btn_clean)
        tab_row.addStretch(1)
        root.addLayout(tab_row)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 6, 0, 6)
        self.cards_layout.setSpacing(12)
        root.addWidget(self.cards_container, 1)

        self.select_tab(0)

    def select_tab(self, index: int) -> None:
        self._active_tab = index
        for i, b in enumerate(self._tab_buttons):
            b.setChecked(i == index)
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        while self.cards_layout.count():
            it = self.cards_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                while it.layout().count():
                    cit = it.layout().takeAt(0)
                    cw = cit.widget()
                    if cw is not None:
                        cw.hide()
                        cw.setParent(None)
                        cw.deleteLater()

        t = get_theme_tokens(self._theme)
        cfg = self.controller.config
        if self._active_tab < len(self.CATEGORIES):
            field_name, label, apps_desc = self.CATEGORIES[self._active_tab]
            lbl_ctx = QLabel(f"Applied automatically in: {apps_desc}")
            lbl_ctx.setObjectName("styleMuted")
            self.cards_layout.addWidget(lbl_ctx)

            selected = str(getattr(cfg, field_name, "formal") or "formal").lower()
            grid = QGridLayout()
            grid.setSpacing(12)
            for idx, (val, title, desc, sample) in enumerate(self.PRESET_CHOICES):
                is_sel = (selected == val)
                card = self._make_choice_card(
                    title=title,
                    desc=desc,
                    sample=sample,
                    is_selected=is_sel,
                    on_click=lambda v=val, fn=field_name, lb=label: self._pick_style(fn, v, lb),
                    tokens=t,
                )
                grid.addWidget(card, idx // 2, idx % 2)
            wrap = QWidget()
            wrap.setLayout(grid)
            self.cards_layout.addWidget(wrap)
        else:
            lbl_ctx = QLabel("Spoken filler cleanup and polish intensity across all dictations:")
            lbl_ctx.setObjectName("styleMuted")
            self.cards_layout.addWidget(lbl_ctx)

            selected = str(getattr(cfg, "auto_cleanup_level", "light") or "light").lower()
            grid = QGridLayout()
            grid.setSpacing(12)
            for idx, (val, title, desc, sample) in enumerate(self.CLEANUP_LEVELS):
                is_sel = (selected == val)
                card = self._make_choice_card(
                    title=title,
                    desc=desc,
                    sample=sample,
                    is_selected=is_sel,
                    on_click=lambda v=val: self._pick_cleanup(v),
                    tokens=t,
                )
                grid.addWidget(card, idx // 2, idx % 2)
            wrap = QWidget()
            wrap.setLayout(grid)
            self.cards_layout.addWidget(wrap)

        self.cards_layout.addStretch(1)

    def _make_choice_card(
        self,
        title: str,
        desc: str,
        sample: str,
        is_selected: bool,
        on_click: Callable[[], None],
        tokens: Dict[str, str],
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("styleCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        border_c = tokens["accent_fathom"] if is_selected else tokens["border"]
        bg_c = tokens["accent_dawn_tint"] if is_selected else tokens["surface"]
        card.setStyleSheet(
            f"QFrame#styleCard {{ background-color: {bg_c}; border: {'2px' if is_selected else '1px'} solid {border_c}; "
            "border-radius: 12px; }"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        lt = QLabel(title)
        lt.setFont(get_ui_font(18, 600, serif=True))
        head.addWidget(lt)
        head.addStretch(1)
        if is_selected:
            badge = QLabel("Active")
            badge.setStyleSheet(
                f"background: {tokens['accent_dawn']}; color: {tokens['dawn_btn_text']}; "
                "border: none; border-radius: 6px; padding: 2px 8px; font-size: 10.5px; font-weight: 700;"
            )
            head.addWidget(badge)
        lay.addLayout(head)

        ld = QLabel(desc)
        ld.setWordWrap(True)
        ld.setObjectName("styleMuted")
        lay.addWidget(ld)

        prev = QLabel(f"\"{sample}\"")
        prev.setWordWrap(True)
        prev.setStyleSheet(
            f"background: {tokens['surface_well']}; border: none; border-radius: 8px; padding: 8px 10px; "
            f"font-size: 12px; color: {tokens['text']};"
        )
        lay.addWidget(prev)

        btn = QPushButton("Selected" if is_selected else "Use This Style")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: on_click())
        lay.addWidget(btn)
        return card

    def _pick_style(self, field_name: str, val: str, category_label: str) -> None:
        setattr(self.controller.config, field_name, val)
        self.controller.config.save()
        self._rebuild_cards()
        self.status_toast.emit("Style Updated", f"{category_label} set to '{val.replace('_', ' ')}'.")

    def _pick_cleanup(self, level: str) -> None:
        self.controller.config.auto_cleanup_level = level
        self.controller.config.save()
        self._rebuild_cards()
        self.status_toast.emit("Auto Cleanup Updated", f"Cleanup level set to '{level.capitalize()}'.")

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#styleMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            """
        )
        tab_qss = (
            f"QPushButton {{ background: transparent; color: {t['text_muted']}; border: 1px solid {t['border']}; "
            f"border-radius: 8px; padding: 6px 14px; font-weight: 600; }}"
            f"QPushButton:checked {{ background: {t['sidebar_active']}; color: {t['text']}; border-color: {t['line_hover']}; }}"
        )
        for btn in self._tab_buttons:
            btn.setStyleSheet(tab_qss)
        self._rebuild_cards()

    set_theme = apply_theme


# ==============================================================================
# 5. SCRATCHPAD HUB PAGE
# ==============================================================================
class ScratchpadHubPage(QWidget):
    """Hub view for persistent Scratchpad notes synced with the Floating Scratchpad window."""

    status_toast = pyqtSignal(str, str)

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._current_note_id: Optional[int] = None
        self._loading = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(450)
        self._save_timer.timeout.connect(self._persist_current)

        self._build_ui()
        self.apply_theme(self._theme)
        if hasattr(self.controller, "notes_updated"):
            self.controller.notes_updated.connect(self.refresh_notes)
        self.refresh_notes()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(12)

        hdr = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("Scratchpad")
        lbl.setFont(get_ui_font(26, 500, serif=True))
        sub = QLabel("Quick voice-first notes, drafts & AI transformations synced with your floating Scratchpad window.")
        sub.setObjectName("spMuted")
        col.addWidget(lbl)
        col.addWidget(sub)
        hdr.addLayout(col, 1)

        self.sw_flow_btn = VoxToggleSwitch("Show in Flow Bar", theme=self._theme)
        self.sw_flow_btn.setChecked(bool(getattr(self.controller.config, "scratchpad_in_flow_bar", True)))
        self.sw_flow_btn.toggled.connect(self._on_flow_btn_toggled)
        hdr.addWidget(self.sw_flow_btn)

        btn_pop = QPushButton("Pop-Out Window")
        btn_pop.setObjectName("dawnBtn")
        btn_pop.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pop.clicked.connect(
            lambda: self.controller.open_scratchpad_requested.emit()
            if hasattr(self.controller, "open_scratchpad_requested")
            else None
        )
        hdr.addWidget(btn_pop)
        root.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left_card = QFrame()
        left_card.setObjectName("spCard")
        left_card.setMinimumWidth(240)
        left_card.setMaximumWidth(330)
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(8)
        top_l = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search notes...")
        self.txt_search.textChanged.connect(self.refresh_notes)
        btn_new = QPushButton("+ New")
        btn_new.clicked.connect(self._create_note)
        top_l.addWidget(self.txt_search, 1)
        top_l.addWidget(btn_new)
        ll.addLayout(top_l)

        self.notes_list = QListWidget()
        self.notes_list.currentItemChanged.connect(self._on_note_selected)
        ll.addWidget(self.notes_list, 1)
        split.addWidget(left_card)

        right_card = QFrame()
        right_card.setObjectName("spCard")
        rl = QVBoxLayout(right_card)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(10)

        t_row = QHBoxLayout()
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("Note title...")
        self.txt_title.textChanged.connect(lambda: self._schedule_save())
        t_row.addWidget(self.txt_title, 1)

        self.btn_pin = QPushButton("Pin")
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self._toggle_pin)
        t_row.addWidget(self.btn_pin)

        self.btn_transform = QPushButton("AI Transform")
        self.btn_transform.clicked.connect(self._show_transform_menu)
        t_row.addWidget(self.btn_transform)

        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete_current)
        t_row.addWidget(btn_del)
        rl.addLayout(t_row)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Write or dictate notes here...")
        self.editor.textChanged.connect(lambda: self._schedule_save())
        rl.addWidget(self.editor, 1)
        split.addWidget(right_card)
        split.setSizes([270, 740])
        root.addWidget(split, 1)

    def _on_flow_btn_toggled(self, checked: bool) -> None:
        self.controller.config.scratchpad_in_flow_bar = checked
        self.controller.config.save()

    def refresh_notes(self) -> None:
        notes = self.controller.db.list_notes(search_query=self.txt_search.text().strip())
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        for n in notes:
            pin = "★ " if n.get("is_pinned") else ""
            item = QListWidgetItem(f"{pin}{n.get('title') or 'Untitled note'}")
            item.setData(Qt.ItemDataRole.UserRole, int(n["id"]))
            self.notes_list.addItem(item)
            if int(n["id"]) == self._current_note_id:
                self.notes_list.setCurrentItem(item)
        self.notes_list.blockSignals(False)

        if self._current_note_id is None and self.notes_list.count() > 0:
            self.notes_list.setCurrentRow(0)
            nid = self.notes_list.item(0).data(Qt.ItemDataRole.UserRole)
            self._load_note(int(nid))

    def _on_note_selected(self, curr: Optional[QListWidgetItem], _prev: Optional[QListWidgetItem]) -> None:
        if curr is not None:
            nid = curr.data(Qt.ItemDataRole.UserRole)
            if nid is not None:
                self._load_note(int(nid))

    def _load_note(self, note_id: int) -> None:
        n = self.controller.db.get_note(note_id)
        if not n:
            return
        self._loading = True
        self._current_note_id = note_id
        self.txt_title.setText(str(n.get("title") or ""))
        self.editor.setPlainText(str(n.get("plain_text") or n.get("content") or ""))
        self.btn_pin.setChecked(bool(n.get("is_pinned")))
        self._loading = False

    def _schedule_save(self) -> None:
        if not self._loading and self._current_note_id is not None:
            self._save_timer.start()

    def _persist_current(self) -> None:
        if self._current_note_id is None:
            return
        txt = self.editor.toPlainText()
        title = self.txt_title.text().strip() or "Untitled note"
        self.controller.db.update_note(self._current_note_id, title=title, content=txt, plain_text=txt)

    def _create_note(self) -> None:
        nid = self.controller.db.create_note("Untitled note", "", "")
        self._current_note_id = nid
        self.refresh_notes()
        self._load_note(nid)

    def _toggle_pin(self) -> None:
        if self._current_note_id is None:
            return
        self.controller.db.update_note(self._current_note_id, is_pinned=1 if self.btn_pin.isChecked() else 0, save_version=False)
        self.refresh_notes()

    def _show_transform_menu(self) -> None:
        if self._current_note_id is None:
            return
        menu = QMenu(self)
        for label, inst in (
            ("Polish", "polish"),
            ("Bullets", "bullets"),
            ("Action Items", "action_items"),
            ("Email Draft", "email"),
            ("Concise Cleanup", "cleanup"),
        ):
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, i=inst: self._apply_ai(i))
        menu.exec(self.btn_transform.mapToGlobal(self.btn_transform.rect().bottomLeft()))

    def _apply_ai(self, instruction: str) -> None:
        raw = self.editor.toPlainText().strip()
        if not raw:
            return
        out = self.controller.transform_text(raw, instruction=instruction)
        if out:
            self.editor.setPlainText(out)
            self._persist_current()
            self.status_toast.emit("Transformed", f"Updated note with '{instruction}'.")

    def _delete_current(self) -> None:
        if self._current_note_id is None:
            return
        self.controller.db.delete_note(self._current_note_id)
        self._current_note_id = None
        self.txt_title.clear()
        self.editor.clear()
        self.refresh_notes()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.sw_flow_btn.set_theme(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#spMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            QFrame#spCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            QPushButton#dawnBtn {{
                background-color: {t['dawn_btn_bg']};
                color: {t['dawn_btn_text']};
                border: 1px solid {t['dawn_btn_border']};
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 700;
            }}
            """
        )

    set_theme = apply_theme


# ==============================================================================
# 6. INSIGHTS PAGE (Real SQLite Analytics & Painted Charts)
# ==============================================================================
class DailyWordsChartWidget(QWidget):
    """Lightweight custom-painted 14-day dictation volume chart matching VOX theme."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._series: List[Dict[str, Any]] = []
        self._theme = "light"
        self.setMinimumHeight(180)

    def set_data(self, series: List[Dict[str, Any]], theme: str) -> None:
        self._series = list(series or [])
        self._theme = theme
        self.update()

    def paintEvent(self, _event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = get_theme_tokens(self._theme)
        rect = self.rect().adjusted(12, 16, -12, -26)
        if not self._series:
            p.setPen(QColor(t["text_muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Dictate your first entries to populate 14-day analytics.")
            return

        max_words = max([int(d.get("words") or 0) for d in self._series] + [10])
        n = len(self._series)
        slot_w = rect.width() / max(1, n)
        bar_w = max(8.0, min(28.0, slot_w * 0.58))
        bar_color = QColor(t["accent_fathom"]) if self._theme == "light" else QColor(t["accent_dawn"])

        p.setFont(get_ui_font(9, 600))
        for i, item in enumerate(self._series):
            words = int(item.get("words") or 0)
            frac = min(1.0, words / float(max_words))
            bh = max(4.0, rect.height() * frac) if words > 0 else 2.0
            bx = rect.left() + (i * slot_w) + (slot_w - bar_w) / 2.0
            by = rect.bottom() - bh
            path = QPainterPath()
            path.addRoundedRect(QRectF(bx, by, bar_w, bh), 4.0, 4.0)
            p.fillPath(path, QBrush(bar_color if words > 0 else QColor(t["line"])))
            date_lbl = str(item.get("date") or "")[-5:]
            p.setPen(QColor(t["text_dim"]))
            p.drawText(
                QRectF(bx - 6, rect.bottom() + 4, bar_w + 12, 18),
                Qt.AlignmentFlag.AlignCenter,
                date_lbl,
            )


class InsightsPage(QWidget):
    """Productivity analytics combining Voice Dictation + Meeting Intelligence SQLite metrics."""

    def __init__(self, controller: AppController, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = getattr(controller, "theme", "light")
        self._build_ui()
        self.apply_theme(self._theme)
        self.controller.history_updated.connect(self.refresh)
        self.controller.meeting_saved_and_transcribed.connect(self._on_meeting_saved_refresh)
        self.refresh()

    def _on_meeting_saved_refresh(self, *args: Any) -> None:
        try:
            self.refresh()
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 22)
        root.setSpacing(14)

        lbl = QLabel("Insights")
        lbl.setFont(get_ui_font(26, 500, serif=True))
        sub = QLabel("Local speech velocity, words spoken, time saved, and meeting action tracking.")
        sub.setObjectName("insMuted")
        root.addWidget(lbl)
        root.addWidget(sub)

        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(12)
        self._m_words = self._add_stat(kpi_grid, 0, 0, "TOTAL WORDS DICTATED", "0")
        self._m_wpm = self._add_stat(kpi_grid, 0, 1, "AVERAGE / PEAK WPM", "0 / 0")
        self._m_saved = self._add_stat(kpi_grid, 0, 2, "ESTIMATED TIME SAVED", "0 min")
        self._m_meetings = self._add_stat(kpi_grid, 1, 0, "COMPLETED MEETINGS", "0")
        self._m_actions = self._add_stat(kpi_grid, 1, 1, "OPEN ACTION ITEMS", "0")
        self._m_decisions = self._add_stat(kpi_grid, 1, 2, "CONFIRMED DECISIONS", "0")
        root.addLayout(kpi_grid)

        chart_card = QFrame()
        chart_card.setObjectName("insCard")
        cl = QVBoxLayout(chart_card)
        cl.setContentsMargins(16, 14, 16, 14)
        ct = QLabel("14-Day Dictation Output")
        ct.setStyleSheet("font-size: 14px; font-weight: 700;")
        cl.addWidget(ct)
        self.chart = DailyWordsChartWidget(chart_card)
        cl.addWidget(self.chart)
        root.addWidget(chart_card)

        apps_card = QFrame()
        apps_card.setObjectName("insCard")
        al = QVBoxLayout(apps_card)
        al.setContentsMargins(16, 14, 16, 14)
        at = QLabel("Top Applications Dictated Into")
        at.setStyleSheet("font-size: 14px; font-weight: 700;")
        al.addWidget(at)
        self.lbl_top_apps = QLabel("No app usage recorded yet.")
        self.lbl_top_apps.setObjectName("insMuted")
        self.lbl_top_apps.setWordWrap(True)
        al.addWidget(self.lbl_top_apps)
        root.addWidget(apps_card)
        root.addStretch(1)

    def _add_stat(self, grid: QGridLayout, r: int, c: int, title: str, val: str) -> QLabel:
        card = QFrame()
        card.setObjectName("insCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(3)
        lt = QLabel(title)
        lt.setObjectName("insMuted")
        lt.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.08em;")
        lv = QLabel(val)
        lv.setStyleSheet("font-size: 20px; font-weight: 700;")
        lay.addWidget(lt)
        lay.addWidget(lv)
        grid.addWidget(card, r, c)
        return lv

    def refresh(self) -> None:
        ds = self.controller.db.get_dictation_stats()
        ms = self.controller.db.get_analytics_summary()

        self._m_words.setText(f"{int(ds.get('total_words', 0)):,}")
        self._m_wpm.setText(f"{int(round(ds.get('avg_wpm', 0)))} / {int(round(ds.get('peak_wpm', 0)))}")
        mins = float(ds.get("minutes_saved", 0.0))
        self._m_saved.setText(f"{int(round(mins))} min" if mins < 120 else f"{mins / 60.0:.1f} hr")
        self._m_meetings.setText(str(int(ms.get("total_meetings", 0))))
        self._m_actions.setText(str(int(ms.get("open_action_items", 0))))
        self._m_decisions.setText(str(int(ms.get("confirmed_decisions", 0))))

        self.chart.set_data(ds.get("daily_series", []), self._theme)
        top_apps = ds.get("top_apps") or []
        if top_apps:
            parts = [f"{a.get('app')} ({a.get('words', 0)} words, {a.get('count', 0)}x)" for a in top_apps[:6]]
            self.lbl_top_apps.setText("  ·  ".join(parts))
        else:
            self.lbl_top_apps.setText("Dictate into Teams, Slack, Outlook, or VS Code to see per-app distribution.")

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"""
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; }}
            QLabel {{ background: transparent; border: none; color: {t['text']}; }}
            QLabel#insMuted {{ color: {t['text_muted']}; font-size: 12px; }}
            QFrame#insCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
            """
        )
        self.refresh()

    set_theme = apply_theme

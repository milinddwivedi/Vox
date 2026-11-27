"""VOX Overall Meeting Intelligence View — Cross-Meeting Synthesis, Discovery, Actions, Decisions & Compare."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QTextBrowser,
)

from voxlocal.core.logger import get_logger
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.exporter import format_meeting_local_datetime, format_seconds_hms
from voxlocal.ui.components import VoxSearchField
from voxlocal.ui.meeting_studio import VoxKnowledgeBaseTab
from voxlocal.ui.meeting_ui_helpers import VoxSegmentedControl, render_safe_markdown_html
from voxlocal.ui.theme import get_html_font_family, get_theme_tokens, get_ui_font

logger = get_logger("ui.overall_intelligence")


class VoxOverallIntelligenceView(QWidget):
    """
    Overall Meeting Intelligence workspace operating strictly across the user's
    filtered local meeting library (All Time / Date Range / Folder / Platform / Search).
    """

    open_meeting_requested = pyqtSignal(int, float)
    ask_overall_requested = pyqtSignal(str)
    scope_changed = pyqtSignal(object, str)  # (Optional[List[int]], scope_summary_text)
    rebuild_knowledge_requested = pyqtSignal(bool)

    SUBVIEWS = (
        ("overview", "Overview"),
        ("actions", "Actions"),
        ("decisions", "Decisions"),
        ("knowledge", "Knowledge"),
        ("compare", "Compare"),
    )

    def __init__(
        self,
        db: Any = None,
        engine: Optional[MeetingIntelligenceEngine] = None,
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.engine = engine or (MeetingIntelligenceEngine(db) if db is not None else None)
        self.theme = (theme or "light").lower()
        self._filtered_meetings: List[Dict[str, Any]] = []
        self._filtered_ids: Optional[List[int]] = None
        self._scope_label: str = "All Meetings"
        self._actions_filter: str = "open"
        self._decisions_filter: str = "all"
        self._has_compared: bool = False
        self._needs_refresh: bool = False
        self._dirty_subviews: set[int] = set()
        self._actions_render_limit: int = 40
        self._snap_cache: Dict[str, Any] = {}
        self._last_query: str = ""

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.refresh_data)

        self._build_ui()
        self.apply_theme(self.theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 1. Scope & Filter Bar
        self.header_card = QFrame()
        self.header_card.setObjectName("overallHeaderCard")
        self.header_card.setMinimumWidth(0)
        hc_lay = QVBoxLayout(self.header_card)
        hc_lay.setContentsMargins(14, 10, 14, 10)
        hc_lay.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.lbl_title = QLabel("Overall Meeting Intelligence")
        self.lbl_subtitle = QLabel(
            "Cross-meeting discovery, commitments, decisions, knowledge memory, and comparisons."
        )
        self.lbl_subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_subtitle.setMinimumWidth(0)
        title_col.addWidget(self.lbl_title)
        title_col.addWidget(self.lbl_subtitle)
        title_row.addLayout(title_col, 1)

        self.lbl_scope_pill = QLabel("Scope: All Recorded Meetings")
        self.lbl_scope_pill.setObjectName("overallScopePill")
        self.lbl_scope_pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.lbl_scope_pill.setMaximumWidth(380)
        title_row.addWidget(self.lbl_scope_pill)
        hc_lay.addLayout(title_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.combo_date = QComboBox()
        self.combo_date.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_date.setMinimumContentsLength(9)
        for label, val in (
            ("All Time", "Anytime"),
            ("Today", "Today"),
            ("Last 7 Days", "Last 7 Days"),
            ("Last 30 Days", "Last 30 Days"),
            ("Last 90 Days", "Last 90 Days"),
        ):
            self.combo_date.addItem(label, val)
        self.combo_date.currentIndexChanged.connect(self.refresh_data)
        filter_row.addWidget(self.combo_date)

        self.combo_folder = QComboBox()
        self.combo_folder.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_folder.setMinimumContentsLength(9)
        self.combo_folder.addItem("All Folders", None)
        self.combo_folder.currentIndexChanged.connect(self.refresh_data)
        filter_row.addWidget(self.combo_folder)

        self.combo_platform = QComboBox()
        self.combo_platform.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_platform.setMinimumContentsLength(9)
        for label, val in (
            ("All Platforms", "All Sources"),
            ("Microsoft Teams", "Microsoft Teams"),
            ("Zoom", "Zoom"),
            ("Google Meet", "Google Meet"),
            ("Desktop App", "Desktop App"),
        ):
            self.combo_platform.addItem(label, val)
        self.combo_platform.currentIndexChanged.connect(self.refresh_data)
        filter_row.addWidget(self.combo_platform)

        self.search_input = VoxSearchField("Search all meetings, topics, decisions...")
        self.search_input.setMinimumWidth(120)
        self.search_input.textChanged.connect(lambda _t: self._search_timer.start())
        filter_row.addWidget(self.search_input, 1)

        self.btn_reset_filters = QPushButton("Reset")
        self.btn_reset_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_filters.clicked.connect(self.reset_filters)
        filter_row.addWidget(self.btn_reset_filters)
        hc_lay.addLayout(filter_row)

        # Sub-navigation segmented bar
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self.subnav = VoxSegmentedControl(list(self.SUBVIEWS), parent=self.header_card, theme=self.theme)
        self.subnav.selection_changed.connect(self._on_subnav_changed)
        nav_row.addWidget(self.subnav)
        nav_row.addStretch(1)
        hc_lay.addLayout(nav_row)

        root.addWidget(self.header_card)

        # 2. Stacked Subviews
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)

        # Page 0: Overview & Trends (Single smooth outer scroll)
        self.overview_scroll = QScrollArea()
        self.overview_scroll.setWidgetResizable(True)
        self.overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ov_host = QWidget()
        ov_host.setMinimumWidth(0)
        ov_lay = QVBoxLayout(ov_host)
        ov_lay.setContentsMargins(0, 2, 4, 8)
        ov_lay.setSpacing(10)

        # KPI Strip
        self.kpi_row = QHBoxLayout()
        self.kpi_row.setSpacing(8)
        self._kpi_cards: Dict[str, Tuple[QFrame, QLabel, QLabel]] = {}
        for key, label in (
            ("meetings", "Meetings in Scope"),
            ("duration", "Recorded Hours"),
            ("decisions", "Decisions Logged"),
            ("actions", "Open Commitments"),
            ("knowledge", "Knowledge Items"),
        ):
            card = QFrame()
            card.setObjectName("overallKpiCard")
            card.setMinimumWidth(0)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(2)
            val_lbl = QLabel("0")
            val_lbl.setObjectName("overallKpiVal")
            cap_lbl = QLabel(label)
            cap_lbl.setObjectName("overallKpiCap")
            cl.addWidget(val_lbl)
            cl.addWidget(cap_lbl)
            self.kpi_row.addWidget(card, 1)
            self._kpi_cards[key] = (card, val_lbl, cap_lbl)
        ov_lay.addLayout(self.kpi_row)

        # Quick Global Prompt Strip
        prompt_card = QFrame()
        prompt_card.setObjectName("overallSectionCard")
        prompt_card.setMinimumWidth(0)
        self.prompt_strip_card = prompt_card
        pl = QHBoxLayout(prompt_card)
        pl.setContentsMargins(12, 8, 12, 8)
        pl.setSpacing(6)
        self.lbl_ask_strip = QLabel("Ask Library:")
        pl.addWidget(self.lbl_ask_strip)
        self._prompt_buttons: List[QPushButton] = []
        for lbl, q in (
            ("Topics", "Across these meetings, what recurring topics or projects appear?"),
            ("Commitments", "List open action items and owners across these meetings with timestamps."),
            ("Decisions", "Summarize key decisions made across recent meetings and note any changes or conflicts."),
            ("Blockers", "What blockers, risks, or unresolved issues came up across these meetings?"),
        ):
            b = QPushButton(lbl)
            b.setObjectName("overallChipBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, qq=q: self.ask_overall_requested.emit(qq))
            pl.addWidget(b)
            self._prompt_buttons.append(b)
        pl.addStretch(1)
        ov_lay.addWidget(prompt_card)

        # Single dynamic HTML intelligence canvas for discovery, topics, speakers, and recent outcomes
        self.overview_browser = QTextBrowser()
        self.overview_browser.setObjectName("overallRichBrowser")
        self.overview_browser.setOpenExternalLinks(False)
        self.overview_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.overview_browser.anchorClicked.connect(self._on_anchor_clicked)
        ov_lay.addWidget(self.overview_browser, 1)
        self.overview_scroll.setWidget(ov_host)
        self.stack.addWidget(self.overview_scroll)

        # Page 1: Commitments & Actions across meetings
        act_page = QWidget()
        act_page.setMinimumWidth(0)
        act_lay = QVBoxLayout(act_page)
        act_lay.setContentsMargins(0, 2, 0, 0)
        act_lay.setSpacing(8)
        act_flt = QHBoxLayout()
        act_flt.setSpacing(6)
        self._action_filter_btns: Dict[str, QPushButton] = {}
        for fk, flbl in (
            ("open", "Open"),
            ("all", "All"),
            ("completed", "Completed"),
            ("blocked", "Blocked"),
            ("unassigned", "Unassigned Owner"),
        ):
            btn = QPushButton(flbl)
            btn.setCheckable(True)
            btn.setChecked(fk == "open")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=fk: self._set_action_filter(k))
            act_flt.addWidget(btn)
            self._action_filter_btns[fk] = btn
        act_flt.addStretch(1)
        self.lbl_actions_count = QLabel("0 action items")
        act_flt.addWidget(self.lbl_actions_count)
        act_lay.addLayout(act_flt)

        self.actions_scroll = QScrollArea()
        self.actions_scroll.setWidgetResizable(True)
        self.actions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.actions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.actions_list_host = QWidget()
        self.actions_list_host.setMinimumWidth(0)
        self.actions_list_layout = QVBoxLayout(self.actions_list_host)
        self.actions_list_layout.setContentsMargins(0, 0, 4, 8)
        self.actions_list_layout.setSpacing(6)
        self.actions_list_layout.addStretch(1)
        self.actions_scroll.setWidget(self.actions_list_host)
        act_lay.addWidget(self.actions_scroll, 1)
        self.stack.addWidget(act_page)

        # Page 2: Decisions & Conflicts across meetings
        dec_page = QWidget()
        dec_lay = QVBoxLayout(dec_page)
        dec_lay.setContentsMargins(0, 2, 0, 0)
        dec_lay.setSpacing(8)
        dec_flt = QHBoxLayout()
        dec_flt.setSpacing(6)
        self._decision_filter_btns: Dict[str, QPushButton] = {}
        for dk, dlbl in (
            ("all", "All"),
            ("confirmed", "Confirmed"),
            ("proposed", "Proposed"),
            ("unresolved", "Unresolved"),
        ):
            btn = QPushButton(dlbl)
            btn.setCheckable(True)
            btn.setChecked(dk == "all")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=dk: self._set_decision_filter(k))
            dec_flt.addWidget(btn)
            self._decision_filter_btns[dk] = btn
        dec_flt.addStretch(1)
        self.lbl_decisions_count = QLabel("0 decisions")
        dec_flt.addWidget(self.lbl_decisions_count)
        dec_lay.addLayout(dec_flt)

        self.decisions_browser = QTextBrowser()
        self.decisions_browser.setObjectName("overallRichBrowser")
        self.decisions_browser.setOpenExternalLinks(False)
        self.decisions_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.decisions_browser.anchorClicked.connect(self._on_anchor_clicked)
        dec_lay.addWidget(self.decisions_browser, 1)
        self.stack.addWidget(dec_page)

        # Page 3: Global Knowledge Library
        self.global_knowledge_tab = VoxKnowledgeBaseTab(
            parent=self,
            theme=self.theme,
            db=self.db,
            strict_meeting_scope=False,
        )
        self.global_knowledge_tab.open_meeting_requested.connect(self.open_meeting_requested.emit)
        self.global_knowledge_tab.rebuild_requested.connect(lambda: self.rebuild_knowledge_requested.emit(False))
        self.global_knowledge_tab.rebuild_with_extract_requested.connect(self.rebuild_knowledge_requested.emit)
        self.stack.addWidget(self.global_knowledge_tab)

        # Page 4: Inline Compare & Pre-Meeting Briefings
        cmp_page = QWidget()
        cmp_lay = QVBoxLayout(cmp_page)
        cmp_lay.setContentsMargins(0, 2, 0, 0)
        cmp_lay.setSpacing(8)

        cmp_bar = QFrame()
        cmp_bar.setObjectName("overallSectionCard")
        cmp_bar.setMinimumWidth(0)
        self.compare_controls_card = cmp_bar
        cb_lay = QVBoxLayout(cmp_bar)
        cb_lay.setContentsMargins(12, 10, 12, 10)
        cb_lay.setSpacing(8)

        row_cmp = QHBoxLayout()
        row_cmp.setSpacing(6)
        self.lbl_cmp_hdr = QLabel("Compare Two Meetings:")
        row_cmp.addWidget(self.lbl_cmp_hdr)
        self.combo_cmp_a = QComboBox()
        self.combo_cmp_a.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_cmp_a.setMinimumContentsLength(12)
        self.combo_cmp_b = QComboBox()
        self.combo_cmp_b.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_cmp_b.setMinimumContentsLength(12)
        row_cmp.addWidget(self.combo_cmp_a, 1)
        row_cmp.addWidget(QLabel("vs"))
        row_cmp.addWidget(self.combo_cmp_b, 1)
        self.btn_run_compare = QPushButton("Compare Selected")
        self.btn_run_compare.setObjectName("overallPrimaryBtn")
        self.btn_run_compare.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_compare.clicked.connect(self._run_compare)
        row_cmp.addWidget(self.btn_run_compare)
        cb_lay.addLayout(row_cmp)

        row_brief = QHBoxLayout()
        row_brief.setSpacing(6)
        self.lbl_brief_hdr = QLabel("Pre-Meeting Briefing Topic:")
        row_brief.addWidget(self.lbl_brief_hdr)
        self.txt_briefing_topic = VoxSearchField("Enter project, client, or topic name (e.g. Strata Eyewear)...")
        self.txt_briefing_topic.returnPressed.connect(self._run_briefing)
        row_brief.addWidget(self.txt_briefing_topic, 1)
        self.btn_run_briefing = QPushButton("Generate Briefing")
        self.btn_run_briefing.setObjectName("overallSecondaryBtn")
        self.btn_run_briefing.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_briefing.clicked.connect(self._run_briefing)
        row_brief.addWidget(self.btn_run_briefing)
        cb_lay.addLayout(row_brief)

        cmp_lay.addWidget(cmp_bar)

        self.compare_browser = QTextBrowser()
        self.compare_browser.setObjectName("overallRichBrowser")
        self.compare_browser.setOpenExternalLinks(False)
        self.compare_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.compare_browser.anchorClicked.connect(self._on_anchor_clicked)
        cmp_lay.addWidget(self.compare_browser, 1)
        self.stack.addWidget(cmp_page)

        root.addWidget(self.stack, 1)

    def _on_subnav_changed(self, key: str) -> None:
        idx_map = {k: i for i, (k, _) in enumerate(self.SUBVIEWS)}
        idx = idx_map.get(key, 0)
        self.stack.setCurrentIndex(idx)
        self._render_subview_if_dirty(idx)

    def _render_subview_if_dirty(self, idx: int) -> None:
        if idx not in self._dirty_subviews and idx != 3:
            return
        self._dirty_subviews.discard(idx)
        if idx == 0:
            self._render_overview(self._snap_cache, query=self._last_query)
        elif idx == 1:
            self._render_actions()
        elif idx == 2:
            self._render_decisions()
        elif idx == 3 and hasattr(self, "global_knowledge_tab"):
            self.global_knowledge_tab.refresh_items()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if getattr(self, "_needs_refresh", False):
            self._needs_refresh = False
            self.refresh_data()

    def set_subview(self, key: str) -> None:
        if hasattr(self, "subnav"):
            self.subnav.set_current(key, emit=False)
        self._on_subnav_changed(key)

    def set_db(self, db: Any, engine: Optional[MeetingIntelligenceEngine] = None) -> None:
        self.db = db
        if engine is not None:
            self.engine = engine
        elif db is not None and self.engine is None:
            self.engine = MeetingIntelligenceEngine(db)
        if hasattr(self, "global_knowledge_tab"):
            self.global_knowledge_tab.db = db
        self.refresh_folders()
        self.refresh_data()

    def refresh_folders(self) -> None:
        if not hasattr(self, "combo_folder") or self.db is None or not hasattr(self.db, "list_folders"):
            return
        cur = self.combo_folder.currentData()
        self.combo_folder.blockSignals(True)
        self.combo_folder.clear()
        self.combo_folder.addItem("All Folders", None)
        try:
            folders = self.db.list_folders()
            if isinstance(folders, (list, tuple)):
                for f in folders:
                    if isinstance(f, dict) and "id" in f and "name" in f:
                        self.combo_folder.addItem(f"Folder: {f['name']}", f["id"])
        except Exception:
            pass
        for i in range(self.combo_folder.count()):
            if self.combo_folder.itemData(i) == cur:
                self.combo_folder.setCurrentIndex(i)
                break
        self.combo_folder.blockSignals(False)

    def reset_filters(self) -> None:
        self.combo_date.blockSignals(True)
        self.combo_date.setCurrentIndex(0)
        self.combo_date.blockSignals(False)
        self.combo_folder.blockSignals(True)
        self.combo_folder.setCurrentIndex(0)
        self.combo_folder.blockSignals(False)
        self.combo_platform.blockSignals(True)
        self.combo_platform.setCurrentIndex(0)
        self.combo_platform.blockSignals(False)
        self.search_input.blockSignals(True)
        self.search_input.setText("")
        self.search_input.blockSignals(False)
        self.refresh_data()

    def get_active_scope(self) -> Tuple[Optional[List[int]], str]:
        return self._filtered_ids, self._scope_label

    def refresh_data(self) -> None:
        if self.db is None or not hasattr(self.db, "list_meetings"):
            return
        db_path = getattr(self.db, "db_path", None)
        if db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists():
            return

        date_val = str(self.combo_date.currentData() or "Anytime")
        date_lbl = self.combo_date.currentText()
        folder_id = self.combo_folder.currentData()
        folder_lbl = self.combo_folder.currentText()
        plat_val = str(self.combo_platform.currentData() or "All Sources")
        plat_lbl = self.combo_platform.currentText()
        query = self.search_input.text().strip()

        raw_meetings = self.db.list_meetings(
            search_query=query,
            folder_id=folder_id,
            source_platform=plat_val,
            timeframe=date_val,
            limit=400,
        )
        meetings = [m for m in raw_meetings if isinstance(m, dict)] if isinstance(raw_meetings, (list, tuple)) else []
        self._filtered_meetings = list(meetings)

        is_filtered = bool(
            date_val != "Anytime" or folder_id is not None or plat_val != "All Sources" or query
        )
        self._filtered_ids = [int(m["id"]) for m in meetings] if is_filtered else None

        done_cnt = sum(1 for m in meetings if str(m.get("status") or "").lower() == "completed")
        parts = [f"{len(meetings)} meeting{'s' if len(meetings) != 1 else ''} ({done_cnt} completed)"]
        parts.append(date_lbl)
        if folder_id is not None:
            parts.append(folder_lbl)
        if plat_val != "All Sources":
            parts.append(plat_lbl)
        if query:
            parts.append(f'matching "{query[:28]}"')
        self._scope_label = " · ".join(parts)
        self.lbl_scope_pill.setText(f"Scope: {self._scope_label}")
        self.scope_changed.emit(self._filtered_ids, self._scope_label)

        raw_snap = (
            self.db.get_library_intelligence_snapshot(meeting_ids=self._filtered_ids)
            if hasattr(self.db, "get_library_intelligence_snapshot")
            else {}
        )
        snap = raw_snap if isinstance(raw_snap, dict) else {}
        self._snap_cache = snap
        self._last_query = query
        self._needs_refresh = False
        self._actions_render_limit = 40
        self._update_kpis(snap)
        self._populate_compare_combos(meetings)
        self._dirty_subviews = {0, 1, 2, 3}
        cur_idx = self.stack.currentIndex() if hasattr(self, "stack") else 0
        self._dirty_subviews.discard(cur_idx)
        if cur_idx == 0:
            self._render_overview(snap, query=query)
        elif cur_idx == 1:
            self._render_actions()
        elif cur_idx == 2:
            self._render_decisions()
        elif cur_idx == 3 and hasattr(self, "global_knowledge_tab"):
            self.global_knowledge_tab.refresh_items()

    def _update_kpis(self, snap: Dict[str, Any]) -> None:
        dur_hrs = float(snap.get("total_duration_sec") or 0.0) / 3600.0
        vals = {
            "meetings": str(int(snap.get("total_meetings") or 0)),
            "duration": f"{dur_hrs:.1f}h",
            "decisions": str(int(snap.get("decisions_count") or 0)),
            "actions": str(int(snap.get("open_actions") or 0)),
            "knowledge": str(int(snap.get("knowledge_count") or 0)),
        }
        for key, txt in vals.items():
            if key in self._kpi_cards:
                self._kpi_cards[key][1].setText(txt)

    def _render_overview(self, snap: Dict[str, Any], query: str = "") -> None:
        t = get_theme_tokens(self.theme)
        accent = t["accent_fathom"]
        border = t["border"]
        surf = t["surface"]
        surf_alt = t["surface_alt"]
        fg = t["text_primary"]
        sec = t["text_secondary"]
        chip_bg = t["chip_bg"]
        chip_bdr = t["chip_border"]

        blocks: List[str] = [
            f'<div style="font-family:{get_html_font_family()};color:{fg};font-size:13px;line-height:1.58;">'
        ]

        # Instant Cross-Meeting Search Hits when user searched
        if query and hasattr(self.db, "search_meeting_memory"):
            hits = self.db.search_meeting_memory(query, limit=18, meeting_ids=self._filtered_ids)
            blocks.append(
                f'<div style="background:{surf};border:1px solid {border};border-radius:10px;padding:12px 14px;margin-bottom:10px;">'
                f'<div style="font-size:13.5px;font-weight:700;margin-bottom:8px;">Search Matches Across Filtered Library ({len(hits)})</div>'
            )
            if hits:
                for h in hits[:12]:
                    mid = int(h.get("meeting_id") or 0)
                    st = max(0.0, float(h.get("start_time") or 0.0))
                    mtitle = html.escape(str(h.get("meeting_title") or f"Meeting #{mid}"))
                    spk = html.escape(str(h.get("speaker") or "Meeting"))
                    ts_lbl = format_seconds_hms(st)
                    snip = html.escape(str(h.get("text") or "").replace("\n", " ")[:220])
                    blocks.append(
                        f'<div style="padding:7px 9px;margin-bottom:6px;background:{surf_alt};border:1px solid {border};border-radius:8px;">'
                        f'<a href="meeting:{mid}:{st:.1f}" style="color:{accent};font-weight:700;text-decoration:none;">'
                        f'{mtitle} [{ts_lbl}]</a> &nbsp;·&nbsp; '
                        f'<span style="color:{sec};font-weight:600;">{spk}</span><br>'
                        f'<span style="color:{fg};font-size:12.5px;">{snip}</span></div>'
                    )
            else:
                blocks.append(f'<div style="color:{sec};font-style:italic;">No excerpts matched "{html.escape(query)}" in the active filter scope.</div>')
            blocks.append("</div>")

        # Clean recurring themes (skip boilerplate placeholder titles)
        raw_topics = snap.get("recurring_topics") or []
        topics = [
            tp for tp in raw_topics
            if str(tp.get("topic") or "").strip().lower() not in ("empty meeting", "untitled", "untitled meeting")
        ]
        speakers = snap.get("top_speakers") or []
        platforms = snap.get("platforms") or []

        blocks.append(
            f'<div style="background:{surf};border:1px solid {border};border-radius:10px;padding:12px 14px;margin-bottom:10px;">'
            f'<div style="font-size:13.5px;font-weight:700;margin-bottom:8px;">Recurring Topics, Speakers &amp; Channels</div>'
        )
        if topics:
            t_pills = " &nbsp;".join(
                f'<span style="background:{chip_bg};color:{fg};border:1px solid {chip_bdr};border-radius:6px;padding:2px 8px;font-size:11.5px;">'
                f'{html.escape(str(tp["topic"])[:44])} <strong>({tp["count"]})</strong></span>'
                for tp in topics[:10]
            )
            blocks.append(f'<div style="margin-bottom:8px;"><strong style="font-size:12px;color:{sec};">Recurring Themes:</strong><div style="margin-top:4px;line-height:2.0;">{t_pills}</div></div>')
        if speakers:
            s_pills = " &nbsp;".join(
                f'<span style="background:{surf_alt};color:{fg};border:1px solid {border};border-radius:6px;padding:2px 8px;font-size:11.5px;">'
                f'{html.escape(str(sp["speaker"]))} · {sp["meeting_count"]} mtg{"s" if sp["meeting_count"] != 1 else ""} ({sp["segment_count"]} segs)</span>'
                for sp in speakers[:8]
            )
            blocks.append(f'<div style="margin-bottom:8px;"><strong style="font-size:12px;color:{sec};">Top Participants Across Scope:</strong><div style="margin-top:4px;line-height:2.0;">{s_pills}</div></div>')
        if platforms:
            p_txt = " &nbsp;·&nbsp; ".join(f"{html.escape(str(p['platform']))}: <strong>{p['count']}</strong>" for p in platforms)
            blocks.append(f'<div style="font-size:12px;color:{sec};"><strong>Platforms:</strong> {p_txt}</div>')
        blocks.append("</div>")

        # Recent Meetings Directory Cards (click any to open Individual Meeting Context)
        blocks.append(
            f'<div style="background:{surf};border:1px solid {border};border-radius:10px;padding:12px 14px;">'
            f'<div style="font-size:13.5px;font-weight:700;margin-bottom:8px;">Meetings Included in Analysis ({len(self._filtered_meetings)})</div>'
        )
        if not self._filtered_meetings:
            blocks.append(f'<div style="color:{sec};font-style:italic;">No meetings match the current filters.</div>')
        else:
            for m in self._filtered_meetings[:25]:
                mid = int(m["id"])
                title = html.escape(str(m.get("title") or "Untitled Meeting"))
                dt = html.escape(format_meeting_local_datetime(m.get("created_at") or m.get("started_at"), str(m.get("timezone") or "")))
                dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
                plat = html.escape(str(m.get("source_platform") or "Call"))
                raw_sum = str(m.get("brief_summary") or m.get("summary") or "").strip()
                clean_lines = [
                    ln.strip()
                    for ln in raw_sum.splitlines()
                    if ln.strip()
                    and not ln.strip().startswith("📋")
                    and not set(ln.strip()).issubset({"=", "-", "_"})
                    and not ln.strip().upper().startswith("1. MEETING DETAILS")
                ]
                raw_sum = " ".join(clean_lines).strip()
                if len(raw_sum) > 190:
                    raw_sum = raw_sum[:187] + "..."
                sum_line = f'<div style="color:{sec};font-size:12px;margin-top:3px;">{html.escape(raw_sum)}</div>' if raw_sum else ""
                blocks.append(
                    f'<div style="padding:8px 10px;margin-bottom:6px;background:{surf_alt};border:1px solid {border};border-radius:8px;">'
                    f'<a href="meeting:{mid}:0.0" style="color:{accent};font-weight:700;font-size:13px;text-decoration:none;">{title}</a>'
                    f' &nbsp;<span style="color:{sec};font-size:11.5px;">{dt} · {dur} · {plat}</span>'
                    f'{sum_line}</div>'
                )
        blocks.append("</div></div>")
        self.overview_browser.setHtml("".join(blocks))

    def _set_action_filter(self, key: str) -> None:
        self._actions_filter = key
        for k, b in self._action_filter_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self._render_actions()

    def _render_actions(self) -> None:
        while self.actions_list_layout.count() > 1:
            item = self.actions_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        if self.db is None or not hasattr(self.db, "list_action_items"):
            return

        status_arg = self._actions_filter if self._actions_filter in ("open", "completed", "blocked") else None
        try:
            raw_rows = self.db.list_action_items(status=status_arg, limit=250, meeting_ids=self._filtered_ids)
        except TypeError:
            raw_rows = self.db.list_action_items(status=status_arg, limit=250)
            if self._filtered_ids is not None and isinstance(raw_rows, (list, tuple)):
                allowed = set(self._filtered_ids)
                raw_rows = [r for r in raw_rows if isinstance(r, dict) and int(r.get("meeting_id") or 0) in allowed]
        rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, (list, tuple)) else []

        if self._actions_filter == "unassigned":
            rows = [r for r in rows if not str(r.get("owner") or "").strip()]

        self.lbl_actions_count.setText(f"{len(rows)} action item{'s' if len(rows) != 1 else ''}")
        t = get_theme_tokens(self.theme)

        if not rows:
            empty = QLabel("No action items match the selected filter and meeting scope.")
            empty.setStyleSheet(f"color: {t['text_muted']}; font-style: italic; padding: 14px;")
            self.actions_list_layout.insertWidget(0, empty)
            return

        render_cap = max(40, int(getattr(self, "_actions_render_limit", 40) or 40))
        visible_rows = rows[:render_cap]
        for idx, a in enumerate(visible_rows):
            card = QFrame()
            card.setObjectName("overallActionRow")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            card.setStyleSheet(
                f"QFrame#overallActionRow {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; }}"
            )
            rl = QHBoxLayout(card)
            rl.setContentsMargins(10, 7, 10, 7)
            rl.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(str(a.get("status") or "").lower() == "completed")
            aid = int(a["id"])
            chk.toggled.connect(lambda _c, item_id=aid: self._toggle_action(item_id))
            rl.addWidget(chk)

            txt_col = QVBoxLayout()
            txt_col.setSpacing(2)
            clean_desc = " ".join(str(a.get("description") or "").split())
            est_lines = max(1, (len(clean_desc) + 84) // 85)
            card.setMinimumHeight(52 + (est_lines - 1) * 18)
            desc = QLabel(clean_desc)
            desc.setWordWrap(True)
            desc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            desc.setMinimumWidth(0)
            desc.setStyleSheet(f"font-size: 12.5px; font-weight: 600; color: {t['text_primary']};")
            txt_col.addWidget(desc)

            owner = str(a.get("owner") or "").strip() or "—"
            due = str(a.get("due_date") or "").strip() or "—"
            pri = str(a.get("priority") or "medium").title()
            st = str(a.get("status") or "pending").title()
            meta = QLabel(f"Owner: {owner} · Due: {due} · Priority: {pri} · Status: {st}")
            meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            meta.setMinimumWidth(0)
            meta.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
            txt_col.addWidget(meta)
            rl.addLayout(txt_col, 1)

            mid = int(a.get("meeting_id") or 0)
            ts = float(a.get("timestamp_sec") or 0.0)
            m_title = str(a.get("meeting_title") or f"Meeting #{mid}")
            btn_src = QPushButton(f"{m_title[:24]} · {format_seconds_hms(ts)} →")
            btn_src.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_src.setStyleSheet(
                f"QPushButton {{ background: {t['chip_bg']}; color: {t['accent_fathom']}; border: 1px solid {t['chip_border']}; "
                f"border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 700; }}"
            )
            btn_src.clicked.connect(lambda _=False, m_id=mid, sec=ts: self.open_meeting_requested.emit(m_id, sec))
            rl.addWidget(btn_src)

            self.actions_list_layout.insertWidget(idx, card)

        if len(rows) > len(visible_rows):
            rem = len(rows) - len(visible_rows)
            btn_more = QPushButton(f"Show more actions ({rem} remaining)")
            btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_more.setStyleSheet(
                f"QPushButton {{ background: {t['secondary_btn_bg']}; color: {t['text_primary']}; border: 1px solid {t['border']}; "
                f"border-radius: 7px; padding: 6px 12px; font-size: 12px; font-weight: 600; }}"
            )

            def _load_more_actions() -> None:
                self._actions_render_limit += 60
                self._render_actions()

            btn_more.clicked.connect(_load_more_actions)
            self.actions_list_layout.insertWidget(len(visible_rows), btn_more)

    def _toggle_action(self, item_id: int) -> None:
        if self.db and hasattr(self.db, "toggle_action_item_status"):
            self.db.toggle_action_item_status(item_id)
            self.refresh_data()

    def _set_decision_filter(self, key: str) -> None:
        self._decisions_filter = key
        for k, b in self._decision_filter_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self._render_decisions()

    def _render_decisions(self) -> None:
        if self.db is None or not hasattr(self.db, "list_decisions"):
            return
        st_arg = self._decisions_filter if self._decisions_filter != "all" else None
        try:
            raw_rows = self.db.list_decisions(status=st_arg, limit=250, meeting_ids=self._filtered_ids)
        except TypeError:
            raw_rows = self.db.list_decisions(status=st_arg, limit=250)
            if self._filtered_ids is not None and isinstance(raw_rows, (list, tuple)):
                allowed = set(self._filtered_ids)
                raw_rows = [r for r in raw_rows if isinstance(r, dict) and int(r.get("meeting_id") or 0) in allowed]
        rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, (list, tuple)) else []

        self.lbl_decisions_count.setText(f"{len(rows)} decision{'s' if len(rows) != 1 else ''}")
        t = get_theme_tokens(self.theme)
        blocks = [f'<div style="font-family:{get_html_font_family()};color:{t["text_primary"]};font-size:13px;line-height:1.58;">']
        if not rows:
            blocks.append(f'<div style="color:{t["text_muted"]};font-style:italic;padding:12px;">No decisions match the active filter and meeting scope.</div>')
        else:
            for d in rows[:150]:
                mid = int(d.get("meeting_id") or 0)
                ts = float(d.get("timestamp_sec") or 0.0)
                mtitle = html.escape(str(d.get("meeting_title") or f"Meeting #{mid}"))
                dec = html.escape(str(d.get("decision") or "").strip())
                st = html.escape(str(d.get("status") or "confirmed").title())
                cat = html.escape(str(d.get("category") or "General"))
                spk = html.escape(str(d.get("speaker") or "Speaker"))
                rat = html.escape(str(d.get("rationale") or "").strip())
                ev = html.escape(str(d.get("evidence_quote") or "").strip())
                evo = str(d.get("evolution_relationship") or "new").lower()
                conflict_badge = (
                    f' &nbsp;<span style="background:{t["status_warning_bg"]};color:{t["status_warning_text"]};padding:1px 6px;border-radius:4px;font-size:10.5px;font-weight:700;">{html.escape(evo.upper())}</span>'
                    if evo in ("superseded", "reversed", "modified") else ""
                )
                rat_line = f'<div style="color:{t["text_secondary"]};font-size:12px;margin-top:3px;"><strong>Rationale:</strong> {rat}</div>' if rat else ""
                ev_line = f'<div style="color:{t["text_muted"]};font-size:11.5px;font-style:italic;margin-top:2px;">"{ev}"</div>' if ev else ""
                blocks.append(
                    f'<div style="background:{t["surface"]};border:1px solid {t["border"]};border-radius:8px;padding:10px 12px;margin-bottom:8px;">'
                    f'<div style="font-weight:700;font-size:13px;">{dec}</div>'
                    f'<div style="margin-top:4px;font-size:11.5px;">'
                    f'<span style="background:{t["chip_bg"]};color:{t["chip_text"]};padding:1px 6px;border-radius:4px;font-weight:600;">{st} · {cat}</span>'
                    f'{conflict_badge} &nbsp;·&nbsp; By <strong>{spk}</strong> &nbsp;·&nbsp; '
                    f'<a href="meeting:{mid}:{ts:.1f}" style="color:{t["accent_fathom"]};font-weight:700;text-decoration:none;">{mtitle} [{format_seconds_hms(ts)}] →</a>'
                    f'</div>{rat_line}{ev_line}</div>'
                )
        blocks.append("</div>")
        self.decisions_browser.setHtml("".join(blocks))

    def _populate_compare_combos(self, meetings: List[Dict[str, Any]]) -> None:
        if not hasattr(self, "combo_cmp_a"):
            return
        cur_a = self.combo_cmp_a.currentData()
        cur_b = self.combo_cmp_b.currentData()
        self.combo_cmp_a.blockSignals(True)
        self.combo_cmp_b.blockSignals(True)
        self.combo_cmp_a.clear()
        self.combo_cmp_b.clear()
        for m in meetings[:80]:
            dt_str = str(m.get("started_at") or m.get("created_at") or "")[:10]
            lbl = f"{m.get('title') or 'Untitled'}" + (f" ({dt_str})" if dt_str else "")
            self.combo_cmp_a.addItem(lbl, int(m["id"]))
            self.combo_cmp_b.addItem(lbl, int(m["id"]))
        for idx, combo, val in ((0, self.combo_cmp_a, cur_a), (1, self.combo_cmp_b, cur_b)):
            i = combo.findData(val)
            if i >= 0:
                combo.setCurrentIndex(i)
            elif combo.count() > idx:
                combo.setCurrentIndex(idx)
        self.combo_cmp_a.blockSignals(False)
        self.combo_cmp_b.blockSignals(False)

    def _run_compare(self) -> None:
        if self.engine is None or not hasattr(self.engine, "compare_meetings"):
            return
        id_a = self.combo_cmp_a.currentData()
        id_b = self.combo_cmp_b.currentData()
        if id_a is None or id_b is None or id_a == id_b:
            self.compare_browser.setHtml(
                f'<div style="color:{get_theme_tokens(self.theme)["text_secondary"]};padding:12px;">Select two distinct meetings above to compare topics, decision evolution, and carry-over commitments.</div>'
            )
            return
        try:
            self._has_compared = True
            res = self.engine.compare_meetings(int(id_a), int(id_b))
            md = str(res.get("comparison_markdown") or res.get("markdown") or res.get("summary") or "Comparison complete.")
            t = get_theme_tokens(self.theme)
            hdr = (
                f'<div style="margin-bottom:8px;">'
                f'<a href="meeting:{int(id_a)}:0.0" style="color:{t["accent_fathom"]};font-weight:700;text-decoration:none;">Open Earlier Meeting →</a>'
                f' &nbsp;&nbsp;|&nbsp;&nbsp; '
                f'<a href="meeting:{int(id_b)}:0.0" style="color:{t["accent_fathom"]};font-weight:700;text-decoration:none;">Open Later Meeting →</a>'
                f'</div>'
            )
            self.compare_browser.setHtml(hdr + render_safe_markdown_html(md, theme=self.theme))
        except Exception as exc:
            self.compare_browser.setPlainText(f"Could not compare meetings: {exc}")

    def _run_briefing(self) -> None:
        topic = self.txt_briefing_topic.text().strip()
        if not topic or self.engine is None:
            return
        if hasattr(self.engine, "generate_pre_meeting_briefing"):
            try:
                res = self.engine.generate_pre_meeting_briefing(topic)
                md = str(res.get("briefing_markdown") or "No historical context found for this topic.")
                self.compare_browser.setHtml(render_safe_markdown_html(md, theme=self.theme))
            except Exception as exc:
                self.compare_browser.setPlainText(f"Could not generate briefing: {exc}")

    def _on_anchor_clicked(self, url: QUrl) -> None:
        raw = url.toString().strip()
        if raw.startswith("meeting:"):
            parts = raw.split(":")
            if len(parts) >= 2:
                try:
                    mid = int(parts[1])
                    sec = float(parts[2]) if len(parts) >= 3 else 0.0
                    self.open_meeting_requested.emit(mid, sec)
                except Exception:
                    pass

    def apply_theme(self, theme_name: str) -> None:
        self.theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self.theme)
        self.header_card.setStyleSheet(
            f"QFrame#overallHeaderCard {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}"
        )
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {t['text_primary']};")
        self.lbl_subtitle.setStyleSheet(f"font-size: 11.5px; color: {t['text_secondary']};")
        self.lbl_scope_pill.setStyleSheet(
            f"QLabel#overallScopePill {{ background: {t['accent_fathom_tint']}; color: {t['accent_fathom']}; "
            f"border: 1px solid {t['chip_border']}; border-radius: 8px; padding: 3px 10px; font-size: 11.5px; font-weight: 700; }}"
        )
        combo_css = (
            f"QComboBox {{ background: {t['input_bg']}; color: {t['text_primary']}; border: 1px solid {t['input_border']}; "
            f"border-radius: 7px; padding: 4px 10px; font-size: 11.5px; font-weight: 600; }} "
            f"QComboBox QAbstractItemView {{ background: {t['surface']}; color: {t['text_primary']}; }}"
        )
        for cb in (self.combo_date, self.combo_folder, self.combo_platform, self.combo_cmp_a, self.combo_cmp_b):
            cb.setStyleSheet(combo_css)
        for sf in (self.search_input, self.txt_briefing_topic):
            sf.setStyleSheet(
                f"QLineEdit#voxSearchField {{ background: {t['input_bg']}; color: {t['text_primary']}; "
                f"border: 1px solid {t['input_border']}; border-radius: 8px; padding: 5px 10px; font-size: 12px; }}"
            )
        sec_btn = (
            f"QPushButton {{ background: {t['secondary_btn_bg']}; color: {t['text_primary']}; "
            f"border: 1px solid {t['border']}; border-radius: 7px; padding: 4px 10px; font-size: 11.5px; font-weight: 600; }} "
            f"QPushButton:hover {{ background: {t['surface_hover']}; }}"
        )
        self.btn_reset_filters.setStyleSheet(sec_btn)
        self.btn_run_briefing.setStyleSheet(sec_btn)
        self.btn_run_compare.setStyleSheet(
            f"QPushButton {{ background: {t['fathom_btn_bg']}; color: {t['fathom_btn_text']}; "
            f"border: 1px solid {t['fathom_btn_border']}; border-radius: 7px; padding: 5px 12px; font-size: 12px; font-weight: 700; }}"
        )
        self.subnav.set_theme(self.theme)

        for card, val_lbl, cap_lbl in self._kpi_cards.values():
            card.setStyleSheet(
                f"QFrame#overallKpiCard {{ background: {t['surface_container_low']}; border: 1px solid {t['outline_variant']}; border-radius: 10px; }}"
            )
            val_lbl.setFont(get_ui_font(19, 600))
            val_lbl.setStyleSheet(f"font-size: 19px; font-weight: 600; color: {t['text_primary']};")
            cap_lbl.setStyleSheet(f"font-size: 11px; font-weight: 500; color: {t['text_secondary']};")

        for sc in (self.prompt_strip_card, self.compare_controls_card):
            sc.setStyleSheet(
                f"QFrame#overallSectionCard {{ background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: 9px; }}"
            )
        self.lbl_ask_strip.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {t['text_secondary']};")
        self.lbl_cmp_hdr.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {t['text_primary']};")
        self.lbl_brief_hdr.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {t['text_primary']};")
        chip_css = (
            f"QPushButton {{ background: {t['chip_bg']}; color: {t['text_secondary']}; border: 1px solid {t['chip_border']}; "
            f"border-radius: 11px; padding: 3px 9px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:checked {{ background: {t['accent_fathom_tint']}; color: {t['accent_fathom']}; border-color: {t['accent_fathom']}; font-weight: 700; }} "
            f"QPushButton:hover {{ background: {t['surface_hover']}; }}"
        )
        for b in [*self._prompt_buttons, *self._action_filter_btns.values(), *self._decision_filter_btns.values()]:
            b.setStyleSheet(chip_css)

        self.lbl_actions_count.setStyleSheet(f"font-size: 11.5px; font-weight: 600; color: {t['text_secondary']};")
        self.lbl_decisions_count.setStyleSheet(f"font-size: 11.5px; font-weight: 600; color: {t['text_secondary']};")
        for br in (self.overview_browser, self.decisions_browser, self.compare_browser):
            br.setStyleSheet(
                f"QTextBrowser#overallRichBrowser {{ background: {t['bg']}; color: {t['text_primary']}; border: none; }}"
            )
        for sa in (self.overview_scroll, self.actions_scroll):
            sa.setStyleSheet(f"QScrollArea {{ background: {t['bg']}; border: none; }}")
        if hasattr(self, "global_knowledge_tab"):
            self.global_knowledge_tab.apply_theme(self.theme)
        if self.isVisible():
            self.refresh_data()
            if getattr(self, "_has_compared", False):
                self._run_compare()
        else:
            self._needs_refresh = True
            self._dirty_subviews = {0, 1, 2, 3}

    set_theme = apply_theme

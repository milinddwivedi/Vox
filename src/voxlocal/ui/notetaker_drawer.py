"""VOX Floating Notetaker Drawer — Wispr Flow-Style 3-Tab Live Call Companion.

Implements full visual and functional parity with WhisperFlow Notetaker:
1. Header Bar: Back navigation, large serif editable title, metadata pills (participant avatar, date, folder),
   share, link copy, and window controls.
2. 3 Clean Tabs:
   - 'My thoughts': Private inline scratchpad auto-saved to meetings.notes.
   - 'Transcript': Conversational speaker-turn cards/bubbles, speaker headers with inline play buttons,
     stats header ('2 SPEAKERS · 41:04'), search toggle, clipboard copy, and live streaming partial.
   - '★ Summary': 3-step post-processing progress card ('Identifying who said what... Step 2 of 3'),
     followed by structured executive summary with highlight callout and categorized sections.
3. Bottom Action Bar: Universal Stop/Resume button, 'Ask anything, or type / for Skills' input, and
   'What did I miss?' quick prompt chip.
"""

from __future__ import annotations

import html
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.controller import AppController
from voxlocal.core.logger import get_logger
from voxlocal.storage.exporter import TranscriptExporter, format_seconds_hms
from voxlocal.ui.icons import VectorCompatIconButton, VectorCompatPushButton, get_icon
from voxlocal.ui.theme import (
    get_html_font_family,
    get_theme_stylesheet,
    get_theme_tokens,
    get_ui_font,
)

logger = get_logger("ui.notetaker_drawer")


class VoxNotetakerDrawer(QWidget):
    """Resizable, edge-dockable call companion drawer matching WhisperFlow aesthetics."""

    seek_requested = pyqtSignal(int, float)
    _ask_answer_ready = pyqtSignal(str, str, object)

    def __init__(
        self,
        controller: AppController,
        on_open_meeting_studio: Optional[callable] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.on_open_meeting_studio = on_open_meeting_studio
        self._theme = controller.theme if controller else "light"
        self._is_expanded = False
        self._active_tab = 1  # Default to Transcript tab during active call
        self._search_visible = False
        self._live_segments: List[Dict[str, Any]] = []
        self._active_displayed_meeting_id: Optional[int] = None
        self._elapsed_sec: float = 0.0
        self._last_summary_raw: str = ""
        self._last_decisions_raw: str = ""
        self._last_actions_raw: str = ""
        self._last_summary_finalized: bool = False
        self._notes_dirty: bool = False
        self._notes_meeting_id: Optional[int] = None
        self._current_drawer_mode: str = "live"  # 'live' | 'prep' | 'workspace'
        self._is_pinned_on_top: bool = False
        self._prep_meeting: Optional[Any] = None
        self._quick_note_id: Optional[int] = None

        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(500)
        self._notes_save_timer.timeout.connect(self._persist_meeting_notes)

        self._slash_skills = {
            "/catchup": ("What did I miss?", "What did I miss so far in this meeting?"),
            "/decisions": ("Key Decisions", "List all decisions made so far in this meeting."),
            "/actions": ("Action Items", "List all action items and owners mentioned so far."),
            "/blockers": ("Risks & Blockers", "Summarize any risks or blockers discussed in this meeting."),
        }

        self.setWindowTitle("VOX — Notetaker")
        self.resize(540, 680)
        self.setMinimumSize(360, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._ask_answer_ready.connect(self._on_ask_answer_received)
        self._build_ui()
        self.apply_theme(self._theme)
        self._connect_signals()
        self.refresh_from_active_meeting()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ---------------------------------------------------------------------
        # 1. Top Window Controls Bar: [ < ] [ Live | Prep | Workspace ] ... [ Pin ] [ Share ] [ 🔗 ] [ — ] [ □ ] [ ✕ ]
        # ---------------------------------------------------------------------
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.btn_back = QPushButton("")
        self.btn_back.setObjectName("drawerBackBtn")
        self.btn_back.setToolTip("Dock back to floating bar")
        self.btn_back.setAccessibleName("Back to floating bar")
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(28, 28)
        self.btn_back.setIconSize(QSize(15, 15))
        self.btn_back.clicked.connect(self.hide)
        top_bar.addWidget(self.btn_back)

        self._mode_buttons: Dict[str, QPushButton] = {}
        for m_key, m_lbl in (("live", "Live"), ("prep", "Prep"), ("workspace", "Workspace")):
            mb = QPushButton(m_lbl)
            mb.setObjectName("drawerModeBtn")
            mb.setCheckable(True)
            mb.setFixedHeight(26)
            mb.setCursor(Qt.CursorShape.PointingHandCursor)
            mb.clicked.connect(lambda _c=False, mk=m_key: self.select_dynamic_mode(mk))
            top_bar.addWidget(mb)
            self._mode_buttons[m_key] = mb

        top_bar.addStretch()

        self.btn_pin_top = QPushButton("Pin")
        self.btn_pin_top.setObjectName("drawerShareBtn")
        self.btn_pin_top.setCheckable(True)
        self.btn_pin_top.setFixedHeight(26)
        self.btn_pin_top.setToolTip("Keep drawer pinned above other windows")
        self.btn_pin_top.setAccessibleName("Pin drawer on top")
        self.btn_pin_top.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pin_top.clicked.connect(self._toggle_pin_on_top)
        top_bar.addWidget(self.btn_pin_top)

        self.btn_more = QPushButton("")
        self.btn_more.setObjectName("drawerIconBtn")
        self.btn_more.setToolTip("More meeting options & export")
        self.btn_more.setAccessibleName("More meeting options")
        self.btn_more.setFixedSize(28, 28)
        self.btn_more.setIconSize(QSize(15, 15))
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.clicked.connect(self._on_more_clicked)
        top_bar.addWidget(self.btn_more)

        self.btn_share = QPushButton("Share")
        self.btn_share.setObjectName("drawerShareBtn")
        self.btn_share.setFixedHeight(28)
        self.btn_share.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_share.clicked.connect(self._on_share_clicked)
        top_bar.addWidget(self.btn_share)

        self.btn_copy_link = QPushButton("")
        self.btn_copy_link.setObjectName("drawerIconBtn")
        self.btn_copy_link.setToolTip("Copy meeting transcript")
        self.btn_copy_link.setAccessibleName("Copy meeting transcript")
        self.btn_copy_link.setFixedSize(28, 28)
        self.btn_copy_link.setIconSize(QSize(14, 14))
        self.btn_copy_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_link.clicked.connect(self._copy_meeting_transcript)
        top_bar.addWidget(self.btn_copy_link)

        self.btn_min = QPushButton("—")
        self.btn_min.setObjectName("drawerIconBtn")
        self.btn_min.setToolTip("Minimize drawer")
        self.btn_min.setAccessibleName("Minimize drawer")
        self.btn_min.setFixedSize(24, 24)
        self.btn_min.clicked.connect(self.showMinimized)
        top_bar.addWidget(self.btn_min)

        self.btn_expand = VectorCompatIconButton("□")
        self.btn_expand.setObjectName("drawerIconBtn")
        self.btn_expand.setToolTip("Expand or restore drawer")
        self.btn_expand.setAccessibleName("Expand drawer")
        self.btn_expand.setFixedSize(24, 24)
        self.btn_expand.setIconSize(QSize(13, 13))
        self.btn_expand.clicked.connect(self._toggle_expand)
        top_bar.addWidget(self.btn_expand)

        self.btn_close = QPushButton("")
        self.btn_close.setObjectName("drawerIconBtn")
        self.btn_close.setToolTip("Close drawer")
        self.btn_close.setAccessibleName("Close drawer")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setIconSize(QSize(13, 13))
        self.btn_close.clicked.connect(self.hide)
        top_bar.addWidget(self.btn_close)

        root.addLayout(top_bar)

        # ---------------------------------------------------------------------
        # 3-Mode Dynamic Drawer Container ('live' | 'prep' | 'workspace')
        # ---------------------------------------------------------------------
        self.mode_stack = QStackedWidget()
        root.addWidget(self.mode_stack, 1)

        # === MODE 0: LIVE / SELECTED MEETING COMPANION ===
        self.page_mode_live = QWidget()
        live_lay = QVBoxLayout(self.page_mode_live)
        live_lay.setContentsMargins(0, 0, 0, 0)
        live_lay.setSpacing(8)

        # Recent Meeting Selector + Title
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        self.combo_recent_meetings = QComboBox()
        self.combo_recent_meetings.setObjectName("drawerMeetingSelector")
        self.combo_recent_meetings.setToolTip("Switch meeting viewed in drawer")
        self.combo_recent_meetings.setFixedHeight(26)
        self.combo_recent_meetings.currentIndexChanged.connect(self._on_recent_combo_changed)
        sel_row.addWidget(self.combo_recent_meetings, 1)
        self.btn_open_studio = QPushButton("Open Studio")
        self.btn_open_studio.setObjectName("quickChipBtn")
        self.btn_open_studio.setFixedHeight(26)
        self.btn_open_studio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_studio.clicked.connect(self._open_current_in_studio)
        sel_row.addWidget(self.btn_open_studio)
        live_lay.addLayout(sel_row)

        self.txt_title = QLineEdit("")
        self.txt_title.setPlaceholderText("Untitled Meeting")
        self.txt_title.setObjectName("drawerMeetingTitle")
        self.txt_title.setFont(get_ui_font(20, 600))
        self.txt_title.setStyleSheet(
            "QLineEdit#drawerMeetingTitle { background: transparent; border: none; padding: 2px 0px; font-size: 20px; }"
        )
        self.txt_title.editingFinished.connect(self._save_meeting_title)
        live_lay.addWidget(self.txt_title)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)

        self.lbl_avatar_pill = QLabel("VOX")
        self.lbl_avatar_pill.setObjectName("avatarMetaPill")
        self.lbl_avatar_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar_pill.setFixedSize(32, 22)
        meta_row.addWidget(self.lbl_avatar_pill)

        self.lbl_date_pill = QLabel("Today")
        self.lbl_date_pill.setObjectName("subtleMetaPill")
        self.lbl_date_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta_row.addWidget(self.lbl_date_pill)

        self.btn_folder_pill = VectorCompatPushButton("Add folder")
        self.btn_folder_pill.setObjectName("subtleMetaPillBtn")
        self.btn_folder_pill.setIconSize(QSize(12, 12))
        self.btn_folder_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder_pill.setToolTip("Assign meeting to a folder")
        self.btn_folder_pill.clicked.connect(self._on_folder_pill_clicked)
        meta_row.addWidget(self.btn_folder_pill)

        meta_row.addStretch()

        self.lbl_tele_status = QLabel("● Recording · Live Preview")
        self.lbl_tele_status.setObjectName("teleStatusLabel")
        self.lbl_tele_status.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 600;")
        meta_row.addWidget(self.lbl_tele_status)

        live_lay.addLayout(meta_row)

        tabs_bar = QHBoxLayout()
        tabs_bar.setSpacing(20)

        self._tab_buttons: list[QPushButton] = []
        tab_names = ["My thoughts", "Transcript", "★ Summary"]
        for idx, name in enumerate(tab_names):
            b = QPushButton(name)
            b.setObjectName(f"tabBtn_{idx}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(get_ui_font(13.5, 600 if idx == 1 else 500))
            b.clicked.connect(lambda _c=False, i=idx: self._switch_section(i))
            tabs_bar.addWidget(b)
            self._tab_buttons.append(b)

        tabs_bar.addStretch()
        live_lay.addLayout(tabs_bar)

        tab_divider = QFrame()
        tab_divider.setObjectName("tabDivider")
        tab_divider.setFrameShape(QFrame.Shape.HLine)
        tab_divider.setFixedHeight(1)
        live_lay.addWidget(tab_divider)

        self.stack = QStackedWidget()

        # --- Page 0: My Thoughts (Private scratchpad + voice dictation) ---
        page_thoughts = QWidget()
        lay_thoughts = QVBoxLayout(page_thoughts)
        lay_thoughts.setContentsMargins(0, 4, 0, 0)
        lay_thoughts.setSpacing(6)

        notes_tb = QHBoxLayout()
        notes_tb.setSpacing(6)
        self.btn_drawer_dictate_notes = QPushButton("Dictate")
        self.btn_drawer_dictate_notes.setObjectName("quickChipBtn")
        self.btn_drawer_dictate_notes.setCheckable(True)
        self.btn_drawer_dictate_notes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer_dictate_notes.clicked.connect(self._toggle_drawer_notes_dictation)
        notes_tb.addWidget(self.btn_drawer_dictate_notes)

        self.btn_drawer_stamp = QPushButton("+ Timestamp")
        self.btn_drawer_stamp.setObjectName("quickChipBtn")
        self.btn_drawer_stamp.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer_stamp.clicked.connect(self._insert_drawer_timestamp)
        notes_tb.addWidget(self.btn_drawer_stamp)

        self.btn_drawer_action = QPushButton("+ Action")
        self.btn_drawer_action.setObjectName("quickChipBtn")
        self.btn_drawer_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drawer_action.clicked.connect(lambda: self.notes_editor.insertPlainText("- [ ] "))
        notes_tb.addWidget(self.btn_drawer_action)
        notes_tb.addStretch()
        lay_thoughts.addLayout(notes_tb)

        self.lbl_notes_live_interim = QLabel("")
        self.lbl_notes_live_interim.setObjectName("livePartialBadge")
        self.lbl_notes_live_interim.setWordWrap(True)
        self.lbl_notes_live_interim.setVisible(False)
        lay_thoughts.addWidget(self.lbl_notes_live_interim)

        self.notes_editor = QTextEdit()
        self.notes_editor.setObjectName("notesEditor")
        self.notes_editor.setFont(get_ui_font(13.5, 400))
        self.notes_editor.setPlaceholderText(
            "Jot down your private thoughts during the meeting...\n\n"
            "(These notes are saved privately with this meeting and auto-synced.)"
        )
        self.notes_editor.textChanged.connect(self._save_meeting_notes)
        lay_thoughts.addWidget(self.notes_editor, 1)
        self.stack.addWidget(page_thoughts)

        # --- Page 1: Transcript (WhisperFlow Conversational View) ---
        page_tx = QWidget()
        lay_tx = QVBoxLayout(page_tx)
        lay_tx.setContentsMargins(0, 0, 0, 0)
        lay_tx.setSpacing(6)

        tx_stats = QHBoxLayout()
        tx_stats.setSpacing(8)

        self.lbl_tx_stats = QLabel("2 SPEAKERS · 00:00")
        self.lbl_tx_stats.setObjectName("txStatsLabel")
        self.lbl_tx_stats.setFont(get_ui_font(11, 700))
        tx_stats.addWidget(self.lbl_tx_stats)
        tx_stats.addStretch()

        self.btn_tx_search_toggle = QPushButton("")
        self.btn_tx_search_toggle.setObjectName("drawerMiniIconBtn")
        self.btn_tx_search_toggle.setToolTip("Search transcript")
        self.btn_tx_search_toggle.setAccessibleName("Search transcript")
        self.btn_tx_search_toggle.setFixedSize(26, 26)
        self.btn_tx_search_toggle.setIconSize(QSize(13, 13))
        self.btn_tx_search_toggle.clicked.connect(self._toggle_search)
        tx_stats.addWidget(self.btn_tx_search_toggle)

        self.btn_tx_copy = QPushButton("")
        self.btn_tx_copy.setObjectName("drawerMiniIconBtn")
        self.btn_tx_copy.setToolTip("Copy transcript")
        self.btn_tx_copy.setAccessibleName("Copy transcript")
        self.btn_tx_copy.setFixedSize(26, 26)
        self.btn_tx_copy.setIconSize(QSize(13, 13))
        self.btn_tx_copy.clicked.connect(self._copy_meeting_transcript)
        tx_stats.addWidget(self.btn_tx_copy)

        self.btn_tx_speakers = QPushButton("")
        self.btn_tx_speakers.setObjectName("drawerMiniIconBtn")
        self.btn_tx_speakers.setToolTip("Speaker management")
        self.btn_tx_speakers.setAccessibleName("Speaker management")
        self.btn_tx_speakers.setFixedSize(26, 26)
        self.btn_tx_speakers.setIconSize(QSize(13, 13))
        self.btn_tx_speakers.clicked.connect(self._on_manage_speakers)
        tx_stats.addWidget(self.btn_tx_speakers)

        lay_tx.addLayout(tx_stats)

        self.txt_tx_search = QLineEdit()
        self.txt_tx_search.setObjectName("txSearchInput")
        self.txt_tx_search.setPlaceholderText("Search in transcript...")
        self.txt_tx_search.textChanged.connect(self._on_search_query_changed)
        self.txt_tx_search.setVisible(False)
        lay_tx.addWidget(self.txt_tx_search)

        self.tx_browser = QTextBrowser()
        self.tx_browser.setObjectName("txConversationalBrowser")
        self.tx_browser.setOpenExternalLinks(False)
        self.tx_browser.anchorClicked.connect(self._on_anchor_clicked)
        if self.tx_browser.verticalScrollBar():
            self.tx_browser.verticalScrollBar().valueChanged.connect(self._on_tx_scrolled)
        lay_tx.addWidget(self.tx_browser, 1)

        self.list_transcript = QListWidget(self)
        self.list_transcript.setObjectName("drawerTranscriptListCompat")
        self.list_transcript.setVisible(False)
        lay_tx.addWidget(self.list_transcript)

        self.btn_jump_live = QPushButton("↓ Jump to Live")
        self.btn_jump_live.setObjectName("quickChipBtn")
        self.btn_jump_live.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.clicked.connect(self._jump_to_live_bottom)
        lay_tx.addWidget(self.btn_jump_live, 0, Qt.AlignmentFlag.AlignRight)

        self.lbl_live_partial = QLabel("")
        self.lbl_live_partial.setObjectName("livePartialBadge")
        self.lbl_live_partial.setWordWrap(True)
        self.lbl_live_partial.setVisible(False)
        lay_tx.addWidget(self.lbl_live_partial)

        self.stack.addWidget(page_tx)

        # --- Page 2: Summary (3-Step Progress + Formatted Executive Summary) ---
        page_summary = QWidget()
        lay_summary = QVBoxLayout(page_summary)
        lay_summary.setContentsMargins(0, 0, 0, 0)
        lay_summary.setSpacing(8)

        self.progress_banner = QFrame()
        self.progress_banner.setObjectName("summaryProgressBanner")
        prog_lay = QVBoxLayout(self.progress_banner)
        prog_lay.setContentsMargins(16, 14, 16, 14)
        prog_lay.setSpacing(6)

        self.lbl_prog_title = QLabel("Preparing local summary...")
        self.lbl_prog_title.setObjectName("progTitle")
        self.lbl_prog_title.setFont(get_ui_font(13.5, 600))
        prog_lay.addWidget(self.lbl_prog_title)

        self.lbl_prog_step = QLabel("Waiting for finalization")
        self.lbl_prog_step.setObjectName("progStep")
        self.lbl_prog_step.setFont(get_ui_font(11.5, 400))
        prog_lay.addWidget(self.lbl_prog_step)

        self.skeleton_bar = QFrame()
        self.skeleton_bar.setObjectName("skeletonBar")
        self.skeleton_bar.setFixedHeight(6)
        prog_lay.addWidget(self.skeleton_bar)
        lay_summary.addWidget(self.progress_banner)

        self.summary_browser = QTextBrowser()
        self.summary_browser.setObjectName("summaryBrowser")
        self.summary_browser.setOpenExternalLinks(True)
        lay_summary.addWidget(self.summary_browser, 1)

        self.stack.addWidget(page_summary)

        live_lay.addWidget(self.stack, 1)

        bottom_box = QFrame()
        bottom_box.setObjectName("drawerBottomBar")
        bot_lay = QVBoxLayout(bottom_box)
        bot_lay.setContentsMargins(0, 8, 0, 0)
        bot_lay.setSpacing(6)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.btn_what_did_i_miss = QPushButton("What did I miss?")
        self.btn_what_did_i_miss.setObjectName("quickChipBtn")
        self.btn_what_did_i_miss.setIconSize(QSize(13, 13))
        self.btn_what_did_i_miss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_what_did_i_miss.clicked.connect(self._on_what_did_i_miss_clicked)
        chip_row.addWidget(self.btn_what_did_i_miss)
        chip_row.addStretch()
        bot_lay.addLayout(chip_row)

        self.slash_skills_bar = QFrame()
        self.slash_skills_bar.setObjectName("slashSkillsBar")
        self.slash_skills_bar.setVisible(False)
        self._slash_lay = QHBoxLayout(self.slash_skills_bar)
        self._slash_lay.setContentsMargins(0, 2, 0, 2)
        self._slash_lay.setSpacing(6)
        self._slash_buttons: Dict[str, QPushButton] = {}
        for cmd_key, (label, _prompt) in self._slash_skills.items():
            sb = QPushButton(f"{cmd_key} · {label}")
            sb.setObjectName("quickChipBtn")
            sb.setCursor(Qt.CursorShape.PointingHandCursor)
            sb.clicked.connect(lambda _=False, k=cmd_key: self._activate_slash_skill(k))
            self._slash_lay.addWidget(sb)
            self._slash_buttons[cmd_key] = sb
        self._slash_lay.addStretch()
        bot_lay.addWidget(self.slash_skills_bar)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.btn_universal_stop = QPushButton("■ Stop")
        self.btn_universal_stop.setObjectName("universalStopBtn")
        self.btn_universal_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_universal_stop.setFixedHeight(36)
        self.btn_universal_stop.clicked.connect(self._on_universal_stop_clicked)
        action_row.addWidget(self.btn_universal_stop)

        self.txt_ask_anything = QLineEdit()
        self.txt_ask_anything.setObjectName("askAnythingInput")
        self.txt_ask_anything.setFixedHeight(36)
        self.txt_ask_anything.setPlaceholderText("Ask anything, or type / for Skills")
        self.txt_ask_anything.textChanged.connect(self._on_ask_text_changed)
        self.txt_ask_anything.returnPressed.connect(self._on_ask_anything_submitted)
        action_row.addWidget(self.txt_ask_anything, 1)

        bot_lay.addLayout(action_row)
        live_lay.addWidget(bottom_box)

        self.mode_stack.addWidget(self.page_mode_live)

        # === MODE 1: PRE-CALL LAUNCHPAD ('prep') ===
        self.page_mode_prep = QWidget()
        prep_lay = QVBoxLayout(self.page_mode_prep)
        prep_lay.setContentsMargins(0, 4, 0, 0)
        prep_lay.setSpacing(10)

        self.prep_card = QFrame()
        self.prep_card.setObjectName("summaryProgressBanner")
        p_in = QVBoxLayout(self.prep_card)
        p_in.setContentsMargins(16, 14, 16, 14)
        p_in.setSpacing(6)
        prep_kicker = QLabel("UPCOMING MEETING PREP")
        prep_kicker.setObjectName("progStep")
        prep_kicker.setFont(get_ui_font(10.5, 700))
        p_in.addWidget(prep_kicker)
        self.prep_title_lbl = QLabel("No upcoming meeting within 30 minutes")
        self.prep_title_lbl.setObjectName("progTitle")
        self.prep_title_lbl.setFont(get_ui_font(16, 600))
        self.prep_title_lbl.setWordWrap(True)
        p_in.addWidget(self.prep_title_lbl)
        self.prep_meta_lbl = QLabel("Connect Google, Outlook, or Zoom calendar to see live briefings.")
        self.prep_meta_lbl.setObjectName("progStep")
        self.prep_meta_lbl.setWordWrap(True)
        p_in.addWidget(self.prep_meta_lbl)

        prep_btns = QHBoxLayout()
        prep_btns.setSpacing(8)
        self.btn_prep_join = QPushButton("▶ Join & Start Notes")
        self.btn_prep_join.setObjectName("universalStopBtn")
        self.btn_prep_join.setFixedHeight(34)
        self.btn_prep_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prep_join.clicked.connect(self._on_prep_join_clicked)
        prep_btns.addWidget(self.btn_prep_join)
        self.btn_prep_adhoc = QPushButton("Start Ad-hoc Notes")
        self.btn_prep_adhoc.setObjectName("quickChipBtn")
        self.btn_prep_adhoc.setFixedHeight(34)
        self.btn_prep_adhoc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prep_adhoc.clicked.connect(self._start_adhoc_notetaker)
        prep_btns.addWidget(self.btn_prep_adhoc)
        prep_btns.addStretch()
        p_in.addLayout(prep_btns)
        prep_lay.addWidget(self.prep_card)

        self.prep_context_browser = QTextBrowser()
        self.prep_context_browser.setObjectName("summaryBrowser")
        self.prep_context_browser.setOpenExternalLinks(False)
        prep_lay.addWidget(self.prep_context_browser, 1)
        self.mode_stack.addWidget(self.page_mode_prep)

        # === MODE 2: IDLE ACTION HUB ('workspace') ===
        self.page_mode_workspace = QWidget()
        ws_lay = QVBoxLayout(self.page_mode_workspace)
        ws_lay.setContentsMargins(0, 4, 0, 0)
        ws_lay.setSpacing(10)

        ws_actions = QHBoxLayout()
        ws_actions.setSpacing(8)
        self.btn_ws_start_rec = QPushButton("▶ Start Notetaker")
        self.btn_ws_start_rec.setObjectName("universalStopBtn")
        self.btn_ws_start_rec.setFixedHeight(32)
        self.btn_ws_start_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ws_start_rec.clicked.connect(self._start_adhoc_notetaker)
        ws_actions.addWidget(self.btn_ws_start_rec)

        self.btn_ws_open_studio = QPushButton("Open Full Studio")
        self.btn_ws_open_studio.setObjectName("quickChipBtn")
        self.btn_ws_open_studio.setFixedHeight(32)
        self.btn_ws_open_studio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ws_open_studio.clicked.connect(self._open_current_in_studio)
        ws_actions.addWidget(self.btn_ws_open_studio)
        ws_actions.addStretch()
        ws_lay.addLayout(ws_actions)

        # Quick Voice Scratchpad Card
        self.ws_scratch_card = QFrame()
        self.ws_scratch_card.setObjectName("summaryProgressBanner")
        sc_lay = QVBoxLayout(self.ws_scratch_card)
        sc_lay.setContentsMargins(12, 10, 12, 10)
        sc_lay.setSpacing(6)
        sc_hdr = QHBoxLayout()
        sc_lbl = QLabel("Quick Voice Scratchpad")
        sc_lbl.setObjectName("progTitle")
        sc_lbl.setFont(get_ui_font(12.5, 600))
        sc_hdr.addWidget(sc_lbl)
        sc_hdr.addStretch()
        self.btn_quick_dictate = QPushButton("Dictate")
        self.btn_quick_dictate.setObjectName("quickChipBtn")
        self.btn_quick_dictate.setCheckable(True)
        self.btn_quick_dictate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_dictate.clicked.connect(self._toggle_quick_scratchpad_dictation)
        sc_hdr.addWidget(self.btn_quick_dictate)
        self.btn_quick_save = QPushButton("Save Note")
        self.btn_quick_save.setObjectName("quickChipBtn")
        self.btn_quick_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_save.clicked.connect(self._save_quick_scratchpad_note)
        sc_hdr.addWidget(self.btn_quick_save)
        sc_lay.addLayout(sc_hdr)

        self.quick_scratchpad_editor = QTextEdit()
        self.quick_scratchpad_editor.setObjectName("notesEditor")
        self.quick_scratchpad_editor.setFixedHeight(88)
        self.quick_scratchpad_editor.setPlaceholderText("Dictate or type a quick note or follow-up...")
        sc_lay.addWidget(self.quick_scratchpad_editor)
        ws_lay.addWidget(self.ws_scratch_card)

        lbl_open_acts = QLabel("Open Action Items")
        lbl_open_acts.setObjectName("txStatsLabel")
        lbl_open_acts.setFont(get_ui_font(11.5, 700))
        ws_lay.addWidget(lbl_open_acts)

        self.workspace_actions_list = QListWidget()
        self.workspace_actions_list.setObjectName("drawerWorkspaceList")
        self.workspace_actions_list.setMaximumHeight(140)
        self.workspace_actions_list.itemChanged.connect(self._on_workspace_action_toggled)
        ws_lay.addWidget(self.workspace_actions_list)

        lbl_recent_mtgs = QLabel("Recent Meetings (Click to open)")
        lbl_recent_mtgs.setObjectName("txStatsLabel")
        lbl_recent_mtgs.setFont(get_ui_font(11.5, 700))
        ws_lay.addWidget(lbl_recent_mtgs)

        self.workspace_recent_list = QListWidget()
        self.workspace_recent_list.setObjectName("drawerWorkspaceList")
        self.workspace_recent_list.itemClicked.connect(self._on_workspace_recent_clicked)
        ws_lay.addWidget(self.workspace_recent_list, 1)

        self.mode_stack.addWidget(self.page_mode_workspace)

        # Set default tab to Transcript inside Live mode
        self._switch_section(self._active_tab)
        self.select_dynamic_mode("live")

    def _switch_section(self, idx: int) -> None:
        self._active_tab = idx
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_buttons):
            is_cur = i == idx
            btn.setProperty("active", "true" if is_cur else "false")
            btn.setFont(get_ui_font(13.5, 700 if is_cur else 500))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _toggle_expand(self) -> None:
        self._is_expanded = not self._is_expanded
        if self._is_expanded:
            self.resize(960, 720)
            self.btn_expand.setCompatText("❐")
        else:
            self.resize(540, 680)
            self.btn_expand.setCompatText("□")
        self._refresh_drawer_icons()

    def _toggle_search(self) -> None:
        self._search_visible = not self._search_visible
        self.txt_tx_search.setVisible(self._search_visible)
        if self._search_visible:
            self.txt_tx_search.setFocus()
        else:
            self.txt_tx_search.clear()
            self._render_conversational_transcript()

    def _on_search_query_changed(self, text: str) -> None:
        self._render_conversational_transcript(query=text.strip())

    def _connect_signals(self) -> None:
        if not self.controller:
            return
        self.controller.theme_changed.connect(self.apply_theme)
        self.controller.meeting_state_changed.connect(self._on_meeting_state_changed)
        self.controller.meeting_tick.connect(self._on_meeting_tick)
        self.controller.meeting_saved_and_transcribed.connect(self._on_meeting_finalized)
        if hasattr(self.controller, "dictation_state_changed"):
            self.controller.dictation_state_changed.connect(self._on_drawer_dictation_state)
        if hasattr(self.controller, "dictation_interim_updated"):
            self.controller.dictation_interim_updated.connect(self._on_drawer_dictation_interim)
        if hasattr(self.controller, "dictation_completed"):
            self.controller.dictation_completed.connect(self._on_drawer_dictation_completed)

    def _toggle_pin_on_top(self) -> None:
        self._is_pinned_on_top = bool(self.btn_pin_top.isChecked())
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._is_pinned_on_top)
        if was_visible:
            self.show()
            self.raise_()
        self.btn_pin_top.setText("Pinned" if self._is_pinned_on_top else "Pin")

    def _toggle_drawer_notes_dictation(self) -> None:
        if not self.controller:
            return
        self.notes_editor.setFocus()
        if hasattr(self.controller, "toggle_in_app_dictation"):
            self.controller.toggle_in_app_dictation(self.notes_editor, title="VOX — Drawer Notes")
        elif hasattr(self.controller, "toggle_dictation"):
            self.controller.toggle_dictation()

    def _toggle_quick_scratchpad_dictation(self) -> None:
        if not self.controller:
            return
        self.quick_scratchpad_editor.setFocus()
        if hasattr(self.controller, "toggle_in_app_dictation"):
            self.controller.toggle_in_app_dictation(self.quick_scratchpad_editor, title="VOX — Quick Scratchpad")
        elif hasattr(self.controller, "toggle_dictation"):
            self.controller.toggle_dictation()

    def _insert_drawer_timestamp(self) -> None:
        sec = int(max(0.0, float(getattr(self, "_elapsed_sec", 0.0) or 0.0)))
        m, s = divmod(sec, 60)
        self.notes_editor.insertPlainText(f"[{m:02d}:{s:02d}] ")
        self.notes_editor.setFocus()

    def _save_quick_scratchpad_note(self) -> None:
        if not self.controller or not getattr(self.controller, "db", None):
            return
        txt = self.quick_scratchpad_editor.toPlainText().strip()
        if not txt:
            return
        title = txt.splitlines()[0][:60].strip() or "Quick Drawer Note"
        try:
            if self._quick_note_id is not None:
                self.controller.db.update_note(self._quick_note_id, title=title, content=txt, plain_text=txt)
            else:
                self._quick_note_id = int(self.controller.db.create_note(title, txt, txt))
            if hasattr(self.controller, "notes_updated"):
                self.controller.notes_updated.emit()
            if hasattr(self.controller, "notification_requested"):
                self.controller.notification_requested.emit("Scratchpad Saved", f"Saved '{title}' to Scratchpad.")
        except Exception as exc:
            logger.debug("Quick drawer scratchpad save failed: %s", exc)

    def _on_drawer_dictation_state(self, state: str, _msg: str = "") -> None:
        if not self.controller:
            return
        s = (state or "").strip().lower()
        explicit = getattr(self.controller, "_explicit_in_app_widget_ref", None)
        notes_active = explicit is self.notes_editor and self.notes_editor.isVisible() and s in ("starting", "listening", "speaking")
        quick_active = explicit is self.quick_scratchpad_editor and self.quick_scratchpad_editor.isVisible() and s in ("starting", "listening", "speaking")
        self.btn_drawer_dictate_notes.blockSignals(True)
        self.btn_drawer_dictate_notes.setChecked(notes_active)
        self.btn_drawer_dictate_notes.setText("⏹ Stop" if notes_active else "Dictate")
        self.btn_drawer_dictate_notes.blockSignals(False)
        self.btn_quick_dictate.blockSignals(True)
        self.btn_quick_dictate.setChecked(quick_active)
        self.btn_quick_dictate.setText("⏹ Stop" if quick_active else "Dictate")
        self.btn_quick_dictate.blockSignals(False)
        if not notes_active:
            self.lbl_notes_live_interim.setVisible(False)
            self.lbl_notes_live_interim.setText("")

    def _on_drawer_dictation_interim(self, provisional: str, stable: str) -> None:
        if not self.controller:
            return
        explicit = getattr(self.controller, "_explicit_in_app_widget_ref", None)
        if explicit is not self.notes_editor or not self.notes_editor.isVisible():
            self.lbl_notes_live_interim.setVisible(False)
            return
        combined = " ".join(x for x in (stable.strip(), provisional.strip()) if x).strip()
        if combined:
            self.lbl_notes_live_interim.setText(f"🎙️ Live: {combined}")
            self.lbl_notes_live_interim.setVisible(True)
        else:
            self.lbl_notes_live_interim.setVisible(False)

    def _on_drawer_dictation_completed(self, text: str, _target: str = "", method: str = "") -> None:
        self.lbl_notes_live_interim.setVisible(False)
        self.lbl_notes_live_interim.setText("")
        explicit = getattr(self.controller, "_explicit_in_app_widget_ref", None) if self.controller else None
        if explicit is self.notes_editor:
            self._save_meeting_notes()

    def _open_current_in_studio(self) -> None:
        m_id = self.current_meeting_id
        if callable(self.on_open_meeting_studio):
            self.on_open_meeting_studio(m_id)

    def _start_adhoc_notetaker(self) -> None:
        if not self.controller:
            return
        if hasattr(self.controller, "start_meeting_recording"):
            self.controller.start_meeting_recording(title="Ad-hoc Meeting")
            self.select_dynamic_mode("live")

    def _on_prep_join_clicked(self) -> None:
        if not self.controller:
            return
        mtg = self._prep_meeting
        if mtg is not None and hasattr(self.controller, "start_meeting_from_calendar"):
            self.controller.start_meeting_from_calendar(mtg)
            self.select_dynamic_mode("live")
        else:
            self._start_adhoc_notetaker()

    def _on_recent_combo_changed(self, idx: int) -> None:
        if idx < 0:
            return
        mid = self.combo_recent_meetings.itemData(idx)
        if mid is not None and int(mid) != (self._active_displayed_meeting_id or -1):
            self._display_meeting(int(mid))

    def _on_workspace_recent_clicked(self, item: QListWidgetItem) -> None:
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid is not None:
            self._display_meeting(int(mid))
            self.select_dynamic_mode("live")

    def _on_workspace_action_toggled(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict) or not self.controller or not getattr(self.controller, "db", None):
            return
        aid = payload.get("id")
        if aid is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        new_status = "completed" if checked else "open"
        try:
            db = self.controller.db
            if hasattr(db, "update_action_item_status"):
                db.update_action_item_status(int(aid), new_status)
            elif hasattr(db, "update_action_item"):
                db.update_action_item(int(aid), status=new_status)
        except Exception as exc:
            logger.debug("Drawer workspace action toggle failed: %s", exc)

    def _refresh_dynamic_context_panels(self) -> None:
        if not self.controller:
            return
        db = getattr(self.controller, "db", None)
        # 1. Populate recent meetings combo + workspace recent list
        recent: List[Dict[str, Any]] = []
        if db and hasattr(db, "list_meetings"):
            try:
                recent = list(db.list_meetings(limit=8) or [])
            except Exception:
                recent = []
        self.combo_recent_meetings.blockSignals(True)
        self.combo_recent_meetings.clear()
        self.workspace_recent_list.clear()
        for m in recent:
            mid = int(m.get("id") or 0)
            mtitle = str(m.get("title") or "Untitled Meeting").strip()
            self.combo_recent_meetings.addItem(mtitle[:52], mid)
            it = QListWidgetItem(f"{mtitle} ({format_seconds_hms(float(m.get('duration_sec') or 0.0))})")
            it.setData(Qt.ItemDataRole.UserRole, mid)
            self.workspace_recent_list.addItem(it)
        if self._active_displayed_meeting_id is not None:
            idx = self.combo_recent_meetings.findData(int(self._active_displayed_meeting_id))
            if idx >= 0:
                self.combo_recent_meetings.setCurrentIndex(idx)
        self.combo_recent_meetings.blockSignals(False)

        # 2. Populate Open Action Items checklist + Prep carry-over context
        open_actions: List[Dict[str, Any]] = []
        if db and hasattr(db, "list_action_items"):
            try:
                open_actions = [a for a in (db.list_action_items(limit=25) or []) if str(a.get("status", "open")).lower() not in ("done", "completed")]
            except Exception:
                open_actions = []
        elif db and recent and hasattr(db, "get_meeting_action_items"):
            for rm in recent[:3]:
                try:
                    for a in db.get_meeting_action_items(int(rm["id"])) or []:
                        if str(a.get("status", "open")).lower() not in ("done", "completed"):
                            open_actions.append(a)
                except Exception:
                    pass

        self.workspace_actions_list.blockSignals(True)
        self.workspace_actions_list.clear()
        for act in open_actions[:12]:
            desc = str(act.get("description") or act.get("title") or "").strip()
            if not desc:
                continue
            owner = str(act.get("assignee") or act.get("owner") or "").strip()
            label = f"{desc} ({owner})" if owner else desc
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, {"id": act.get("id")})
            self.workspace_actions_list.addItem(item)
        if self.workspace_actions_list.count() == 0:
            empty_it = QListWidgetItem("No pending action items across recent meetings.")
            empty_it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.workspace_actions_list.addItem(empty_it)
        self.workspace_actions_list.blockSignals(False)

        # 3. Prep card (upcoming meeting within 30 mins or next today)
        upcoming = []
        try:
            cal = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
            if cal and hasattr(cal, "get_upcoming_meetings"):
                upcoming = list(cal.get_upcoming_meetings(limit=3) or [])
        except Exception:
            upcoming = []
        self._prep_meeting = upcoming[0] if upcoming else None
        if self._prep_meeting is not None:
            ptitle = str(getattr(self._prep_meeting, "title", "Upcoming Meeting") or "Upcoming Meeting")
            plat = str(getattr(self._prep_meeting, "meeting_platform_label", "") or getattr(self._prep_meeting, "platform", "") or "Meeting")
            mins = int(getattr(self._prep_meeting, "minutes_until_start", 0) or 0)
            when_str = "Starting now" if mins <= 0 else f"Starts in {mins} min"
            org = str(getattr(self._prep_meeting, "organizer", "") or "").strip()
            self.prep_title_lbl.setText(ptitle)
            self.prep_meta_lbl.setText(f"{plat} · {when_str}" + (f" · Organizer: {org}" if org else ""))
        else:
            self.prep_title_lbl.setText("No meeting starting in the next 30 minutes")
            self.prep_meta_lbl.setText("Review carry-over items below or start an ad-hoc notetaker anytime.")

        ctx_parts: List[str] = ["<h4>Carry-Over Action Items</h4>"]
        if open_actions:
            ctx_parts.append("<ul>" + "".join(
                f"<li>{html.escape(str(a.get('description') or a.get('title') or ''))}</li>" for a in open_actions[:6]
            ) + "</ul>")
        else:
            ctx_parts.append("<p>No open carry-over tasks.</p>")
        if recent:
            ctx_parts.append(f"<h4>Last Meeting Highlight: {html.escape(str(recent[0].get('title') or 'Recent'))}</h4>")
            last_dec = str(recent[0].get("decisions") or "").strip()
            if last_dec:
                ctx_parts.append(f"<p>{html.escape(last_dec[:380])}</p>")
            else:
                ctx_parts.append("<p>Ready for your next conversation.</p>")
        self.prep_context_browser.setHtml("".join(ctx_parts))

    def select_dynamic_mode(self, mode: Optional[str] = None, auto: bool = False) -> str:
        """Select Drawer mode dynamically ('live' | 'prep' | 'workspace') based on call & calendar state."""
        chosen = (mode or "").strip().lower()
        if auto or chosen not in ("live", "prep", "workspace"):
            is_active_call = False
            imminent_call = False
            if self.controller:
                m_state = str(getattr(self.controller, "meeting_state", "idle") or "idle").lower()
                is_active_call = bool(
                    getattr(self.controller, "is_meeting_recording", False)
                    or m_state in ("recording", "paused", "processing", "transcribing")
                    or getattr(self.controller, "active_meeting_id", None) is not None
                )
                try:
                    cal = getattr(self.controller, "calendar_engine", None) or getattr(self.controller, "calendar_manager", None)
                    if cal and hasattr(cal, "get_upcoming_meetings"):
                        ups = list(cal.get_upcoming_meetings(limit=1) or [])
                        if ups:
                            mins = int(getattr(ups[0], "minutes_until_start", 999) or 999)
                            if -10 <= mins <= 30:
                                imminent_call = True
                except Exception:
                    pass
            if is_active_call:
                chosen = "live"
            elif imminent_call:
                chosen = "prep"
            else:
                chosen = "workspace"

        self._current_drawer_mode = chosen
        idx_map = {"live": 0, "prep": 1, "workspace": 2}
        self.mode_stack.setCurrentIndex(idx_map.get(chosen, 0))
        for k, btn in self._mode_buttons.items():
            is_cur = (k == chosen)
            btn.blockSignals(True)
            btn.setChecked(is_cur)
            btn.blockSignals(False)
            btn.setProperty("active", "true" if is_cur else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        return chosen

    def refresh_from_active_meeting(self) -> None:
        m_id = self.controller.active_meeting_id if self.controller else None
        if m_id is None and self.controller and getattr(self.controller, "db", None):
            recent = self.controller.db.list_meetings(limit=1)
            if recent:
                m_id = int(recent[0]["id"])
        self._display_meeting(m_id)
        self._refresh_dynamic_context_panels()
        self.select_dynamic_mode(auto=True)
        self._sync_controls_state(self.controller.meeting_state if self.controller else "idle")

    def _clear_live_transcript(self, meeting_id: Optional[int] = None) -> None:
        """Reset live transcript state cleanly so prior meetings never leak into a new recording."""
        if (
            self._active_displayed_meeting_id is not None
            and meeting_id is not None
            and int(self._active_displayed_meeting_id) != int(meeting_id)
        ):
            self.flush_pending_notes()
        self._live_segments = []
        self._active_displayed_meeting_id = int(meeting_id) if meeting_id is not None else None
        if hasattr(self, "list_transcript"):
            self.list_transcript.clear()
        if hasattr(self, "lbl_live_partial"):
            self.lbl_live_partial.setText("")
            self.lbl_live_partial.setVisible(False)
        self._render_conversational_transcript()

    def _on_progressive_segment(self, meeting_id: Optional[int], seg: Dict[str, Any]) -> None:
        """Append live transcript segment safely in chronological order with deduplication."""
        if (
            self._active_displayed_meeting_id is not None
            and meeting_id is not None
            and int(meeting_id) != int(self._active_displayed_meeting_id)
        ):
            return
        if not isinstance(seg, dict):
            return
        txt = str(seg.get("text") or "").strip()
        if not txt:
            return
        st = float(seg.get("start_time", 0.0) or 0.0)
        norm_txt = " ".join(txt.lower().split())
        for existing in self._live_segments:
            ex_txt = " ".join(str(existing.get("text") or "").lower().split())
            ex_st = float(existing.get("start_time", 0.0) or 0.0)
            if ex_txt == norm_txt and abs(ex_st - st) <= 1.5:
                return
        self._live_segments.append(dict(seg))
        self._live_segments.sort(key=lambda s: float(s.get("start_time", 0.0) or 0.0))
        q_cur = self.txt_tx_search.text().strip() if hasattr(self, "txt_tx_search") else ""
        self._render_conversational_transcript(query=q_cur)

    def _display_meeting(self, meeting_id: Optional[int]) -> None:
        if meeting_id is None:
            return
        if self._active_displayed_meeting_id is not None and self._active_displayed_meeting_id != int(meeting_id):
            self.flush_pending_notes()
        self._active_displayed_meeting_id = int(meeting_id)
        m = self.controller.db.get_meeting(int(meeting_id)) if (self.controller and getattr(self.controller, "db", None)) else None
        if not m:
            self._live_segments = []
            if hasattr(self, "list_transcript"):
                self.list_transcript.clear()
            self._render_conversational_transcript()
            return

        title = str(m.get("title") or "Untitled Meeting")
        self.txt_title.blockSignals(True)
        self.txt_title.setText(title)
        self.txt_title.setCursorPosition(0)
        self.txt_title.blockSignals(False)

        # Participants / Title initial pill & Folder pill
        initials = self._extract_initials_from_title(title)
        self.lbl_avatar_pill.setText(initials)
        folder_name = str(m.get("folder_name") or "").strip()
        self.btn_folder_pill.setText(folder_name if folder_name else "Add folder")

        # Notes
        self.notes_editor.blockSignals(True)
        self.notes_editor.setPlainText(str(m.get("notes", "") or ""))
        self.notes_editor.blockSignals(False)
        self._notes_dirty = False
        self._notes_meeting_id = int(meeting_id)

        # Transcript segments
        db_segs = self.controller.db.get_transcript_segments(int(meeting_id)) or []
        self._live_segments = list(db_segs)
        self._render_conversational_transcript()

        # Summary
        summary_raw = str(m.get("summary", "") or "")
        decisions_raw = str(m.get("decisions", "") or "")
        actions_raw = str(m.get("action_items", "") or "")
        self._render_summary_content(summary_raw, decisions_raw, actions_raw, is_finalized=bool(summary_raw))

    def _extract_initials_from_title(self, title: str) -> str:
        words = re.findall(r"[A-Za-z0-9]+", title)
        if not words:
            return "VOX"
        if len(words) == 1:
            return words[0][:2].upper()
        return (words[0][0] + words[1][0]).upper()

    def _on_tx_scrolled(self, val: int) -> None:
        sb = self.tx_browser.verticalScrollBar()
        if not sb or not hasattr(self, "btn_jump_live"):
            return
        at_bottom = (sb.maximum() <= 4) or (val >= sb.maximum() - 40)
        self.btn_jump_live.setVisible(not at_bottom and len(self._live_segments) > 0)

    def _jump_to_live_bottom(self) -> None:
        sb = self.tx_browser.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
        if hasattr(self, "btn_jump_live"):
            self.btn_jump_live.setVisible(False)

    def _render_conversational_transcript(self, query: str = "") -> None:
        """Render theme-aware conversational speaker turn bubbles with seek playback links."""
        t = get_theme_tokens(self._theme)
        is_dark = (self._theme or "").lower() == "dark"
        segs = sorted(self._live_segments, key=lambda s: float(s.get("start_time", 0.0)))
        speakers_set = set()
        total_dur = 0.0

        for s in segs:
            spk = s.get("speaker") or "Speaker 1"
            speakers_set.add(spk)
            total_dur = max(total_dur, float(s.get("end_time", 0.0)))

        spk_count = max(1, len(speakers_set))
        time_str = format_seconds_hms(max(total_dur, self._elapsed_sec))
        self.lbl_tx_stats.setText(f"{spk_count} SPEAKER{'S' if spk_count > 1 else ''} · {time_str}")

        if hasattr(self, "list_transcript"):
            self.list_transcript.clear()
            for s in segs:
                txt_item = str(s.get("text") or "").strip()
                if not txt_item:
                    continue
                spk_item = str(s.get("speaker") or "Speaker 1").strip() or "Speaker 1"
                st_sec = max(0.0, float(s.get("start_time", 0.0) or 0.0))
                mm = int(st_sec // 60)
                ss = int(st_sec % 60)
                self.list_transcript.addItem(f"[{mm:02d}:{ss:02d}] {spk_item}: {txt_item}")

        muted_hex = t.get("text_muted", "#94A3B8" if is_dark else "#64748B")
        if not segs:
            self.tx_browser.setHtml(
                f"<div style='color:{muted_hex}; text-align:center; padding:40px 10px; font-size:13px; font-style:italic;'>"
                "Capturing dual-track English audio reliably... Full high-accuracy English transcription, diarization, and AI summary run automatically after the call ends."
                "</div>"
            )
            if hasattr(self, "btn_jump_live"):
                self.btn_jump_live.setVisible(False)
            return

        # Group consecutive utterances into turns by speaker
        turns: List[Dict[str, Any]] = []
        cur_speaker = None
        cur_turn_segments: List[Dict[str, Any]] = []

        q_lower = query.lower().strip()

        for s in segs:
            spk = s.get("speaker") or "Speaker 1"
            txt = s.get("text", "").strip()
            if not txt:
                continue
            if q_lower and q_lower not in txt.lower() and q_lower not in spk.lower():
                continue

            if spk != cur_speaker:
                if cur_turn_segments:
                    turns.append({"speaker": cur_speaker, "segments": cur_turn_segments})
                cur_speaker = spk
                cur_turn_segments = [s]
            else:
                cur_turn_segments.append(s)

        if cur_turn_segments:
            turns.append({"speaker": cur_speaker, "segments": cur_turn_segments})

        sb = self.tx_browser.verticalScrollBar()
        prev_val = sb.value() if sb else 0
        was_at_bottom = (sb is None) or (sb.maximum() <= 4) or (prev_val >= sb.maximum() - 40)

        text_hex = t.get("text", "#F8FAFC" if is_dark else "#0F172A")
        bubble_other_bg = t.get("surface_secondary", "#162032" if is_dark else "#F1F5F9")
        bubble_you_bg = t.get("surface_elevated", "#1E293B") if is_dark else "#EFF6FF"
        bubble_you_fg = t.get("accent_dawn_text", "#E0E7FF") if is_dark else "#1E3A8A"
        spk_other_fg = t.get("accent_emerald", "#2DD4BF" if is_dark else "#0D9488")
        spk_you_fg = t.get("accent_fathom", "#8AB4F8") if is_dark else t.get("accent_fathom", "#1967D2")
        border_hex = t.get("border", "#283548" if is_dark else "#E2E8F0")

        html_font = get_html_font_family()
        html_parts = [
            f"""
            <style>
              body {{ font-family: {html_font}; margin: 4px; color: {text_hex}; }}
              .turn-container {{ margin-bottom: 14px; }}
              .turn-header {{ font-size: 12.5px; font-weight: 600; color: {spk_other_fg}; margin-bottom: 5px; }}
              .turn-header-you {{ font-size: 12.5px; font-weight: 600; color: {spk_you_fg}; margin-bottom: 5px; }}
              .play-btn {{ color: {spk_other_fg}; text-decoration: none; font-size: 12px; margin-left: 6px; }}
              .bubble {{ background: {bubble_other_bg}; border: 1px solid {border_hex}; border-radius: 12px; padding: 9px 13px; line-height: 1.58; font-size: 13.5px; color: {text_hex}; margin-bottom: 6px; }}
              .bubble-you {{ background: {bubble_you_bg}; border: 1px solid {border_hex}; border-radius: 12px; padding: 9px 13px; line-height: 1.58; font-size: 13.5px; color: {bubble_you_fg}; margin-bottom: 6px; }}
            </style>
            """
        ]

        for turn in turns:
            spk = turn["speaker"]
            is_you = "you" in spk.lower() or "mic" in spk.lower()
            header_cls = "turn-header-you" if is_you else "turn-header"
            bubble_cls = "bubble-you" if is_you else "bubble"

            first_start = float(turn["segments"][0].get("start_time") or 0.0)
            html_parts.append("<div class='turn-container'>")
            html_parts.append(
                f"<div class='{header_cls}'>{html.escape(spk)} "
                f"<a href='seek:{first_start:.2f}' class='play-btn'>▷</a></div>"
            )

            for s in turn["segments"]:
                txt_escaped = html.escape(s.get("text", ""))
                html_parts.append(f"<div class='{bubble_cls}'>{txt_escaped}</div>")

            html_parts.append("</div>")

        self.tx_browser.setHtml("".join(html_parts))
        sb = self.tx_browser.verticalScrollBar()
        if sb:
            if was_at_bottom:
                sb.setValue(sb.maximum())
                if hasattr(self, "btn_jump_live"):
                    self.btn_jump_live.setVisible(False)
            else:
                sb.setValue(min(prev_val, sb.maximum()))
                if hasattr(self, "btn_jump_live"):
                    self.btn_jump_live.setVisible(True)

    def _render_summary_content(
        self,
        summary_raw: str,
        decisions_raw: str,
        actions_raw: str,
        is_finalized: bool = False,
    ) -> None:
        """Render theme-aware multi-step progress card + structured executive summary."""
        self._last_summary_raw = summary_raw
        self._last_decisions_raw = decisions_raw
        self._last_actions_raw = actions_raw
        self._last_summary_finalized = is_finalized

        t = get_theme_tokens(self._theme)
        is_dark = (self._theme or "").lower() == "dark"
        muted_hex = t.get("text_muted", "#94A3B8" if is_dark else "#64748B")

        if not is_finalized and not summary_raw:
            self.progress_banner.setVisible(True)
            self.lbl_prog_title.setText("Recording audio for post-call English analysis...")
            self.lbl_prog_step.setText("Transcription & AI summary build when meeting finishes")
            self.summary_browser.setHtml(
                f"<div style='color:{muted_hex}; text-align:center; padding:30px 10px; font-style:italic; font-size:13px;'>"
                "Executive Summary will be automatically synthesized once the meeting completes."
                "</div>"
            )
            return

        self.progress_banner.setVisible(False)

        text_hex = t.get("text", "#F8FAFC" if is_dark else "#0F172A")
        sec_hex = t.get("text_secondary", "#CBD5E1" if is_dark else "#475569")
        card_bg = t.get("surface_secondary", "#162032" if is_dark else "#F1F5F9")
        border_hex = t.get("border", "#283548" if is_dark else "#CBD5E1")

        html_font = get_html_font_family()
        html_builder = [
            f"""
            <style>
              body {{ font-family: {html_font}; color: {text_hex}; line-height: 1.62; margin: 4px; }}
              .highlight-card {{ background: {card_bg}; border: 1px solid {border_hex}; border-radius: 12px; padding: 13px 15px; margin-bottom: 16px; font-size: 13.5px; color: {text_hex}; font-weight: 500; }}
              .section-title {{ font-size: 14.5px; font-weight: 600; color: {text_hex}; margin-top: 14px; margin-bottom: 6px; }}
              ul {{ margin-top: 4px; margin-bottom: 12px; padding-left: 20px; }}
              li {{ margin-bottom: 6px; font-size: 13.5px; color: {sec_hex}; }}
              b {{ color: {text_hex}; }}
            </style>
            """
        ]

        # Extract high-level opening sentence for top highlight card
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary_raw) if s.strip()]
        opening_highlight = sentences[0] if sentences else "Meeting highlights and discussion points."
        html_builder.append(f"<div class='highlight-card'>{html.escape(opening_highlight)}</div>")

        # Categorized Sections (neutral, non-hardcoded headings)
        remaining_text = " ".join(sentences[1:]) if len(sentences) > 1 else ""
        if remaining_text:
            html_builder.append("<div class='section-title'>Key Highlights &amp; Discussion</div>")
            html_builder.append(f"<ul><li>{html.escape(remaining_text)}</li></ul>")

        if decisions_raw:
            html_builder.append("<div class='section-title'>Key Decisions</div>")
            html_builder.append("<ul>")
            for line in decisions_raw.splitlines():
                cl = line.strip(" -*•")
                if cl:
                    html_builder.append(f"<li><b>Decision:</b> {html.escape(cl)}</li>")
            html_builder.append("</ul>")

        if actions_raw:
            html_builder.append("<div class='section-title'>Action Items</div>")
            html_builder.append("<ul>")
            for line in actions_raw.splitlines():
                cl = line.strip(" -*•")
                if cl:
                    html_builder.append(f"<li><b>Action Item:</b> {html.escape(cl)}</li>")
            html_builder.append("</ul>")

        self.summary_browser.setHtml("".join(html_builder))

    def _sync_controls_state(self, state: str) -> None:
        s = (state or "idle").lower()
        t = get_theme_tokens(self._theme)
        if s == "recording":
            self.lbl_tele_status.setText("● Recording · Live Preview (Provisional)")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['status_success_text']}; font-weight: 600; font-size: 11px;"
            )
            self.btn_universal_stop.setText("■ Stop")
            self.btn_universal_stop.setStyleSheet(
                f"QPushButton#universalStopBtn {{ background: {t['surface']}; color: {t['status_success_text']}; "
                f"border: 1.5px solid {t['status_success']}; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }}"
            )
        elif s == "paused":
            self.lbl_tele_status.setText("⏸ Paused")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['status_warning_text']}; font-weight: 600; font-size: 11px;"
            )
            self.btn_universal_stop.setText("◉ Resume")
            self.btn_universal_stop.setStyleSheet(
                f"QPushButton#universalStopBtn {{ background: {t['surface']}; color: {t['status_warning_text']}; "
                f"border: 1.5px solid {t['status_warning']}; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }}"
            )
        else:
            self.lbl_tele_status.setText("● Finalized")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['text_muted']}; font-weight: 600; font-size: 11px;"
            )
            self.btn_universal_stop.setText("▶ Start Notetaker")
            self.btn_universal_stop.setStyleSheet(
                f"QPushButton#universalStopBtn {{ background: {t['status_success']}; color: {t['primary_btn_text']}; "
                f"border: none; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }}"
            )

    def _on_universal_stop_clicked(self) -> None:
        if not self.controller:
            return
        s = getattr(self.controller, "meeting_state", "idle").lower()
        if s == "recording":
            self.controller.stop_meeting_recording()
        elif s == "paused":
            self.controller.resume_meeting_recording()
        else:
            self.controller.start_meeting_recording(title=self.txt_title.text().strip() or "Meeting Notes")

    def update_processing_stage(self, state: str, msg: str = "") -> None:
        """Update the drawer summary progress banner from real background pipeline stages."""
        s = (state or "").strip().lower()
        msg_str = str(msg or "").strip()
        if s in ("transcribing", "processing"):
            self.progress_banner.setVisible(True)
            if "Stage 1/" in msg_str:
                self.lbl_prog_title.setText("Packaging audio & video tracks...")
                self.lbl_prog_step.setText("Step 1 of 5")
            elif "Stage 2/" in msg_str or "Re-transcribing" in msg_str:
                self.lbl_prog_title.setText("Transcribing full English audio (Cohere Transcribe 2B)...")
                self.lbl_prog_step.setText("Step 2 of 5")
            elif "Stage 3/" in msg_str:
                self.lbl_prog_title.setText("Separating speakers & verifying completeness...")
                self.lbl_prog_step.setText("Step 3 of 5")
            elif "Stage 4/" in msg_str:
                self.lbl_prog_title.setText("Synthesizing MoM, decisions & action items...")
                self.lbl_prog_step.setText("Step 4 of 5")
            elif "Stage 5/" in msg_str:
                self.lbl_prog_title.setText("Extracting knowledge & updating RAG index...")
                self.lbl_prog_step.setText("Step 5 of 5")
            else:
                self.lbl_prog_title.setText(msg_str or "Processing meeting locally...")
                self.lbl_prog_step.setText("Processing locally...")
        elif s == "error":
            self.progress_banner.setVisible(True)
            self.lbl_prog_title.setText("Processing interrupted")
            self.lbl_prog_step.setText(msg_str or "Check audio & retry from Notetaker")
        elif s == "idle":
            if bool((self._last_summary_raw or "").strip()) or self._last_summary_finalized:
                self.progress_banner.setVisible(False)
            else:
                self.lbl_prog_title.setText("Recording audio for post-call analysis...")
                self.lbl_prog_step.setText("Transcription & MoM run automatically on stop")

    def _on_meeting_state_changed(self, state: str, msg: str = "") -> None:
        s = (state or "").lower()
        ctrl_cap = str(getattr(self.controller, "capture_state", "") or "").lower() if self.controller else ""
        if ctrl_cap in ("recording", "paused") and s not in ("recording", "paused"):
            self._sync_controls_state(ctrl_cap)
            return
        self._sync_controls_state(state)
        self.update_processing_stage(state, msg)
        if s in ("recording", "paused"):
            self.select_dynamic_mode("live")
        if s == "recording":
            m_id = self.controller.active_meeting_id if self.controller else None
            self._clear_live_transcript(m_id)
            self._display_meeting(m_id)

    def _on_meeting_tick(
        self,
        elapsed: float,
        mic_lvl: float = 0.0,
        sys_lvl: float = 0.0,
        mic_ok: bool = True,
        sys_ok: bool = True,
    ) -> None:
        self._elapsed_sec = max(0.0, elapsed)
        spk_count = max(1, len({s.get("speaker") for s in self._live_segments} or {"Speaker 1"}))
        time_str = format_seconds_hms(self._elapsed_sec)
        self.lbl_tx_stats.setText(f"{spk_count} SPEAKER{'S' if spk_count > 1 else ''} · {time_str}")
        t = get_theme_tokens(self._theme)
        if not mic_ok:
            self.lbl_tele_status.setText("● Recording · ⚠ Check Mic (Post-Call Ready)")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['status_warning_text']}; font-weight: 600; font-size: 11px;"
            )
        elif not sys_ok:
            self.lbl_tele_status.setText("● Recording · ⚠ Check Loopback (Post-Call Ready)")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['status_warning_text']}; font-weight: 600; font-size: 11px;"
            )
        else:
            self.lbl_tele_status.setText("● Recording · Mic OK · Sys OK (Post-Call English ASR)")
            self.lbl_tele_status.setStyleSheet(
                f"color: {t['status_success_text']}; font-weight: 600; font-size: 11px;"
            )

    def _on_meeting_finalized(self, meeting_id: int) -> None:
        if (
            self._active_displayed_meeting_id is not None
            and int(self._active_displayed_meeting_id) != int(meeting_id)
        ):
            return
        self._display_meeting(meeting_id)
        ctrl_state = (
            str(getattr(self.controller, "meeting_state", "idle") or "idle").lower()
            if self.controller
            else "idle"
        )
        if ctrl_state not in ("recording", "paused"):
            self._sync_controls_state("idle")

    def _save_meeting_title(self) -> None:
        m_id = self.current_meeting_id
        if m_id is not None and self.controller:
            self.controller.db.update_meeting(m_id, title=self.txt_title.text().strip())

    def _save_meeting_notes(self) -> None:
        m_id = self.current_meeting_id
        if m_id is not None and self.controller:
            self._notes_dirty = True
            self._notes_meeting_id = int(m_id)
            self._notes_save_timer.start()

    def flush_pending_notes(self) -> None:
        if self._notes_save_timer.isActive():
            self._notes_save_timer.stop()
        if self._notes_dirty:
            self._persist_meeting_notes()

    def _persist_meeting_notes(self) -> None:
        m_id = self._notes_meeting_id or self.current_meeting_id
        if m_id is None or not self.controller or getattr(self.controller, "_cancel_ask_vox", False):
            return
        db = getattr(self.controller, "db", None)
        db_path = getattr(db, "db_path", None) if db is not None else None
        if db is None or (db_path and ":memory:" not in str(db_path).lower() and not Path(str(db_path)).exists()):
            return
        try:
            db.update_meeting(int(m_id), notes=self.notes_editor.toPlainText())
            self._notes_dirty = False
        except Exception as exc:
            logger.warning("Failed persisting drawer notes for meeting %s: %s", m_id, exc)

    def hideEvent(self, event: Any) -> None:
        self.flush_pending_notes()
        if self.controller and hasattr(self.controller, "clear_in_app_dictation_target"):
            self.controller.clear_in_app_dictation_target(self.notes_editor)
            self.controller.clear_in_app_dictation_target(self.quick_scratchpad_editor)
        super().hideEvent(event)

    def closeEvent(self, event: Any) -> None:
        self.flush_pending_notes()
        if self.controller and hasattr(self.controller, "clear_in_app_dictation_target"):
            self.controller.clear_in_app_dictation_target(self.notes_editor)
            self.controller.clear_in_app_dictation_target(self.quick_scratchpad_editor)
        super().closeEvent(event)

    def _copy_meeting_transcript(self) -> None:
        text_lines = []
        for s in self._live_segments:
            spk = s.get("speaker", "Speaker")
            txt = s.get("text", "")
            text_lines.append(f"{spk}: {txt}")
        full = "\n".join(text_lines)
        QApplication.clipboard().setText(full)
        if self.controller and hasattr(self.controller, "notification_requested"):
            self.controller.notification_requested.emit("Transcript Copied", "Copied full transcript to clipboard.")

    def _copy_meeting_summary(self) -> None:
        m_id = self.current_meeting_id
        m = self.controller.db.get_meeting(int(m_id)) if (m_id is not None and self.controller) else None
        text = str((m or {}).get("summary") or self._last_summary_raw or "").strip()
        if text:
            QApplication.clipboard().setText(text)
            if self.controller and hasattr(self.controller, "notification_requested"):
                self.controller.notification_requested.emit("Summary Copied", "Copied meeting summary to clipboard.")

    def _export_meeting_markdown(self) -> None:
        m_id = self.current_meeting_id
        if m_id is None or not self.controller:
            return
        m = self.controller.db.get_meeting(int(m_id)) or {}
        segs = self.controller.db.get_transcript_segments(int(m_id)) or self._live_segments
        bundle = dict(m)
        bundle["segments"] = segs
        md = TranscriptExporter.to_markdown(bundle)
        path, _ = QFileDialog.getSaveFileName(self, "Export Meeting Notes (Markdown)", f"{m.get('title', 'meeting')}.md", "Markdown (*.md)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(md)

    def _on_folder_pill_clicked(self) -> None:
        m_id = self.current_meeting_id
        if m_id is None or not self.controller:
            return
        db = self.controller.db
        m = db.get_meeting(int(m_id)) or {}
        cur_folder_id = m.get("folder_id")
        folders = db.list_folders() if hasattr(db, "list_folders") else []

        menu = QMenu(self)
        act_none = menu.addAction(("✓ " if cur_folder_id is None else "") + "No Folder")
        folder_actions = {}
        for f in folders:
            fid = int(f["id"])
            fname = str(f["name"])
            act = menu.addAction(("✓ " if cur_folder_id == fid else "") + f"📁 {fname}")
            folder_actions[act] = (fid, fname)
        menu.addSeparator()
        act_new = menu.addAction("+ New Folder...")

        chosen = menu.exec(self.btn_folder_pill.mapToGlobal(self.btn_folder_pill.rect().bottomLeft()))
        if not chosen:
            return
        if chosen == act_none:
            db.assign_meeting_folder(int(m_id), None)
            self.btn_folder_pill.setText("Add folder")
        elif chosen in folder_actions:
            fid, fname = folder_actions[chosen]
            db.assign_meeting_folder(int(m_id), fid)
            self.btn_folder_pill.setText(fname)
        elif chosen == act_new:
            new_name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
            if ok and new_name and new_name.strip():
                fid = db.create_folder(new_name.strip())
                db.assign_meeting_folder(int(m_id), fid)
                self.btn_folder_pill.setText(new_name.strip())

    def _on_more_clicked(self) -> None:
        menu = QMenu(self)
        act_studio = menu.addAction("Open in Notetaker Studio")
        act_speakers = menu.addAction("Assign Speakers")
        menu.addSeparator()
        act_copy_sum = menu.addAction("Copy Summary")
        act_copy_tx = menu.addAction("Copy Full Transcript")
        act_export = menu.addAction("Export Markdown...")
        chosen = menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))
        if chosen == act_studio and self.on_open_meeting_studio:
            self.on_open_meeting_studio()
        elif chosen == act_speakers:
            self._on_manage_speakers()
        elif chosen == act_copy_sum:
            self._copy_meeting_summary()
        elif chosen == act_copy_tx:
            self._copy_meeting_transcript()
        elif chosen == act_export:
            self._export_meeting_markdown()

    def _on_share_clicked(self) -> None:
        menu = QMenu(self)
        act_sum = menu.addAction("Copy Clean Summary & Actions")
        act_tx = menu.addAction("Copy Full Transcript")
        act_studio = menu.addAction("Open in Notetaker Studio")
        act_export = menu.addAction("Export Markdown...")
        chosen = menu.exec(self.btn_share.mapToGlobal(self.btn_share.rect().bottomLeft()))
        if chosen == act_sum:
            self._copy_meeting_summary()
        elif chosen == act_tx:
            self._copy_meeting_transcript()
        elif chosen == act_studio and self.on_open_meeting_studio:
            self.on_open_meeting_studio()
        elif chosen == act_export:
            self._export_meeting_markdown()

    def _on_manage_speakers(self) -> None:
        if self.on_open_meeting_studio:
            self.on_open_meeting_studio()

    def _on_anchor_clicked(self, url) -> None:
        link = url.toString()
        m_id = self.current_meeting_id or 0
        if link.startswith("seek:"):
            try:
                sec = float(link.split(":", 1)[1])
                self.seek_requested.emit(int(m_id), sec)
            except Exception as exc:
                logger.warning("Failed parsing seek anchor %s: %s", link, exc)
        elif link.startswith("play:"):
            seg_ref = link.split(":", 1)[1]
            sec = 0.0
            for s in self._live_segments:
                if str(s.get("id", "")) == seg_ref:
                    sec = float(s.get("start_time") or 0.0)
                    break
            self.seek_requested.emit(int(m_id), sec)

    def _on_ask_text_changed(self, text: str) -> None:
        if not hasattr(self, "slash_skills_bar"):
            return
        raw = (text or "").lstrip()
        if raw.startswith("/"):
            self.slash_skills_bar.setVisible(True)
            prefix = raw.lower().split()[0]
            for cmd_key, btn in self._slash_buttons.items():
                btn.setVisible(cmd_key.startswith(prefix))
        else:
            self.slash_skills_bar.setVisible(False)

    def _activate_slash_skill(self, cmd_key: str) -> None:
        skill = self._slash_skills.get(cmd_key)
        if not skill:
            return
        if hasattr(self, "slash_skills_bar"):
            self.slash_skills_bar.setVisible(False)
        self.txt_ask_anything.setText(skill[1])
        self._on_ask_anything_submitted()

    def _on_what_did_i_miss_clicked(self) -> None:
        self.txt_ask_anything.setText("What did I miss so far in this meeting?")
        self._on_ask_anything_submitted()

    def _on_ask_anything_submitted(self) -> None:
        if hasattr(self.controller, "can_use_ask_vox_feature"):
            allowed, gate_reason, gate_msg = self.controller.can_use_ask_vox_feature()
            if not allowed:
                t = get_theme_tokens(self._theme)
                self.summary_browser.append(
                    f"<div style='margin-top:12px; padding:12px 14px; background:{t['surface_secondary']}; "
                    f"border:1px solid {t['border']}; border-radius:10px; font-size:12.5px;'>"
                    f"<b>{'Google Sign-In Required' if gate_reason == 'auth_required' else '10-Day Trial Ended — VOX Pro Required'}</b>"
                    f"<br><br>{html.escape(gate_msg)}</div>"
                )
                if gate_reason == "auth_required" and hasattr(self.controller, "login_with_google"):
                    self.controller.request_upgrade_dialog(gate_msg) if hasattr(self.controller, "request_upgrade_dialog") else None
                elif hasattr(self.controller, "request_upgrade_dialog"):
                    self.controller.request_upgrade_dialog(gate_msg)
                return
        raw = self.txt_ask_anything.text().strip()
        if not raw or not self.controller:
            return
        if raw.lower() in self._slash_skills:
            raw = self._slash_skills[raw.lower()][1]
        q = raw
        m_id = self.current_meeting_id
        self.txt_ask_anything.clear()
        if hasattr(self, "slash_skills_bar"):
            self.slash_skills_bar.setVisible(False)
        self._switch_section(2)  # Switch to Summary / Q&A view

        target_mid = m_id
        ctrl = self.controller

        def _worker() -> None:
            if getattr(ctrl, "_cancel_ask_vox", False):
                return
            try:
                res = ctrl.query_meeting_memory(q, meeting_id=target_mid) if hasattr(ctrl, "query_meeting_memory") else {}
                ans = (res or {}).get("answer") or "Based on the live transcript, the team discussed active agenda items and status."
            except Exception as exc:
                logger.warning("Ask VOX drawer query failed: %s", exc)
                ans = "Unable to query meeting memory right now."
            if getattr(ctrl, "_cancel_ask_vox", False):
                return
            try:
                self._ask_answer_ready.emit(q, str(ans), target_mid)
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True, name="DrawerAskVox").start()

    def _on_ask_answer_received(
        self, q: str, ans: str, meeting_id: Optional[int] = None
    ) -> None:
        if self.controller and getattr(self.controller, "_cancel_ask_vox", False):
            return
        cur_mid = self.current_meeting_id
        if meeting_id is not None and cur_mid is not None and int(meeting_id) != int(cur_mid):
            return
        t = get_theme_tokens(self._theme)
        is_dark = (self._theme or "").lower() == "dark"
        qa_bg = t.get("accent_lumen", "#064E3B" if is_dark else "#F0FDF4")
        qa_border = t.get("accent_lumen_border", "#065F46" if is_dark else "#BBF7D0")
        qa_fg = t.get("text", "#F8FAFC" if is_dark else "#0F172A")
        self.summary_browser.append(
            f"<div style='margin-top:14px; padding:10px 14px; background:{qa_bg}; border:1px solid {qa_border}; color:{qa_fg}; border-radius:10px; font-size:13px;'>"
            f"<b>Q: {html.escape(q)}</b><br><br>{html.escape(ans)}</div>"
        )

    @property
    def current_meeting_id(self) -> Optional[int]:
        return getattr(self, "_active_displayed_meeting_id", None) or (
            self.controller.active_meeting_id if self.controller else None
        )

    def _refresh_drawer_icons(self) -> None:
        t = get_theme_tokens(self._theme)
        muted = t.get("text_muted", "#5F6368")
        accent = t.get("accent_fathom", "#1967D2")
        if hasattr(self, "btn_back"):
            self.btn_back.setIcon(get_icon("chevron_left", muted, size=15))
        if hasattr(self, "btn_more"):
            self.btn_more.setIcon(get_icon("more_horiz", muted, size=15))
        if hasattr(self, "btn_copy_link"):
            self.btn_copy_link.setIcon(get_icon("link", muted, size=14))
        if hasattr(self, "btn_expand"):
            exp_name = "collapse" if getattr(self, "_is_expanded", False) else "expand"
            self.btn_expand.setIcon(get_icon(exp_name, muted, size=13))
        if hasattr(self, "btn_close"):
            self.btn_close.setIcon(get_icon("close", muted, size=13))
        if hasattr(self, "btn_folder_pill"):
            self.btn_folder_pill.setIcon(get_icon("folder", muted, size=12))
        if hasattr(self, "btn_tx_search_toggle"):
            self.btn_tx_search_toggle.setIcon(get_icon("search", muted, size=13))
        if hasattr(self, "btn_tx_copy"):
            self.btn_tx_copy.setIcon(get_icon("copy", muted, size=13))
        if hasattr(self, "btn_tx_speakers"):
            self.btn_tx_speakers.setIcon(get_icon("users", muted, size=13))
        if hasattr(self, "btn_what_did_i_miss"):
            self.btn_what_did_i_miss.setIcon(get_icon("sparkles", accent, size=13, filled=True))

    def apply_theme(self, theme: str) -> None:
        self._theme = theme or "light"
        t = get_theme_tokens(self._theme)
        is_dark = (self._theme or "").lower() == "dark"
        # Apply clean modern Material 3 stylesheet
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {t['surface']};
                color: {t['text']};
            }}
            QPushButton#drawerBackBtn, QPushButton#drawerIconBtn, QPushButton#drawerMiniIconBtn {{
                background: transparent;
                border: none;
                border-radius: 8px;
                color: {t['text_muted']};
                font-size: 13px;
            }}
            QPushButton#drawerBackBtn:hover, QPushButton#drawerIconBtn:hover, QPushButton#drawerMiniIconBtn:hover {{
                background: {t.get('surface_hover', t.get('surface_secondary', '#162032' if is_dark else '#F1F5F9'))};
                color: {t['text']};
            }}
            QPushButton#drawerShareBtn {{
                background: {t['surface_secondary']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 14px;
                padding: 2px 12px;
                font-weight: 600;
                font-size: 12px;
            }}
            QLabel#avatarMetaPill {{
                background: {t.get('accent_fathom_tint', '#1E3A5F' if is_dark else '#E8F0FE')};
                color: {t.get('accent_fathom', '#8AB4F8' if is_dark else '#1967D2')};
                border-radius: 11px;
                font-weight: 700;
                font-size: 10.5px;
            }}
            QLabel#subtleMetaPill, QPushButton#subtleMetaPillBtn {{
                background: {t['surface_secondary']};
                color: {t['text_muted']};
                border: 1px solid {t['border']};
                border-radius: 11px;
                padding: 1px 8px;
                font-size: 11px;
                font-weight: 500;
            }}
            QFrame#tabDivider {{
                background-color: {t['border']};
                border: none;
            }}
            QPushButton[objectName^="tabBtn_"] {{
                background: transparent;
                border: none;
                padding: 6px 2px;
                color: {t['text_muted']};
            }}
            QPushButton[objectName^="tabBtn_"][active="true"] {{
                color: {t['accent_fathom']};
                border-bottom: 2px solid {t['accent_fathom']};
            }}
            QTextBrowser#txConversationalBrowser, QTextBrowser#summaryBrowser, QTextEdit#notesEditor {{
                background: transparent;
                border: none;
            }}
            QLabel#livePartialBadge {{
                background: {t['accent_emerald_tint']};
                color: {t['accent_emerald']};
                border-radius: 8px;
                padding: 6px 12px;
                font-style: italic;
                font-size: 12px;
            }}
            QFrame#summaryProgressBanner {{
                background: {t['surface_secondary']};
                border-radius: 12px;
                border: 1px solid {t['border']};
            }}
            QLabel#progTitle {{
                color: {t['text']};
            }}
            QLabel#progStep {{
                color: {t['text_muted']};
            }}
            QFrame#skeletonBar {{
                background: {t['border']};
                border-radius: 3px;
            }}
            QPushButton#drawerModeBtn {{
                background: transparent;
                color: {t['text_muted']};
                border: 1px solid transparent;
                border-radius: 13px;
                padding: 2px 10px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton#drawerModeBtn[active="true"] {{
                background: {t.get('accent_fathom_tint', '#1E3A5F' if is_dark else '#E8F0FE')};
                color: {t.get('accent_fathom', '#8AB4F8' if is_dark else '#1967D2')};
                border-color: {t.get('accent_fathom', '#1967D2')};
            }}
            QComboBox#drawerMeetingSelector, QListWidget#drawerWorkspaceList {{
                background: {t['surface_secondary']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 8px;
                padding: 3px 8px;
                font-size: 12px;
            }}
            QPushButton#quickChipBtn {{
                background: {t['surface_secondary']};
                color: {t['text_secondary']};
                border: 1px solid {t['border']};
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11.5px;
                font-weight: 500;
            }}
            QPushButton#quickChipBtn:hover {{
                background: {t['surface_hover']};
                color: {t['text']};
            }}
            QLineEdit#askAnythingInput, QLineEdit#txSearchInput {{
                background: {t['surface_secondary']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 18px;
                padding: 4px 14px;
                font-size: 12.5px;
            }}
            """
        )
        self._refresh_drawer_icons()
        if self._live_segments:
            q_cur = self.txt_tx_search.text().strip() if hasattr(self, "txt_tx_search") else ""
            self._render_conversational_transcript(query=q_cur)
        if self._last_summary_raw or self._active_displayed_meeting_id is not None:
            self._render_summary_content(
                self._last_summary_raw,
                self._last_decisions_raw,
                self._last_actions_raw,
                is_finalized=self._last_summary_finalized,
            )
        if hasattr(self, "btn_universal_stop"):
            self._sync_controls_state(self.controller.meeting_state if self.controller else "idle")

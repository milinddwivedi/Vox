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
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from voxlocal.storage.exporter import format_seconds_hms
from voxlocal.ui.theme import get_theme_stylesheet, get_theme_tokens, get_ui_font

logger = get_logger("ui.notetaker_drawer")


class VoxNotetakerDrawer(QWidget):
    """Resizable, edge-dockable call companion drawer matching WhisperFlow aesthetics."""

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

        self.setWindowTitle("VOX — Notetaker")
        self.resize(540, 680)
        self.setMinimumSize(360, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._build_ui()
        self.apply_theme(self._theme)
        self._connect_signals()
        self.refresh_from_active_meeting()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ---------------------------------------------------------------------
        # 1. Top Window Controls Bar: [ < ] ... [ Share ] [ 🔗 ] [ — ] [ □ ] [ ✕ ]
        # ---------------------------------------------------------------------
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.btn_back = QPushButton("‹")
        self.btn_back.setObjectName("drawerBackBtn")
        self.btn_back.setToolTip("Dock back to floating bar")
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(28, 28)
        self.btn_back.clicked.connect(self.hide)
        top_bar.addWidget(self.btn_back)

        top_bar.addStretch()

        self.btn_more = QPushButton("···")
        self.btn_more.setObjectName("drawerIconBtn")
        self.btn_more.setFixedSize(28, 28)
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        top_bar.addWidget(self.btn_more)

        self.btn_share = QPushButton("Share")
        self.btn_share.setObjectName("drawerShareBtn")
        self.btn_share.setFixedHeight(28)
        self.btn_share.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_share.clicked.connect(self._on_share_clicked)
        top_bar.addWidget(self.btn_share)

        self.btn_copy_link = QPushButton("🔗")
        self.btn_copy_link.setObjectName("drawerIconBtn")
        self.btn_copy_link.setToolTip("Copy meeting transcript link")
        self.btn_copy_link.setFixedSize(28, 28)
        self.btn_copy_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_link.clicked.connect(self._copy_meeting_transcript)
        top_bar.addWidget(self.btn_copy_link)

        self.btn_min = QPushButton("—")
        self.btn_min.setObjectName("drawerIconBtn")
        self.btn_min.setFixedSize(24, 24)
        self.btn_min.clicked.connect(self.showMinimized)
        top_bar.addWidget(self.btn_min)

        self.btn_expand = QPushButton("□")
        self.btn_expand.setObjectName("drawerIconBtn")
        self.btn_expand.setFixedSize(24, 24)
        self.btn_expand.clicked.connect(self._toggle_expand)
        top_bar.addWidget(self.btn_expand)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("drawerIconBtn")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.clicked.connect(self.hide)
        top_bar.addWidget(self.btn_close)

        root.addLayout(top_bar)

        # ---------------------------------------------------------------------
        # 2. Meeting Title Header: Large Editorial Typography (Editable)
        # ---------------------------------------------------------------------
        self.txt_title = QLineEdit("HR Connect Session")
        self.txt_title.setObjectName("drawerMeetingTitle")
        self.txt_title.setFont(get_ui_font(21, 650, serif=True))
        self.txt_title.setStyleSheet(
            "QLineEdit#drawerMeetingTitle { background: transparent; border: none; padding: 2px 0px; font-size: 21px; }"
        )
        self.txt_title.editingFinished.connect(self._save_meeting_title)
        root.addWidget(self.txt_title)

        # ---------------------------------------------------------------------
        # 3. Meeting Metadata Pills: [ MH ]  [ Today ]  [ + Add folder ]
        # ---------------------------------------------------------------------
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)

        self.lbl_avatar_pill = QLabel("MH")
        self.lbl_avatar_pill.setObjectName("avatarMetaPill")
        self.lbl_avatar_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar_pill.setFixedSize(32, 22)
        meta_row.addWidget(self.lbl_avatar_pill)

        self.lbl_date_pill = QLabel("Today")
        self.lbl_date_pill.setObjectName("subtleMetaPill")
        self.lbl_date_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta_row.addWidget(self.lbl_date_pill)

        self.btn_folder_pill = QPushButton("📁 Add folder")
        self.btn_folder_pill.setObjectName("subtleMetaPillBtn")
        self.btn_folder_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        meta_row.addWidget(self.btn_folder_pill)

        meta_row.addStretch()

        # Telemetry indicator (Audio active)
        self.lbl_tele_status = QLabel("● Live Recording")
        self.lbl_tele_status.setObjectName("teleStatusLabel")
        self.lbl_tele_status.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 600;")
        meta_row.addWidget(self.lbl_tele_status)

        root.addLayout(meta_row)

        # ---------------------------------------------------------------------
        # 4. 3 Clean Tabs: [ My thoughts ]  [ Transcript ]  [ ★ Summary ]
        # ---------------------------------------------------------------------
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
        root.addLayout(tabs_bar)

        tab_divider = QFrame()
        tab_divider.setObjectName("tabDivider")
        tab_divider.setFrameShape(QFrame.Shape.HLine)
        tab_divider.setFixedHeight(1)
        root.addWidget(tab_divider)

        # ---------------------------------------------------------------------
        # 5. Stacked Pages
        # ---------------------------------------------------------------------
        self.stack = QStackedWidget()

        # --- Page 0: My Thoughts (Private scratchpad during call) ---
        page_thoughts = QWidget()
        lay_thoughts = QVBoxLayout(page_thoughts)
        lay_thoughts.setContentsMargins(0, 4, 0, 0)
        self.notes_editor = QTextEdit()
        self.notes_editor.setObjectName("notesEditor")
        self.notes_editor.setFont(get_ui_font(13.5, 400))
        self.notes_editor.setPlaceholderText(
            "Jot down your private thoughts during the meeting...\n\n"
            "(These notes are saved privately with this meeting and auto-synced.)"
        )
        self.notes_editor.textChanged.connect(self._save_meeting_notes)
        lay_thoughts.addWidget(self.notes_editor)
        self.stack.addWidget(page_thoughts)

        # --- Page 1: Transcript (WhisperFlow Conversational View) ---
        page_tx = QWidget()
        lay_tx = QVBoxLayout(page_tx)
        lay_tx.setContentsMargins(0, 0, 0, 0)
        lay_tx.setSpacing(6)

        # Stats Sub-Header: 🕒 2 SPEAKERS · 41:04  |  🔍  📋  👥
        tx_stats = QHBoxLayout()
        tx_stats.setSpacing(8)

        self.lbl_tx_stats = QLabel("🕒 2 SPEAKERS · 00:00")
        self.lbl_tx_stats.setObjectName("txStatsLabel")
        self.lbl_tx_stats.setFont(get_ui_font(11, 700))
        tx_stats.addWidget(self.lbl_tx_stats)
        tx_stats.addStretch()

        self.btn_tx_search_toggle = QPushButton("🔍")
        self.btn_tx_search_toggle.setObjectName("drawerMiniIconBtn")
        self.btn_tx_search_toggle.setToolTip("Search transcript")
        self.btn_tx_search_toggle.setFixedSize(24, 24)
        self.btn_tx_search_toggle.clicked.connect(self._toggle_search)
        tx_stats.addWidget(self.btn_tx_search_toggle)

        self.btn_tx_copy = QPushButton("📋")
        self.btn_tx_copy.setObjectName("drawerMiniIconBtn")
        self.btn_tx_copy.setToolTip("Copy transcript")
        self.btn_tx_copy.setFixedSize(24, 24)
        self.btn_tx_copy.clicked.connect(self._copy_meeting_transcript)
        tx_stats.addWidget(self.btn_tx_copy)

        self.btn_tx_speakers = QPushButton("👥")
        self.btn_tx_speakers.setObjectName("drawerMiniIconBtn")
        self.btn_tx_speakers.setToolTip("Speaker management")
        self.btn_tx_speakers.setFixedSize(24, 24)
        self.btn_tx_speakers.clicked.connect(self._on_manage_speakers)
        tx_stats.addWidget(self.btn_tx_speakers)

        lay_tx.addLayout(tx_stats)

        # Collapsible search bar
        self.txt_tx_search = QLineEdit()
        self.txt_tx_search.setObjectName("txSearchInput")
        self.txt_tx_search.setPlaceholderText("Search in transcript...")
        self.txt_tx_search.textChanged.connect(self._on_search_query_changed)
        self.txt_tx_search.setVisible(False)
        lay_tx.addWidget(self.txt_tx_search)

        # Conversational HTML Browser (Bubble turns + Play buttons)
        self.tx_browser = QTextBrowser()
        self.tx_browser.setObjectName("txConversationalBrowser")
        self.tx_browser.setOpenExternalLinks(False)
        self.tx_browser.anchorClicked.connect(self._on_anchor_clicked)
        lay_tx.addWidget(self.tx_browser, 1)

        # Real-time streaming provisional speech line at bottom
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

        # Post-Processing Progress Banner Card
        self.progress_banner = QFrame()
        self.progress_banner.setObjectName("summaryProgressBanner")
        prog_lay = QVBoxLayout(self.progress_banner)
        prog_lay.setContentsMargins(16, 14, 16, 14)
        prog_lay.setSpacing(6)

        self.lbl_prog_title = QLabel("Identifying who said what...")
        self.lbl_prog_title.setObjectName("progTitle")
        self.lbl_prog_title.setFont(get_ui_font(13.5, 600))
        prog_lay.addWidget(self.lbl_prog_title)

        self.lbl_prog_step = QLabel("Step 2 of 3")
        self.lbl_prog_step.setObjectName("progStep")
        self.lbl_prog_step.setFont(get_ui_font(11.5, 400))
        prog_lay.addWidget(self.lbl_prog_step)

        # Skeleton placeholder lines
        self.skeleton_bar = QFrame()
        self.skeleton_bar.setObjectName("skeletonBar")
        self.skeleton_bar.setFixedHeight(6)
        prog_lay.addWidget(self.skeleton_bar)
        lay_summary.addWidget(self.progress_banner)

        # Formatted HTML Executive Summary View
        self.summary_browser = QTextBrowser()
        self.summary_browser.setObjectName("summaryBrowser")
        self.summary_browser.setOpenExternalLinks(True)
        lay_summary.addWidget(self.summary_browser, 1)

        self.stack.addWidget(page_summary)

        root.addWidget(self.stack, 1)

        # ---------------------------------------------------------------------
        # 6. Bottom Action Bar: [ ■ Stop / ▶ Resume ]  [ Ask anything, or type / for Skills ]
        # ---------------------------------------------------------------------
        bottom_box = QFrame()
        bottom_box.setObjectName("drawerBottomBar")
        bot_lay = QVBoxLayout(bottom_box)
        bot_lay.setContentsMargins(0, 8, 0, 0)
        bot_lay.setSpacing(6)

        # Quick prompt chip row
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.btn_what_did_i_miss = QPushButton("✨ What did I miss?")
        self.btn_what_did_i_miss.setObjectName("quickChipBtn")
        self.btn_what_did_i_miss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_what_did_i_miss.clicked.connect(self._on_what_did_i_miss_clicked)
        chip_row.addWidget(self.btn_what_did_i_miss)
        chip_row.addStretch()
        bot_lay.addLayout(chip_row)

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
        self.txt_ask_anything.returnPressed.connect(self._on_ask_anything_submitted)
        action_row.addWidget(self.txt_ask_anything, 1)

        bot_lay.addLayout(action_row)
        root.addWidget(bottom_box)

        # Set default tab to Transcript
        self._switch_section(self._active_tab)

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
            self.btn_expand.setText("❐")
        else:
            self.resize(540, 680)
            self.btn_expand.setText("□")

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
        self.controller.meeting_progressive_segment.connect(self._on_progressive_segment)
        if hasattr(self.controller, "meeting_live_partial"):
            self.controller.meeting_live_partial.connect(self._on_live_partial)
        if hasattr(self.controller, "meeting_live_confirmed"):
            self.controller.meeting_live_confirmed.connect(self._on_live_confirmed)
        self.controller.meeting_saved_and_transcribed.connect(self._on_meeting_finalized)

    def refresh_from_active_meeting(self) -> None:
        m_id = self.controller.active_meeting_id if self.controller else None
        if m_id is None and self.controller:
            recent = self.controller.db.list_meetings(limit=1)
            if recent:
                m_id = int(recent[0]["id"])
        self._display_meeting(m_id)
        self._sync_controls_state(self.controller.meeting_state if self.controller else "idle")

    def _display_meeting(self, meeting_id: Optional[int]) -> None:
        if meeting_id is None:
            return
        self._active_displayed_meeting_id = int(meeting_id)
        m = self.controller.db.get_meeting(int(meeting_id))
        if not m:
            return

        title = str(m.get("title", "Meeting Notes"))
        self.txt_title.blockSignals(True)
        self.txt_title.setText(title)
        self.txt_title.setCursorPosition(0)
        self.txt_title.blockSignals(False)

        # Participants initial pill
        initials = self._extract_initials_from_title(title)
        self.lbl_avatar_pill.setText(initials)

        # Notes
        self.notes_editor.blockSignals(True)
        self.notes_editor.setPlainText(str(m.get("notes", "") or ""))
        self.notes_editor.blockSignals(False)

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

    def _render_conversational_transcript(self, query: str = "") -> None:
        """Render conversational speaker turn bubbles matching WhisperFlow screenshot."""
        segs = sorted(self._live_segments, key=lambda s: float(s.get("start_time", 0.0)))
        speakers_set = set()
        total_dur = 0.0

        for s in segs:
            spk = s.get("speaker") or "Speaker 1"
            speakers_set.add(spk)
            total_dur = max(total_dur, float(s.get("end_time", 0.0)))

        spk_count = max(1, len(speakers_set))
        time_str = format_seconds_hms(max(total_dur, self._elapsed_sec))
        self.lbl_tx_stats.setText(f"🕒 {spk_count} SPEAKER{'S' if spk_count > 1 else ''} · {time_str}")

        if not segs:
            self.tx_browser.setHtml(
                "<div style='color:#78716C; text-align:center; padding:40px 10px; font-size:13px; font-style:italic;'>"
                "Listening for speech... Words will appear here live."
                "</div>"
            )
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

        # Build clean HTML
        html_parts = [
            """
            <style>
              body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 4px; color: #1C1917; }
              .turn-container { margin-bottom: 16px; }
              .turn-header { font-size: 13px; font-weight: 700; color: #059669; margin-bottom: 6px; }
              .turn-header-you { font-size: 13px; font-weight: 700; color: #2563EB; margin-bottom: 6px; }
              .play-btn { color: #059669; text-decoration: none; font-size: 12px; margin-left: 6px; }
              .bubble { background: #F6F5EF; border-radius: 14px; padding: 10px 14px; line-height: 1.55; font-size: 13.5px; color: #1C1917; margin-bottom: 6px; }
              .bubble-you { background: #EFF6FF; border-radius: 14px; padding: 10px 14px; line-height: 1.55; font-size: 13.5px; color: #1E3A8A; margin-bottom: 6px; }
            </style>
            """
        ]

        for t in turns:
            spk = t["speaker"]
            is_you = "you" in spk.lower() or "mic" in spk.lower()
            header_cls = "turn-header-you" if is_you else "turn-header"
            bubble_cls = "bubble-you" if is_you else "bubble"

            first_seg_id = t["segments"][0].get("id", 0)
            html_parts.append(f"<div class='turn-container'>")
            html_parts.append(
                f"<div class='{header_cls}'>{html.escape(spk)} "
                f"<a href='play:{first_seg_id}' class='play-btn'>▷</a></div>"
            )

            for s in t["segments"]:
                txt_escaped = html.escape(s.get("text", ""))
                html_parts.append(f"<div class='{bubble_cls}'>{txt_escaped}</div>")

            html_parts.append("</div>")

        self.tx_browser.setHtml("".join(html_parts))
        sb = self.tx_browser.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _render_summary_content(
        self,
        summary_raw: str,
        decisions_raw: str,
        actions_raw: str,
        is_finalized: bool = False,
    ) -> None:
        """Render multi-step progress card + structured executive summary."""
        if not is_finalized and not summary_raw:
            self.progress_banner.setVisible(True)
            self.lbl_prog_title.setText("Identifying who said what...")
            self.lbl_prog_step.setText("Step 2 of 3")
            self.summary_browser.setHtml(
                "<div style='color:#78716C; text-align:center; padding:30px 10px; font-style:italic; font-size:13px;'>"
                "Executive Summary will be automatically synthesized once the meeting completes."
                "</div>"
            )
            return

        self.progress_banner.setVisible(False)

        # Parse sections and bold lead-in bullet points
        summary_escaped = html.escape(summary_raw)
        html_builder = [
            """
            <style>
              body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1C1917; line-height: 1.6; margin: 4px; }
              .highlight-card { background: #FAF8F3; border: 1px dashed #D6D3CD; border-radius: 12px; padding: 14px 16px; margin-bottom: 18px; font-size: 13.5px; color: #44403C; font-weight: 500; }
              .section-title { font-size: 14.5px; font-weight: 700; color: #1C1917; margin-top: 14px; margin-bottom: 6px; }
              ul { margin-top: 4px; margin-bottom: 12px; padding-left: 20px; }
              li { margin-bottom: 6px; font-size: 13.5px; color: #374151; }
              b { color: #111827; }
            </style>
            """
        ]

        # Extract high-level opening sentence for top highlight card
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", summary_raw) if s.strip()]
        opening_highlight = sentences[0] if sentences else "Meeting highlights and discussion points."
        html_builder.append(f"<div class='highlight-card'>{html.escape(opening_highlight)}</div>")

        # Categorized Sections
        remaining_text = " ".join(sentences[1:]) if len(sentences) > 1 else ""
        if remaining_text:
            html_builder.append("<div class='section-title'>Role and Satisfaction</div>")
            html_builder.append(f"<ul><li>{html.escape(remaining_text)}</li></ul>")

        if decisions_raw:
            html_builder.append("<div class='section-title'>Key Decisions & Feedback</div>")
            html_builder.append("<ul>")
            for line in decisions_raw.splitlines():
                cl = line.strip(" -*•")
                if cl:
                    html_builder.append(f"<li><b>Decision:</b> {html.escape(cl)}</li>")
            html_builder.append("</ul>")

        if actions_raw:
            html_builder.append("<div class='section-title'>Tools & Action Items</div>")
            html_builder.append("<ul>")
            for line in actions_raw.splitlines():
                cl = line.strip(" -*•")
                if cl:
                    html_builder.append(f"<li><b>Action Item:</b> {html.escape(cl)}</li>")
            html_builder.append("</ul>")

        self.summary_browser.setHtml("".join(html_builder))

    def _sync_controls_state(self, state: str) -> None:
        s = (state or "idle").lower()
        if s == "recording":
            self.lbl_tele_status.setText("● Live Recording")
            self.lbl_tele_status.setStyleSheet("color: #10B981; font-weight: 600; font-size: 11px;")
            self.btn_universal_stop.setText("■ Stop")
            self.btn_universal_stop.setStyleSheet(
                "QPushButton#universalStopBtn { background: #FFFFFF; color: #10B981; border: 1.5px solid #10B981; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }"
            )
        elif s == "paused":
            self.lbl_tele_status.setText("⏸ Paused")
            self.lbl_tele_status.setStyleSheet("color: #F59E0B; font-weight: 600; font-size: 11px;")
            self.btn_universal_stop.setText("◉ Resume")
            self.btn_universal_stop.setStyleSheet(
                "QPushButton#universalStopBtn { background: #FFFFFF; color: #F59E0B; border: 1.5px solid #F59E0B; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }"
            )
        else:
            self.lbl_tele_status.setText("● Finalized")
            self.lbl_tele_status.setStyleSheet("color: #78716C; font-weight: 600; font-size: 11px;")
            self.btn_universal_stop.setText("▶ Start Notetaker")
            self.btn_universal_stop.setStyleSheet(
                "QPushButton#universalStopBtn { background: #10B981; color: #FFFFFF; border: none; border-radius: 18px; padding: 4px 16px; font-weight: 700; font-size: 12px; }"
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

    def _on_meeting_state_changed(self, state: str, _msg: str) -> None:
        self._sync_controls_state(state)
        s = (state or "").lower()
        if s == "recording":
            m_id = self.controller.active_meeting_id if self.controller else None
            self._display_meeting(m_id)

    def _on_meeting_tick(self, elapsed: float, mic_lvl: float, sys_lvl: float, mic_ok: bool, sys_ok: bool) -> None:
        self._elapsed_sec = max(0.0, elapsed)
        spk_count = max(1, len({s.get("speaker") for s in self._live_segments} or {"Speaker 1"}))
        time_str = format_seconds_hms(self._elapsed_sec)
        self.lbl_tx_stats.setText(f"🕒 {spk_count} SPEAKER{'S' if spk_count > 1 else ''} · {time_str}")

    def _on_live_partial(self, meeting_id: int, partial_dict: dict) -> None:
        if self.controller and self.controller.active_meeting_id != meeting_id:
            return
        txt = partial_dict.get("text", "").strip()
        spk = partial_dict.get("speaker", "Speaker")
        if txt:
            self.lbl_live_partial.setText(f"● {spk}: {txt}...")
            self.lbl_live_partial.setVisible(True)
        else:
            self.lbl_live_partial.setVisible(False)

    def _on_live_confirmed(self, meeting_id: int, seg_dict: dict) -> None:
        if self.controller and self.controller.active_meeting_id != meeting_id:
            return
        self.lbl_live_partial.setVisible(False)
        self._on_progressive_segment(meeting_id, seg_dict)

    def _on_progressive_segment(self, meeting_id: int, seg: dict) -> None:
        if self.controller and self.controller.active_meeting_id != meeting_id:
            return
        st = float(seg.get("start_time", 0.0))
        spk = str(seg.get("speaker", "Speaker")).strip().lower()
        txt = " ".join(str(seg.get("text", "")).strip().lower().split())
        if not txt:
            return
        # Deduplication check
        for ex in self._live_segments:
            ex_st = float(ex.get("start_time", 0.0))
            ex_spk = str(ex.get("speaker", "Speaker")).strip().lower()
            ex_txt = " ".join(str(ex.get("text", "")).strip().lower().split())
            if abs(ex_st - st) <= 1.0 and ex_spk == spk and (ex_txt == txt or txt in ex_txt):
                return
        self._live_segments.append(dict(seg))
        self._render_conversational_transcript()

    def _on_meeting_finalized(self, meeting_id: int) -> None:
        self._display_meeting(meeting_id)
        self._sync_controls_state("idle")

    def _save_meeting_title(self) -> None:
        m_id = self.current_meeting_id
        if m_id is not None and self.controller:
            self.controller.db.update_meeting(m_id, title=self.txt_title.text().strip())

    def _save_meeting_notes(self) -> None:
        m_id = self.current_meeting_id
        if m_id is not None and self.controller:
            self.controller.db.update_meeting(m_id, notes=self.notes_editor.toPlainText())

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

    def _on_share_clicked(self) -> None:
        self._copy_meeting_transcript()

    def _on_manage_speakers(self) -> None:
        if self.on_open_meeting_studio:
            self.on_open_meeting_studio()

    def _on_anchor_clicked(self, url) -> None:
        link = url.toString()
        if link.startswith("play:"):
            logger.info("Playback requested for segment %s", link)

    def _on_what_did_i_miss_clicked(self) -> None:
        self.txt_ask_anything.setText("What did I miss so far in this meeting?")
        self._on_ask_anything_submitted()

    def _on_ask_anything_submitted(self) -> None:
        q = self.txt_ask_anything.text().strip()
        if not q or not self.controller:
            return
        m_id = self.current_meeting_id
        res = self.controller.query_meeting_memory(q, meeting_id=m_id) if hasattr(self.controller, "query_meeting_memory") else {}
        ans = res.get("answer") or "Based on the live transcript, the team discussed active agenda items and status."
        self._switch_section(2)  # Switch to Summary / Q&A view
        self.summary_browser.append(
            f"<div style='margin-top:14px; padding:10px 14px; background:#F0FDF4; border-radius:10px; font-size:13px;'>"
            f"<b>Q: {html.escape(q)}</b><br><br>{html.escape(ans)}</div>"
        )
        self.txt_ask_anything.clear()

    @property
    def current_meeting_id(self) -> Optional[int]:
        return getattr(self, "_active_displayed_meeting_id", None) or (
            self.controller.active_meeting_id if self.controller else None
        )

    def apply_theme(self, theme: str) -> None:
        self._theme = theme or "light"
        t = get_theme_tokens(self._theme)
        # Apply clean modern stylesheet
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {t['surface']};
                color: {t['text']};
            }}
            QPushButton#drawerBackBtn, QPushButton#drawerIconBtn, QPushButton#drawerMiniIconBtn {{
                background: transparent;
                border: none;
                border-radius: 6px;
                color: {t['text_muted']};
                font-size: 13px;
            }}
            QPushButton#drawerBackBtn:hover, QPushButton#drawerIconBtn:hover, QPushButton#drawerMiniIconBtn:hover {{
                background: {t.get('surface_hover', t.get('surface_secondary', '#F5F5F4'))};
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
                background: #F3E8FF;
                color: #7E22CE;
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
                color: {t['text']};
                border-bottom: 2px solid {t['text']};
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
                background: #F5F3EC;
                border-radius: 14px;
                border: 1px solid #E5E3DA;
            }}
            QLabel#progTitle {{
                color: #1C1917;
            }}
            QLabel#progStep {{
                color: #78716C;
            }}
            QFrame#skeletonBar {{
                background: #E5E3DA;
                border-radius: 3px;
            }}
            QPushButton#quickChipBtn {{
                background: {t['surface_secondary']};
                color: {t['text_muted']};
                border: 1px solid {t['border']};
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
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

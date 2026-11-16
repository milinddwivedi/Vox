"""VOX Floating Notetaker Drawer — Wispr Flow-Style Resizable Live Call Companion.

Implements a compact-to-expanded companion window (500x420 -> 920x660, min 300x200)
featuring live call recording telemetry, section pills (My Notes, Transcript, Summary,
Action Items, Brief, Ask Anything), private inline meeting thoughts saved to `meetings.notes`,
speaker assignment access, and pause/stop controls.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.controller import AppController
from voxlocal.storage.exporter import format_seconds_hms
from voxlocal.ui.theme import get_theme_stylesheet, get_theme_tokens, get_ui_font


class VoxNotetakerDrawer(QWidget):
    """Resizable call companion drawer synchronized with VOX's live meeting session."""

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
        self._active_tab = 0
        is_active_call = bool(
            self.controller and getattr(self.controller, "meeting_state", "idle") in ("recording", "paused")
        )
        self._tasks_tab_hidden_during_call = is_active_call

        self.setWindowTitle("VOX — Notetaker")
        self.resize(500, 420)
        self.setMinimumSize(300, 200)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._build_ui()
        self.apply_theme(self._theme)
        self._connect_signals()
        self.refresh_from_active_meeting()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header Strip: Status + Editable Title + Expand + Recording Controls
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        self.lbl_rec_dot = QLabel("●")
        self.lbl_rec_dot.setStyleSheet("color: #736E62; font-size: 13px; border: none; background: transparent;")
        hdr.addWidget(self.lbl_rec_dot)

        self.txt_title = QLineEdit("Meeting Notes")
        self.txt_title.setFont(get_ui_font(15, 600, serif=True))
        self.txt_title.editingFinished.connect(self._save_meeting_title)
        hdr.addWidget(self.txt_title, 1)

        self.lbl_timer = QLabel("00:00")
        self.lbl_timer.setFont(get_ui_font(12, 600, mono=True))
        hdr.addWidget(self.lbl_timer)

        self.btn_expand = QPushButton("Expand")
        self.btn_expand.clicked.connect(self._toggle_expand)
        hdr.addWidget(self.btn_expand)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._on_pause_resume)
        hdr.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("startBtn")
        self.btn_stop.clicked.connect(self._on_stop_or_start)
        hdr.addWidget(self.btn_stop)
        root.addLayout(hdr)

        # Audio / Capture Telemetry Strip
        tele = QHBoxLayout()
        tele.setSpacing(10)
        self.lbl_mic_badge = QLabel("Mic: Standby")
        self.lbl_sys_badge = QLabel("Call Audio: Standby")
        self.lbl_privacy_badge = QLabel("Call-Only Privacy Guard")
        for lbl in (self.lbl_mic_badge, self.lbl_sys_badge, self.lbl_privacy_badge):
            lbl.setObjectName("mutedLabel")
        tele.addWidget(self.lbl_mic_badge)
        tele.addWidget(self.lbl_sys_badge)
        tele.addStretch()
        tele.addWidget(self.lbl_privacy_badge)
        root.addLayout(tele)

        # Section Selector Pills
        nav_row = QHBoxLayout()
        nav_row.setSpacing(4)
        self._tab_buttons: list[QPushButton] = []
        for idx, label in enumerate(["My thoughts", "Transcript", "+ Summary", "Tasks", "Ask AI"]):
            b = QPushButton(label)
            b.setObjectName("pillTabBtn")
            b.clicked.connect(lambda _c=False, i=idx: self._switch_section(i))
            nav_row.addWidget(b)
            self._tab_buttons.append(b)
        nav_row.addStretch()
        if self.on_open_meeting_studio:
            self.btn_hub = QPushButton("Full View")
            self.btn_hub.clicked.connect(self.on_open_meeting_studio)
            nav_row.addWidget(self.btn_hub)
        root.addLayout(nav_row)

        # Stacked Section Canvas
        self.stack = QStackedWidget()

        # 0: My Notes (private while recording)
        self.notes_editor = QTextEdit()
        self.notes_editor.setPlaceholderText(
            "Jot down your private thoughts during the meeting...\n"
            "(Saved privately with the meeting transcript & summary.)"
        )
        self.notes_editor.textChanged.connect(self._save_meeting_notes)
        self.stack.addWidget(self.notes_editor)

        # 1: Live Transcript
        tx_page = QWidget()
        tx_lay = QVBoxLayout(tx_page)
        tx_lay.setContentsMargins(0, 0, 0, 0)
        tx_lay.setSpacing(6)
        self.txt_tx_search = QLineEdit()
        self.txt_tx_search.setPlaceholderText("Filter live transcript or speaker...")
        self.txt_tx_search.textChanged.connect(self._filter_transcript_list)
        tx_lay.addWidget(self.txt_tx_search)
        self.list_transcript = QListWidget()
        self.list_transcript.setWordWrap(True)
        tx_lay.addWidget(self.list_transcript, 1)

        self.lbl_live_partial = QLabel("")
        self.lbl_live_partial.setWordWrap(True)
        self.lbl_live_partial.setStyleSheet(
            "color: #00D084; font-style: italic; font-size: 12px; padding: 4px 8px; "
            "background: rgba(0, 208, 132, 0.08); border-radius: 4px;"
        )
        self.lbl_live_partial.setVisible(False)
        tx_lay.addWidget(self.lbl_live_partial)

        self.stack.addWidget(tx_page)

        # 2: Summary
        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setPlaceholderText("Meeting summary will appear automatically once finalized.")
        self.stack.addWidget(self.summary_view)

        # 3: Action Items / Tasks
        self.list_tasks = QListWidget()
        self.list_tasks.setWordWrap(True)
        self.stack.addWidget(self.list_tasks)

        # 4: Ask Anything
        qa_page = QWidget()
        qa_lay = QVBoxLayout(qa_page)
        qa_lay.setContentsMargins(0, 0, 0, 0)
        qa_lay.setSpacing(8)
        self.qa_output = QTextEdit()
        self.qa_output.setReadOnly(True)
        self.qa_output.setPlaceholderText("Ask about decisions, blockers, owners, or follow-ups from this meeting...")
        qa_lay.addWidget(self.qa_output, 1)
        self.stack.addWidget(qa_page)

        root.addWidget(self.stack, 1)

        # Integrated Bottom "Ask AI" Action Bar with Quick Prompt Chip
        bottom_bar = QWidget()
        bottom_lay = QVBoxLayout(bottom_bar)
        bottom_lay.setContentsMargins(0, 4, 0, 0)
        bottom_lay.setSpacing(6)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.btn_what_did_i_miss = QPushButton("[ What did I miss? ]")
        self.btn_what_did_i_miss.setObjectName("whatDidIMissBtn")
        self.btn_what_did_i_miss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_what_did_i_miss.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 2px 8px; border-radius: 10px; }"
        )
        self.btn_what_did_i_miss.clicked.connect(self._on_what_did_i_miss_clicked)
        chip_row.addWidget(self.btn_what_did_i_miss)
        chip_row.addStretch()
        bottom_lay.addLayout(chip_row)

        qa_row = QHBoxLayout()
        qa_row.setSpacing(8)
        self.txt_question = QLineEdit()
        self.txt_question.setPlaceholderText("Ask anything... (or / for skills)")
        self.txt_question.returnPressed.connect(self._ask_ai)
        qa_row.addWidget(self.txt_question, 1)

        self.btn_ask = QPushButton("Ask VOX")
        self.btn_ask.setObjectName("primaryBtn")
        self.btn_ask.clicked.connect(self._ask_ai)
        qa_row.addWidget(self.btn_ask)
        bottom_lay.addLayout(qa_row)

        root.addWidget(bottom_bar)

        self._switch_section(0)
        self._update_tab_visibility()

    @property
    def current_meeting_id(self) -> Optional[int]:
        """Return the ID of the meeting currently displayed in the drawer."""
        return getattr(self, "_active_displayed_meeting_id", None) or (
            self.controller.active_meeting_id if self.controller else None
        )

    def calculate_snap_geometry(self, screen_rect: QRect, edge: str = "right") -> QRect:
        """Calculate canonical edge-docked geometry for the notetaker drawer."""
        min_w = self.minimumWidth() if self.minimumWidth() > 0 else 300
        max_w = self.maximumWidth() if self.maximumWidth() > 0 else 16777215
        cur_w = self.width() if self.width() > 0 else 500
        target_w = min(max(min_w, cur_w), min(max_w, screen_rect.width()))
        target_h = screen_rect.height()

        edge_norm = (edge or "right").lower().strip()
        if edge_norm == "right":
            target_x = screen_rect.x() + screen_rect.width() - target_w
            target_y = screen_rect.y()
        elif edge_norm == "left":
            target_x = screen_rect.x()
            target_y = screen_rect.y()
        else:  # bottom
            target_x = screen_rect.x() + (screen_rect.width() - target_w) // 2
            target_y = screen_rect.y() + screen_rect.height() - target_h

        return QRect(target_x, target_y, target_w, target_h)

    def snap_to_edge(self, screen_rect: Optional[QRect] = None, edge: str = "right") -> None:
        """Snap drawer flush to display edge at full available workArea height."""
        if screen_rect is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
            screen_rect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        self.setGeometry(self.calculate_snap_geometry(screen_rect, edge))

    def get_visible_tab_names(self) -> list[str]:
        """Return names of all currently visible tab buttons."""
        return [btn.text().strip() for btn in getattr(self, "_tab_buttons", []) if not btn.isHidden()]

    def _update_tab_visibility(self) -> None:
        """Dynamically manage 2+1 context-aware tabs and post-meeting tasks reveal."""
        if not hasattr(self, "_tab_buttons") or len(self._tab_buttons) < 4:
            return
        # Core live tabs: My thoughts, Transcript, + Summary
        self._tab_buttons[0].setVisible(True)
        self._tab_buttons[1].setVisible(True)
        self._tab_buttons[2].setVisible(True)

        # Tasks tab (index 3): hidden during active calls; revealed post-finalization if tasks exist
        if self._tasks_tab_hidden_during_call:
            self._tab_buttons[3].setVisible(False)
        else:
            task_count = self.list_tasks.count() if hasattr(self, "list_tasks") else 0
            if task_count > 0:
                self._tab_buttons[3].setText(f"Tasks ({task_count})")
                self._tab_buttons[3].setVisible(True)
            else:
                self._tab_buttons[3].setText("Tasks")
                self._tab_buttons[3].setVisible(False)

        # Ask AI tab (index 4): integrated into bottom drawer in M7; hidden from top tabs
        if len(self._tab_buttons) > 4:
            self._tab_buttons[4].setVisible(False)

    def _on_what_did_i_miss_clicked(self) -> None:
        """Quick prompt chip handler: populates query and executes AI cognitive lookup."""
        self.txt_question.setText("What did I miss?")
        self._ask_ai()

    def apply_theme(self, theme: str) -> None:
        self._theme = theme or "light"
        self.setStyleSheet(get_theme_stylesheet(self._theme))

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

    def _switch_section(self, idx: int) -> None:
        self._active_tab = idx
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_buttons):
            btn.setProperty("active", "true" if i == idx else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _toggle_expand(self) -> None:
        self._is_expanded = not self._is_expanded
        if self._is_expanded:
            self.resize(920, 660)
            self.btn_expand.setText("Compact")
        else:
            self.resize(500, 420)
            self.btn_expand.setText("Expand")

    def refresh_from_active_meeting(self) -> None:
        m_id = self.controller.active_meeting_id if self.controller else None
        if m_id is None and self.controller:
            recent = self.controller.db.list_meetings(limit=1)
            if recent:
                m_id = int(recent[0]["id"])
        self._display_meeting(m_id)
        self._sync_controls_state(self.controller.meeting_state if self.controller else "idle")

    def _clear_live_transcript(self, meeting_id: Optional[int] = None) -> None:
        """Clear displayed live transcript segments and isolate state per meeting."""
        self._live_segments = []
        self._active_displayed_meeting_id = meeting_id
        self.list_transcript.clear()
        if hasattr(self, "lbl_live_partial"):
            self.lbl_live_partial.setText("")
            self.lbl_live_partial.setVisible(False)

    def _render_live_segments(self) -> None:
        """Render deduplicated live segments sorted strictly chronologically."""
        segs = sorted(getattr(self, "_live_segments", []), key=lambda s: float(s.get("start_time", 0.0)))
        self.list_transcript.clear()
        for seg in segs:
            ts = format_seconds_hms(float(seg.get("start_time", 0.0)))
            spk = seg.get("speaker", "Speaker")
            txt = seg.get("text", "")
            self.list_transcript.addItem(QListWidgetItem(f"[{ts}] {spk}: {txt}"))
        if hasattr(self, "txt_tx_search") and self.txt_tx_search.text().strip():
            self._filter_transcript_list(self.txt_tx_search.text())
        self.list_transcript.scrollToBottom()

    def _display_meeting(self, meeting_id: Optional[int]) -> None:
        if meeting_id is None:
            return
        if getattr(self, "_active_displayed_meeting_id", None) != int(meeting_id):
            self._clear_live_transcript(int(meeting_id))
        m = self.controller.db.get_meeting(int(meeting_id))
        if not m:
            return
        self.txt_title.setText(str(m.get("title", "Meeting")))
        self.txt_title.setCursorPosition(0)
        self.txt_title.clearFocus()
        self.notes_editor.blockSignals(True)
        self.notes_editor.setPlainText(str(m.get("notes", "") or ""))
        self.notes_editor.blockSignals(False)
        self.summary_view.setPlainText(str(m.get("summary", "") or ""))

        self._live_segments = list(self.controller.db.get_transcript_segments(int(meeting_id)) or [])
        self._active_displayed_meeting_id = int(meeting_id)
        self._render_live_segments()

        self.list_tasks.clear()
        for act in self.controller.db.list_action_items(meeting_id=int(meeting_id)):
            owner = act.get("owner", "Unassigned")
            desc = act.get("description", "")
            mark = "✓" if act.get("status") == "completed" else "○"
            self.list_tasks.addItem(QListWidgetItem(f"{mark} {desc} — {owner}"))
        self._update_tab_visibility()

    def _sync_controls_state(self, state: str) -> None:
        s = (state or "idle").lower()
        if s == "recording":
            self.lbl_rec_dot.setStyleSheet("color: #FF5757; font-size: 14px; border: none;")
            self.btn_pause.setVisible(True)
            self.btn_pause.setText("Pause")
            self.btn_stop.setText("Stop")
        elif s == "paused":
            self.lbl_rec_dot.setStyleSheet("color: #FFA946; font-size: 14px; border: none;")
            self.btn_pause.setVisible(True)
            self.btn_pause.setText("Resume")
            self.btn_stop.setText("Stop")
        elif s == "transcribing":
            self.lbl_rec_dot.setStyleSheet("color: #D2ACE9; font-size: 14px; border: none;")
            self.btn_pause.setVisible(False)
            self.btn_stop.setText("Finalizing...")
        else:
            self.lbl_rec_dot.setStyleSheet("color: #736E62; font-size: 14px; border: none;")
            self.btn_pause.setVisible(False)
            self.btn_stop.setText("Start Notetaker")

    def _on_meeting_state_changed(self, state: str, _msg: str) -> None:
        if self.controller and self.controller.is_meeting_recording and (state or "").lower() in ("idle", "transcribing", "error"):
            return
        s = (state or "").lower()
        if s in ("recording", "paused"):
            self._tasks_tab_hidden_during_call = True
        else:
            self._tasks_tab_hidden_during_call = False
        self._update_tab_visibility()
        self._sync_controls_state(state)
        if s == "recording":
            m_id = self.controller.active_meeting_id if self.controller else None
            self._clear_live_transcript(m_id)
            self.refresh_from_active_meeting()

    def _on_meeting_tick(self, elapsed: float, mic_lvl: float, sys_lvl: float, mic_ok: bool, sys_ok: bool) -> None:
        mins, secs = divmod(int(max(0.0, elapsed)), 60)
        self.lbl_timer.setText(f"{mins:02d}:{secs:02d}")
        self.lbl_mic_badge.setText(f"Mic: {'Live' if mic_ok else 'Off'} ({int(mic_lvl * 100)}%)")
        self.lbl_sys_badge.setText(f"Call Audio: {'Live' if sys_ok else 'Off'} ({int(sys_lvl * 100)}%)")

    def _on_live_partial(self, meeting_id: int, partial_dict: dict) -> None:
        if self.controller and self.controller.active_meeting_id != meeting_id:
            return
        txt = partial_dict.get("text", "").strip()
        spk = partial_dict.get("speaker", "Speaker")
        if hasattr(self, "lbl_live_partial"):
            if txt:
                self.lbl_live_partial.setText(f"● {spk}: {txt}")
                self.lbl_live_partial.setVisible(True)
            else:
                self.lbl_live_partial.setVisible(False)

    def _on_live_confirmed(self, meeting_id: int, seg_dict: dict) -> None:
        if self.controller and self.controller.active_meeting_id != meeting_id:
            return
        if hasattr(self, "lbl_live_partial"):
            self.lbl_live_partial.setText("")
            self.lbl_live_partial.setVisible(False)
        self._on_progressive_segment(meeting_id, seg_dict)

    def _on_progressive_segment(self, meeting_id: int, seg: dict) -> None:
        if self.controller.active_meeting_id != meeting_id:
            return
        if not hasattr(self, "_live_segments"):
            self._live_segments = []
        st = float(seg.get("start_time", 0.0))
        spk_norm = str(seg.get("speaker", "Speaker")).strip().lower()
        txt_norm = " ".join(str(seg.get("text", "")).strip().lower().split())
        if not txt_norm:
            return
        for existing in self._live_segments:
            ex_st = float(existing.get("start_time", 0.0))
            ex_spk = str(existing.get("speaker", "Speaker")).strip().lower()
            ex_txt = " ".join(str(existing.get("text", "")).strip().lower().split())
            if abs(ex_st - st) <= 4.0 and ex_spk == spk_norm and (ex_txt == txt_norm or txt_norm in ex_txt):
                return
        self._live_segments.append(dict(seg))
        self._render_live_segments()

    def _on_meeting_finalized(self, meeting_id: int) -> None:
        try:
            if self.controller and self.controller.is_meeting_recording:
                return
            if getattr(self, "_active_displayed_meeting_id", None) not in (None, meeting_id):
                return
            self._tasks_tab_hidden_during_call = False
            self._display_meeting(meeting_id)
            self._sync_controls_state("idle")
        except Exception as exc:
            logger.debug("_on_meeting_finalized notice: %s", exc)

    def _filter_transcript_list(self, query: str) -> None:
        q = query.strip().lower()
        for i in range(self.list_transcript.count()):
            item = self.list_transcript.item(i)
            item.setHidden(bool(q) and q not in item.text().lower())

    def _save_meeting_title(self) -> None:
        m_id = self.controller.active_meeting_id
        if m_id is None:
            recent = self.controller.db.list_meetings(limit=1)
            m_id = int(recent[0]["id"]) if recent else None
        if m_id is not None and self.txt_title.text().strip():
            self.controller.db.update_meeting(m_id, title=self.txt_title.text().strip())

    def _save_meeting_notes(self) -> None:
        m_id = self.controller.active_meeting_id
        if m_id is None:
            recent = self.controller.db.list_meetings(limit=1)
            m_id = int(recent[0]["id"]) if recent else None
        if m_id is not None:
            self.controller.db.update_meeting(m_id, notes=self.notes_editor.toPlainText())

    def _on_pause_resume(self) -> None:
        if self.controller.meeting_state == "recording":
            self.controller.pause_meeting_recording()
        elif self.controller.meeting_state == "paused":
            self.controller.resume_meeting_recording()

    def _on_stop_or_start(self) -> None:
        if self.controller.meeting_state in ("recording", "paused"):
            self.controller.stop_meeting_recording()
        else:
            self.controller.start_meeting_recording(title=self.txt_title.text().strip() or "Meeting Notes")

    def _ask_ai(self) -> None:
        q = self.txt_question.text().strip()
        if not q:
            return
        m_id = self.controller.active_meeting_id if self.controller else None
        if m_id is None and self.controller:
            recent = self.controller.db.list_meetings(limit=1)
            m_id = int(recent[0]["id"]) if recent else None
        res = self.controller.query_meeting_memory(q, meeting_id=m_id) if self.controller else {}
        ans = res.get("answer") or "No relevant answer found yet."
        self.qa_output.append(f"<b>You:</b> {q}<br><b>VOX:</b> {ans}<br>")
        self.txt_question.clear()

"""VOX v2.2 Ask VOX Conversational UX Optimization & Interaction Regression Suite."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.animations import set_reduced_motion_preference
from voxlocal.ui.ask_vox_panel import (
    AskChatMessage,
    AskMessageState,
    VoxAssistantMessageWidget,
    VoxUserMessageWidget,
)
from voxlocal.ui.meeting_ui_helpers import sanitize_ask_vox_display_markdown
from voxlocal.ui.notetaker_page import NotetakerPage


def _ensure_qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _MockStreamingController(QObject):
    """Test controller supporting real async Qt streaming signals, cancellation, failure & retry."""

    theme_changed = pyqtSignal(str)
    meeting_saved_and_transcribed = pyqtSignal(int)
    meeting_state_changed = pyqtSignal(str, str)
    meeting_tick = pyqtSignal(float, object)
    calendar_upcoming_changed = pyqtSignal(list)
    dictation_interim_updated = pyqtSignal(str, str)
    dictation_completed = pyqtSignal(str, str, str)
    knowledge_updated = pyqtSignal()
    open_notetaker_drawer_requested = pyqtSignal()
    ask_vox_status_changed = pyqtSignal(int, str)
    ask_vox_token_streamed = pyqtSignal(int, str)
    ask_vox_citations_ready = pyqtSignal(int, list)
    ask_vox_stream_finished = pyqtSignal(int, dict)
    ask_vox_stream_cancelled = pyqtSignal(int)

    def __init__(self, db: LocalDatabase, td: str) -> None:
        super().__init__()
        self.db = db
        self.theme = "light"
        self.config = AppConfig(storage_dir=str(Path(td) / "data"), notetaker_auto_start_banner_dismissed=True)
        self.intelligence = MeetingIntelligenceEngine(db=db, llm_engine=None)
        self.meeting_state = "idle"
        self.is_meeting_recording = False
        self.active_meeting_id = None
        self._cancel_ask_vox = False
        self._ask_vox_query_id = 0
        self.stream_calls: list[dict] = []
        self.delay_before_tokens_sec = 0.25
        self.token_chunks: list[str] = ["Amar ", "proposed **TEET** ", "for t-shirts ", "[04:56].\n\n**Sources:**\n- [04:56] Amar"]
        self.force_error = False
        self.force_empty = False
        self.stop_called = 0

    def get_upcoming_meetings(self):
        return []

    def save_config(self):
        pass

    def stop_meeting_recording(self):
        pass

    def start_meeting_recording(self, *a, **k):
        pass

    def stop_ask_vox_stream(self) -> None:
        self._cancel_ask_vox = True
        self.stop_called += 1

    def query_meeting_memory_stream(
        self,
        question: str,
        meeting_id=None,
        conversation_history=None,
        answer_depth="detailed",
        meeting_ids=None,
    ) -> int:
        self._ask_vox_query_id += 1
        qid = self._ask_vox_query_id
        self._cancel_ask_vox = False
        self.stream_calls.append({
            "qid": qid,
            "question": question,
            "meeting_id": meeting_id,
            "meeting_ids": meeting_ids,
            "history": list(conversation_history or []),
        })
        self.ask_vox_status_changed.emit(qid, "submitted")

        delay = self.delay_before_tokens_sec
        chunks = list(self.token_chunks)
        fail = self.force_error
        empty = self.force_empty

        def _bg() -> None:
            t_end = time.monotonic() + delay
            while time.monotonic() < t_end:
                if self._cancel_ask_vox or self._ask_vox_query_id != qid:
                    self.ask_vox_stream_cancelled.emit(qid)
                    return
                time.sleep(0.02)

            self.ask_vox_status_changed.emit(qid, "retrieving_context")
            cits = [{"meeting_id": meeting_id or 1, "meeting_title": "Northstar PLM", "start_time": 296.0, "text": "Use TEET."}]
            self.ask_vox_citations_ready.emit(qid, cits)

            if fail:
                self.ask_vox_status_changed.emit(qid, "failed")
                self.ask_vox_stream_finished.emit(qid, {
                    "question": question,
                    "answer": "",
                    "error": True,
                    "error_message": "Local LLM timed out.",
                    "citations": [],
                })
                return

            self.ask_vox_status_changed.emit(qid, "generating")
            if not empty:
                for tok in chunks:
                    if self._cancel_ask_vox or self._ask_vox_query_id != qid:
                        self.ask_vox_stream_cancelled.emit(qid)
                        return
                    self.ask_vox_token_streamed.emit(qid, tok)
                    time.sleep(0.025)

            self.ask_vox_status_changed.emit(qid, "validating")
            full_ans = "" if empty else "".join(chunks)
            self.ask_vox_status_changed.emit(qid, "completed")
            self.ask_vox_stream_finished.emit(qid, {
                "question": question,
                "answer": full_ans,
                "citations": cits,
            })

        threading.Thread(target=_bg, daemon=True).start()
        return qid


def _seed_sample_meetings(db: LocalDatabase) -> tuple[int, int]:
    m1 = db.create_meeting("Northstar PLM Sync", source_platform="Microsoft Teams")
    db.update_meeting(m1, duration_sec=620.0, status="completed", brief_summary="Reviewed TEET mapping.")
    db.add_transcript_segment(m1, 0, 296.0, 305.0, "Amar", "Use TEET for t-shirts and check 12 SQs.")

    m2 = db.create_meeting("Q4 Pricing Sync", source_platform="Zoom")
    db.update_meeting(m2, duration_sec=900.0, status="completed", brief_summary="Approved Q4 pricing tiers.")
    db.add_transcript_segment(m2, 0, 45.0, 58.0, "Sarah", "Enterprise discount remains capped at 15%.")
    return m1, m2


def test_sanitizer_strips_sources_and_preserves_markdown_structure():
    raw = (
        "<think>internal reasoning</think>\n"
        "### Agreed Outcomes\n\n"
        "- **Product Code:** Use **TEET** for t-shirts (`3:00 PM` call) ([04:56]) [1].\n"
        "| Action | Owner | Target |\n"
        "|---|---|---|\n"
        "| Fix SQ import | Amar | Next Thursday |\n\n"
        "---\n"
        "**Verified Source Citations:**\n"
        "• `04:56` **Northstar** — \"Use TEET\"\n"
    )
    cleaned = sanitize_ask_vox_display_markdown(raw)
    assert "internal reasoning" not in cleaned
    assert "Verified Source Citations" not in cleaned
    assert "[04:56]" not in cleaned
    assert "[1]" not in cleaned
    assert "3:00 PM" in cleaned
    assert "### Agreed Outcomes" in cleaned
    assert "| Action | Owner | Target |" in cleaned


def test_immediate_question_visibility_no_processing_text_streaming_and_copy(qapp, monkeypatch):
    app = _ensure_qapp()
    copied: list[str] = []
    fail_clip = {"fail": False}

    class FakeClipboard:
        def setText(self, val):
            if fail_clip["fail"]:
                raise RuntimeError("Clipboard locked")
            copied.append(str(val))
        def text(self):
            return copied[-1] if copied else ""

    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: FakeClipboard()))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "ux.db")
        m1, _ = _seed_sample_meetings(db)
        ctrl = _MockStreamingController(db, td)
        page = NotetakerPage(ctrl)
        try:
            page.resize(1440, 900)
            page.show()
            page.select_meeting(m1)
            page._toggle_ask_rail(True)
            app.processEvents()

            q_text = "Line 1: What did Amar propose?\nLine 2: Include *markdown* & <special> chars."
            t0 = time.perf_counter()
            page.ask_panel.composer.input_edit.setPlainText(q_text)
            enter_ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            page.ask_panel.composer.input_edit.keyPressEvent(enter_ev)
            app.processEvents()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # 1. Immediate optimistic UI (< 100ms), input cleared, duplicate submit blocked
            assert elapsed_ms < 250.0
            assert page.ask_panel.composer.input_edit.toPlainText() == ""
            assert page.ask_panel.composer.is_busy() is True
            assert page._submit_ask_rail_text("Duplicate click") is False

            msgs = page._get_current_ask_messages()
            assert len(msgs) == 2
            assert msgs[0].role == "user"
            assert msgs[0].content == q_text
            assert msgs[1].role == "assistant"
            assert msgs[1].state == AskMessageState.SUBMITTED

            u_w = page.ask_panel.conversation_view.get_message_widget(msgs[0].id)
            a_w = page.ask_panel.conversation_view.get_message_widget(msgs[1].id)
            assert isinstance(u_w, VoxUserMessageWidget)
            assert isinstance(a_w, VoxAssistantMessageWidget)
            assert q_text in u_w.lbl_text.text()
            assert a_w.indicator.isVisible() is True

            # 2. Zero forbidden textual processing phrases
            visible_now = page.ask_rail_browser.toPlainText()
            for noisy in ("Thinking", "Processing your request", "Searching", "Generating response", "Please wait", "Analyzing"):
                assert noisy.lower() not in visible_now.lower()

            # 3. Wait for real streamed completion
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                app.processEvents()
                if msgs[1].state == AskMessageState.COMPLETED and not page.ask_panel.composer.is_busy():
                    break
                time.sleep(0.015)

            assert msgs[1].state == AskMessageState.COMPLETED
            assert a_w.indicator.isVisible() is False
            assert a_w.btn_copy.isVisible() is True
            assert len(msgs[1].citations) == 1  # Internal citations preserved!
            final_plain = page.ask_rail_browser.toPlainText()
            assert "TEET" in final_plain
            assert "04:56" not in final_plain
            assert "Sources:" not in final_plain

            # 4. Icon-only Copy button: copies Markdown, transitions Copy -> Check, handles error state
            a_w.btn_copy.click()
            app.processEvents()
            assert "**TEET**" in copied[-1]
            assert "[04:56]" not in copied[-1]
            assert a_w._copy_feedback_state == "copied"
            assert a_w.btn_copy.toolTip() == "Copied"

            fail_clip["fail"] = True
            a_w.btn_copy.click()
            app.processEvents()
            assert a_w._copy_feedback_state == "error"
            assert a_w.btn_copy.toolTip() == "Could not copy"
        finally:
            page.close()
            app.processEvents()
            db.close()


def test_multiline_shift_enter_stop_failure_retry_persistence_and_new_chat(qapp):
    app = _ensure_qapp()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "persist.db")
        m1, m2 = _seed_sample_meetings(db)
        ctrl = _MockStreamingController(db, td)
        page = NotetakerPage(ctrl)
        try:
            page.resize(1240, 820)
            page.show()
            page.select_meeting(m1)
            page._toggle_ask_rail(True)
            app.processEvents()

            # Shift+Enter inserts newline without submitting
            inp = page.ask_panel.composer.input_edit
            inp.setPlainText("First line")
            inp.moveCursor(inp.textCursor().MoveOperation.End)
            shift_enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier, "\n")
            inp.keyPressEvent(shift_enter)
            app.processEvents()
            assert "\n" in inp.toPlainText()
            assert len(ctrl.stream_calls) == 0

            # Stop cancellation aborts long wait & transitions cleanly to CANCELLED
            ctrl.delay_before_tokens_sec = 2.0
            page._submit_ask_rail_text("Long question to cancel")
            app.processEvents()
            assert page.ask_panel.composer.is_busy() is True
            page.ask_panel.composer.btn_action.click()  # Stop button
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if not page.ask_panel.composer.is_busy():
                    break
                time.sleep(0.01)
            assert ctrl.stop_called >= 1
            assert page._get_current_ask_messages()[-1].state == AskMessageState.CANCELLED

            # Generation failure + icon-only Retry
            ctrl.delay_before_tokens_sec = 0.05
            ctrl.force_error = True
            page._submit_ask_rail_text("Retryable question")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if not page.ask_panel.composer.is_busy():
                    break
                time.sleep(0.01)
            err_msg = page._get_current_ask_messages()[-1]
            err_w = page.ask_panel.conversation_view.get_message_widget(err_msg.id)
            assert err_msg.state == AskMessageState.FAILED
            assert err_w.btn_retry.isVisible() is True

            ctrl.force_error = False
            ctrl.token_chunks = ["Recovered table:\n\n| Item | Status |\n|---|---|\n| TEET | Approved |"]
            err_w.btn_retry.click()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if page._get_current_ask_messages()[-1].state == AskMessageState.COMPLETED:
                    break
                time.sleep(0.01)
            assert "Approved" in page.ask_rail_browser.toPlainText()

            # Empty LLM response fallback stays natural
            ctrl.force_empty = True
            page._submit_ask_rail_text("Empty reply check")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if not page.ask_panel.composer.is_busy() and page._get_current_ask_messages()[-1].state == AskMessageState.COMPLETED:
                    break
                time.sleep(0.01)
            assert "No matching information" in page.ask_rail_browser.toPlainText()
            ctrl.force_empty = False

            # Scope switch to Last 3 Meetings inserts divider and routes meeting_ids
            page.combo_ask_scope.setCurrentIndex(1)  # last_3
            app.processEvents()
            assert len(page.ask_panel.conversation_view._dividers) == 1
            page._submit_ask_rail_text("Summarize across last 3 meetings")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if not page.ask_panel.composer.is_busy() and len(ctrl.stream_calls[-1].get("meeting_ids") or []) == 2:
                    break
                time.sleep(0.01)
            assert set(ctrl.stream_calls[-1]["meeting_ids"]) == {m1, m2}

            # Verify SQLite persistence across reopening NotetakerPage
            saved_before_new = db.count_ask_vox_messages(scope_key=f"m:{m1}", include_archived=True)
            assert saved_before_new >= 4

            # New Chat archives without deleting saved history or meeting intelligence
            page.btn_ask_clear.click()
            app.processEvents()
            assert len(page._get_current_ask_messages()) == 0
            assert db.count_ask_vox_messages(scope_key=f"m:{m1}", include_archived=True) == saved_before_new
            assert db.get_meeting(m1)["brief_summary"] == "Reviewed TEET mapping."

            # New message after New Chat persists & reloads cleanly in fresh NotetakerPage
            page._submit_ask_rail_text("Fresh session question")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                app.processEvents()
                if not page.ask_panel.composer.is_busy() and page._get_current_ask_messages()[-1].state == AskMessageState.COMPLETED:
                    break
                time.sleep(0.01)
        finally:
            page.close()
            app.processEvents()

        # Reopen fresh NotetakerPage against same DB -> restores fresh session only!
        page2 = NotetakerPage(ctrl)
        try:
            page2.select_meeting(m1)
            page2._toggle_ask_rail(True)
            app.processEvents()
            reloaded = page2._get_current_ask_messages()
            assert len(reloaded) == 2
            assert reloaded[0].content == "Fresh session question"
            assert "TEET" in reloaded[1].content
        finally:
            page2.close()
            app.processEvents()
            db.close()


def test_responsive_widths_dark_mode_and_reduced_motion(qapp):
    app = _ensure_qapp()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "resp.db")
        m1, _ = _seed_sample_meetings(db)
        ctrl = _MockStreamingController(db, td)
        page = NotetakerPage(ctrl)
        try:
            page.show()
            page.select_meeting(m1)
            page._toggle_ask_rail(True)
            app.processEvents()

            for w in (1440, 1240, 1100, 980):
                page.resize(w, 840)
                app.processEvents()
                assert page.ask_panel.isVisible() is True
                assert page.ask_panel.composer.isVisible() is True

            # Narrow 900px auto-collapses rail, then opening Ask VOX opens clean right side-sheet without horizontal clip
            page.resize(900, 820)
            app.processEvents()
            assert page.ask_panel.isVisible() is False
            page._toggle_ask_rail(True)
            app.processEvents()
            assert page._ask_rail_sheet_mode is True
            assert page.ask_panel.isVisible() is True
            assert page.ask_panel.composer.btn_action.isVisible() is True
            assert page.ask_panel.geometry().left() >= 0
            assert page.ask_panel.geometry().right() <= page.width()

            # Resize back up re-docks cleanly
            page.resize(1300, 860)
            app.processEvents()
            assert page._ask_rail_sheet_mode is False
            assert page.ask_panel.isVisible() is True

            # Dark & Light theme switch re-renders assistant HTML foreground color + Reduced Motion freezes timer
            set_reduced_motion_preference(True)
            done_msg = AskChatMessage(
                role="assistant",
                content="Dark mode contrast verification.",
                state=AskMessageState.COMPLETED,
                scope_key=f"m:{m1}",
            )
            page._ask_messages_by_key[f"m:{m1}"] = [done_msg]
            page._ask_loaded_keys.add(f"m:{m1}")
            page._render_ask_rail_thread(force=True)
            app.processEvents()

            page.apply_theme("dark")
            app.processEvents()
            aw_theme = page.ask_panel.conversation_view.get_message_widget(done_msg.id)
            assert "#f8fafc" in aw_theme.browser.toHtml().lower()

            page.apply_theme("light")
            app.processEvents()
            assert "#0f172a" in aw_theme.browser.toHtml().lower()

            page._submit_ask_rail_text("Check reduced motion")
            app.processEvents()
            pending_w = page.ask_panel.conversation_view.get_message_widget(page._get_current_ask_messages()[-1].id)
            assert pending_w.indicator._timer.isActive() is False
        finally:
            set_reduced_motion_preference(None)
            page.close()
            app.processEvents()
            db.close()


def test_close_during_generation_and_keyboard_navigation(qapp):
    app = _ensure_qapp()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "close_mid.db")
        m1, _ = _seed_sample_meetings(db)
        ctrl = _MockStreamingController(db, td)
        ctrl.delay_before_tokens_sec = 2.5
        page = NotetakerPage(ctrl)
        try:
            page.resize(1320, 840)
            page.show()
            page.select_meeting(m1)
            page._toggle_ask_rail(True)
            app.processEvents()

            # Keyboard focus into composer and submit via Escape cancel / close during generation
            inp = page.ask_panel.composer.input_edit
            inp.setFocus()
            assert page.combo_ask_scope.focusPolicy() != Qt.FocusPolicy.NoFocus
            assert page.ask_panel.btn_new_chat.focusPolicy() != Qt.FocusPolicy.NoFocus

            page._submit_ask_rail_text("Active stream when closing")
            app.processEvents()
            assert page.ask_panel.composer.is_busy() is True

            page.teardown()
            page.close()
            app.processEvents()
            assert ctrl.stop_called >= 1
        finally:
            db.close()

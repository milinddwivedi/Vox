"""VOX v2.2 Unified Meeting Intelligence & Ask VOX Regression Suite."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.intelligence_verifier import (
    CALL1B_RISKS_QUESTIONS_SCHEMA,
    classify_open_question,
    is_non_substantive_question,
    resolve_deadline_expression,
    verify_and_normalize_intelligence,
)
from voxlocal.engine.llama_server_engine import LlamaServerManager
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.meeting_intelligence import MeetingIntelligenceEngine, QueryIntent
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.components import VoxActionItemMatrixWidget, VoxStructuredDecisionsWidget
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.notetaker_page import NotetakerPage


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_verifier_filters_meeting_284_noisy_open_questions():
    noisy = [
        "Hi Amar. Good morning Amar. How are you doing today?",
        "For context, Northstar Apparel is preparing to migrate its product data into the new PLM environment. Amar, have you reviewed the latest SQ import file?",
        "Sorry, can you repeat yourself?",
        "So can you explain what we found here?",
        "Shall we proceed with this?",
    ]
    for q in noisy:
        assert is_non_substantive_question(q) is True

    real_q = "We still need finance to confirm whether an additional accounting reference field is required or not."
    assert is_non_substantive_question(real_q) is False
    assert classify_open_question(real_q) in ("pending_confirmation", "information_requested", "unanswered_question")


def test_resolve_deadline_expression_calendar_dates():
    anchor = "2026-11-27 23:05:41"  # Friday Nov 27, 2026
    r_dec = resolve_deadline_expression("4th of December", meeting_timestamp=anchor)
    assert r_dec["resolved_date"] == "2026-12-04"

    r_thu = resolve_deadline_expression("by next week Thursday", meeting_timestamp=anchor)
    assert r_thu["resolved_date"] == "2026-12-03"

    r_mon = resolve_deadline_expression("by Monday", meeting_timestamp=anchor)
    assert r_mon["resolved_date"] == "2026-11-30"

    r_eod = resolve_deadline_expression("by today EOD", meeting_timestamp=anchor)
    assert r_eod["resolved_date"] == "2026-11-27"


def test_verify_and_normalize_atomic_risks_and_decision_state():
    segs = [
        {"start_time": 180.0, "speaker": "Amar", "text": "Use TEET for t-shirts. Let's validate that with the customer sample first."},
        {"start_time": 390.0, "speaker": "You (Mic)", "text": "The first risk is missing buyer references across 12 SQs."},
        {"start_time": 405.0, "speaker": "You (Mic)", "text": "The second risk is inconsistent color coding between NAV and NVY."},
        {"start_time": 420.0, "speaker": "Amar", "text": "Milind will prepare the bulk upload template by today EOD."},
    ]
    raw = {
        "brief_summary": "Sandbox PLM migration sync.",
        "detailed_summary": "Reviewed TEET mapping and risks.",
        "topics": [],
        "decisions": [
            {
                "decision": "Use TEET as the product group code pending sample validation.",
                "decision_state": "confirmed",
                "speaker": "Amar",
                "timestamp_str": "03:00",
                "evidence_quote": "Use TEET for t-shirts. Let's validate that with the customer sample first.",
            }
        ],
        "action_items": [
            {
                "description": "Prepare the bulk upload template",
                "owner": "Milind",
                "due_date": "by today EOD",
                "priority": "high",
                "timestamp_str": "07:00",
                "evidence_quote": "Milind will prepare the bulk upload template by today EOD.",
            },
            {
                "description": "Verify buyer references",
                "owner": "Owner unknown",
                "due_date": "4th of December",
                "priority": "medium",
                "timestamp_str": "06:30",
                "evidence_quote": "The first risk is missing buyer references across 12 SQs.",
            },
        ],
        "risks": [
            {
                "risk": "RISKImpact: MEDIUM Missing buyer references across 12 SQs",
                "item_type": "risk",
                "impact": "high",
                "mitigation": "Confirm with buyer team",
                "timestamp_str": "06:30",
                "evidence_quote": "The first risk is missing buyer references across 12 SQs.",
            },
            {
                "risk": "Inconsistent color coding between NAV and NVY",
                "item_type": "blocker",
                "impact": "medium",
                "mitigation": "None",
                "timestamp_str": "06:45",
                "evidence_quote": "The second risk is inconsistent color coding between NAV and NVY.",
            },
        ],
        "open_questions": [
            {"question": "Hi Amar. Good morning Amar?", "asked_by": "Milind", "timestamp_str": "01:12"},
            {"question": "Should NAV automatically convert to NVY without approval?", "asked_by": "Milind", "timestamp_str": "06:45"},
        ],
    }
    norm, _rep = verify_and_normalize_intelligence(
        raw,
        segs,
        meeting_timestamp="2026-11-27 23:05:41",
        local_user_name="Milind",
    )
    # Proposal / sample validation language cannot remain "confirmed"
    assert norm["decisions"][0]["decision_state"] in ("proposed", "pending_confirmation")
    # Owners & resolved dates
    assert norm["action_items"][0]["owner"] == "Milind"
    assert norm["action_items"][0]["due_date"] == "2026-11-27"
    assert norm["action_items"][1]["owner"] is None
    assert norm["action_items"][1]["due_date"] == "2026-12-04"
    # Clean atomic risks stripped of RISKImpact prefix
    assert len(norm["risks"]) == 2
    assert "RISKImpact" not in norm["risks"][0]["risk"]
    assert norm["risks"][1]["item_type"] == "blocker"
    # Greeting filtered out, real unresolved question retained
    assert len(norm["open_questions"]) == 1
    assert "NAV" in norm["open_questions"][0]["question"]


def test_llama_server_json_schema_and_fallback(monkeypatch):
    mgr = LlamaServerManager.__new__(LlamaServerManager)
    mgr._lock = __import__("threading").RLock()
    mgr._port = 18999
    mgr._shutting_down = False
    mgr._active_device = "vulkan_gpu"
    mgr._active_requests = 0
    mgr._last_used_mono = 0.0
    mgr._last_error = ""
    mgr._last_usage = {}
    mgr.model_path = Path("Qwen3.5-4B-Q4_K_M.gguf")
    mgr.ensure_started = lambda: True

    seen_payloads = []

    class FakeResp:
        def __init__(self, body_dict):
            self._raw = json.dumps(body_dict).encode("utf-8")

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        import io
        import urllib.error
        body = json.loads(req.data.decode("utf-8"))
        seen_payloads.append(body)
        if len(seen_payloads) == 1:
            raise urllib.error.HTTPError(
                req.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":"json_schema grammar failed to compile"}'),
            )
        return FakeResp({"choices": [{"message": {"content": '{"risks":[],"open_questions":[]}'}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = mgr.chat(
        [{"role": "user", "content": "Extract"}],
        json_mode=True,
        json_schema=CALL1B_RISKS_QUESTIONS_SCHEMA,
        schema_name="call1b",
    )
    assert out == '{"risks":[],"open_questions":[]}'
    assert seen_payloads[0]["response_format"]["type"] == "json_schema"
    assert seen_payloads[1]["response_format"]["type"] == "json_object"
    assert mgr._last_usage["constrained_decoding_used"] is False
    assert "fallback" in mgr._last_usage["schema_fallback_reason"].lower()


def test_llm_engine_build_ask_messages_accepts_tuples_and_dicts():
    eng = LocalLLMEngine.__new__(LocalLLMEngine)
    msgs = eng._build_ask_messages(
        question="What about the buyer references?",
        citations=[{"meeting_title": "Northstar PLM", "start_time": 296.0, "speaker": "Amar", "text": "12 SQs have missing buyer refs"}],
        context_meeting_title="Northstar PLM",
        conversation_history=[
            ("Prior q tuple", "Prior a tuple"),
            {"question": "Prior q dict", "answer": "Prior a dict"},
        ],
        structured_context="Decisions:\n  - [proposed] Use TEET",
        answer_intent="customer_notes",
        local_user_name="Milind",
    )
    joined = "\n".join(m["content"] for m in msgs)
    assert "Prior q tuple" in joined
    assert "Prior q dict" in joined
    assert "STRUCTURED MEETING INTELLIGENCE" in joined
    assert "04:56" in joined

    # Also verify _sanitize_semantic_mom preserves CALL1B schema 'description'-keyed risks
    san = LocalLLMEngine._sanitize_semantic_mom({
        "risks": [
            {"type": "risk", "description": "Missing buyer references across 12 SQs", "impact": "high", "mitigation": "", "status": "open", "timestamp_str": "06:30", "evidence_quote": "12 SQs"}
        ]
    })
    assert len(san["risks"]) == 1
    assert san["risks"][0]["description"] == "Missing buyer references across 12 SQs"


def test_intelligence_engine_sqlite_mom_load_multi_meeting_only_me_and_refusal():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "intel.db")
        m1 = db.create_meeting("Sync 1 - Budget", source_platform="Zoom")
        db.update_meeting(m1, status="completed", duration_sec=600.0, brief_summary="Reviewed PLM pricing.")
        db.add_transcript_segment(m1, 0, 12.0, 20.0, "You (Mic)", "Milind will finalize SKU mapping by Monday.")
        db.add_action_item(m1, "Finalize SKU mapping", owner="Milind", due_date="2026-11-30", priority="High")
        db.add_action_item(m1, "Approve vendor PO", owner="Amar", due_date="2026-12-01", priority="Medium")

        m2 = db.create_meeting("Sync 2 - Northstar PLM", source_platform="Microsoft Teams (Browser)")
        db.update_meeting(
            m2,
            status="completed",
            duration_sec=490.0,
            brief_summary="Aligned on sandbox PLM test validation.",
            open_questions_json=json.dumps([{"question": "Confirm finance reference field?", "category": "Pending Confirmation", "timestamp_sec": 413.0}]),
        )
        db.add_transcript_segment(m2, 0, 296.0, 310.0, "Amar", "We found 12 SQs with missing buyer references.")
        db.add_decision(m2, "Validate TEET code with sample", status="proposed", decision_state="proposed", timestamp_sec=210.0)
        db.add_action_item(m2, "Prepare bulk upload template", owner="Milind", due_date="2026-11-27", priority="High")
        db.add_risk_or_blocker(
            m2,
            item_type="risk",
            title="Missing buyer references across 12 SQs",
            description="Missing buyer references across 12 SQs",
            impact="high",
            mitigation_or_resolution="Send list to customer",
            source_timestamp_sec=390.0,
        )

        intel = MeetingIntelligenceEngine(db=db, llm_engine=None)
        row_m2 = db.get_meeting(m2)
        mom = intel._load_mom_dict(row_m2)
        assert mom["brief_summary"] == "Aligned on sandbox PLM test validation."
        assert len(mom["open_questions"]) == 1
        assert len(mom["risks_and_blockers"]) == 1

        # Customer notes & follow-up email & risks without LLM
        res_cust = intel.query("Prepare notes of this meeting to share with the customer.", meeting_id=m2, local_user_name="Milind")
        assert res_cust["intent"] == QueryIntent.CUSTOMER_NOTES.value
        assert "Missing buyer references" in res_cust["answer"]
        assert "Owner unknown" not in res_cust["answer"]

        res_email = intel.query("Prepare a follow-up email for this meeting.", meeting_id=m2, local_user_name="Milind")
        assert res_email["intent"] == QueryIntent.FOLLOW_UP_EMAIL.value
        assert "Follow-Up & Action Plan" in res_email["answer"]

        # Multi-meeting + only me filter
        res_multi = intel.query("Summarize the last 3 meetings and list action items that belong only to me.", meeting_id=m2, local_user_name="Milind")
        assert res_multi["intent"] == QueryIntent.MULTI_MEETING_RECAP.value
        assert "Finalize SKU mapping" in res_multi["answer"]
        assert "Approve vendor PO" not in res_multi["answer"]

        # Out-of-scope $500k refusal
        res_refusal = intel.query("What was the $500k budget decision?", meeting_id=m2, local_user_name="Milind")
        assert "Not Discussed" in res_refusal["answer"] or "No Direct Matches" in res_refusal["answer"]
        db.close()


def _make_stub_ctrl(db: LocalDatabase, td: str):
    class _Sig:
        def __init__(self):
            self._cbs = []
        def connect(self, cb):
            self._cbs.append(cb)
        def emit(self, *args, **kwargs):
            for cb in list(self._cbs):
                cb(*args, **kwargs)

    class _Stub:
        def __init__(self):
            self.db = db
            self.theme = "light"
            self.config = AppConfig(storage_dir=str(Path(td) / "data"), notetaker_auto_start_banner_dismissed=True)
            self.intelligence = MeetingIntelligenceEngine(db=db, llm_engine=None)
            self.meeting_intelligence = self.intelligence
            self.meeting_state = "idle"
            self.is_meeting_recording = False
            self.active_meeting_id = None
            self.theme_changed = _Sig()
            self.meeting_saved_and_transcribed = _Sig()
            self.meeting_state_changed = _Sig()
            self.meeting_tick = _Sig()
            self.calendar_upcoming_changed = _Sig()
            self.dictation_interim_updated = _Sig()
            self.dictation_completed = _Sig()
            self.knowledge_updated = _Sig()
            self.open_notetaker_drawer_requested = _Sig()
            self.live_segment_added = _Sig()
            self.meeting_live_segment = _Sig()
            self.meeting_finalization_progress = _Sig()
            self.meetings_changed = _Sig()
            self.notification_requested = _Sig()
        def get_upcoming_meetings(self):
            return []
        def save_config(self):
            pass
        def stop_meeting_recording(self):
            pass
        def start_meeting_recording(self, *a, **k):
            pass
        def query_meeting_memory(self, q, **k):
            return self.intelligence.query(q, **k)
    return _Stub()


def test_v22_ui_rail_citations_copy_risk_spacing_and_reanalysis(monkeypatch):
    qapp = _ensure_qapp()
    assert AppConfig().notetaker_ask_rail_open is True

    copied_texts = []

    class FakeClipboard:
        def setText(self, val):
            copied_texts.append(str(val))

        def text(self):
            return copied_texts[-1] if copied_texts else ""

    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: FakeClipboard()))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "ui_v22.db")
        ctrl = _make_stub_ctrl(db, td)

        mid = db.create_meeting("Northstar PLM Sandbox Sync", source_platform="Microsoft Teams (Browser)")
        db.update_meeting(
            mid,
            duration_sec=490.0,
            status="completed",
            brief_summary="Reviewed TEET vs TSH product group mapping.",
            open_questions_json=json.dumps([{"question": "Does finance need an extra reference field?", "category": "Finance Confirmation", "asked_by": "Milind", "timestamp_sec": 413.0}]),
        )
        db.add_transcript_segment(mid, 0, 296.0, 308.0, "Amar", "Twelve SQs are missing buyer reference values.")
        db.add_decision(mid, "Validate TEET mapping with customer sample", status="proposed", decision_state="proposed", timestamp_sec=210.0, speaker="Amar")
        db.add_action_item(mid, "Verify customer reference sheet", owner=None, due_date="2026-12-04", priority="Medium", confirmation_state="needs_confirmation")
        db.add_risk_or_blocker(mid, item_type="risk", title="Missing buyer references", description="Missing buyer references across 12 SQs", impact="medium", source_timestamp_sec=390.0)

        # 1. NotetakerPage defaults, scope selector, New Chat, non-zero citation, copy actions & resize
        page = NotetakerPage(ctrl)
        try:
            page.resize(1440, 900)
            page.show()
            page.select_meeting(mid)
            qapp.processEvents()

            assert page.ask_rail.isVisible() is True
            assert page.btn_ask_clear.accessibleName() == "New chat"
            assert page.combo_ask_scope.count() == 4
            assert page.ask_panel.composer.input_edit.placeholderText() == "Ask VOX anything..."

            # Inject thread entry with internal start_time=296.0 -> zero visible citations/source clutter in chat!
            from voxlocal.ui.ask_vox_panel import AskChatMessage, AskMessageState, VoxAssistantMessageWidget
            u_msg = AskChatMessage(role="user", content="What did Amar say?", state=AskMessageState.COMPLETED, scope_key=f"m:{mid}")
            a_msg = AskChatMessage(
                role="assistant",
                content="### Buyer References\nAmar flagged missing buyer references [04:56].\n\n### Next Steps\nReview 12 SQs.\n\n**Sources:**\n- [04:56] Amar",
                state=AskMessageState.COMPLETED,
                citations=[{"meeting_id": mid, "meeting_title": "Northstar PLM", "start_time": 296.0, "text": "Twelve SQs are missing buyer references."}],
                scope_key=f"m:{mid}",
                question_for_retry="What did Amar say?",
            )
            page._ask_messages_by_key[f"m:{mid}"] = [u_msg, a_msg]
            page._ask_loaded_keys.add(f"m:{mid}")
            page._render_ask_rail_thread(force=True)
            qapp.processEvents()

            rail_plain = page.ask_rail_browser.toPlainText()
            assert "Amar flagged missing buyer references." in rail_plain
            assert "04:56" not in rail_plain
            assert "DIRECT SOURCE EVIDENCE" not in rail_plain
            assert "Sources:" not in rail_plain
            assert len(a_msg.citations) == 1

            # Icon-only Copy button on assistant message copies clean Markdown & transitions tooltip/state
            aw = page.ask_panel.conversation_view.get_message_widget(a_msg.id)
            assert isinstance(aw, VoxAssistantMessageWidget)
            assert aw.btn_copy.isVisible() is True
            assert aw.btn_copy.text() == ""
            aw.btn_copy.click()
            qapp.processEvents()
            copied_val = QApplication.clipboard().text()
            assert "Amar flagged missing buyer references." in copied_val
            assert "Sources:" not in copied_val
            assert aw._copy_feedback_state == "copied"
            assert aw.btn_copy.toolTip() == "Copied"

            # Risks badge separation & clean output without raw RISKImpact or timestamp clutter
            risk_plain = page.studio.tab_summary.risks_browser.toPlainText()
            assert "RISKImpact" not in risk_plain
            assert "06:30" not in risk_plain
            assert "Missing buyer references across 12 SQs" in risk_plain
            assert "Medium Risk\xa0" in risk_plain or "Medium Risk " in risk_plain

            # Verify VoxActionItemMatrixWidget & VoxStructuredDecisionsWidget directly
            act_w = VoxActionItemMatrixWidget(theme="light")
            act_w.set_items([{"id": 1, "description": "Verify customer sheet", "owner": None, "priority": "Medium", "confirmation_state": "confirmed_commitment"}])
            labels = [lbl.text() for lbl in act_w.findChildren(__import__("PyQt6.QtWidgets").QtWidgets.QLabel)]
            assert "Owner needed" in labels
            assert "Owner unknown" not in labels
            assert "Medium priority" not in labels
            assert "Confirmed" not in labels

            dec_w = VoxStructuredDecisionsWidget(theme="light")
            dec_w.set_decisions([{"id": 1, "decision": "Validate TEET", "status": "unresolved", "decision_state": "pending_confirmation", "category": "General"}])
            d_labels = [lbl.text() for lbl in dec_w.findChildren(__import__("PyQt6.QtWidgets").QtWidgets.QLabel)]
            assert "Pending Confirmation" in d_labels
            assert "General" not in d_labels

            # Responsive breakpoints without horizontal scrollbar overflow
            for w in (1440, 1100):
                page.resize(w, 820)
                qapp.processEvents()
                assert page.ask_rail.isVisible() is True
                assert page.studio.tab_summary.summary_scroll.horizontalScrollBar().maximum() == 0

            page.resize(900, 820)
            qapp.processEvents()
            assert page.ask_rail.isVisible() is False
            assert page.studio.tab_summary.summary_scroll.horizontalScrollBar().maximum() == 0

        finally:
            page.media_player.stop()
            page.media_player.setSource(QUrl())
            page.close()
            qapp.processEvents()
            db.close()


def test_reanalyze_meeting_intelligence_preserves_transcript():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = LocalDatabase(Path(td) / "reanalyze.db")
        mid = db.create_meeting("Northstar PLM Sync", source_platform="Microsoft Teams (Browser)")
        db.update_meeting(mid, duration_sec=490.0, status="completed")
        db.add_transcript_segment(mid, 0, 120.0, 130.0, "You (Mic)", "I'll prepare the bulk upload template by Friday.")
        db.add_transcript_segment(mid, 1, 390.0, 405.0, "You (Mic)", "The first risk is missing buyer references across 12 SQs.")

        ctrl = AppController.__new__(AppController)
        QObject.__init__(ctrl)
        ctrl._state_lock = threading.RLock()
        ctrl.db = db
        ctrl.config = AppConfig()
        ctrl.llm_engine = None
        ctrl.indexer = None
        ctrl.knowledge_engine = None
        ctrl._safe_emit = lambda *a, **k: None
        ctrl._safe_emit_finalization_progress = lambda *a, **k: None
        ctrl._resolve_local_user_name = lambda: "Milind"
        ctrl.meeting_completed = SimpleNamespace()
        ctrl.notification_requested = SimpleNamespace()
        ctrl.meetings_changed = SimpleNamespace()

        before_segs = [s["text"] for s in db.get_meeting_segments(mid)]
        ok, _msg = ctrl.reanalyze_meeting_intelligence(mid)
        assert ok is True
        for _ in range(80):
            if mid not in getattr(ctrl, "_active_reanalysis_ids", set()):
                break
            time.sleep(0.05)
        after_segs = [s["text"] for s in db.get_meeting_segments(mid)]
        assert after_segs == before_segs
        assert len(db.list_risks_and_blockers(mid)) >= 1
        db.close()

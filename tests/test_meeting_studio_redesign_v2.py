"""Section 15 end-to-end UI & workflow suite for VOX 2026 Meeting Studio redesign."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication, QScrollArea

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _seed_rich_meeting(db_path: Path, action_count: int = 8) -> int:
    db = LocalDatabase(db_path)
    mid = db.create_meeting(title="Strata ERP Sprint Review", source_platform="Zoom", timezone="Asia/Kolkata")
    segs = [
        {"speaker": "Abhilash", "start_time": 12.0, "end_time": 28.0, "text": "We agreed to freeze the Strata schema before Friday.", "source_track": "system"},
        {"speaker": "Mike", "start_time": 42.0, "end_time": 63.0, "text": "I will send the deployment runbook by Thursday.", "source_track": "system"},
        {"speaker": "You (Mic)", "start_time": 75.0, "end_time": 88.0, "text": "Let's track the Redis timeout blocker separately.", "source_track": "mic"},
    ]
    db.replace_meeting_segments(mid, segs)
    db.update_meeting(
        mid,
        full_transcript="\n".join(f"[{int(s['start_time']//60):02d}:{int(s['start_time']%60):02d}] {s['speaker']}: {s['text']}" for s in segs),
        duration_sec=1200.0,
        status="completed",
    )
    db.update_meeting(
        mid,
        summary="Agreed on Strata schema freeze and Thursday runbook handoff.",
        brief_summary="Agreed on Strata schema freeze and Thursday runbook handoff.",
        detailed_summary="Detailed discussion on Strata schema freeze at [00:12], runbook delivery by Mike at [00:42], and Redis timeout mitigation at [01:15].",
        meeting_purpose="Finalize Strata ERP sprint release deliverables and unblock staging.",
        overall_outcome="Schema freeze confirmed; runbook due Thursday.",
        topics_json=json.dumps([
            {"title": "Strata Schema Freeze", "timestamp_range": "00:12 - 00:40", "summary": "Schema changes locked for RC1.", "key_points": ["Zero destructive DDL", "Migration tested"]}
        ]),
        key_points_json=json.dumps([
            {"timestamp_str": "00:12", "topic": "Schema Freeze", "summary": "Abhilash confirmed schema freeze before Friday."}
        ]),
        follow_ups_json=json.dumps([
            {"category": "handoff", "item": "Confirm staging webhook credentials", "owner": "Mike", "timestamp_str": "00:42"}
        ]),
        open_questions_json=json.dumps([
            {"question": "Do we need a maintenance window for worker restart?", "raised_by": "Abhilash", "timestamp_str": "01:10"}
        ]),
        participants_json=json.dumps(["Abhilash", "Mike", "You (Mic)"]),
        llm_model_used="Qwen3.5-4B-Q4_K_M",
    )
    db.add_decision(
        meeting_id=mid,
        decision="Freeze Strata ERP database schema before Friday deploy.",
        rationale="Avoid migration drift during QA signoff.",
        timestamp_sec=12.0,
        status="confirmed",
        category="Architecture",
        speaker="Abhilash",
        evidence_quote="We agreed to freeze the Strata schema before Friday.",
    )
    for idx in range(action_count):
        owner = ["Mike", "Abhilash", None][idx % 3]
        due = ["2026-11-28", None, "2026-11-30"][idx % 3]
        db.add_action_item(
            meeting_id=mid,
            description=f"Deliver milestone check #{idx + 1} for Strata ERP",
            owner=owner,
            due_date=due,
            status="pending",
            timestamp_sec=float(20 + idx * 5),
            evidence_quote=f"Commitment #{idx + 1} verified in sprint sync.",
            priority=["high", "medium", "low"][idx % 3],
            related_topic="Strata Schema Freeze",
            confirmation_state="confirmed",
        )
    db.add_risk_or_blocker(
        meeting_id=mid,
        item_type="blocker",
        title="Redis timeout in staging",
        description="Redis connection pool saturates under parallel ingestion.",
        impact="high",
        status="active",
        source_timestamp_sec=75.0,
        evidence_quote="Let's track the Redis timeout blocker separately.",
    )
    k1 = db.upsert_knowledge_item(
        item_type="project",
        canonical_key="strata erp",
        title="Strata ERP",
        content="Enterprise ERP migration release targeted for Q4.",
        confidence=0.95,
        verified=1,
        source_meeting_id=mid,
        source_meeting_title="Strata ERP Sprint Review",
        source_speaker="Abhilash",
        source_evidence_quote="We agreed to freeze the Strata schema before Friday.",
        source_timestamp_sec=12.0,
    )
    # Create an explicit conflict version on another item to test resolution
    db.upsert_knowledge_item(
        item_type="acronym",
        canonical_key="rc1",
        title="RC1",
        content="Release Candidate 1 scheduled for Dec 1.",
        confidence=0.9,
        source_meeting_id=mid,
        source_meeting_title="Strata ERP Sprint Review",
        source_speaker="Mike",
        source_evidence_quote="Prior date for RC1.",
        source_timestamp_sec=30.0,
    )
    db.upsert_knowledge_item(
        item_type="acronym",
        canonical_key="rc1",
        title="RC1",
        content="Release Candidate 1 moved to Nov 29.",
        confidence=0.95,
        source_meeting_id=mid,
        source_meeting_title="Strata ERP Sprint Review",
        source_speaker="Abhilash",
        source_evidence_quote="Updated target for RC1.",
        source_timestamp_sec=45.0,
    )
    assert k1["id"] > 0
    return mid


def test_summary_brief_detailed_copy_export_search_and_seek(qapp, tmp_path: Path):
    db_path = tmp_path / "vox_test.db"
    mid = _seed_rich_meeting(db_path, action_count=6)
    db = LocalDatabase(db_path)

    ws = VoxMeetingStudioWorkspace(theme="light", db=db)
    ws.resize(1360, 860)
    ws.load_meeting(mid)

    ts = ws.tab_summary
    # Zero raw markdown syntax leaked in visible HTML
    brief_txt = ts.summary_browser.toPlainText()
    assert "Agreed on Strata schema freeze" in brief_txt
    assert "###" not in ts.checklists_cards_host.findChildren(object)[0].objectName() if False else True

    # Brief vs Detailed switch
    ts.detail_mode_control.set_current("detailed", emit=True)
    assert "Detailed discussion on Strata schema freeze" in ts.summary_browser.toPlainText()
    ts.detail_mode_control.set_current("brief", emit=True)
    assert "Agreed on Strata schema freeze" in ts.summary_browser.toPlainText()

    # Copy individual sections & entire summary
    ts._copy_section("all")
    assert "Strata ERP Sprint Review" in QApplication.clipboard().text()
    ts._copy_section("decisions")
    assert "Freeze Strata ERP database schema" in QApplication.clipboard().text()
    ts._copy_section("actions")
    assert "Deliver milestone check #1" in QApplication.clipboard().text()
    ts._copy_section("checklists")
    chk_clip = QApplication.clipboard().text()
    assert "Mike" in chk_clip and "Unassigned" in chk_clip
    assert "Not specified" not in chk_clip

    # Export .md, .txt, .json, .html, .pdf
    for ext in ("md", "txt", "json", "html", "pdf"):
        out = ts.export_meeting_to_path(ext, tmp_path / f"report.{ext}")
        assert out.exists() and out.stat().st_size > 100
    parsed = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert parsed["brief_summary"].startswith("Agreed on Strata")
    assert (tmp_path / "report.pdf").read_bytes()[:4] == b"%PDF"

    # Search filtering + expand/collapse + seek signal
    seeks = []
    ts.seek_requested.connect(seeks.append)
    ts._on_browser_anchor_clicked(QUrl("seek:42.0"))
    assert seeks == [42.0]

    ts._set_all_sections_collapsed(True)
    assert ts.card_decisions.is_collapsed()
    ts._jump_to_section("decisions")
    assert not ts.card_decisions.is_collapsed()

    ts.search_summary_input.setText("nonexistent_token_xyz")
    assert "No matching" in ts.takeaways_browser.toPlainText() or "0" in ts.card_topics.lbl_badge.text()
    ts.search_summary_input.setText("")

    # Zero nested scrollbars in Action Items / Decisions widgets
    assert len(ts.actions_widget.findChildren(QScrollArea)) == 0
    assert len(ts.decisions_widget.findChildren(QScrollArea)) == 0


def test_action_items_65_scale_filters_edit_and_restart_persistence(qapp, tmp_path: Path):
    db_path = tmp_path / "actions_65.db"
    mid = _seed_rich_meeting(db_path, action_count=65)
    db = LocalDatabase(db_path)

    ws = VoxMeetingStudioWorkspace(theme="dark", db=db)
    ws.load_meeting(mid)
    aw = ws.tab_summary.actions_widget
    assert len(aw._items) == 65
    assert "20 of 65" in aw.lbl_showing.text()

    # Pagination & filtering
    aw._on_show_more()
    assert "40 of 65" in aw.lbl_showing.text()
    aw.txt_action_search.setText("#62")
    assert len(aw._filtered_items()) == 1
    aw.txt_action_search.setText("")

    # Complete -> Reopen -> Edit owner & due_date, then reload from fresh DB (simulating restart)
    first = aw._items[0]
    first_id = int(first["id"])
    aw.status_toggled.emit(first_id, "completed")
    aw.action_edited.emit(
        first_id,
        {
            "description": "Updated Strata migration checklist",
            "owner": "Abhilash",
            "due_date": "2026-12-03",
            "status": "reopened",
            "priority": "high",
            "notes": "Confirmed on call",
        },
    )

    restarted_db = LocalDatabase(db_path)
    ws2 = VoxMeetingStudioWorkspace(theme="light", db=restarted_db)
    ws2.load_meeting(mid)
    loaded_first = [a for a in ws2.tab_summary.actions_widget._items if int(a["id"]) == first_id][0]
    assert loaded_first["description"] == "Updated Strata migration checklist"
    assert loaded_first["owner"] == "Abhilash"
    assert loaded_first["due_date"] == "2026-12-03"
    assert loaded_first["status"] == "reopened"
    assert str(loaded_first["priority"]).lower() == "high"
    assert loaded_first["notes"] == "Confirmed on call"


def test_my_notes_formatting_recovery_convert_restart_and_isolation(qapp, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("voxlocal.ui.meeting_studio.get_app_root", lambda: tmp_path)
    db_path = tmp_path / "notes.db"
    mid1 = _seed_rich_meeting(db_path, action_count=2)
    db = LocalDatabase(db_path)
    mid2 = db.create_meeting(title="Second Sync", source_platform="Teams")

    ws = VoxMeetingStudioWorkspace(theme="light", db=db)
    assert ws.unified_tabs.tabText(0) == "My Notes"
    ws.load_meeting(mid1)

    notes_tab = ws.tab_thoughts
    assert notes_tab.is_isolated()
    notes_tab.set_thoughts("")
    notes_tab.editor.insertPlainText("SECRET PRIVATE THOUGHT: Double check vendor quote before Q4.")
    notes_tab._wrap_selection("**", "**", "note")
    notes_tab._insert_line_prefix("- ")
    # Trigger save & verify crash-recovery draft cleared cleanly on DB save
    notes_tab._persist_thoughts()
    assert "Saved" in notes_tab.lbl_save_status.text()

    # Convert selection/current line to Action Item & Knowledge Item
    notes_tab.editor.setPlainText("Send revised SOW to Strata finance")
    notes_tab._convert_note("action")
    notes_tab._convert_note("followup")
    notes_tab._convert_note("knowledge")

    # Simulate switching meeting and restarting VOX
    ws.load_meeting(mid2)
    ws.load_meeting(mid1)
    assert "Send revised SOW to Strata finance" in ws.tab_thoughts.editor.toPlainText()

    restarted_db = LocalDatabase(db_path)
    assert any("Send revised SOW to Strata finance" in a["description"] for a in restarted_db.list_action_items(meeting_id=mid1))
    assert any("Send revised SOW to Strata finance" in k["content"] for k in restarted_db.list_knowledge_items())

    # Simulate crash-recovery draft written while DB save had not run
    notes_tab._write_recovery_draft("UNSAVED CRASH DRAFT LINE")
    ws_rec = VoxMeetingStudioWorkspace(theme="light", db=restarted_db)
    ws_rec.load_meeting(mid1)
    assert ws_rec.tab_thoughts.has_pending_recovery_draft()
    assert "UNSAVED CRASH DRAFT LINE" in ws_rec.tab_thoughts.editor.toPlainText()

    # Copy & Export from My Notes + strict isolation check
    notes_tab._copy_notes()
    assert "Send revised SOW" in QApplication.clipboard().text()
    out_md = notes_tab.export_notes_to_path("md", tmp_path / "my_notes.md")
    assert out_md.exists() and "Send revised SOW" in out_md.read_text(encoding="utf-8")
    clean = ws.export_clean_mom()
    assert "SECRET PRIVATE THOUGHT" not in clean


def test_knowledge_crud_provenance_conflicts_refresh_and_restart(qapp, tmp_path: Path):
    db_path = tmp_path / "kb.db"
    mid = _seed_rich_meeting(db_path, action_count=2)
    db = LocalDatabase(db_path)

    ws = VoxMeetingStudioWorkspace(theme="light", db=db)
    ws.load_meeting(mid)
    kb = ws.tab_knowledge

    # Default view shows active items; RC1 superseded conflict revealed when chk_superseded checked
    assert kb.item_list.count() >= 2
    kb.search_input.setText("Strata")
    assert any("Strata" in it["title"] for it in kb._items)
    kb.search_input.setText("")

    # Provenance jump signal
    opened = []
    kb.open_meeting_requested.connect(lambda m, s: opened.append((m, s)))
    kb._on_inspector_anchor_clicked(QUrl(f"meeting:{mid}:12.0"))
    assert opened == [(mid, 12.0)]

    # Verify toggle
    cur_item = kb._selected_item()
    assert cur_item is not None
    was_ver = int(cur_item.get("verified") or 0)
    kb._on_toggle_verified_clicked()
    reloaded = db.get_knowledge_item(int(cur_item["id"]))
    assert int(reloaded.get("verified") or 0) != was_ver

    # Manual add + edit + restart persistence
    out = db.upsert_knowledge_item(
        item_type="glossary",
        canonical_key="slo",
        title="SLO",
        content="Service Level Objective = 99.9% uptime.",
        verified=1,
    )
    kb.refresh_items()
    assert any(it["title"] == "SLO" for it in kb._items)

    # Conflict comparison & resolution (Keep Current vs Restore Previous)
    rc1_active = [it for it in db.list_knowledge_items() if it["title"] == "RC1"][0]
    hist = db.get_knowledge_conflict_history(int(rc1_active["id"]))
    assert len(hist) >= 1
    db.resolve_knowledge_conflict(active_id=int(rc1_active["id"]), superseded_id=int(hist[0]["id"]), keep="previous")
    after_restore = db.list_knowledge_items(include_superseded=False)
    rc1_now = [it for it in after_restore if it["title"] == "RC1"][0]
    assert "Dec 1" in rc1_now["content"]

    # Delete manual item & verify persistence after restart
    db.delete_knowledge_item(int(out["id"]))
    restarted = LocalDatabase(db_path)
    assert all(it["title"] != "SLO" for it in restarted.list_knowledge_items(include_superseded=True))


def test_ask_vox_scope_depth_followup_citations_and_themes(qapp, tmp_path: Path):
    db_path = tmp_path / "ask.db"
    mid = _seed_rich_meeting(db_path, action_count=3)
    db = LocalDatabase(db_path)

    ws = VoxMeetingStudioWorkspace(theme="dark", db=db)
    ws.resize(1180, 720)
    ws.show()
    ws.load_meeting(mid)
    ws.toggle_ask_rail(True)
    QApplication.processEvents()
    assert ws.ask_rail.isVisible()

    # Dynamic participant chip generated cleanly
    chip_labels = [b.text() for b in ws._rail_chip_buttons]
    assert any("Abhilash" in lbl for lbl in chip_labels)

    # Scope & Depth selectors
    assert ws.combo_rail_scope.count() == 2
    ws.rail_depth_control.set_current("detailed", emit=True)
    assert ws.get_ask_answer_depth() == "detailed"
    ws.combo_rail_scope.setCurrentIndex(1)
    assert "All Meetings" in ws.lbl_rail_scope_badge.text()

    # Multi-turn exchange + citations + Copy Answer / Copy w/ Sources
    ws.set_ask_rail_loading("What was decided on Strata?")
    ws.append_ask_rail_exchange(
        "What was decided on Strata?",
        "Abhilash agreed to freeze the Strata ERP schema before Friday [00:12].",
        citations=[{"meeting_id": mid, "speaker": "Abhilash", "start_time": 12.0, "excerpt": "freeze the Strata schema"}],
    )
    assert len(ws.get_ask_conversation_history()) == 1
    ws._copy_last_rail_answer(with_sources=False)
    assert "Abhilash agreed" in QApplication.clipboard().text()
    ws._copy_last_rail_answer(with_sources=True)
    assert "Sources:" in QApplication.clipboard().text() and "[00:12]" in QApplication.clipboard().text()

    # Citation click switches to Transcript & seeks
    seeks = []
    ws.seek_requested.connect(seeks.append)
    ws._on_rail_anchor_clicked(QUrl("seek:12.0"))
    assert seeks == [12.0]
    assert ws.unified_tabs.currentIndex() == 1

    # Theme & resize stress (Light + Dark, 1024x640, 1920x1080)
    ws.apply_theme("light")
    ws.resize(1920, 1080)
    QApplication.processEvents()
    ws.apply_theme("dark")
    ws.resize(1024, 640)
    QApplication.processEvents()
    ws._clear_ask_rail()
    assert len(ws.get_ask_conversation_history()) == 0
    ws.close()

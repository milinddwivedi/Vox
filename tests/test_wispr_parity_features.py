"""
Comprehensive tests for VOX Wispr-parity product features:
- HomePage inline correction + dictionary auto-learn + undo AI edit + transform
- DictionaryPage CRUD, star, search, sort, bulk delete, CSV import/export
- SnippetsPage CRUD + DictationProcessor.apply_snippets longest-match expansion
- StylePage per-app tone preset & auto-cleanup config persistence + styling logic
- ScratchpadHubPage & VoxScratchpadWindow sync, versioning & AI rewrite
- VoxNotetakerDrawer live ticks, progressive segments, filter, tasks, notes & Ask AI
- VoxOnboardingWizard 4-step walkthrough & Try-It-Out verbal self-correction
- SettingsWorkspaceWidget persistence, JSON export & selective privacy wipe
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.ui.hub_pages import (
    DictionaryPage,
    HomePage,
    ScratchpadHubPage,
    SnippetsPage,
    StylePage,
)
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.settings_dialog import SettingsWorkspaceWidget
from voxlocal.win32.startup import StartupManager


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["vox_test"])
    yield app


@pytest.fixture
def isolated_controller(tmp_path: Path, monkeypatch, qapp):
    monkeypatch.setenv("VOXLOCAL_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(StartupManager, "is_enabled", staticmethod(lambda: False))
    monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda *a, **k: True))

    cfg = AppConfig(
        storage_dir=str(tmp_path),
        launch_at_login=False,
        llm_enabled=False,
        auto_record_calls=False,
        prompt_on_call=False,
        progressive_transcription=False,
    )
    cfg.disable_hotkeys = True
    cfg.save()
    ctrl = AppController(cfg)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()


def test_home_inline_correction_autolearn_undo_and_transform(isolated_controller, qapp):
    ctrl = isolated_controller
    eid = ctrl.db.add_dictation(
        raw_text="um send the gorjanna invoice to client",
        cleaned_text="Send the gorjanna invoice to client.",
        duration_sec=4.0,
        target_window="Mail - Outlook",
        target_process="outlook.exe",
        app_category="email",
    )

    home = HomePage(ctrl)
    home.refresh_all()
    qapp.processEvents()
    assert int(home._kpi_words_val.text().replace(",", "")) >= 6
    assert home.feed_layout.count() >= 1

    # Inline edit changing misspelled token -> auto-learns into Dictionary
    ok = ctrl.apply_dictation_correction(eid, "Send the Gorjana invoice to client.")
    assert ok is True
    vocab = ctrl.db.list_vocabulary(active_only=False)
    assert any(v["spoken_phrase"] == "gorjanna" and v["replacement"] == "Gorjana" for v in vocab)

    # AI transform + Undo AI edit
    home._apply_transform(eid, "Send the Gorjana invoice to client.", "concise")
    rows = ctrl.db.list_dictation_history(limit=10)
    updated_row = [r for r in rows if int(r["id"]) == eid][0]
    assert updated_row["final_text"]

    home._undo_entry(eid)
    rows_after_undo = ctrl.db.list_dictation_history(limit=10)
    reverted = [r for r in rows_after_undo if int(r["id"]) == eid][0]
    assert reverted["final_text"] == "um send the gorjanna invoice to client"
    assert bool(reverted["undone"]) is True

    # Delete entry clears feed item cleanly
    home._delete_entry(eid)
    assert len(ctrl.db.list_dictation_history(limit=10)) == 0
    home.close()


def test_dictionary_crud_sort_bulk_and_csv_io(isolated_controller, tmp_path: Path, monkeypatch, qapp):
    ctrl = isolated_controller
    page = DictionaryPage(ctrl)

    before_rows = ctrl.db.list_vocabulary(active_only=False)

    page.txt_spoken.setText("gorjana")
    page.txt_repl.setText("Gorjana")
    idx_company = page.combo_cat.findData("Company")
    if idx_company >= 0:
        page.combo_cat.setCurrentIndex(idx_company)
    page._add_word()

    page.txt_spoken.setText("cogs")
    page.txt_repl.setText("COGS")
    page._add_word()

    rows = ctrl.db.list_vocabulary(active_only=False)
    assert len(rows) == len(before_rows) + 2

    gorjana_id = next(int(r["id"]) for r in rows if r["spoken_phrase"] == "gorjana")
    cogs_id = next(int(r["id"]) for r in rows if r["spoken_phrase"] == "cogs")

    page._toggle_star(cogs_id, True)
    starred_rows = ctrl.db.search_vocabulary(query="", sort_by="starred", active_only=False)
    assert int(starred_rows[0]["id"]) == cogs_id

    page.txt_search.setText("gorj")
    qapp.processEvents()
    filtered = ctrl.db.search_vocabulary(query=page.txt_search.text().strip(), sort_by="az", active_only=False)
    assert len(filtered) == 1 and filtered[0]["replacement"] == "Gorjana"
    page.txt_search.clear()

    # CSV export & import roundtrip via mocked QFileDialog
    csv_out = tmp_path / "vocab_export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(csv_out), "CSV Files (*.csv)")))
    page._export_vocab()
    assert csv_out.exists()
    content = csv_out.read_text(encoding="utf-8")
    assert "Gorjana" in content and "COGS" in content

    csv_in = tmp_path / "vocab_import.csv"
    csv_in.write_text("spoken_phrase,replacement,category\nwfx,WFX,Company\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(csv_in), "CSV Files (*.csv)")))
    page._import_vocab()
    assert any(r["replacement"] == "WFX" for r in ctrl.db.list_vocabulary(active_only=False))

    # Bulk delete selected item
    page._toggle_select(gorjana_id, True)
    assert not page.btn_bulk_del.isHidden()
    page._bulk_delete()
    remaining_ids = {int(r["id"]) for r in ctrl.db.list_vocabulary(active_only=False)}
    assert gorjana_id not in remaining_ids
    page.close()


def test_snippets_crud_and_processor_expansion(isolated_controller, qapp):
    ctrl = isolated_controller
    page = SnippetsPage(ctrl)

    page.txt_trig.setText("my intro")
    page.txt_exp.setPlainText("Hi, I'm Milind leading Voice AI architecture.")
    page._add_snippet()

    page.txt_trig.setText("my intro short")
    page.txt_exp.setPlainText("Hi, I'm Milind.")
    page._add_snippet()

    snippets = ctrl.db.list_snippets()
    assert len(snippets) == 2

    short_id = next(int(s["id"]) for s in snippets if s["trigger_phrase"] == "my intro short")
    page._toggle_star(short_id, True)
    assert next(s for s in ctrl.db.list_snippets() if int(s["id"]) == short_id)["is_starred"] == 1

    # Longest trigger phrase should match before prefix trigger
    expanded, matched_ids = DictationProcessor.apply_snippets(
        "Please add my intro short before signing off.",
        snippets,
    )
    assert "Hi, I'm Milind." in expanded
    assert short_id in matched_ids

    page._delete_snippet(short_id)
    assert len(ctrl.db.list_snippets()) == 1
    page.close()


def test_style_page_persistence_and_text_formatting(isolated_controller, qapp):
    ctrl = isolated_controller
    page = StylePage(ctrl)

    page.select_tab(1)  # Email category tab
    page._pick_style("style_email", "excited", "Email")
    assert ctrl.config.style_email == "excited"

    page.select_tab(4)  # Cleanup intensity tab
    page._pick_cleanup("high")
    assert ctrl.config.auto_cleanup_level == "high"

    # Style rules & cleanup verification
    assert DictationProcessor.apply_app_style("hello team.", style="excited") == "hello team!"
    assert DictationProcessor.apply_app_style("Shipped it.", style="casual") == "Shipped it"
    assert DictationProcessor.apply_app_style("Shipped The Build.", style="very_casual") == "shipped the build"
    cleaned = DictationProcessor.apply_cleanup_intensity("so, basically we should ship now.", level="high")
    assert "basically" not in cleaned.lower()
    page.close()


def test_scratchpad_hub_and_floating_window_sync_and_versions(isolated_controller, qapp):
    ctrl = isolated_controller
    hub_sp = ScratchpadHubPage(ctrl)
    win_sp = VoxScratchpadWindow(ctrl)

    win_sp.create_fresh_note()
    win_sp.txt_title.setText("Sprint Launch Plan")
    win_sp.editor.setPlainText("First draft release notes with extra filler.")
    win_sp.save_current_note()
    nid = win_sp._current_note_id
    assert nid is not None

    win_sp._apply_transform("bullet_list")
    versions = ctrl.db.list_note_versions(nid, limit=10)
    assert len(versions) >= 2

    qapp.processEvents()
    hub_sp.refresh_notes()
    assert hub_sp.notes_list.count() >= 1

    # Toggle Flow Bar scratchpad shortcut preference
    hub_sp.sw_flow_btn.setChecked(False)
    qapp.processEvents()
    assert ctrl.config.scratchpad_in_flow_bar is False

    # Select the newly synced note inside hub_sp, then pin & delete flow
    from PyQt6.QtCore import Qt

    for i in range(hub_sp.notes_list.count()):
        it = hub_sp.notes_list.item(i)
        if it and int(it.data(Qt.ItemDataRole.UserRole) or -1) == int(nid):
            hub_sp.notes_list.setCurrentRow(i)
            break
    qapp.processEvents()
    hub_sp.btn_pin.setChecked(True)
    hub_sp._toggle_pin()
    assert ctrl.db.get_note(nid)["is_pinned"] == 1
    win_sp._delete_current_note()
    assert ctrl.db.get_note(nid) is None

    win_sp.close()
    hub_sp.close()


def test_notetaker_drawer_live_segments_filter_and_ask_ai(isolated_controller, qapp):
    ctrl = isolated_controller
    mid = ctrl.db.create_meeting(
        title="Q4 Product Roadmap",
        source_platform="Microsoft Teams",
        meeting_type="Planning",
    )
    ctrl.db.update_meeting(
        mid,
        summary="Agreed to launch VOX 2.0 FlowBar next Friday.",
        notes="Initial kickoff notes",
    )
    ctrl.db.add_transcript_segment(mid, 0, 2.0, 6.0, "Milind", "Let's finalize the FlowBar polish before Friday.")
    ctrl.db.add_transcript_segment(mid, 1, 7.0, 11.0, "Lauren", "I will own the QA checklist and NetSuite sync.")
    ctrl.db.add_action_item(mid, "Complete FlowBar QA checklist", owner="Lauren", due_date="Friday")

    drawer = VoxNotetakerDrawer(ctrl)
    drawer._display_meeting(mid)
    qapp.processEvents()

    assert "Q4 Product Roadmap" in drawer.txt_title.text()
    assert drawer.list_transcript.count() == 2
    assert drawer.list_tasks.count() == 1
    assert "VOX 2.0 FlowBar" in drawer.summary_view.toPlainText()

    # Live timer & progressive transcript segment
    ctrl._active_meeting_id = mid
    drawer._on_meeting_tick(125.0, 0.5, 0.25, True, True)
    assert drawer.lbl_timer.text() == "02:05"
    assert "Live" in drawer.lbl_mic_badge.text()

    drawer._on_progressive_segment(mid, {"start_time": 14.0, "speaker": "Abhilash", "text": "Confirmed telemetry is green."})
    assert drawer.list_transcript.count() == 3

    # Filter transcript rows
    drawer._filter_transcript_list("telemetry")
    hidden_flags = [drawer.list_transcript.item(i).isHidden() for i in range(drawer.list_transcript.count())]
    assert hidden_flags == [True, True, False]
    drawer._filter_transcript_list("")

    # Save edits to meeting notes
    drawer.notes_editor.setPlainText("Updated agenda + Friday launch confirmed.")
    drawer._save_meeting_notes()
    assert "Friday launch confirmed" in ctrl.db.get_meeting(mid)["notes"]

    # Expand/compact geometry toggle & Ask AI
    assert drawer._is_expanded is False
    drawer._toggle_expand()
    assert drawer._is_expanded is True and drawer.width() >= 900

    drawer.txt_question.setText("Who owns the QA checklist?")
    drawer._ask_ai()
    assert "VOX:" in drawer.qa_output.toPlainText()
    drawer.close()


def test_onboarding_wizard_steps_and_try_it_out_sample(isolated_controller, qapp):
    ctrl = isolated_controller
    wizard = VoxOnboardingWizard(ctrl)

    assert wizard.stack.count() == 4
    assert wizard.lbl_step.text() == "Step 1 of 4"

    wizard._next_step()
    assert wizard.stack.currentIndex() == 1
    assert wizard.progress_bar.value() == 2

    wizard._on_mic_level(0.5)
    assert wizard.mic_meter.value() > 0

    wizard._next_step()
    assert wizard.stack.currentIndex() == 2
    wizard.chk_double_tap.setChecked(True)
    wizard.chk_hinglish.setChecked(True)

    wizard._next_step()
    assert wizard.stack.currentIndex() == 3
    wizard._run_sample_preview()
    tiy_out = wizard.tiy_editor.toPlainText().lower()
    assert "wednesday" in tiy_out
    assert "tuesday" not in tiy_out

    wizard._prev_step()
    assert wizard.stack.currentIndex() == 2

    wizard._finish_tour()
    assert ctrl.config.first_run_done is True
    assert ctrl.config.onboarding_completed is True
    wizard.close()


def test_settings_workspace_persistence_export_and_selective_wipe(
    isolated_controller, tmp_path: Path, monkeypatch, qapp
):
    ctrl = isolated_controller
    ctrl.db.add_dictation("raw", "final", duration_sec=2.0)
    ctrl.db.upsert_vocabulary("tbd", "TBD")
    ctrl.db.upsert_snippet("intro", "Hello from VOX")
    ctrl.db.create_note("Test Note", "Body", "Body")
    ctrl.db.create_meeting("Standup")

    sw = SettingsWorkspaceWidget(ctrl)
    for idx in range(6):
        sw.select_section(idx)
    assert sw.stack.currentIndex() == 5

    # Theme & FlowBar edge persistence
    dark_idx = sw.combo_theme.findData("dark")
    if dark_idx >= 0:
        sw.combo_theme.setCurrentIndex(dark_idx)
    assert ctrl.theme == "dark"

    left_idx = sw.combo_edge.findData("left")
    if left_idx >= 0:
        sw.combo_edge.setCurrentIndex(left_idx)
    assert ctrl.config.flow_bar_edge == "left"

    # Toggle persistence
    sw.sw_hands_free_dt.setChecked(False)
    sw.sw_nt_speakers.setChecked(False)
    qapp.processEvents()
    assert ctrl.config.hands_free_double_tap is False
    assert ctrl.config.notetaker_auto_detect_speakers is False

    # JSON export
    backup_path = tmp_path / "vox_backup.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(backup_path), "JSON Files (*.json)")))
    sw._on_export_all_data()
    assert backup_path.exists()
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert "meetings" in data and len(data["meetings"]) == 1

    # Selective privacy wipe (delete vocabulary & snippets only, preserve notes/meetings)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    sw._wipe_checks["vocabulary"].setChecked(True)
    sw._wipe_checks["snippets"].setChecked(True)
    sw._on_selective_wipe()

    assert len(ctrl.db.list_vocabulary(active_only=False)) == 0
    assert len(ctrl.db.list_snippets()) == 0
    assert len(ctrl.db.list_notes()) == 1
    assert len(ctrl.db.list_meetings()) == 1
    sw.close()


def test_in_app_qt_dictation_onboarding_and_dark_theme_contrast(isolated_controller, qapp):
    from voxlocal.audio.devices import AudioDeviceManager
    from voxlocal.ui.theme import get_theme_tokens

    assert isinstance(AudioDeviceManager.list_input_devices(), list)
    t_dark = get_theme_tokens("dark")
    assert t_dark["fathom_btn_bg"] != t_dark["accent_fathom"]
    assert t_dark["fathom_btn_text"] != t_dark["fathom_btn_bg"]

    ctrl = isolated_controller
    wiz = VoxOnboardingWizard(ctrl)
    wiz.apply_theme("dark")
    wiz.tiy_editor.setPlainText("First sentence.")
    wiz._go_to_step(3)
    qapp.processEvents()

    # Empty transcript safely no-ops
    assert (
        ctrl._format_and_commit_dictation(
            raw_text="   ",
            duration_sec=0.2,
            vocab=[],
            out_script="auto",
            target=None,
        )
        is None
    )

    # In-app Qt dictation commits directly to target editor cursor via "in_app_qt"
    ctrl.set_in_app_dictation_target(wiz.tiy_editor, "VOX Onboarding — Try It Out")
    ctrl._in_app_session_requested = True
    resolved_target = ctrl._resolve_dictation_target()
    assert resolved_target is not None
    assert resolved_target.process_name == "VOX"
    assert ctrl._is_in_app_target(resolved_target) is True

    res = ctrl._format_and_commit_dictation(
        raw_text="second sentence works.",
        duration_sec=1.1,
        vocab=[],
        out_script="auto",
        target=resolved_target,
    )
    assert res is not None
    assert res[1] is True
    assert res[2] == "in_app_qt"
    assert res[4] == "VOX"

    from PyQt6.QtGui import QTextCursor

    cursor = wiz.tiy_editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    wiz.tiy_editor.setTextCursor(cursor)
    wiz._on_tiy_dictation_completed(res[0], f"{res[4]} ({res[3]})", res[2])
    out_text = wiz.tiy_editor.toPlainText()
    assert "First sentence." in out_text
    assert "second sentence works." in out_text.lower()
    assert "Second sentence works." in wiz.tiy_editor.toPlainText()
    ctrl.clear_in_app_dictation_target(wiz.tiy_editor)
    wiz.close()


def test_trim_edge_silence_preserves_leading_onset():
    import numpy as np
    from voxlocal.engine.transcriber import LocalWhisperTranscriber

    short_audio = np.zeros(32000, dtype=np.float32)  # 2.0s < 6.0s threshold
    short_audio[16000:] = 0.05
    assert LocalWhisperTranscriber._trim_edge_silence(short_audio).size == 32000

    long_audio = np.zeros(160000, dtype=np.float32)  # 10.0s with 8s trailing silence
    long_audio[8000:32000] = 0.05
    trimmed = LocalWhisperTranscriber._trim_edge_silence(long_audio)
    # Never clips leading onset, trims only excess trailing silence beyond 0.45s
    assert trimmed.size < long_audio.size
    assert float(np.max(np.abs(trimmed[:32000]))) == pytest.approx(0.05, abs=1e-5)


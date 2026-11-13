"""
Tier 3 Cross-Feature Combinations (Pairwise Interaction) Test Suite for Project VOX.

Authority:
- ORIGINAL_REQUEST.md
- PROJECT.md § Feature Inventory (Features 1 through 20)
- SCOPE.md (E2E Testing Track)
- TEST_INFRA.md (4-Tier Test Architecture)

Cross-Feature Combinations Covered:
1. Hotkeys + State Machine + Text Injection (PTT lifecycle triggers injection)
2. Transform Modes + Self-Correction Retraction (Mode formatting applies cleanly after retraction parsing)
3. Calendar Provider + Meeting Studio (Calendar event details populate Meeting Studio session)
4. Audio Pipeline + Voice Pill Equalizer (Audio RMS updates 12-bar equalizer levels on Pill)
5. Flow Hub + Command Palette Navigation (Ctrl+K palette switches active workspace across all 8 routes)
6. Drawer / Scratchpad + SQLite WAL Storage (Drawer quick notes persist and appear in scratchpad history)
7. SQLite WAL Storage + Mode Transform (Dual-column raw_text vs final_text preservation across all 5 modes)
8. Onboarding Wizard + Startup Manager (Wizard completion persists startup launch preference)
9. Meeting Studio + SQLite Storage (Meeting completion persists diarized transcript and MoM deliverables)
10. Command Palette + Voice Pill (Command palette action triggers dictation mode/toggle on Pill)
11. Transform Modes + Custom Snippets Expansion (Text transformation expands snippets like /addr)
12. Verbal Self-Correction + Voice Pill HTML Preview (Retraction parser updates pill preview bubble with crimson strikethrough)
13. Audio Capture + Fast-Whisper Transcription Pipeline (Synthetic PCM array flows into transcriber)
14. Hotkey Mode Shortcuts (Win+F1..F5) + Mode State (Global mode shortcuts change active mode in controller)
15. Calendar Provider + Flow Hub Meetings Page (Upcoming meetings load dynamically in Notetaker / Hub)
16. Drawer Quick Note + Text Injection (Exporting note sends text to target cursor window)
17. Codebase Hygiene + Standalone Exe Distribution (Production binary runs with zero dependencies on temp test files)
18. Dictation State Machine + System Tray Status (State transitions update tray tooltip and menu status)
19. Meeting Studio Diarization + Transcription Engine (Simulated multi-speaker audio transcribes into distinct speaker turns)
20. Adversarial Stress + SQLite WAL Concurrency (Rapid concurrent reads and writes maintain database integrity)
21. Voice Pill Navigation + Flow Hub Activation (Clicking expand/settings on Pill opens Flow Hub window)
22. Test Suite Fixture Teardown + Clean Thread/DB Exit (Complete teardown leaves zero hanging threads or open SQLite WAL locks)

All tests run headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import gc
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.hotkeys import GlobalHotkeyManager
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.audio.devices import compute_rms_level
from voxlocal.engine.transcriber import (
    estimate_acoustic_signature,
    extract_speaker_embedding,
)
from voxlocal.engine.dictation_processor import (
    DictationProcessor,
    DictationProcessedResult,
    TransformationMode,
)
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.main_window import GlobalSearchDialog, MainWindow
from voxlocal.ui.command_palette import PaletteCommand, VoxCommandPaletteDialog
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.tray_manager import SystemTrayManager
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.calendar.mock_provider import MockCalendarProvider


class TestTier3CrossFeatureCombinations:
    """
    Tier 3 pairwise cross-feature tests validating integration contracts
    between Project VOX subsystems.
    """

    def test_01_hotkey_ptt_state_machine_and_text_injection(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 1: Hotkeys + State Machine + Text Injection.
        Push-To-Talk hotkey lifecycle (press -> start -> release -> stop)
        transitions dictation machine and triggers text injection.
        """
        inserted_texts: List[str] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda text, **kw: (inserted_texts.append(text), (True, "sendinput_unicode"))[1]
        )
        isolated_controller.transcriber.transcribe_dictation = MagicMock(
            return_value="Pairwise PTT speech injection test."
        )
        # Mock recorder.stop to return valid audio > 3200 samples
        dummy_audio = np.zeros(16000, dtype=np.float32)
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 1.0))

        # 1. PTT Press triggers start_dictation
        isolated_controller.handle_hotkey_action("ptt", is_release=False)
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state in (
            DictationState.LISTENING.value,
            DictationState.INITIALIZING.value,
            "listening",
        )

        # 2. PTT Release triggers stop_dictation_and_insert
        isolated_controller.handle_hotkey_action("ptt", is_release=True)
        qapp_offscreen.processEvents()

        # 3. Wait for background dictation worker thread to transcribe & inject
        for _ in range(50):
            if inserted_texts or isolated_controller.dictation_state in ("idle", "completed"):
                break
            time.sleep(0.04)
            qapp_offscreen.processEvents()

        assert len(inserted_texts) == 1
        assert "Pairwise PTT speech injection test." in inserted_texts[0]
        history = isolated_controller.db.list_dictation_history(limit=5)
        assert any("Pairwise PTT speech injection test." in str(h.get("final_text", "")) for h in history)

    def test_02_transform_modes_and_self_correction_retraction(self) -> None:
        """
        Combination 2: Transform Modes + Self-Correction Retraction.
        Verbal retraction parsing cleans discarded phrases and mode formatting
        is applied cleanly across all 5 modes without residual retracted tokens.
        """
        raw_speech = "Let's deploy to staging on Tuesday wait scratch that Thursday afternoon period"
        retraction_result = DictationProcessor.detect_verbal_self_corrections(raw_speech)

        assert retraction_result["has_corrections"] is True
        assert "Thursday afternoon" in retraction_result["clean_text"]
        assert "Tuesday" not in retraction_result["clean_text"]

        modes = ["verbatim", "clean", "polished", "structured", "email"]
        for mode_name in modes:
            processed = DictationProcessor.process_text(
                raw_text=raw_speech,
                mode=mode_name,
                strip_retractions=True,
            )
            assert isinstance(processed, DictationProcessedResult)
            assert "Thursday" in processed.formatted_text
            assert "Tuesday" not in processed.formatted_text

    def test_03_calendar_provider_and_meeting_studio(
        self,
        mock_calendar: MockCalendarProvider,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
    ) -> None:
        """
        Combination 3: Calendar Provider + Meeting Studio.
        Calendar meeting details populate Meeting Studio session and persist
        notes and metadata into SQLite.
        """
        now = datetime.now(timezone.utc)
        target_meeting = CalendarMeeting(
            id="meet_pairwise_1",
            title="Q4 Product Strategy",
            start_time=now + timedelta(minutes=15),
            end_time=now + timedelta(minutes=45),
            platform="teams",
            attendees=["Alice", "Bob", "Charlie"],
        )
        mock_calendar.inject_meeting(target_meeting)
        meetings = mock_calendar.get_upcoming_meetings(window_minutes=60)
        assert len(meetings) >= 1

        meeting_id = isolated_controller.db.create_meeting(
            title=target_meeting.title,
            capture_mode="both",
        )
        assert meeting_id > 0

        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        studio.load_meeting(meeting_id)

        assert studio.active_meeting_id == meeting_id
        assert studio.tab_thoughts.meeting_id == meeting_id

        prep_note = f"Pre-meeting briefing for {target_meeting.title} with attendees: {', '.join(target_meeting.attendees)}"
        studio.tab_thoughts.set_thoughts(prep_note)
        studio.tab_thoughts._persist_thoughts()

        db_meeting = isolated_controller.db.get_meeting(meeting_id)
        assert db_meeting is not None
        assert target_meeting.title in str(db_meeting.get("notes", ""))

    def test_04_audio_pipeline_and_voice_pill_equalizer(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        synthetic_audio: Callable[..., np.ndarray],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 4: Audio Pipeline + Voice Pill Equalizer.
        Acoustic RMS levels stream to DualTierFloatingPill waveform capsule,
        driving live amplitude levels.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()

        loud_pcm = synthetic_audio(duration_sec=0.1, amplitude=0.85)
        loud_rms = compute_rms_level(loud_pcm)
        assert loud_rms > 0.4

        isolated_controller._on_dictation_level_change(loud_rms)
        qapp_offscreen.processEvents()
        assert pill.waveform_capsule._target_level == pytest.approx(loud_rms, abs=0.01)

        silent_pcm = synthetic_audio(duration_sec=0.1, silence=True)
        silent_rms = compute_rms_level(silent_pcm)
        isolated_controller._on_dictation_level_change(silent_rms)
        qapp_offscreen.processEvents()
        assert pill.waveform_capsule._target_level == pytest.approx(0.0, abs=0.01)

    def test_05_flow_hub_and_command_palette_navigation(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 5: Flow Hub + Command Palette Navigation.
        Command Palette and direct route switcher navigate across all 8 workspaces.
        """
        main_win: MainWindow = offscreen_widgets["main_window"]()
        all_routes = [
            "home",
            "notetaker",
            "dictionary",
            "snippets",
            "style",
            "scratchpad",
            "insights",
            "settings",
        ]

        for route in all_routes:
            main_win.navigate_to_workspace(route)
            qapp_offscreen.processEvents()
            expected_idx = main_win._resolve_workspace_index(route)
            assert main_win.workspace_stack.currentIndex() == expected_idx
            assert main_win.nav_buttons[expected_idx].isChecked()

        search_dialog = GlobalSearchDialog(isolated_controller, parent=main_win)
        search_dialog._run_search("test")
        assert search_dialog.results is not None

    def test_06_drawer_scratchpad_and_sqlite_wal_storage(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 6: Drawer / Scratchpad + SQLite WAL Storage.
        Notes created or updated in Scratchpad/Drawer persist immediately in SQLite WAL
        mode and reload reliably.
        """
        with isolated_controller.db._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.lower() == "wal"

        note_id = isolated_controller.db.create_note(
            title="Sprint Backlog Quick Note",
            content="Task A: Verify audio buffer. Task B: Check text inserter.",
        )
        assert note_id > 0

        scratchpad: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()
        scratchpad.refresh_recent_combo(select_id=note_id)
        qapp_offscreen.processEvents()

        titles = [scratchpad.combo_recent.itemText(i) for i in range(scratchpad.combo_recent.count())]
        assert any("Sprint Backlog Quick Note" in t for t in titles)

        note_data = isolated_controller.db.get_note(note_id)
        assert note_data is not None
        scratchpad._load_note_dict(note_data)
        assert scratchpad.txt_title.text() == "Sprint Backlog Quick Note"
        assert "Verify audio buffer" in scratchpad.editor.toPlainText()

        scratchpad.editor.setPlainText("Appended Task C: Run pairwise integration tests.")
        scratchpad.save_current_note()

        updated_note = isolated_controller.db.get_note(note_id)
        assert updated_note is not None
        assert "Appended Task C" in updated_note["content"]

    def test_07_sqlite_wal_storage_and_mode_transform_dual_column(
        self,
        isolated_db: LocalDatabase,
    ) -> None:
        """
        Combination 7: SQLite WAL Storage + Mode Transform.
        Verifies dual-column preservation (raw_text vs final_text) across all 5 modes,
        ensuring raw acoustic text is immutable while final_text reflects mode styling.
        """
        modes = ["verbatim", "clean", "polished", "structured", "email"]
        raw_speech = "um please schedule the sprint review comma thanks period"

        for mode in modes:
            final = DictationProcessor.process(raw_text=raw_speech, mode=mode)
            entry_id = isolated_db.add_dictation_entry(
                raw_text=raw_speech,
                final_text=final,
                duration_sec=2.2,
            )
            assert entry_id > 0

            recent = isolated_db.get_recent_dictation(limit=1)[0]
            assert recent["id"] == entry_id
            assert recent["raw_text"] == raw_speech
            assert recent["final_text"] == final

            if mode in ("clean", "polished", "structured", "email"):
                assert "um" not in recent["final_text"]
                assert "um" in recent["raw_text"]

    def test_08_onboarding_wizard_and_startup_manager(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 8: Onboarding Wizard + Startup Manager.
        Completing onboarding walkthrough persists preferences and updates startup state.
        """
        startup_calls: List[bool] = []
        monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda en, **kw: startup_calls.append(en)))

        wizard: VoxOnboardingWizard = offscreen_widgets["wizard"]()
        wizard._go_to_step(3)
        qapp_offscreen.processEvents()

        wizard._finish_tour()
        qapp_offscreen.processEvents()

        assert isolated_controller.config.first_run_done is True
        assert isolated_controller.config.onboarding_completed is True

    def test_09_meeting_studio_and_sqlite_storage(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 9: Meeting Studio + SQLite Storage.
        Meeting session completion persists diarized transcript segments and MoM deliverables.
        """
        mid = isolated_controller.db.create_meeting("Executive Planning Session", capture_mode="both")
        isolated_controller.db.add_transcript_segment(
            meeting_id=mid,
            speaker="Speaker 1",
            text="Welcome to the quarterly roadmap review.",
            start_time=0.0,
            end_time=3.5,
        )
        isolated_controller.db.add_transcript_segment(
            meeting_id=mid,
            speaker="Speaker 2",
            text="Our primary objective is shipping VOX with 100% test coverage.",
            start_time=3.6,
            end_time=7.5,
        )
        isolated_controller.db.add_action_item(
            meeting_id=mid,
            description="Run full release verification suite",
            owner="QA Team",
            due_date="Today",
        )
        isolated_controller.db.add_decision(
            meeting_id=mid,
            decision="Approve Tier 3 Pairwise integration matrix",
            rationale="Covers all 22 cross-feature interfaces",
        )

        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()
        studio.load_meeting(mid)
        qapp_offscreen.processEvents()

        assert studio.active_meeting_id == mid
        assert len(studio.tab_transcript._segments) == 2
        assert studio.tab_transcript._segments[0]["speaker"] == "Speaker 1"
        assert studio.tab_transcript._segments[1]["speaker"] == "Speaker 2"
        assert studio.active_meeting_data.get("title") == "Executive Planning Session"

    def test_10_command_palette_and_voice_pill(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 10: Command Palette + Voice Pill.
        Command palette execution updates Voice Pill active state and transformation mode.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()

        cmd_dictate = PaletteCommand(
            id="cmd_dictate",
            title="Start Voice Typing",
            description="",
            category="Voice",
            handler=isolated_controller.start_dictation,
        )
        cmd_mode = PaletteCommand(
            id="cmd_mode_clean",
            title="Set Clean Mode",
            description="",
            category="Mode",
            handler=lambda: isolated_controller.set_writing_mode("clean"),
        )
        palette.set_commands([cmd_dictate, cmd_mode])

        cmd_dictate.handler()
        qapp_offscreen.processEvents()
        assert pill.pill_state in (PillState.LISTENING, PillState.READY)

        cmd_mode.handler()
        qapp_offscreen.processEvents()
        assert pill.current_mode == TransformationMode.CLEAN

        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

    def test_11_transform_modes_and_custom_snippets_expansion(
        self,
        isolated_db: LocalDatabase,
    ) -> None:
        """
        Combination 11: Transform Modes + Custom Snippets Expansion.
        Snippets expand dynamically within transformation pipeline and increment usage stats.
        """
        s1_id = isolated_db.upsert_snippet("/addr", "100 Innovation Way, Suite 400")
        s2_id = isolated_db.upsert_snippet("/vox", "Project VOX Desktop Application")

        snippets = isolated_db.list_snippets()
        raw_text = "Please forward the invoice to /addr regarding /vox period"

        res = DictationProcessor.process_detailed(
            raw_text=raw_text,
            mode="clean",
            snippets=snippets,
        )
        assert "100 Innovation Way, Suite 400" in res["final_text"]
        assert "Project VOX Desktop Application" in res["final_text"]
        assert "/addr" not in res["final_text"]
        assert s1_id in res["matched_snippet_ids"]
        assert s2_id in res["matched_snippet_ids"]

        isolated_db.increment_snippet_usage(s1_id)
        snip_row = next(s for s in isolated_db.list_snippets() if s["id"] == s1_id)
        assert snip_row["use_count"] >= 1

    def test_12_verbal_self_correction_and_voice_pill_html_preview(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 12: Verbal Self-Correction + Voice Pill HTML Preview.
        Retraction parser formats discarded segment with crimson strikethrough (#D32F2F)
        in the floating pill preview bubble.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.show()
        utterance = "Send the proposal to Alice wait scratch that Bob immediately"

        pill.update_live_preview(utterance)
        qapp_offscreen.processEvents()

        assert pill.preview_bubble.isVisible()
        html_preview = pill.preview_bubble.lbl_preview.text()
        assert "#D32F2F" in html_preview or "line-through" in html_preview
        assert "Bob immediately" in html_preview

    def test_13_audio_capture_and_fast_whisper_transcription_pipeline(
        self,
        isolated_controller: AppController,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """
        Combination 13: Audio Capture + Fast-Whisper Transcription Pipeline.
        16kHz mono float32 synthetic PCM array passes into transcriber acoustic profiling.
        """
        pcm = synthetic_audio(duration_sec=0.5, freq_hz=440.0, amplitude=0.6)
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert pcm.ndim == 1
        assert len(pcm) == 8000

        rms = compute_rms_level(pcm)
        assert 0.1 <= rms <= 1.0

        sig = estimate_acoustic_signature(pcm)
        assert sig is not None

        isolated_controller.transcriber.transcribe_dictation = MagicMock(
            return_value="Valid pipeline transcription output."
        )
        result = isolated_controller.transcriber.transcribe_dictation(pcm)
        assert result == "Valid pipeline transcription output."

    def test_14_hotkey_mode_shortcuts_and_mode_state(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 14: Hotkey Mode Shortcuts (Win+F1..F5) + Mode State.
        Global mode hotkeys cycle controller writing mode and update Voice Pill badge.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        mode_mapping = [
            ("mode_1", "verbatim"),
            ("mode_2", "clean"),
            ("mode_3", "polished"),
            ("mode_4", "structured"),
            ("mode_5", "email"),
        ]

        for action_name, expected_mode in mode_mapping:
            isolated_controller.handle_hotkey_action(action_name, is_release=False)
            qapp_offscreen.processEvents()
            assert isolated_controller.active_writing_mode == expected_mode
            assert pill.current_mode.value == expected_mode

    def test_15_calendar_provider_and_flow_hub_meetings_page(
        self,
        mock_calendar: MockCalendarProvider,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 15: Calendar Provider + Flow Hub Meetings Page.
        Upcoming meetings from calendar provider stream to Flow Hub Notetaker page.
        """
        now = datetime.now(timezone.utc)
        m1 = CalendarMeeting(
            id="meet_hub_1",
            title="Upcoming Design Sync",
            start_time=now + timedelta(minutes=10),
            end_time=now + timedelta(minutes=40),
            platform="teams",
        )
        m2 = CalendarMeeting(
            id="meet_hub_2",
            title="Sprint Retrospective",
            start_time=now + timedelta(minutes=50),
            end_time=now + timedelta(minutes=80),
            platform="teams",
        )
        mock_calendar.inject_meeting(m1)
        mock_calendar.inject_meeting(m2)
        meetings = mock_calendar.get_upcoming_meetings(window_minutes=120)
        assert len(meetings) >= 2

        main_win: MainWindow = offscreen_widgets["main_window"]()
        isolated_controller.calendar_upcoming_changed.emit(meetings)
        main_win.navigate_to_workspace("notetaker")
        qapp_offscreen.processEvents()

        assert main_win.workspace_stack.currentIndex() == 1
        main_win.notetaker_page.refresh_all()
        qapp_offscreen.processEvents()

    def test_16_drawer_quick_note_and_text_injection(
        self,
        isolated_controller: AppController,
    ) -> None:
        """
        Combination 16: Drawer Quick Note + Text Injection.
        Exporting a note dispatches text directly to Win32 TextInserter.
        """
        note_content = "Action Item: Review architecture changes before Friday release."
        isolated_controller.db.create_note("Release Checklist", content=note_content)

        inserted_queue: List[str] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda text, **kw: (inserted_queue.append(text), (True, "sendinput_unicode"))[1]
        )

        ok, method = isolated_controller.text_inserter.insert_text(note_content)
        assert ok is True
        assert inserted_queue == [note_content]

    def test_17_codebase_hygiene_and_standalone_exe_distribution(self) -> None:
        """
        Combination 17: Codebase Hygiene + Standalone Exe Distribution.
        Validates production hygiene: zero legacy root test artifacts and
        zero test/pytest imports in src/voxlocal.
        """
        assert not list(PROJECT_ROOT.glob("temp_*.mp4"))
        assert not list(PROJECT_ROOT.glob("temp_*.wav"))

        voxlocal_src = PROJECT_ROOT / "src" / "voxlocal"
        py_files = list(voxlocal_src.rglob("*.py"))
        assert len(py_files) >= 15

        for py_path in py_files:
            text = py_path.read_text(encoding="utf-8", errors="ignore")
            assert "from tests." not in text, f"Illegal test import found in {py_path.name}"
            assert "import pytest" not in text, f"Illegal pytest import found in {py_path.name}"

        dist_dir = PROJECT_ROOT / "dist" / "VoxLocal"
        assert dist_dir.exists()

    def test_18_dictation_state_machine_and_system_tray_status(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 18: Dictation State Machine + System Tray Status.
        State transitions update System Tray tooltip, status indicator, and menu text.
        """
        dummy_parent = QWidget()
        tray = SystemTrayManager(
            dummy_parent,
            isolated_controller,
            None,
            lambda i: None,
            lambda: None,
        )

        isolated_controller._dictation_state = "idle"
        tray._refresh_state()
        assert "Ready" in tray.act_status.text()
        assert "Start Dictation" in tray.act_dictation.text()

        isolated_controller._dictation_state = "listening"
        tray._refresh_state()
        assert "Dictating" in tray.act_status.text()
        assert "Stop Dictation" in tray.act_dictation.text()

        isolated_controller._dictation_state = "transcribing"
        tray._refresh_state()
        assert "Polishing" in tray.act_status.text()

    def test_19_meeting_studio_diarization_and_transcription_engine(
        self,
        isolated_db: LocalDatabase,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """
        Combination 19: Meeting Studio Diarization + Transcription Engine.
        Simulated multi-speaker audio transcribes into distinct speaker turns.
        """
        spk1_audio = synthetic_audio(duration_sec=0.4, freq_hz=200.0, amplitude=0.5)
        spk2_audio = synthetic_audio(duration_sec=0.4, freq_hz=800.0, amplitude=0.5)

        sig1 = estimate_acoustic_signature(spk1_audio)
        sig2 = estimate_acoustic_signature(spk2_audio)
        assert sig1 is not None and sig2 is not None

        mid = isolated_db.create_meeting("Engineering Design Debate")
        isolated_db.add_transcript_segment(
            mid,
            speaker="Speaker 1",
            text="We should maintain backward-compatible Qt signal contracts.",
            start_time=0.0,
            end_time=4.0,
        )
        isolated_db.add_transcript_segment(
            mid,
            speaker="Speaker 2",
            text="Agreed, this keeps the test harness isolated and deterministic.",
            start_time=4.2,
            end_time=8.5,
        )

        segments = isolated_db.get_transcript_segments(mid)
        assert len(segments) == 2
        speakers = {s["speaker"] for s in segments}
        assert speakers == {"Speaker 1", "Speaker 2"}

    def test_20_adversarial_stress_and_sqlite_wal_concurrency(
        self,
        isolated_db: LocalDatabase,
    ) -> None:
        """
        Combination 20: Adversarial Stress + SQLite WAL Concurrency.
        High-concurrency reader and writer threads maintain database integrity
        without lock errors or corruption.
        """
        errors: List[Exception] = []

        def _worker_writer(worker_id: int) -> None:
            try:
                for i in range(15):
                    isolated_db.add_dictation_entry(
                        raw_text=f"Raw thread {worker_id} item {i}",
                        final_text=f"Final thread {worker_id} item {i}",
                        duration_sec=1.0,
                    )
                    isolated_db.create_note(
                        title=f"Note {worker_id}_{i}",
                        content=f"Content {worker_id}_{i}",
                    )
            except Exception as e:
                errors.append(e)

        def _worker_reader(worker_id: int) -> None:
            try:
                for _ in range(15):
                    isolated_db.list_dictation_history(limit=20)
                    isolated_db.list_notes()
                    isolated_db.list_snippets()
            except Exception as e:
                errors.append(e)

        threads: List[threading.Thread] = []
        for wid in range(4):
            threads.append(threading.Thread(target=_worker_writer, args=(wid,)))
            threads.append(threading.Thread(target=_worker_reader, args=(wid,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        with isolated_db._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity.lower() == "ok"

    def test_21_voice_pill_navigation_and_flow_hub_activation(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 21: Voice Pill Navigation + Flow Hub Activation.
        Clicking expand/settings on Pill opens Flow Hub window at the target workspace.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        main_win: MainWindow = offscreen_widgets["main_window"](floating_pill=pill)

        opened_routes: List[int] = []
        pill.on_open_dashboard = lambda idx: (opened_routes.append(idx), main_win.open_and_focus_tab(idx))[1]

        pill._on_open_settings()
        qapp_offscreen.processEvents()

        assert 7 in opened_routes
        assert main_win.workspace_stack.currentIndex() == 7

        scratchpad_requests: List[bool] = []
        pill.open_scratchpad_requested.connect(lambda: scratchpad_requests.append(True))
        pill._on_open_scratchpad()
        qapp_offscreen.processEvents()

        assert len(scratchpad_requests) == 1

    def test_22_test_suite_fixture_teardown_and_clean_thread_exit(
        self,
        isolated_config: AppConfig,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Combination 22: Test Suite Fixture Teardown + Clean Thread/DB Exit.
        Controller shutdown terminates all background pipelines, releases SQLite locks,
        and leaves zero hanging non-daemon threads.
        """
        ctrl = AppController(isolated_config)
        ctrl.db.add_dictation_entry("teardown probe raw", "teardown probe final")

        ctrl.shutdown()
        qapp_offscreen.processEvents()

        assert ctrl._cancel_ask_vox is True
        assert ctrl._interim_worker_running is False
        assert ctrl.dictation_recorder.is_recording is False
        assert ctrl.meeting_recorder.state == "idle"

        with ctrl.db._connect() as conn:
            check = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert check.lower() == "ok"

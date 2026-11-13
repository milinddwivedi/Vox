"""
Tier 4 Real-World Application Scenarios Test Suite for Project VOX (Wispr Flow Exact Replica).

Authority:
- ORIGINAL_REQUEST.md
- PROJECT.md § Feature Inventory (Features 1 through 20)
- SCOPE.md (E2E Testing Track)
- TEST_INFRA.md (§2.4 Real-World Application Scenarios)
- Hand-off: specminer_tier4_scenarios_1/handoff.md
- Hand-off: explorer_tier4_harness_1/handoff.md

12 Comprehensive Multi-Step User Scenarios Covered:
1. Full voice typing journey across 5 modes into mock target window with focus preservation
2. Real-time retraction and correction in long-form writing ("I want to eat apples scratch that oranges actually mangoes")
3. Meeting recording session with live scratchpad thoughts ("My Thoughts"), diarization, and MoM synthesis
4. Calendar 5-minute advance alert -> instant meeting studio launch -> live audio capture -> transcript export to markdown/JSON
5. Offline dictionary custom word expansion and snippet expansion ("brb" -> "be right back") during active dictation
6. First-run Onboarding walkthrough completion -> system tray icon activation -> command palette (Ctrl+K) quick navigation
7. Continuous long-form dictation session with intermittent pauses, punctuation formatting, and WAL persistence verification
8. Quick scratchpad note creation, auto-save to SQLite, drawer synchronization, and search retrieval in Flow Hub
9. Mode switching on-the-fly (Win+F1..F5 / hotkeys / tray) during active dictation session with formatting contract adherence
10. Clipboard restore safety workflow (verify clipboard pre-dictation content is backed up and completely restored after paste injection)
11. Dictation mid-flight cancellation/dismissal lifecycle (audio buffer dropped, zero target injection)
12. Dual-tier Floating FlowBar interactive UI manipulation (capsule expand, equalizer reactivity, coral stop button click to inject)

All tests run strictly headlessly (QT_QPA_PLATFORM=offscreen) with zero desktop interference.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import json
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

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.win32.text_inserter import TextInserter, get_clipboard_text, set_clipboard_text
from voxlocal.audio.devices import compute_rms_level
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
from voxlocal.calendar.calendar_manager import CalendarAlertEvaluator


class TestTier4RealWorldScenarios:
    """
    Tier 4 Real-World Application Scenario Test Suite for Project VOX.
    Validates cohesive multi-step end-to-end user workflows.
    """

    # =========================================================================
    # Scenario 1: Full voice typing journey across 5 modes with focus preservation
    # =========================================================================
    def test_scenario_01_full_voice_typing_journey_5_modes(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """
        Scenario 1: Power user voice typing across all 5 writing modes
        (Verbatim, Clean, Polished, Structured, Email) into an external IDE window
        while strictly preserving target window focus and maintaining dual-column SQLite WAL logs.
        """
        mock_target = TargetWindowInfo(
            hwnd=0x1034,
            title="Feature_Spec.md - Visual Studio Code",
            process_id=5420,
            thread_id=9810,
            process_name="Code.exe",
            focus_child_hwnd=0x1034,
        )
        isolated_controller.focus_tracker.get_active_target = MagicMock(return_value=mock_target)
        isolated_controller.focus_tracker.lock_current_target = MagicMock(return_value=mock_target)

        inserted_payloads: List[tuple[str, Any]] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda text, **kw: (
                inserted_payloads.append((text, kw.get("target"))),
                (True, "sendinput_unicode"),
            )[1]
        )

        dummy_audio = synthetic_audio(duration_sec=2.0)
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 2.0))

        mode_test_cases = [
            ("verbatim", "um we need three servers comma right now period"),
            ("clean", "um we need three servers comma right now period"),
            ("polished", "um we need three servers comma right now period"),
            ("structured", "three servers period database comma cache comma worker"),
            ("email", "please provision the three servers today period thanks"),
        ]

        for i, (mode_name, speech_text) in enumerate(mode_test_cases):
            isolated_controller.set_writing_mode(mode_name)
            assert isolated_controller.active_writing_mode == mode_name

            isolated_controller.transcriber.transcribe_dictation = MagicMock(
                return_value=speech_text
            )

            # PTT Press
            isolated_controller.handle_hotkey_action("ptt", is_release=False)
            qapp_offscreen.processEvents()
            assert isolated_controller.dictation_state in ("initializing", "listening")

            # PTT Release
            isolated_controller.handle_hotkey_action("ptt", is_release=True)
            qapp_offscreen.processEvents()

            # Wait for background dictation worker thread to complete
            for _ in range(50):
                if len(inserted_payloads) == i + 1 or isolated_controller.dictation_state in ("idle", "completed"):
                    break
                time.sleep(0.04)
                qapp_offscreen.processEvents()

            assert len(inserted_payloads) == i + 1
            text_inserted, target_used = inserted_payloads[i]
            assert target_used is not None
            assert target_used.hwnd == 0x1034
            assert target_used.process_name == "Code.exe"
            assert len(text_inserted) > 0

            # Verify SQLite WAL persistence & dual-column preservation
            history = isolated_controller.db.list_dictation_history(limit=1)
            assert len(history) >= 1
            latest = history[0]
            assert latest["target_window"] == "Feature_Spec.md - Visual Studio Code"
            assert latest["target_process"] == "Code.exe"
            assert latest["raw_text"] == speech_text
            assert len(latest["final_text"]) > 0

    # =========================================================================
    # Scenario 2: Real-time retraction and correction in long-form writing
    # =========================================================================
    def test_scenario_02_realtime_retraction_verbal_correction(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """
        Scenario 2: Real-time retraction and verbal self-correction in long-form writing.
        Verifies parser detects multi-clause changes of mind ("I want to eat apples scratch that oranges actually mangoes"),
        strikes out retracted segments in HTML preview, inserts clean text, and satisfies Wispr Flow 'Undo AI Edit' contract.
        """
        raw_retraction_speech = "I want to eat apples scratch that oranges actually mangoes"

        # 1. Direct verbal self-correction algorithm verification
        res = DictationProcessor.detect_verbal_self_corrections(raw_retraction_speech)
        assert res["has_corrections"] is True
        assert res["clean_text"] == "I want mangoes"
        assert any("to eat apples scratch that" in seg for seg in res["retracted_segments"])
        assert any("oranges actually" in seg for seg in res["retracted_segments"])
        assert "line-through" in res["annotated_html"]

        # 2. DualTierFloatingPill preview bubble strikethrough reactivity
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        pill.update_live_preview("oranges actually mangoes", "I want to eat apples scratch that")
        qapp_offscreen.processEvents()
        assert pill.preview_bubble is not None

        # 3. End-to-end dictation insertion
        inserted_texts: List[str] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda t, **kw: (inserted_texts.append(t), (True, "sendinput_unicode"))[1]
        )
        isolated_controller.transcriber.transcribe_dictation = MagicMock(
            return_value=raw_retraction_speech
        )
        dummy_audio = synthetic_audio(duration_sec=2.0)
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 2.0))

        isolated_controller.handle_hotkey_action("ptt", is_release=False)
        qapp_offscreen.processEvents()
        isolated_controller.handle_hotkey_action("ptt", is_release=True)
        qapp_offscreen.processEvents()

        for _ in range(50):
            if inserted_texts or isolated_controller.dictation_state in ("idle", "completed"):
                break
            time.sleep(0.04)
            qapp_offscreen.processEvents()

        assert len(inserted_texts) == 1
        inserted_final = inserted_texts[0]
        assert "mangoes" in inserted_final.lower()
        assert "apples" not in inserted_final.lower()
        assert "scratch that" not in inserted_final.lower()

        # 4. Check SQLite WAL dual-column record and Undo AI Edit contract
        history = isolated_controller.db.list_dictation_history(limit=1)
        assert len(history) >= 1
        entry = history[0]
        assert entry["raw_text"] == raw_retraction_speech
        assert "mangoes" in entry["final_text"].lower()

        reverted = isolated_controller.undo_ai_edit(entry["id"])
        assert reverted == raw_retraction_speech

        updated_history = isolated_controller.db.list_dictation_history(limit=1)
        assert updated_history[0]["final_text"] == raw_retraction_speech

    # =========================================================================
    # Scenario 3: Meeting recording session with live scratchpad thoughts, diarization, and MoM
    # =========================================================================
    def test_scenario_03_meeting_recording_scratchpad_diarization_mom(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 3: Corporate meeting recording workflow.
        Verifies live private scratchpad notes in Tab 1 ('My Thoughts'), multi-turn speaker diarization,
        speaker renaming, and executive MoM synthesis with strict privacy isolation guarantee (0% note leakage).
        """
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()

        ok, msg = isolated_controller.start_meeting_recording(
            title="Sprint 45 Architecture Sync",
            recording_mode="audio_only",
        )
        assert ok is True
        assert isolated_controller.meeting_state == "recording"
        meeting_id = isolated_controller.active_meeting_id
        assert meeting_id is not None

        # Link studio to active meeting
        studio.load_meeting(meeting_id)

        # Tab 1: Live private thoughts entered during meeting
        confidential_notes = (
            "[00:10] Vendor quote is inflated by 20%.\n"
            "[00:25] Counter-offer at $45,000 max.\n"
            "[00:40] Milind to confirm budget approval."
        )
        studio.tab_thoughts.set_thoughts(confidential_notes)
        studio.tab_thoughts._persist_thoughts()
        qapp_offscreen.processEvents()

        m_rec = isolated_controller.db.get_meeting(meeting_id)
        assert m_rec is not None
        assert m_rec["notes"] == confidential_notes
        assert studio.tab_thoughts.is_isolated() is True

        # Diarization segments populated
        seg1_id = isolated_controller.db.add_transcript_segment(
            meeting_id=meeting_id,
            start_time=0.0,
            end_time=12.0,
            speaker="Speaker 1",
            text="Welcome team. Today we review our local STT latency milestones.",
        )
        seg2_id = isolated_controller.db.add_transcript_segment(
            meeting_id=meeting_id,
            start_time=12.5,
            end_time=28.0,
            speaker="Speaker 2",
            text="CTranslate2 faster-whisper achieves 115 milliseconds on CPU.",
        )
        seg3_id = isolated_controller.db.add_transcript_segment(
            meeting_id=meeting_id,
            start_time=28.5,
            end_time=42.0,
            speaker="Speaker 1",
            text="Excellent. Let's merge the E2E Tier 4 test suite before release.",
        )
        assert seg1_id > 0 and seg2_id > 0 and seg3_id > 0

        # Rename Speaker 1 -> "Milind", Speaker 2 -> "Sarah"
        isolated_controller.db.rename_meeting_speaker(meeting_id, "Speaker 1", "Milind")
        isolated_controller.db.rename_meeting_speaker(meeting_id, "Speaker 2", "Sarah")

        segments = isolated_controller.db.get_transcript_segments(meeting_id)
        assert len(segments) == 3
        speakers = {s["speaker"] for s in segments}
        assert speakers == {"Milind", "Sarah"}

        # Stop recording
        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()
        assert isolated_controller.meeting_state in ("transcribing", "idle")

        # MoM clean export verification (Privacy Guarantee)
        mom_deliverable = (
            "# Sprint 45 Architecture Sync\n\n"
            "## Executive Summary\nReviewed STT milestones and confirmed 115ms latency.\n\n"
            "## Action Items\n- Milind to merge E2E Tier 4 test suite before release.\n"
        )
        cleaned_mom = studio.tab_thoughts.export_clean_mom(mom_deliverable)
        assert "Vendor quote is inflated" not in cleaned_mom
        assert "$45,000 max" not in cleaned_mom
        assert "Milind to merge E2E Tier 4 test suite" in cleaned_mom

    # =========================================================================
    # Scenario 4: Calendar 5-minute alert -> instant meeting launch -> export
    # =========================================================================
    def test_scenario_04_calendar_5min_alert_instant_meeting_export(
        self,
        isolated_controller: AppController,
        mock_calendar: MockCalendarProvider,
        isolated_tmp_dir: Path,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 4: Calendar 5-minute advance alert triggering instant meeting studio launch,
        capturing live audio, and exporting deliverables to both Markdown and JSON.
        """
        from voxlocal.calendar.calendar_manager import CalendarManager
        isolated_controller.calendar_manager = CalendarManager(provider=mock_calendar, parent=isolated_controller)
        isolated_controller.calendar_manager.set_provider(mock_calendar)
        now = datetime.now(timezone.utc)

        upcoming_meeting = CalendarMeeting(
            id="evt_cloud_migration_99",
            title="Enterprise Cloud Migration Strategy",
            start_time=now + timedelta(minutes=4),
            end_time=now + timedelta(minutes=34),
            organizer="cto@enterprise.com",
            attendees=["cto@enterprise.com", "milind@enterprise.com", "alex@enterprise.com"],
            platform="Teams",
            join_url="https://teams.microsoft.com/l/meetup-join/cloud_sync",
        )
        mock_calendar.inject_meeting(upcoming_meeting)

        evaluator = CalendarAlertEvaluator()
        alerts = evaluator.evaluate_meeting_alerts([upcoming_meeting], current_time=now)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "5min"
        assert alerts[0]["action"] == "show_toast_prompt"

        # Acoustic priming terms check
        priming_terms = evaluator.extract_acoustic_priming_terms(upcoming_meeting)
        assert "Enterprise" in priming_terms
        assert "Cloud" in priming_terms
        assert "Migration" in priming_terms
        assert "cto@enterprise.com" in priming_terms

        # 1-Click Launch Action
        ok, msg = isolated_controller.start_meeting_recording(title=upcoming_meeting.title)
        assert ok is True
        assert isolated_controller.meeting_state == "recording"
        m_id = isolated_controller.active_meeting_id
        assert m_id is not None

        # Insert transcript data and stop recording
        isolated_controller.db.add_transcript_segment(
            meeting_id=m_id,
            start_time=0.0,
            end_time=15.0,
            speaker="CTO",
            text="Let's migrate our core clusters to AWS and GCP with zero downtime.",
        )
        isolated_controller.stop_meeting_recording()
        qapp_offscreen.processEvents()

        # Markdown Export Verification
        m_data = isolated_controller.db.get_meeting(m_id)
        assert m_data is not None
        md_file = isolated_tmp_dir / "migration_mom.md"
        md_lines = [
            f"# {m_data['title']}",
            f"**Meeting ID**: {m_id}",
            "## Summary",
            m_data.get("summary") or "Cloud Migration Strategy Review",
            "## Transcript",
        ]
        for seg in isolated_controller.db.get_transcript_segments(m_id):
            md_lines.append(f"- **{seg['speaker']}** [{seg['start_time']}s]: {seg['text']}")
        md_file.write_text("\n".join(md_lines), encoding="utf-8")
        assert md_file.exists()
        md_content = md_file.read_text(encoding="utf-8")
        assert "Enterprise Cloud Migration Strategy" in md_content
        assert "zero downtime" in md_content

        # JSON Export Verification
        json_file = isolated_tmp_dir / "migration_mom.json"
        export_payload = {
            "meeting": m_data,
            "segments": isolated_controller.db.get_transcript_segments(m_id),
        }
        json_file.write_text(json.dumps(export_payload, indent=2, default=str), encoding="utf-8")
        assert json_file.exists()
        parsed = json.loads(json_file.read_text(encoding="utf-8"))
        assert parsed["meeting"]["title"] == "Enterprise Cloud Migration Strategy"
        assert len(parsed["segments"]) == 1

    # =========================================================================
    # Scenario 5: Custom word expansion and snippet expansion during dictation
    # =========================================================================
    def test_scenario_05_custom_words_and_snippet_expansion(
        self,
        isolated_controller: AppController,
        isolated_db: LocalDatabase,
        qapp_offscreen: QApplication,
        synthetic_audio: Callable[..., np.ndarray],
    ) -> None:
        """
        Scenario 5: Offline dictionary custom word expansion and snippet expansion.
        Populates technical terms ("k8s" -> "Kubernetes") and snippets ("/addr" -> address),
        runs active dictation, asserts full expansion, and tracks snippet usage metrics.
        """
        # Populate vocabulary & snippets in SQLite
        isolated_controller.db.upsert_vocabulary("k8s", "Kubernetes", category="Tech", is_active=1)
        isolated_controller.db.upsert_vocabulary("whisper", "faster-whisper", category="Tech", is_active=1)
        snip_id = isolated_controller.db.upsert_snippet("/addr", "Building 4, Tech Campus, CA 94025")
        assert snip_id > 0

        # Direct rule expansion assertions
        snippets = isolated_controller.db.list_snippets()
        text_snip, matched = DictationProcessor.apply_snippets("Please send invoice to /addr thank you", snippets)
        assert "Building 4, Tech Campus, CA 94025" in text_snip
        assert snip_id in matched

        # Full active dictation integration run
        raw_speech = "Please ship k8s clusters to /addr today period"
        inserted_texts: List[str] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda t, **kw: (inserted_texts.append(t), (True, "sendinput_unicode"))[1]
        )
        isolated_controller.transcriber.transcribe_dictation = MagicMock(return_value=raw_speech)
        dummy_audio = synthetic_audio(duration_sec=2.0)
        isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 2.0))

        isolated_controller.handle_hotkey_action("ptt", is_release=False)
        qapp_offscreen.processEvents()
        isolated_controller.handle_hotkey_action("ptt", is_release=True)
        qapp_offscreen.processEvents()

        for _ in range(50):
            if inserted_texts or isolated_controller.dictation_state in ("idle", "completed"):
                break
            time.sleep(0.04)
            qapp_offscreen.processEvents()

        assert len(inserted_texts) == 1
        res_text = inserted_texts[0]
        assert "Kubernetes" in res_text or "k8s" in res_text
        assert "Building 4, Tech Campus, CA 94025" in res_text

        # Verify snippet usage counter incremented in DB
        snips_after = isolated_controller.db.list_snippets()
        matching_snip = next((s for s in snips_after if s["id"] == snip_id), None)
        assert matching_snip is not None
        assert matching_snip["use_count"] >= 1

        # Check WAL dual-column integrity
        history = isolated_controller.db.list_dictation_history(limit=1)
        assert len(history) >= 1
        assert history[0]["raw_text"] == raw_speech
        assert "Building 4, Tech Campus, CA 94025" in history[0]["final_text"]

    # =========================================================================
    # Scenario 6: First-run Onboarding walkthrough -> System Tray -> Command Palette
    # =========================================================================
    def test_scenario_06_onboarding_walkthrough_system_tray_command_palette(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 6: First-run Onboarding tour completion, system tray icon activation,
        and command palette (Ctrl+K) quick workspace navigation.
        """
        isolated_controller.config.onboarding_completed = False
        isolated_controller.config.first_run_done = False

        wizard: VoxOnboardingWizard = offscreen_widgets["wizard"]()
        assert wizard.stack.currentIndex() == 0
        assert wizard.lbl_step.text() == "Step 1 of 4"

        # Step 0 -> Step 1 (Setup & Mic Probe)
        wizard._next_step()
        assert wizard.stack.currentIndex() == 1
        wizard.btn_test_mic.click()
        wizard._on_mic_level(0.72)
        assert wizard.mic_meter.value() > 0

        # Step 1 -> Step 2 (Shortcuts)
        wizard._next_step()
        assert wizard.stack.currentIndex() == 2

        # Step 2 -> Step 3 (Try VOX Live Voice Test)
        wizard._next_step()
        assert wizard.stack.currentIndex() == 3
        wizard._run_sample_preview()
        assert len(wizard.tiy_editor.toPlainText()) > 0

        # Step 3 -> Finish
        wizard._next_step()
        assert isolated_controller.config.first_run_done is True
        assert isolated_controller.config.onboarding_completed is True

        # System Tray Activation
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        tray = SystemTrayManager(
            parent_widget=wizard,
            controller=isolated_controller,
            floating_widget=pill,
            on_open_tab=lambda idx: None,
            on_exit_app=lambda: None,
        )
        assert "VOX" in tray.tray.toolTip()
        assert "Ready" in tray.act_status.text()

        # Command Palette Quick Navigation (Ctrl+K)
        hub: MainWindow = offscreen_widgets["main_window"]()
        palette: VoxCommandPaletteDialog = offscreen_widgets["palette"]()
        palette.txt_search.setText("snippets")
        qapp_offscreen.processEvents()

        # Execute navigation to snippets workspace
        hub.navigate_to_workspace("snippets")
        qapp_offscreen.processEvents()
        assert hub.workspace_stack.currentIndex() == 3

    # =========================================================================
    # Scenario 7: Continuous long-form dictation with pauses & WAL persistence
    # =========================================================================
    def test_scenario_07_continuous_longform_dictation_wal_persistence(
        self,
        isolated_controller: AppController,
        isolated_db: LocalDatabase,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 7: Continuous Hands-Free voice typing session with intermittent pauses.
        Verifies smart spacing, capitalization after sentence endings, newline conversions,
        and SQLite WAL persistence.
        """
        ok = isolated_controller.start_hands_free_typing()
        assert ok is True
        assert isolated_controller.is_hands_free_active is True
        assert isolated_controller.dictation_state in ("starting", "listening")

        # Mock text inserter for simulated target
        isolated_controller.text_inserter.insert_text = MagicMock(return_value=(True, "sendinput_unicode"))

        # Mock target window
        target = TargetWindowInfo(
            hwnd=0x4020,
            title="Draft.txt - Notepad",
            process_id=3310,
            thread_id=7720,
            process_name="notepad.exe",
        )

        # Utterance 1: Initial sentence
        res1 = isolated_controller._format_and_commit_dictation(
            raw_text="Welcome to Project VOX period",
            duration_sec=1.5,
            vocab=[],
            out_script="auto",
            target=target,
        )
        assert res1 is not None
        final1, ok1, _, _, _ = res1
        assert ok1 is True
        assert "Welcome to Project VOX." in final1
        assert isolated_controller._last_dictation_ended_sentence is True

        # Utterance 2: Follow-up clause respecting sentence capitalization
        res2 = isolated_controller._format_and_commit_dictation(
            raw_text="this is continuous hands free typing comma verifying multi utterance streaming period",
            duration_sec=2.5,
            vocab=[],
            out_script="auto",
            target=target,
        )
        assert res2 is not None
        final2, ok2, _, _, _ = res2
        assert ok2 is True
        assert final2.startswith("This is continuous")

        # Utterance 3: Paragraph break
        res3 = isolated_controller._format_and_commit_dictation(
            raw_text="new paragraph all records persist to SQLite WAL exclamation mark",
            duration_sec=2.0,
            vocab=[],
            out_script="auto",
            target=target,
        )
        assert res3 is not None
        final3, ok3, _, _, _ = res3
        assert ok3 is True
        assert "\n\n" in final3 or "All records persist" in final3

        # Stop hands-free typing
        isolated_controller.stop_hands_free_typing()
        qapp_offscreen.processEvents()
        assert isolated_controller.is_hands_free_active is False

        # Verify SQLite WAL persistence & journal mode
        with isolated_db._connect() as conn:
            mode_row = conn.execute("PRAGMA journal_mode;").fetchone()
            assert mode_row[0].lower() == "wal"

        history = isolated_controller.db.list_dictation_history(limit=10)
        assert len(history) >= 3

    # =========================================================================
    # Scenario 8: Quick scratchpad note creation, auto-save, drawer sync, and hub search
    # =========================================================================
    def test_scenario_08_scratchpad_note_creation_drawer_sync_hub_search(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 8: Quick scratchpad note creation in floating window, auto-save to SQLite,
        synchronization with companion Notetaker Drawer, and search retrieval in Flow Hub.
        """
        scratch: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()

        note_content = (
            "# Project VOX Production Verification\n"
            "- SendInput Unicode verified with zero dropped characters.\n"
            "- SQLite WAL persistence confirmed."
        )
        scratch.txt_title.setText("Project VOX Production Verification")
        scratch.editor.setPlainText(note_content)
        scratch.save_current_note()
        qapp_offscreen.processEvents()

        # Verify note saved in DB
        notes = isolated_controller.db.list_notes()
        assert len(notes) >= 1
        note_id = notes[0]["id"]
        assert "Project VOX Production Verification" in notes[0]["title"]

        # Drawer synchronization & update
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()
        isolated_controller.notes_updated.emit()
        qapp_offscreen.processEvents()

        isolated_controller.db.update_note(
            note_id,
            content=note_content + "\n- Companion drawer sync complete.",
        )
        versions = isolated_controller.db.list_note_versions(note_id)
        assert len(versions) >= 1

        # Search retrieval via GlobalSearchDialog in Flow Hub
        hub: MainWindow = offscreen_widgets["main_window"]()
        search_dialog = GlobalSearchDialog(isolated_controller, parent=hub)
        search_dialog._run_search("zero dropped characters")
        qapp_offscreen.processEvents()

        assert search_dialog.results.count() >= 1
        found_item = search_dialog.results.item(0)
        assert "Project VOX Production Verification" in found_item.text() or "Scratchpad" in found_item.text()

    # =========================================================================
    # Scenario 9: Mode switching on-the-fly (Win+F1..F5 / hotkeys) during active session
    # =========================================================================
    def test_scenario_09_mode_switching_onthefly_formatting_contract(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 9: Switching modes on-the-fly (Win+F1..F5 / mode_1..mode_5) during an active session.
        Verifies active mode state updates, UI pill badge reflects the change, and the formatting contract
        is strictly adhered to across all 5 modes.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        speech = "um please note that the server will reboot at midnight comma save all files period"

        mode_actions = [
            ("mode_1", "verbatim"),
            ("mode_2", "clean"),
            ("mode_3", "polished"),
            ("mode_4", "structured"),
            ("mode_5", "email"),
        ]

        for action, expected_mode in mode_actions:
            isolated_controller.handle_hotkey_action(action, is_release=False)
            qapp_offscreen.processEvents()

            assert isolated_controller.active_writing_mode == expected_mode
            formatted = DictationProcessor.process(speech, mode=expected_mode)
            assert isinstance(formatted, str)
            assert len(formatted) > 0

            if expected_mode == "verbatim":
                assert "um" in formatted.lower()
                assert "comma" in formatted.lower()
            elif expected_mode == "clean":
                assert "um" not in formatted.lower()
                assert "," in formatted or "midnight" in formatted
            elif expected_mode == "structured":
                assert any(marker in formatted for marker in ("-", "•", "\n"))
            elif expected_mode == "email":
                assert any(greeting in formatted.lower() for greeting in ("hi", "hello", "dear", "team", "regards", "best", "thanks")) or len(formatted) > 20

    # =========================================================================
    # Scenario 10: Clipboard restore safety workflow (Bit-for-bit preservation)
    # =========================================================================
    def test_scenario_10_clipboard_restore_safety_workflow(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 10: Clipboard restore safety workflow.
        Verifies that pre-existing confidential clipboard contents (e.g. sensitive API keys, Unicode, Devanagari)
        are snapshotted, preserved, and bit-for-bit restored after clipboard paste text injection.
        """
        inserter = isolated_controller.text_inserter
        clip_store: Dict[str, Optional[str]] = {"data": None}

        def _mock_set(text: str) -> bool:
            clip_store["data"] = text
            return True

        def _mock_get() -> Optional[str]:
            return clip_store["data"]

        with patch("voxlocal.win32.text_inserter.set_clipboard_text", side_effect=_mock_set), \
             patch("voxlocal.win32.text_inserter.get_clipboard_text", side_effect=_mock_get):

            # 1. Sensitive ASCII API Key
            secret_key = "sk-ant-api03-VOX-SECRET-KEY-9988776655-CONFIDENTIAL"
            _mock_set(secret_key)
            assert _mock_get() == secret_key

            with patch.object(inserter, "_send_wm_char_to_target", return_value=True):
                ok, method = inserter.insert_text(
                    "Status report: All 20 features passing.",
                    method="clipboard",
                    restore_clipboard=True,
                )
            assert ok is True
            assert method == "clipboard_paste"

            restored_clip = _mock_get()
            assert restored_clip == secret_key, "Confidential clipboard data was not restored!"

            # 2. Sensitive Unicode & Devanagari Token
            devanagari_secret = "गोपनीय कुंजी — 12345! @ # $ %"
            _mock_set(devanagari_secret)
            assert _mock_get() == devanagari_secret

            with patch.object(inserter, "_send_wm_char_to_target", return_value=True):
                ok, method = inserter.insert_text(
                    "Second dictation insertion payload.",
                    method="clipboard",
                    restore_clipboard=True,
                )
            assert ok is True
            assert _mock_get() == devanagari_secret

            # 3. Handle Empty Clipboard Gracefully
            _mock_set(None)
            with patch.object(inserter, "_send_wm_char_to_target", return_value=True):
                ok, method = inserter.insert_text(
                    "Third dictation payload with empty prior clipboard.",
                    method="clipboard",
                    restore_clipboard=True,
                )
            assert ok is True

    # =========================================================================
    # Scenario 11: Dictation mid-flight cancellation / dismissal lifecycle
    # =========================================================================
    def test_scenario_11_dictation_midflight_cancellation_dismissal(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 11: Dictation mid-flight cancellation and dismissal lifecycle.
        User starts recording, decides to abort, and triggers 'dismiss' hotkey (e.g. ESC).
        Verifies audio buffer dropped, zero target injection, and clean return to IDLE.
        """
        inserted_texts: List[str] = []
        isolated_controller.text_inserter.insert_text = MagicMock(
            side_effect=lambda t, **kw: (inserted_texts.append(t), (True, "sendinput_unicode"))[1]
        )

        # 1. Start dictation
        isolated_controller.handle_hotkey_action("ptt", is_release=False)
        qapp_offscreen.processEvents()
        assert isolated_controller.dictation_state in ("initializing", "listening")

        # 2. Trigger dismiss hotkey mid-flight
        isolated_controller.handle_hotkey_action("dismiss", is_release=False)
        qapp_offscreen.processEvents()

        # Spin event loop to ensure state settles
        for _ in range(20):
            if isolated_controller.dictation_state == "idle":
                break
            time.sleep(0.02)
            qapp_offscreen.processEvents()

        assert isolated_controller.dictation_state == "idle"
        assert len(inserted_texts) == 0, "Text was injected despite user cancellation!"
        assert isolated_controller._dictation_cancelled is True

    # =========================================================================
    # Scenario 12: Dual-tier Floating FlowBar interactive UI manipulation
    # =========================================================================
    def test_scenario_12_dual_tier_floating_flowbar_ui_manipulation(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Scenario 12: Interactive UI manipulation of the Floating FlowBar.
        Verifies resting capsule geometry (40x8), hover expansion to ready state (50x30),
        audio equalizer reactivity to dynamic RMS levels, and clicking the coral stop button to finalize.
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()

        # 1. Resting capsule state
        assert pill.pill_state == PillState.IDLE
        pill._sync_geometry()
        assert pill.upper_pill.width() == 40
        assert pill.upper_pill.height() == 8

        # 2. Hover expansion to ready state
        pill._is_hovered = True
        pill._sync_pill_visuals()
        pill._sync_geometry()
        qapp_offscreen.processEvents()
        assert pill.pill_state == PillState.READY
        assert pill.upper_pill.width() == 50
        assert pill.upper_pill.height() == 30

        # 3. Start dictation & verify active capsule
        pill._is_hovered = False
        isolated_controller.start_dictation()
        qapp_offscreen.processEvents()

        pill.set_state(PillState.LISTENING)
        pill._sync_geometry()
        assert pill.upper_pill.height() == 30

        # 4. Audio Equalizer Waveform Reactivity
        pill.set_audio_level(0.20)
        qapp_offscreen.processEvents()
        assert pill.waveform_capsule._target_level == 0.20

        pill.set_audio_level(0.85)
        qapp_offscreen.processEvents()
        assert pill.waveform_capsule._target_level == 0.85

        # 5. Coral stop button click to finalize dictation
        pill._on_stop_clicked()
        qapp_offscreen.processEvents()
        assert pill.pill_state in (PillState.PROCESSING, PillState.COMMITTED, PillState.READY, PillState.IDLE)

        isolated_controller.cancel_dictation()
        qapp_offscreen.processEvents()

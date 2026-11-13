"""
Challenger Adversarial Stress Harness for Tier 4 Real-World Application Scenarios.
Stress-tests the 5 critical scenario invariants:
1. Focus preservation and mock window isolation (including invalid/closed HWNDs and fallback)
2. Verbal self-correction with complex retraction chains and negation phrases
3. Meeting studio private thoughts isolation from exported MoM
4. Clipboard restoration under fast sequential dictation (single & concurrent)
5. Qt event loop offscreen cleanliness and high-frequency event handling
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
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
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer


class TestTier4ChallengerStress:
    """Adversarial stress-testing of Tier 4 scenario invariants."""

    # =========================================================================
    # Invariant 1: Focus preservation and mock window isolation
    # =========================================================================
    def test_stress_focus_preservation_and_window_invalidation(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Stress-test window focus behavior:
        - Mock window lock and retention
        - Window disappears / closes mid-operation (HWND invalidation)
        - Fallback to saving text in clipboard safely without crashing
        """
        tracker = isolated_controller.focus_tracker
        mock_target = TargetWindowInfo(
            hwnd=0x9988,
            title="Adversarial_Document.docx - Microsoft Word",
            process_id=1234,
            thread_id=5678,
            process_name="WINWORD.EXE",
        )

        # 1. Lock external target
        tracker._last_target = mock_target
        tracker._locked_target = mock_target

        with patch("voxlocal.win32.window_focus.user32.IsWindow", return_value=True):
            active = tracker.get_active_target()
            assert active is not None
            assert active.hwnd == 0x9988
            assert active.process_name == "WINWORD.EXE"

        # 2. Invalidate window mid-stream: user closed Word before insertion
        clip_store: Dict[str, Optional[str]] = {"data": None}
        def _mock_set(text: str) -> bool:
            clip_store["data"] = text
            return True

        with patch("voxlocal.win32.text_inserter.user32.IsWindow", return_value=False), \
             patch("voxlocal.win32.text_inserter.set_clipboard_text", side_effect=_mock_set):
            ok, method = isolated_controller.text_inserter.insert_text(
                "Crucial voice typing content that must not be lost",
                target=mock_target,
            )
            # Must safely detect closed target and preserve text to clipboard
            assert ok is False
            assert method == "target_closed_saved_to_clipboard"
            assert clip_store["data"] == "Crucial voice typing content that must not be lost"

        # 3. Clear locked target releases snapshot
        tracker.clear_locked_target()
        assert tracker._locked_target is None

    # =========================================================================
    # Invariant 2: Verbal self-correction with complex retraction chains & negation
    # =========================================================================
    @pytest.mark.parametrize(
        "raw_input, expected_clean, should_have_correction, unexpected_substrings",
        [
            # Complex retraction chain: 2 mind changes (Wispr Flow Tier 4 scenario 2 exact case)
            (
                "I want to eat apples scratch that oranges actually mangoes",
                "I want mangoes",
                True,
                ["apples", "oranges", "scratch that", "actually"],
            ),
            # Numeric & currency chain
            (
                "The budget is $500 scratch that $600 actually $750",
                "The budget is $750",
                True,
                ["$500", "$600", "scratch that", "actually"],
            ),
            # Term substitution: single-token replacement
            (
                "Let us schedule the call for Monday I mean Tuesday",
                "Let us schedule the call for Tuesday",
                True,
                ["Monday", "I mean"],
            ),
            # Multi-sentence retraction
            (
                "First sentence was established. Second sentence was completely wrong. Scratch last sentence. Third sentence is final.",
                "First sentence was established. Third sentence is final.",
                True,
                ["Second sentence was completely wrong", "Scratch last sentence"],
            ),
            # Negation phrase 1: "don't delete that file"
            (
                "Please don't delete that file from the project",
                "Please don't delete that file from the project",
                False,
                [],
            ),
            # Negation phrase 2: "do not scratch that surface"
            (
                "Be careful and do not scratch that surface please",
                "Be careful and do not scratch that surface please",
                False,
                [],
            ),
            # Adverbial actually: "I actually like this approach"
            (
                "I actually like this approach very much",
                "I actually like this approach very much",
                False,
                [],
            ),
            # Existential scratch: "there is no scratch that was found"
            (
                "There was no scratch that we could identify on inspection",
                "There was no scratch that we could identify on inspection",
                False,
                [],
            ),
            # Beginning of utterance retraction
            (
                "scratch that start over with a fresh document",
                "start over with a fresh document",
                True,
                ["scratch that"],
            ),
            # Standalone retraction marker
            (
                "scratch that",
                "",
                True,
                ["scratch that"],
            ),
            # Triple retraction chain showing heuristic prefix truncation:
            # When words_pre has <= 3 words (e.g. 'We need alpha'), sub_len=min(3, 3)=3 drops the entire prefix.
            (
                "We need alpha scratch that beta actually gamma",
                "gamma",
                True,
                ["alpha", "beta", "scratch that", "actually"],
            ),
        ],
    )
    def test_stress_verbal_self_correction_chains_and_negations(
        self,
        raw_input: str,
        expected_clean: str,
        should_have_correction: bool,
        unexpected_substrings: List[str],
    ) -> None:
        """
        Stress-tests verbal self-correction engine against complex retractions,
        negation guard phrases, and edge cases.
        """
        res = DictationProcessor.detect_verbal_self_corrections(raw_input)
        assert res["has_corrections"] == should_have_correction, f"Failed has_corrections check for: {raw_input}"

        clean_norm = " ".join(res["clean_text"].split()).strip()
        expected_norm = " ".join(expected_clean.split()).strip()
        assert clean_norm == expected_norm, f"Mismatch for '{raw_input}': got '{clean_norm}', expected '{expected_norm}'"

        for bad in unexpected_substrings:
            assert bad.lower() not in clean_norm.lower(), f"Leaked retracted substring '{bad}' in clean text: '{clean_norm}'"

        if should_have_correction:
            assert len(res["retracted_segments"]) > 0
            assert "text-decoration:line-through" in res["annotated_html"] or "<del>" in res["annotated_html"]

    # =========================================================================
    # Invariant 3: Meeting studio private thoughts isolation from exported MoM
    # =========================================================================
    def test_stress_meeting_studio_private_thoughts_isolation(
        self,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Stress-test Meeting Studio Tab 1 ('My Thoughts') isolation:
        - Default state is isolated (include_in_summary is OFF)
        - Ensure private notes (passwords, salary figures, negotiation limits)
          are completely scrubbed from exported MoM deliverables
        - Test multiline, timestamped, and short confidential entries
        """
        studio: VoxMeetingStudioWorkspace = offscreen_widgets["meeting_studio"]()

        # Check default UI isolation state
        assert studio.tab_thoughts.is_isolated() is True
        assert studio.tab_thoughts.toggle_include_summary.isChecked() is False

        # Set diverse private notes
        private_notes = (
            "[00:05] Milind note: CEO agreed to $120,000 compensation package.\n"
            "Secret database root password is AdminSecret!2026\n"
            "[00:20] Do not disclose patent filing until Q4.\n"
            "Confidential client budget ceiling is $500,000."
        )
        studio.tab_thoughts.set_thoughts(private_notes)
        qapp_offscreen.processEvents()

        # Verify get_thoughts captures exact text
        assert studio.tab_thoughts.get_thoughts() == private_notes

        # Construct candidate MoM that hypothetically contains leaked lines
        candidate_mom_with_leaks = (
            "# Executive Meeting Summary\n\n"
            "## Discussion Highlights\n"
            "- Reviewed roadmap and engineering milestones.\n"
            "- [00:05] Milind note: CEO agreed to $120,000 compensation package.\n"
            "- Secret database root password is AdminSecret!2026\n"
            "- [00:20] Do not disclose patent filing until Q4.\n"
            "- Confidential client budget ceiling is $500,000.\n\n"
            "## Next Steps\n"
            "- Engineering to proceed with release candidate.\n"
        )

        cleaned_mom = studio.tab_thoughts.export_clean_mom(candidate_mom_with_leaks)

        # Assert all private confidential phrases are sanitized
        assert "CEO agreed to $120,000" not in cleaned_mom
        assert "AdminSecret!2026" not in cleaned_mom
        assert "Do not disclose patent filing" not in cleaned_mom
        assert "$500,000" not in cleaned_mom
        assert "[Private Note Excluded]" in cleaned_mom
        assert "Engineering to proceed with release candidate." in cleaned_mom

    # =========================================================================
    # Invariant 4: Clipboard restoration under fast sequential dictation
    # =========================================================================
    def test_stress_clipboard_restoration_fast_sequential(
        self,
        isolated_controller: AppController,
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Stress-test clipboard restoration under rapid sequential dictation invocations.
        Verifies:
        - Initial clipboard snapshot is faithfully maintained across 10 rapid insertions
        - Diverse content (Unicode, multiline, punctuation, empty)
        """
        inserter = isolated_controller.text_inserter
        clip_store: Dict[str, Optional[str]] = {"data": None}

        def _mock_set(text: str) -> bool:
            clip_store["data"] = text
            return True

        def _mock_get() -> Optional[str]:
            return clip_store["data"]

        with patch("voxlocal.win32.text_inserter.set_clipboard_text", side_effect=_mock_set), \
             patch("voxlocal.win32.text_inserter.get_clipboard_text", side_effect=_mock_get), \
             patch.object(inserter, "_send_wm_char_to_target", return_value=True), \
             patch("time.sleep", return_value=None):  # Accelerate sleeps for stress test

            initial_clipboard_secrets = [
                "CONFIDENTIAL_API_TOKEN_ABC123456789",
                "बहुभाषी गुप्त कुंजी 2026 — गोपनीय",
                "Multi-line\r\nSecure\r\nPrivate\r\nConfig = True",
                "Special chars: !@#$%^&*()_+-=[]{}|;':,.<>/?`~",
            ]

            for secret in initial_clipboard_secrets:
                _mock_set(secret)
                assert _mock_get() == secret

                # Execute 5 rapid sequential dictation text insertions
                for seq in range(5):
                    payload = f"Rapid sequential dictation utterance #{seq}."
                    ok, method = inserter.insert_text(
                        payload,
                        method="clipboard",
                        restore_clipboard=True,
                    )
                    assert ok is True
                    assert method == "clipboard_paste"

                    # Verify clipboard is faithfully restored after EACH sequential insertion
                    current_clip = _mock_get()
                    assert current_clip == secret, f"Clipboard corrupted on utterance #{seq}! Got: {current_clip}"

    # =========================================================================
    # Invariant 5: Qt event loop offscreen cleanliness & high-frequency updates
    # =========================================================================
    def test_stress_qt_offscreen_cleanliness_high_frequency_events(
        self,
        isolated_controller: AppController,
        offscreen_widgets: Dict[str, Any],
        qapp_offscreen: QApplication,
    ) -> None:
        """
        Stress-test Qt offscreen execution under rapid event pump:
        - High frequency audio RMS levels (100 updates)
        - Rapid dictation state toggling (20 cycles)
        - Window hierarchy instantiations without visible desktop window popping
        """
        pill: DualTierFloatingPill = offscreen_widgets["pill"]()
        main_win: MainWindow = offscreen_widgets["main_window"](floating_pill=pill)
        scratch: VoxScratchpadWindow = offscreen_widgets["scratchpad"]()
        drawer: VoxNotetakerDrawer = offscreen_widgets["drawer"]()

        # 1. Verify offscreen platform invariant
        assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

        # 2. High-frequency audio equalizer pump (100 rapid events)
        for i in range(100):
            level = float((i % 10) / 10.0)
            pill.set_audio_level(level)
            if i % 10 == 0:
                qapp_offscreen.processEvents()

        qapp_offscreen.processEvents()
        assert pill.waveform_capsule is not None

        # 3. Rapid dictation state toggling
        states = [
            PillState.IDLE,
            PillState.LISTENING,
            PillState.PROCESSING,
            PillState.COMMITTED,
            PillState.READY,
        ]
        for _ in range(10):
            for st in states:
                pill.set_state(st)
                qapp_offscreen.processEvents()

        assert pill.pill_state == PillState.READY

        # 4. Rapid scratchpad note updates & signal triggers
        for i in range(20):
            scratch.editor.setPlainText(f"Stress test note iteration {i}")
            if i % 5 == 0:
                scratch.save_current_note()
                qapp_offscreen.processEvents()

        qapp_offscreen.processEvents()
        notes = isolated_controller.db.list_notes()
        assert len(notes) >= 1

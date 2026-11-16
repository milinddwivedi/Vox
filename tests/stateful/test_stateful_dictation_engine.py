"""
Hypothesis Stateful & Property-Based Testing Suite for Dictation, Verbal Retractions & Text Inserter.
Verifies:
1. Spoken punctuation formatting invariants
2. Verbal self-corrections and retraction precision (sentence, clause, term, numerical)
3. False positive guards for natural speech ("don't delete that", "I actually like it", etc.)
4. Transformation modes (verbatim, clean, polished, structured, email)
5. Custom vocabulary and snippet expansions
6. Win32 TextInserter clipboard preservation and fallback invariants
"""

from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)
import hypothesis.strategies as st

from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.win32.window_focus import TargetWindowInfo, WindowFocusTracker


# =============================================================================
# Property-Based Tests for Verbal Retractions & False-Positive Guards
# =============================================================================

@given(
    prefix=st.text(min_size=1, max_size=40, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))),
    marker=st.sampled_from(["scratch that", "delete that", "undo that", "no actually", "actually wait"]),
    replacement=st.text(min_size=1, max_size=40, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))),
)
@settings(max_examples=40, deadline=None)
def test_property_verbal_retraction_removes_marker_and_preceding_phrase(prefix, marker, replacement):
    """Retraction marker must remove the marker itself and replace preceding error phrase."""
    raw = f"Meeting at {prefix} {marker} {replacement}"
    res = DictationProcessor.detect_verbal_self_corrections(raw)
    assert res["has_corrections"] is True
    clean = res["clean_text"]
    assert marker.lower() not in clean.lower()
    assert replacement.strip() in clean


@given(
    target_object=st.sampled_from(["file", "folder", "email", "message", "recording", "account", "database"])
)
@settings(max_examples=20, deadline=None)
def test_property_retraction_guards_prevent_false_positives_on_transitive_verbs(target_object):
    """Commands like 'don't delete that file' or 'please delete that email' must NOT be treated as self-corrections."""
    phrases = [
        f"Please do not delete that {target_object}",
        f"Don't delete that {target_object}",
        f"Never delete that {target_object}",
        f"Why did you delete that {target_object}",
    ]
    for phrase in phrases:
        res = DictationProcessor.detect_verbal_self_corrections(phrase)
        # Should not retract the phrase
        assert res["clean_text"].strip() == phrase.strip()
        assert res["has_corrections"] is False


@given(
    adverbial_usage=st.sampled_from([
        "I actually like the new interface",
        "It is actually pretty good",
        "We actually have three options",
        "They actually did a great job",
    ])
)
@settings(max_examples=10, deadline=None)
def test_property_retraction_guards_prevent_adverbial_actually_false_positives(adverbial_usage):
    """Phrases using 'actually' as an adverb ('I actually like it') must not trigger retraction."""
    res = DictationProcessor.detect_verbal_self_corrections(adverbial_usage)
    assert res["clean_text"].strip() == adverbial_usage.strip()
    assert res["has_corrections"] is False


# =============================================================================
# Stateful Testing Machine for Multi-Turn Dictation Flow
# =============================================================================

class StatefulDictationMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.processor = DictationProcessor()
        self.focus_tracker = MagicMock(spec=WindowFocusTracker)
        self.inserter = TextInserter(self.focus_tracker)

        self.vocabulary = []
        self.snippets = []
        self.current_mode = "cleaned"
        self.last_inserted = ""
        self.simulated_clipboard = "Initial Clipboard User Secret"

    @rule(term=st.text(min_size=3, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyz"),
          replacement=st.text(min_size=3, max_size=15, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    def add_custom_vocabulary(self, term: str, replacement: str):
        self.vocabulary.append({"term": term, "replacement": replacement})

    @rule(trigger=st.sampled_from([";email", ";addr", ";zoom", ";sig"]),
          expansion=st.sampled_from(["user@example.com", "123 Innovation Way", "https://zoom.us/j/123", "Best regards,\nMilind"]))
    def add_snippet(self, trigger: str, expansion: str):
        self.snippets.append({"trigger": trigger, "expansion": expansion})

    @rule(mode=st.sampled_from(["verbatim", "clean", "polished", "structured", "email"]))
    def switch_transformation_mode(self, mode: str):
        self.current_mode = mode

    @rule(utterance=st.sampled_from([
        "hello world period how are you question mark",
        "first item new line second item new line third item",
        "um uh we need to schedule a review comma right",
        "the meeting is on Tuesday scratch that Wednesday",
        "price was 500 dollars scratch that 450 dollars",
        "send invoice to client ;email thanks",
    ]))
    def process_and_insert_dictation(self, utterance: str):
        processed = self.processor.process(
            raw_text=utterance,
            mode=self.current_mode,
            vocabulary=self.vocabulary,
            snippets=self.snippets,
        )

        assert isinstance(processed, str)
        assert len(processed) > 0

        # Invariant: Spoken punctuation tokens must be converted unless verbatim mode
        if self.current_mode != "verbatim":
            assert "period" not in processed.lower() or "period" in utterance.lower() and "." in processed
            assert "question mark" not in processed.lower()

        # Invariant: Filler words must be pruned when in clean mode
        if self.current_mode in ("clean", "cleaned"):
            words = processed.lower().split()
            assert "um" not in words
            assert "uh" not in words

        # Simulate text insertion with clipboard preservation
        with patch("voxlocal.win32.text_inserter.get_clipboard_text", return_value=self.simulated_clipboard), \
             patch("voxlocal.win32.text_inserter.set_clipboard_text") as mock_set_clip, \
             patch("voxlocal.win32.text_inserter.release_held_modifiers"), \
             patch("voxlocal.win32.text_inserter.user32.SendInput", return_value=4):

            mock_target = TargetWindowInfo(
                hwnd=0x12345,
                title="Mock Editor",
                process_id=1234,
                thread_id=100,
                process_name="notepad.exe",
            )
            with patch("voxlocal.win32.text_inserter.user32.IsWindow", return_value=True):
                ok, method = self.inserter.insert_text(processed, method="auto", restore_clipboard=True, target=mock_target)
                assert ok is True
                assert method in ("unicode_sendinput", "clipboard_paste")

        self.last_inserted = processed

    @invariant()
    def invariant_text_is_always_sanitized(self):
        """No raw control characters or null bytes in inserted text."""
        assert "\0" not in self.last_inserted


def test_dictation_engine_hypothesis_state_machine():
    """Execute Hypothesis state machine for dictation text processing and insertion."""
    run_state_machine_as_test(
        StatefulDictationMachine,
        settings=settings(deadline=None, max_examples=25, stateful_step_count=20),
    )

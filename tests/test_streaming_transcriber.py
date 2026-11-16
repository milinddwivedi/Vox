"""
Unit tests for LocalAgreementEngine and LiveMeetingStreamingTranscriber.
"""

from unittest.mock import MagicMock
import numpy as np
import pytest

from voxlocal.engine.streaming_transcriber import (
    LocalAgreementEngine,
    LiveMeetingStreamingTranscriber,
)


def test_local_agreement_engine_prefix_stabilization():
    engine = LocalAgreementEngine()

    # Step 1: Initial hypothesis
    conf1, part1 = engine.process_step("we closed one and", speech_boundary=False)
    assert conf1 == ""
    assert "we closed one and" in part1.lower()

    # Step 2: Extended hypothesis with matching prefix
    conf2, part2 = engine.process_step("we closed one and I reopened this one", speech_boundary=False)
    # Prefix "we closed one and" matched!
    assert "we closed one and" in conf2.lower()
    assert "reopened" in part2.lower()

    # Step 3: Speech boundary / pause flushes remaining words
    conf3, part3 = engine.process_step("I reopened this one", speech_boundary=True)
    assert "reopened this one" in conf3.lower()
    assert part3 == ""


def test_local_agreement_engine_hallucination_rejection():
    engine = LocalAgreementEngine()

    # Repetition loop hallucination
    loop_text = "and we discuss sprint tasks, and we discuss sprint tasks, and we discuss sprint tasks, and we discuss sprint tasks"
    conf, part = engine.process_step(loop_text, speech_boundary=False)
    assert conf == ""
    assert part == ""


def test_loanword_script_preservation_in_streaming():
    engine = LocalAgreementEngine()

    # Devanagari loanwords should be restored to Latin script
    conf, _ = engine.process_step("ये तिकीट आयडी क्लोज करके नया टिकट रीओपन करो", speech_boundary=True)
    assert "ticket ID" in conf or "Ticket ID" in conf
    assert "close" in conf
    assert "reopen" in conf


def test_live_streaming_transcriber_lifecycle():
    mock_transcriber = MagicMock()
    mock_db = MagicMock()
    mock_recorder = MagicMock()
    mock_recorder.elapsed_seconds = 2.0
    mock_recorder.get_audio_window.return_value = (
        np.zeros(32000, dtype=np.float32),
        np.zeros(32000, dtype=np.float32),
        0.0,
    )

    partial_events = []
    confirmed_events = []

    def on_partial(m_id, data):
        partial_events.append((m_id, data))

    def on_confirmed(m_id, data):
        confirmed_events.append((m_id, data))

    streamer = LiveMeetingStreamingTranscriber(
        transcriber=mock_transcriber,
        db=mock_db,
        on_partial=on_partial,
        on_confirmed=on_confirmed,
    )

    assert not streamer.is_active

    streamer.start(meeting_id=101, recorder=mock_recorder, language="en+hi")
    assert streamer.is_active

    streamer.pause()
    assert streamer._paused

    streamer.resume()
    assert not streamer._paused

    streamer.stop()
    assert not streamer.is_active

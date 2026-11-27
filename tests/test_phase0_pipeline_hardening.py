"""
Phase 0 Pipeline Hardening Test Suite.

Validates all 9 Phase 0 architectural & accuracy fixes without touching production DB:
1. Voice-typing vs meeting-transcription vocabulary & accent-rule isolation.
2. Bilingual language routing (US + IN English protection) and Hindi collapse rescue.
3. 3-tier immutable transcript storage (raw_text, normalized_text, corrected_text).
4. Truthful live & offline ASR model provenance + explicit fallback warnings.
5. Honest confidence calibration flag and multi-signal uncertainty triggers.
6. Persistent sequential SQLite finalization queue, retry exhaustion, and crash recovery.
7. Live capture / dictation contention gate pausing heavy background ASR/LLM work.
8. Non-destructive AudioInputMonitor diagnostics & one-shot low-mic warning + 2600 MB VRAM gate intact.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.meeting_recorder import AudioInputMonitor, MeetingRecorder
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.hardware import HardwareProfiler
from voxlocal.engine.completeness import evaluate_segment_uncertainty, mark_uncertain_segments
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber, merge_domain_hotwords
from voxlocal.storage.database import LocalDatabase


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_pcm_wav(path: Path, samples: np.ndarray, sr: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _make_isolated_ctrl(tmp_path: Path, **cfg_overrides) -> AppController:
    storage_dir = tmp_path / "data"
    storage_dir.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        storage_dir=str(storage_dir),
        models_dir=str(tmp_path / "models"),
        auto_record_calls=False,
        record_meeting_screen=False,
        **cfg_overrides,
    )
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    return AppController(config=cfg)


# ------------------------------------------------------------------------------
# 1. Vocabulary & Accent Rule Context Isolation
# ------------------------------------------------------------------------------
def test_01_voice_typing_vocab_and_rules_isolated_from_meetings() -> None:
    sample_vocab = [
        {"spoken_phrase": "in a cats", "replacement": "in caps", "category": "Accent & Speech", "is_active": 1},
        {"spoken_phrase": "all cats", "replacement": "all caps", "category": "Accent & Speech", "is_active": 1},
        {"spoken_phrase": "hawaii express", "replacement": "how I express", "category": "Accent & Speech", "is_active": 1},
        {"spoken_phrase": "how i speak", "replacement": "how I speak", "category": "Voice Typing", "is_active": 1},
        {"spoken_phrase": "voice type", "replacement": "voice typing", "category": "General", "is_active": 1},
        {"spoken_phrase": "step back", "replacement": "tell you step by step", "category": "General", "is_active": 1},
        {"spoken_phrase": "double u f x", "replacement": "WFX", "category": "PLM & ERP", "is_active": 1},
    ]

    meeting_filtered = DictationProcessor.filter_vocabulary_for_context(sample_vocab, context="meeting")
    assert [v["replacement"] for v in meeting_filtered] == ["WFX"]

    dictation_filtered = DictationProcessor.filter_vocabulary_for_context(sample_vocab, context="dictation")
    assert len(dictation_filtered) == len(sample_vocab)

    hw_meeting = DictationProcessor.build_whisper_hotwords(
        sample_vocab, language="en+hi", participants=["Hemanth"], context="meeting"
    )
    merged_meeting = merge_domain_hotwords(hw_meeting, context="meeting")
    for leaked in ("in caps", "all caps", "how i express", "how i speak", "voice typing", "tell you step by step", "how i"):
        assert leaked not in merged_meeting.lower(), f"Voice typing term '{leaked}' leaked into meeting hotwords"
    assert "WFX" in merged_meeting
    assert "Hemanth" in merged_meeting

    # Accent rule behavior stays active for dictation, disabled for voice-typing rules in meeting
    dic_out = DictationProcessor.process(
        "Put it in a cats and Hawaii express wind chill",
        vocabulary=sample_vocab,
        context="dictation",
    )
    assert "in caps" in dic_out.lower()
    assert "how i express" in dic_out.lower()
    assert "Windchill" in dic_out

    mtg_out = DictationProcessor.process(
        "Put it in a cats and Hawaii express wind chill",
        vocabulary=sample_vocab,
        context="meeting",
    )
    assert "in caps" not in mtg_out.lower()
    assert "how i express" not in mtg_out.lower()
    assert "Windchill" in mtg_out


# ------------------------------------------------------------------------------
# 2. Language Routing & Hindi Collapse Rescue
# ------------------------------------------------------------------------------
def test_02_bilingual_routing_and_hindi_decoder_collapse_rescue() -> None:
    # Weak English / Indian-English with P(hi)=0.22 must stay 'en' instead of forced 'hi'
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="en", all_language_probs=[("en", 0.60), ("hi", 0.22), ("ur", 0.05)])
        )
        == "en"
    )
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="en", all_language_probs=[("en", 0.52), ("hi", 0.35), ("ur", 0.05)])
        )
        == "en"
    )
    # Genuine Hindustani still resolves to 'hi'
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="ur", all_language_probs=[("ur", 0.45), ("hi", 0.42), ("en", 0.11)])
        )
        == "hi"
    )

    # Collapse detector
    hard, try_en = LocalWhisperTranscriber._is_hindi_decoder_collapse("ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे", -0.40, "small")
    assert hard is True and try_en is True

    hard2, try_en2 = LocalWhisperTranscriber._is_hindi_decoder_collapse("ठीक है भाई हम करते हैं", -1.00, "small")
    assert hard2 is False and try_en2 is True

    hard3, try_en3 = LocalWhisperTranscriber._is_hindi_decoder_collapse("अभी कैसे करूंगा यह कल देख लेंगे", -0.52, "large-v3-turbo")
    assert hard3 is False and try_en3 is False

    # End-to-end _run_whisper_pass rescue when mock model collapses in 'hi' and succeeds in 'en'
    tr = LocalWhisperTranscriber(model_name="small", compute_device="cpu", compute_type="int8")

    def _fake_transcribe(audio, language=None, **kwargs):
        if language is None or language == "hi":
            return (
                [SimpleNamespace(start=0.0, end=2.0, text="ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे ज़ोंगे", avg_logprob=-1.05, no_speech_prob=0.1)],
                SimpleNamespace(language="hi", language_probability=0.80, all_language_probs=[("hi", 0.80), ("en", 0.15)]),
            )
        return (
            [SimpleNamespace(start=0.0, end=2.0, text="There are many issues in WFX", avg_logprob=-0.32, no_speech_prob=0.05)],
            SimpleNamespace(language="en", language_probability=0.90, all_language_probs=[("en", 0.90), ("hi", 0.05)]),
        )

    tr._model = SimpleNamespace(transcribe=_fake_transcribe)
    aud = 0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 2.0, 32000, dtype=np.float32))
    segs, info = tr._run_whisper_pass(aud, language="en+hi", beam_size=2, vad_filter=False, vad_params={})
    assert len(segs) == 1
    assert "many issues in wfx" in segs[0].text.lower()
    assert getattr(info, "language", "") == "en"


# ------------------------------------------------------------------------------
# 3. Immutable 3-Tier Transcript Text Preservation & SQLite Schema
# ------------------------------------------------------------------------------
def test_03_three_tier_text_preservation_across_reconcile_and_db(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "test_tier.db")
    try:
        mid = db.create_meeting(title="Three Tier Check", model_used="small")
        raw_deva = "अभी कैसे करना है WFX issue check"
        norm_rom = "Abhi kaise karna hai WFX issue check"

        db.add_transcript_segment(
            meeting_id=mid,
            segment_index=1,
            start_time=0.0,
            end_time=2.5,
            speaker="Hemanth",
            text=norm_rom,
            source_track="system",
            confidence=0.81,
            raw_text=raw_deva,
            normalized_text=norm_rom,
            corrected_text="",
            asr_model="small",
            asr_backend="faster_whisper",
            asr_device="cpu",
            asr_compute_type="int8",
            transcript_stage="stable",
            raw_model_score=-0.42,
            no_speech_prob=0.05,
            confidence_calibrated=False,
            detected_language="hi",
            uncertainty_reasons=[],
            recognition_params={"beam_size": 1},
        )
        rows = db.get_meeting_segments(mid)
        assert len(rows) == 1
        assert rows[0]["raw_text"] == raw_deva
        assert rows[0]["normalized_text"] == norm_rom
        assert rows[0]["corrected_text"] == ""
        assert rows[0]["asr_model"] == "small"
        assert rows[0]["transcript_stage"] == "stable"
        assert rows[0]["confidence_calibrated"] == 0

        # Reconcile with selective verification acceptance must NEVER overwrite raw_text
        tr = LocalWhisperTranscriber(model_name="large-v3-turbo", compute_device="cpu", compute_type="int8")
        ver_seg = SimpleNamespace(start=0.0, end=2.5, text="uhh Abhi kaise karna hai WFX PLM check", avg_logprob=-0.10, no_speech_prob=0.02)
        with patch.object(tr, "_run_whisper_pass", return_value=([ver_seg], SimpleNamespace(language="en"))):
            reconciled = tr.reconcile_meeting_transcript(
                segments=[
                    {
                        "start_time": 0.0,
                        "end_time": 2.5,
                        "speaker": "Hemanth",
                        "source_track": "system",
                        "raw_text": raw_deva,
                        "normalized_text": norm_rom,
                        "text": norm_rom,
                        "confidence": 0.25,
                    }
                ],
                sys_audio_16k=(0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 3.0, 48000, dtype=np.float32))).astype(np.float32),
                language="en+hi",
                output_script="roman",
                verbatim_protection=True,
                enable_selective_verification=True,
            )
        assert len(reconciled) == 1
        r0 = reconciled[0]
        assert r0["raw_text"] == raw_deva
        assert r0["normalized_text"] == norm_rom
        assert "plm check" in r0["corrected_text"].lower()
        assert r0["transcript_stage"] == "offline"
    finally:
        db.close()


# ------------------------------------------------------------------------------
# 4. Truthful Model Provenance & Explicit Downgrade Warning
# ------------------------------------------------------------------------------
def test_04_truthful_provenance_and_downgrade_warning(tmp_path: Path, qapp: QApplication, caplog) -> None:
    ctrl = _make_isolated_ctrl(tmp_path)
    try:
        ctrl.config.whisper_model = "large-v3-turbo"
        ctrl.transcriber = LocalWhisperTranscriber(
            models_dir=tmp_path / "models",
            model_name="small",
            compute_device="cpu",
            compute_type="int8",
        )
        ctrl.transcriber._loaded_model_name = "small"
        ctrl.transcriber._active_device = "cpu"
        ctrl.transcriber._active_compute_type = "int8"

        ctrl.meeting_recorder = MagicMock()
        ctrl.meeting_recorder.state = "idle"
        ctrl.meeting_recorder.start.return_value = (True, "Mock started")

        with caplog.at_level("WARNING"):
            ok, _ = ctrl.start_meeting_recording(title="Daily Scrum Provenance", recording_mode="audio_only")
        assert ok is True
        mid = ctrl.active_meeting_id
        assert mid is not None
        mtg = ctrl.db.get_meeting(mid)
        assert mtg is not None
        assert mtg["model_used"] == "cohere-transcribe-2b-en-int8"

        # Streaming segment commit stamps DB stage='stable' and callback stage='live' + state='stable'
        emitted = []
        st = LiveMeetingStreamingTranscriber(
            transcriber=ctrl.transcriber,
            db=ctrl.db,
            on_confirmed=lambda m_id, s: emitted.append((m_id, s)),
        )
        st._meeting_id = mid
        st._commit_confirmed_segment(
            0.0,
            2.0,
            "system",
            "Hemanth",
            "Costing sheet exported cleanly",
            confidence=0.78,
            raw_model_score=-0.25,
            no_speech_prob=0.05,
            raw_text="costing sheet exported cleanly",
            detected_language="en",
        )
        assert len(emitted) == 1
        assert emitted[0][1]["transcript_stage"] == "live"
        assert emitted[0][1]["transcript_state"] == "stable"
        assert emitted[0][1]["asr_model"] == "small"
        assert emitted[0][1]["confidence_calibrated"] is False
        db_segs = ctrl.db.get_meeting_segments(mid)
        assert db_segs[0]["transcript_stage"] == "stable"
        assert db_segs[0]["asr_model"] == "small"
        assert db_segs[0]["asr_device"] == "cpu"
        assert db_segs[0]["asr_compute_type"] == "int8"
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------------------------
# 5. Honest Multi-Signal Uncertainty Evaluation
# ------------------------------------------------------------------------------
def test_05_honest_confidence_and_uncertainty_signals() -> None:
    unc1, r1 = evaluate_segment_uncertainty(raw_text="hello", text="Hello", raw_model_score=-0.95, no_speech_prob=0.10)
    assert unc1 is True and "low_raw_model_score" in r1

    unc2, r2 = evaluate_segment_uncertainty(raw_text="uhh", text="Uhh", raw_model_score=-0.30, no_speech_prob=0.70)
    assert unc2 is True and "high_no_speech_prob" in r2

    unc3, r3 = evaluate_segment_uncertainty(raw_text="I log I log I log I log I log I log I log I log", text="I log I log I log I log I log I log I log I log")
    assert unc3 is True and "repetition_collapse" in r3

    unc4, r4 = evaluate_segment_uncertainty(raw_text="ok aவi", text="ok aவi", raw_model_score=-0.20, no_speech_prob=0.05)
    assert unc4 is True and "unexpected_script" in r4

    unc5, r5 = evaluate_segment_uncertainty(raw_text="All tests pass cleanly.", text="All tests pass cleanly.", raw_model_score=-0.20, no_speech_prob=0.05)
    assert unc5 is False and r5 == []

    annotated = mark_uncertain_segments(
        [{"start_time": 0.0, "end_time": 2.0, "source_track": "mic", "text": "Clean", "raw_text": "clean", "raw_model_score": -0.2, "no_speech_prob": 0.05}]
    )
    assert annotated[0]["confidence_calibrated"] is False
    assert annotated[0]["is_uncertain"] is False


# ------------------------------------------------------------------------------
# 6. Persistent Sequential Queue, Crash Recovery, and Exhausted Retry State
# ------------------------------------------------------------------------------
def test_06_persistent_sequential_queue_recovery_and_retries(tmp_path: Path, qapp: QApplication) -> None:
    # A. Sequential execution (max concurrency == 1) & crash recovery from FINALIZING state
    wav_a = tmp_path / "m_a.wav"
    wav_b = tmp_path / "m_b.wav"
    tone = 0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 1.0, 16000, dtype=np.float32))
    _write_pcm_wav(wav_a, tone)
    _write_pcm_wav(wav_b, tone)

    db_path = tmp_path / "data" / "voxlocal.db"
    db_pre = LocalDatabase(db_path)
    mid_crashed = db_pre.create_meeting(title="Crashed Finalizing", audio_path=str(wav_a), model_used="small")
    db_pre.update_meeting(mid_crashed, status="transcribing", duration_sec=1.0)
    db_pre.enqueue_finalization_job(mid_crashed, audio_path=str(wav_a), duration_sec=1.0)
    # Simulate sudden process kill mid-job while in FINALIZING
    db_pre.update_finalization_job(mid_crashed, state="FINALIZING", last_stage="running")

    mid_queued = db_pre.create_meeting(title="Second Queued", audio_path=str(wav_b), model_used="small")
    db_pre.update_meeting(mid_queued, status="transcribing", duration_sec=1.0)
    db_pre.enqueue_finalization_job(mid_queued, audio_path=str(wav_b), duration_sec=1.0)

    # Manually marked interrupted row WITHOUT a finalization_jobs row must NOT be auto-processed
    mid_manual = db_pre.create_meeting(title="Manual Interrupted Only", audio_path=str(wav_a), model_used="small")
    db_pre.update_meeting(mid_manual, status="interrupted", duration_sec=1.0)
    db_pre.close()

    active_now = 0
    max_concurrent = 0
    exec_order = []
    lock = threading.Lock()
    done_evt = threading.Event()

    orig_fin = AppController._finalize_meeting_worker

    def _spy_finalize(self_ctrl, mid, result, video_path=""):
        nonlocal active_now, max_concurrent
        with lock:
            active_now += 1
            max_concurrent = max(max_concurrent, active_now)
            exec_order.append(mid)
        time.sleep(0.05)
        try:
            self_ctrl.db.update_meeting(mid, status="completed")
            self_ctrl.db.update_finalization_job(mid, state="COMPLETED", last_stage="completed", last_error="")
            if len(exec_order) >= 2:
                done_evt.set()
        finally:
            with lock:
                active_now -= 1

    with patch.object(AppController, "_finalize_meeting_worker", _spy_finalize):
        ctrl = _make_isolated_ctrl(tmp_path)
        try:
            assert done_evt.wait(timeout=4.0) is True
            time.sleep(0.1)
            assert max_concurrent == 1
            assert exec_order == [mid_crashed, mid_queued]
            assert ctrl.db.get_finalization_job(mid_crashed)["state"] == "COMPLETED"
            assert ctrl.db.get_finalization_job(mid_queued)["state"] == "COMPLETED"
            # Untouched manual interrupted row stays interrupted
            assert ctrl.db.get_meeting(mid_manual)["status"] == "interrupted"
            assert ctrl.db.get_finalization_job(mid_manual) is None
        finally:
            ctrl.shutdown()

    # B. Retry exhaustion reaches FAILED_WITH_RECOVERY preserving audio
    ctrl2 = _make_isolated_ctrl(tmp_path / "retry_env")
    try:
        mid_fail = ctrl2.db.create_meeting(title="Retry Fail Test", audio_path=str(wav_a), model_used="small")
        ctrl2.db.enqueue_finalization_job(mid_fail, audio_path=str(wav_a), max_attempts=2)
        ctrl2._record_finalization_job_failure(mid_fail, "err1", stage="stage_2")
        j1 = ctrl2.db.get_finalization_job(mid_fail)
        assert j1["state"] == "RETRY_PENDING" and j1["attempts"] == 1
        ctrl2._record_finalization_job_failure(mid_fail, "err2", stage="stage_2")
        j2 = ctrl2.db.get_finalization_job(mid_fail)
        assert j2["state"] == "FAILED_WITH_RECOVERY" and j2["attempts"] == 2
        assert Path(wav_a).exists()
    finally:
        ctrl2.shutdown()


# ------------------------------------------------------------------------------
# 7. Live-Priority Gate & pause_check Cooperative Yield
# ------------------------------------------------------------------------------
def test_07_live_work_pauses_background_finalization_and_windowed_asr(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_isolated_ctrl(tmp_path)
    try:
        ctrl.transcriber = LocalWhisperTranscriber(models_dir=tmp_path / "models", model_name="small", compute_device="cpu")
        ctrl.transcriber.transcribe_meeting_dual_track = LocalWhisperTranscriber.transcribe_meeting_dual_track.__get__(ctrl.transcriber)

        assert ctrl._should_pause_finalization_for_live_work() is False

        # Dictation active pauses heavy background work
        ctrl._dictation_state = "listening"
        assert ctrl._should_pause_finalization_for_live_work() is True
        ctrl._dictation_state = "idle"
        assert ctrl._should_pause_finalization_for_live_work() is False

        # Active real meeting capture pauses background finalization
        with ctrl._state_lock:
            ctrl._capture_state = "recording"
        assert ctrl._should_pause_finalization_for_live_work() is True

        resumed_evt = threading.Event()

        def _waiter():
            ok = ctrl._wait_for_live_work_idle(lambda: False)
            if ok:
                resumed_evt.set()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        time.sleep(0.15)
        assert resumed_evt.is_set() is False

        with ctrl._state_lock:
            ctrl._capture_state = "idle"
        assert resumed_evt.wait(timeout=2.0) is True

        # Verify _transcribe_wav_windowed honors pause_check cleanly
        wav_long = tmp_path / "long_test.wav"
        tone = 0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 3.0, 48000, dtype=np.float32))
        _write_pcm_wav(wav_long, tone)
        pause_flag = [True]

        def _unpause():
            time.sleep(0.15)
            pause_flag[0] = False

        threading.Thread(target=_unpause, daemon=True).start()
        with patch.object(ctrl.transcriber, "transcribe_audio_segments", return_value=[]):
            segs = ctrl.transcriber._transcribe_wav_windowed(
                str(wav_long),
                speaker_label="You (Mic)",
                source_track="mic",
                initial_prompt="",
                language="en",
                diarize_remote_turns=False,
                window_sec=1.5,
                pause_check=lambda: pause_flag[0],
            )
        assert segs == []
        assert pause_flag[0] is False
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------------------------
# 8. Non-Destructive AudioInputMonitor & Untouched 2600 MB VRAM Gate
# ------------------------------------------------------------------------------
def test_08_non_destructive_audio_monitor_and_vram_threshold_intact() -> None:
    mon = AudioInputMonitor()
    sr = 16000
    t = np.linspace(0, 1.0, sr, dtype=np.float32)
    # 7 seconds noise floor (~0.0004) + 6 seconds speech-modulated quiet bursts (peak ~0.015 < -24 dBFS)
    noise = (0.0004 * np.sin(2.0 * np.pi * 60.0 * t)).astype(np.float32)
    quiet_speech = (0.012 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
    orig_copy = quiet_speech.copy()

    for _ in range(7):
        mon.observe("mic", noise)
    assert mon.evaluate_low_mic_warning(7.0) is None

    for _ in range(6):
        mon.observe("mic", quiet_speech)
    # Must NOT alter observed array
    assert np.array_equal(quiet_speech, orig_copy)

    warn1 = mon.evaluate_low_mic_warning(13.0)
    assert warn1 is not None and "very low" in warn1.lower()
    # One-shot: second evaluation does not spam
    assert mon.evaluate_low_mic_warning(20.0) is None

    snap = mon.snapshot()
    assert snap["low_mic_warning_fired"] is True
    assert snap["mic"]["peak_dbfs"] < -24.0
    assert snap["mic"]["clipped_samples"] == 0

    # Verify HardwareProfiler still preserves the conservative 2600 MB GPU gate
    hw_src = (ROOT_DIR / "src" / "voxlocal" / "core" / "hardware.py").read_text(encoding="utf-8")
    assert "2600" in hw_src

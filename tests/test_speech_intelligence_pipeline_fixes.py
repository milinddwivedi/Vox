"""
Unit & Integration Tests for VOX Speech Intelligence Phase 1 Pipeline Fixes.

Validates deterministically (no model downloads, no mutation of user DB/recordings):
1. Empty initial_prompt + expanded hallucination filter rejecting prompt-echo phrases.
2. Devanagari -> Roman Hinglish initial-consonant schwa preservation & enterprise loanwords.
3. Strict {'en', 'hi'} bilingual language resolution (preventing ur/cy/ne/mr drift).
4. Domain hotword merging & propagation in bilingual modes.
5. Raw *_raw.wav preference + speaker_cluster vs calendar participant separation.
6. Streaming confidence from avg_logprob, ghost rejection, hotwords, and stop() tail flush.
7. _write_mixed() keeping mic_track.wav / system_track.wav untouched raw PCM on disk.
8. _finalize_meeting_worker always running full offline transcription, preserving *_raw.wav,
   avoiding duplicate Stage 2 DB writes, and freeing Whisper VRAM (< 1800 MB) before Stage 4 LLM.
9. LocalLLMEngine 1800 MB VRAM gate for CUDA vs CPU selection.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.audio.meeting_recorder import _write_mixed
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    clean_repetition_tail,
    is_hallucination,
    merge_domain_hotwords,
)


def _write_pcm_wav(path: Path, samples: np.ndarray, sr: int = TARGET_SAMPLE_RATE) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path.read_bytes()


def test_empty_prompt_and_hallucination_phrase_filter():
    assert DictationProcessor.DEFAULT_HINGLISH_PROMPT == ""
    assert DictationProcessor.build_whisper_prompt(
        [{"spoken_phrase": "in a cats", "replacement": "in caps", "is_active": 1}]
    ) == ""
    # Legacy prompt echoes are NOT in unconditional HALLUCINATION_PHRASES — they require contamination/acoustic evidence
    assert "tech team, sprint, daily scrum." not in DictationProcessor.HALLUCINATION_PHRASES
    assert "tech team, sprint, daily scrum." in DictationProcessor.KNOWN_LEGACY_PROMPT_ECHOES
    assert is_hallucination("Tech Team, Sprint, Daily Scrum.", initial_prompt="Tech Team, Sprint, Daily Scrum.")
    assert is_hallucination("World Fashion Exchange. Tech Team, Sprint, Daily Scrum.", no_speech_prob=0.80)
    assert not is_hallucination("Haan bataiye, WFX ERP me HubSpot check kar rahe hain.", duration_sec=3.2)


def test_legitimately_spoken_tech_team_sprint_daily_scrum_is_preserved():
    phrase = "Tech Team, Sprint, Daily Scrum."

    # 1. Legitimate utterance across DictationProcessor, is_hallucination, _filter_raw_segment_objects, and streaming
    processed = DictationProcessor.process(phrase, output_script="roman")
    assert "Tech Team" in processed and "Daily Scrum" in processed
    assert not is_hallucination(
        phrase,
        duration_sec=2.1,
        initial_prompt="",
        avg_logprob=-0.28,
        no_speech_prob=0.05,
        audio_rms=0.05,
    )

    genuine_seg = SimpleNamespace(
        start=0.0,
        end=2.1,
        text=phrase,
        avg_logprob=-0.28,
        no_speech_prob=0.05,
        compression_ratio=1.1,
    )
    kept = LocalWhisperTranscriber._filter_raw_segment_objects(
        [genuine_seg], initial_prompt="", audio_rms=0.05
    )
    assert len(kept) == 1 and "Daily Scrum" in kept[0].text

    mock_transcriber = MagicMock()
    mock_transcriber.is_model_loaded.return_value = True
    mock_transcriber._model = None
    mock_transcriber._transcribe_single_encode.return_value = (
        [SimpleNamespace(start=0.0, end=2.1, text=phrase, avg_logprob=-0.28, no_speech_prob=0.05)],
        SimpleNamespace(language="en", language_probability=0.95),
    )
    speech_audio = np.sin(2 * np.pi * 220 * np.arange(int(2.1 * TARGET_SAMPLE_RATE)) / TARGET_SAMPLE_RATE).astype(np.float32) * 0.08
    mock_rec = MagicMock()
    mock_rec.elapsed_seconds = 2.1
    mock_rec.get_audio_window.return_value = (speech_audio, np.zeros_like(speech_audio), 0.0)
    confirmed = []
    streamer = LiveMeetingStreamingTranscriber(
        transcriber=mock_transcriber,
        db=None,
        on_confirmed=lambda _mid, seg: confirmed.append(seg),
    )
    streamer._recorder = mock_rec
    streamer._meeting_id = 88
    streamer._language = "en+hi"
    streamer._execute_transcribe_pass(0.0, 2.1, "mic", "You (Mic)", speech_boundary=True)
    assert len(confirmed) == 1
    assert "Tech Team" in confirmed[0]["text"] and "Daily Scrum" in confirmed[0]["text"]

    # 2. Same phrase MUST be rejected when corroborated by prompt contamination or acoustic failure signals
    assert is_hallucination(phrase, duration_sec=2.1, initial_prompt="World Fashion Exchange. Tech Team, Sprint, Daily Scrum.")
    assert is_hallucination(phrase, duration_sec=2.1, initial_prompt="", avg_logprob=-0.28, no_speech_prob=0.80, audio_rms=0.05)
    assert is_hallucination(phrase, duration_sec=2.1, initial_prompt="", avg_logprob=-1.35, no_speech_prob=0.05, audio_rms=0.05)
    assert is_hallucination(phrase, duration_sec=2.1, initial_prompt="", avg_logprob=-0.28, no_speech_prob=0.05, audio_rms=0.001)
    assert is_hallucination(phrase, duration_sec=0.25, initial_prompt="", avg_logprob=-0.28, no_speech_prob=0.05, audio_rms=0.05)


def test_raw_model_score_stored_separately_and_uncalibrated():
    # Offline transcribe_audio_segments stores raw_model_score & confidence_calibrated=False
    t = LocalWhisperTranscriber()
    t._model = MagicMock()
    t._loaded_model_name = t.model_name
    aud = np.sin(2 * np.pi * 200 * np.arange(int(1.6 * TARGET_SAMPLE_RATE)) / TARGET_SAMPLE_RATE).astype(np.float32) * 0.08

    with patch.object(
        t,
        "_run_whisper_pass",
        return_value=(
            [SimpleNamespace(start=0.0, end=1.5, text="Haan ticket check karo", avg_logprob=-0.42, no_speech_prob=0.07, compression_ratio=1.1, words=None)],
            SimpleNamespace(language="hi", language_probability=0.90),
        ),
    ):
        segs = t.transcribe_audio_segments(aud, language="en+hi", word_timestamps=False)
    assert len(segs) == 1
    assert segs[0]["raw_model_score"] == pytest.approx(-0.42, abs=1e-4)
    assert segs[0]["no_speech_prob"] == pytest.approx(0.07, abs=1e-4)
    assert segs[0]["confidence_calibrated"] is False
    assert segs[0]["confidence"] != 0.95

    # Missing avg_logprob must NOT advertise fake 0.85/0.95 confidence
    with patch.object(
        t,
        "_run_whisper_pass",
        return_value=(
            [SimpleNamespace(start=0.0, end=1.5, text="No logprob segment", compression_ratio=1.1, words=None)],
            SimpleNamespace(language="en", language_probability=0.90),
        ),
    ):
        segs_no_lp = t.transcribe_audio_segments(aud, language="en", word_timestamps=False)
    assert len(segs_no_lp) == 1
    assert segs_no_lp[0]["raw_model_score"] is None
    assert segs_no_lp[0]["confidence"] == 0.0
    assert segs_no_lp[0]["confidence_calibrated"] is False

    # Streaming transcriber also exposes raw_model_score, no_speech_prob, confidence_calibrated=False
    mock_tr = MagicMock()
    mock_tr.is_model_loaded.return_value = True
    mock_tr._model = None
    mock_tr._transcribe_single_encode.return_value = (
        [SimpleNamespace(start=0.0, end=1.5, text="Streaming check done", avg_logprob=-0.35, no_speech_prob=0.04)],
        SimpleNamespace(language="en", language_probability=0.95),
    )
    mock_rec = MagicMock()
    mock_rec.elapsed_seconds = 1.6
    mock_rec.get_audio_window.return_value = (aud, np.zeros_like(aud), 0.0)
    confirmed = []
    streamer = LiveMeetingStreamingTranscriber(transcriber=mock_tr, db=None, on_confirmed=lambda _m, s: confirmed.append(s))
    streamer._recorder = mock_rec
    streamer._meeting_id = 99
    streamer._language = "en+hi"
    streamer._execute_transcribe_pass(0.0, 1.5, "mic", "You (Mic)", speech_boundary=True)
    assert len(confirmed) == 1
    assert confirmed[0]["raw_model_score"] == pytest.approx(-0.35, abs=1e-4)
    assert confirmed[0]["no_speech_prob"] == pytest.approx(0.04, abs=1e-4)
    assert confirmed[0]["confidence_calibrated"] is False
    assert confirmed[0]["confidence"] != 0.95


def test_devanagari_romanization_initial_schwa_and_loanwords():
    assert DictationProcessor.devanagari_to_roman_hinglish("बता") == "bata"
    assert DictationProcessor.devanagari_to_roman_hinglish("बताएंगे").lower().startswith("bata")
    assert DictationProcessor.devanagari_to_roman_hinglish("प्रकार") == "prakaar"
    assert DictationProcessor.devanagari_to_roman_hinglish("ज़रा") == "zara"
    assert DictationProcessor.devanagari_to_roman_hinglish("करते") == "karte"
    assert DictationProcessor.devanagari_to_roman_hinglish("कमरा") == "kamra"

    restored = DictationProcessor.devanagari_to_roman_hinglish(
        "पीएलएम सपोर्ट में कॉस्टिंग शीट और हबस्पॉट्स चेक करो।"
    )
    assert "PLM support" in restored
    assert "costing sheet" in restored
    assert "HubSpot" in restored


def test_bilingual_lang_resolution_strict_en_or_hi():
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="ur", all_language_probs=[("ur", 0.55), ("en", 0.12), ("hi", 0.10)])
        )
        == "hi"
    )
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="ne", all_language_probs=[("ne", 0.60), ("en", 0.15), ("hi", 0.05)])
        )
        == "hi"
    )
    assert (
        LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language="cy", all_language_probs=[("cy", 0.70), ("en", 0.25), ("hi", 0.02)])
        )
        == "en"
    )
    for bad_code in ("mr", "pa", "es", "cy", "ur", "ne"):
        out = LocalWhisperTranscriber._resolve_bilingual_lang(
            SimpleNamespace(language=bad_code, all_language_probs=[(bad_code, 0.50), ("en", 0.25), ("hi", 0.05)])
        )
        assert out in ("en", "hi")


def test_hotword_merging_for_bilingual_and_english():
    merged = merge_domain_hotwords("CustomClient, WFX, CustomClient")
    tokens = [t.strip() for t in merged.split(",")]
    assert tokens[0] == "CustomClient"
    assert sum(1 for t in tokens if t.lower() == "customclient") == 1
    assert sum(1 for t in tokens if t.lower() == "wfx") == 1
    assert "Daily Scrum" in merged

    t = LocalWhisperTranscriber()
    fake_model = MagicMock()
    t._model = fake_model
    t._loaded_model_name = t.model_name

    captured_hotwords = []

    def _fake_pass(norm_audio, language="en+hi", beam_size=1, vad_filter=False, vad_params=None, hotwords=None, **kw):
        captured_hotwords.append(hotwords)
        seg = SimpleNamespace(start=0.0, end=1.2, text="Haan WFX check kar lo.", avg_logprob=-0.25, no_speech_prob=0.01, compression_ratio=1.1, words=None)
        return [seg], SimpleNamespace(language="hi", language_probability=0.9)

    audio = np.ones(int(1.2 * TARGET_SAMPLE_RATE), dtype=np.float32) * 0.05
    with patch.object(t, "_run_whisper_pass", side_effect=_fake_pass):
        t.transcribe_audio(audio, language="en+hi", hotwords="MyHotword")
        t.transcribe_audio_segments(audio, language="en+hi", hotwords="MyHotword")

    assert len(captured_hotwords) == 2
    for hw in captured_hotwords:
        assert hw is not None and "MyHotword" in hw and "WFX" in hw


def test_dual_track_prefers_raw_wav_and_separates_clusters_vs_participant_names():
    t = LocalWhisperTranscriber()
    with tempfile.TemporaryDirectory(prefix="vox_dual_raw_") as td:
        tmp = Path(td)
        samples = np.sin(2 * np.pi * 220 * np.arange(TARGET_SAMPLE_RATE) / TARGET_SAMPLE_RATE).astype(np.float32) * 0.1
        mic_wav = tmp / "mic_track.wav"
        mic_raw = tmp / "mic_track_raw.wav"
        sys_wav = tmp / "system_track.wav"
        sys_raw = tmp / "system_track_raw.wav"
        mix_wav = tmp / "meeting_mixed.wav"
        _write_pcm_wav(mic_wav, samples)
        _write_pcm_wav(mic_raw, samples)
        _write_pcm_wav(sys_wav, samples)
        _write_pcm_wav(sys_raw, samples)
        _write_pcm_wav(mix_wav, samples)

        called_paths = []

        def _fake_windowed_single(wav_path, speaker_label="Speaker 1", source_track="mixed", **kw):
            called_paths.append(Path(wav_path).name)
            if source_track == "system":
                return [
                    {
                        "start_time": 0.5,
                        "end_time": 2.5,
                        "speaker": "Speaker 1",
                        "speaker_cluster": "Speaker 1",
                        "speaker_confidence": 0.9,
                        "source_track": "system",
                        "raw_text": "Hello Milind",
                        "text": "Hello Milind",
                        "confidence": 0.88,
                        "low_confidence": False,
                        "overlap": False,
                    }
                ]
            return [
                {
                    "start_time": 3.0,
                    "end_time": 4.5,
                    "speaker": "You (Mic)",
                    "source_track": "mic",
                    "raw_text": "Hi Hemanth",
                    "text": "Hi Hemanth",
                    "confidence": 0.90,
                    "low_confidence": False,
                    "overlap": False,
                }
            ]

        with patch.object(t, "_transcribe_wav_windowed", side_effect=_fake_windowed_single):
            segs_single = t.transcribe_meeting_dual_track(
                str(mix_wav),
                str(mic_wav),
                str(sys_wav),
                language="en+hi",
                remote_speaker_label="Hemanth Talathoti",
            )

        assert "system_track_raw.wav" in called_paths
        assert "mic_track_raw.wav" in called_paths
        sys_single = [s for s in segs_single if s["source_track"] == "system"][0]
        assert sys_single["speaker"] == "Hemanth Talathoti"
        assert sys_single["speaker_cluster"] == "Speaker 1"
        assert sys_single["participant_name"] == "Hemanth Talathoti"

        # Multi-speaker system track must NOT flatten Speaker 2 or overwrite clusters falsely
        def _fake_windowed_multi(wav_path, speaker_label="Speaker 1", source_track="mixed", **kw):
            if source_track == "system":
                return [
                    {
                        "start_time": 0.5,
                        "end_time": 2.0,
                        "speaker": "Speaker 1",
                        "speaker_cluster": "Speaker 1",
                        "speaker_confidence": 0.9,
                        "source_track": "system",
                        "raw_text": "First speaker.",
                        "text": "First speaker.",
                        "confidence": 0.85,
                        "low_confidence": False,
                        "overlap": False,
                    },
                    {
                        "start_time": 2.5,
                        "end_time": 4.0,
                        "speaker": "Speaker 2",
                        "speaker_cluster": "Speaker 2",
                        "speaker_confidence": 0.86,
                        "source_track": "system",
                        "raw_text": "Second speaker.",
                        "text": "Second speaker.",
                        "confidence": 0.84,
                        "low_confidence": False,
                        "overlap": False,
                    },
                ]
            return []

        with patch.object(t, "_transcribe_wav_windowed", side_effect=_fake_windowed_multi):
            segs_multi = t.transcribe_meeting_dual_track(
                str(mix_wav),
                str(mic_wav),
                str(sys_wav),
                language="en+hi",
                remote_speaker_label="Hemanth Talathoti",
            )
        speakers = [s["speaker"] for s in segs_multi]
        assert speakers == ["Speaker 1", "Speaker 2"]
        assert all(s["participant_name"] == "Hemanth Talathoti" for s in segs_multi)


def test_streaming_transcriber_confidence_hotwords_and_stop_flush():
    mock_transcriber = MagicMock()
    mock_transcriber.is_model_loaded.return_value = True
    mock_transcriber._model = None

    captured_calls = []

    def _fake_single(norm_audio, **kw):
        captured_calls.append(kw)
        # Emit first a ghost no_speech segment, then real segment on flush/pass
        text = kw.get("_test_text", "Haan WFX costing sheet check kar lo.")
        seg = SimpleNamespace(
            start=0.0,
            end=1.4,
            text=text,
            avg_logprob=-0.22,
            no_speech_prob=0.02,
        )
        return [seg], SimpleNamespace(language="hi", language_probability=0.92)

    mock_transcriber._transcribe_single_encode.side_effect = _fake_single

    mock_recorder = MagicMock()
    t_arr = np.sin(2 * np.pi * 180 * np.arange(int(2.0 * TARGET_SAMPLE_RATE)) / TARGET_SAMPLE_RATE).astype(np.float32) * 0.08
    silent_arr = np.zeros_like(t_arr)
    mock_recorder.elapsed_seconds = 1.6
    mock_recorder.get_audio_window.return_value = (t_arr, silent_arr, 0.0)

    confirmed_items = []
    mock_db = MagicMock()
    mock_db.list_vocabulary.return_value = [{"spoken_phrase": "w f x", "replacement": "WFX", "is_active": 1}]
    mock_db.add_transcript_segment.return_value = 42

    streamer = LiveMeetingStreamingTranscriber(
        transcriber=mock_transcriber,
        db=mock_db,
        on_confirmed=lambda mid, seg: confirmed_items.append(seg),
    )

    # 1. Ghost rejection check on low avg_logprob / high no_speech_prob
    streamer._recorder = mock_recorder
    streamer._meeting_id = 77
    streamer._language = "en+hi"
    mock_transcriber._transcribe_single_encode.side_effect = lambda norm_audio, **kw: (
        [SimpleNamespace(start=0.0, end=1.0, text="Hallucinated ghost", avg_logprob=-1.80, no_speech_prob=0.92)],
        SimpleNamespace(language="en", language_probability=0.9),
    )
    streamer._execute_transcribe_pass(0.0, 1.5, "mic", "You (Mic)", speech_boundary=True)
    assert len(confirmed_items) == 0

    # 2. Real segment computes real confidence (~0.80, NOT 0.95) with empty prompt & hotwords
    mock_transcriber._transcribe_single_encode.side_effect = _fake_single
    streamer._execute_transcribe_pass(0.0, 1.6, "mic", "You (Mic)", speech_boundary=True)
    assert len(confirmed_items) == 1
    assert abs(confirmed_items[0]["confidence"] - 0.80) <= 0.02
    assert captured_calls[-1]["effective_prompt"] is None
    assert captured_calls[-1]["hotwords"] and "WFX" in captured_calls[-1]["hotwords"]

    # 3. stop() flushes pending tail speech even after _stop_event is set
    streamer._active = True
    streamer._stop_event.clear()
    streamer._audio_cursor_sec = 0.0
    streamer._speech_active = True
    streamer._current_track = "mic"
    streamer._current_speaker = "You (Mic)"
    mock_transcriber._transcribe_single_encode.side_effect = lambda norm_audio, **kw: (
        [SimpleNamespace(start=0.0, end=1.2, text="Final tail sentence before stop.", avg_logprob=-0.18, no_speech_prob=0.01)],
        SimpleNamespace(language="en", language_probability=0.95),
    )
    streamer.stop()
    assert any("Final tail sentence before stop" in c["text"] for c in confirmed_items)


def test_write_mixed_preserves_raw_tracks_on_disk():
    with tempfile.TemporaryDirectory(prefix="vox_raw_mix_") as td:
        tmp = Path(td)
        sr = TARGET_SAMPLE_RATE
        t = np.linspace(0, 1.5, int(1.5 * sr), endpoint=False, dtype=np.float32)
        mic_pcm = (0.08 * np.sin(2 * np.pi * 240 * t) + 0.01 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
        sys_pcm = (0.04 * np.sin(2 * np.pi * 320 * t)).astype(np.float32)

        mic_path = tmp / "mic_track.wav"
        sys_path = tmp / "system_track.wav"
        mixed_path = tmp / "meeting_mixed.wav"
        orig_mic_bytes = _write_pcm_wav(mic_path, mic_pcm)
        orig_sys_bytes = _write_pcm_wav(sys_path, sys_pcm)

        dur = _write_mixed(mic_path, sys_path, mixed_path)
        assert dur >= 1.4

        # Original mic_track.wav and system_track.wav MUST remain byte-for-byte untouched raw audio
        assert hashlib.sha256(mic_path.read_bytes()).hexdigest() == hashlib.sha256(orig_mic_bytes).hexdigest()
        assert hashlib.sha256(sys_path.read_bytes()).hexdigest() == hashlib.sha256(orig_sys_bytes).hexdigest()
        assert (tmp / "mic_track_raw.wav").exists()
        assert (tmp / "system_track_raw.wav").exists()
        assert mixed_path.exists() and mixed_path.stat().st_size > 44


def test_finalize_worker_always_runs_offline_and_preserves_raw_wavs_and_vram():
    with tempfile.TemporaryDirectory(prefix="vox_ctrl_fin_") as td:
        tmp = Path(td)
        cfg = AppConfig(storage_dir=str(tmp), auto_record_calls=False, prompt_on_call=False)
        cfg.disable_model_preload = True
        cfg.disable_hotkeys = True

        with patch("voxlocal.core.controller.WindowFocusTracker"), \
             patch("voxlocal.core.controller.TextInserter"), \
             patch("voxlocal.core.controller.GlobalHotkeyManager"), \
             patch("voxlocal.core.controller.CallDetector"):
            ctrl = AppController(cfg)
            try:
                rec_dir = tmp / "recordings" / "meeting_test"
                rec_dir.mkdir(parents=True, exist_ok=True)
                samples = np.sin(2 * np.pi * 220 * np.arange(TARGET_SAMPLE_RATE) / TARGET_SAMPLE_RATE).astype(np.float32) * 0.1
                mic_wav = rec_dir / "mic_track.wav"
                sys_wav = rec_dir / "system_track.wav"
                mic_raw = rec_dir / "mic_track_raw.wav"
                sys_raw = rec_dir / "system_track_raw.wav"
                mix_wav = rec_dir / "meeting_mixed.wav"
                for p in (mic_wav, sys_wav, mic_raw, sys_raw, mix_wav):
                    _write_pcm_wav(p, samples)

                m_id = ctrl.db.create_meeting(title="Hemanth Talathoti", capture_mode="both")
                # Seed >= 2 live segments covering 90% (> 70%) to verify offline Whisper pass is NOT skipped
                ctrl.db.add_transcript_segment(m_id, 1, 1.0, 45.0, "You (Mic)", "Old live seg 1", "mic", confidence=0.95)
                ctrl.db.add_transcript_segment(m_id, 2, 46.0, 92.0, "Hemanth Talathoti", "Old live seg 2", "system", confidence=0.95)

                offline_segs = [
                    {
                        "start_time": 1.0,
                        "end_time": 4.0,
                        "speaker": "Hemanth Talathoti",
                        "speaker_cluster": "Speaker 1",
                        "source_track": "system",
                        "raw_text": "Clean offline Whisper pass",
                        "text": "Clean offline Whisper pass",
                        "confidence": 0.91,
                    }
                ]

                ctrl.transcriber._active_device = "cuda"
                ctrl.transcriber._model = object()
                ctrl.transcriber.unload_model = MagicMock()
                ctrl.transcriber.transcribe_meeting_dual_track = MagicMock(return_value=offline_segs)
                ctrl.transcriber.reconcile_meeting_transcript = MagicMock(return_value=offline_segs)
                ctrl.llm_engine._server._get_free_vram_mb = MagicMock(return_value=1200)

                replace_spy = MagicMock(wraps=ctrl.db.replace_meeting_segments)
                ctrl.db.replace_meeting_segments = replace_spy

                saved_res = SimpleNamespace(
                    duration_sec=100.0,
                    mixed_wav_path=str(mix_wav),
                    mic_wav_path=str(mic_wav),
                    sys_wav_path=str(sys_wav),
                    mic_device_name="Mic",
                    loopback_device_name="Loopback",
                )

                with patch(
                    "voxlocal.core.controller.LocalMeetingSummarizer.analyze_full",
                    return_value={"mom_markdown": "Summary", "engagement_score": 90, "topics": ["WFX"], "decisions": [], "action_items": []},
                ):
                    ctrl._finalize_meeting_worker(m_id, saved_res, "")

                ctrl.transcriber.transcribe_meeting_dual_track.assert_called_once()
                _, dt_kwargs = ctrl.transcriber.transcribe_meeting_dual_track.call_args
                assert dt_kwargs.get("initial_prompt") == ""
                assert dt_kwargs.get("hotwords")

                ctrl.transcriber.reconcile_meeting_transcript.assert_called_once()
                assert replace_spy.call_count == 2  # once after Stage 2, once after Stage 3 (no duplicate)
                assert mic_raw.exists() and sys_raw.exists()
                ctrl.transcriber.unload_model.assert_called_once()
            finally:
                ctrl.shutdown()


def test_llm_engine_vram_threshold_1800mb():
    with tempfile.TemporaryDirectory(prefix="vox_llm_vram_") as td:
        mdir = Path(td) / "model"
        mdir.mkdir(parents=True, exist_ok=True)
        model_file = mdir / LocalLLMEngine.DEFAULT_MODEL
        model_file.write_bytes(b"GGUF" + b"\x00" * 1024)

        def _fake_resolve(prefer_gpu: bool = True):
            return (Path("fake_vulkan.exe"), "vulkan") if prefer_gpu else (Path("fake_cpu.exe"), "cpu")

        engine_low = LocalLLMEngine(model_dir=model_file, device="auto")
        low_variants = []

        def _launch_low(bin_path, variant):
            low_variants.append(variant)
            engine_low._server._active_device = "cpu"
            fake_p = MagicMock()
            fake_p.poll.return_value = None
            engine_low._server._proc = fake_p
            return True, "ok"

        try:
            with patch.object(engine_low._server, "_get_free_vram_mb", return_value=1500), \
                 patch.object(engine_low._server, "_get_memory_mb", return_value=(16384, 8192)), \
                 patch("voxlocal.engine.llama_server_engine.resolve_llama_server_bin", side_effect=_fake_resolve), \
                 patch.object(engine_low._server, "_launch_variant", side_effect=_launch_low):
                assert engine_low._server.ensure_started()
                assert low_variants == ["cpu"]
                assert engine_low._server.active_device == "cpu"
        finally:
            engine_low._server._proc = None

        engine_hi = LocalLLMEngine(model_dir=model_file, device="auto")
        hi_variants = []

        def _launch_hi(bin_path, variant):
            hi_variants.append(variant)
            engine_hi._server._active_device = "vulkan_gpu"
            fake_p = MagicMock()
            fake_p.poll.return_value = None
            engine_hi._server._proc = fake_p
            return True, "ok"

        try:
            with patch.object(engine_hi._server, "_get_free_vram_mb", return_value=2200), \
                 patch.object(engine_hi._server, "_get_memory_mb", return_value=(16384, 8192)), \
                 patch("voxlocal.engine.llama_server_engine.resolve_llama_server_bin", side_effect=_fake_resolve), \
                 patch.object(engine_hi._server, "_launch_variant", side_effect=_launch_hi):
                assert engine_hi._server.ensure_started()
                assert hi_variants == ["vulkan"]
                assert engine_hi._server.active_device == "vulkan_gpu"
        finally:
            engine_hi._server._proc = None


def test_hindi_decode_receives_null_hotwords_while_english_gets_hotwords():
    t = LocalWhisperTranscriber()
    calls = []

    def _fake_transcribe(audio, language=None, hotwords=None, **kw):
        calls.append((language, hotwords))
        info = SimpleNamespace(language="hi" if language is None else language, language_probability=0.9, all_language_probs=[("hi", 0.7), ("en", 0.2)])
        seg = SimpleNamespace(start=0.0, end=1.0, text="यह चेक कर लो।", avg_logprob=-0.2)
        return [seg], info

    t._model = MagicMock()
    t._model.transcribe.side_effect = _fake_transcribe
    t._loaded_model_name = t.model_name

    aud = np.ones(int(1.0 * TARGET_SAMPLE_RATE), dtype=np.float32) * 0.05
    t._run_whisper_pass(aud, language="hi", beam_size=2, vad_filter=False, vad_params={}, hotwords="WFX, HubSpot")
    assert calls[-1] == ("hi", None)

    t._run_whisper_pass(aud, language="en", beam_size=2, vad_filter=False, vad_params={}, hotwords="WFX, HubSpot")
    assert calls[-1][0] == "en" and calls[-1][1] == "WFX, HubSpot"


def test_perso_arabic_cleanup_and_upspot_fix():
    out = DictationProcessor.devanagari_to_roman_hinglish("to mera aگr login nahi hua")
    assert out == "to mera agar login nahi hua"
    assert not any("\u0600" <= ch <= "\u06FF" for ch in out)
    proc = DictationProcessor.process("to mera aگr Upspot login nahi hua", output_script="roman")
    assert "agar" in proc
    assert "HubSpot" in proc
    assert not any("\u0600" <= ch <= "\u06FF" for ch in proc)


def test_counting_and_clause_repetition_guards():
    assert is_hallucination("One, two, three, four, five, six, seven, eight, nine, ten, ten.")
    cleaned = clean_repetition_tail("Oh, I forgot about it. Oh my God. Oh my god. Oh my gosh. Oh my goodness. Oh my.")
    assert cleaned == "Oh, I forgot about it."


def test_build_packed_vad_batches_packs_and_preserves_timestamps():
    sr = TARGET_SAMPLE_RATE
    audio = np.zeros(int(10.0 * sr), dtype=np.float32)
    t1 = np.arange(int(1.2 * sr))
    audio[int(0.5 * sr) : int(1.7 * sr)] = 0.2 * np.sin(2 * np.pi * 220 * t1 / sr)
    audio[int(3.0 * sr) : int(4.2 * sr)] = 0.2 * np.sin(2 * np.pi * 260 * t1 / sr)
    fake_spans = [
        {"start": int(0.5 * sr), "end": int(1.7 * sr)},
        {"start": int(3.0 * sr), "end": int(4.2 * sr)},
    ]
    with patch("faster_whisper.vad.get_speech_timestamps", return_value=fake_spans):
        batches = LocalWhisperTranscriber._build_packed_vad_batches(audio, {}, max_speech_sec=24.0, max_gap_sec=6.0)
    assert batches is not None and len(batches) == 1
    batch_aud, batch_spans = batches[0]
    assert len(batch_spans) == 2
    assert batch_spans[0]["start"] < batch_spans[0]["end"] < batch_spans[1]["start"]
    expected_len = (batch_spans[0]["end"] - batch_spans[0]["start"]) + (batch_spans[1]["end"] - batch_spans[1]["start"])
    assert batch_aud.size == expected_len

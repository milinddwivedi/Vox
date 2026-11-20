"""
Unit & Integration Tests for Audio-Synchronized Ground-Truth Review Workbench
and Strict Verified-Only Ground-Truth Scorer.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import sys
import tempfile
import threading
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from review_ground_truth import (
    GroundTruthStore,
    compute_overlapping_indices,
    extract_wav_slice_bytes,
    make_request_handler,
)
from score_ground_truth import audit_mixed_script_and_utf8, score_predictions_against_reference


def _write_test_wav(path: Path, dur_sec: float = 6.0, sr: int = 16000) -> str:
    n = int(dur_sec * sr)
    t = np.arange(n, dtype=np.float32) / float(sr)
    pcm = (np.sin(2.0 * np.pi * 220.0 * t) * 10000.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wav_slice_extraction_is_read_only_and_exact_duration():
    with tempfile.TemporaryDirectory(prefix="vox_gt_wav_") as td:
        wav_path = Path(td) / "mic_track.wav"
        orig_sha = _write_test_wav(wav_path, dur_sec=6.0, sr=16000)

        wav_bytes = extract_wav_slice_bytes(wav_path, start_sec=1.0, end_sec=3.0, pad_sec=0.25)
        assert hashlib.sha256(wav_path.read_bytes()).hexdigest() == orig_sha

        with wave.open(io.BytesIO(wav_bytes), "rb") as rf:
            assert rf.getnchannels() == 1
            assert rf.getframerate() == 16000
            slice_dur = rf.getnframes() / float(rf.getframerate())
            assert slice_dur == pytest.approx(2.5, abs=0.02)


def test_review_workbench_http_validation_and_immutability():
    with tempfile.TemporaryDirectory(prefix="vox_gt_srv_") as td:
        tmp = Path(td)
        mic_wav = tmp / "mic_track.wav"
        sys_wav = tmp / "system_track.wav"
        mix_wav = tmp / "meeting_mixed.wav"
        sha_mic = _write_test_wav(mic_wav, dur_sec=8.0)
        sha_sys = _write_test_wav(sys_wav, dur_sec=8.0)
        sha_mix = _write_test_wav(mix_wav, dur_sec=8.0)

        draft_path = tmp / "draft_234.json"
        draft_payload = {
            "meeting_id": 234,
            "title": "Hemanth Kumar Talathoti",
            "duration_sec": 8.0,
            "segments": [
                {
                    "segment_index": 0,
                    "start_time": 0.5,
                    "end_time": 3.0,
                    "source_track": "mic",
                    "speaker": "You (Mic)",
                    "speaker_cluster": "You (Mic)",
                    "raw_text": "hello",
                    "display_text": "Hello",
                    "baseline_db_text": "Hello",
                    "reference_text": "Hello Hemanth",
                    "verified_by_human": False,
                    "notes": "",
                },
                {
                    "segment_index": 1,
                    "start_time": 2.4,
                    "end_time": 5.0,
                    "source_track": "system",
                    "speaker": "Speaker 1",
                    "speaker_cluster": "Speaker 1",
                    "raw_text": "hi",
                    "display_text": "Hi",
                    "baseline_db_text": "Hi",
                    "reference_text": "Hi Milind",
                    "verified_by_human": False,
                    "notes": "",
                },
            ],
        }
        draft_path.write_text(json.dumps(draft_payload), encoding="utf-8")

        specs = {
            234: {
                "meeting_id": 234,
                "title": "Hemanth Kumar Talathoti",
                "draft_path": draft_path,
                "audio_dir": tmp,
                "default_speakers": ["You (Mic)", "Hemanth Kumar Talathoti"],
            }
        }
        store = GroundTruthStore(specs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_request_handler(store))
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            # 1. GET /api/meeting/234 detects overlap cleanly
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meeting/234") as resp:
                assert resp.status == 200
                mtg = json.loads(resp.read().decode("utf-8"))
            assert mtg["segments"][0]["overlaps_with"] == [1]
            assert mtg["segments"][1]["overlaps_with"] == [0]

            # 2. GET /api/audio returns valid WAV bytes without mutating WAV files
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/audio?meeting_id=234&track=mic&start=0.5&end=3.0&pad=0.35"
            ) as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Type") == "audio/wav"
                wav_data = resp.read()
            assert wav_data[:4] == b"RIFF"
            assert hashlib.sha256(mic_wav.read_bytes()).hexdigest() == sha_mic
            assert hashlib.sha256(sys_wav.read_bytes()).hexdigest() == sha_sys
            assert hashlib.sha256(mix_wav.read_bytes()).hexdigest() == sha_mix

            # 3. POST verified_by_human=True without audio_listened=True MUST fail HTTP 400
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/meeting/234/segment/0",
                body=json.dumps({"verified_by_human": True, "audio_listened": False}),
                headers={"Content-Type": "application/json"},
            )
            r_bad = conn.getresponse()
            r_bad.read()
            assert r_bad.status == 400

            # 4. POST with audio_listened=True succeeds and preserves timestamps/source_track
            conn.request(
                "POST",
                "/api/meeting/234/segment/0",
                body=json.dumps(
                    {
                        "reference_text": "Hello Hemanth check WFX.",
                        "speaker": "You (Mic)",
                        "language_tags": ["en", "hinglish"],
                        "uncertain": False,
                        "notes": "Verified",
                        "audio_listened": True,
                        "verified_by_human": True,
                        "start_time": 999.0,  # attempted tamper must be ignored
                        "source_track": "tampered",
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            r_ok = conn.getresponse()
            body_ok = json.loads(r_ok.read().decode("utf-8"))
            assert r_ok.status == 200
            seg0 = body_ok["segment"]
            assert seg0["verified_by_human"] is True
            assert seg0["audio_listened"] is True
            assert seg0["start_time"] == 0.5
            assert seg0["end_time"] == 3.0
            assert seg0["source_track"] == "mic"
            assert seg0["language_tags"] == ["en", "hinglish"]
            assert seg0["overlaps_with"] == [1]
        finally:
            server.shutdown()
            server.server_close()


def test_scorer_refuses_unverified_references():
    unverified_ref = {
        "meeting_id": 234,
        "title": "Hemanth Kumar Talathoti",
        "segments": [
            {
                "segment_index": 0,
                "start_time": 0.0,
                "end_time": 2.0,
                "speaker": "You (Mic)",
                "reference_text": "Hello WFX",
                "verified_by_human": False,
            }
        ],
    }
    preds = [{"start_time": 0.0, "end_time": 2.0, "speaker": "You (Mic)", "text": "Hello WFX"}]
    res = score_predictions_against_reference(unverified_ref, preds)
    assert res["status"] == "awaiting_human_verification"
    assert res["verified_segment_count"] == 0
    assert res["english_wer"] is None
    assert res["hinglish_norm_wer"] is None
    assert res["speaker_der"] is None


def test_scorer_computes_exact_wer_cer_entities_diarization_and_word_ts():
    ref = {
        "meeting_id": 234,
        "title": "Test",
        "segments": [
            {
                "segment_index": 0,
                "start_time": 0.0,
                "end_time": 2.0,
                "speaker": "You (Mic)",
                "language_tags": ["en"],
                "reference_text": "Check WFX PLM status",
                "verified_by_human": True,
                "words": [
                    {"word": "Check", "start": 0.10, "end": 0.40},
                    {"word": "WFX", "start": 0.50, "end": 0.90},
                    {"word": "PLM", "start": 1.00, "end": 1.40},
                    {"word": "status", "start": 1.50, "end": 1.90},
                ],
            },
            {
                "segment_index": 1,
                "start_time": 2.0,
                "end_time": 4.0,
                "speaker": "Hemanth",
                "language_tags": ["hinglish", "code_switch"],
                "reference_text": "Haan phir [?muffled?] HubSpot check karo",
                "uncertain": True,
                "verified_by_human": True,
                "words": [
                    {"word": "Haan", "start": 2.10, "end": 2.40},
                    {"word": "HubSpot", "start": 3.00, "end": 3.50},
                ],
            },
        ],
    }
    # Prediction:
    # - Seg 0 matches "Check WFX PLM status" cleanly, with slight 50ms timestamp shift -> ~0.0 english_wer
    # - Seg 1 uses Hinglish variant "Han fir something HubSpot check karo" (Han/fir fold -> han/fir vs haan/phir, "something" matches [?muffled?] at 0 penalty!)
    #   + speaker label "Speaker 1" mapped optimally to "Hemanth"
    # - Extra silence insertion at [5.0, 6.0] ("thank you guys") to verify hallucination rate!
    preds = [
        {
            "start_time": 0.0,
            "end_time": 2.0,
            "speaker": "You (Mic)",
            "text": "Check WFX PLM status",
            "words": [
                {"word": "Check", "start": 0.15, "end": 0.45},
                {"word": "WFX", "start": 0.55, "end": 0.95},
                {"word": "PLM", "start": 1.05, "end": 1.45},
                {"word": "status", "start": 1.55, "end": 1.95},
            ],
        },
        {
            "start_time": 2.0,
            "end_time": 4.0,
            "speaker": "Speaker 1",
            "text": "Han fir something HubSpot check karo",
            "words": [
                {"word": "Han", "start": 2.15, "end": 2.45},
                {"word": "HubSpot", "start": 3.05, "end": 3.55},
            ],
        },
        {
            "start_time": 5.0,
            "end_time": 6.0,
            "speaker": "Speaker 1",
            "text": "extra ghost extra ghost extra ghost",
        },
    ]

    res = score_predictions_against_reference(ref, preds)
    assert res["status"] == "scored"
    assert res["verified_segment_count"] == 2
    assert res["english_wer"] == 0.0
    assert res["hinglish_norm_wer"] == 0.0
    assert res["hinglish_norm_cer"] == 0.0
    # Strict spelling ("Han fir" != "Haan phir") has non-zero raw_wer
    assert res["raw_wer"] > 0.0
    assert res["hindi_code_switch_wer"] == 0.0
    assert res["uncertain_segment_count"] == 1
    assert res["uncertain_token_count"] == 1
    assert res["technical_entity_recall"] == 1.0
    assert res["technical_entity_precision"] == 1.0
    # 6 silence-insertion words ("extra ghost" x 3) + consecutive 2-word repetition
    assert res["hallucination_breakdown"]["silence_insertion_words"] == 6
    assert res["hallucination_and_duplication_rate"] > 0.3

    # Also verify omitted [?muffled?] span gives 0.0 WER/CER cleanly
    preds_omitted = [dict(preds[0]), dict(preds[1], text="Han fir HubSpot check karo")]
    res_omitted = score_predictions_against_reference(ref, preds_omitted)
    assert res_omitted["hinglish_norm_wer"] == 0.0
    assert res_omitted["hinglish_norm_cer"] == 0.0
    # Missed=0, Confusion=0, False Alarm=1.0s over 4.0s ref -> DER = 0.25, cluster attribution = 100%
    assert res["speaker_der"] == pytest.approx(0.25, abs=0.01)
    assert res["speaker_jer"] == pytest.approx(0.1667, abs=0.02)
    assert res["speaker_attribution_accuracy"] == 1.0
    assert res["word_timestamp_error_ms"] is not None
    assert res["word_timestamp_error_ms"]["matched_words"] == 6
    assert res["word_timestamp_error_ms"]["median_ms"] == pytest.approx(50.0, abs=1.0)


def test_scorer_isolates_overlapping_mic_and_system_tracks_and_computes_stream_wer():
    ref = {
        "meeting_id": 234,
        "title": "Dual Track Overlap Test",
        "segments": [
            {
                "segment_index": 0,
                "start_time": 0.0,
                "end_time": 5.0,
                "source_track": "mic",
                "speaker": "You (Mic)",
                "language_tags": ["hinglish"],
                "reference_text": "Maine HubSpot pe check kiya",
                "verified_by_human": True,
            },
            {
                "segment_index": 1,
                "start_time": 5.0,
                "end_time": 10.0,
                "source_track": "mic",
                "speaker": "You (Mic)",
                "language_tags": ["hinglish"],
                "reference_text": "aur PLM ticket assign kiya",
                "verified_by_human": True,
            },
            {
                "segment_index": 2,
                "start_time": 2.0,
                "end_time": 8.0,
                "source_track": "system",
                "speaker": "Hemanth",
                "language_tags": ["en"],
                "reference_text": "Okay please share WFX screen",
                "verified_by_human": True,
            },
        ],
    }
    # Mic prediction spans 0.0-10.0s (6.0s overlap with system 2.0-8.0s > 5.0s overlap with either mic half).
    # Track isolation must keep mic out of system, and stream WER must eliminate the 5s split boundary.
    preds = [
        {
            "start_time": 0.0,
            "end_time": 10.0,
            "source_track": "mic",
            "speaker": "You (Mic)",
            "text": "Maine HubSpot pe check kiya aur PLM ticket assign kiya",
        },
        {
            "start_time": 2.0,
            "end_time": 8.0,
            "source_track": "system",
            "speaker": "Speaker 1",
            "text": "Okay please share WFX screen",
        },
    ]
    res = score_predictions_against_reference(ref, preds)
    assert res["status"] == "scored"
    assert res["english_wer"] == 0.0
    assert res["stream_raw_wer"] == 0.0
    assert res["stream_hinglish_norm_wer"] == 0.0
    assert res["stream_hinglish_norm_cer"] == 0.0
    assert res["track_stream_breakdown"]["mic"]["hinglish_norm_wer"] == 0.0
    assert res["track_stream_breakdown"]["system"]["hinglish_norm_wer"] == 0.0
    assert res["stream_technical_entity_recall"] == 1.0
    assert res["stream_technical_entity_precision"] == 1.0


def test_dual_views_uncertain_masking_empty_ref_audit_and_ops():
    ref = {
        "meeting_id": 235,
        "title": "Dual View Test",
        "segments": [
            {
                "segment_index": 0,
                "start_time": 0.0,
                "end_time": 4.0,
                "source_track": "mic",
                "speaker": "You (Mic)",
                "language_tags": ["en"],
                "reference_text": "Open WFX now right away",
                "uncertain": False,
                "verified_by_human": True,
            },
            {
                "segment_index": 1,
                "start_time": 5.0,
                "end_time": 8.0,
                "source_track": "system",
                "speaker": "Rumjhum.R",
                "language_tags": ["hinglish"],
                "reference_text": "achha ohh che",
                "uncertain": True,
                "verified_by_human": True,
            },
            {
                "segment_index": 2,
                "start_time": 10.0,
                "end_time": 12.0,
                "source_track": "mic",
                "speaker": "You (Mic)",
                "language_tags": [],
                "reference_text": "",
                "uncertain": True,
                "verified_by_human": True,
            },
        ],
    }
    # Mic seg 0 -> 1 sub ("Open" -> "Bad"), 1 del ("now"), 2-word anchor "right away"
    # System seg 1 (uncertain=True, non-empty) -> completely wrong prediction -> masked in View A, scored in View B!
    # Mic seg 2 (empty reference) -> hallucinated "ghost hallucination" (2 insertions) -> penalized in BOTH View A & View B!
    preds = [
        {
            "start_time": 0.0,
            "end_time": 4.0,
            "source_track": "mic",
            "speaker": "You (Mic)",
            "text": "Bad WFX right away",
        },
        {
            "start_time": 5.0,
            "end_time": 8.0,
            "source_track": "system",
            "speaker": "Speaker 1",
            "text": "completely wrong noise words here",
        },
        {
            "start_time": 10.0,
            "end_time": 12.0,
            "source_track": "mic",
            "speaker": "You (Mic)",
            "text": "ghost hallucination",
        },
    ]
    res = score_predictions_against_reference(ref, preds)
    va = res["primary_masked_uncertain"]
    vb = res["secondary_unmasked_complete"]

    # View A masks segment 1 (uncertain non-empty), retains segment 0 (5 ref words -> 1 sub, 1 del) + empty segment 2 (2 ins)
    assert va["masked_whole_segment_indices"] == [1]
    assert va["scored_segment_count"] == 2
    assert va["empty_ref_segment_count"] == 1
    assert va["empty_ref_insertion_words"] == 2
    assert va["stream_norm_ops"] == {
        "substitutions": 1,
        "deletions": 1,
        "insertions": 2,
        "ref_words": 5,
        "pred_words": 6,
    }
    assert va["stream_hinglish_norm_wer"] == pytest.approx(4.0 / 5.0, abs=1e-4)  # (1+1+2)/5 = 0.8
    assert va["hallucination_and_duplication_rate"] == pytest.approx(2.0 / 6.0, abs=1e-4)

    # View B scores ALL 3 segments unmasked (5 + 3 = 8 ref words), including the 4 wrong words on segment 1
    assert vb["masked_whole_segment_indices"] == []
    assert vb["scored_segment_count"] == 3
    assert vb["empty_ref_insertion_words"] == 2
    assert vb["stream_norm_ops"]["ref_words"] == 8
    assert vb["stream_hinglish_norm_wer"] > va["stream_hinglish_norm_wer"]
    assert res["empty_reference_audit"]["empty_ref_indices"] == [2]
    assert res["empty_reference_audit"]["empty_ref_insertion_words"] == 2


def test_orthography_aware_mixed_devanagari_and_latin_scoring():
    ref = {
        "meeting_id": 999,
        "title": "Mixed Orthography Test",
        "segments": [
            {
                "segment_index": 0,
                "start_time": 0.0,
                "end_time": 4.0,
                "source_track": "mic",
                "speaker": "You (Mic)",
                "language_tags": ["hinglish"],
                "reference_text": "mai bahut soch raha tha ki hubspot ka kya karun",
                "uncertain": False,
                "verified_by_human": True,
            }
        ],
    }
    preds = [
        {
            "start_time": 0.0,
            "end_time": 4.0,
            "source_track": "mic",
            "speaker": "You (Mic)",
            "raw_text": "मैं बहुत सोच रहा था कि HubSpot का क्या करूं",
            "text": "मैं बहुत सोच रहा था कि HubSpot का क्या करूं",
        }
    ]
    res = score_predictions_against_reference(ref, preds)
    assert res["status"] == "scored"
    assert res["stream_hinglish_norm_wer"] == 0.0
    assert res["stream_raw_wer"] > 0.5
    assert res["stream_technical_entity_recall"] == 1.0
    assert res["stream_technical_entity_precision"] == 1.0

    audit = audit_mixed_script_and_utf8(
        preds
        + [
            {"raw_text": "bye bye \ufffd 你好"},
        ]
    )
    assert audit["devanagari_words"] == 9
    assert audit["latin_words"] == 3
    assert audit["cjk_segs"] == 1
    assert audit["ufffd_segs"] == 1

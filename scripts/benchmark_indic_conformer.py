"""
Phase 2 Challenger #5 Benchmark: AI4Bharat IndicConformer INT8 ONNX via `sherpa-onnx` (CPU-only, 0 VRAM).

Evaluates FULL M#234 (90.15s) and FULL M#235 (1129.00s) strictly read-only,
saving both raw Devanagari outputs and Romanized Hinglish normalized outputs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import sherpa_onnx

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_speech_intelligence import (
    BENCH_DIR,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    read_only_db_state,
    snapshot_protected_files,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

MODEL_DIR = ROOT_DIR / "data" / "models" / "challengers" / "indic-conformer-sherpa"
OUT_DIR = BENCH_DIR / "phase2_challengers"

DEVA_RE = re.compile(r"[\u0900-\u097F]")
BENGALI_AS_RE = re.compile(r"[\u0980-\u09FF]")
OTHER_INDIC_RE = re.compile(r"[\u0A00-\u0D7F]")


def build_recognizer(num_threads: int = 4) -> sherpa_onnx.OfflineRecognizer:
    model_path = MODEL_DIR / "model.int8.onnx"
    tokens_path = MODEL_DIR / "tokens.txt"
    if not model_path.exists() or not tokens_path.exists():
        raise FileNotFoundError(f"IndicConformer files missing in {MODEL_DIR}")
    return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=str(model_path),
        tokens=str(tokens_path),
        num_threads=num_threads,
        sample_rate=TARGET_SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
        provider="cpu",
    )


def transcribe_track_with_indic_conformer(
    recognizer: sherpa_onnx.OfflineRecognizer,
    audio_16k: np.ndarray,
    source_track: str,
    speaker: str,
    vocab: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
        audio_16k, vad_p, max_chunk_sec=20.0, merge_gap_sec=0.65, pad_sec=0.20
    )
    if spans is None:
        spans = [(0, audio_16k.size)]

    segments: List[Dict[str, Any]] = []
    for s_i, e_i in spans:
        sl = audio_16k[s_i:e_i]
        if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
            continue
        norm_sl = normalize_speech_audio(sl)
        st = recognizer.create_stream()
        st.accept_waveform(TARGET_SAMPLE_RATE, norm_sl)
        recognizer.decode_stream(st)
        raw_txt = str(st.result.text or "").strip()
        if not raw_txt:
            continue
        roman_raw = DictationProcessor.devanagari_to_roman_hinglish(raw_txt).strip()
        roman_norm = DictationProcessor.process(
            raw_txt,
            vocabulary=vocab,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
            mode="cleaned",
        ).strip()
        segments.append(
            {
                "start_time": round(s_i / float(TARGET_SAMPLE_RATE), 2),
                "end_time": round(e_i / float(TARGET_SAMPLE_RATE), 2),
                "source_track": source_track,
                "speaker": speaker,
                "raw_text": raw_txt,
                "roman_raw": roman_raw,
                "text": roman_norm or roman_raw or raw_txt,
            }
        )
    return segments


def compute_script_diagnostics(segments: List[Dict[str, Any]], duration_sec: float) -> Dict[str, Any]:
    total_raw_words = sum(len(str(s.get("raw_text", "")).split()) for s in segments)
    total_roman_words = sum(len(str(s.get("text", "")).split()) for s in segments)
    deva_segs = sum(1 for s in segments if DEVA_RE.search(str(s.get("raw_text", ""))))
    bengali_segs = sum(1 for s in segments if BENGALI_AS_RE.search(str(s.get("raw_text", ""))))
    other_indic_segs = sum(1 for s in segments if OTHER_INDIC_RE.search(str(s.get("raw_text", ""))))
    latin_raw_segs = sum(1 for s in segments if re.search(r"[A-Za-z]", str(s.get("raw_text", ""))))
    cov_end = max((float(s.get("end_time", 0.0)) for s in segments), default=0.0)
    trk_counts = Counter(str(s.get("source_track", "")) for s in segments)
    return {
        "segment_count": len(segments),
        "raw_word_count": total_raw_words,
        "roman_word_count": total_roman_words,
        "source_track_counts": dict(trk_counts),
        "raw_devanagari_segs": deva_segs,
        "raw_bengali_assamese_leak_segs": bengali_segs,
        "raw_other_indic_leak_segs": other_indic_segs,
        "raw_latin_segs": latin_raw_segs,
        "coverage_end_sec": round(cov_end, 2),
        "coverage_pct": round((cov_end / max(0.01, duration_sec)) * 100.0, 1),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, vocab = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_rows_sha = compute_target_db_rows_sha256()

    t_load0 = time.perf_counter()
    recognizer = build_recognizer(num_threads=4)
    load_sec = time.perf_counter() - t_load0
    print(f"[IndicConformer] Loaded meetsync/indic-conformer-onnx-sherpa (int8 CPU) in {load_sec:.2f}s | RAM={get_process_rss_mb()}MB", flush=True)

    summary: Dict[str, Any] = {
        "benchmark_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": "meetsync/indic-conformer-onnx-sherpa (ai4bharat/indicconformer_stt_hi_hybrid_ctc_rnnt_large INT8)",
        "provider": "cpu (onnxruntime via sherpa-onnx)",
        "load_sec": round(load_sec, 3),
        "vram_used_mb": 0,
        "meetings": {},
    }

    cpu_cores = max(1, os.cpu_count() or 1)
    for mid in (234, 235):
        m = meetings[mid]
        dur = float(m["duration_sec"])
        mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
        sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
        mic_aud = load_wav_as_16k_f32(mic_path)
        sys_aud = load_wav_as_16k_f32(sys_path)

        t0 = time.perf_counter()
        c0 = time.process_time()
        mic_segs = transcribe_track_with_indic_conformer(recognizer, mic_aud, "mic", "You (Mic)", vocab)
        sys_segs = transcribe_track_with_indic_conformer(recognizer, sys_aud, "system", "Speaker 1", vocab)
        all_segs = sorted(mic_segs + sys_segs, key=lambda s: (float(s["start_time"]), float(s["end_time"])))
        for idx, seg in enumerate(all_segs):
            seg["segment_index"] = idx
        wall = time.perf_counter() - t0
        cpu = time.process_time() - c0
        ram_mb = get_process_rss_mb()
        metrics = compute_script_diagnostics(all_segs, dur)

        pred_path = OUT_DIR / f"indic_conformer_onnx_M{mid}.json"
        pred_payload = {
            "meeting_id": mid,
            "title": m["title"],
            "model": summary["model"],
            "duration_sec": dur,
            "segments": all_segs,
        }
        pred_path.write_text(json.dumps(pred_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        excerpts = all_segs[:4] if mid == 234 else [all_segs[0], all_segs[len(all_segs) // 3], all_segs[(2 * len(all_segs)) // 3], all_segs[-1]] if len(all_segs) >= 4 else all_segs
        summary["meetings"][str(mid)] = {
            "title": m["title"],
            "duration_sec": dur,
            "wall_time_sec": round(wall, 3),
            "rtf": round(wall / max(0.01, dur), 4),
            "cpu_time_sec": round(cpu, 3),
            "avg_cpu_pct": round((cpu / max(0.01, wall)) * 100.0 / cpu_cores, 1),
            "ram_rss_mb": ram_mb,
            "metrics": metrics,
            "sample_excerpts": [
                {
                    "window": f"{e['start_time']:.2f}s-{e['end_time']:.2f}s",
                    "track": e["source_track"],
                    "raw_text": e["raw_text"],
                    "roman_norm": e["text"],
                }
                for e in excerpts
            ],
            "prediction_path": str(pred_path),
        }
        print(
            f"[IndicConformer] M#{mid} done: wall={wall:.2f}s rtf={wall/dur:.4f} ram={ram_mb}MB segs={metrics['segment_count']} words_raw={metrics['raw_word_count']} bengali_leaks={metrics['raw_bengali_assamese_leak_segs']} other_indic_leaks={metrics['raw_other_indic_leak_segs']}",
            flush=True,
        )

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_rows_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_rows_sha != post_db_rows_sha:
        mismatches.append("DB_TARGET_ROWS")

    summary["read_only_integrity"] = {
        "checked_file_count": len(pre_snap),
        "all_recording_and_config_hashes_unchanged": len(mismatches) == 0,
        "target_db_rows_unchanged": pre_db_rows_sha == post_db_rows_sha,
        "mismatches": mismatches,
    }
    sum_path = OUT_DIR / "indic_conformer_onnx_summary.json"
    sum_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[IndicConformer] Saved {sum_path} | protected_intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

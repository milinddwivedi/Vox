"""
Phase 2 Diarization Diagnostic & Challenger #2 (`sherpa-onnx` PyAnnote 3.0 + WeSpeaker ResNet34-LM).

Strictly read-only on original recordings (`M#234` and `M#235`), `data/config.json`, and `data/voxlocal.db`.
Verifies pre/post protected file & DB row SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
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
    DB_PATH,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    read_only_db_state,
    snapshot_protected_files,
)
from score_ground_truth import compute_diarization_metrics
from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    PersistentSpeakerDiarizer,
    extract_speaker_embedding,
    load_wav_as_16k_f32,
    speaker_cosine_distance,
)

DIAR_DIR = ROOT_DIR / "data" / "models" / "challengers" / "sherpa-diarization"
PYANNOTE_MODEL = DIAR_DIR / "pyannote-seg-3-0" / "model.onnx"
WESPEAKER_MODEL = DIAR_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"
OUT_DIR = BENCH_DIR / "phase2_challengers"

THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90)


def diagnose_persistent_diarizer(
    sys_audio: np.ndarray,
    system_turns: List[Tuple[float, float]],
    meeting_id: int,
) -> Dict[str, Any]:
    """
    Replay M#234 / M#235 system-track speech turns through VOX's current `PersistentSpeakerDiarizer`
    and quantify why single-remote-speaker audio fragments into 3-5 clusters.
    """
    diarizer = PersistentSpeakerDiarizer(speaker_prefix="Speaker")
    embs_full: List[np.ndarray] = []
    embs_nopitch: List[np.ndarray] = []
    split_events: List[Dict[str, Any]] = []
    dists_to_c0: List[float] = []
    dists_nopitch_to_e0: List[float] = []
    assigned_clusters: List[str] = []

    for idx, (s, e) in enumerate(system_turns):
        s_i = max(0, min(sys_audio.size, int(s * TARGET_SAMPLE_RATE)))
        e_i = max(s_i, min(sys_audio.size, int(e * TARGET_SAMPLE_RATE)))
        sl = sys_audio[s_i:e_i]
        prev_n = diarizer.num_speakers
        c0_before = diarizer.centroids[0].copy() if diarizer.centroids else None
        raw_emb = extract_speaker_embedding(sl)
        label, conf = diarizer.assign_segment(sl, start_sec=s, end_sec=e)
        assigned_clusters.append(label)

        if raw_emb is not None:
            embs_full.append(raw_emb)
            np_vec = raw_emb.copy()
            np_vec[0] = 0.0
            nrm = float(np.linalg.norm(np_vec))
            if nrm > 1e-6:
                np_vec = np_vec / nrm
            embs_nopitch.append(np_vec)
            if c0_before is not None:
                dists_to_c0.append(speaker_cosine_distance(raw_emb, c0_before))
            if len(embs_nopitch) > 1:
                dists_nopitch_to_e0.append(speaker_cosine_distance(np_vec, embs_nopitch[0]))

        if diarizer.num_speakers > prev_n and idx > 0:
            dist_c0 = dists_to_c0[-1] if dists_to_c0 else None
            split_events.append(
                {
                    "turn_index": idx,
                    "start_sec": round(s, 2),
                    "end_sec": round(e, 2),
                    "duration_sec": round(e - s, 2),
                    "new_cluster": label,
                    "cosine_dist_vs_c0": round(dist_c0, 4) if dist_c0 is not None else None,
                    "threshold": diarizer.new_speaker_threshold,
                    "pitch_dim_abs_delta_from_e0": round(abs(float(raw_emb[0] - embs_full[0][0])), 4)
                    if (raw_emb is not None and embs_full)
                    else None,
                }
            )

    full_arr = np.array(dists_to_c0, dtype=np.float64) if dists_to_c0 else np.zeros(1)
    np_arr = np.array(dists_nopitch_to_e0, dtype=np.float64) if dists_nopitch_to_e0 else np.zeros(1)
    pitch_deltas = (
        [abs(float(e[0] - embs_full[0][0])) for e in embs_full[1:]] if len(embs_full) > 1 else [0.0]
    )

    from collections import Counter

    return {
        "meeting_id": meeting_id,
        "system_turn_count": len(system_turns),
        "new_speaker_threshold": diarizer.new_speaker_threshold,
        "cluster_count": diarizer.num_speakers,
        "cluster_distribution": dict(Counter(assigned_clusters)),
        "full_16d_cosine_dist_vs_c0": {
            "min": round(float(np.min(full_arr)), 4),
            "median": round(float(np.median(full_arr)), 4),
            "p90": round(float(np.percentile(full_arr, 90)), 4),
            "max": round(float(np.max(full_arr)), 4),
            "pct_exceeding_threshold": round(float(np.mean(full_arr > diarizer.new_speaker_threshold) * 100.0), 1),
        },
        "without_pitch_dim0_cosine_dist_vs_e0": {
            "median": round(float(np.median(np_arr)), 4),
            "p90": round(float(np.percentile(np_arr, 90)), 4),
            "max": round(float(np.max(np_arr)), 4),
        },
        "pitch_dim0_3p4x_oct_abs_delta": {
            "median": round(float(np.median(pitch_deltas)), 4),
            "p90": round(float(np.percentile(pitch_deltas, 90)), 4),
            "max": round(float(np.max(pitch_deltas)), 4),
        },
        "cluster_split_events_sample": split_events[:8],
    }


def _build_sherpa_diarizer(threshold: float, num_threads: int = 4) -> sherpa_onnx.OfflineSpeakerDiarization:
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(PYANNOTE_MODEL),
            ),
            num_threads=num_threads,
            debug=False,
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(WESPEAKER_MODEL),
            num_threads=num_threads,
            debug=False,
            provider="cpu",
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=-1,
            threshold=float(threshold),
            compute_confidence=False,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError(f"Invalid OfflineSpeakerDiarizationConfig for threshold={threshold}")
    return sherpa_onnx.OfflineSpeakerDiarization(cfg)


def run_sherpa_on_audio(
    audio_16k: np.ndarray,
    duration_sec: float,
    threshold: float,
    num_threads: int = 4,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    sd = _build_sherpa_diarizer(threshold=threshold, num_threads=num_threads)
    t0 = time.perf_counter()
    c0 = time.process_time()
    res = sd.process(audio_16k)
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    raw_segs = res.sort_by_start_time()
    segments: List[Dict[str, Any]] = []
    cluster_durs: Dict[str, float] = {}
    for seg in raw_segs:
        spk_lbl = f"Speaker {int(seg.speaker) + 1}"
        s_t = float(seg.start)
        e_t = float(seg.end)
        cluster_durs[spk_lbl] = cluster_durs.get(spk_lbl, 0.0) + max(0.0, e_t - s_t)
        segments.append(
            {
                "start_time": round(s_t, 3),
                "end_time": round(e_t, 3),
                "speaker": spk_lbl,
                "text": "",
            }
        )
    ordered_durs = {
        k: round(v, 2)
        for k, v in sorted(cluster_durs.items(), key=lambda kv: kv[1], reverse=True)
    }
    summary = {
        "threshold": float(threshold),
        "num_speakers": int(res.num_speakers),
        "num_segments": len(segments),
        "cluster_durations_sec": ordered_durs,
        "wall_time_sec": round(wall, 3),
        "rtf": round(wall / max(0.01, duration_sec), 4),
        "cpu_time_sec": round(cpu, 3),
        "ram_rss_mb": get_process_rss_mb(),
    }
    return summary, segments


def _eval_isolated_oracle(
    vad_oracle: List[Dict[str, Any]],
    sys_segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    iso_preds: List[Dict[str, Any]] = [
        {**s, "speaker": f"Sys {s['speaker']}"} for s in sys_segments
    ]
    for ov in vad_oracle:
        if ov["speaker"] == "You (Mic)":
            iso_preds.append(
                {
                    "start_time": ov["start_time"],
                    "end_time": ov["end_time"],
                    "speaker": "You (Mic)",
                }
            )
    return compute_diarization_metrics(vad_oracle, iso_preds)


def build_dual_track_vad_oracle(mic_audio: np.ndarray, sys_audio: np.ndarray) -> List[Dict[str, Any]]:
    """
    Build a coarse track-level (`You (Mic)` vs `Remote Track`) Silero VAD oracle for
    evaluating mixed-track vs isolated-track diarization boundaries (NOT human-verified WER truth).
    """
    vad_p = dict(threshold=0.35, min_silence_duration_ms=400, speech_pad_ms=150)
    oracle: List[Dict[str, Any]] = []
    for spk, aud in (("You (Mic)", mic_audio), ("Remote Track", sys_audio)):
        spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
            aud, vad_p, max_chunk_sec=15.0, merge_gap_sec=0.45, pad_sec=0.10
        ) or []
        for s_samp, e_samp in spans:
            oracle.append(
                {
                    "start_time": round(s_samp / float(TARGET_SAMPLE_RATE), 3),
                    "end_time": round(e_samp / float(TARGET_SAMPLE_RATE), 3),
                    "speaker": spk,
                }
            )
    oracle.sort(key=lambda x: x["start_time"])
    return oracle


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PYANNOTE_MODEL.exists() or not WESPEAKER_MODEL.exists():
        raise FileNotFoundError("Missing PyAnnote or WeSpeaker ONNX models under data/models/challengers/sherpa-diarization")

    meetings, _ = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_rows_sha = compute_target_db_rows_sha256()

    report: Dict[str, Any] = {
        "benchmark_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "models": {
            "segmentation": str(PYANNOTE_MODEL),
            "embedding": str(WESPEAKER_MODEL),
        },
        "meetings": {},
    }

    for mid, draft_name in ((234, "M234_hemanth_reference_draft.json"), (235, "M235_rumjhum_reference_draft.json")):
        m = meetings[mid]
        dur = float(m["duration_sec"])
        mic_aud = load_wav_as_16k_f32(m["mic_audio_path"])
        sys_aud = load_wav_as_16k_f32(m["sys_audio_path"])
        mix_aud = load_wav_as_16k_f32(m["audio_path"])

        draft = json.loads((BENCH_DIR / draft_name).read_text(encoding="utf-8"))
        sys_turns = [
            (float(s["start_time"]), float(s["end_time"]))
            for s in draft.get("segments", [])
            if s.get("source_track") == "system"
        ]
        if not sys_turns:
            sys_turns = [(0.0, min(10.0, dur))]

        print(f"[Diar] Diagnosing PersistentSpeakerDiarizer on M#{mid} ({len(sys_turns)} system turns)...", flush=True)
        diag = diagnose_persistent_diarizer(sys_aud, sys_turns, mid)
        print(
            f"[Diar] M#{mid} PersistentSpeakerDiarizer: clusters={diag['cluster_count']} dist_p90={diag['full_16d_cosine_dist_vs_c0']['p90']} (thresh={diag['new_speaker_threshold']})",
            flush=True,
        )

        # Threshold sweep on isolated system_track.wav
        sys_thresholds = (0.50, 0.60, 0.70, 0.80, 0.90) if mid == 234 else (0.60, 0.70, 0.80, 0.90)
        sys_sweep: List[Dict[str, Any]] = []
        sys_segs_by_thresh: Dict[str, List[Dict[str, Any]]] = {}
        for th in sys_thresholds:
            summ, segs = run_sherpa_on_audio(sys_aud, dur, threshold=th, num_threads=4)
            sys_sweep.append(summ)
            sys_segs_by_thresh[f"{th:.2f}"] = segs
            print(
                f"[Diar] M#{mid} sherpa-onnx system_track thresh={th:.2f}: speakers={summ['num_speakers']} durs={summ['cluster_durations_sec']} segs={summ['num_segments']} wall={summ['wall_time_sec']}s rtf={summ['rtf']}",
                flush=True,
            )

        # Mixed-track sweep & isolated-track comparison vs VAD oracle
        vad_oracle = build_dual_track_vad_oracle(mic_aud, sys_aud)
        mix_thresholds = (0.60, 0.70, 0.80) if mid == 234 else (0.70, 0.80)
        mix_results: List[Dict[str, Any]] = []
        mix_segs_by_thresh: Dict[str, List[Dict[str, Any]]] = {}
        for th in mix_thresholds:
            m_summ, m_segs = run_sherpa_on_audio(mix_aud, dur, threshold=th, num_threads=4)
            diar_scores = compute_diarization_metrics(vad_oracle, m_segs)
            mix_results.append({**m_summ, "dual_track_vad_oracle_metrics": diar_scores})
            mix_segs_by_thresh[f"{th:.2f}"] = m_segs
            print(
                f"[Diar] M#{mid} sherpa-onnx mixed thresh={th:.2f}: speakers={m_summ['num_speakers']} durs={m_summ['cluster_durations_sec']} DER={diar_scores['speaker_der']} JER={diar_scores['speaker_jer']} attr_acc={diar_scores['speaker_attribution_accuracy']}",
                flush=True,
            )

        iso_oracle_070 = _eval_isolated_oracle(vad_oracle, sys_segs_by_thresh["0.70"])
        iso_oracle_090 = _eval_isolated_oracle(vad_oracle, sys_segs_by_thresh["0.90"])

        report["meetings"][str(mid)] = {
            "title": m["title"],
            "duration_sec": dur,
            "persistent_speaker_diarizer_diagnostic": diag,
            "sherpa_isolated_system_threshold_sweep": sys_sweep,
            "sherpa_mixed_threshold_sweep": mix_results,
            "source_track_isolated_oracle_eval_at_0_70": iso_oracle_070,
            "source_track_isolated_oracle_eval_at_0_90": iso_oracle_090,
            "segments_system_0_70": sys_segs_by_thresh["0.70"],
            "segments_system_0_90": sys_segs_by_thresh["0.90"],
            "segments_mixed_0_70": mix_segs_by_thresh["0.70"],
        }

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_rows_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_rows_sha != post_db_rows_sha:
        mismatches.append("DB_TARGET_ROWS")

    report["read_only_integrity"] = {
        "checked_file_count": len(pre_snap),
        "all_recording_and_config_hashes_unchanged": len(mismatches) == 0,
        "target_db_rows_unchanged": pre_db_rows_sha == post_db_rows_sha,
        "mismatches": mismatches,
    }
    out_path = OUT_DIR / "diarization_sherpa_onnx.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Diar] Saved {out_path} | protected_intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

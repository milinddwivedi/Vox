"""
VOX Phase 2.5 — Diarization Threshold (0.70/0.75/0.80/0.90) & Short-Cluster Folding Validation.

Runs `sherpa-onnx` (PyAnnote Seg 3.0 + WeSpeaker ResNet34-LM, CPU-only, 0 MB VRAM) on the
isolated `system` track across:
  - M#234 (90.15s, 16/16 human-verified, 1 true remote speaker)
  - M#235 (1129.00s, 111/111 human-verified, 1 true remote speaker)
  - M#188 (1182.95s, Daily Scrum - PLM North America, local multi-speaker cluster probe; UNVERIFIED)

Strictly read-only on audio files, `data/config.json`, `data/voxlocal.db`, and reference JSONs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_diarization_challenger import (
    PYANNOTE_MODEL,
    WESPEAKER_MODEL,
    run_sherpa_on_audio,
)
from benchmark_speech_intelligence import (
    BENCH_DIR,
    DB_PATH,
    compute_target_db_rows_sha256,
    read_only_db_state,
    snapshot_protected_files,
)
from score_ground_truth import compute_diarization_metrics
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

THRESHOLDS = (0.70, 0.75, 0.80, 0.90)
OUT_DIR = BENCH_DIR / "phase2_5_validation"
PHASE2_DIR = BENCH_DIR / "phase2_challengers"
REPORT_PATH = OUT_DIR / "diarization_phase25_report.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def fold_short_clusters(
    segments: List[Dict[str, Any]],
    min_cluster_dur_sec: float = 3.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Deterministically fold remote clusters with total duration `< min_cluster_dur_sec`
    into the temporally nearest major cluster (`>= min_cluster_dur_sec`, tie-break largest).
    """
    if not segments:
        return [], {"cluster_durations_sec": {}, "folded_clusters": [], "num_speakers": 0}

    durs: Dict[str, float] = defaultdict(float)
    by_cluster: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for seg in segments:
        spk = str(seg["speaker"])
        dur = max(0.0, float(seg["end_time"]) - float(seg["start_time"]))
        durs[spk] += dur
        by_cluster[spk].append(seg)

    if len(durs) <= 1:
        ordered = {k: round(v, 2) for k, v in sorted(durs.items(), key=lambda kv: kv[1], reverse=True)}
        return [dict(s) for s in segments], {
            "cluster_durations_sec": ordered,
            "folded_clusters": [],
            "num_speakers": len(durs),
        }

    major = [spk for spk, d in durs.items() if d >= min_cluster_dur_sec]
    if not major:
        major = [max(durs.items(), key=lambda kv: kv[1])[0]]
    major_set = set(major)
    folded_clusters = sorted(spk for spk in durs if spk not in major_set)

    major_segs = [s for s in segments if str(s["speaker"]) in major_set]
    folded_out: List[Dict[str, Any]] = []
    new_durs: Dict[str, float] = defaultdict(float)

    for s in segments:
        sc = dict(s)
        spk = str(sc["speaker"])
        if spk not in major_set:
            s0 = float(sc["start_time"])
            e0 = float(sc["end_time"])
            best_spk = max(major, key=lambda c: durs[c])
            best_dist = float("inf")
            best_dur = -1.0
            for ms in major_segs:
                m_spk = str(ms["speaker"])
                ms0 = float(ms["start_time"])
                me0 = float(ms["end_time"])
                if me0 < s0:
                    gap = s0 - me0
                elif ms0 > e0:
                    gap = ms0 - e0
                else:
                    gap = 0.0
                if gap < best_dist - 1e-6 or (abs(gap - best_dist) <= 1e-6 and durs[m_spk] > best_dur):
                    best_dist = gap
                    best_dur = durs[m_spk]
                    best_spk = m_spk
            sc["speaker"] = best_spk
        new_durs[str(sc["speaker"])] += max(0.0, float(sc["end_time"]) - float(sc["start_time"]))
        folded_out.append(sc)

    ordered_new = {k: round(v, 2) for k, v in sorted(new_durs.items(), key=lambda kv: kv[1], reverse=True)}
    return folded_out, {
        "cluster_durations_sec": ordered_new,
        "folded_clusters": folded_clusters,
        "num_speakers": len(ordered_new),
    }


def _score_against_human_verified(
    verified_refs: List[Dict[str, Any]],
    mic_intervals: List[Dict[str, Any]],
    sys_segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    combined = list(mic_intervals) + [
        {
            "start_time": float(s["start_time"]),
            "end_time": float(s["end_time"]),
            "speaker": f"Sys {s['speaker']}",
        }
        for s in sys_segments
    ]
    return compute_diarization_metrics(verified_refs, combined)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, _ = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_sha = compute_target_db_rows_sha256()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    r188 = dict(conn.execute("SELECT id, title, duration_sec, sys_audio_path FROM meetings WHERE id=188").fetchone())
    conn.close()

    m188_sys_raw = Path(LocalWhisperTranscriber._prefer_raw_wav_path(r188["sys_audio_path"]))
    pre_extra = {
        str(m188_sys_raw.resolve()): _sha256_file(m188_sys_raw),
        str((BENCH_DIR / "M234_hemanth_reference_draft.json").resolve()): _sha256_file(BENCH_DIR / "M234_hemanth_reference_draft.json"),
        str((BENCH_DIR / "M235_rumjhum_reference_draft.json").resolve()): _sha256_file(BENCH_DIR / "M235_rumjhum_reference_draft.json"),
    }

    report: Dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "models": {
            "segmentation": str(PYANNOTE_MODEL),
            "embedding": str(WESPEAKER_MODEL),
            "provider": "cpu",
            "vram_used_mb": 0,
        },
        "thresholds": list(THRESHOLDS),
        "short_cluster_fold_min_dur_sec": 3.0,
        "verified_single_remote_meetings": {},
        "unverified_local_multi_speaker_probe": {},
    }

    # 1. Verified single-remote-speaker meetings: M#234 (16/16) & M#235 (111/111)
    for mid, ref_fn in ((234, "M234_hemanth_reference_draft.json"), (235, "M235_rumjhum_reference_draft.json")):
        m = meetings[mid]
        dur = float(m["duration_sec"])
        sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
        sys_aud = load_wav_as_16k_f32(sys_path)

        ref_data = json.loads((BENCH_DIR / ref_fn).read_text(encoding="utf-8"))
        verified_refs = [s for s in ref_data["segments"] if s.get("verified_by_human") is True]

        # Real predicted mic VAD turns (zero ground-truth leakage)
        mic_preds = json.loads((PHASE2_DIR / f"qwen3-asr-0.6b_offline_M{mid}.json").read_text(encoding="utf-8"))["segments"]
        mic_intervals = [
            {
                "start_time": float(s["start_time"]),
                "end_time": float(s["end_time"]),
                "speaker": "You (Mic)",
            }
            for s in mic_preds
            if str(s.get("source_track", "")).strip().lower() == "mic"
        ]

        rows: List[Dict[str, Any]] = []
        for thr in THRESHOLDS:
            raw_sum, raw_segs = run_sherpa_on_audio(sys_aud, dur, threshold=thr, num_threads=4)
            folded_segs, fold_meta = fold_short_clusters(raw_segs, min_cluster_dur_sec=3.0)

            raw_hv = _score_against_human_verified(verified_refs, mic_intervals, raw_segs)
            fold_hv = _score_against_human_verified(verified_refs, mic_intervals, folded_segs)

            raw_n = int(raw_sum["num_speakers"])
            fold_n = int(fold_meta["num_speakers"])
            rows.append(
                {
                    "threshold": thr,
                    "wall_time_sec": raw_sum["wall_time_sec"],
                    "rtf": raw_sum["rtf"],
                    "ram_rss_mb": raw_sum["ram_rss_mb"],
                    "num_segments": raw_sum["num_segments"],
                    "raw": {
                        "remote_cluster_count": raw_n,
                        "cluster_durations_sec": raw_sum["cluster_durations_sec"],
                        "false_splits": max(0, raw_n - 1),
                        "false_merges": max(0, 1 - raw_n),
                        "human_verified_metrics": raw_hv,
                    },
                    "folded_lt_3s": {
                        "remote_cluster_count": fold_n,
                        "cluster_durations_sec": fold_meta["cluster_durations_sec"],
                        "folded_clusters": fold_meta["folded_clusters"],
                        "false_splits": max(0, fold_n - 1),
                        "false_merges": max(0, 1 - fold_n),
                        "human_verified_metrics": fold_hv,
                    },
                }
            )
            print(
                f"[Diar2.5 M#{mid}] t={thr:.2f}: raw_clusters={raw_n} (attr={raw_hv['speaker_attribution_accuracy']}, DER={raw_hv['speaker_der']}, conf={raw_hv['diarization_breakdown']['confusion_sec']}s) "
                f"-> folded={fold_n} (attr={fold_hv['speaker_attribution_accuracy']}, DER={fold_hv['speaker_der']}, conf={fold_hv['diarization_breakdown']['confusion_sec']}s) wall={raw_sum['wall_time_sec']}s",
                flush=True,
            )

        report["verified_single_remote_meetings"][str(mid)] = {
            "title": m["title"],
            "duration_sec": dur,
            "verified_segments": len(verified_refs),
            "true_remote_speakers": 1,
            "mic_interval_source": f"qwen3-asr-0.6b_offline_M{mid}.json (source_track=='mic')",
            "threshold_sweep": rows,
        }

    # 2. Local genuine multi-participant probe: M#188 (Daily Scrum - PLM North America) — UNVERIFIED
    dur188 = float(r188["duration_sec"])
    sys188 = load_wav_as_16k_f32(str(m188_sys_raw))
    m188_rows: List[Dict[str, Any]] = []
    for thr in THRESHOLDS:
        raw_sum, raw_segs = run_sherpa_on_audio(sys188, dur188, threshold=thr, num_threads=4)
        _, fold_meta = fold_short_clusters(raw_segs, min_cluster_dur_sec=3.0)
        m188_rows.append(
            {
                "threshold": thr,
                "wall_time_sec": raw_sum["wall_time_sec"],
                "rtf": raw_sum["rtf"],
                "ram_rss_mb": raw_sum["ram_rss_mb"],
                "num_segments": raw_sum["num_segments"],
                "raw_remote_cluster_count": raw_sum["num_speakers"],
                "raw_cluster_durations_sec": raw_sum["cluster_durations_sec"],
                "folded_remote_cluster_count": fold_meta["num_speakers"],
                "folded_cluster_durations_sec": fold_meta["cluster_durations_sec"],
                "folded_clusters": fold_meta["folded_clusters"],
            }
        )
        print(
            f"[Diar2.5 M#188 UNVERIFIED PROBE] t={thr:.2f}: raw_clusters={raw_sum['num_speakers']} {raw_sum['cluster_durations_sec']} "
            f"-> folded={fold_meta['num_speakers']} {fold_meta['cluster_durations_sec']} wall={raw_sum['wall_time_sec']}s",
            flush=True,
        )

    report["unverified_local_multi_speaker_probe"]["188"] = {
        "title": r188["title"],
        "duration_sec": dur188,
        "verified_by_human": False,
        "probe_warning": (
            "UNVERIFIED local multi-participant call probe ONLY; measures cluster count and stability "
            "across thresholds, not human-verified multi-speaker DER/attribution."
        ),
        "threshold_sweep": m188_rows,
    }

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_sha != post_db_sha:
        mismatches.append("DB_TARGET_ROWS")
    for k, v in pre_extra.items():
        if _sha256_file(Path(k)) != v:
            mismatches.append(k)

    report["read_only_integrity"] = {
        "all_hashes_unchanged": len(mismatches) == 0,
        "mismatches": mismatches,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Diar2.5] Saved {REPORT_PATH} | intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

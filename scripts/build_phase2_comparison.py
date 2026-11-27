"""
Unified Side-by-Side Comparison Builder for VOX Speech Intelligence Phase 2.

Strictly read-only across `data/voxlocal.db` (?mode=ro) and benchmark artifacts.
Consolidates Live, Offline, Resource/VRAM, Technical Entity Counts, Prompt Echo/Script Leakage,
Diarization, Human-Verified Scorer Status, and Aligned Excerpts across M#234 & M#235.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_speech_intelligence import (
    BENCH_DIR,
    count_prompt_echoes,
    read_only_db_state,
)
from score_ground_truth import (
    DEFAULT_TECHNICAL_ENTITIES,
    _count_duplication_words,
    compute_diarization_metrics,
    score_predictions_against_reference,
    tokenize_strict,
)

OUT_DIR = BENCH_DIR / "phase2_challengers"
SUMMARY_PATH = OUT_DIR / "phase2_side_by_side_summary.json"

M234_WINDOWS: Tuple[Tuple[float, float], ...] = ((0.0, 15.0), (25.0, 48.0), (60.0, 85.0))
M235_WINDOWS: Tuple[Tuple[float, float], ...] = (
    (0.0, 29.0),
    (36.0, 66.0),
    (328.0, 348.0),
    (548.0, 588.0),
    (649.0, 689.0),
    (895.0, 955.0),
    (1115.0, 1129.0),
)

DEVA_RE = re.compile(r"[\u0900-\u097F]")
URDU_RE = re.compile(r"[\u0600-\u06FF]")
CJK_RE = re.compile(r"[\u4E00-\u9FFF]")


def _count_entities_and_dups(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    joined = " ".join(str(s.get("text", "")).strip() for s in segments)
    ent_counts: Dict[str, int] = {}
    for ent in DEFAULT_TECHNICAL_ENTITIES:
        hits = len(re.findall(rf"\b{re.escape(ent)}\b", joined, flags=re.IGNORECASE))
        if hits > 0:
            ent_counts[ent] = hits
    toks = tokenize_strict(joined)
    dup_words = _count_duplication_words(toks)
    deva_segs = sum(1 for s in segments if DEVA_RE.search(str(s.get("text", ""))))
    urdu_segs = sum(1 for s in segments if URDU_RE.search(str(s.get("text", ""))))
    cjk_segs = sum(1 for s in segments if CJK_RE.search(str(s.get("text", ""))))
    return {
        "segment_count": len(segments),
        "total_words": len(toks),
        "prompt_echo_count": count_prompt_echoes(segments),
        "deva_leak_segs": deva_segs,
        "urdu_leak_segs": urdu_segs,
        "cjk_leak_segs": cjk_segs,
        "duplication_words": dup_words,
        "duplication_rate": round(dup_words / max(1, len(toks)), 4),
        "technical_entity_total_hits": sum(ent_counts.values()),
        "technical_entity_distinct_count": len(ent_counts),
        "technical_entity_hits": ent_counts,
    }


def _slice_excerpt(segments: List[Dict[str, Any]], w_s: float, w_e: float, max_chars: int = 420) -> str:
    parts: List[str] = []
    for s in segments:
        s0 = float(s.get("start_time", s.get("start", 0.0)))
        e0 = float(s.get("end_time", s.get("end", s0)))
        if min(e0, w_e) >= max(s0, w_s) - 0.35:
            txt = str(s.get("text", "")).strip()
            if txt:
                spk = str(s.get("speaker", "?"))
                parts.append(f"[{spk} {s0:.1f}-{e0:.1f}s] {txt}")
    joined = " | ".join(parts)
    if len(joined) > max_chars:
        return joined[: max_chars - 3] + "..."
    return joined or "(no speech segment)"


def _load_segs(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("segments", [])) if isinstance(data, dict) else list(data)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, _ = read_only_db_state()
    phase1_rpt = json.loads((BENCH_DIR / "phase1_whisper_benchmark_report.json").read_text(encoding="utf-8"))
    phase2_rpt = json.loads((OUT_DIR / "phase2_challenger_report.json").read_text(encoding="utf-8"))
    indic_rpt = (
        json.loads((OUT_DIR / "indic_conformer_onnx_summary.json").read_text(encoding="utf-8"))
        if (OUT_DIR / "indic_conformer_onnx_summary.json").exists()
        else {}
    )
    diar_rpt = (
        json.loads((OUT_DIR / "diarization_sherpa_onnx.json").read_text(encoding="utf-8"))
        if (OUT_DIR / "diarization_sherpa_onnx.json").exists()
        else {}
    )

    ref_drafts = {
        234: json.loads((BENCH_DIR / "M234_hemanth_reference_draft.json").read_text(encoding="utf-8")),
        235: json.loads((BENCH_DIR / "M235_rumjhum_reference_draft.json").read_text(encoding="utf-8")),
    }

    challenger_keys = list(phase2_rpt.get("challengers", {}).keys())

    summary: Dict[str, Any] = {
        "generated_at_utc": phase2_rpt.get("last_updated_utc"),
        "human_verified_counts": {
            str(mid): {
                "verified": sum(1 for s in ref_drafts[mid]["segments"] if s.get("verified_by_human") is True),
                "total": len(ref_drafts[mid]["segments"]),
            }
            for mid in (234, 235)
        },
        "models": {},
        "diarization_comparison": {},
        "side_by_side_excerpts": {"234": [], "235": []},
    }

    # Populate diarization summary from diar_rpt (preserving oracle vs human-verified label)
    if diar_rpt:
        for mid_s, d_m in diar_rpt.get("meetings", {}).items():
            summary["diarization_comparison"][mid_s] = {
                "persistent_diarizer_clusters": d_m.get("persistent_speaker_diarizer_diagnostic", {}).get("cluster_count"),
                "sherpa_isolated_system_threshold_sweep": d_m.get("sherpa_isolated_system_threshold_sweep", []),
                "source_track_isolated_oracle_eval_at_0_90": d_m.get("source_track_isolated_oracle_eval_at_0_90"),
                "sherpa_mixed_best": d_m.get("sherpa_mixed_threshold_sweep", []),
                "oracle_note": "Dual-track Silero VAD oracle diagnostic ONLY; human-verified speaker DER/JER requires verified reference segments.",
            }

    # Build segment maps per meeting across ALL models for metrics + side-by-side excerpts
    model_segments: Dict[int, Dict[str, List[Dict[str, Any]]]] = {234: {}, 235: {}}

    for mid in (234, 235):
        model_segments[mid]["baseline_db"] = list(meetings[mid]["baseline_segments"])
        for mk in challenger_keys:
            off_p = OUT_DIR / f"{mk}_offline_M{mid}.json"
            if off_p.exists():
                model_segments[mid][f"{mk}:offline"] = _load_segs(off_p)
            live_p = OUT_DIR / f"{mk}_live_M{mid}.json"
            if live_p.exists():
                model_segments[mid][f"{mk}:live"] = _load_segs(live_p)
        indic_p = OUT_DIR / f"indic_conformer_onnx_M{mid}.json"
        if indic_p.exists():
            model_segments[mid]["indic-conformer-onnx:offline"] = _load_segs(indic_p)

    # Enrich diarization comparison with human-verified speaker attribution metrics (kept strictly separate from oracle)
    if diar_rpt:
        for mid in (234, 235):
            mid_s = str(mid)
            if mid_s not in summary["diarization_comparison"]:
                continue
            d_m = diar_rpt.get("meetings", {}).get(mid_s, {})
            verified_refs = [s for s in ref_drafts[mid].get("segments", []) if s.get("verified_by_human") is True]
            if not verified_refs:
                continue
            mic_intervals = [
                {
                    "start_time": float(s["start_time"]),
                    "end_time": float(s["end_time"]),
                    "speaker": "You (Mic)",
                }
                for s in model_segments[mid].get("qwen3-asr-0.6b:offline", [])
                if str(s.get("source_track", "")).strip().lower() == "mic"
            ]
            d_cmp = summary["diarization_comparison"][mid_s]
            d_cmp["sherpa_mixed_human_verified_at_0_70"] = compute_diarization_metrics(
                verified_refs, d_m.get("segments_mixed_0_70", [])
            )
            for thr_tag, key in (("0_70", "segments_system_0_70"), ("0_90", "segments_system_0_90")):
                sys_intervals = [
                    {
                        "start_time": float(s["start_time"]),
                        "end_time": float(s["end_time"]),
                        "speaker": f"Sys {s['speaker']}",
                    }
                    for s in d_m.get(key, [])
                ]
                d_cmp[f"sherpa_isolated_system_human_verified_at_{thr_tag}"] = compute_diarization_metrics(
                    verified_refs, mic_intervals + sys_intervals
                )
            d_cmp["oracle_note"] = (
                "source_track_isolated_oracle_eval_at_0_90 and sherpa_mixed_best use automated dual-track Silero VAD oracle reference; "
                "sherpa_*_human_verified_at_* fields are scored directly against human-verified speaker intervals."
            )

    # Assemble per-model metrics
    summary["models"]["baseline_db"] = {
        "role": "Legacy baseline stored in SQLite prior to Phase 1",
        "meetings": {
            str(mid): {
                "metrics": _count_entities_and_dups(model_segments[mid]["baseline_db"]),
                "report_stats": phase1_rpt["meetings"][str(mid)]["baseline_db"],
                "verified_scores": score_predictions_against_reference(ref_drafts[mid], model_segments[mid]["baseline_db"]),
            }
            for mid in (234, 235)
        },
    }

    for mk, m_data in phase2_rpt.get("challengers", {}).items():
        m_out: Dict[str, Any] = {
            "status": m_data.get("status", "ok"),
            "error_message": m_data.get("error_message"),
            "load_sec": m_data.get("load_sec"),
            "vram_before_load_mb": m_data.get("vram_before_load_mb"),
            "vram_after_load_mb": m_data.get("vram_after_load_mb"),
            "peak_vram_used_mb": m_data.get("peak_vram_used_mb"),
            "vram_after_unload_mb": m_data.get("vram_after_unload_mb"),
            "vram_recovered_delta_mb": m_data.get("vram_recovered_delta_mb"),
            "meetings": {},
        }
        for mid_s, m_entry in m_data.get("meetings", {}).items():
            mid = int(mid_s)
            m_rec: Dict[str, Any] = {}
            if "live_continuous" in m_entry:
                lc = dict(m_entry["live_continuous"])
                segs_l = model_segments[mid].get(f"{mk}:live", [])
                lc["entity_and_quality"] = _count_entities_and_dups(segs_l)
                lc["verified_scores"] = score_predictions_against_reference(ref_drafts[mid], segs_l)
                m_rec["live_continuous"] = lc
            if "offline_full" in m_entry:
                of = dict(m_entry["offline_full"])
                segs_o = model_segments[mid].get(f"{mk}:offline", [])
                of["entity_and_quality"] = _count_entities_and_dups(segs_o)
                of["verified_scores"] = score_predictions_against_reference(ref_drafts[mid], segs_o)
                m_rec["offline_full"] = of
            m_out["meetings"][mid_s] = m_rec
        summary["models"][mk] = m_out

    if indic_rpt:
        ind_out: Dict[str, Any] = {
            "status": "ok",
            "model": indic_rpt.get("model"),
            "load_sec": indic_rpt.get("load_sec"),
            "vram_used_mb": indic_rpt.get("vram_used_mb", 0),
            "meetings": {},
        }
        for mid_s, im_entry in indic_rpt.get("meetings", {}).items():
            mid = int(mid_s)
            segs_i = model_segments[mid].get("indic-conformer-onnx:offline", [])
            ind_out["meetings"][mid_s] = {
                "wall_time_sec": im_entry.get("wall_time_sec"),
                "rtf": im_entry.get("rtf"),
                "avg_cpu_pct": im_entry.get("avg_cpu_pct"),
                "ram_rss_mb": im_entry.get("ram_rss_mb"),
                "raw_script_metrics": im_entry.get("metrics"),
                "entity_and_quality": _count_entities_and_dups(segs_i),
                "verified_scores": score_predictions_against_reference(ref_drafts[mid], segs_i),
            }
        summary["models"]["indic-conformer-onnx"] = ind_out

    # Side-by-side aligned excerpts across key M#234 and M#235 timestamps
    for mid, win_list in ((234, M234_WINDOWS), (235, M235_WINDOWS)):
        # Order excerpt keys logically: baseline, live models, offline models, indic
        ordered_keys = ["baseline_db"]
        for mk in challenger_keys:
            if f"{mk}:live" in model_segments[mid]:
                ordered_keys.append(f"{mk}:live")
        for mk in challenger_keys:
            if f"{mk}:offline" in model_segments[mid]:
                ordered_keys.append(f"{mk}:offline")
        if "indic-conformer-onnx:offline" in model_segments[mid]:
            ordered_keys.append("indic-conformer-onnx:offline")

        for ws, we in win_list:
            row = {
                "window_sec": f"{ws:.1f}s - {we:.1f}s",
                "outputs": {k: _slice_excerpt(model_segments[mid][k], ws, we) for k in ordered_keys},
            }
            summary["side_by_side_excerpts"][str(mid)].append(row)

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Comparison] Saved {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

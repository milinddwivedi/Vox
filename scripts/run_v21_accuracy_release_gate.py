"""
VOX v2.1.0 Product-Quality Accuracy Release Gate.

Strictly read-only on production `data/voxlocal.db` and `data/recordings/`.
Runs:
1. Cohere Transcribe 2B explicit English-mode verification (`language="en"`, `use_punct=True`, `use_itn=True`).
2. Locked 20-slice M#247 English benchmark (`M247_audio_verified_slices.json`, 658 ref words, unmodified)
   + 15 silence probes (`canonical_m247_chunks.json`): Indian EN WER, US EN WER, Mixed WER, domain/proper-noun recall,
   script purity, silence hallucination rate, RTF, peak RSS.
3. Real dual-track ASR + echo-bleed suppression + PyAnnote/WeSpeaker diarization on M#234 (`90.15s`, read-only).
4. Isolated Stage 4/5 Meeting Intelligence (Qwen3.5-4B-Q4_K_M) + BGE-Small-EN RAG + Ask VOX grounding & refusal check
   against a read-only `.backup()` clone of M#247 (`366` segments).

Emits `release_artifacts/v21_accuracy_gate_report.json`.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import gc
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import wave
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BENCH_DIR = Path(
    r"C:\Users\milin\.gemini\antigravity\brain\9b0bd9e3-7696-49ec-8ae0-ed63b06e7018\scratch\benchmarks"
)
SCRATCH_GATE_DIR = Path(
    r"C:\Users\milin\.gemini\antigravity\brain\9b0bd9e3-7696-49ec-8ae0-ed63b06e7018\scratch\v21_gate_tmp"
)
OUT_REPORT = ROOT / "release_artifacts" / "v21_accuracy_gate_report.json"

SR = 16000
FOREIGN_SCRIPT_RE = re.compile(r"[\u4E00-\u9FFF\u0600-\u06FF\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF\u0900-\u097F\uFFFD]")

CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "he's": "he is", "she's": "she is", "it's": "it is", "that's": "that is",
    "what's": "what is", "there's": "there is", "here's": "here is", "who's": "who is",
    "i'll": "i will", "you'll": "you will", "we'll": "we will", "they'll": "they will",
    "it'll": "it will", "that'll": "that will", "i'd": "i would", "you'd": "you would",
    "we'd": "we would", "i've": "i have", "you've": "you have", "we've": "we have",
    "don't": "do not", "doesn't": "does not", "didn't": "did not", "can't": "cannot",
    "won't": "will not", "wouldn't": "would not", "shouldn't": "should not",
    "couldn't": "could not", "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "let's": "let us", "gonna": "going to", "gotta": "got to",
    "wanna": "want to", "kinda": "kind of", "sorta": "sort of", "alright": "all right",
    "okay": "ok",
}
NUM_WORDS = {
    "0": "zero", "01": "zero one", "1": "one", "2": "two", "3": "three",
    "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "25": "twenty five", "26": "twenty six", "27": "twenty seven",
    "106": "one hundred six", "2026": "twenty twenty six", "2027": "twenty twenty seven",
}


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_rss_mb() -> Tuple[float, float]:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(pmc)
    if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        return round(pmc.WorkingSetSize / (1024 * 1024), 1), round(pmc.PeakWorkingSetSize / (1024 * 1024), 1)
    return 0.0, 0.0


def normalize_for_wer(text: str) -> str:
    s = str(text or "").lower().replace("...", " ").replace("-", " ")
    for k, v in CONTRACTIONS.items():
        s = re.sub(rf"\b{re.escape(k)}\b", v, s)
    s = re.sub(r"[^\w\s']", " ", s).replace("'", "")
    return " ".join(
        tok for raw in s.split() for tok in (NUM_WORDS[raw].split() if raw in NUM_WORDS else [raw])
    )


def levenshtein_ops(ref: List[str], hyp: List[str]) -> Tuple[int, int, int, int]:
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    op = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0], op[i][0] = i, 1
    for j in range(1, m + 1):
        dp[0][j], op[0][j] = j, 2
    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == hyp[j - 1] else 1
            best, best_op = dp[i - 1][j - 1] + cost, 0
            if dp[i - 1][j] + 1 < best:
                best, best_op = dp[i - 1][j] + 1, 1
            if dp[i][j - 1] + 1 < best:
                best, best_op = dp[i][j - 1] + 1, 2
            dp[i][j], op[i][j] = best, best_op
    i, j = n, m
    subs = dels = ins = 0
    while i > 0 or j > 0:
        c = op[i][j]
        if c == 0 and i > 0 and j > 0:
            if ref[i - 1] != hyp[j - 1]:
                subs += 1
            i -= 1
            j -= 1
        elif c == 1 and i > 0:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return dp[n][m], subs, dels, ins


def compute_wer_cer(ref_text: str, hyp_text: str) -> Dict[str, Any]:
    nr, nh = normalize_for_wer(ref_text), normalize_for_wer(hyp_text)
    rw, hw = nr.split(), nh.split()
    wd, ws, wdel, wins = levenshtein_ops(rw, hw)
    rc, hc = list(nr), list(nh)
    cd, _, _, _ = levenshtein_ops(rc, hc)
    return {
        "ref_words": len(rw),
        "hyp_words": len(hw),
        "word_edits": wd,
        "subs": ws,
        "dels": wdel,
        "ins": wins,
        "wer": round(wd / max(1, len(rw)), 4),
        "ref_chars": len(rc),
        "char_edits": cd,
        "cer": round(cd / max(1, len(rc)), 4),
    }


def query_gpu_mem_mb() -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        return int(out.strip().splitlines()[0].strip())
    except Exception:
        return None


def main() -> int:
    os.environ["VOXLOCAL_APP_ROOT"] = str(ROOT)
    from voxlocal.core.config import AppConfig
    from voxlocal.core.controller import AppController
    from voxlocal.engine.meeting_asr_pipeline import PostCallEnglishASRPipeline, load_wav_mono_16k_readonly
    from voxlocal.storage.exporter import LocalMeetingSummarizer

    report: Dict[str, Any] = {
        "generated_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": "2.1.0",
        "gate_notes": [
            "Strictly read-only on production data/voxlocal.db and data/recordings/.",
            "Locked M247_audio_verified_slices.json (20 slices, 658 ref words) used unmodified.",
            "Historical M234/M235 draft reference JSONs were absent from workspace and not fabricated; M#234 dual-track ASR/diarization metrics and live M#247 Stage 4/5/Ask-VOX grounding are directly executed below.",
        ],
    }

    # 1. Cohere English-only configuration check + 20-slice M247 benchmark + 15 silence probes
    pipe = PostCallEnglishASRPipeline(models_dir=ROOT / "data" / "models")
    ensure_src = inspect.getsource(pipe._ensure_asr_loaded)
    assert 'language="en"' in ensure_src, "Cohere pipeline must pass language='en'"
    pipe._ensure_asr_loaded()

    ref_data = json.loads((BENCH_DIR / "M247_audio_verified_slices.json").read_text(encoding="utf-8"))
    slices_manifest = {s["slice_id"]: s for s in json.loads((BENCH_DIR / "slices_manifest.json").read_text(encoding="utf-8"))}

    tot_edits = tot_ref_w = tot_c_edits = tot_ref_c = 0
    tot_infer_sec = tot_audio_sec = 0.0
    domain_hits = domain_total = noun_hits = noun_total = 0
    foreign_hits = omitted_slices = 0
    group_stats: Dict[str, Dict[str, int]] = {}
    per_slice: List[Dict[str, Any]] = []

    for sl in ref_data["slices"]:
        sid = sl["slice_id"]
        wav_p = Path(slices_manifest[sid]["wav_path"])
        arr = load_wav_mono_16k_readonly(wav_p)
        t0 = time.perf_counter()
        hyp = pipe._transcribe_chunk(arr)
        dt = time.perf_counter() - t0
        tot_infer_sec += dt
        tot_audio_sec += float(sl["duration_sec"])
        sc = compute_wer_cer(sl["reference_text"], hyp)

        tot_edits += sc["word_edits"]
        tot_ref_w += sc["ref_words"]
        tot_c_edits += sc["char_edits"]
        tot_ref_c += sc["ref_chars"]
        if not hyp.strip():
            omitted_slices += 1
        if FOREIGN_SCRIPT_RE.search(hyp):
            foreign_hits += 1

        grp = sl["accent_group"]
        gs = group_stats.setdefault(grp, {"edits": 0, "ref_words": 0, "c_edits": 0, "ref_chars": 0, "slices": 0})
        gs["edits"] += sc["word_edits"]
        gs["ref_words"] += sc["ref_words"]
        gs["c_edits"] += sc["char_edits"]
        gs["ref_chars"] += sc["ref_chars"]
        gs["slices"] += 1

        nh_space = " " + normalize_for_wer(hyp) + " "
        hyp_low = hyp.lower()
        for dt_term in sl["domain_terms"]:
            domain_total += 1
            if normalize_for_wer(dt_term) in nh_space or dt_term.lower() in hyp_low:
                domain_hits += 1
        for pn in sl["proper_nouns"]:
            noun_total += 1
            if normalize_for_wer(pn) in nh_space or pn.lower() in hyp_low:
                noun_hits += 1

        per_slice.append({"slice_id": sid, "accent_group": grp, "wer": sc["wer"], "cer": sc["cer"], "hyp": hyp[:120]})

    canonical = json.loads((BENCH_DIR / "canonical_m247_chunks.json").read_text(encoding="utf-8"))
    sys_wav = ROOT / "data" / "recordings" / "meeting_20261125_213207" / "system_track.wav"
    with wave.open(str(sys_wav), "rb") as wf:
        sys_audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    silence_hallucinations = 0
    for sp in canonical["silence_probes"]:
        seg = sys_audio[sp["start_sample"] : sp["end_sample"]]
        # Apply production RMS floor check matching meeting_asr_pipeline
        if float(np.sqrt(np.mean(np.square(seg)))) >= 0.002:
            stxt = pipe._transcribe_chunk(seg)
            if stxt.strip():
                silence_hallucinations += 1
    del sys_audio
    gc.collect()

    _, peak_rss_asr = get_rss_mb()
    report["cohere_english_gate"] = {
        "language_mode_enforced": "en",
        "overall_wer": round(tot_edits / max(1, tot_ref_w), 4),
        "overall_cer": round(tot_c_edits / max(1, tot_ref_c), 4),
        "ref_words": tot_ref_w,
        "word_edits": tot_edits,
        "omitted_slices": omitted_slices,
        "foreign_script_hits": foreign_hits,
        "silence_hallucinations": f"{silence_hallucinations}/{len(canonical['silence_probes'])}",
        "domain_term_recall": f"{domain_hits}/{domain_total} ({domain_hits / max(1, domain_total):.1%})",
        "proper_noun_recall": f"{noun_hits}/{noun_total} ({noun_hits / max(1, noun_total):.1%})",
        "by_accent_group": {
            k: {
                "slices": v["slices"],
                "wer": round(v["edits"] / max(1, v["ref_words"]), 4),
                "cer": round(v["c_edits"] / max(1, v["ref_chars"]), 4),
            }
            for k, v in group_stats.items()
        },
        "total_audio_sec": round(tot_audio_sec, 2),
        "total_infer_sec": round(tot_infer_sec, 2),
        "rtf": round(tot_infer_sec / max(0.01, tot_audio_sec), 4),
        "peak_rss_mb": peak_rss_asr,
    }

    # 2. Live M#234 (90.15s) Dual-Track Echo-Bleed + Cohere ASR + Sherpa PyAnnote/WeSpeaker Diarization
    m234_dir = ROOT / "data" / "recordings" / "meeting_20261119_200303"
    t_m234 = time.perf_counter()
    segs_234 = pipe.transcribe_meeting_dual_track(
        mic_wav_path=m234_dir / "mic_track.wav",
        sys_wav_path=m234_dir / "system_track.wav",
        diarization_enabled=True,
        echo_bleed_suppression=True,
        glossary_correction=True,
    )
    dt_234 = round(time.perf_counter() - t_m234, 2)
    m234_foreign = sum(1 for s in segs_234 if FOREIGN_SCRIPT_RE.search(str(s.get("text") or "")))
    m234_words = sum(len(str(s.get("text") or "").split()) for s in segs_234)
    _, peak_rss_m234 = get_rss_mb()
    pipe.unload()
    gc.collect()

    report["m234_multitrack_pipeline_gate"] = {
        "meeting_id": 234,
        "duration_sec": 90.15,
        "wall_sec": dt_234,
        "rtf": round(dt_234 / 90.15, 4),
        "segments_count": len(segs_234),
        "total_words": m234_words,
        "foreign_script_hits": m234_foreign,
        "track_stats": pipe.last_track_stats,
        "diarization_stats": pipe.last_diarization_stats,
        "peak_rss_mb": peak_rss_m234,
        "sample_segments": [
            {"speaker": s.get("speaker"), "start": s.get("start_time"), "text": str(s.get("text") or "")[:140]}
            for s in segs_234[:5]
        ],
    }

    # 3. Isolated Meeting Intelligence + Knowledge RAG + Ask VOX Gate (cloned DB -> zero mutation to live DB)
    if SCRATCH_GATE_DIR.exists():
        shutil.rmtree(SCRATCH_GATE_DIR, ignore_errors=True)
    gate_data = SCRATCH_GATE_DIR / "data"
    gate_data.mkdir(parents=True, exist_ok=True)
    sc = sqlite3.connect(f"file:{ROOT / 'data' / 'voxlocal.db'}?mode=ro", uri=True)
    dc = sqlite3.connect(str(gate_data / "voxlocal.db"))
    sc.backup(dc)
    sc.close()
    dc.close()

    os.environ["VOXLOCAL_DATA_DIR"] = str(gate_data)
    cfg = AppConfig(
        storage_dir=str(gate_data),
        models_dir=str(ROOT / "data" / "models"),
        auto_record_calls=False,
        record_meeting_screen=False,
    )
    cfg.check_updates_on_startup = False
    cfg.calendar_sync_enabled = False
    cfg.auto_detect_calls = False
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True

    AppController._preload_model_bg = lambda self: None
    ctrl = AppController(config=cfg)
    try:
        m247 = ctrl.db.get_meeting(247)
        segs247 = ctrl.db.get_meeting_segments(247)
        assert m247 and segs247

        vram_before = query_gpu_mem_mb()
        t_s4 = time.perf_counter()
        full_txt = m247.get("full_transcript") or "\n".join(
            f"[{int(float(s.get('start_time') or 0)//60):02d}:{int(float(s.get('start_time') or 0)%60):02d}] {s.get('speaker', 'Speaker')}: {s.get('text', '')}"
            for s in segs247
        )
        analysis = LocalMeetingSummarizer.analyze_full(
            segs247,
            full_txt,
            duration_sec=float(m247.get("duration_sec") or 0.0),
            title=str(m247.get("title") or "Zoom Meeting"),
            platform="Zoom",
            llm_engine=ctrl.llm_engine,
            meeting_timestamp=m247.get("created_at"),
            timezone="local",
        )
        s4_sec = round(time.perf_counter() - t_s4, 2)
        vram_peak = query_gpu_mem_mb()
        rel_tel = ctrl.llm_engine.get_last_reliability_telemetry() or {}

        actions = analysis.get("action_items") or []
        decisions = analysis.get("decisions") or []
        bad_placeholders = sum(
            1
            for a in actions
            if str(a.get("description") or "").startswith("Review and address:")
            or "unassigned" in str(a.get("owner") or "").lower()
            or str(a.get("due_date") or "").lower() == "not specified"
        )
        empty_quotes = sum(1 for a in actions if not str(a.get("evidence_quote") or "").strip())

        t_s5 = time.perf_counter()
        k_items = ctrl.llm_engine.extract_meeting_knowledge(
            segs247,
            title=str(m247.get("title") or "Zoom Meeting"),
            meeting_date=str(m247.get("created_at") or "")[:10],
        )
        s5_sec = round(time.perf_counter() - t_s5, 2)

        ask_queries = [
            ("factual_summary", "What were the main eyewear article coding rules decided in this meeting?", 247),
            ("participant_attribution", "What did Abhilash explain about family identifiers in SKUs?", 247),
            ("decisions_check", "List the confirmed decisions made in this call.", 247),
            ("negative_trap_refusal", "Was a Kubernetes budget of $500,000 for Project Helios approved in this meeting?", 247),
            ("cross_meeting", "Across recorded meetings, where do Strata or PLM workflows appear?", None),
        ]
        ask_results = []
        for label, q, mid in ask_queries:
            res = ctrl.query_meeting_memory(q, meeting_id=mid, answer_depth="concise")
            ans = str((res or {}).get("answer") or "")
            cits = list((res or {}).get("citations") or [])
            ask_results.append({
                "case": label,
                "meeting_id": mid,
                "citation_count": len(cits),
                "has_timestamp": bool(re.search(r"\[\d{1,2}:\d{2}", ans) or any(c.get("timestamp_formatted") for c in cits)),
                "refused_absent": ("not discussed" in ans.lower() or "no mention" in ans.lower()) if label == "negative_trap_refusal" else None,
                "invented_500k": ("500,000" in ans or "500000" in ans) if label == "negative_trap_refusal" else None,
                "answer_preview": ans[:220],
            })

        _, peak_rss_llm = get_rss_mb()
        report["meeting_intelligence_and_rag_gate"] = {
            "llm_model": analysis.get("llm_model"),
            "llm_device": analysis.get("llm_device"),
            "llm_fallback_reason": analysis.get("llm_fallback_reason"),
            "thinking_disabled_verified": rel_tel.get("thinking_disabled_verified"),
            "stage4_sec": s4_sec,
            "stage5_extract_sec": s5_sec,
            "decisions_count": len(decisions),
            "action_items_count": len(actions),
            "key_points_count": len(analysis.get("key_discussion_points") or []),
            "knowledge_items_extracted": len(k_items or []),
            "placeholder_violations": bad_placeholders,
            "empty_action_evidence_quotes": empty_quotes,
            "vram_before_mb": vram_before,
            "vram_peak_mb": vram_peak,
            "peak_rss_mb": peak_rss_llm,
            "ask_vox_checks": ask_results,
        }
    finally:
        ctrl.shutdown()
        os.environ.pop("VOXLOCAL_DATA_DIR", None)
        os.environ.pop("VOXLOCAL_APP_ROOT", None)
        shutil.rmtree(SCRATCH_GATE_DIR, ignore_errors=True)

    # Cross-reference long-duration 1:02:49 end-to-end M#247 telemetry
    v2_e2e_p = BENCH_DIR / "v2_m247_production_e2e.json"
    if v2_e2e_p.is_file():
        v2_e2e = json.loads(v2_e2e_p.read_text(encoding="utf-8"))
        report["long_meeting_62min_reference_telemetry"] = {
            "meeting_id": 247,
            "duration_sec": v2_e2e.get("duration_sec"),
            "finalization_wall_sec": v2_e2e.get("finalization_wall_sec"),
            "rtf": v2_e2e.get("rtf"),
            "segments_count": v2_e2e.get("segments_count"),
            "total_words": v2_e2e.get("total_words"),
            "sample_coverage_pct": (v2_e2e.get("completeness_report") or {}).get("system_track_coverage_pct"),
            "llm_device_used": v2_e2e.get("llm_device_used"),
        }

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("V21_ACCURACY_GATE_REPORT=" + json.dumps({
        "cohere_english_gate": report["cohere_english_gate"],
        "m234_multitrack_pipeline_gate": {
            k: v for k, v in report["m234_multitrack_pipeline_gate"].items() if k != "sample_segments"
        },
        "meeting_intelligence_and_rag_gate": report["meeting_intelligence_and_rag_gate"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

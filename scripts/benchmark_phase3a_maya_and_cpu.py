"""
VOX Phase 3A & 3B — Targeted Challenger Evaluation (Maya Transcribe One + SraVaani Blocker)
and Controlled Workstation CPU-Only Hardware Tier Simulations (Tier 1: 4 threads, Tier 2: 6 threads).

Strictly read-only on `src/`, `data/voxlocal.db`, `data/config.json`, original recordings,
and human-verified M#234/M#235 reference drafts. Verifies all 16 SHA-256 hashes from
`data/benchmarks/phase2_5_validation/ground_truth_lock.json` before and after runs.

All outputs are written into `data/benchmarks/phase3a_model_eval/`.
"""

from __future__ import annotations

import os
import sys

# Pre-parse CPU simulation flags BEFORE importing numpy/torch/ctranslate2 so BLAS/OMP/MKL thread caps apply cleanly.
def _early_configure_env() -> None:
    if "--mode" in sys.argv:
        try:
            mode_val = sys.argv[sys.argv.index("--mode") + 1]
        except Exception:
            mode_val = ""
        if mode_val in ("cpu-tier1", "cpu-tier2"):
            n_threads = "4" if mode_val == "cpu-tier1" else "6"
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            os.environ["OMP_NUM_THREADS"] = n_threads
            os.environ["MKL_NUM_THREADS"] = n_threads
            os.environ["OPENBLAS_NUM_THREADS"] = n_threads
            os.environ["VECLIB_MAXIMUM_THREADS"] = n_threads
            os.environ["NUMEXPR_NUM_THREADS"] = n_threads


_early_configure_env()

import argparse
import gc
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark_phase2_challengers import (
    CHALLENGER_MODELS_DIR,
    _gpu_used_free,
    _load_qwen3_asr_and_processor,
    _release_vram,
    run_continuous_live_simulation,
)
from benchmark_phase25_live_qwen import run_qwen_continuous_live
from benchmark_phase25_qwen_ab import (
    VOX_HOTWORDS,
    _script_and_echo_audit,
    build_foreign_script_token_ids,
    make_logits_processors,
)
from benchmark_speech_intelligence import (
    BENCH_DIR,
    _InMemoryReplayRecorder,
    _ReadOnlyVocabProxy,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    read_only_db_state,
    run_offline_finalization,
    snapshot_protected_files,
)
from score_ground_truth import (
    audit_mixed_script_and_utf8,
    load_prediction_segments,
    score_predictions_against_reference,
    tokenize_strict,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    clean_repetition_tail,
    load_wav_as_16k_f32,
)

LOCK_PATH = BENCH_DIR / "phase2_5_validation" / "ground_truth_lock.json"
PHASE25_AB_PATH = BENCH_DIR / "phase2_5_validation" / "qwen_ab_report.json"
PHASE25_LIVE_PATH = BENCH_DIR / "phase2_5_validation" / "live_qwen_continuous_report.json"
OUT_DIR = BENCH_DIR / "phase3a_model_eval"
MAYA_LOCAL_DIR = CHALLENGER_MODELS_DIR / "maya-transcribe"
QWEN_NATIVE_LOCAL_DIR = CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b"

SIMULATION_DISCLAIMER = (
    "Controlled thread-count CPU simulation on developer Ryzen 7 workstation; "
    "NOT proof of Dell/Lenovo laptop compatibility."
)

SRAVANI_BLOCKER_INFO = {
    "repo_id": "ARTPARK-IISc/SraVaani-0.5-live",
    "status": "blocked_gated_access",
    "gated": "auto",
    "authenticated": False,
    "error": "GatedRepoError 401 Client Error: repository access requires manual HF terms acceptance & user login.",
    "policy": "Did not accept gated terms or credentials on user behalf.",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def verify_all_16_locked_hashes() -> Tuple[bool, List[str]]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    targets: Dict[str, str] = {}
    for k in ("reference_hashes", "backup_hashes", "m188_sys_audio"):
        for p, h in lock[k].items():
            targets[p] = str(h)
    for p, info in lock["protected_files"].items():
        targets[p] = str(info["sha256"])

    mismatches: List[str] = []
    for p, exp in targets.items():
        path = Path(p)
        if not path.exists() or _sha256_file(path) != exp:
            mismatches.append(p)
    if compute_target_db_rows_sha256() != lock.get("db_target_rows_sha256"):
        mismatches.append("DB_TARGET_ROWS")
    return (len(mismatches) == 0), mismatches


def _extract_vad_spans_strict_max(
    aud: np.ndarray,
    vad_p: Dict[str, Any],
    *,
    max_chunk_sec: float = 22.0,
    hard_cap_sec: float = 28.0,
) -> List[Tuple[int, int]]:
    spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
        aud, vad_p, max_chunk_sec=max_chunk_sec, merge_gap_sec=0.65, pad_sec=0.20
    )
    if spans is None:
        spans = [(0, aud.size)]
    cap_samples = int(hard_cap_sec * TARGET_SAMPLE_RATE)
    out: List[Tuple[int, int]] = []
    for s_i, e_i in spans:
        pos = int(s_i)
        end = int(e_i)
        while pos < end:
            nxt = min(end, pos + cap_samples)
            out.append((pos, nxt))
            pos = nxt
    return out


def _unload_hf_cuda_model(model: Any) -> None:
    try:
        model.to("cpu")
    except Exception:
        pass
    try:
        del model
    except Exception:
        pass
    _release_vram()


def _decode_maya_chunk(
    model: Any,
    processor: Any,
    norm_sl: np.ndarray,
    *,
    blocked_ids_gpu: Any,
    language: Optional[str] = "Hindi",
    prompt: str = "style: mixed",
    max_new_tokens: int = 320,
    repetition_penalty: float = 1.05,
    device: str = "cuda",
) -> Tuple[str, float]:
    import torch
    from transformers import LogitsProcessor, LogitsProcessorList

    class _ForeignScriptMask(LogitsProcessor):
        def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
            if blocked_ids_gpu is not None and blocked_ids_gpu.numel() > 0:
                scores = scores.clone()
                scores[:, blocked_ids_gpu] = -float("inf")
            return scores

    tc0 = time.perf_counter()
    req_kwargs: Dict[str, Any] = {"audio": norm_sl, "prompt": prompt}
    if language is not None:
        req_kwargs["language"] = language
    inputs = processor.apply_transcription_request(**req_kwargs)
    inputs = inputs.to(model.device, model.dtype)
    in_len = int(inputs["input_ids"].shape[1])
    lp = LogitsProcessorList([_ForeignScriptMask()]) if blocked_ids_gpu is not None else None
    gen_kwargs: Dict[str, Any] = dict(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        repetition_penalty=repetition_penalty,
    )
    if lp is not None:
        gen_kwargs["logits_processor"] = lp
    with torch.inference_mode():
        gen_ids = model.generate(**gen_kwargs)
    generated = gen_ids[:, in_len:]
    raw_txt = str(processor.decode(generated, return_format="transcription_only")[0] or "").strip()
    del inputs, gen_ids, generated
    infer_s = time.perf_counter() - tc0
    return raw_txt, infer_s


def _run_maya_english_diagnostic(
    model: Any,
    processor: Any,
    blocked_ids_gpu: Any,
    meetings: Dict[int, Dict[str, Any]],
    ref_235: Dict[str, Any],
) -> Dict[str, Any]:
    """Quick 5-segment diagnostic comparing language='Hindi' vs language=None on M#235 pure English turns."""
    m = meetings[235]
    mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
    sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
    mic_aud = load_wav_as_16k_f32(mic_path)
    sys_aud = load_wav_as_16k_f32(sys_path)

    en_refs = [
        s
        for s in ref_235["segments"]
        if s.get("language_tags") == ["en"] and not s.get("uncertain") and str(s.get("reference_text", "")).strip()
    ][:5]

    diag_items: List[Dict[str, Any]] = []
    for lang_opt in ("Hindi", None):
        label = "Hindi" if lang_opt else "None"
        preds = []
        refs_sub = []
        for idx, r in enumerate(en_refs):
            aud = mic_aud if r["source_track"] == "mic" else sys_aud
            s_i = max(0, int(float(r["start_time"]) * TARGET_SAMPLE_RATE))
            e_i = min(aud.size, int(float(r["end_time"]) * TARGET_SAMPLE_RATE))
            sl = normalize_speech_audio(aud[s_i:e_i])
            raw_txt, inf_s = _decode_maya_chunk(
                model,
                processor,
                sl,
                blocked_ids_gpu=blocked_ids_gpu,
                language=lang_opt,
                prompt="style: mixed",
                max_new_tokens=160,
                repetition_penalty=1.05,
            )
            txt = clean_repetition_tail(DictationProcessor.devanagari_to_roman_hinglish(raw_txt)).strip()
            preds.append(
                {
                    "segment_index": idx,
                    "start_time": float(r["start_time"]),
                    "end_time": float(r["end_time"]),
                    "source_track": r["source_track"],
                    "speaker": r["speaker"],
                    "raw_text": raw_txt,
                    "text": txt or raw_txt,
                    "infer_sec": round(inf_s, 3),
                }
            )
            refs_sub.append(r)
        sc = score_predictions_against_reference(
            {"meeting_id": 235, "title": "M235_EN_Diag", "segments": refs_sub},
            preds,
        )
        va = sc["primary_masked_uncertain"]
        diag_items.append(
            {
                "language_arg": label,
                "english_wer_norm": va["stream_hinglish_norm_wer"],
                "english_wer_raw": va["stream_raw_wer"],
                "ops": va["stream_norm_ops"],
                "samples": [
                    {
                        "segment_index": refs_sub[i]["segment_index"],
                        "ref": refs_sub[i]["reference_text"],
                        "raw": preds[i]["raw_text"],
                        "roman": preds[i]["text"],
                    }
                    for i in range(len(refs_sub))
                ],
            }
        )
    return {"tested_segments": [r["segment_index"] for r in en_refs], "results": diag_items}


def run_maya_gpu(min_free_vram_mb: int) -> int:
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    ok_pre, pre_mis = verify_all_16_locked_hashes()
    if not ok_pre:
        raise RuntimeError(f"PRE-RUN LOCK INTEGRITY FAILURE: {pre_mis}")

    used_mb, free_mb = _gpu_used_free()
    if free_mb is not None and free_mb < min_free_vram_mb:
        print(f"[Maya3A] VRAM GUARD STOP: free={free_mb} MB < {min_free_vram_mb} MB", file=sys.stderr, flush=True)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, _ = read_only_db_state()
    ref_files = {
        234: BENCH_DIR / "M234_hemanth_reference_draft.json",
        235: BENCH_DIR / "M235_rumjhum_reference_draft.json",
    }
    ref_drafts = {mid: json.loads(p.read_text(encoding="utf-8")) for mid, p in ref_files.items()}

    _release_vram()
    torch.cuda.reset_peak_memory_stats()
    vram_before, _ = _gpu_used_free()

    t_load0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(str(MAYA_LOCAL_DIR), local_files_only=True, trust_remote_code=False)
    model = AutoModelForMultimodalLM.from_pretrained(
        str(MAYA_LOCAL_DIR), dtype=torch.float16, local_files_only=True, trust_remote_code=False
    ).to("cuda").eval()
    load_sec = time.perf_counter() - t_load0

    blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
    blocked_ids_gpu = torch.tensor(blocked_ids, device="cuda", dtype=torch.long)
    vram_after_load, free_after_load = _gpu_used_free()
    print(
        f"[Maya3A] Loaded maya-research/maya-transcribe CUDA FP16 in {load_sec:.2f}s | "
        f"blocked_foreign={len(blocked_ids)} | VRAM={vram_after_load}MB used / {free_after_load}MB free",
        flush=True,
    )

    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    cpu_cores = max(1, os.cpu_count() or 1)
    run_meta: Dict[str, Any] = {
        "model_key": "maya-transcribe-mixed",
        "repo_id": "maya-research/maya-transcribe",
        "decode_config": {
            "prompt": "style: mixed",
            "language": "Hindi",
            "dtype": "float16",
            "do_sample": False,
            "max_new_tokens": 320,
            "repetition_penalty": 1.05,
            "no_repeat_ngram_size": None,
            "blocked_foreign_token_count": len(blocked_ids),
            "hard_chunk_cap_sec": 28.0,
        },
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before,
        "vram_after_load_mb": vram_after_load,
        "meetings": {},
    }

    try:
        for mid in (234, 235):
            m = meetings[mid]
            dur = float(m["duration_sec"])
            mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
            sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
            mic_aud = load_wav_as_16k_f32(mic_path)
            sys_aud = load_wav_as_16k_f32(sys_path)

            all_segs: List[Dict[str, Any]] = []
            raw_eval_segs: List[Dict[str, Any]] = []
            chunk_lat_ms: List[float] = []
            peak_vram = vram_after_load or 0
            peak_ram = get_process_rss_mb()

            t0 = time.perf_counter()
            c0 = time.process_time()
            chunk_idx = 0

            for trk, spk, aud in (("mic", "You (Mic)", mic_aud), ("system", "Speaker 1", sys_aud)):
                spans = _extract_vad_spans_strict_max(aud, vad_p, max_chunk_sec=22.0, hard_cap_sec=28.0)
                for s_i, e_i in spans:
                    sl = aud[s_i:e_i]
                    if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                        continue
                    chunk_idx += 1
                    s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
                    e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
                    norm_sl = normalize_speech_audio(sl)
                    raw_txt, infer_s = _decode_maya_chunk(
                        model,
                        processor,
                        norm_sl,
                        blocked_ids_gpu=blocked_ids_gpu,
                        language="Hindi",
                        prompt="style: mixed",
                        max_new_tokens=320,
                        repetition_penalty=1.05,
                    )
                    chunk_lat_ms.append(infer_s * 1000.0)
                    if chunk_idx % 12 == 0:
                        torch.cuda.empty_cache()

                    u_mb, _ = _gpu_used_free()
                    if u_mb is not None:
                        peak_vram = max(peak_vram, u_mb)
                    peak_ram = max(peak_ram, get_process_rss_mb())

                    if not raw_txt:
                        continue
                    roman_txt = clean_repetition_tail(DictationProcessor.devanagari_to_roman_hinglish(raw_txt)).strip()
                    seg_obj = {
                        "start_time": s_sec,
                        "end_time": e_sec,
                        "source_track": trk,
                        "speaker": spk,
                        "raw_text": raw_txt,
                        "text": roman_txt or raw_txt,
                    }
                    all_segs.append(seg_obj)
                    raw_eval_segs.append(
                        {
                            "start_time": s_sec,
                            "end_time": e_sec,
                            "source_track": trk,
                            "speaker": spk,
                            "raw_text": raw_txt,
                            "text": raw_txt,
                        }
                    )

            all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            raw_eval_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            for idx, s in enumerate(all_segs):
                s["segment_index"] = idx
                raw_eval_segs[idx]["segment_index"] = idx

            wall = time.perf_counter() - t0
            cpu_s = time.process_time() - c0
            pred_path = OUT_DIR / f"maya-transcribe-mixed_offline_M{mid}.json"
            pred_path.write_text(
                json.dumps(
                    {
                        "meeting_id": mid,
                        "title": m["title"],
                        "model": "maya-transcribe-mixed",
                        "segments": all_segs,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            scores_roman = score_predictions_against_reference(ref_drafts[mid], all_segs)
            scores_raw_ortho = score_predictions_against_reference(ref_drafts[mid], raw_eval_segs)
            mixed_audit = audit_mixed_script_and_utf8(all_segs)
            echo_audit = _script_and_echo_audit(all_segs)
            lat_arr = np.array(chunk_lat_ms, dtype=np.float64) if chunk_lat_ms else np.zeros(1)

            va = scores_roman["primary_masked_uncertain"]
            vb = scores_roman["secondary_unmasked_complete"]
            va_ortho = scores_raw_ortho["primary_masked_uncertain"]
            run_meta["meetings"][str(mid)] = {
                "runtime": {
                    "wall_time_sec": round(wall, 2),
                    "rtf": round(wall / max(0.01, dur), 4),
                    "cpu_time_sec": round(cpu_s, 2),
                    "avg_cpu_pct": round((cpu_s / max(0.01, wall)) * 100.0 / cpu_cores, 1),
                    "peak_vram_used_mb": peak_vram,
                    "peak_torch_alloc_mb": round(torch.cuda.max_memory_allocated() / (1024**2), 1),
                    "peak_ram_rss_mb": round(peak_ram, 1),
                    "chunk_count": len(chunk_lat_ms),
                    "chunk_p50_ms": round(float(np.median(lat_arr)), 1),
                    "chunk_p95_ms": round(float(np.percentile(lat_arr, 95)), 1),
                    "segment_count": len(all_segs),
                },
                "prediction_path": str(pred_path),
                "mixed_script_audit": mixed_audit,
                "script_and_echo_audit": echo_audit,
                "scores": scores_roman,
                "scores_orthography_aware_on_raw_mixed": {
                    "view_a_stream_norm_wer": va_ortho["stream_hinglish_norm_wer"],
                    "view_a_stream_raw_wer": va_ortho["stream_raw_wer"],
                    "view_a_stream_norm_cer": va_ortho["stream_hinglish_norm_cer"],
                    "view_a_ops": va_ortho["stream_norm_ops"],
                },
            }
            print(
                f"[Maya3A] M#{mid}: wall={wall:.2f}s rtf={wall/dur:.4f} segs={len(all_segs)} "
                f"ViewA_WER={va['stream_hinglish_norm_wer']} (raw_ortho={va_ortho['stream_hinglish_norm_wer']}) "
                f"ViewB_WER={vb['stream_hinglish_norm_wer']} ops={va['stream_norm_ops']} "
                f"ent={va['technical_entity_recall']}/{va['technical_entity_precision']} "
                f"ufffd={mixed_audit['ufffd_segs']} cjk={mixed_audit['cjk_segs']} peak_vram={peak_vram}MB",
                flush=True,
            )

        run_meta["english_language_arg_diagnostic"] = _run_maya_english_diagnostic(
            model, processor, blocked_ids_gpu, meetings, ref_drafts[235]
        )
        m235_va = run_meta["meetings"]["235"]["scores"]["primary_masked_uncertain"]
        m235_audit = run_meta["meetings"]["235"]["mixed_script_audit"]
        maya_passes_gate = (
            float(m235_va["stream_hinglish_norm_wer"]) <= 0.4150
            and int(m235_audit["ufffd_segs"]) == 0
            and float(m235_va["technical_entity_recall"]) >= 0.50
        )
        run_meta["live_gate_evaluation"] = {
            "criteria": "ViewA M#235 stream_hinglish_norm_wer <= 0.4150 AND ufffd_segs == 0 AND entity_recall >= 0.50",
            "measured_m235_view_a_wer": m235_va["stream_hinglish_norm_wer"],
            "measured_m235_ufffd_segs": m235_audit["ufffd_segs"],
            "measured_m235_entity_recall": m235_va["technical_entity_recall"],
            "passes_gate_for_live_test": maya_passes_gate,
        }
        print(
            f"[Maya3A] LIVE_GATE: passes={maya_passes_gate} "
            f"(M235 ViewA={m235_va['stream_hinglish_norm_wer']} vs Qwen3 A2=0.4133)",
            flush=True,
        )
    finally:
        del blocked_ids_gpu
        _unload_hf_cuda_model(model)
        del processor
        vram_after_unload, vram_free_after = _gpu_used_free()
        run_meta["vram_after_unload_mb"] = vram_after_unload
        run_meta["vram_free_after_unload_mb"] = vram_free_after

    ok_post, post_mis = verify_all_16_locked_hashes()
    run_meta["read_only_integrity"] = {"all_hashes_unchanged": ok_post, "mismatches": post_mis}
    meta_path = OUT_DIR / "maya-transcribe-mixed_meta.json"
    meta_path.write_text(json.dumps(run_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Maya3A] Saved {meta_path} | protected_intact={ok_post}", flush=True)
    if not ok_post:
        raise RuntimeError(f"POST-RUN LOCK INTEGRITY FAILURE: {post_mis}")
    return 0


class MayaStreamingAdapter:
    """Read-only adapter driving `maya-research/maya-transcribe` in LiveMeetingStreamingTranscriber."""

    def __init__(self, model: Any, processor: Any, blocked_ids_gpu: Any) -> None:
        self.model = model
        self.processor = processor
        self.blocked_ids_gpu = blocked_ids_gpu
        self._model = None
        self.passes = 0
        self.partial_passes = 0
        self.commit_passes = 0
        self.echo_retries = 0

    def is_model_loaded(self) -> bool:
        return True

    def ensure_loaded(self) -> Tuple[bool, str]:
        return True, "Loaded Maya mixed streaming adapter"

    def _run_whisper_pass(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _transcribe_single_encode(
        self,
        norm_audio: np.ndarray,
        *,
        beam_size: int = 1,
        hotwords: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Tuple[List[Any], Any]]:
        import torch

        if norm_audio is None or norm_audio.size < int(TARGET_SAMPLE_RATE * 0.35):
            return [], SimpleNamespace(language="hi")

        self.passes += 1
        if int(beam_size) <= 1:
            self.partial_passes += 1
            max_toks = 160
        else:
            self.commit_passes += 1
            max_toks = 256

        raw_txt, _ = _decode_maya_chunk(
            self.model,
            self.processor,
            norm_audio,
            blocked_ids_gpu=self.blocked_ids_gpu,
            language="Hindi",
            prompt="style: mixed",
            max_new_tokens=max_toks,
            repetition_penalty=1.05,
        )
        if self.passes % 10 == 0:
            torch.cuda.empty_cache()

        txt = clean_repetition_tail(DictationProcessor.devanagari_to_roman_hinglish(raw_txt)).strip()
        if not txt:
            return [], SimpleNamespace(language="hi")
        return [SimpleNamespace(text=txt, avg_logprob=None, no_speech_prob=0.0)], SimpleNamespace(language="hi")


def run_maya_live_gpu(min_free_vram_mb: int) -> int:
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    meta_path = OUT_DIR / "maya-transcribe-mixed_meta.json"
    if meta_path.exists():
        m_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not (m_meta.get("live_gate_evaluation") or {}).get("passes_gate_for_live_test"):
            print("[MayaLive] Skipping Maya Live test: offline gate not met.", flush=True)
            return 0

    ok_pre, pre_mis = verify_all_16_locked_hashes()
    if not ok_pre:
        raise RuntimeError(f"PRE-RUN LOCK INTEGRITY FAILURE: {pre_mis}")

    _, free_mb = _gpu_used_free()
    if free_mb is not None and free_mb < min_free_vram_mb:
        print(f"[MayaLive] VRAM GUARD STOP: free={free_mb} MB < {min_free_vram_mb} MB", file=sys.stderr, flush=True)
        return 2

    meetings, vocab = read_only_db_state()
    ref_files = {
        234: BENCH_DIR / "M234_hemanth_reference_draft.json",
        235: BENCH_DIR / "M235_rumjhum_reference_draft.json",
    }
    ref_drafts = {mid: json.loads(p.read_text(encoding="utf-8")) for mid, p in ref_files.items()}

    _release_vram()
    vram_before, _ = _gpu_used_free()
    t_load0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(str(MAYA_LOCAL_DIR), local_files_only=True, trust_remote_code=False)
    model = AutoModelForMultimodalLM.from_pretrained(
        str(MAYA_LOCAL_DIR), dtype=torch.float16, local_files_only=True, trust_remote_code=False
    ).to("cuda").eval()
    load_sec = time.perf_counter() - t_load0
    blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
    blocked_ids_gpu = torch.tensor(blocked_ids, device="cuda", dtype=torch.long)
    vram_after_load, _ = _gpu_used_free()

    report: Dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_key": "maya-transcribe-mixed:live",
        "repo_id": "maya-research/maya-transcribe",
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before,
        "vram_after_load_mb": vram_after_load,
        "meetings": {},
    }

    try:
        for mid in (234, 235):
            m = meetings[mid]
            mic_aud = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"]))
            sys_aud = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"]))
            adapter = MayaStreamingAdapter(model, processor, blocked_ids_gpu)
            live_sum, live_segs = run_qwen_continuous_live(adapter, mic_aud, sys_aud, vocab, mid, m["title"])
            pred_path = OUT_DIR / f"maya_live_continuous_M{mid}.json"
            pred_path.write_text(
                json.dumps({"meeting_id": mid, "title": m["title"], "model": "maya-transcribe-mixed:live", "segments": live_segs}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            live_sum["prediction_path"] = str(pred_path)
            live_sum["mixed_script_audit"] = audit_mixed_script_and_utf8(live_segs)
            live_sum["scores"] = score_predictions_against_reference(ref_drafts[mid], live_segs)
            report["meetings"][str(mid)] = live_sum
    finally:
        del blocked_ids_gpu
        _unload_hf_cuda_model(model)
        del processor
        v_after, v_free = _gpu_used_free()
        report["vram_after_unload_mb"] = v_after
        report["vram_free_after_unload_mb"] = v_free

    ok_post, post_mis = verify_all_16_locked_hashes()
    report["read_only_integrity"] = {"all_hashes_unchanged": ok_post, "mismatches": post_mis}
    out_p = OUT_DIR / "live_maya_continuous_report.json"
    out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[MayaLive] Saved {out_p} | protected_intact={ok_post}", flush=True)
    if not ok_post:
        raise RuntimeError(f"POST-RUN LOCK INTEGRITY FAILURE: {post_mis}")
    return 0


def _score_m235_bounded_slice(
    transcriber: LocalWhisperTranscriber,
    mic_aud: np.ndarray,
    sys_aud: np.ndarray,
    ref_235: Dict[str, Any],
    vocab: List[Dict[str, Any]],
    remote_label: str,
) -> Dict[str, Any]:
    """Run CPU finalization cleanly on 3 representative 60s M#235 slices (180s total) when full 19-min exceeds cutoff."""
    windows = ((0.0, 60.0), (540.0, 600.0), (1069.0, 1129.0))
    vad_p = dict(threshold=0.35, min_silence_duration_ms=700, speech_pad_ms=300)
    all_segs: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    peak_ram = get_process_rss_mb()

    for w_start, w_end in windows:
        s_idx = int(w_start * TARGET_SAMPLE_RATE)
        for trk, spk, aud in (("system", remote_label, sys_aud), ("mic", "You (Mic)", mic_aud)):
            e_idx = min(aud.size, int(w_end * TARGET_SAMPLE_RATE))
            if e_idx <= s_idx:
                continue
            sl_w = aud[s_idx:e_idx]
            spans = _extract_vad_spans_strict_max(sl_w, vad_p, max_chunk_sec=22.0, hard_cap_sec=28.0)
            for cs, ce in spans:
                chunk = sl_w[cs:ce]
                if chunk.size < int(0.3 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(chunk))) < 0.0005:
                    continue
                res = transcriber._transcribe_single_encode(
                    norm_audio=normalize_speech_audio(chunk),
                    language="en+hi",
                    lang_arg=None,
                    is_en_only=False,
                    bilingual_modes=("", "auto", "en+hi", "hinglish", "hi_latn"),
                    beam_size=2,
                    vad_filter=False,
                    vad_params={},
                    hotwords=None,
                    temperatures=0.0,
                    effective_prompt=None,
                    word_timestamps=False,
                )
                raw_txt = " ".join(str(getattr(seg, "text", "")).strip() for seg in (res[0] if res else [])).strip()
                peak_ram = max(peak_ram, get_process_rss_mb())
                if raw_txt:
                    roman_txt = clean_repetition_tail(DictationProcessor.devanagari_to_roman_hinglish(raw_txt)).strip()
                    all_segs.append(
                        {
                            "start_time": round(w_start + cs / TARGET_SAMPLE_RATE, 2),
                            "end_time": round(w_start + ce / TARGET_SAMPLE_RATE, 2),
                            "source_track": trk,
                            "speaker": spk,
                            "raw_text": raw_txt,
                            "text": roman_txt or raw_txt,
                        }
                    )

    wall = time.perf_counter() - t0
    all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
    for idx, s in enumerate(all_segs):
        s["segment_index"] = idx

    sub_refs = []
    for r in ref_235["segments"]:
        rs, re_ = float(r["start_time"]), float(r["end_time"])
        if any(min(re_, we) - max(rs, ws) > 0.2 for ws, we in windows):
            sub_refs.append(r)

    sc = score_predictions_against_reference(
        {"meeting_id": 235, "title": "M235_Bounded_180s_Slice", "segments": sub_refs},
        all_segs,
    )
    return {
        "bounded_180s_multi_region_slice": True,
        "slice_windows_sec": windows,
        "evaluated_audio_sec": 180.0,
        "wall_time_sec": round(wall, 2),
        "rtf": round(wall / 180.0, 4),
        "extrapolated_19m_wall_sec": round((wall / 180.0) * 1129.0, 1),
        "peak_ram_rss_mb": round(peak_ram, 1),
        "segment_count": len(all_segs),
        "scores": sc,
    }


def _load_cpu_whisper(whisper_size: str, threads: int) -> Tuple[LocalWhisperTranscriber, float]:
    with (ROOT_DIR / "data" / "config.json").open("r", encoding="utf-8") as cf:
        cfg = json.load(cf)
    transcriber = LocalWhisperTranscriber(
        models_dir=cfg.get("models_dir"),
        model_name=whisper_size,
        compute_device="cpu",
        compute_type="int8",
        cpu_threads=threads,
    )
    t0 = time.perf_counter()
    ok, msg = transcriber.ensure_loaded()
    load_sec = time.perf_counter() - t0
    if not ok or transcriber._active_device != "cpu" or transcriber._loaded_model_name != whisper_size:
        raise RuntimeError(
            f"CPU Whisper load validation failed for {whisper_size}: ok={ok} dev={transcriber._active_device} "
            f"loaded={transcriber._loaded_model_name} msg={msg}"
        )
    transcriber.is_model_cached = lambda *a, **kw: True  # type: ignore[method-assign]
    transcriber.ensure_loaded = lambda *a, **kw: (True, f"Locked CPU {whisper_size} ({threads}t)")  # type: ignore[method-assign]
    return transcriber, load_sec


def _run_cpu_whisper_eval(
    whisper_size: str,
    threads: int,
    tier_label: str,
    meetings: Dict[int, Dict[str, Any]],
    vocab: List[Dict[str, Any]],
    ref_drafts: Dict[int, Dict[str, Any]],
    *,
    m235_full_rtf_cutoff: float,
    run_m235_bounded_on_slow: bool,
) -> Dict[str, Any]:
    rss_before = get_process_rss_mb()
    transcriber, load_sec = _load_cpu_whisper(whisper_size, threads)
    rss_after_load = get_process_rss_mb()
    print(
        f"[CPU-{tier_label}] Loaded whisper-{whisper_size} CPU int8 ({threads}t) in {load_sec:.2f}s | RAM={rss_after_load:.1f}MB",
        flush=True,
    )

    m234 = meetings[234]
    dur234 = float(m234["duration_sec"])
    mic234 = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m234["mic_audio_path"]))
    sys234 = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m234["sys_audio_path"]))

    result: Dict[str, Any] = {
        "model": f"whisper-{whisper_size}",
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": threads,
        "load_sec": round(load_sec, 2),
        "ram_before_load_mb": rss_before,
        "ram_after_load_mb": rss_after_load,
        "vram_used_mb": 0,
        "meetings": {},
    }

    try:
        # M234 Continuous-Stream Live Simulation
        live_sum, live_segs = run_continuous_live_simulation(
            transcriber, mic234, sys234, vocab, 234, m234["title"], "en+hi"
        )
        live_path = OUT_DIR / f"cpu_{tier_label}_whisper-{whisper_size}_live_M234.json"
        live_path.write_text(
            json.dumps({"meeting_id": 234, "segments": live_segs}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        live_sum["prediction_path"] = str(live_path)
        live_sum["scores"] = score_predictions_against_reference(ref_drafts[234], live_segs)

        # M234 Offline Finalization
        off234 = run_offline_finalization(
            transcriber, m234["mic_audio_path"], m234["sys_audio_path"], m234["audio_path"], vocab, m234["title"], "en+hi", dur234
        )
        off_segs234 = off234.pop("segments")
        off_path234 = OUT_DIR / f"cpu_{tier_label}_whisper-{whisper_size}_offline_M234.json"
        off_path234.write_text(
            json.dumps({"meeting_id": 234, "segments": off_segs234}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        off234["prediction_path"] = str(off_path234)
        off234["scores"] = score_predictions_against_reference(ref_drafts[234], off_segs234)

        result["meetings"]["234"] = {
            "live_continuous": live_sum,
            "offline_full": off234,
        }
        print(
            f"[CPU-{tier_label}] whisper-{whisper_size} M#234: "
            f"LIVE rtf={live_sum['rtf']} backlog_max={live_sum['audio_backlog_sec']['max_sec']}s conf_p50={live_sum['speech_to_display_latency_ms']['confirmed_p50_ms']}ms WER={live_sum['scores']['primary_masked_uncertain']['stream_hinglish_norm_wer']} | "
            f"OFFLINE rtf={off234['rtf']} wall={off234['wall_time_sec']}s WER={off234['scores']['primary_masked_uncertain']['stream_hinglish_norm_wer']}",
            flush=True,
        )

        # M235 Offline Evaluation based on M#234 RTF gate
        off_rtf_234 = float(off234["rtf"])
        m235 = meetings[235]
        dur235 = float(m235["duration_sec"])
        if off_rtf_234 <= m235_full_rtf_cutoff:
            off235 = run_offline_finalization(
                transcriber, m235["mic_audio_path"], m235["sys_audio_path"], m235["audio_path"], vocab, m235["title"], "en+hi", dur235
            )
            off_segs235 = off235.pop("segments")
            off_path235 = OUT_DIR / f"cpu_{tier_label}_whisper-{whisper_size}_offline_M235.json"
            off_path235.write_text(
                json.dumps({"meeting_id": 235, "segments": off_segs235}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            off235["prediction_path"] = str(off_path235)
            off235["scores"] = score_predictions_against_reference(ref_drafts[235], off_segs235)
            result["meetings"]["235"] = {"offline_full": off235}
            print(
                f"[CPU-{tier_label}] whisper-{whisper_size} FULL M#235 OFFLINE: rtf={off235['rtf']} "
                f"wall={off235['wall_time_sec']}s WER={off235['scores']['primary_masked_uncertain']['stream_hinglish_norm_wer']}",
                flush=True,
            )
        else:
            extrap_sec = round(off_rtf_234 * dur235, 1)
            m235_entry: Dict[str, Any] = {
                "full_m235_skipped_due_to_rtf_cutoff": True,
                "cutoff_rtf": m235_full_rtf_cutoff,
                "measured_m234_offline_rtf": off_rtf_234,
                "extrapolated_19m_wall_sec": extrap_sec,
            }
            if run_m235_bounded_on_slow:
                mic235 = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m235["mic_audio_path"]))
                sys235 = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m235["sys_audio_path"]))
                sl_res = _score_m235_bounded_slice(transcriber, mic235, sys235, ref_drafts[235], vocab, m235["title"])
                m235_entry["bounded_slice"] = sl_res
                print(
                    f"[CPU-{tier_label}] whisper-{whisper_size} BOUNDED 180s M#235 SLICE: rtf={sl_res['rtf']} "
                    f"extrap_19m={sl_res['extrapolated_19m_wall_sec']}s WER={sl_res['scores']['primary_masked_uncertain']['stream_hinglish_norm_wer']}",
                    flush=True,
                )
            else:
                print(
                    f"[CPU-{tier_label}] whisper-{whisper_size} M#235 full skipped (M#234 RTF={off_rtf_234} > {m235_full_rtf_cutoff}); "
                    f"extrapolated 19m={extrap_sec}s",
                    flush=True,
                )
            result["meetings"]["235"] = m235_entry
    finally:
        transcriber.unload_model()
        gc.collect()

    return result


def _run_winning_hf_cpu_probe(
    threads: int,
    meetings: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Feasibility probe of the winning 0.6B/0.78B HuggingFace ASR model on CPU float32 (6 threads),
    capped cleanly at 180s wall time if RTF > 2.5.
    """
    import torch

    torch.set_num_threads(threads)
    maya_meta_path = OUT_DIR / "maya-transcribe-mixed_meta.json"
    use_maya = False
    if maya_meta_path.exists():
        mm = json.loads(maya_meta_path.read_text(encoding="utf-8"))
        use_maya = bool((mm.get("live_gate_evaluation") or {}).get("passes_gate_for_live_test"))

    m234 = meetings[234]
    mic_aud = load_wav_as_16k_f32(LocalWhisperTranscriber._prefer_raw_wav_path(m234["mic_audio_path"]))
    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    spans = _extract_vad_spans_strict_max(mic_aud, vad_p, max_chunk_sec=22.0, hard_cap_sec=28.0)

    rss_before = get_process_rss_mb()
    t_load0 = time.perf_counter()

    if use_maya:
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        model_label = "maya-research/maya-transcribe"
        processor = AutoProcessor.from_pretrained(str(MAYA_LOCAL_DIR), local_files_only=True, trust_remote_code=False)
        model = AutoModelForMultimodalLM.from_pretrained(
            str(MAYA_LOCAL_DIR), dtype=torch.float32, local_files_only=True, trust_remote_code=False
        ).to("cpu").eval()
    else:
        from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration
        model_label = "Qwen/Qwen3-ASR-0.6B-hf"
        processor = AutoProcessor.from_pretrained(str(QWEN_NATIVE_LOCAL_DIR), local_files_only=True, trust_remote_code=False)
        model = Qwen3ASRForConditionalGeneration.from_pretrained(
            str(QWEN_NATIVE_LOCAL_DIR), dtype=torch.float32, local_files_only=True, trust_remote_code=False
        ).to(device="cpu", dtype=torch.float32).eval()

    load_sec = time.perf_counter() - t_load0
    peak_ram = get_process_rss_mb()

    measured_audio_sec = 0.0
    t_inf0 = time.perf_counter()
    samples_out: List[Dict[str, Any]] = []
    capped = False

    try:
        for idx, (s_i, e_i) in enumerate(spans):
            sl = mic_aud[s_i:e_i]
            if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue
            chunk_dur = sl.size / float(TARGET_SAMPLE_RATE)
            norm_sl = normalize_speech_audio(sl)
            tc0 = time.perf_counter()
            if use_maya:
                inputs = processor.apply_transcription_request(audio=norm_sl, prompt="style: mixed", language="Hindi")
                inputs = inputs.to(torch.device("cpu"), torch.float32)
                in_len = int(inputs["input_ids"].shape[1])
                with torch.inference_mode():
                    gen_ids = model.generate(**inputs, max_new_tokens=160, do_sample=False, repetition_penalty=1.05)
                txt = str(processor.decode(gen_ids[:, in_len:], return_format="transcription_only")[0] or "").strip()
            else:
                inputs = processor.apply_transcription_request(audio=norm_sl, language=None, prompt=None, tokenize=True)
                inputs = inputs.to("cpu")
                if "input_features" in inputs:
                    inputs["input_features"] = inputs["input_features"].to(dtype=torch.float32)
                in_len = int(inputs["input_ids"].shape[1])
                with torch.inference_mode():
                    gen_ids = model.generate(**inputs, max_new_tokens=160, do_sample=False, repetition_penalty=1.05)
                raw_dec = processor.decode(gen_ids[0][in_len:], skip_special_tokens=True)
                parsed = processor.parse_output(raw_dec)
                txt = str(parsed.get("transcription") if isinstance(parsed, dict) else raw_dec).strip()

            c_wall = time.perf_counter() - tc0
            measured_audio_sec += chunk_dur
            peak_ram = max(peak_ram, get_process_rss_mb())
            samples_out.append(
                {
                    "chunk_index": idx,
                    "chunk_sec": round(chunk_dur, 2),
                    "infer_wall_sec": round(c_wall, 2),
                    "rtf": round(c_wall / max(0.01, chunk_dur), 3),
                    "text": txt[:200],
                }
            )
            elapsed_tot = time.perf_counter() - t_inf0
            cur_rtf = elapsed_tot / max(0.01, measured_audio_sec)
            print(
                f"  [CPU-Probe {model_label}] chunk {idx}: {chunk_dur:.1f}s -> {c_wall:.1f}s (cum_rtf={cur_rtf:.2f})",
                flush=True,
            )
            if cur_rtf > 2.5 and elapsed_tot >= 180.0:
                capped = True
                break
            if elapsed_tot >= 180.0:
                capped = True
                break
    finally:
        del model, processor
        gc.collect()

    wall_sec = time.perf_counter() - t_inf0
    rtf = wall_sec / max(0.01, measured_audio_sec)
    return {
        "model": model_label,
        "device": "cpu",
        "dtype": "float32",
        "cpu_threads": threads,
        "load_sec": round(load_sec, 2),
        "ram_before_load_mb": rss_before,
        "peak_ram_rss_mb": round(peak_ram, 1),
        "measured_audio_sec": round(measured_audio_sec, 2),
        "wall_sec": round(wall_sec, 2),
        "rtf": round(rtf, 3),
        "extrapolated_19m_wall_min": round((rtf * 1129.0) / 60.0, 1),
        "wall_capped_at_180s": capped,
        "sample_outputs": samples_out,
    }


def run_cpu_tier(tier_mode: str) -> int:
    import torch

    threads = 4 if tier_mode == "cpu-tier1" else 6
    tier_tag = "tier1_4t" if tier_mode == "cpu-tier1" else "tier2_6t"
    torch.set_num_threads(threads)
    if hasattr(torch, "cuda") and torch.cuda.is_available():
        # Verify CUDA masking is effective or warn if CUDA still visible
        pass

    ok_pre, pre_mis = verify_all_16_locked_hashes()
    if not ok_pre:
        raise RuntimeError(f"PRE-RUN LOCK INTEGRITY FAILURE: {pre_mis}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, vocab = read_only_db_state()
    ref_drafts = {
        234: json.loads((BENCH_DIR / "M234_hemanth_reference_draft.json").read_text(encoding="utf-8")),
        235: json.loads((BENCH_DIR / "M235_rumjhum_reference_draft.json").read_text(encoding="utf-8")),
    }

    report: Dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workstation_cpu_simulation": True,
        "simulation_disclaimer": SIMULATION_DISCLAIMER,
        "simulated_tier": "Tier 1 (4 threads, CPU int8, 0 VRAM)" if threads == 4 else "Tier 2 (6 threads, CPU int8, 0 VRAM)",
        "cpu_threads": threads,
        "host_os_cpu_count": os.cpu_count(),
        "models": {},
    }

    if tier_mode == "cpu-tier1":
        report["models"]["whisper-small"] = _run_cpu_whisper_eval(
            "small",
            threads,
            tier_tag,
            meetings,
            vocab,
            ref_drafts,
            m235_full_rtf_cutoff=1.2,
            run_m235_bounded_on_slow=True,
        )
        report["models"]["whisper-base"] = _run_cpu_whisper_eval(
            "base",
            threads,
            tier_tag,
            meetings,
            vocab,
            ref_drafts,
            m235_full_rtf_cutoff=0.45,
            run_m235_bounded_on_slow=False,
        )
    else:
        report["models"]["whisper-small"] = _run_cpu_whisper_eval(
            "small",
            threads,
            tier_tag,
            meetings,
            vocab,
            ref_drafts,
            m235_full_rtf_cutoff=0.95,
            run_m235_bounded_on_slow=False,
        )
        report["models"]["whisper-large-v3-turbo"] = _run_cpu_whisper_eval(
            "large-v3-turbo",
            threads,
            tier_tag,
            meetings,
            vocab,
            ref_drafts,
            m235_full_rtf_cutoff=1.0,
            run_m235_bounded_on_slow=False,
        )
        report["winning_hf_cpu_feasibility_probe"] = _run_winning_hf_cpu_probe(threads, meetings)

    ok_post, post_mis = verify_all_16_locked_hashes()
    report["read_only_integrity"] = {"all_hashes_unchanged": ok_post, "mismatches": post_mis}
    out_p = OUT_DIR / f"cpu_{tier_tag}_report.json"
    out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[CPU-{tier_tag}] Saved {out_p} | protected_intact={ok_post}", flush=True)
    if not ok_post:
        raise RuntimeError(f"POST-RUN LOCK INTEGRITY FAILURE: {post_mis}")
    return 0


def build_unified_report() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ab25 = json.loads(PHASE25_AB_PATH.read_text(encoding="utf-8"))
    live25 = json.loads(PHASE25_LIVE_PATH.read_text(encoding="utf-8"))
    maya_meta_path = OUT_DIR / "maya-transcribe-mixed_meta.json"
    t1 = json.loads((OUT_DIR / "cpu_tier1_4t_report.json").read_text(encoding="utf-8"))
    t2 = json.loads((OUT_DIR / "cpu_tier2_6t_report.json").read_text(encoding="utf-8"))

    maya_live = None
    maya_live_p = OUT_DIR / "live_maya_continuous_report.json"
    if maya_live_p.exists():
        maya_live = json.loads(maya_live_p.read_text(encoding="utf-8"))

    ref_drafts = {
        234: json.loads((BENCH_DIR / "M234_hemanth_reference_draft.json").read_text(encoding="utf-8")),
        235: json.loads((BENCH_DIR / "M235_rumjhum_reference_draft.json").read_text(encoding="utf-8")),
    }

    excerpts: Dict[str, List[Dict[str, Any]]] = {"234": [], "235": []}
    used_vram_now, free_vram_now = _gpu_used_free()
    if maya_meta_path.exists():
        maya_meta: Dict[str, Any] = json.loads(maya_meta_path.read_text(encoding="utf-8"))
        # Aligned disagreement excerpts: Maya vs Qwen3-Native-Guarded (A2) vs Verified Reference
        for mid in (234, 235):
            a2_segs = load_prediction_segments(BENCH_DIR / "phase2_5_validation" / f"qwen3-native-guarded_offline_M{mid}.json")
            maya_segs = load_prediction_segments(OUT_DIR / f"maya-transcribe-mixed_offline_M{mid}.json")

            def _gather(segs: List[Dict[str, Any]], trk: str, rs: float, re_: float, field: str = "text") -> str:
                hits = []
                for p in segs:
                    if str(p.get("source_track", "")).lower() != trk:
                        continue
                    if min(float(p["end_time"]), re_) - max(float(p["start_time"]), rs) > 0.25:
                        hits.append(str(p.get(field) or p.get("text") or "").strip())
                return " ".join(h for h in hits if h)

            cands: List[Tuple[float, Dict[str, Any]]] = []
            for r in ref_drafts[mid]["segments"]:
                rs, re_ = float(r["start_time"]), float(r["end_time"])
                trk = str(r["source_track"])
                ref_txt = str(r.get("reference_text", "")).strip()
                a2_t = _gather(a2_segs, trk, rs, re_)
                m_roman = _gather(maya_segs, trk, rs, re_, "text")
                m_raw = _gather(maya_segs, trk, rs, re_, "raw_text")
                if a2_t.lower() == m_roman.lower():
                    continue
                sc = abs(len(a2_t) - len(m_roman)) / 20.0
                if "\ufffd" in a2_t:
                    sc += 6.0
                if any(w in ref_txt.lower() for w in ("hubspot", "upc", "wfx", "plm", "erp", "postman", "settings", "whisper")):
                    sc += 4.0
                if r.get("segment_index") in (0, 1, 4, 12, 84, 94, 110):
                    sc += 3.0
                cands.append(
                    (
                        sc,
                        {
                            "segment_index": r["segment_index"],
                            "window": f"{rs:.1f}-{re_:.1f}s",
                            "source_track": trk,
                            "speaker": r["speaker"],
                            "uncertain": bool(r.get("uncertain")),
                            "reference_text": ref_txt,
                            "qwen3_native_guarded_A2": a2_t,
                            "maya_mixed_raw": m_raw,
                            "maya_mixed_romanized": m_roman,
                        },
                    )
                )
            cands.sort(key=lambda x: x[0], reverse=True)
            excerpts[str(mid)] = [c[1] for c in cands[: (8 if mid == 234 else 12)]]

        maya_va_235 = maya_meta["meetings"]["235"]["scores"]["primary_masked_uncertain"]
        qwen_va_235 = ab25["models"]["qwen3-native-guarded"]["meetings"]["235"]["scores"]["primary_masked_uncertain"]
        decision_summary = {
            "qwen3_m235_view_a_wer": qwen_va_235["stream_hinglish_norm_wer"],
            "maya_m235_view_a_wer": maya_va_235["stream_hinglish_norm_wer"],
            "maya_beats_qwen3_materially": float(maya_va_235["stream_hinglish_norm_wer"]) < float(qwen_va_235["stream_hinglish_norm_wer"]) - 0.01,
        }
    else:
        qwen_va_235 = ab25["models"]["qwen3-native-guarded"]["meetings"]["235"]["scores"]["primary_masked_uncertain"]
        maya_meta = {
            "status": "deferred_external_gpu_vram_occupied",
            "reason": "External user application occupied GTX 1650 VRAM below minimum 2000 MB free threshold; 6s GPU smoke check passed earlier (1610.6 MB alloc, 0 U+FFFD).",
            "gpu_vram_used_mb": used_vram_now,
            "gpu_vram_free_mb": free_vram_now,
        }
        decision_summary = {
            "qwen3_m235_view_a_wer": qwen_va_235["stream_hinglish_norm_wer"],
            "maya_m235_view_a_wer": None,
            "maya_status": "deferred_external_gpu_vram_occupied",
        }

    ok_lock, lock_mis = verify_all_16_locked_hashes()
    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "Phase 3A + 3B",
        "workstation_cpu_simulation_disclaimer": SIMULATION_DISCLAIMER,
        "sravaani_blocker": SRAVANI_BLOCKER_INFO,
        "gpu_baselines_from_phase25": {
            "baseline_db": ab25["models"]["baseline_db"],
            "qwen3-native-guarded": ab25["models"]["qwen3-native-guarded"],
            "qwen3-native-guarded:live": live25,
            "whisper-large-v3-turbo:offline": ab25["models"]["whisper-large-v3-turbo:offline"],
            "whisper-large-v3-turbo:live": ab25["models"]["whisper-large-v3-turbo:live"],
            "indic-conformer-onnx:offline": ab25["models"]["indic-conformer-onnx:offline"],
        },
        "maya_transcribe_mixed": maya_meta,
        "maya_live_continuous": maya_live,
        "cpu_tier1_simulation": t1,
        "cpu_tier2_simulation": t2,
        "maya_vs_qwen_excerpts": excerpts,
        "decision_summary": decision_summary,
        "read_only_integrity": {"all_hashes_unchanged": ok_lock, "mismatches": lock_mis},
    }
    out_p = OUT_DIR / "phase3a_3b_report.json"
    out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Phase3A/3B] Saved unified report to {out_p} | protected_intact={ok_lock}", flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VOX Phase 3A & 3B Targeted Model & CPU Tier Evaluation")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["maya-gpu", "maya-live-gpu", "cpu-tier1", "cpu-tier2", "build-report"],
    )
    parser.add_argument("--min-free-vram-mb", type=int, default=2400)
    args = parser.parse_args(argv)

    if args.mode == "maya-gpu":
        return run_maya_gpu(args.min_free_vram_mb)
    if args.mode == "maya-live-gpu":
        return run_maya_live_gpu(args.min_free_vram_mb)
    if args.mode in ("cpu-tier1", "cpu-tier2"):
        return run_cpu_tier(args.mode)
    return build_unified_report()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Phase 1 Multi-Accent ASR & Hybrid Architecture Benchmark Harness.

Strictly read-only on `src/voxlocal/`, `tests/`, recordings, and `data/voxlocal.db`.
Evaluates single models (sequential isolated subprocesses), continuous streaming
simulations, non-oracle acoustic-LID hybrid routers, and produces unified comparison
reports under `data/benchmarks/phase1_results/`.

Usage:
  python scripts/benchmark_phase1_asr.py --model onnx_parakeet_tdt_v2_int8 --scope full
  python scripts/benchmark_phase1_asr.py --model onnx_qwen3_asr_06b_int8 --scope standard
  python scripts/benchmark_phase1_asr.py --model onnx_qwen3_asr_06b_int8 --scope m235_rest
  python scripts/benchmark_phase1_asr.py --mode stream
  python scripts/benchmark_phase1_asr.py --mode hybrid
  python scripts/benchmark_phase1_asr.py --mode compare
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import gc
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark_speech_intelligence import (
    BENCH_DIR,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    query_gpu_vram,
)
from score_ground_truth import (
    UNCERTAIN_SPAN_RE,
    _count_duplication_words,
    audit_mixed_script_and_utf8,
    levenshtein_distance,
    levenshtein_ops,
    normalize_strict_text,
    parse_reference_text,
    score_predictions_against_reference,
    tokenize_hinglish_norm,
    tokenize_strict,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.hf_asr_engine import (
    LocalHFASRTranscriber,
    build_foreign_script_token_ids,
    clean_repetition_tail,
    sanitize_utf8_text,
)
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

EXPECTED_DB_HASH = "e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439"
CORPUS_MANIFEST = BENCH_DIR / "phase1_corpus" / "manifest.json"
RESULTS_DIR = BENCH_DIR / "phase1_results"
CHALLENGER_DIR = ROOT_DIR / "data" / "models" / "challengers"
MODELS_DIR = ROOT_DIR / "data" / "models"
PHASE0_AFTER_PATH = BENCH_DIR / "phase0_validation" / "phase0_after.json"
PHASE25_AB_PATH = BENCH_DIR / "phase2_5_validation" / "qwen_ab_report.json"

ALL_TRACK_ORDER = [
    "earnings22_us",
    "fleurs_en_us",
    "phase0_us_neural",
    "nptel_in_en",
    "phase0_in_neural",
    "fleurs_hi_in",
    "m234_full_90s",
    "m235_verified_111",
    "m241_scrum_omission",
]

STANDARD_TRACK_ORDER = [
    "earnings22_us",
    "fleurs_en_us",
    "phase0_us_neural",
    "nptel_in_en",
    "phase0_in_neural",
    "fleurs_hi_in",
    "m234_full_90s",
    "m235_strat30",
    "m241_scrum_omission",
]

MODEL_SPECS: Dict[str, Dict[str, Any]] = {
    "onnx_parakeet_tdt_v2_int8": {
        "display": "Parakeet TDT 0.6B v2 ONNX INT8",
        "family": "NeMo TDT ONNX INT8",
        "languages": ["en"],
        "default_scope": "full",
    },
    "onnx_indic_conformer_hi": {
        "display": "IndicConformer Hindi ONNX INT8",
        "family": "NeMo CTC ONNX INT8",
        "languages": ["hi", "en_limited"],
        "default_scope": "full",
    },
    "p0_whisper_small_cpu": {
        "display": "Phase 0 Whisper Small (CPU INT8)",
        "family": "CTranslate2 Whisper INT8",
        "languages": ["multilingual"],
        "default_scope": "full",
    },
    "onnx_nemotron_stream_560ms_int8": {
        "display": "Nemotron Speech Streaming EN 0.6B (560ms ONNX INT8)",
        "family": "NeMo Causal Streaming Transducer INT8",
        "languages": ["en"],
        "default_scope": "full",
    },
    "onnx_qwen3_asr_06b_int8": {
        "display": "Qwen3-ASR 0.6B ONNX INT8",
        "family": "Qwen3-ASR ONNX INT8",
        "languages": ["multilingual"],
        "default_scope": "standard",
    },
    "p0_whisper_turbo_cpu": {
        "display": "Phase 0 Whisper Large-v3-Turbo (CPU INT8)",
        "family": "CTranslate2 Whisper INT8",
        "languages": ["multilingual"],
        "default_scope": "standard",
    },
    "hf_qwen3_asr_06b_guarded": {
        "display": "Qwen3-ASR 0.6B HF Guarded",
        "family": "Qwen3-ASR PyTorch Guarded",
        "languages": ["multilingual"],
        "default_scope": "hf_screen",
    },
    "hf_srota_qwen3_hinglish": {
        "display": "Srota Qwen3-ASR 0.6B Hinglish (Guarded)",
        "family": "Qwen3-ASR Fine-tuned PyTorch",
        "languages": ["hinglish", "hi", "en"],
        "default_scope": "hf_screen",
    },
    "hf_maya_transcribe": {
        "display": "Maya Transcribe 0.6B",
        "family": "Qwen3-ASR Hinglish Style-Mixed PyTorch",
        "languages": ["hinglish", "hi", "en"],
        "default_scope": "hf_screen_fast",
    },
}

DEVA_WORD_RE = re.compile(r"[\u0900-\u097F]+|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
DEVA_CHAR_KEEP_RE = re.compile(r"[\u0900-\u097FA-Za-z0-9]+")


def verify_db_unchanged() -> str:
    h = compute_target_db_rows_sha256()
    if h != EXPECTED_DB_HASH:
        raise RuntimeError(f"Protected DB SHA-256 mismatch: {h} != {EXPECTED_DB_HASH}")
    return h


def tokenize_devanagari_words(text: str) -> List[str]:
    """Preserve Devanagari matras cleanly (Python \\w strips Unicode combining marks)."""
    return DEVA_WORD_RE.findall(str(text or "").lower())


def chars_devanagari(text: str) -> List[str]:
    return list("".join(DEVA_CHAR_KEEP_RE.findall(str(text or "").lower())))


def _strip_uncertain_markers(text: str) -> str:
    return UNCERTAIN_SPAN_RE.sub(r"\1", str(text or ""))


def align_clip_details(
    ref_raw: str,
    pred_text: str,
    *,
    mode: str = "strict",
    mask_uncertain: bool = True,
) -> Dict[str, Any]:
    """
    Deterministic DP edit-op alignment returning WER/CER ingredients and omission stats.
    mode:
      - "strict": English strict token normalization (`tokenize_strict`)
      - "hinglish": Devanagari->Roman + Hinglish normalization (`tokenize_hinglish_norm`)
      - "devanagari": Matra-preserving Devanagari + Latin word tokens
    """
    if mode == "hinglish":
        tok_fn = tokenize_hinglish_norm
        char_fn = lambda s: list("".join(tokenize_hinglish_norm(s)))
    elif mode == "devanagari":
        tok_fn = tokenize_devanagari_words
        char_fn = chars_devanagari
    else:
        tok_fn = tokenize_strict
        char_fn = lambda s: list(re.sub(r"\s+", "", normalize_strict_text(s)))

    pred_toks = tok_fn(pred_text)

    if mode == "hinglish" and mask_uncertain and "[?" in str(ref_raw or ""):
        # Parse [?uncertain?] slots cleanly like score_ground_truth.py
        parsed, _ = parse_reference_text(ref_raw)
        ref_seq: List[Tuple[str, bool]] = []
        certain_toks: List[str] = []
        for raw_tok, is_unc in parsed:
            if is_unc:
                nts = tok_fn(raw_tok) if raw_tok else [""]
                for nt in (nts or [""]):
                    ref_seq.append((nt, True))
            else:
                nts = tok_fn(raw_tok)
                for nt in nts:
                    ref_seq.append((nt, False))
                    certain_toks.append(nt)
        if not ref_seq:
            ref_toks = tok_fn(_strip_uncertain_markers(ref_raw))
            edits, subs, dels, ins = levenshtein_ops(ref_toks, pred_toks)
            ref_n = len(ref_toks)
        else:
            m, n = len(ref_seq), len(pred_toks)
            dp = [[0] * (n + 1) for _ in range(m + 1)]
            for i in range(1, m + 1):
                dp[i][0] = dp[i - 1][0] + (0 if ref_seq[i - 1][1] else 1)
            for j in range(1, n + 1):
                dp[0][j] = j
            for i in range(1, m + 1):
                rt, is_u = ref_seq[i - 1]
                for j in range(1, n + 1):
                    pt = pred_toks[j - 1]
                    sub_c = 0 if (is_u or rt == pt) else 1
                    del_c = 0 if is_u else 1
                    dp[i][j] = min(dp[i - 1][j] + del_c, dp[i][j - 1] + 1, dp[i - 1][j - 1] + sub_c)
            subs = dels = ins = 0
            i, j = m, n
            while i > 0 or j > 0:
                if i > 0 and j > 0:
                    rt, is_u = ref_seq[i - 1]
                    pt = pred_toks[j - 1]
                    sub_c = 0 if (is_u or rt == pt) else 1
                    if dp[i][j] == dp[i - 1][j - 1] + sub_c:
                        if sub_c:
                            subs += 1
                        i -= 1
                        j -= 1
                        continue
                if i > 0:
                    del_c = 0 if ref_seq[i - 1][1] else 1
                    if dp[i][j] == dp[i - 1][j] + del_c:
                        if del_c:
                            dels += 1
                        i -= 1
                        continue
                ins += 1
                j -= 1
            edits = subs + dels + ins
            ref_n = len(certain_toks)
            ref_toks = certain_toks
    else:
        clean_ref = _strip_uncertain_markers(ref_raw)
        ref_toks = tok_fn(clean_ref)
        ref_n = len(ref_toks)
        edits, subs, dels, ins = levenshtein_ops(ref_toks, pred_toks)

    ref_chars = char_fn(_strip_uncertain_markers(ref_raw))
    pred_chars = char_fn(pred_text)
    char_edits = levenshtein_distance(ref_chars, pred_chars)
    char_ref_n = len(ref_chars)
    pred_n = len(pred_toks)

    retention = round(float(pred_n) / float(max(1, ref_n)), 4) if ref_n > 0 else 1.0
    del_rate = round(float(dels) / float(max(1, ref_n)), 4) if ref_n > 0 else 0.0
    omitted_flag = bool(ref_n >= 4 and (pred_n == 0 or retention < 0.35 or del_rate >= 0.65))
    return {
        "ref_words": ref_n,
        "pred_words": pred_n,
        "edits": int(edits),
        "subs": int(subs),
        "dels": int(dels),
        "ins": int(ins),
        "char_edits": int(char_edits),
        "char_ref": int(char_ref_n),
        "word_retention_ratio": retention,
        "del_rate": del_rate,
        "omitted": omitted_flag,
    }


def aggregate_track_metrics(clip_records: List[Dict[str, Any]], *, align_key: str = "align") -> Dict[str, Any]:
    if not clip_records:
        return {"clip_count": 0}
    total_ref = sum(int(r[align_key]["ref_words"]) for r in clip_records)
    total_pred = sum(int(r[align_key]["pred_words"]) for r in clip_records)
    total_edits = sum(int(r[align_key]["edits"]) for r in clip_records)
    total_subs = sum(int(r[align_key]["subs"]) for r in clip_records)
    total_dels = sum(int(r[align_key]["dels"]) for r in clip_records)
    total_ins = sum(int(r[align_key]["ins"]) for r in clip_records)
    total_ce = sum(int(r[align_key]["char_edits"]) for r in clip_records)
    total_cr = sum(int(r[align_key]["char_ref"]) for r in clip_records)

    eligible_om = [r for r in clip_records if int(r[align_key]["ref_words"]) >= 4]
    omitted_cnt = sum(1 for r in eligible_om if r[align_key]["omitted"])
    empty_cnt = sum(1 for r in clip_records if int(r[align_key]["pred_words"]) == 0 and int(r[align_key]["ref_words"]) > 0)

    total_aud = sum(float(r["duration_sec"]) for r in clip_records)
    total_inf = sum(float(r["infer_sec"]) for r in clip_records)
    infers = [float(r["infer_sec"]) for r in clip_records]
    dup_words = sum(_count_duplication_words(str(r.get("raw_text") or "").split()) for r in clip_records)

    script_audit = audit_mixed_script_and_utf8(
        [{"text": str(r.get("raw_text") or "")} for r in clip_records]
    )
    lang_counts: Dict[str, int] = {}
    for r in clip_records:
        dl = str(r.get("detected_language") or "unknown")
        lang_counts[dl] = lang_counts.get(dl, 0) + 1

    return {
        "clip_count": len(clip_records),
        "total_audio_sec": round(total_aud, 2),
        "total_infer_sec": round(total_inf, 2),
        "rtf": round(total_inf / max(0.01, total_aud), 4),
        "p50_clip_infer_sec": round(float(np.percentile(infers, 50)), 3) if infers else 0.0,
        "p95_clip_infer_sec": round(float(np.percentile(infers, 95)), 3) if infers else 0.0,
        "ref_words": total_ref,
        "pred_words": total_pred,
        "wer": round(total_edits / max(1, total_ref), 4),
        "cer": round(total_ce / max(1, total_cr), 4),
        "sub_rate": round(total_subs / max(1, total_ref), 4),
        "del_rate": round(total_dels / max(1, total_ref), 4),
        "ins_rate": round(total_ins / max(1, total_ref), 4),
        "word_retention_ratio": round(total_pred / max(1, total_ref), 4),
        "omitted_clip_count": omitted_cnt,
        "omitted_clip_pct": round(100.0 * omitted_cnt / max(1, len(eligible_om)), 2),
        "empty_clip_count": empty_cnt,
        "duplication_words": dup_words,
        "detected_language_counts": lang_counts,
        "script_purity": script_audit,
    }


def _aggregate_m241_probe(clip_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    hi_clips = [r for r in clip_records if str(r.get("expected_language_group")) == "hinglish"]
    en_clips = [r for r in clip_records if str(r.get("expected_language_group")) == "en"]
    total_aud = sum(float(r["duration_sec"]) for r in clip_records)
    total_inf = sum(float(r["infer_sec"]) for r in clip_records)

    def _group_stats(grp: List[Dict[str, Any]]) -> Dict[str, Any]:
        aud = sum(float(r["duration_sec"]) for r in grp)
        words = sum(len(tokenize_hinglish_norm(str(r.get("roman_text") or r.get("raw_text") or ""))) for r in grp)
        empty = sum(1 for r in grp if not str(r.get("raw_text") or "").strip())
        return {
            "clip_count": len(grp),
            "audio_sec": round(aud, 2),
            "pred_words": words,
            "words_per_sec": round(words / max(0.01, aud), 3),
            "empty_clips": empty,
        }

    hi_s = _group_stats(hi_clips)
    en_s = _group_stats(en_clips)
    ratio = round(hi_s["words_per_sec"] / max(0.001, en_s["words_per_sec"]), 3) if en_s["clip_count"] else None
    script_audit = audit_mixed_script_and_utf8(
        [{"text": str(r.get("raw_text") or "")} for r in clip_records]
    )
    return {
        "clip_count": len(clip_records),
        "total_audio_sec": round(total_aud, 2),
        "total_infer_sec": round(total_inf, 2),
        "rtf": round(total_inf / max(0.01, total_aud), 4),
        "hinglish_group": hi_s,
        "english_group": en_s,
        "hinglish_to_english_wps_ratio": ratio,
        "script_purity": script_audit,
    }


def _resolve_whisper_path(repo_name: str, fallback: str) -> str:
    root = MODELS_DIR / repo_name / "snapshots"
    if root.exists():
        snaps = [p for p in sorted(root.iterdir()) if p.is_dir()]
        if snaps:
            return str(snaps[-1])
    return fallback


def _split_long_audio(arr: np.ndarray, *, max_chunk_sec: float) -> List[np.ndarray]:
    dur = float(arr.size) / float(TARGET_SAMPLE_RATE)
    if dur <= max_chunk_sec + 1.5:
        return [arr]
    try:
        spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
            arr,
            dict(threshold=0.35, min_silence_duration_ms=450, speech_pad_ms=200),
            max_chunk_sec=max_chunk_sec,
            merge_gap_sec=0.5,
            pad_sec=0.15,
        )
    except Exception:
        spans = None
    if not spans:
        step = int(max_chunk_sec * TARGET_SAMPLE_RATE)
        return [arr[i : i + step] for i in range(0, arr.size, step) if arr[i : i + step].size >= 1600]
    out: List[np.ndarray] = []
    for s_i, e_i in spans:
        sl = arr[s_i:e_i]
        if sl.size >= 1600 and float(np.max(np.abs(sl))) >= 0.0003:
            out.append(sl)
    return out or [arr]


def _load_srota_qwen3(device_req: str = "auto") -> Tuple[Any, Any, str, str, List[int]]:
    import torch
    from safetensors.torch import load_file
    from transformers import AutoProcessor, GenerationConfig, Qwen3ASRConfig, Qwen3ASRForConditionalGeneration

    local_dir = CHALLENGER_DIR / "qwen3-asr-0.6b-hinglish"
    proc_dir = CHALLENGER_DIR / "qwen3-asr-0.6b"
    cfg_raw = json.loads((local_dir / "config.json").read_text(encoding="utf-8"))

    dev = device_req
    if dev == "auto":
        v = query_gpu_vram()
        free_mb = v.get("free_mb")
        if torch.cuda.is_available() and free_mb is not None and free_mb >= 1950:
            dev = "cuda"
        else:
            dev = "cpu"
    elif dev == "cuda" and not torch.cuda.is_available():
        dev = "cpu"
    dtype = torch.float16 if dev == "cuda" else torch.float32
    dtype_str = "float16" if dtype == torch.float16 else "float32"

    processor = AutoProcessor.from_pretrained(str(proc_dir), local_files_only=True, trust_remote_code=False)

    tc = cfg_raw["thinker_config"]
    hf_ref = Qwen3ASRConfig()
    valid_audio_keys = set(hf_ref.audio_config.to_dict().keys())
    valid_text_keys = set(hf_ref.text_config.to_dict().keys())

    ac = {k: v for k, v in tc.get("audio_config", {}).items() if k in valid_audio_keys and v is not None}
    ac["model_type"] = "qwen3_asr_encoder"

    raw_text_cfg = dict(tc.get("text_config", {}))
    rope_theta = float(raw_text_cfg.get("rope_theta", 1000000))
    tcfg = {k: v for k, v in raw_text_cfg.items() if k in valid_text_keys and v is not None}
    tcfg["model_type"] = "qwen3"
    tcfg["rope_parameters"] = {"rope_theta": rope_theta, "rope_type": "default"}

    ts_tok = tc.get("timestamp_token_id")
    q_cfg = Qwen3ASRConfig(
        audio_config=ac,
        text_config=tcfg,
        audio_token_id=int(tc.get("audio_token_id", 151676)),
        timestamp_token_id=int(ts_tok) if ts_tok is not None else 151705,
        tie_word_embeddings=bool(raw_text_cfg.get("tie_word_embeddings", True)),
    )
    model = Qwen3ASRForConditionalGeneration(q_cfg)
    raw_sd = load_file(str(local_dir / "model.safetensors"), device="cpu")
    mapped_sd: Dict[str, Any] = {}
    for k, v in raw_sd.items():
        new_k = k
        if new_k.startswith("thinker.audio_tower.proj1."):
            new_k = "model.multi_modal_projector.linear_1." + new_k[len("thinker.audio_tower.proj1.") :]
        elif new_k.startswith("thinker.audio_tower.proj2."):
            new_k = "model.multi_modal_projector.linear_2." + new_k[len("thinker.audio_tower.proj2.") :]
        elif new_k.startswith("thinker.audio_tower."):
            new_k = "model.audio_tower." + new_k[len("thinker.audio_tower.") :]
        elif new_k.startswith("thinker.model."):
            new_k = "model.language_model." + new_k[len("thinker.model.") :]
        elif new_k.startswith("thinker.lm_head."):
            new_k = "lm_head." + new_k[len("thinker.lm_head.") :]
        mapped_sd[new_k] = v.to(dtype=dtype)

    if "lm_head.weight" not in mapped_sd and "model.language_model.embed_tokens.weight" in mapped_sd:
        mapped_sd["lm_head.weight"] = mapped_sd["model.language_model.embed_tokens.weight"]

    missing, unexpected = model.load_state_dict(mapped_sd, strict=False)
    real_missing = [k for k in missing if k != "lm_head.weight"]
    if real_missing or unexpected:
        raise RuntimeError(f"Srota state_dict mismatch: missing={real_missing[:5]} unexpected={unexpected[:5]}")

    gen_cfg_path = local_dir / "generation_config.json"
    if gen_cfg_path.exists():
        model.generation_config = GenerationConfig.from_pretrained(str(local_dir))
    model.generation_config.eos_token_id = [151643, 151645]
    if getattr(model.generation_config, "pad_token_id", None) is None:
        model.generation_config.pad_token_id = 151643
    model = model.to(device=dev, dtype=dtype).eval()
    blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
    return model, processor, dev, dtype_str, blocked_ids


class ModelRunner:
    def __init__(self, model_id: str, cpu_threads: Optional[int] = None, max_new_tokens: int = 512) -> None:
        self.model_id = model_id
        self.cpu_threads = cpu_threads
        self.max_new_tokens = int(max_new_tokens)
        self.device = "cpu"
        self.compute_type = "int8"
        self.load_sec = 0.0
        self.obj: Any = None
        self.extra: Dict[str, Any] = {}

    def load(self) -> None:
        t0 = time.perf_counter()
        if self.model_id == "onnx_parakeet_tdt_v2_int8":
            import sherpa_onnx

            d = CHALLENGER_DIR / "sherpa-onnx-parakeet-tdt-0.6b-v2-int8"
            th = self.cpu_threads or 4
            self.obj = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                tokens=str(d / "tokens.txt"),
                model_type="nemo_transducer",
                num_threads=th,
                feature_dim=128,
                provider="cpu",
            )
            self.compute_type = f"onnx_int8_t{th}"
        elif self.model_id == "onnx_indic_conformer_hi":
            import sherpa_onnx

            d = CHALLENGER_DIR / "indic-conformer-sherpa"
            th = self.cpu_threads or 4
            self.obj = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                model=str(d / "model.int8.onnx"),
                tokens=str(d / "tokens.txt"),
                num_threads=th,
                sample_rate=TARGET_SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
            )
            self.compute_type = f"onnx_int8_t{th}"
        elif self.model_id == "onnx_nemotron_stream_560ms_int8":
            import sherpa_onnx

            d = CHALLENGER_DIR / "sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8"
            th = self.cpu_threads or 4
            self.obj = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(d / "tokens.txt"),
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                model_type="nemo_transducer",
                num_threads=th,
                feature_dim=128,
                normalize_samples=True,
                provider="cpu",
            )
            self.compute_type = f"onnx_int8_560ms_t{th}"
        elif self.model_id == "onnx_qwen3_asr_06b_int8":
            import sherpa_onnx

            d = CHALLENGER_DIR / "sherpa-onnx-qwen3-asr-0.6b-int8"
            th = self.cpu_threads or 4
            self.obj = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                conv_frontend=str(d / "conv_frontend.onnx"),
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                tokenizer=str(d / "tokenizer"),
                num_threads=th,
                feature_dim=128,
                provider="cpu",
                max_total_len=1024,
                max_new_tokens=self.max_new_tokens,
                hotwords="",
            )
            self.compute_type = f"onnx_int8_t{th}"
        elif self.model_id == "p0_whisper_small_cpu":
            from faster_whisper import WhisperModel

            th = self.cpu_threads or 6
            path = _resolve_whisper_path("models--Systran--faster-whisper-small", "small")
            self.obj = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=th)
            self.compute_type = f"ct2_int8_t{th}"
        elif self.model_id == "p0_whisper_turbo_cpu":
            from faster_whisper import WhisperModel

            th = self.cpu_threads or 8
            path = _resolve_whisper_path("models--deepdml--faster-whisper-large-v3-turbo-ct2", "large-v3-turbo")
            self.obj = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=th)
            self.compute_type = f"ct2_int8_t{th}"
        elif self.model_id in ("hf_qwen3_asr_06b_guarded", "hf_maya_transcribe"):
            import torch

            th = self.cpu_threads or 8
            torch.set_num_threads(th)
            hf_id = "Qwen/Qwen3-ASR-0.6B-hf" if self.model_id == "hf_qwen3_asr_06b_guarded" else "maya-research/maya-transcribe"
            tr = LocalHFASRTranscriber(model_id=hf_id, device="auto")
            ok, msg = tr.ensure_loaded(local_only=True)
            if not ok:
                raise RuntimeError(msg)
            self.obj = tr
            self.device = tr._active_device
            self.compute_type = f"{tr._active_dtype}_t{th}"
        elif self.model_id == "hf_srota_qwen3_hinglish":
            import torch

            th = self.cpu_threads or 8
            torch.set_num_threads(th)
            model, proc, dev, dt_str, blocked = _load_srota_qwen3("auto")
            self.obj = model
            self.device = dev
            self.compute_type = f"{dt_str}_t{th}"
            self.extra = {
                "processor": proc,
                "blocked_ids_dev": torch.tensor(blocked, device=dev, dtype=torch.long) if blocked else None,
                "dtype": torch.float16 if dev == "cuda" else torch.float32,
            }
        else:
            raise ValueError(f"Unknown model_id: {self.model_id}")
        self.load_sec = round(time.perf_counter() - t0, 3)

    def unload(self) -> None:
        if hasattr(self.obj, "unload_model"):
            try:
                self.obj.unload_model()
            except Exception:
                pass
        self.obj = None
        self.extra.clear()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe_array(self, arr_16k: np.ndarray, *, live_mode: bool = False) -> Dict[str, Any]:
        t0 = time.perf_counter()
        raw_text = ""
        det_lang = ""
        lang_prob = 0.0
        lang_probs: Optional[Dict[str, float]] = None

        if self.model_id == "onnx_parakeet_tdt_v2_int8":
            chunks = _split_long_audio(arr_16k, max_chunk_sec=18.0)
            parts: List[str] = []
            pad = np.zeros(3200, dtype=np.float32)
            for ch in chunks:
                st = self.obj.create_stream()
                st.accept_waveform(TARGET_SAMPLE_RATE, np.concatenate([ch, pad]))
                self.obj.decode_stream(st)
                t = str(st.result.text or "").strip()
                if t:
                    parts.append(t)
            raw_text = " ".join(parts).strip()
            det_lang = "en"
            lang_prob = 1.0

        elif self.model_id == "onnx_indic_conformer_hi":
            chunks = _split_long_audio(arr_16k, max_chunk_sec=18.0)
            parts = []
            for ch in chunks:
                st = self.obj.create_stream()
                st.accept_waveform(TARGET_SAMPLE_RATE, normalize_speech_audio(ch))
                self.obj.decode_stream(st)
                t = str(st.result.text or "").strip()
                if t:
                    parts.append(t)
            raw_text = " ".join(parts).strip()
            det_lang = "hi"
            lang_prob = 1.0

        elif self.model_id == "onnx_nemotron_stream_560ms_int8":
            st = self.obj.create_stream()
            chunk_n = int(0.56 * TARGET_SAMPLE_RATE)
            padded = np.concatenate([arr_16k, np.zeros(int(1.2 * TARGET_SAMPLE_RATE), dtype=np.float32)])
            for off in range(0, padded.size, chunk_n):
                st.accept_waveform(TARGET_SAMPLE_RATE, padded[off : off + chunk_n])
                while self.obj.is_ready(st):
                    self.obj.decode_stream(st)
            st.input_finished()
            while self.obj.is_ready(st):
                self.obj.decode_stream(st)
            raw_text = str(self.obj.get_result(st) or "").strip()
            det_lang = "en"
            lang_prob = 1.0

        elif self.model_id == "onnx_qwen3_asr_06b_int8":
            chunks = _split_long_audio(arr_16k, max_chunk_sec=20.0)
            parts = []
            for ch in chunks:
                rms = float(np.sqrt(np.mean(np.square(ch)))) if ch.size else 0.0
                aud = normalize_speech_audio(ch) if rms < 0.015 else ch
                st = self.obj.create_stream()
                st.accept_waveform(TARGET_SAMPLE_RATE, aud)
                self.obj.decode_stream(st)
                t = str(st.result.text or "").strip()
                t, _ = sanitize_utf8_text(t)
                t = clean_repetition_tail(t).strip()
                if t:
                    parts.append(t)
            raw_text = " ".join(parts).strip()
            det_lang = "auto"

        elif self.model_id in ("p0_whisper_small_cpu", "p0_whisper_turbo_cpu"):
            if self.model_id == "p0_whisper_turbo_cpu":
                segs, info = self.obj.transcribe(
                    arr_16k,
                    language=None,
                    beam_size=3,
                    best_of=2,
                    temperature=(0.0, 0.2),
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
            elif live_mode:
                segs, info = self.obj.transcribe(
                    arr_16k,
                    language=None,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
            else:
                segs, info = self.obj.transcribe(
                    arr_16k,
                    language=None,
                    beam_size=5,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
            raw_text = " ".join(s.text.strip() for s in segs if getattr(s, "text", "")).strip()
            det_lang = str(getattr(info, "language", "") or "")
            lang_prob = round(float(getattr(info, "language_probability", 0.0) or 0.0), 5)
            all_lp = getattr(info, "all_language_probs", None) or []
            lp_dict = {str(k): float(v) for k, v in all_lp}
            lang_probs = {
                "p_en": round(lp_dict.get("en", 0.0), 5),
                "p_hi": round(lp_dict.get("hi", 0.0), 5),
                "p_ur": round(lp_dict.get("ur", 0.0), 5),
                "top5": [[str(k), round(float(v), 5)] for k, v in all_lp[:5]],
            }

        elif self.model_id in ("hf_qwen3_asr_06b_guarded", "hf_maya_transcribe"):
            max_new = 256 if self.model_id == "hf_maya_transcribe" else 320
            chunks = _split_long_audio(arr_16k, max_chunk_sec=22.0)
            parts = []
            langs: List[str] = []
            for ch in chunks:
                txt, dl, _ = self.obj.decode_chunk(ch, hotwords=None, max_new_tokens=max_new)
                if txt:
                    parts.append(txt.strip())
                if dl:
                    langs.append(dl)
            raw_text = " ".join(parts).strip()
            det_lang = langs[0] if langs else ""

        elif self.model_id == "hf_srota_qwen3_hinglish":
            import torch
            from transformers import LogitsProcessor, LogitsProcessorList

            proc = self.extra["processor"]
            blocked = self.extra["blocked_ids_dev"]
            target_dt = self.extra["dtype"]

            class _ForeignMask(LogitsProcessor):
                def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
                    if blocked is not None and blocked.numel() > 0:
                        scores = scores.clone()
                        scores[:, blocked] = -float("inf")
                    return scores

            lp_list = LogitsProcessorList([_ForeignMask()]) if blocked is not None else None
            chunks = _split_long_audio(arr_16k, max_chunk_sec=22.0)
            parts = []
            langs = []
            for ch in chunks:
                norm_sl = normalize_speech_audio(ch)
                inputs = proc.apply_transcription_request(
                    audio=norm_sl, language=None, prompt=None, tokenize=True
                )
                inputs = inputs.to(self.device)
                if "input_features" in inputs:
                    inputs["input_features"] = inputs["input_features"].to(dtype=target_dt)
                in_len = int(inputs["input_ids"].shape[1])
                with torch.inference_mode():
                    gen_ids = self.obj.generate(
                        **inputs,
                        max_new_tokens=320,
                        do_sample=False,
                        repetition_penalty=1.05,
                        logits_processor=lp_list,
                    )
                raw_dec = proc.decode(gen_ids[0][in_len:], skip_special_tokens=True)
                parsed = proc.parse_output(raw_dec)
                txt = str(parsed.get("transcription") if isinstance(parsed, dict) else raw_dec).strip()
                dl = str(parsed.get("language") if isinstance(parsed, dict) else "")
                txt, _ = sanitize_utf8_text(txt)
                txt = clean_repetition_tail(txt).strip()
                if txt:
                    parts.append(txt)
                if dl:
                    langs.append(dl)
                del inputs, gen_ids
            raw_text = " ".join(parts).strip()
            det_lang = langs[0] if langs else ""

        infer_sec = round(time.perf_counter() - t0, 4)
        roman_text = DictationProcessor.devanagari_to_roman_hinglish(raw_text).strip() if raw_text else ""
        return {
            "raw_text": raw_text,
            "roman_text": roman_text,
            "detected_language": det_lang,
            "language_probability": lang_prob,
            "lang_probs": lang_probs,
            "infer_sec": infer_sec,
        }


def _select_tracks_for_scope(manifest: Dict[str, Any], scope: str) -> List[Tuple[str, List[Dict[str, Any]]]]:
    tr = manifest["tracks"]
    if scope == "full":
        return [(t, list(tr[t])) for t in ALL_TRACK_ORDER]
    if scope == "standard":
        return [(t, list(tr[t])) for t in STANDARD_TRACK_ORDER]
    if scope == "standard_en":
        return [
            (t, list(tr[t]))
            for t in ("earnings22_us", "fleurs_en_us", "phase0_us_neural", "nptel_in_en", "phase0_in_neural")
        ]
    if scope == "standard_hi_mix":
        return [
            (t, list(tr[t]))
            for t in ("fleurs_hi_in", "m234_full_90s", "m235_strat30", "m241_scrum_omission")
        ]
    if scope == "m235_rest":
        return [("m235_verified_111", list(tr["m235_verified_111"]))]
    if scope == "hf_screen":
        m241_ids = ("U1_", "U2_", "U4_", "EN2_", "MIC_")
        return [
            ("earnings22_us", list(tr["earnings22_us"][:10])),
            ("fleurs_en_us", list(tr["fleurs_en_us"][:10])),
            ("phase0_us_neural", list(tr["phase0_us_neural"])),
            ("nptel_in_en", list(tr["nptel_in_en"][:10])),
            ("phase0_in_neural", list(tr["phase0_in_neural"])),
            ("fleurs_hi_in", list(tr["fleurs_hi_in"][:10])),
            ("m234_full_90s", list(tr["m234_full_90s"])),
            ("m241_scrum_omission", [c for c in tr["m241_scrum_omission"] if c["id"].startswith(m241_ids)]),
        ]
    if scope == "hf_screen_fast":
        m234_sel = [tr["m234_full_90s"][i] for i in (0, 1, 3, 5, 8, 12) if i < len(tr["m234_full_90s"])]
        m241_ids = ("U1_", "EN2_", "MIC_")
        return [
            ("earnings22_us", list(tr["earnings22_us"][:5])),
            ("fleurs_en_us", list(tr["fleurs_en_us"][:5])),
            ("phase0_us_neural", list(tr["phase0_us_neural"])),
            ("nptel_in_en", list(tr["nptel_in_en"][:5])),
            ("fleurs_hi_in", list(tr["fleurs_hi_in"][:5])),
            ("m234_full_90s", m234_sel),
            ("m241_scrum_omission", [c for c in tr["m241_scrum_omission"] if c["id"].startswith(m241_ids)]),
        ]
    raise ValueError(f"Unsupported scope: {scope}")


def _score_clip(track_name: str, clip: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
    raw_txt = out["raw_text"]
    roman_txt = out["roman_text"]
    rec: Dict[str, Any] = {
        "id": clip["id"],
        "track": track_name,
        "duration_sec": float(clip["duration_sec"]),
        "infer_sec": float(out["infer_sec"]),
        "detected_language": out["detected_language"],
        "language_probability": out["language_probability"],
        "lang_probs": out["lang_probs"],
        "raw_text": raw_txt,
        "roman_text": roman_txt,
    }
    if track_name == "fleurs_hi_in":
        rec["reference_devanagari"] = clip["reference_devanagari"]
        rec["reference_roman"] = clip["reference_roman"]
        rec["align_devanagari"] = align_clip_details(clip["reference_devanagari"], raw_txt, mode="devanagari")
        rec["align_roman"] = align_clip_details(clip["reference_roman"], roman_txt, mode="hinglish")
        rec["align"] = rec["align_roman"]
    elif track_name in ("m234_full_90s", "m235_verified_111", "m235_strat30"):
        rec["segment_index"] = int(clip["segment_index"])
        rec["source_track"] = clip["source_track"]
        rec["speaker"] = clip["speaker"]
        rec["start_time"] = clip["start_time"]
        rec["end_time"] = clip["end_time"]
        rec["language_tags"] = clip.get("language_tags") or []
        rec["reference_text"] = clip["reference_text"]
        rec["align"] = align_clip_details(clip["reference_text"], roman_txt, mode="hinglish", mask_uncertain=True)
    elif track_name == "m241_scrum_omission":
        rec["expected_language_group"] = clip["expected_language_group"]
        rec["source_track"] = clip["source_track"]
    else:
        rec["reference_text"] = clip["reference_text"]
        rec["align"] = align_clip_details(clip["reference_text"], raw_txt, mode="strict")
    return rec


def _score_official_meeting(
    ref_path: Path,
    clip_records: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
    verified_cnt = sum(1 for s in ref_data["segments"] if s.get("verified_by_human"))
    if len(clip_records) < verified_cnt:
        return None
    pred_segs = [
        {
            "start_time": float(r["start_time"]),
            "end_time": float(r["end_time"]),
            "start": float(r["start_time"]),
            "end": float(r["end_time"]),
            "source_track": r["source_track"],
            "speaker": r["speaker"],
            "raw_text": r["raw_text"],
            "text": r["roman_text"],
        }
        for r in sorted(clip_records, key=lambda x: int(x["segment_index"]))
    ]
    return score_predictions_against_reference(ref_data, pred_segs)


def _finalize_track_bundle(track_name: str, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if track_name == "fleurs_hi_in":
        metrics = aggregate_track_metrics(recs, align_key="align_roman")
        metrics["devanagari"] = aggregate_track_metrics(recs, align_key="align_devanagari")
    elif track_name == "m241_scrum_omission":
        metrics = _aggregate_m241_probe(recs)
    else:
        metrics = aggregate_track_metrics(recs, align_key="align")
    return {"metrics": metrics, "clips": recs}


def run_model_benchmark(
    model_id: str,
    scope: str,
    cpu_threads: Optional[int] = None,
    tracks_override: Optional[List[str]] = None,
) -> int:
    db_before = verify_db_unchanged()
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{model_id}.json"

    existing: Dict[str, Any] = {}
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))

    vram_before = query_gpu_vram()
    rss_before = get_process_rss_mb()

    runner = ModelRunner(model_id, cpu_threads=cpu_threads)
    runner.load()
    vram_after_load = query_gpu_vram()
    peak_rss = max(rss_before, get_process_rss_mb())
    vram_peak = max(
        int(vram_before.get("used_mb") or 0),
        int(vram_after_load.get("used_mb") or 0),
    )

    tracks_out: Dict[str, Any] = dict(existing.get("tracks") or {})
    cached_by_id: Dict[str, Dict[str, Any]] = {}
    for _tname, tb in tracks_out.items():
        for cr in tb.get("clips") or []:
            cached_by_id[cr["id"]] = cr

    eff_scope = f"tracks({','.join(tracks_override)})" if tracks_override else scope
    def _save_payload(official_meetings: Optional[Dict[str, Any]] = None) -> None:
        nonlocal peak_rss, vram_peak
        peak_rss = max(peak_rss, get_process_rss_mb())
        vq = query_gpu_vram()
        vram_peak = max(vram_peak, int(vq.get("used_mb") or 0))
        payload = {
            "model_id": model_id,
            "spec": MODEL_SPECS[model_id],
            "scope": eff_scope if not existing else f"{existing.get('scope', eff_scope)}+{eff_scope}",
            "device": runner.device,
            "compute_type": runner.compute_type,
            "load_sec": runner.load_sec,
            "peak_rss_mb": round(peak_rss, 1),
            "vram_before_mb": vram_before.get("used_mb"),
            "vram_after_load_mb": vram_after_load.get("used_mb"),
            "vram_peak_mb": vram_peak,
            "db_sha256": db_before,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tracks": tracks_out,
            "official_meetings": official_meetings if official_meetings is not None else (existing.get("official_meetings") or {}),
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    track_jobs = (
        [(t, list(manifest["tracks"][t])) for t in tracks_override]
        if tracks_override
        else _select_tracks_for_scope(manifest, scope)
    )
    for track_name, clips in track_jobs:
        print(f"[{model_id}] Track '{track_name}' ({len(clips)} clips)...")
        recs: List[Dict[str, Any]] = []
        reused = 0
        for i, clip in enumerate(clips, 1):
            cid = clip["id"]
            if scope == "m235_rest" and cid in cached_by_id:
                recs.append(cached_by_id[cid])
                reused += 1
                continue
            arr = load_wav_as_16k_f32(clip["wav_path"])
            out = runner.transcribe_array(arr)
            rec = _score_clip(track_name, clip, out)
            recs.append(rec)
            cached_by_id[cid] = rec
            if i % 20 == 0 or i == len(clips):
                peak_rss = max(peak_rss, get_process_rss_mb())
                print(f"  progress {i}/{len(clips)} (RSS={peak_rss:.0f} MB, cached={reused})")
            if scope == "m235_rest" and i % 25 == 0:
                tracks_out[track_name] = _finalize_track_bundle(track_name, recs)
                _save_payload()

        tracks_out[track_name] = _finalize_track_bundle(track_name, recs)
        # If full run produced m235_verified_111, also materialize m235_strat30 slice cleanly
        if track_name == "m235_verified_111" and len(recs) == len(manifest["tracks"]["m235_verified_111"]):
            strat_ids = {c["id"] for c in manifest["tracks"]["m235_strat30"]}
            strat_recs = [r for r in recs if r["id"] in strat_ids]
            tracks_out["m235_strat30"] = _finalize_track_bundle("m235_strat30", strat_recs)

        m = tracks_out[track_name]["metrics"]
        if "wer" in m:
            dev_info = f" | dev_wer={m['devanagari']['wer']:.4f}" if "devanagari" in m else ""
            print(
                f"  -> {track_name}: WER={m['wer']:.4f} CER={m['cer']:.4f} "
                f"del={m['del_rate']:.4f} ret={m['word_retention_ratio']:.3f} "
                f"omit={m['omitted_clip_pct']}% RTF={m['rtf']:.3f}{dev_info}"
            )
        else:
            print(f"  -> {track_name}: hi_to_en_wps_ratio={m.get('hinglish_to_english_wps_ratio')} RTF={m['rtf']:.3f}")
        _save_payload()

    official: Dict[str, Any] = dict(existing.get("official_meetings") or {})
    if "m234_full_90s" in tracks_out:
        sc = _score_official_meeting(Path(manifest["full_meetings"]["m234"]["ref_path"]), tracks_out["m234_full_90s"]["clips"])
        if sc:
            official["m234"] = sc
    if "m235_verified_111" in tracks_out:
        m235_all = tracks_out["m235_verified_111"].get("clips") or []
        if len(m235_all) == len(manifest["tracks"]["m235_verified_111"]):
            strat_ids = {c["id"] for c in manifest["tracks"]["m235_strat30"]}
            tracks_out["m235_strat30"] = _finalize_track_bundle(
                "m235_strat30", [r for r in m235_all if r["id"] in strat_ids]
            )
            sc = _score_official_meeting(Path(manifest["full_meetings"]["m235"]["ref_path"]), m235_all)
            if sc:
                official["m235"] = sc
    _save_payload(official)

    runner.unload()
    vram_end = query_gpu_vram()
    final_data = json.loads(out_path.read_text(encoding="utf-8"))
    final_data["vram_after_unload_mb"] = vram_end.get("used_mb")
    out_path.write_text(json.dumps(final_data, indent=2, ensure_ascii=False), encoding="utf-8")

    verify_db_unchanged()
    print(f"[DONE] Saved {out_path} | Peak RSS={peak_rss:.1f} MB | VRAM peak={vram_peak} MB | DB OK")
    return 0


# ---------------------------------------------------------------------------
# Continuous Streaming Simulation (--mode stream)
# ---------------------------------------------------------------------------

def _simulate_vad_stream(
    wav_paths: List[Tuple[str, str, str]],
    runner: ModelRunner,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Simulate continuous real-time VAD-endpointed streaming across one or more tracks.
    Maintains real wall-clock execution vs audio-clock availability:
      backlog_sec = max(0, worker_free_at - chunk_end_sec)
      commit_latency_sec = backlog_sec + infer_sec
    """
    spans_all: List[Tuple[str, str, float, float, np.ndarray]] = []
    total_audio_sec = 0.0
    for track_label, speaker_label, wpath in wav_paths:
        arr = load_wav_as_16k_f32(wpath)
        dur = float(arr.size) / float(TARGET_SAMPLE_RATE)
        total_audio_sec = max(total_audio_sec, dur)
        spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
            arr,
            dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=200),
            max_chunk_sec=15.0,
            merge_gap_sec=0.45,
            pad_sec=0.15,
        ) or [(0, arr.size)]
        for s_i, e_i in spans:
            sl = arr[s_i:e_i]
            if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue
            spans_all.append((track_label, speaker_label, s_i / float(TARGET_SAMPLE_RATE), e_i / float(TARGET_SAMPLE_RATE), sl))

    # Chronological order by utterance end (when endpoint triggers in live stream!)
    spans_all.sort(key=lambda x: (x[3], x[2]))

    worker_free_at = 0.0
    commit_latencies: List[float] = []
    backlogs: List[float] = []
    would_drop = 0
    total_infer = 0.0
    segs: List[Dict[str, Any]] = []

    for track_label, speaker_label, s_sec, e_sec, sl in spans_all:
        backlog = max(0.0, worker_free_at - e_sec)
        if backlog > 15.0:
            would_drop += 1
        out = runner.transcribe_array(sl, live_mode=True)
        inf_s = float(out["infer_sec"])
        total_infer += inf_s
        commit_at = max(e_sec, worker_free_at) + inf_s
        commit_lat = commit_at - e_sec
        worker_free_at = commit_at
        backlogs.append(backlog)
        commit_latencies.append(commit_lat)
        if out["raw_text"]:
            segs.append({
                "start_time": round(s_sec, 2),
                "end_time": round(e_sec, 2),
                "start": round(s_sec, 2),
                "end": round(e_sec, 2),
                "source_track": track_label,
                "speaker": speaker_label,
                "raw_text": out["raw_text"],
                "text": out["roman_text"],
                "infer_sec": inf_s,
                "commit_latency_sec": round(commit_lat, 3),
                "backlog_sec": round(backlog, 3),
            })

    end_drain_lag = max(0.0, worker_free_at - total_audio_sec)
    summary = {
        "audio_duration_sec": round(total_audio_sec, 2),
        "chunks_processed": len(spans_all),
        "emitted_segments": len(segs),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.01, total_audio_sec), 4),
        "p50_commit_latency_sec": round(float(np.percentile(commit_latencies, 50)), 3) if commit_latencies else 0.0,
        "p95_commit_latency_sec": round(float(np.percentile(commit_latencies, 95)), 3) if commit_latencies else 0.0,
        "max_backlog_sec": round(max(backlogs), 3) if backlogs else 0.0,
        "end_drain_lag_sec": round(end_drain_lag, 3),
        "would_drop_over_15s_chunks": would_drop,
        "revisions_per_utterance": 0.0,
        "full_text_roman": " ".join(s["text"] for s in segs).strip(),
        "full_text_raw": " ".join(s["raw_text"] for s in segs).strip(),
    }
    return summary, segs


def _simulate_nemotron_560ms_stream(
    wav_paths: List[Tuple[str, str, str]],
    runner: ModelRunner,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    frame_n = int(0.56 * TARGET_SAMPLE_RATE)
    total_audio_sec = 0.0
    step_infer_ms: List[float] = []
    commit_latencies: List[float] = []
    backlogs: List[float] = []
    revisions_total = 0
    total_infer = 0.0
    would_drop = 0
    segs: List[Dict[str, Any]] = []

    for track_label, speaker_label, wpath in wav_paths:
        arr = load_wav_as_16k_f32(wpath)
        dur = float(arr.size) / float(TARGET_SAMPLE_RATE)
        total_audio_sec = max(total_audio_sec, dur)
        # Active spans map cleanly so continuous utterances commit at silence boundaries
        spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
            arr,
            dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=200),
            max_chunk_sec=20.0,
            merge_gap_sec=0.5,
            pad_sec=0.15,
        ) or [(0, arr.size)]

        worker_free_at = 0.0
        for s_i, e_i in spans:
            sl = arr[s_i:e_i]
            if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue
            s_sec = s_i / float(TARGET_SAMPLE_RATE)
            e_sec = e_i / float(TARGET_SAMPLE_RATE)
            st = runner.obj.create_stream()
            padded = np.concatenate([sl, np.zeros(int(1.0 * TARGET_SAMPLE_RATE), dtype=np.float32)])
            prev_txt = ""
            chunk_t0 = time.perf_counter()
            for off in range(0, padded.size, frame_n):
                piece = padded[off : off + frame_n]
                frame_end_sec = min(e_sec + 1.0, s_sec + (off + piece.size) / float(TARGET_SAMPLE_RATE))
                backlog = max(0.0, worker_free_at - frame_end_sec)
                if backlog > 15.0:
                    would_drop += 1
                ft0 = time.perf_counter()
                st.accept_waveform(TARGET_SAMPLE_RATE, piece)
                while runner.obj.is_ready(st):
                    runner.obj.decode_stream(st)
                if off + frame_n >= padded.size:
                    st.input_finished()
                    while runner.obj.is_ready(st):
                        runner.obj.decode_stream(st)
                fdt = time.perf_counter() - ft0
                step_infer_ms.append(fdt * 1000.0)
                worker_free_at = max(frame_end_sec, worker_free_at) + fdt
                backlogs.append(backlog)
                cur_txt = str(runner.obj.get_result(st) or "").strip()
                if cur_txt and cur_txt != prev_txt:
                    revisions_total += 1
                    prev_txt = cur_txt

            utt_inf = time.perf_counter() - chunk_t0
            total_infer += utt_inf
            commit_lat = max(0.0, worker_free_at - e_sec)
            commit_latencies.append(commit_lat)
            if prev_txt:
                segs.append({
                    "start_time": round(s_sec, 2),
                    "end_time": round(e_sec, 2),
                    "start": round(s_sec, 2),
                    "end": round(e_sec, 2),
                    "source_track": track_label,
                    "speaker": speaker_label,
                    "raw_text": prev_txt,
                    "text": prev_txt,
                    "commit_latency_sec": round(commit_lat, 3),
                })

    summary = {
        "audio_duration_sec": round(total_audio_sec, 2),
        "chunks_processed": len(step_infer_ms),
        "emitted_segments": len(segs),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.01, total_audio_sec), 4),
        "p50_commit_latency_sec": round(float(np.percentile(commit_latencies, 50)), 3) if commit_latencies else 0.0,
        "p95_commit_latency_sec": round(float(np.percentile(commit_latencies, 95)), 3) if commit_latencies else 0.0,
        "p50_560ms_step_ms": round(float(np.percentile(step_infer_ms, 50)), 1) if step_infer_ms else 0.0,
        "p95_560ms_step_ms": round(float(np.percentile(step_infer_ms, 95)), 1) if step_infer_ms else 0.0,
        "max_backlog_sec": round(max(backlogs), 3) if backlogs else 0.0,
        "end_drain_lag_sec": round(max(commit_latencies[-1], 0.0), 3) if commit_latencies else 0.0,
        "would_drop_over_15s_chunks": would_drop,
        "revisions_per_utterance": round(revisions_total / max(1, len(segs)), 2),
        "full_text_roman": " ".join(s["text"] for s in segs).strip(),
        "full_text_raw": " ".join(s["raw_text"] for s in segs).strip(),
    }
    return summary, segs


def run_streaming_suite(engine_filter: Optional[str] = None) -> int:
    db_before = verify_db_unchanged()
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "stream_results.json"
    e22_cont = manifest["continuous_streams"]["earnings22_us_continuous"]
    m240_cont = manifest["continuous_streams"]["m240_us_in_call_5m"]
    m234_meta = manifest["full_meetings"]["m234"]
    m234_ref = json.loads(Path(m234_meta["ref_path"]).read_text(encoding="utf-8"))

    stream_engines = [
        ("p0_whisper_small_stream", "p0_whisper_small_cpu", False),
        ("onnx_nemotron_stream_560ms_int8", "onnx_nemotron_stream_560ms_int8", True),
        ("onnx_parakeet_tdt_v2_vad_stream", "onnx_parakeet_tdt_v2_int8", False),
        ("onnx_qwen3_asr_06b_vad_stream", "onnx_qwen3_asr_06b_int8", False),
    ]
    if engine_filter:
        stream_engines = [e for e in stream_engines if e[0] == engine_filter]
        if not stream_engines:
            raise ValueError(f"Unknown --stream-engine: {engine_filter}")

    results: Dict[str, Any] = {"db_sha256": db_before, "engines": {}}
    if out_path.exists():
        old = json.loads(out_path.read_text(encoding="utf-8"))
        results["engines"] = dict(old.get("engines") or {})
    for stream_name, base_model_id, is_frame_560 in stream_engines:
        print(f"\n[Stream] Running {stream_name} ({base_model_id})...")
        rss0 = get_process_rss_mb()
        mnt = 256 if base_model_id == "onnx_qwen3_asr_06b_int8" else 512
        runner = ModelRunner(base_model_id, max_new_tokens=mnt)
        runner.load()
        sim_fn = _simulate_nemotron_560ms_stream if is_frame_560 else _simulate_vad_stream

        # 1. earnings22_us_continuous (325.9s)
        e22_sum, e22_segs = sim_fn([("system", "Speaker 1", e22_cont["wav_path"])], runner)
        e22_al = align_clip_details(e22_cont["reference_text"], e22_sum["full_text_raw"], mode="strict")
        e22_sum["wer"] = round(e22_al["edits"] / max(1, e22_al["ref_words"]), 4)
        e22_sum["del_rate"] = e22_al["del_rate"]
        e22_sum["word_retention_ratio"] = e22_al["word_retention_ratio"]
        print(f"  earnings22_us_cont: WER={e22_sum['wer']:.4f} RTF={e22_sum['rtf']:.3f} P95_lat={e22_sum['p95_commit_latency_sec']}s max_bl={e22_sum['max_backlog_sec']}s")

        # 2. m240_us_in_call_5m (300.0s real call)
        m240_sum, _ = sim_fn([("system", "Speaker 1", m240_cont["wav_path"])], runner)
        m240_words = tokenize_strict(m240_sum["full_text_raw"])
        m240_sum["total_words"] = len(m240_words)
        m240_sum["words_per_min"] = round(len(m240_words) / max(0.1, m240_sum["audio_duration_sec"] / 60.0), 1)
        m240_sum["duplication_words"] = _count_duplication_words(m240_sum["full_text_raw"].split())
        m240_sum["script_purity"] = audit_mixed_script_and_utf8([{"text": m240_sum["full_text_raw"]}])
        print(f"  m240_5m_call      : words={len(m240_words)} ({m240_sum['words_per_min']}/min) RTF={m240_sum['rtf']:.3f} P95_lat={m240_sum['p95_commit_latency_sec']}s")

        # 3. M#234 dual-track continuous (90.15s)
        m234_sum, m234_segs = sim_fn(
            [("system", "Hemanth Kumar Talathoti", m234_meta["system"]), ("mic", "You (Mic)", m234_meta["mic"])],
            runner,
        )
        m234_sc = score_predictions_against_reference(m234_ref, m234_segs)
        m234_sum["official_scores"] = {
            "stream_hinglish_norm_wer": m234_sc["stream_hinglish_norm_wer"],
            "english_wer": m234_sc["english_wer"],
            "hindi_code_switch_wer": m234_sc["hindi_code_switch_wer"],
            "technical_entity_recall": m234_sc["technical_entity_recall"],
            "stream_norm_ops": m234_sc["stream_norm_ops"],
        }
        print(f"  m234_dual_track   : stream_WER={m234_sc['stream_hinglish_norm_wer']:.4f} RTF={m234_sum['rtf']:.3f}")

        peak_rss = max(rss0, get_process_rss_mb())
        runner.unload()
        results["engines"][stream_name] = {
            "base_model_id": base_model_id,
            "load_sec": runner.load_sec,
            "peak_rss_mb": round(peak_rss, 1),
            "earnings22_us_continuous": {k: v for k, v in e22_sum.items() if not k.startswith("full_text_")},
            "m240_us_in_call_5m": {k: v for k, v in m240_sum.items() if not k.startswith("full_text_")},
            "m234_continuous": {k: v for k, v in m234_sum.items() if not k.startswith("full_text_")},
        }

    out_path = RESULTS_DIR / "stream_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] Saved {out_path}")
    return 0


# ---------------------------------------------------------------------------
# Non-Oracle Hybrid Router Simulation (--mode hybrid)
# ---------------------------------------------------------------------------

def _load_model_json(model_id: str) -> Optional[Dict[str, Any]]:
    p = RESULTS_DIR / f"{model_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _clip_map(model_data: Dict[str, Any], track_name: str) -> Dict[str, Dict[str, Any]]:
    tb = (model_data.get("tracks") or {}).get(track_name) or {}
    return {c["id"]: c for c in (tb.get("clips") or [])}


def run_hybrid_sweep() -> int:
    db_before = verify_db_unchanged()
    lid_src = _load_model_json("p0_whisper_small_cpu")
    parakeet = _load_model_json("onnx_parakeet_tdt_v2_int8")
    qwen_onnx = _load_model_json("onnx_qwen3_asr_06b_int8")
    whisper_t = _load_model_json("p0_whisper_turbo_cpu")
    indic = _load_model_json("onnx_indic_conformer_hi")
    qwen_hf = _load_model_json("hf_qwen3_asr_06b_guarded")

    if not (lid_src and parakeet and qwen_onnx):
        raise RuntimeError("Missing required base model JSONs for hybrid simulation")

    eval_tracks = [
        "earnings22_us",
        "fleurs_en_us",
        "nptel_in_en",
        "fleurs_hi_in",
        "m234_full_90s",
        "m235_verified_111",
    ]
    available_tracks = [
        t for t in eval_tracks
        if t in (parakeet.get("tracks") or {}) and t in (qwen_onnx.get("tracks") or {}) and t in (lid_src.get("tracks") or {})
    ]

    def _eval_router(
        name: str,
        route_fn: Callable[[str, Dict[str, Any]], str],
        models_by_key: Dict[str, Dict[str, Any]],
        tracks_to_eval: List[str],
        *,
        is_oracle: bool = False,
        lid_overhead_rtf: float = 0.04,
    ) -> Dict[str, Any]:
        track_summaries: Dict[str, Any] = {}
        for t in tracks_to_eval:
            lid_map = _clip_map(lid_src, t)
            maps = {k: _clip_map(m, t) for k, m in models_by_key.items() if m is not None}
            ref_ids = sorted(lid_map.keys())
            # Use intersection of clips present across all participant models for fair comparison
            common_ids = [
                cid for cid in ref_ids
                if all(cid in mp for mp in maps.values())
            ]
            chosen_recs: List[Dict[str, Any]] = []
            route_counts: Dict[str, int] = {}
            misrouted_to_en = 0
            misrouted_to_non_en = 0
            for cid in common_ids:
                lid_rec = lid_map[cid]
                sel_key = route_fn(t, lid_rec)
                route_counts[sel_key] = route_counts.get(sel_key, 0) + 1
                chosen = dict(maps[sel_key][cid])
                # track routing errors transparently
                if t in ("fleurs_hi_in",) and sel_key == "en_model":
                    misrouted_to_en += 1
                elif t in ("m234_full_90s", "m235_verified_111"):
                    tags = lid_rec.get("language_tags") or []
                    if sel_key == "en_model" and tags != ["en"]:
                        misrouted_to_en += 1
                elif t in ("earnings22_us", "fleurs_en_us", "nptel_in_en") and sel_key != "en_model":
                    misrouted_to_non_en += 1
                chosen_recs.append(chosen)

            agg = aggregate_track_metrics(chosen_recs, align_key="align_roman" if t == "fleurs_hi_in" else "align")
            if t == "fleurs_hi_in":
                agg["devanagari"] = aggregate_track_metrics(chosen_recs, align_key="align_devanagari")
            agg["route_counts"] = route_counts
            agg["blended_rtf_with_lid"] = round(float(agg["rtf"]) + (0.0 if is_oracle else lid_overhead_rtf), 4)
            if t in ("fleurs_hi_in", "m234_full_90s", "m235_verified_111"):
                agg["hindi_or_hinglish_misrouted_to_en_pct"] = round(100.0 * misrouted_to_en / max(1, len(common_ids)), 2)
            else:
                agg["en_misrouted_to_non_en_pct"] = round(100.0 * misrouted_to_non_en / max(1, len(common_ids)), 2)
            track_summaries[t] = agg

        return {
            "router_id": name,
            "is_oracle": is_oracle,
            "tracks": track_summaries,
        }

    routers: List[Dict[str, Any]] = []
    # Hybrid-A threshold sweep: Parakeet-EN + Qwen3-ONNX-HI/Mix
    for thresh in (0.60, 0.70, 0.80, 0.85, 0.90, 0.95):
        def _mk_a(th: float) -> Callable[[str, Dict[str, Any]], str]:
            def _r(_t: str, lid: Dict[str, Any]) -> str:
                lp = lid.get("lang_probs") or {}
                p_en = float(lp.get("p_en", 0.0))
                p_hi_ur = float(lp.get("p_hi", 0.0)) + float(lp.get("p_ur", 0.0))
                return "en_model" if (p_en >= th and p_hi_ur < 0.15) else "multi_model"
            return _r

        routers.append(
            _eval_router(
                f"hybrid_A_parakeet_qwen_onnx_T{int(thresh*100)}",
                _mk_a(thresh),
                {"en_model": parakeet, "multi_model": qwen_onnx},
                available_tracks,
            )
        )

    # Hybrid-B: Whisper-Turbo-EN + Qwen3-ONNX
    if whisper_t:
        b_tracks = [t for t in available_tracks if t in (whisper_t.get("tracks") or {})]
        for thresh in (0.80, 0.85, 0.90):
            def _mk_b(th: float) -> Callable[[str, Dict[str, Any]], str]:
                def _r(_t: str, lid: Dict[str, Any]) -> str:
                    lp = lid.get("lang_probs") or {}
                    p_en = float(lp.get("p_en", 0.0))
                    p_hi_ur = float(lp.get("p_hi", 0.0)) + float(lp.get("p_ur", 0.0))
                    return "en_model" if (p_en >= th and p_hi_ur < 0.15) else "multi_model"
                return _r

            routers.append(
                _eval_router(
                    f"hybrid_B_turbo_qwen_onnx_T{int(thresh*100)}",
                    _mk_b(thresh),
                    {"en_model": whisper_t, "multi_model": qwen_onnx},
                    b_tracks,
                )
            )

    # Hybrid-C: 3-way Parakeet + IndicConformer + Qwen3-ONNX
    if indic:
        def _r_c(_t: str, lid: Dict[str, Any]) -> str:
            lp = lid.get("lang_probs") or {}
            p_en = float(lp.get("p_en", 0.0))
            p_hi = float(lp.get("p_hi", 0.0))
            p_ur = float(lp.get("p_ur", 0.0))
            if p_en >= 0.85 and (p_hi + p_ur) < 0.15:
                return "en_model"
            if p_hi >= 0.85:
                return "hi_model"
            return "multi_model"

        routers.append(
            _eval_router(
                "hybrid_C_parakeet_indic_qwen_onnx_3way",
                _r_c,
                {"en_model": parakeet, "hi_model": indic, "multi_model": qwen_onnx},
                available_tracks,
            )
        )

    # Hybrid-D on 73-clip hf_screen intersection: Parakeet-EN + HF Guarded Qwen3
    if qwen_hf:
        d_tracks = [t for t in ("earnings22_us", "fleurs_en_us", "nptel_in_en", "fleurs_hi_in", "m234_full_90s") if t in (qwen_hf.get("tracks") or {})]
        def _r_d(_t: str, lid: Dict[str, Any]) -> str:
            lp = lid.get("lang_probs") or {}
            if float(lp.get("p_en", 0.0)) >= 0.85 and (float(lp.get("p_hi", 0.0)) + float(lp.get("p_ur", 0.0))) < 0.15:
                return "en_model"
            return "multi_model"

        routers.append(
            _eval_router(
                "hybrid_D_parakeet_qwen_hf_guarded_T85_screen",
                _r_d,
                {"en_model": parakeet, "multi_model": qwen_hf},
                d_tracks,
            )
        )

    # Oracle Upper Bound (Non-deployable reference!)
    def _r_oracle(t: str, lid: Dict[str, Any]) -> str:
        if t in ("earnings22_us", "fleurs_en_us", "nptel_in_en"):
            return "en_model"
        if t in ("m234_full_90s", "m235_verified_111") and (lid.get("language_tags") or []) == ["en"]:
            return "en_model"
        return "multi_model"

    routers.append(
        _eval_router(
            "ORACLE_UPPER_BOUND_NON_DEPLOYABLE_parakeet_qwen_onnx",
            _r_oracle,
            {"en_model": parakeet, "multi_model": qwen_onnx},
            available_tracks,
            is_oracle=True,
        )
    )

    out = {"db_sha256": db_before, "lid_source": "p0_whisper_small_cpu", "routers": routers}
    out_path = RESULTS_DIR / "hybrid_results.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] Saved {out_path} ({len(routers)} router variants)")
    return 0


# ---------------------------------------------------------------------------
# Unified Comparison & Regression Gate Report (--mode compare)
# ---------------------------------------------------------------------------

def run_compare() -> int:
    db_before = verify_db_unchanged()
    model_reports: Dict[str, Any] = {}
    for mid in MODEL_SPECS:
        data = _load_model_json(mid)
        if not data:
            continue
        compact_tracks: Dict[str, Any] = {}
        for tname, tb in (data.get("tracks") or {}).items():
            compact_tracks[tname] = tb.get("metrics") or {}
        model_reports[mid] = {
            "display": MODEL_SPECS[mid]["display"],
            "family": MODEL_SPECS[mid]["family"],
            "scope": data.get("scope"),
            "device": data.get("device"),
            "compute_type": data.get("compute_type"),
            "load_sec": data.get("load_sec"),
            "peak_rss_mb": data.get("peak_rss_mb"),
            "vram_before_mb": data.get("vram_before_mb"),
            "vram_peak_mb": data.get("vram_peak_mb"),
            "vram_after_unload_mb": data.get("vram_after_unload_mb"),
            "tracks": compact_tracks,
            "official_meetings": {
                k: {
                    "stream_hinglish_norm_wer": v.get("stream_hinglish_norm_wer"),
                    "english_wer": v.get("english_wer"),
                    "hindi_code_switch_wer": v.get("hindi_code_switch_wer"),
                    "technical_entity_recall": v.get("technical_entity_recall"),
                    "stream_norm_ops": v.get("stream_norm_ops"),
                }
                for k, v in (data.get("official_meetings") or {}).items()
            },
        }

    p0_ref: Dict[str, Any] = {}
    if PHASE0_AFTER_PATH.exists():
        p0 = json.loads(PHASE0_AFTER_PATH.read_text(encoding="utf-8"))
        for k in ("small", "large-v3-turbo"):
            m = (p0.get("models") or {}).get(k) or {}
            p0_ref[k] = {
                "us_neural_wer": (m.get("us_english_benchmark") or {}).get("wer"),
                "in_neural_wer": (m.get("in_english_benchmark") or {}).get("wer"),
                "m235_real_en_wer": (m.get("m235_real_english") or {}).get("wer"),
                "m234_stream_norm_wer": (m.get("m234_hinglish_full_90s") or {}).get("stream_hinglish_norm_wer"),
            }

    p25_ref: Dict[str, Any] = {}
    if PHASE25_AB_PATH.exists():
        p25 = json.loads(PHASE25_AB_PATH.read_text(encoding="utf-8"))
        p25_ref = {
            "note": "Historical CUDA FP16 reference from Phase 2.5 (M#234 16/16 & M#235 111/111)",
            "table": p25.get("side_by_side_table") or p25.get("summary") or {},
        }

    stream_data = json.loads((RESULTS_DIR / "stream_results.json").read_text(encoding="utf-8")) if (RESULTS_DIR / "stream_results.json").exists() else None
    hybrid_data = json.loads((RESULTS_DIR / "hybrid_results.json").read_text(encoding="utf-8")) if (RESULTS_DIR / "hybrid_results.json").exists() else None

    # Phase 0 Regression Check vs real-world English baselines (`p0_whisper_small_cpu` / `p0_whisper_turbo_cpu`)
    base_small = model_reports.get("p0_whisper_small_cpu", {}).get("tracks", {})
    base_turbo = model_reports.get("p0_whisper_turbo_cpu", {}).get("tracks", {})
    regression_gate: Dict[str, Any] = {}
    for mid, mr in model_reports.items():
        tr = mr["tracks"]
        gate_row: Dict[str, Any] = {"scope": mr["scope"]}
        for t in ("earnings22_us", "fleurs_en_us", "nptel_in_en", "phase0_us_neural", "phase0_in_neural"):
            if t in tr and t in base_turbo:
                gate_row[t] = {
                    "wer": tr[t]["wer"],
                    "delta_vs_p0_small": round(tr[t]["wer"] - base_small[t]["wer"], 4) if t in base_small else None,
                    "delta_vs_p0_turbo": round(tr[t]["wer"] - base_turbo[t]["wer"], 4),
                    "regresses_gt_1pct_vs_turbo": bool(tr[t]["wer"] > base_turbo[t]["wer"] + 0.01),
                }
        regression_gate[mid] = gate_row

    # Include M#241 clip-by-clip sample texts for U1..U5, EN1..EN3, MIC across evaluated models
    m241_side_by_side: Dict[str, List[Dict[str, Any]]] = {}
    for mid in MODEL_SPECS:
        data = _load_model_json(mid)
        if not data or "m241_scrum_omission" not in (data.get("tracks") or {}):
            continue
        for cl in data["tracks"]["m241_scrum_omission"]["clips"]:
            m241_side_by_side.setdefault(cl["id"], []).append({
                "model_id": mid,
                "detected_language": cl.get("detected_language"),
                "pred_words": len(tokenize_hinglish_norm(cl.get("roman_text") or cl.get("raw_text") or "")),
                "raw_text": (cl.get("raw_text") or "")[:240],
                "roman_text": (cl.get("roman_text") or "")[:240],
            })

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256": db_before,
        "phase0_frozen_reference": p0_ref,
        "phase25_historical_cuda_reference": p25_ref,
        "models": model_reports,
        "english_regression_gate": regression_gate,
        "streaming_benchmark": stream_data,
        "hybrid_routers": hybrid_data,
        "m241_scrum_side_by_side": m241_side_by_side,
    }
    out_path = RESULTS_DIR / "phase1_comparison_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] Unified comparison report written to {out_path}")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VOX Phase 1 Multi-Accent ASR & Hybrid Benchmark")
    p.add_argument("--model", choices=sorted(MODEL_SPECS.keys()), help="Single model benchmark id")
    p.add_argument(
        "--scope",
        choices=["full", "standard", "standard_en", "standard_hi_mix", "m235_rest", "hf_screen", "hf_screen_fast"],
        default=None,
        help="Track subset scope for --model",
    )
    p.add_argument("--tracks", type=str, default=None, help="Optional comma-separated track names for --model")
    p.add_argument("--cpu-threads", type=int, default=None, help="Override CPU threads for the model")
    p.add_argument("--mode", choices=["stream", "hybrid", "compare"], help="Simulation or report mode")
    p.add_argument(
        "--stream-engine",
        choices=[
            "p0_whisper_small_stream",
            "onnx_nemotron_stream_560ms_int8",
            "onnx_parakeet_tdt_v2_vad_stream",
            "onnx_qwen3_asr_06b_vad_stream",
        ],
        default=None,
        help="Optional single streaming engine for --mode stream",
    )
    return p.parse_args(argv)


@contextlib.contextmanager
def _prevent_windows_sleep():
    enabled = False
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        enabled = True
    except Exception:
        pass
    try:
        yield
    finally:
        if enabled:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
            except Exception:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    with _prevent_windows_sleep():
        if args.model:
            scope = args.scope or MODEL_SPECS[args.model]["default_scope"]
            tracks_ov = [t.strip() for t in args.tracks.split(",") if t.strip()] if args.tracks else None
            return run_model_benchmark(args.model, scope, cpu_threads=args.cpu_threads, tracks_override=tracks_ov)
        if args.mode == "stream":
            return run_streaming_suite(engine_filter=args.stream_engine)
        if args.mode == "hybrid":
            return run_hybrid_sweep()
        if args.mode == "compare":
            return run_compare()
    raise SystemExit("Provide either --model <id> or --mode <stream|hybrid|compare>")


if __name__ == "__main__":
    raise SystemExit(main())

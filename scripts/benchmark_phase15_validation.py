"""
Phase 1.5 CTO Validation & Challenger Benchmark Suite.

Strictly read-only on `src/voxlocal/`, `tests/`, `data/config.json`, `data/voxlocal.db`,
and all original recordings in `data/recordings/`.

Supports:
  --model <id> [--scope ...] [--tracks ...] [--cpu-threads N] [--device auto|cpu|cuda]
  --mode m240_draft
  --mode rescore_m240
  --mode stream_v2 [--stream-engines ...]
  --mode routing_v2
  --mode report
"""

from __future__ import annotations

import argparse
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

from benchmark_phase1_asr import (
    _aggregate_m241_probe,
    _prevent_windows_sleep,
    _resolve_whisper_path,
    _score_official_meeting,
    _split_long_audio,
    align_clip_details,
    aggregate_track_metrics,
    verify_db_unchanged,
)
from benchmark_speech_intelligence import get_process_rss_mb, query_gpu_vram
from guarded_qwen3_onnx import GuardedQwen3OnnxTranscriber
from score_ground_truth import (
    _count_duplication_words,
    audit_mixed_script_and_utf8,
    tokenize_hinglish_norm,
    tokenize_strict,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.hf_asr_engine import (
    LocalHFASRTranscriber,
    clean_repetition_tail,
    sanitize_utf8_text,
)
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    load_wav_as_16k_f32,
    merge_domain_hotwords,
)

BENCH_DIR = ROOT_DIR / "data" / "benchmarks"
CORPUS15_MANIFEST = BENCH_DIR / "phase15_corpus" / "manifest.json"
PHASE1_RESULTS_DIR = BENCH_DIR / "phase1_results"
RESULTS15_DIR = BENCH_DIR / "phase15_results"
CHALLENGER_DIR = ROOT_DIR / "data" / "models" / "challengers"
MODELS_DIR = ROOT_DIR / "data" / "models"
M240_DRAFT_PATH = BENCH_DIR / "M240_us_in_5m_reference_draft.json"
M240_CANDIDATES_PATH = RESULTS15_DIR / "m240_candidate_predictions.json"

ALL_TRACK_ORDER_15 = [
    "earnings22_us",
    "fleurs_en_us",
    "ami_meetings_en",
    "phase0_us_neural",
    "nptel_in_en",
    "monsoon_in_en",
    "phase0_in_neural",
    "fleurs_hi_in",
    "coshe_hinglish",
    "m234_full_90s",
    "m235_verified_111",
    "m241_scrum_omission",
]

CORE_EN_AND_NEW_TRACKS = [
    "earnings22_us",
    "fleurs_en_us",
    "ami_meetings_en",
    "phase0_us_neural",
    "nptel_in_en",
    "monsoon_in_en",
    "phase0_in_neural",
    "fleurs_hi_in",
    "coshe_hinglish",
    "m234_full_90s",
    "m235_strat30",
    "m241_scrum_omission",
]

NEW_3_TRACKS = ["ami_meetings_en", "monsoon_in_en", "coshe_hinglish"]

MODEL_SPECS_15: Dict[str, Dict[str, Any]] = {
    "onnx_qwen3_asr_06b_int8_guarded": {
        "display": "Qwen3-ASR 0.6B ONNX INT8 (Guarded ORT)",
        "family": "Qwen3-ASR Guarded ONNX INT8",
        "languages": ["en", "hi", "hinglish"],
        "default_scope": "full",
    },
    "onnx_qwen3_asr_06b_int8_guarded_hotwords": {
        "display": "Qwen3-ASR 0.6B ONNX INT8 (Guarded ORT + Domain Hotwords)",
        "family": "Qwen3-ASR Guarded ONNX INT8",
        "languages": ["en", "hi", "hinglish"],
        "default_scope": "hotword_ab",
    },
    "ct2_whisper_base_hinglish_int8": {
        "display": "Whisper Base Hinglish (CT2 INT8, 73 MB)",
        "family": "CTranslate2 Whisper Base INT8",
        "languages": ["hinglish", "hi", "en"],
        "default_scope": "full",
    },
    "ct2_oriserve_apex_int8": {
        "display": "Oriserve Whisper-Hindi2Hinglish-Apex (CT2 INT8, auto lang)",
        "family": "CTranslate2 Whisper Large-v3-Turbo Fine-Tune",
        "languages": ["hinglish", "hi", "en"],
        "default_scope": "standard",
    },
    "prod_whisper_turbo_balanced": {
        "display": "VOX Production Baseline: Whisper Large-v3-Turbo (en+hi, balanced, hotwords)",
        "family": "LocalWhisperTranscriber Production",
        "languages": ["en", "hi", "hinglish"],
        "default_scope": "standard",
    },
    "prod_whisper_small_balanced": {
        "display": "VOX Production Baseline: Whisper Small (en+hi, balanced, hotwords)",
        "family": "LocalWhisperTranscriber Production",
        "languages": ["en", "hi", "hinglish"],
        "default_scope": "full",
    },
    "p0_whisper_turbo_cpu": {
        "display": "Zero-Hotword Whisper Large-v3-Turbo (CPU INT8)",
        "family": "CTranslate2 Whisper INT8",
        "languages": ["multilingual"],
        "default_scope": "new3",
    },
    "p0_whisper_small_cpu": {
        "display": "Zero-Hotword Whisper Small (CPU INT8)",
        "family": "CTranslate2 Whisper INT8",
        "languages": ["multilingual"],
        "default_scope": "new3",
    },
    "onnx_parakeet_tdt_v2_int8": {
        "display": "Parakeet TDT 0.6B v2 ONNX INT8",
        "family": "NeMo TDT ONNX INT8",
        "languages": ["en"],
        "default_scope": "new3",
    },
    "hf_qwen3_asr_06b_guarded": {
        "display": "Qwen3-ASR 0.6B HF Guarded (PyTorch)",
        "family": "Qwen3-ASR PyTorch Guarded",
        "languages": ["en", "hi", "hinglish"],
        "default_scope": "full",
    },
}


class Phase15ModelRunner:
    def __init__(
        self,
        model_id: str,
        *,
        cpu_threads: Optional[int] = None,
        device: str = "auto",
        max_new_tokens: int = 256,
    ) -> None:
        self.model_id = model_id
        self.cpu_threads = cpu_threads
        self.requested_device = str(device or "auto").lower()
        self.max_new_tokens = int(max_new_tokens)
        self.device = "cpu"
        self.compute_type = "int8"
        self.load_sec = 0.0
        self.obj: Any = None
        self._hotwords: Optional[str] = None

    def load(self) -> None:
        t0 = time.perf_counter()
        if self.model_id in ("onnx_qwen3_asr_06b_int8_guarded", "onnx_qwen3_asr_06b_int8_guarded_hotwords"):
            th = self.cpu_threads or 4
            dec = GuardedQwen3OnnxTranscriber(num_threads=th)
            dec.load()
            self.obj = dec
            self.device = "cpu"
            self.compute_type = f"onnx_int8_guarded_t{th}"
            if self.model_id.endswith("_hotwords"):
                self._hotwords = merge_domain_hotwords(None, context="meeting")

        elif self.model_id == "ct2_whisper_base_hinglish_int8":
            from faster_whisper import WhisperModel

            th = self.cpu_threads or 6
            path = CHALLENGER_DIR / "whisper-base-hinglish-ct2-int8"
            self.obj = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=th)
            self.device = "cpu"
            self.compute_type = f"ct2_int8_t{th}"

        elif self.model_id == "ct2_oriserve_apex_int8":
            from faster_whisper import WhisperModel
            from voxlocal.engine.transcriber import setup_nvidia_dll_paths

            th = self.cpu_threads or 8
            path = CHALLENGER_DIR / "oriserve-apex-ct2-int8"
            dev = "cuda" if self.requested_device == "cuda" else "cpu"
            if dev == "cuda":
                setup_nvidia_dll_paths()
            ct = "int8"
            kw: Dict[str, Any] = dict(device=dev, compute_type=ct)
            if dev == "cpu":
                kw["cpu_threads"] = th
            self.obj = WhisperModel(str(path), **kw)
            self.device = dev
            self.compute_type = f"ct2_{ct}_{'gpu' if dev == 'cuda' else f't{th}'}"

        elif self.model_id in ("prod_whisper_turbo_balanced", "prod_whisper_small_balanced"):
            m_name = "large-v3-turbo" if self.model_id == "prod_whisper_turbo_balanced" else "small"
            th = self.cpu_threads or (8 if m_name == "large-v3-turbo" else 6)
            dev = "cpu" if self.requested_device == "auto" else self.requested_device
            tr = LocalWhisperTranscriber(
                models_dir=MODELS_DIR,
                model_size=m_name,
                compute_device=dev,
                compute_type="int8",
                cpu_threads=th,
                profile="balanced",
            )
            ok, msg = tr.ensure_loaded()
            if not ok:
                raise RuntimeError(msg)
            self.obj = tr
            prov = tr.get_active_asr_provenance()
            self.device = prov["asr_device"]
            self.compute_type = f"prod_{prov['asr_compute_type']}_t{th}"

        elif self.model_id in ("p0_whisper_small_cpu", "p0_whisper_turbo_cpu"):
            from faster_whisper import WhisperModel
            from voxlocal.engine.transcriber import setup_nvidia_dll_paths

            is_turbo = self.model_id == "p0_whisper_turbo_cpu"
            th = self.cpu_threads or (8 if is_turbo else 6)
            slug = "models--deepdml--faster-whisper-large-v3-turbo-ct2" if is_turbo else "models--Systran--faster-whisper-small"
            fb = "large-v3-turbo" if is_turbo else "small"
            path = _resolve_whisper_path(slug, fb)
            dev = "cuda" if self.requested_device == "cuda" else "cpu"
            if dev == "cuda":
                setup_nvidia_dll_paths()
            kw_p0: Dict[str, Any] = dict(device=dev, compute_type="int8")
            if dev == "cpu":
                kw_p0["cpu_threads"] = th
            self.obj = WhisperModel(path, **kw_p0)
            self.device = dev
            self.compute_type = f"ct2_int8_{'gpu' if dev == 'cuda' else f't{th}'}"

        elif self.model_id == "onnx_parakeet_tdt_v2_int8":
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
            self.device = "cpu"
            self.compute_type = f"onnx_int8_t{th}"

        elif self.model_id == "hf_qwen3_asr_06b_guarded":
            import torch

            th = self.cpu_threads or 8
            torch.set_num_threads(th)
            dev_arg = "auto" if self.requested_device == "auto" else self.requested_device
            tr = LocalHFASRTranscriber(model_id="Qwen/Qwen3-ASR-0.6B-hf", device=dev_arg)
            ok, msg = tr.ensure_loaded(local_only=True)
            if not ok:
                raise RuntimeError(msg)
            self.obj = tr
            self.device = tr._active_device
            self.compute_type = f"{tr._active_dtype}_t{th}"

        else:
            raise ValueError(f"Unsupported Phase 1.5 model_id: {self.model_id}")
        self.load_sec = round(time.perf_counter() - t0, 3)

    def unload(self) -> None:
        if hasattr(self.obj, "unload_model"):
            try:
                self.obj.unload_model()
            except Exception:
                pass
        self.obj = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _whisper_segs_stats(seg_list: List[Any]) -> Tuple[str, Optional[float], Optional[float]]:
        txt_parts: List[str] = []
        lp_num = 0.0
        lp_den = 0
        max_nsp: Optional[float] = None
        for s in seg_list:
            t = str(getattr(s, "text", "") or "").strip()
            if t:
                txt_parts.append(t)
                w_cnt = max(1, len(t.split()))
                lp = getattr(s, "avg_logprob", None)
                if lp is not None:
                    lp_num += float(lp) * w_cnt
                    lp_den += w_cnt
            nsp = getattr(s, "no_speech_prob", None)
            if nsp is not None:
                val = float(nsp)
                max_nsp = val if max_nsp is None else max(max_nsp, val)
        avg_lp = round(lp_num / lp_den, 4) if lp_den > 0 else None
        nsp_out = round(max_nsp, 4) if max_nsp is not None else None
        return " ".join(txt_parts).strip(), avg_lp, nsp_out

    def transcribe_array(self, arr_16k: np.ndarray, *, live_mode: bool = False) -> Dict[str, Any]:
        t0 = time.perf_counter()
        raw_text = ""
        roman_text = ""
        det_lang = ""
        lang_prob = 0.0
        lang_probs: Optional[Dict[str, float]] = None
        avg_logprob: Optional[float] = None
        min_token_prob: Optional[float] = None
        no_speech_prob: Optional[float] = None
        is_uncertain: Optional[bool] = None
        uncertainty_reasons: List[str] = []

        if arr_16k.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(arr_16k))) < 0.0004:
            return {
                "raw_text": "",
                "roman_text": "",
                "detected_language": "",
                "language_probability": 0.0,
                "lang_probs": None,
                "avg_logprob": None,
                "min_token_prob": None,
                "no_speech_prob": 1.0,
                "is_uncertain": False,
                "uncertainty_reasons": [],
                "infer_sec": round(time.perf_counter() - t0, 4),
            }

        if self.model_id in ("onnx_qwen3_asr_06b_int8_guarded", "onnx_qwen3_asr_06b_int8_guarded_hotwords"):
            res = self.obj.transcribe_array(arr_16k, hotwords=self._hotwords, max_new_tokens=self.max_new_tokens)
            res["no_speech_prob"] = None
            res["is_uncertain"] = None
            res["uncertainty_reasons"] = []
            return res

        if self.model_id in ("ct2_whisper_base_hinglish_int8", "ct2_oriserve_apex_int8"):
            norm = normalize_speech_audio(arr_16k)
            beam = 1 if live_mode else 2
            segs, info = self.obj.transcribe(
                norm,
                language=None,
                beam_size=beam,
                best_of=1 if live_mode else 2,
                temperature=0.0 if live_mode else (0.0, 0.2),
                condition_on_previous_text=False,
                vad_filter=False,
            )
            seg_list = list(segs)
            raw_text, avg_logprob, no_speech_prob = self._whisper_segs_stats(seg_list)
            raw_text, _ = sanitize_utf8_text(raw_text)
            raw_text = clean_repetition_tail(raw_text).strip()
            det_lang = str(getattr(info, "language", "") or "")
            lang_prob = round(float(getattr(info, "language_probability", 0.0) or 0.0), 4)

        elif self.model_id in ("prod_whisper_turbo_balanced", "prod_whisper_small_balanced"):
            segs = self.obj.transcribe_audio_segments(
                arr_16k,
                speaker_label="Speaker 1",
                source_track="system",
                time_offset_sec=0.0,
                initial_prompt="",
                language="en+hi",
                diarize_remote_turns=False,
                hotwords=None,
                output_script="roman",
                beam_size=1 if live_mode else 2,
                word_timestamps=False,
            )
            raw_parts = [str(s.get("raw_text") or "").strip() for s in segs if str(s.get("raw_text") or "").strip()]
            norm_parts = [str(s.get("text") or "").strip() for s in segs if str(s.get("text") or "").strip()]
            raw_text = " ".join(raw_parts).strip()
            roman_text = " ".join(norm_parts).strip()
            lps = [float(s["raw_model_score"]) for s in segs if s.get("raw_model_score") is not None]
            nsps = [float(s["no_speech_prob"]) for s in segs if s.get("no_speech_prob") is not None]
            avg_logprob = round(sum(lps) / len(lps), 4) if lps else None
            no_speech_prob = round(max(nsps), 4) if nsps else None
            langs = [str(s.get("detected_language") or "") for s in segs if s.get("detected_language")]
            det_lang = langs[0] if langs else ""
            is_uncertain = any(bool(s.get("is_uncertain")) for s in segs)
            reasons: List[str] = []
            for s in segs:
                for r in s.get("uncertainty_reasons") or []:
                    if r not in reasons:
                        reasons.append(str(r))
            uncertainty_reasons = reasons

        elif self.model_id in ("p0_whisper_small_cpu", "p0_whisper_turbo_cpu"):
            if self.model_id == "p0_whisper_turbo_cpu":
                segs, info = self.obj.transcribe(
                    arr_16k,
                    language=None,
                    beam_size=1 if live_mode else 3,
                    best_of=1 if live_mode else 2,
                    temperature=0.0 if live_mode else (0.0, 0.2),
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
            seg_list = list(segs)
            raw_text, avg_logprob, no_speech_prob = self._whisper_segs_stats(seg_list)
            det_lang = str(getattr(info, "language", "") or "")
            lang_prob = round(float(getattr(info, "language_probability", 0.0) or 0.0), 4)
            all_lp = getattr(info, "all_language_probs", None) or []
            lp_dict = {str(k): float(v) for k, v in all_lp}
            lang_probs = {
                "p_en": round(lp_dict.get("en", 0.0), 5),
                "p_hi": round(lp_dict.get("hi", 0.0), 5),
                "p_ur": round(lp_dict.get("ur", 0.0), 5),
            }

        elif self.model_id == "onnx_parakeet_tdt_v2_int8":
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

        elif self.model_id == "hf_qwen3_asr_06b_guarded":
            chunks = _split_long_audio(arr_16k, max_chunk_sec=22.0)
            parts = []
            langs_hf: List[str] = []
            for ch in chunks:
                txt, dl, _ = self.obj.decode_chunk(ch, hotwords=None, max_new_tokens=320)
                if txt:
                    parts.append(txt.strip())
                if dl:
                    langs_hf.append(dl)
            raw_text = " ".join(parts).strip()
            det_lang = langs_hf[0] if langs_hf else ""

        if not roman_text:
            roman_text = DictationProcessor.devanagari_to_roman_hinglish(raw_text).strip() if raw_text else ""

        infer_sec = round(time.perf_counter() - t0, 4)
        return {
            "raw_text": raw_text,
            "roman_text": roman_text,
            "detected_language": det_lang,
            "language_probability": lang_prob,
            "lang_probs": lang_probs,
            "avg_logprob": avg_logprob,
            "min_token_prob": min_token_prob,
            "no_speech_prob": no_speech_prob,
            "is_uncertain": is_uncertain,
            "uncertainty_reasons": uncertainty_reasons,
            "infer_sec": infer_sec,
        }


def _score_clip_15(track_name: str, clip: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
    raw_txt = str(out.get("raw_text") or "")
    roman_txt = str(out.get("roman_text") or "")
    dur = float(clip["duration_sec"])
    pred_toks = tokenize_hinglish_norm(roman_txt or raw_txt)
    wps = round(len(pred_toks) / max(0.05, dur), 3)
    rec: Dict[str, Any] = {
        "id": clip["id"],
        "track": track_name,
        "duration_sec": dur,
        "infer_sec": float(out["infer_sec"]),
        "rtf": round(float(out["infer_sec"]) / max(0.05, dur), 4),
        "detected_language": out.get("detected_language") or "",
        "language_probability": out.get("language_probability"),
        "lang_probs": out.get("lang_probs"),
        "avg_logprob": out.get("avg_logprob"),
        "min_token_prob": out.get("min_token_prob"),
        "no_speech_prob": out.get("no_speech_prob"),
        "is_uncertain": out.get("is_uncertain"),
        "uncertainty_reasons": out.get("uncertainty_reasons") or [],
        "words_per_sec": wps,
        "raw_text": raw_txt,
        "roman_text": roman_txt,
    }
    if track_name == "fleurs_hi_in":
        rec["reference_devanagari"] = clip["reference_devanagari"]
        rec["reference_roman"] = clip["reference_roman"]
        rec["align_devanagari"] = align_clip_details(clip["reference_devanagari"], raw_txt, mode="devanagari")
        rec["align_roman"] = align_clip_details(clip["reference_roman"], roman_txt, mode="hinglish")
        rec["align"] = rec["align_roman"]
    elif track_name == "coshe_hinglish":
        rec["reference_raw"] = clip.get("reference_raw", "")
        rec["reference_text"] = clip["reference_text"]
        rec["align"] = align_clip_details(clip["reference_text"], roman_txt, mode="hinglish")
        if clip.get("reference_raw"):
            rec["align_deva_mixed"] = align_clip_details(clip["reference_raw"], raw_txt, mode="devanagari")
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
        if roman_txt and roman_txt != raw_txt:
            rec["align_post"] = align_clip_details(clip["reference_text"], roman_txt, mode="strict")
    return rec


def _finalize_track_bundle_15(track_name: str, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if track_name == "fleurs_hi_in":
        metrics = aggregate_track_metrics(recs, align_key="align_roman")
        metrics["devanagari"] = aggregate_track_metrics(recs, align_key="align_devanagari")
    elif track_name == "coshe_hinglish":
        metrics = aggregate_track_metrics(recs, align_key="align")
        if recs and all("align_deva_mixed" in r for r in recs):
            metrics["devanagari_mixed"] = aggregate_track_metrics(recs, align_key="align_deva_mixed")
    elif track_name == "m241_scrum_omission":
        metrics = _aggregate_m241_probe(recs)
    else:
        metrics = aggregate_track_metrics(recs, align_key="align")
        if recs and any("align_post" in r for r in recs):
            post_recs = [dict(r, align=r.get("align_post", r["align"])) for r in recs]
            metrics["post_processed_wer"] = aggregate_track_metrics(post_recs, align_key="align")["wer"]
    return {"metrics": metrics, "clips": recs}


def _select_tracks_15(manifest: Dict[str, Any], scope: str) -> List[Tuple[str, List[Dict[str, Any]]]]:
    tr = manifest["tracks"]
    if scope == "full":
        return [(t, list(tr[t])) for t in ALL_TRACK_ORDER_15]
    if scope == "standard":
        return [(t, list(tr[t])) for t in CORE_EN_AND_NEW_TRACKS]
    if scope == "new3":
        return [(t, list(tr[t])) for t in NEW_3_TRACKS]
    if scope == "hotword_ab":
        return [(t, list(tr[t])) for t in ("earnings22_us", "ami_meetings_en", "m234_full_90s", "m235_strat30")]
    if scope == "m235_rest":
        return [("m235_verified_111", list(tr["m235_verified_111"]))]
    raise ValueError(f"Unsupported Phase 1.5 scope: {scope}")


def run_model_benchmark_15(
    model_id: str,
    scope: str,
    *,
    cpu_threads: Optional[int] = None,
    device: str = "auto",
    tracks_override: Optional[List[str]] = None,
    out_name: Optional[str] = None,
) -> int:
    db_before = verify_db_unchanged()
    manifest = json.loads(CORPUS15_MANIFEST.read_text(encoding="utf-8"))
    RESULTS15_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS15_DIR / f"{out_name or model_id}.json"

    existing: Dict[str, Any] = {}
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    elif model_id in ("p0_whisper_turbo_cpu", "p0_whisper_small_cpu", "onnx_parakeet_tdt_v2_int8"):
        p1_fallback = PHASE1_RESULTS_DIR / f"{model_id}.json"
        if p1_fallback.exists():
            existing = json.loads(p1_fallback.read_text(encoding="utf-8"))
            print(f"[{model_id}] Seeded {len(existing.get('tracks') or {})} prior tracks from Phase 1 JSON.", flush=True)

    vram_before = query_gpu_vram()
    rss_before = get_process_rss_mb()

    runner = Phase15ModelRunner(model_id, cpu_threads=cpu_threads, device=device)
    runner.load()
    vram_after_load = query_gpu_vram()
    peak_rss = max(rss_before, get_process_rss_mb())
    vram_peak = max(int(vram_before.get("used_mb") or 0), int(vram_after_load.get("used_mb") or 0))

    tracks_out: Dict[str, Any] = dict(existing.get("tracks") or {})
    cached_by_id: Dict[str, Dict[str, Any]] = {}
    for tb in tracks_out.values():
        for cr in tb.get("clips") or []:
            cached_by_id[cr["id"]] = cr

    eff_scope = f"tracks({','.join(tracks_override)})" if tracks_override else scope

    def _save_payload(official_meetings: Optional[Dict[str, Any]] = None) -> None:
        nonlocal peak_rss, vram_peak
        peak_rss = max(peak_rss, get_process_rss_mb())
        vq = query_gpu_vram()
        vram_peak = max(vram_peak, int(vq.get("used_mb") or 0))
        payload = {
            "model_id": out_name or model_id,
            "base_model_id": model_id,
            "spec": MODEL_SPECS_15[model_id],
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
        else _select_tracks_15(manifest, scope)
    )

    for track_name, clips in track_jobs:
        print(f"[{model_id}] Track '{track_name}' ({len(clips)} clips)...", flush=True)
        recs: List[Dict[str, Any]] = []
        reused = 0
        for i, clip in enumerate(clips, 1):
            cid = clip["id"]
            if (scope == "m235_rest" or track_name == "m235_verified_111") and cid in cached_by_id:
                recs.append(cached_by_id[cid])
                reused += 1
                continue
            arr = load_wav_as_16k_f32(clip["wav_path"])
            out = runner.transcribe_array(arr)
            rec = _score_clip_15(track_name, clip, out)
            recs.append(rec)
            cached_by_id[cid] = rec
            if i % 15 == 0 or i == len(clips):
                peak_rss = max(peak_rss, get_process_rss_mb())
                print(f"  progress {i}/{len(clips)} (RSS={peak_rss:.0f} MB, cached={reused})", flush=True)
            if track_name == "m235_verified_111" and i % 25 == 0:
                tracks_out[track_name] = _finalize_track_bundle_15(track_name, recs)
                _save_payload()

        tracks_out[track_name] = _finalize_track_bundle_15(track_name, recs)
        if track_name == "m235_verified_111" and len(recs) == len(manifest["tracks"]["m235_verified_111"]):
            strat_ids = {c["id"] for c in manifest["tracks"]["m235_strat30"]}
            tracks_out["m235_strat30"] = _finalize_track_bundle_15("m235_strat30", [r for r in recs if r["id"] in strat_ids])

        m = tracks_out[track_name]["metrics"]
        if "wer" in m:
            extra = f" | post_wer={m['post_processed_wer']:.4f}" if "post_processed_wer" in m else ""
            if "devanagari" in m:
                extra += f" | dev_wer={m['devanagari']['wer']:.4f}"
            print(
                f"  -> {track_name}: WER={m['wer']:.4f} del={m['del_rate']:.4f} "
                f"ret={m['word_retention_ratio']:.3f} omit={m['omitted_clip_pct']}% "
                f"CJK={m['script_purity']['cjk_segs']} RTF={m['rtf']:.3f}{extra}",
                flush=True,
            )
        else:
            print(f"  -> {track_name}: hi_to_en_wps_ratio={m.get('hinglish_to_english_wps_ratio')} RTF={m['rtf']:.3f}", flush=True)
        _save_payload()

    official: Dict[str, Any] = dict(existing.get("official_meetings") or {})
    if "m234_full_90s" in tracks_out:
        sc = _score_official_meeting(Path(manifest["full_meetings"]["m234"]["ref_path"]), tracks_out["m234_full_90s"]["clips"])
        if sc:
            official["m234"] = sc
    if "m235_verified_111" in tracks_out:
        m235_all = tracks_out["m235_verified_111"].get("clips") or []
        if len(m235_all) == len(manifest["tracks"]["m235_verified_111"]):
            sc2 = _score_official_meeting(Path(manifest["full_meetings"]["m235"]["ref_path"]), m235_all)
            if sc2:
                official["m235"] = sc2

    _save_payload(official_meetings=official)
    runner.unload()
    vram_after_unload = query_gpu_vram()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    payload["vram_after_unload_mb"] = vram_after_unload.get("used_mb")
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    verify_db_unchanged()
    print(f"[DONE] Saved {out_path}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Step 5: Human-Verifiable M#240 Reference Draft & Rescorer
# ---------------------------------------------------------------------------

def _simple_word_wer(a: str, b: str) -> float:
    ta = tokenize_hinglish_norm(a)
    tb = tokenize_hinglish_norm(b)
    if not ta and not tb:
        return 0.0
    if not ta or not tb:
        return 1.0
    from score_ground_truth import levenshtein_distance
    return round(levenshtein_distance(ta, tb) / float(max(len(ta), len(tb))), 4)


def run_m240_draft() -> int:
    db_before = verify_db_unchanged()
    manifest = json.loads(CORPUS15_MANIFEST.read_text(encoding="utf-8"))
    m240_meta = manifest["continuous_streams"]["m240_us_in_call_5m"]
    slice_wav = Path(m240_meta["wav_path"])
    base_offset_sec = float(m240_meta["start_time"])  # 1140.0

    # Verify alignment with raw recording WAV
    rec_sys = ROOT_DIR / "data" / "recordings" / "meeting_20261119_223231" / "system_track.wav"
    rec_raw = ROOT_DIR / "data" / "recordings" / "meeting_20261119_223231" / "system_track_raw.wav"
    if not rec_sys.exists() or not rec_raw.exists():
        raise FileNotFoundError("Missing M#240 source WAVs in meeting_20261119_223231")

    arr_slice = load_wav_as_16k_f32(str(slice_wav))
    spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
        arr_slice,
        dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250),
        max_chunk_sec=15.0,
        merge_gap_sec=0.50,
        pad_sec=0.20,
    ) or [(0, arr_slice.size)]

    clean_spans: List[Tuple[int, float, float, np.ndarray]] = []
    for s_i, e_i in spans:
        sl = arr_slice[s_i:e_i]
        if sl.size < int(0.30 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
            continue
        s_local = s_i / float(TARGET_SAMPLE_RATE)
        e_local = e_i / float(TARGET_SAMPLE_RATE)
        clean_spans.append((len(clean_spans), s_local, e_local, sl))

    print(f"[M240 Draft] Extracted {len(clean_spans)} VAD segments across 300s (abs {base_offset_sec:.0f}..{base_offset_sec+300:.0f}s)", flush=True)

    candidate_models = [
        "prod_whisper_turbo_balanced",
        "onnx_qwen3_asr_06b_int8_guarded",
        "onnx_parakeet_tdt_v2_int8",
        "ct2_oriserve_apex_int8",
        "ct2_whisper_base_hinglish_int8",
    ]
    all_preds: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for mid in candidate_models:
        v_free = query_gpu_vram().get("free_mb") or 0
        use_dev = "cuda" if (mid in ("prod_whisper_turbo_balanced", "ct2_oriserve_apex_int8") and v_free >= 1100) else "cpu"
        print(f"  [M240] Transcribing {len(clean_spans)} spans with {mid} (device={use_dev})...", flush=True)
        runner = Phase15ModelRunner(mid, device=use_dev)
        runner.load()
        per_seg: Dict[int, Dict[str, Any]] = {}
        for idx, s_loc, e_loc, sl in clean_spans:
            out = runner.transcribe_array(sl)
            per_seg[idx] = {
                "raw_text": out["raw_text"],
                "roman_text": out["roman_text"],
                "detected_language": out["detected_language"],
                "avg_logprob": out["avg_logprob"],
                "infer_sec": out["infer_sec"],
            }
        runner.unload()
        all_preds[mid] = per_seg

    segments: List[Dict[str, Any]] = []
    high_disagreement = 0
    for idx, s_loc, e_loc, _ in clean_spans:
        hyps = {mid: all_preds[mid][idx]["roman_text"] or all_preds[mid][idx]["raw_text"] for mid in candidate_models}
        turbo_h = hyps["prod_whisper_turbo_balanced"]
        qwen_h = hyps["onnx_qwen3_asr_06b_int8_guarded"]
        parakeet_h = hyps["onnx_parakeet_tdt_v2_int8"]
        disagreement = _simple_word_wer(turbo_h, qwen_h)
        needs_review = disagreement > 0.25 or not (turbo_h or qwen_h)
        if needs_review:
            high_disagreement += 1
        draft_ref = turbo_h or qwen_h or parakeet_h
        abs_s = round(base_offset_sec + s_loc, 2)
        abs_e = round(base_offset_sec + e_loc, 2)
        segments.append({
            "segment_index": idx,
            "start_time": abs_s,
            "end_time": abs_e,
            "local_start_sec": round(s_loc, 2),
            "local_end_sec": round(e_loc, 2),
            "source_track": "system",
            "speaker": "Speaker 1",
            "speaker_cluster": "Speaker 1",
            "confidence": 0.0,
            "raw_text": all_preds["prod_whisper_turbo_balanced"][idx]["raw_text"],
            "display_text": draft_ref,
            "baseline_db_text": "",
            "reference_text": draft_ref,
            "verified_by_human": False,
            "audio_listened": False,
            "verified_at_utc": "",
            "language_tags": [],
            "uncertain": False,
            "needs_careful_review": needs_review,
            "inter_model_wer_turbo_vs_qwen": disagreement,
            "candidate_hypotheses": hyps,
            "notes": f"Disagreement={disagreement:.2f}" if needs_review else "",
        })

    draft_doc = {
        "meeting_id": 240,
        "title": "US + Indian PLM/WFX Call (5m Slice 19:00-24:00)",
        "duration_sec": 300.0,
        "abs_window_start_sec": base_offset_sec,
        "abs_window_end_sec": base_offset_sec + 300.0,
        "review_status": "pending_human_verification",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instructions": (
            "UNVERIFIED AI DRAFT. Listen to each segment in scripts/review_ground_truth.py, "
            "correct `reference_text`, set `audio_listened=True`, and verify before scoring."
        ),
        "segments": segments,
    }
    M240_DRAFT_PATH.write_text(json.dumps(draft_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    RESULTS15_DIR.mkdir(parents=True, exist_ok=True)
    cand_doc = {
        "meeting_id": 240,
        "db_sha256": db_before,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "segment_count": len(segments),
        "high_disagreement_count": high_disagreement,
        "segments": [
            {
                "segment_index": seg["segment_index"],
                "start_time": seg["start_time"],
                "end_time": seg["end_time"],
                "source_track": "system",
                "speaker": "Speaker 1",
                "predictions": {mid: all_preds[mid][seg["segment_index"]] for mid in candidate_models},
            }
            for seg in segments
        ],
    }
    M240_CANDIDATES_PATH.write_text(json.dumps(cand_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] M#240 draft: {M240_DRAFT_PATH} ({len(segments)} segs, {high_disagreement} high-disagreement)", flush=True)
    return 0


def run_rescore_m240() -> int:
    verify_db_unchanged()
    if not M240_DRAFT_PATH.exists() or not M240_CANDIDATES_PATH.exists():
        raise FileNotFoundError("Run --mode m240_draft first.")
    ref_doc = json.loads(M240_DRAFT_PATH.read_text(encoding="utf-8"))
    verified_segs = [s for s in ref_doc.get("segments", []) if s.get("verified_by_human") is True]
    if not verified_segs:
        print("[Rescore M#240] awaiting_human_verification: 0 segments verified in M240_us_in_5m_reference_draft.json", flush=True)
        return 0
    cand_doc = json.loads(M240_CANDIDATES_PATH.read_text(encoding="utf-8"))
    model_ids = sorted(cand_doc["segments"][0]["predictions"].keys()) if cand_doc["segments"] else []
    out_scores: Dict[str, Any] = {}
    for mid in model_ids:
        pred_segs = []
        for s in cand_doc["segments"]:
            p = s["predictions"][mid]
            pred_segs.append({
                "start_time": float(s["start_time"]),
                "end_time": float(s["end_time"]),
                "start": float(s["start_time"]),
                "end": float(s["end_time"]),
                "source_track": "system",
                "speaker": "Speaker 1",
                "raw_text": p["raw_text"],
                "text": p["roman_text"],
            })
        sc = _score_official_meeting.__wrapped__(M240_DRAFT_PATH, []) if False else None
        from score_ground_truth import score_predictions_against_reference
        sc = score_predictions_against_reference(ref_doc, pred_segs)
        out_scores[mid] = sc
        print(f"  {mid:32s}: verified_WER={sc.get('stream_hinglish_norm_wer')} (verified={len(verified_segs)})", flush=True)
    res_path = RESULTS15_DIR / "m240_verified_scores.json"
    res_path.write_text(json.dumps(out_scores, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# Step 6: Fixed Shared-Queue Dual-Track & True LocalAgreement-2 Streaming
# ---------------------------------------------------------------------------

def _extract_stream_spans(wav_paths: List[Tuple[str, str, str]], max_chunk_sec: float = 14.0) -> Tuple[List[Dict[str, Any]], float]:
    all_spans: List[Dict[str, Any]] = []
    total_dur = 0.0
    uid = 0
    for track_label, speaker_label, wpath in wav_paths:
        arr = load_wav_as_16k_f32(wpath)
        dur = float(arr.size) / float(TARGET_SAMPLE_RATE)
        total_dur = max(total_dur, dur)
        spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
            arr,
            dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=200),
            max_chunk_sec=max_chunk_sec,
            merge_gap_sec=0.45,
            pad_sec=0.15,
        ) or [(0, arr.size)]
        for s_i, e_i in spans:
            sl = arr[s_i:e_i]
            if sl.size < int(0.30 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue
            uid += 1
            all_spans.append({
                "uid": uid,
                "track_label": track_label,
                "speaker_label": speaker_label,
                "s_sec": float(s_i) / float(TARGET_SAMPLE_RATE),
                "e_sec": float(e_i) / float(TARGET_SAMPLE_RATE),
                "sl": sl,
            })
    all_spans.sort(key=lambda x: (x["s_sec"], x["e_sec"]))
    return all_spans, total_dur


def _sim_frame_560ms_shared_queue(wav_paths: List[Tuple[str, str, str]], runner: Phase15ModelRunner) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fixed shared dual-track chronological queue for 560ms streaming transducer (Nemotron)."""
    spans, total_audio_sec = _extract_stream_spans(wav_paths, max_chunk_sec=20.0)
    frame_n = int(0.56 * TARGET_SAMPLE_RATE)
    silence_pad = np.zeros(int(1.0 * TARGET_SAMPLE_RATE), dtype=np.float32)

    events: List[Tuple[float, int, int, bool, np.ndarray]] = []
    states: Dict[int, Dict[str, Any]] = {}
    for sp in spans:
        uid = sp["uid"]
        padded = np.concatenate([sp["sl"], silence_pad])
        states[uid] = {
            **sp,
            "stream": runner.obj.create_stream(),
            "prev_txt": "",
            "first_word_lat": None,
            "revisions": 0,
            "utt_infer": 0.0,
        }
        n_frames = (padded.size + frame_n - 1) // frame_n
        for fi in range(n_frames):
            off = fi * frame_n
            piece = padded[off : off + frame_n]
            arrive = min(sp["e_sec"] + 1.0, sp["s_sec"] + (off + piece.size) / float(TARGET_SAMPLE_RATE))
            events.append((arrive, uid, fi, fi == (n_frames - 1), piece))

    # Strict chronological order across ALL tracks sharing ONE worker!
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    worker_free_at = 0.0
    backlogs: List[float] = []
    first_lats: List[float] = []
    commit_lats: List[float] = []
    step_ms: List[float] = []
    would_drop = 0
    total_infer = 0.0
    segs: List[Dict[str, Any]] = []

    for arrive_sec, uid, _fi, is_last, piece in events:
        st_info = states[uid]
        backlog = max(0.0, worker_free_at - arrive_sec)
        if backlog > 15.0:
            would_drop += 1
        backlogs.append(backlog)

        ft0 = time.perf_counter()
        st = st_info["stream"]
        st.accept_waveform(TARGET_SAMPLE_RATE, piece)
        while runner.obj.is_ready(st):
            runner.obj.decode_stream(st)
        if is_last:
            st.input_finished()
            while runner.obj.is_ready(st):
                runner.obj.decode_stream(st)
        fdt = time.perf_counter() - ft0
        step_ms.append(fdt * 1000.0)
        total_infer += fdt
        st_info["utt_infer"] += fdt
        finish_sec = max(arrive_sec, worker_free_at) + fdt
        worker_free_at = finish_sec

        cur_txt = str(runner.obj.get_result(st) or "").strip()
        if cur_txt and st_info["first_word_lat"] is None:
            st_info["first_word_lat"] = max(0.0, finish_sec - st_info["s_sec"])
            first_lats.append(st_info["first_word_lat"])
        if cur_txt and cur_txt != st_info["prev_txt"]:
            if st_info["prev_txt"]:
                st_info["revisions"] += 1
            st_info["prev_txt"] = cur_txt

        if is_last:
            clat = max(0.0, finish_sec - st_info["e_sec"])
            commit_lats.append(clat)
            final_txt = st_info["prev_txt"]
            if final_txt:
                segs.append({
                    "start_time": round(st_info["s_sec"], 2),
                    "end_time": round(st_info["e_sec"], 2),
                    "start": round(st_info["s_sec"], 2),
                    "end": round(st_info["e_sec"], 2),
                    "source_track": st_info["track_label"],
                    "speaker": st_info["speaker_label"],
                    "raw_text": final_txt,
                    "text": final_txt,
                    "first_word_latency_sec": round(st_info["first_word_lat"] or clat, 3),
                    "commit_latency_sec": round(clat, 3),
                })

    segs.sort(key=lambda s: (s["start_time"], s["end_time"]))
    total_revs = sum(st_info["revisions"] for st_info in states.values())
    end_drain_lag = max(0.0, worker_free_at - total_audio_sec)
    summary = {
        "streaming_type": "frame_incremental_shared_queue",
        "is_word_by_word_incremental": True,
        "audio_duration_sec": round(total_audio_sec, 2),
        "chunks_processed": len(events),
        "emitted_segments": len(segs),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.01, total_audio_sec), 4),
        "p50_first_word_latency_sec": round(float(np.percentile(first_lats, 50)), 3) if first_lats else 0.0,
        "p95_first_word_latency_sec": round(float(np.percentile(first_lats, 95)), 3) if first_lats else 0.0,
        "p50_commit_latency_sec": round(float(np.percentile(commit_lats, 50)), 3) if commit_lats else 0.0,
        "p95_commit_latency_sec": round(float(np.percentile(commit_lats, 95)), 3) if commit_lats else 0.0,
        "p50_step_ms": round(float(np.percentile(step_ms, 50)), 1) if step_ms else 0.0,
        "p95_step_ms": round(float(np.percentile(step_ms, 95)), 1) if step_ms else 0.0,
        "draft_revisions_per_utt": round(total_revs / max(1, len(states)), 2),
        "max_shared_queue_backlog_sec": round(max(backlogs), 3) if backlogs else 0.0,
        "end_drain_lag_sec": round(end_drain_lag, 3),
        "would_drop_over_15s_chunks": would_drop,
        "full_text_roman": " ".join(s["text"] for s in segs).strip(),
        "full_text_raw": " ".join(s["raw_text"] for s in segs).strip(),
    }
    return summary, segs


def _sim_local_agreement_2_shared_queue(
    wav_paths: List[Tuple[str, str, str]],
    runner: Phase15ModelRunner,
    *,
    step_sec: float = 1.2,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Genuine LocalAgreement-2 word-by-word incremental policy (WhisperLiveKit style)
    across a single chronological worker queue shared by mic + system tracks.
    """
    spans, total_audio_sec = _extract_stream_spans(wav_paths, max_chunk_sec=14.0)
    step_samp = int(round(step_sec * TARGET_SAMPLE_RATE))

    events: List[Tuple[float, int, int, bool, int]] = []
    states: Dict[int, Dict[str, Any]] = {}
    for sp in spans:
        uid = sp["uid"]
        n_samps = int(sp["sl"].size)
        states[uid] = {
            **sp,
            "committed_raw": [],
            "committed_norm": [],
            "prev_hyp_norm": [],
            "prev_draft_norm": [],
            "first_word_lat": None,
            "draft_revisions": 0,
            "flicker_words": 0,
            "commit_events": [],
        }
        ticks: List[int] = list(range(step_samp, n_samps, step_samp))
        if not ticks or ticks[-1] != n_samps:
            ticks.append(n_samps)
        for ti, end_sample in enumerate(ticks):
            arrive = sp["s_sec"] + end_sample / float(TARGET_SAMPLE_RATE)
            events.append((arrive, uid, ti, ti == (len(ticks) - 1), end_sample))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    worker_free_at = 0.0
    backlogs: List[float] = []
    first_lats: List[float] = []
    word_commit_lats: List[float] = []
    final_commit_lats: List[float] = []
    total_infer = 0.0
    segs: List[Dict[str, Any]] = []

    for arrive_sec, uid, _ti, is_last, end_sample in events:
        st = states[uid]
        backlog = max(0.0, worker_free_at - arrive_sec)
        if backlog > 15.0:
            pass
        backlogs.append(backlog)

        sl_prefix = st["sl"][:end_sample]
        out = runner.transcribe_array(sl_prefix, live_mode=True)
        inf_s = float(out["infer_sec"])
        total_infer += inf_s
        finish_sec = max(arrive_sec, worker_free_at) + inf_s
        worker_free_at = finish_sec

        hyp_txt_roman = out["roman_text"] or out["raw_text"]
        hyp_norm = hyp_txt_roman.split()
        hyp_raw = (out["raw_text"] or "").split()

        if hyp_norm and st["first_word_lat"] is None:
            st["first_word_lat"] = max(0.0, finish_sec - st["s_sec"])
            first_lats.append(st["first_word_lat"])

        already_k = len(st["committed_norm"])
        prev_n = st["prev_hyp_norm"]
        # Longest matching normalized prefix vs previous hypothesis (LocalAgreement-2)
        lcp = 0
        lim = min(len(prev_n), len(hyp_norm))
        while lcp < lim and prev_n[lcp].lower().strip(".,!?") == hyp_norm[lcp].lower().strip(".,!?"):
            lcp += 1

        if is_last:
            # Final endpoint flush commits full remaining hypothesis
            if len(hyp_norm) > already_k:
                new_words_n = len(hyp_norm) - already_k
                word_commit_lats.extend([max(0.0, finish_sec - arrive_sec)] * new_words_n)
            st["committed_norm"] = hyp_norm
            st["committed_raw"] = hyp_raw
            final_lat = max(0.0, finish_sec - st["e_sec"])
            final_commit_lats.append(final_lat)
            final_roman = " ".join(st["committed_norm"]).strip()
            final_raw = " ".join(st["committed_raw"]).strip() or final_roman
            if final_roman or final_raw:
                segs.append({
                    "start_time": round(st["s_sec"], 2),
                    "end_time": round(st["e_sec"], 2),
                    "start": round(st["s_sec"], 2),
                    "end": round(st["e_sec"], 2),
                    "source_track": st["track_label"],
                    "speaker": st["speaker_label"],
                    "raw_text": final_raw,
                    "text": final_roman or final_raw,
                    "first_word_latency_sec": round(st["first_word_lat"] or final_lat, 3),
                    "commit_latency_sec": round(final_lat, 3),
                })
        else:
            if lcp > already_k:
                st["committed_norm"] = hyp_norm[:lcp]
                st["committed_raw"] = hyp_raw[:lcp]
                st["commit_events"].append((finish_sec, lcp - already_k))
                word_commit_lats.extend([max(0.0, finish_sec - arrive_sec)] * (lcp - already_k))
            draft_now = hyp_norm[len(st["committed_norm"]) :]
            if st["prev_draft_norm"] and draft_now != st["prev_draft_norm"]:
                st["draft_revisions"] += 1
                diff_w = abs(len(draft_now) - len(st["prev_draft_norm"])) + sum(
                    1 for a, b in zip(draft_now, st["prev_draft_norm"]) if a != b
                )
                st["flicker_words"] += diff_w
            st["prev_draft_norm"] = draft_now
            st["prev_hyp_norm"] = hyp_norm

    segs.sort(key=lambda s: (s["start_time"], s["end_time"]))
    would_drop = sum(1 for b in backlogs if b > 15.0)
    total_revs = sum(s["draft_revisions"] for s in states.values())
    total_flick = sum(s["flicker_words"] for s in states.values())
    end_drain_lag = max(0.0, worker_free_at - total_audio_sec)
    summary = {
        "streaming_type": f"local_agreement_2_step_{step_sec}s_shared_queue",
        "is_word_by_word_incremental": True,
        "audio_duration_sec": round(total_audio_sec, 2),
        "chunks_processed": len(events),
        "emitted_segments": len(segs),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.01, total_audio_sec), 4),
        "p50_first_word_latency_sec": round(float(np.percentile(first_lats, 50)), 3) if first_lats else 0.0,
        "p95_first_word_latency_sec": round(float(np.percentile(first_lats, 95)), 3) if first_lats else 0.0,
        "p50_intermediate_commit_lat_sec": round(float(np.percentile(word_commit_lats, 50)), 3) if word_commit_lats else 0.0,
        "p95_intermediate_commit_lat_sec": round(float(np.percentile(word_commit_lats, 95)), 3) if word_commit_lats else 0.0,
        "p50_commit_latency_sec": round(float(np.percentile(final_commit_lats, 50)), 3) if final_commit_lats else 0.0,
        "p95_commit_latency_sec": round(float(np.percentile(final_commit_lats, 95)), 3) if final_commit_lats else 0.0,
        "draft_revisions_per_utt": round(total_revs / max(1, len(states)), 2),
        "flicker_words_per_utt": round(total_flick / max(1, len(states)), 2),
        "max_shared_queue_backlog_sec": round(max(backlogs), 3) if backlogs else 0.0,
        "end_drain_lag_sec": round(end_drain_lag, 3),
        "would_drop_over_15s_chunks": would_drop,
        "full_text_roman": " ".join(s["text"] for s in segs).strip(),
        "full_text_raw": " ".join(s["raw_text"] for s in segs).strip(),
    }
    return summary, segs


def _sim_vad_endpointed_shared_queue(wav_paths: List[Tuple[str, str, str]], runner: Phase15ModelRunner) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    spans, total_audio_sec = _extract_stream_spans(wav_paths, max_chunk_sec=15.0)
    # Sort chronologically by utterance END (when VAD endpoint triggers)
    spans.sort(key=lambda sp: (sp["e_sec"], sp["s_sec"]))

    worker_free_at = 0.0
    backlogs: List[float] = []
    commit_lats: List[float] = []
    would_drop = 0
    total_infer = 0.0
    segs: List[Dict[str, Any]] = []

    for sp in spans:
        backlog = max(0.0, worker_free_at - sp["e_sec"])
        if backlog > 15.0:
            would_drop += 1
        backlogs.append(backlog)
        out = runner.transcribe_array(sp["sl"], live_mode=True)
        inf_s = float(out["infer_sec"])
        total_infer += inf_s
        commit_at = max(sp["e_sec"], worker_free_at) + inf_s
        worker_free_at = commit_at
        clat = commit_at - sp["e_sec"]
        commit_lats.append(clat)
        if out["raw_text"] or out["roman_text"]:
            segs.append({
                "start_time": round(sp["s_sec"], 2),
                "end_time": round(sp["e_sec"], 2),
                "start": round(sp["s_sec"], 2),
                "end": round(sp["e_sec"], 2),
                "source_track": sp["track_label"],
                "speaker": sp["speaker_label"],
                "raw_text": out["raw_text"],
                "text": out["roman_text"] or out["raw_text"],
                "first_word_latency_sec": round(clat, 3),
                "commit_latency_sec": round(clat, 3),
            })

    segs.sort(key=lambda s: (s["start_time"], s["end_time"]))
    end_drain_lag = max(0.0, worker_free_at - total_audio_sec)
    summary = {
        "streaming_type": "vad_endpointed_non_incremental",
        "is_word_by_word_incremental": False,
        "audio_duration_sec": round(total_audio_sec, 2),
        "chunks_processed": len(spans),
        "emitted_segments": len(segs),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.01, total_audio_sec), 4),
        "p50_first_word_latency_sec": round(float(np.percentile(commit_lats, 50)), 3) if commit_lats else 0.0,
        "p95_first_word_latency_sec": round(float(np.percentile(commit_lats, 95)), 3) if commit_lats else 0.0,
        "p50_commit_latency_sec": round(float(np.percentile(commit_lats, 50)), 3) if commit_lats else 0.0,
        "p95_commit_latency_sec": round(float(np.percentile(commit_lats, 95)), 3) if commit_lats else 0.0,
        "draft_revisions_per_utt": 0.0,
        "flicker_words_per_utt": 0.0,
        "max_shared_queue_backlog_sec": round(max(backlogs), 3) if backlogs else 0.0,
        "end_drain_lag_sec": round(end_drain_lag, 3),
        "would_drop_over_15s_chunks": would_drop,
        "full_text_roman": " ".join(s["text"] for s in segs).strip(),
        "full_text_raw": " ".join(s["raw_text"] for s in segs).strip(),
    }
    return summary, segs


def run_streaming_v2(engine_filter: Optional[List[str]] = None) -> int:
    db_before = verify_db_unchanged()
    manifest = json.loads(CORPUS15_MANIFEST.read_text(encoding="utf-8"))
    RESULTS15_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS15_DIR / "stream_v2_results.json"

    e22_cont = manifest["continuous_streams"]["earnings22_us_continuous"]
    ami_cont = manifest["continuous_streams"]["ami_meetings_continuous"]
    m240_cont = manifest["continuous_streams"]["m240_us_in_call_5m"]
    m234_meta = manifest["full_meetings"]["m234"]
    m234_ref = json.loads(Path(m234_meta["ref_path"]).read_text(encoding="utf-8"))

    specs = [
        # (stream_engine_id, base_model_id, policy, step_sec)
        ("nemotron_560ms_shared_q", "onnx_nemotron_stream_560ms_int8", "frame_560", 0.56),
        ("la2_whisper_small_cpu", "p0_whisper_small_cpu", "la2", 1.2),
        ("la2_whisper_base_hinglish_cpu", "ct2_whisper_base_hinglish_int8", "la2", 1.2),
        ("la2_qwen3_onnx_guarded_cpu", "onnx_qwen3_asr_06b_int8_guarded", "la2", 1.5),
        ("la2_oriserve_apex_cpu", "ct2_oriserve_apex_int8", "la2", 1.5),
        ("vad_parakeet_tdt_v2_cpu", "onnx_parakeet_tdt_v2_int8", "vad", 0.0),
        ("vad_qwen3_onnx_guarded_cpu", "onnx_qwen3_asr_06b_int8_guarded", "vad", 0.0),
        ("vad_oriserve_apex_cpu", "ct2_oriserve_apex_int8", "vad", 0.0),
    ]
    if engine_filter:
        specs = [s for s in specs if s[0] in engine_filter]

    out_doc: Dict[str, Any] = {"db_sha256": db_before, "engines": {}}
    if out_path.exists():
        old = json.loads(out_path.read_text(encoding="utf-8"))
        out_doc["engines"] = dict(old.get("engines") or {})

    from score_ground_truth import score_predictions_against_reference

    for eng_id, base_mid, pol, step_s in specs:
        print(f"\n[Stream v2] {eng_id} (base={base_mid}, policy={pol})...", flush=True)
        rss0 = get_process_rss_mb()
        if base_mid == "onnx_nemotron_stream_560ms_int8":
            import sherpa_onnx
            d = CHALLENGER_DIR / "sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8"
            runner = Phase15ModelRunner("onnx_parakeet_tdt_v2_int8")  # dummy holder
            t0 = time.perf_counter()
            runner.obj = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(d / "tokens.txt"),
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                model_type="nemo_transducer",
                num_threads=4,
                feature_dim=128,
                normalize_samples=True,
                provider="cpu",
            )
            runner.load_sec = round(time.perf_counter() - t0, 3)
            sim_fn = _sim_frame_560ms_shared_queue
        else:
            runner = Phase15ModelRunner(base_mid, max_new_tokens=160 if pol == "la2" else 256)
            runner.load()
            if pol == "la2":
                sim_fn = lambda wps, r: _sim_local_agreement_2_shared_queue(wps, r, step_sec=step_s)
            else:
                sim_fn = _sim_vad_endpointed_shared_queue

        run_full_streams = eng_id not in ("la2_oriserve_apex_cpu", "vad_oriserve_apex_cpu")
        e22_sum: Optional[Dict[str, Any]] = None
        if run_full_streams:
            # 1. earnings22_us_continuous
            e22_sum, _ = sim_fn([("system", "Speaker 1", e22_cont["wav_path"])], runner)
            e22_al = align_clip_details(e22_cont["reference_text"], e22_sum["full_text_raw"], mode="strict")
            e22_sum["wer"] = round(e22_al["edits"] / max(1, e22_al["ref_words"]), 4)
            e22_sum["del_rate"] = e22_al["del_rate"]
            e22_sum["word_retention_ratio"] = e22_al["word_retention_ratio"]
            print(f"  e22_cont : WER={e22_sum['wer']:.4f} RTF={e22_sum['rtf']:.3f} P50_1stW={e22_sum['p50_first_word_latency_sec']}s P95_commit={e22_sum['p95_commit_latency_sec']}s max_bl={e22_sum['max_shared_queue_backlog_sec']}s", flush=True)

        # 2. ami_meetings_continuous
        ami_sum, _ = sim_fn([("system", "Speaker 1", ami_cont["wav_path"])], runner)
        ami_al = align_clip_details(ami_cont["reference_text"], ami_sum["full_text_raw"], mode="strict")
        ami_sum["wer"] = round(ami_al["edits"] / max(1, ami_al["ref_words"]), 4)
        ami_sum["del_rate"] = ami_al["del_rate"]
        ami_sum["word_retention_ratio"] = ami_al["word_retention_ratio"]
        print(f"  ami_cont : WER={ami_sum['wer']:.4f} RTF={ami_sum['rtf']:.3f} P50_1stW={ami_sum['p50_first_word_latency_sec']}s P95_commit={ami_sum['p95_commit_latency_sec']}s max_bl={ami_sum['max_shared_queue_backlog_sec']}s", flush=True)

        m240_sum: Optional[Dict[str, Any]] = None
        if run_full_streams:
            # 3. m240_us_in_call_5m
            m240_sum, _ = sim_fn([("system", "Speaker 1", m240_cont["wav_path"])], runner)
            m240_words = tokenize_strict(m240_sum["full_text_raw"])
            m240_sum["total_words"] = len(m240_words)
            m240_sum["words_per_min"] = round(len(m240_words) / max(0.1, m240_sum["audio_duration_sec"] / 60.0), 1)
            m240_sum["duplication_words"] = _count_duplication_words(m240_sum["full_text_raw"].split())
            m240_sum["script_purity"] = audit_mixed_script_and_utf8([{"text": m240_sum["full_text_raw"]}])
            print(f"  m240_5m  : words={len(m240_words)} ({m240_sum['words_per_min']}/m) RTF={m240_sum['rtf']:.3f} max_bl={m240_sum['max_shared_queue_backlog_sec']}s", flush=True)

        # 4. M#234 dual-track continuous (SHARED QUEUE!)
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
        m234_sum["script_purity"] = audit_mixed_script_and_utf8([{"text": m234_sum["full_text_raw"]}])
        print(f"  m234_dual: stream_WER={m234_sc['stream_hinglish_norm_wer']:.4f} RTF={m234_sum['rtf']:.3f} P50_1stW={m234_sum['p50_first_word_latency_sec']}s max_bl={m234_sum['max_shared_queue_backlog_sec']}s", flush=True)

        peak_rss = max(rss0, get_process_rss_mb())
        runner.unload()

        out_doc["engines"][eng_id] = {
            "base_model_id": base_mid,
            "policy": pol,
            "load_sec": runner.load_sec,
            "peak_rss_mb": round(peak_rss, 1),
            "earnings22_us_continuous": {k: v for k, v in e22_sum.items() if not k.startswith("full_text_")} if e22_sum else None,
            "ami_meetings_continuous": {k: v for k, v in ami_sum.items() if not k.startswith("full_text_")},
            "m240_us_in_call_5m": {k: v for k, v in m240_sum.items() if not k.startswith("full_text_")} if m240_sum else None,
            "m234_dual_track_shared_q": {k: v for k, v in m234_sum.items() if not k.startswith("full_text_")},
        }
        out_path.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    verify_db_unchanged()
    print(f"[DONE] Saved {out_path}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Step 7: Next-Gen Routing & Confidence-Gated Selective Verification
# ---------------------------------------------------------------------------

_HINGLISH_MARKERS = {
    "hai", "haan", "nahi", "nai", "nhi", "kya", "mein", "me", "mai", "hum", "aap",
    "tum", "woh", "yeh", "aur", "par", "toh", "to", "bhi", "kar", "karna", "karo",
    "karne", "raha", "rha", "rahe", "ho", "hota", "hoti", "theek", "thik", "accha",
    "achha", "acha", "matlab", "baat", "kuch", "abhi", "koi", "iska", "usko",
    "pehle", "phir", "fir", "liye", "wala", "wali", "wale", "ki", "ke", "ka", "ko", "se",
}


def _has_deva(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _hinglish_cue_score(rec: Dict[str, Any]) -> Tuple[bool, str]:
    dl = str(rec.get("detected_language") or "").lower()
    raw = str(rec.get("raw_text") or "")
    if dl in ("hi", "hindi", "ur", "mr", "ne", "pa"):
        return True, f"lang={dl}"
    if _has_deva(raw):
        return True, "devanagari_chars"
    toks = [t.lower() for t in re.findall(r"[a-zA-Z]+", rec.get("roman_text") or raw)]
    if len(toks) >= 3:
        hits = sum(1 for t in toks if t in _HINGLISH_MARKERS)
        if hits >= 2 and (hits / float(len(toks))) >= 0.18:
            return True, f"hinglish_lex_{hits}/{len(toks)}"
    lp = rec.get("lang_probs") or {}
    if float(lp.get("hi", 0.0)) + float(lp.get("p_hi", 0.0)) + float(lp.get("p_ur", 0.0)) >= 0.35:
        return True, "lang_prob_hi"
    return False, ""


def _load15(mid: str) -> Optional[Dict[str, Any]]:
    p = RESULTS15_DIR / f"{mid}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _clip_map15(mdata: Dict[str, Any], track_name: str) -> Dict[str, Dict[str, Any]]:
    tb = (mdata.get("tracks") or {}).get(track_name) or {}
    return {c["id"]: c for c in (tb.get("clips") or [])}


def run_routing_v2() -> int:
    db_before = verify_db_unchanged()
    RESULTS15_DIR.mkdir(parents=True, exist_ok=True)

    qwen_g = _load15("onnx_qwen3_asr_06b_int8_guarded")
    qwen_hf = _load15("hf_qwen3_asr_06b_guarded")
    turbo_prod = _load15("prod_whisper_turbo_balanced")
    small_prod = _load15("prod_whisper_small_balanced")
    parakeet = _load15("onnx_parakeet_tdt_v2_int8")
    apex = _load15("ct2_oriserve_apex_int8")
    base_h = _load15("ct2_whisper_base_hinglish_int8")

    eval_tracks = [
        "earnings22_us",
        "fleurs_en_us",
        "ami_meetings_en",
        "nptel_in_en",
        "monsoon_in_en",
        "fleurs_hi_in",
        "coshe_hinglish",
        "m234_full_90s",
        "m235_verified_111",
    ]

    def _eval_selective_pair(
        pair_id: str,
        prim_data: Optional[Dict[str, Any]],
        sec_data: Optional[Dict[str, Any]],
        *,
        hardware_tier: str = "cpu_only_0_vram",
        logprob_thresh: float = -0.55,
        mode: str = "confidence_selective",  # or session_auto_escalate
        warmup_sec: float = 28.0,
        esc_lang_hi: bool = False,
        esc_min_wps: float = 0.75,
        overhead_check_rtf: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        if not prim_data or not sec_data:
            return None
        avail = [t for t in eval_tracks if t in (prim_data.get("tracks") or {}) and t in (sec_data.get("tracks") or {})]
        if not avail:
            return None

        out_tracks: Dict[str, Any] = {}
        for t in avail:
            pmap = _clip_map15(prim_data, t)
            smap = _clip_map15(sec_data, t)
            common = sorted(cid for cid in pmap if cid in smap)
            if t in ("m234_full_90s", "m235_verified_111"):
                common.sort(key=lambda cid: int(pmap[cid].get("segment_index", 0)))

            recs: List[Dict[str, Any]] = []
            escalated = 0
            accepted_sec = 0
            session_escalated = False
            warmup_elapsed = 0.0
            trigger_reasons: Dict[str, int] = {}

            for cid in common:
                pr = pmap[cid]
                sr = smap[cid]
                dur = float(pr["duration_sec"])
                wps = (
                    float(pr["words_per_sec"])
                    if pr.get("words_per_sec") is not None
                    else round(len(tokenize_hinglish_norm(pr.get("roman_text") or pr.get("raw_text") or "")) / max(0.05, dur), 3)
                )
                reasons: List[str] = []

                if mode == "session_auto_escalate":
                    if not session_escalated:
                        warmup_elapsed += dur
                        cue, cue_r = _hinglish_cue_score(pr)
                        sc_cue, sc_r = _hinglish_cue_score(sr) if warmup_elapsed <= warmup_sec else (False, "")
                        if cue or sc_cue or (dur >= 1.5 and wps < 0.6):
                            session_escalated = True
                            reasons.append(cue_r or sc_r or "warmup_low_wps")
                    use_sec = session_escalated
                    if use_sec:
                        escalated += 1
                        accepted_sec += 1
                        chosen = dict(sr)
                        chosen["infer_sec"] = round(float(sr["infer_sec"]) + (0.0 if len(reasons) == 0 else float(pr["infer_sec"])), 4)
                    else:
                        chosen = dict(pr)
                    recs.append(chosen)
                    for r in reasons:
                        trigger_reasons[r] = trigger_reasons.get(r, 0) + 1
                    continue

                # Confidence-gated selective second-pass verification
                lp = pr.get("avg_logprob")
                if lp is not None and float(lp) < logprob_thresh:
                    reasons.append("low_logprob")
                nsp = pr.get("no_speech_prob")
                if nsp is not None and float(nsp) >= 0.60 and dur >= 1.5:
                    reasons.append("high_no_speech_prob")
                if (dur >= 2.2 and wps < esc_min_wps) or (dur >= 0.8 and not (pr.get("raw_text") or "").strip()):
                    reasons.append("omission_risk")
                if pr.get("is_uncertain") is True:
                    reasons.append("uncertainty_flag")
                raw_p = str(pr.get("raw_text") or "")
                if _count_duplication_words(raw_p.split()) > 0:
                    reasons.append("repetition_anomaly")
                sp_p = audit_mixed_script_and_utf8([{"text": raw_p}])
                if (sp_p["cjk_segs"] + sp_p["arabic_segs"] + sp_p["kana_hangul_cyrillic_segs"] + sp_p["ufffd_segs"]) > 0:
                    reasons.append("foreign_script")
                if esc_lang_hi:
                    is_hi_cue, cue_r = _hinglish_cue_score(pr)
                    if is_hi_cue:
                        reasons.append(f"cue:{cue_r}")

                chosen = dict(pr)
                if reasons:
                    escalated += 1
                    for r in reasons:
                        trigger_reasons[r] = trigger_reasons.get(r, 0) + 1
                    raw_s = str(sr.get("raw_text") or "")
                    sp_s = audit_mixed_script_and_utf8([{"text": raw_s}])
                    sec_clean = (sp_s["cjk_segs"] + sp_s["arabic_segs"] + sp_s["kana_hangul_cyrillic_segs"] + sp_s["ufffd_segs"]) == 0
                    swps = (
                        float(sr["words_per_sec"])
                        if sr.get("words_per_sec") is not None
                        else round(len(tokenize_hinglish_norm(sr.get("roman_text") or sr.get("raw_text") or "")) / max(0.05, dur), 3)
                    )
                    slp = sr.get("avg_logprob")
                    # Controlled reconciliation: never replace a fluent high-conf primary with worse/empty 2nd pass
                    should_accept = False
                    if sec_clean and (raw_s.strip() or (sr.get("roman_text") or "").strip()):
                        if "omission_risk" in reasons or "foreign_script" in reasons or "repetition_anomaly" in reasons:
                            if swps > wps * 1.15 or not raw_p.strip():
                                should_accept = True
                        elif esc_lang_hi and any(r.startswith("cue:") for r in reasons):
                            should_accept = True
                        elif slp is not None and lp is not None and float(slp) >= float(lp) - 0.08:
                            should_accept = True
                        elif slp is None and "low_logprob" in reasons:
                            # Accept non-empty second pass when primary had very low logprob and secondary has denser speech
                            if swps >= max(1.0, wps):
                                should_accept = True

                    if should_accept:
                        accepted_sec += 1
                        chosen = dict(sr)
                    chosen["infer_sec"] = round(float(pr["infer_sec"]) + float(sr["infer_sec"]), 4)

                recs.append(chosen)

            agg = _finalize_track_bundle_15(t, recs)["metrics"]
            agg["escalated_count"] = escalated
            agg["escalated_pct"] = round(100.0 * escalated / max(1, len(common)), 2)
            agg["accepted_second_pass_pct"] = round(100.0 * accepted_sec / max(1, len(common)), 2)
            agg["trigger_reasons"] = trigger_reasons
            if overhead_check_rtf > 0.0:
                agg["blended_rtf"] = round(float(agg["rtf"]) + overhead_check_rtf, 4)
            else:
                agg["blended_rtf"] = agg["rtf"]
            out_tracks[t] = agg

        return {
            "strategy_id": pair_id,
            "mode": mode,
            "hardware_tier": hardware_tier,
            "primary_model": prim_data["model_id"],
            "secondary_model": sec_data["model_id"],
            "tracks": out_tracks,
        }

    strategies: List[Dict[str, Any]] = []
    # Pair A: Session Auto-Escalation (CPU + GPU tiers)
    for pid, tier, p_dat, s_dat, ov_rtf in [
        ("session_auto_parakeet_to_qwen3_onnx", "cpu_only_0_vram", parakeet, qwen_g, 0.03),
        ("session_auto_prod_turbo_to_qwen3_onnx", "gpu_cuda_gtx1650", turbo_prod, qwen_g, 0.0),
        ("session_auto_parakeet_to_hf_qwen3", "gpu_cuda_gtx1650", parakeet, qwen_hf, 0.03),
        ("session_auto_prod_turbo_to_hf_qwen3", "gpu_cuda_gtx1650", turbo_prod, qwen_hf, 0.0),
    ]:
        res = _eval_selective_pair(
            pid, p_dat, s_dat, hardware_tier=tier, mode="session_auto_escalate", warmup_sec=28.0, overhead_check_rtf=ov_rtf
        )
        if res:
            strategies.append(res)

    # Pair B1: Primary Guarded Qwen3 ONNX -> Selective Prod Whisper Turbo
    for lp_th in (-0.45, -0.55, -0.65):
        res = _eval_selective_pair(
            f"sel_qwen3_onnx_to_prod_turbo_lp{abs(int(lp_th*100))}",
            qwen_g,
            turbo_prod,
            hardware_tier="hybrid_cpu_or_gpu",
            logprob_thresh=lp_th,
            mode="confidence_selective",
        )
        if res:
            strategies.append(res)

    # Pair B2: Primary Prod Whisper Turbo -> Selective Guarded Qwen3 ONNX / HF Qwen3 / Oriserve Apex
    for lp_th in (-0.45, -0.55):
        res = _eval_selective_pair(
            f"sel_prod_turbo_to_qwen3_onnx_lp{abs(int(lp_th*100))}_hicue",
            turbo_prod,
            qwen_g,
            hardware_tier="hybrid_cpu_or_gpu",
            logprob_thresh=lp_th,
            mode="confidence_selective",
            esc_lang_hi=True,
        )
        if res:
            strategies.append(res)

    for pid, sec_m in [
        ("sel_prod_turbo_to_hf_qwen3_lp55_hicue", qwen_hf),
        ("sel_prod_turbo_to_oriserve_apex_lp55_hicue", apex),
    ]:
        res = _eval_selective_pair(
            pid,
            turbo_prod,
            sec_m,
            hardware_tier="gpu_cuda_gtx1650",
            logprob_thresh=-0.55,
            mode="confidence_selective",
            esc_lang_hi=True,
        )
        if res:
            strategies.append(res)

    # Pair B3: Primary Parakeet -> Selective Guarded Qwen3 ONNX (CPU-only 0-VRAM!)
    res_pk = _eval_selective_pair(
        "sel_parakeet_to_qwen3_onnx_omission_hicue",
        parakeet,
        qwen_g,
        hardware_tier="cpu_only_0_vram",
        logprob_thresh=-9.9,
        mode="confidence_selective",
        esc_lang_hi=True,
        esc_min_wps=0.95,
    )
    if res_pk:
        strategies.append(res_pk)

    # Pair B4: Primary Qwen3 (ONNX & HF) -> Selective Oriserve Apex
    for pid, tier, pm in [
        ("sel_qwen3_onnx_to_oriserve_apex_lp45_hicue", "gpu_cuda_gtx1650", qwen_g),
        ("sel_hf_qwen3_to_oriserve_apex_lp45_hicue", "gpu_cuda_gtx1650", qwen_hf),
    ]:
        res_qa = _eval_selective_pair(
            pid,
            pm,
            apex,
            hardware_tier=tier,
            logprob_thresh=-0.45,
            mode="confidence_selective",
            esc_lang_hi=True,
        )
        if res_qa:
            strategies.append(res_qa)

    out_doc = {
        "db_sha256": db_before,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategies": strategies,
    }
    out_path = RESULTS15_DIR / "routing_v2_results.json"
    out_path.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] Saved {out_path} ({len(strategies)} strategies)", flush=True)
    return 0


def run_report() -> int:
    db_before = verify_db_unchanged()
    models_out: Dict[str, Any] = {}
    for p in sorted(RESULTS15_DIR.glob("*.json")):
        if p.name in ("stream_v2_results.json", "routing_v2_results.json", "phase15_validation_report.json", "oriserve_apex_mode_probe.json", "m240_candidate_predictions.json", "m240_verified_scores.json"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        mid = d.get("model_id") or p.stem
        models_out[mid] = {
            "display": (d.get("spec") or {}).get("display", mid),
            "device": d.get("device"),
            "compute_type": d.get("compute_type"),
            "load_sec": d.get("load_sec"),
            "peak_rss_mb": d.get("peak_rss_mb"),
            "vram_before_mb": d.get("vram_before_mb"),
            "vram_peak_mb": d.get("vram_peak_mb"),
            "vram_after_unload_mb": d.get("vram_after_unload_mb"),
            "tracks": {tn: (tb.get("metrics") or {}) for tn, tb in (d.get("tracks") or {}).items()},
            "official_meetings": d.get("official_meetings") or {},
        }

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256": db_before,
        "models": models_out,
        "stream_v2": json.loads((RESULTS15_DIR / "stream_v2_results.json").read_text(encoding="utf-8")) if (RESULTS15_DIR / "stream_v2_results.json").exists() else None,
        "routing_v2": json.loads((RESULTS15_DIR / "routing_v2_results.json").read_text(encoding="utf-8")) if (RESULTS15_DIR / "routing_v2_results.json").exists() else None,
        "chariot_stt_assessment": {
            "provider": "chariot.in/speech-to-text",
            "status": "Unsuitable for VOX offline enterprise architecture",
            "reason": "Paid cloud WebSocket API (wss://api.chariot.in/v1/asr/ws, 1.5 credits/sec); violates zero-cost offline privacy requirements.",
        },
        "hardware_matrix": {
            "tier_1_ordinary_cpu_8_16gb": {
                "recommended_stack": "onnx_qwen3_asr_06b_int8_guarded (or prod_whisper_turbo / parakeet for English-only)",
                "vram_required_mb": 0,
                "peak_rss_mb": models_out.get("onnx_qwen3_asr_06b_int8_guarded", {}).get("peak_rss_mb"),
            },
            "tier_2_gtx1650_4gb_variable_vram": {
                "recommended_stack": "CPU INT8 Guarded Qwen3 ONNX + optional CUDA FP16 when free_vram >= 2000 MB",
                "note": "Desktop compositors hold ~1.0-2.8 GB VRAM; CPU INT8 avoids silent Small CPU fallback.",
            },
        },
    }
    out_json = RESULTS15_DIR / "phase15_validation_report.json"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Build concise Markdown summary table for quick inspection
    lines = [
        "# VOX Phase 1.5 CTO Validation & Challenger Benchmark Report",
        "",
        f"- Generated UTC: `{report['generated_at_utc']}`",
        f"- DB SHA-256 verified unchanged: `{db_before}`",
        "",
        "## Model Accuracy & Purity Matrix (WER ↓, CJK/Arab Leaks ↓)",
        "",
        "| Model | Device | `e22_us` | `fleurs_en` | `ami_mtg` | `nptel_in` | `monsoon_in` | `fleurs_hi(R)` | `coshe_mix` | `m234(16)` | `m235(s30)` | `m235(111)` | Leaks(111) | Avg RTF |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def _w(tr_map, k):
        m = tr_map.get(k)
        return f"{m['wer']*100:.2f}%" if (m and "wer" in m) else "—"
    def _leaks(tr_map):
        m = tr_map.get("m235_verified_111") or tr_map.get("m234_full_90s")
        if not m or "script_purity" not in m:
            return "—"
        sp = m["script_purity"]
        return str(sp.get("cjk_segs", 0) + sp.get("arabic_segs", 0) + sp.get("kana_hangul_cyrillic_segs", 0))

    for mid, info in models_out.items():
        tm = info["tracks"]
        rtfs = [m["rtf"] for k, m in tm.items() if isinstance(m, dict) and "rtf" in m and k != "m235_strat30"]
        avg_rtf = f"{(sum(rtfs)/len(rtfs)):.3f}x" if rtfs else "—"
        lines.append(
            f"| `{mid}` | `{info.get('device')}/{info.get('compute_type')}` | "
            f"{_w(tm,'earnings22_us')} | {_w(tm,'fleurs_en_us')} | {_w(tm,'ami_meetings_en')} | "
            f"{_w(tm,'nptel_in_en')} | {_w(tm,'monsoon_in_en')} | {_w(tm,'fleurs_hi_in')} | "
            f"{_w(tm,'coshe_hinglish')} | {_w(tm,'m234_full_90s')} | {_w(tm,'m235_strat30')} | "
            f"{_w(tm,'m235_verified_111')} | {_leaks(tm)} | {avg_rtf} |"
        )

    if report["stream_v2"] and report["stream_v2"].get("engines"):
        lines.extend([
            "",
            "## Shared-Queue Dual-Track Streaming v2",
            "",
            "| Engine | Policy | `e22_WER` | `e22_RTF` | `ami_WER` | `ami_RTF` | `m234_WER` | `m234_RTF` | `m234_1stW_P50` | `m234_MaxBL` |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        def _fp(d: Dict[str, Any], k: str, suf: str = "%", mult: float = 100.0) -> str:
            return f"{d[k] * mult:.2f}{suf}" if (d and k in d and d[k] is not None) else "—"
        for eid, ed in report["stream_v2"]["engines"].items():
            e22 = ed.get("earnings22_us_continuous") or {}
            ami = ed.get("ami_meetings_continuous") or {}
            m234 = ed.get("m234_dual_track_shared_q") or {}
            m234_w = (m234.get("official_scores") or {}).get("stream_hinglish_norm_wer")
            m234_ws = f"{m234_w * 100:.2f}%" if m234_w is not None else "—"
            lines.append(
                f"| `{eid}` | `{ed.get('policy')}` | "
                f"{_fp(e22, 'wer')} | {_fp(e22, 'rtf', 'x', 1.0)} | "
                f"{_fp(ami, 'wer')} | {_fp(ami, 'rtf', 'x', 1.0)} | "
                f"{m234_ws} | {_fp(m234, 'rtf', 'x', 1.0)} | "
                f"{_fp(m234, 'p50_first_word_latency_sec', 's', 1.0)} | "
                f"{_fp(m234, 'max_shared_queue_backlog_sec', 's', 1.0)} |"
            )

    if report["routing_v2"] and report["routing_v2"].get("strategies"):
        lines.extend([
            "",
            "## Next-Gen Routing & Selective Verification v2",
            "",
            "| Strategy | Tier | `e22_us` | `ami_mtg` | `nptel_in` | `monsoon_in` | `fleurs_hi` | `coshe_mix` | `m234` | `m235_111` | `m235_Esc%` |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for st in report["routing_v2"]["strategies"]:
            tm = st.get("tracks") or {}
            m235_esc = (tm.get("m235_verified_111") or {}).get("escalated_pct")
            esc_s = f"{m235_esc:.1f}%" if m235_esc is not None else "—"
            lines.append(
                f"| `{st['strategy_id']}` | `{st.get('hardware_tier','')}` | "
                f"{_w(tm,'earnings22_us')} | {_w(tm,'ami_meetings_en')} | {_w(tm,'nptel_in_en')} | "
                f"{_w(tm,'monsoon_in_en')} | {_w(tm,'fleurs_hi_in')} | {_w(tm,'coshe_hinglish')} | "
                f"{_w(tm,'m234_full_90s')} | {_w(tm,'m235_verified_111')} | {esc_s} |"
            )

    out_md = RESULTS15_DIR / "phase15_validation_summary.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    verify_db_unchanged()
    print(f"[DONE] Wrote {out_json} and {out_md}", flush=True)
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VOX Phase 1.5 CTO Validation Suite")
    p.add_argument("--model", choices=sorted(MODEL_SPECS_15.keys()))
    p.add_argument("--scope", choices=["full", "standard", "new3", "hotword_ab", "m235_rest"], default=None)
    p.add_argument("--tracks", type=str, default=None)
    p.add_argument("--cpu-threads", type=int, default=None)
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    p.add_argument("--out-name", type=str, default=None)
    p.add_argument("--mode", choices=["m240_draft", "rescore_m240", "stream_v2", "routing_v2", "report"])
    p.add_argument("--stream-engines", type=str, default=None)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    args = parse_args(argv)
    with _prevent_windows_sleep():
        if args.model:
            scope = args.scope or MODEL_SPECS_15[args.model]["default_scope"]
            tracks_ov = [t.strip() for t in args.tracks.split(",") if t.strip()] if args.tracks else None
            return run_model_benchmark_15(
                args.model,
                scope,
                cpu_threads=args.cpu_threads,
                device=args.device,
                tracks_override=tracks_ov,
                out_name=args.out_name,
            )
        if args.mode == "m240_draft":
            return run_m240_draft()
        if args.mode == "rescore_m240":
            return run_rescore_m240()
        if args.mode == "stream_v2":
            fil = [s.strip() for s in args.stream_engines.split(",") if s.strip()] if args.stream_engines else None
            return run_streaming_v2(engine_filter=fil)
        if args.mode == "routing_v2":
            return run_routing_v2()
        if args.mode == "report":
            return run_report()
    raise SystemExit("Pass --model <id> or --mode <...>")


if __name__ == "__main__":
    raise SystemExit(main())

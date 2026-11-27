"""
Guarded Python ONNX Runtime Decoder for Qwen3-ASR 0.6B INT8.

Replaces sherpa-onnx's unguarded internal decoder for Phase 1.5 validation so we can
apply the exact Phase 2.5 logits constraints directly on `decoder.int8.onnx` logits:
1. Forced language header: `step 0 = "language"`, `step 1 in {" None", " English", " Hindi"}`,
   `step 2 = "<asr_text>"`.
2. Foreign-script token masking (`build_foreign_script_token_ids`: CJK, Arabic, Kana,
   Hangul, Cyrillic -> `-inf`).
3. Mild repetition penalty (`1.05`) + degenerate n-gram loop breaker + UTF-8/tail cleanup.
4. Native token log-probability extraction (`avg_logprob`, `min_token_prob`) +
   30s-safe pause-aligned VAD chunking with tail preservation.

Strictly isolated in `scripts/` (never imported by or modifying `src/voxlocal/`).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import onnxruntime as ort
from transformers import AutoProcessor, AutoTokenizer

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.hf_asr_engine import (
    build_foreign_script_token_ids,
    clean_repetition_tail,
    is_prompt_contamination_hallucination,
    sanitize_utf8_text,
)
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

DEFAULT_ONNX_DIR = ROOT_DIR / "data" / "models" / "challengers" / "sherpa-onnx-qwen3-asr-0.6b-int8"
DEFAULT_HF_PROC_DIR = ROOT_DIR / "data" / "models" / "challengers" / "qwen3-asr-0.6b"


def _logsumexp_1d(x: np.ndarray) -> float:
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return 0.0
    m = float(np.max(finite))
    return m + float(np.log(np.sum(np.exp(finite - m))))


def _has_degenerate_token_loop(content_ids: List[int]) -> bool:
    n = len(content_ids)
    if n >= 10 and len(set(content_ids[-10:])) == 1:
        return True
    if n >= 16:
        pat2 = content_ids[-2:]
        reps2 = 0
        for i in range(n - 2, 1, -2):
            if content_ids[i : i + 2] == pat2:
                reps2 += 1
            else:
                break
        if reps2 >= 6:
            return True
    if n >= 18:
        pat3 = content_ids[-3:]
        reps3 = 0
        for i in range(n - 3, 2, -3):
            if content_ids[i : i + 3] == pat3:
                reps3 += 1
            else:
                break
        if reps3 >= 5:
            return True
    return False


class GuardedQwen3OnnxTranscriber:
    """KV-cached ONNX Runtime greedy decoder with English/Hindi & foreign-script guards."""

    def __init__(
        self,
        onnx_dir: Optional[str | Path] = None,
        processor_dir: Optional[str | Path] = None,
        num_threads: int = 4,
        repetition_penalty: float = 1.05,
    ) -> None:
        self.onnx_dir = Path(onnx_dir) if onnx_dir else DEFAULT_ONNX_DIR
        self.processor_dir = Path(processor_dir) if processor_dir else DEFAULT_HF_PROC_DIR
        self.num_threads = max(1, int(num_threads))
        self.repetition_penalty = float(repetition_penalty)

        self._conv_sess: Optional[ort.InferenceSession] = None
        self._enc_sess: Optional[ort.InferenceSession] = None
        self._dec_sess: Optional[ort.InferenceSession] = None
        self._fe: Any = None
        self._tok: Any = None
        self._blocked_ids: Optional[np.ndarray] = None
        self._lang_word_id: int = 0
        self._allowed_lang_ids: Optional[np.ndarray] = None
        self._lang_names = ("none", "en", "hi")
        self._asr_text_id: int = 0
        self._im_start: int = 0
        self._im_end: int = 0
        self._audio_start: int = 0
        self._audio_pad: int = 0
        self._audio_end: int = 0
        self._eos_ids: set[int] = set()
        self._nl_ids: List[int] = []
        self._sys_prefix_ids: List[int] = []
        self._user_prefix_ids: List[int] = []
        self._assistant_suffix_ids: List[int] = []
        self.load_sec: float = 0.0

    def load(self) -> None:
        if self._dec_sess is not None:
            return
        t0 = time.perf_counter()
        if not (self.onnx_dir / "decoder.int8.onnx").exists():
            raise FileNotFoundError(f"Missing ONNX decoder in {self.onnx_dir}")

        proc_src = self.processor_dir if self.processor_dir.exists() else (self.onnx_dir / "tokenizer")
        proc = AutoProcessor.from_pretrained(str(proc_src), local_files_only=True, trust_remote_code=False)
        self._fe = proc.feature_extractor
        self._tok = AutoTokenizer.from_pretrained(str(self.onnx_dir / "tokenizer"), local_files_only=True)

        self._blocked_ids = np.array(build_foreign_script_token_ids(self._tok), dtype=np.int64)
        self._lang_word_id = self._tok.encode("language", add_special_tokens=False)[0]
        self._allowed_lang_ids = np.array(
            [
                self._tok.encode(" None", add_special_tokens=False)[0],
                self._tok.encode(" English", add_special_tokens=False)[0],
                self._tok.encode(" Hindi", add_special_tokens=False)[0],
            ],
            dtype=np.int64,
        )
        self._asr_text_id = self._tok.encode("<asr_text>", add_special_tokens=False)[0]
        self._im_start = int(self._tok.convert_tokens_to_ids("<|im_start|>"))
        self._im_end = int(self._tok.convert_tokens_to_ids("<|im_end|>"))
        self._audio_start = int(self._tok.convert_tokens_to_ids("<|audio_start|>"))
        self._audio_pad = int(self._tok.convert_tokens_to_ids("<|audio_pad|>"))
        self._audio_end = int(self._tok.convert_tokens_to_ids("<|audio_end|>"))
        self._eos_ids = {self._im_end, int(self._tok.convert_tokens_to_ids("<|endoftext|>"))}
        self._nl_ids = self._tok.encode("\n", add_special_tokens=False)
        self._sys_prefix_ids = [self._im_start] + self._tok.encode("system\n", add_special_tokens=False)
        self._user_prefix_ids = (
            [self._im_end] + self._nl_ids + [self._im_start] + self._tok.encode("user\n", add_special_tokens=False) + [self._audio_start]
        )
        self._assistant_suffix_ids = (
            [self._audio_end, self._im_end] + self._nl_ids + [self._im_start] + self._tok.encode("assistant\n", add_special_tokens=False)
        )

        so = ort.SessionOptions()
        so.intra_op_num_threads = self.num_threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._conv_sess = ort.InferenceSession(str(self.onnx_dir / "conv_frontend.onnx"), sess_options=so, providers=["CPUExecutionProvider"])
        self._enc_sess = ort.InferenceSession(str(self.onnx_dir / "encoder.int8.onnx"), sess_options=so, providers=["CPUExecutionProvider"])
        self._dec_sess = ort.InferenceSession(str(self.onnx_dir / "decoder.int8.onnx"), sess_options=so, providers=["CPUExecutionProvider"])
        self.load_sec = round(time.perf_counter() - t0, 3)

    def _build_prompt_ids(self, n_audio_tokens: int, hotwords: Optional[str]) -> List[int]:
        mid = self._tok.encode(str(hotwords).strip(), add_special_tokens=False) if hotwords and str(hotwords).strip() else []
        return (
            self._sys_prefix_ids
            + mid
            + self._user_prefix_ids
            + [self._audio_pad] * n_audio_tokens
            + self._assistant_suffix_ids
        )

    def _decode_single_window(
        self,
        chunk_16k: np.ndarray,
        *,
        hotwords: Optional[str] = None,
        max_new_tokens: int = 256,
    ) -> Dict[str, Any]:
        self.load()
        if chunk_16k.size < int(0.15 * TARGET_SAMPLE_RATE):
            return {
                "raw_text": "",
                "detected_language": "",
                "lang_prob": 0.0,
                "lang_probs": {},
                "avg_logprob": None,
                "min_token_prob": None,
                "num_tokens": 0,
                "had_utf8_replacement": False,
            }

        norm = normalize_speech_audio(chunk_16k)
        feat_out = self._fe([norm], sampling_rate=TARGET_SAMPLE_RATE, return_attention_mask=True, padding=False, return_tensors="np")
        feats = feat_out["input_features"].transpose(0, 2, 1).astype(np.float32)

        conv_out = self._conv_sess.run(None, {"input_features": feats})[0]
        n_audio = int(conv_out.shape[1])
        fam = np.ones((1, n_audio), dtype=np.bool_)
        audio_feats = self._enc_sess.run(None, {"input_features": conv_out, "feature_attention_mask": fam})[0]

        prefix_ids = self._build_prompt_ids(n_audio, hotwords)
        prompt_len = len(prefix_ids)
        max_total = prompt_len + max_new_tokens + 4
        k_cache = [np.zeros((1, max_total, 8, 128), dtype=np.float32) for _ in range(28)]
        v_cache = [np.zeros((1, max_total, 8, 128), dtype=np.float32) for _ in range(28)]

        def _run_dec(ids_arr: np.ndarray, af_arr: np.ndarray, cache_pos: np.ndarray) -> np.ndarray:
            seq_len = int(ids_arr.shape[1])
            feed: Dict[str, Any] = {
                "input_ids": ids_arr.astype(np.int64, copy=False),
                "audio_features": af_arr.astype(np.float32, copy=False),
                "attention_mask": np.ones((1, seq_len), dtype=np.int64),
                "cache_position": cache_pos.astype(np.int64, copy=False),
            }
            for idx in range(28):
                feed[f"cache_key_{idx}"] = k_cache[idx]
                feed[f"cache_value_{idx}"] = v_cache[idx]
            outs = self._dec_sess.run(None, feed)
            for idx in range(28):
                k_cache[idx][:, cache_pos, :, :] = outs[1 + 2 * idx]
                v_cache[idx][:, cache_pos, :, :] = outs[2 + 2 * idx]
            return outs[0]

        logits = _run_dec(np.array([prefix_ids], dtype=np.int64), audio_feats, np.arange(prompt_len, dtype=np.int64))
        gen_ids: List[int] = []
        token_logprobs: List[float] = []
        det_lang = ""
        lang_prob = 0.0
        lang_probs: Dict[str, float] = {}

        # Note: dummy audio_features MUST have length 1 (never 0) to satisfy decoder GatherElements node!
        dummy_af = np.zeros((1, 1, 1024), dtype=np.float32)
        cur_pos = prompt_len

        for step in range(max_new_tokens):
            scores = logits[0, -1].astype(np.float64, copy=True)
            if step == 0:
                nxt = int(self._lang_word_id)
            elif step == 1:
                lang_logits = scores[self._allowed_lang_ids]
                lse = _logsumexp_1d(lang_logits)
                probs = np.exp(lang_logits - lse)
                best_i = int(np.argmax(lang_logits))
                nxt = int(self._allowed_lang_ids[best_i])
                det_lang = self._lang_names[best_i]
                lang_prob = round(float(probs[best_i]), 4)
                lang_probs = {k: round(float(v), 4) for k, v in zip(self._lang_names, probs)}
            elif step == 2:
                nxt = int(self._asr_text_id)
            else:
                if self._blocked_ids is not None and self._blocked_ids.size > 0:
                    scores[self._blocked_ids] = -np.inf
                if self.repetition_penalty != 1.0 and len(gen_ids) > 3:
                    for gid in set(gen_ids[3:]):
                        if scores[gid] > 0:
                            scores[gid] /= self.repetition_penalty
                        elif np.isfinite(scores[gid]):
                            scores[gid] *= self.repetition_penalty
                nxt = int(np.argmax(scores))
                if nxt in self._eos_ids:
                    break
                lse = _logsumexp_1d(scores)
                token_logprobs.append(float(scores[nxt] - lse))

            gen_ids.append(nxt)
            if nxt in self._eos_ids:
                break
            if step >= 12 and _has_degenerate_token_loop(gen_ids[3:]):
                break
            if step + 1 >= max_new_tokens:
                break
            logits = _run_dec(np.array([[nxt]], dtype=np.int64), dummy_af, np.array([cur_pos], dtype=np.int64))
            cur_pos += 1

        content_ids = gen_ids[3:] if len(gen_ids) > 3 else []
        raw_dec = self._tok.decode(content_ids, skip_special_tokens=True).strip() if content_ids else ""
        cleaned_raw, had_rep = sanitize_utf8_text(raw_dec)
        cleaned_raw = clean_repetition_tail(cleaned_raw)

        avg_lp = round(float(sum(token_logprobs) / len(token_logprobs)), 4) if token_logprobs else None
        min_tp = round(float(np.exp(min(token_logprobs))), 4) if token_logprobs else None

        return {
            "raw_text": cleaned_raw,
            "detected_language": det_lang,
            "lang_prob": lang_prob,
            "lang_probs": lang_probs,
            "avg_logprob": avg_lp,
            "min_token_prob": min_tp,
            "num_tokens": len(token_logprobs),
            "had_utf8_replacement": had_rep,
        }

    def transcribe_array(
        self,
        audio_16k: np.ndarray,
        *,
        hotwords: Optional[str] = None,
        max_new_tokens: int = 256,
    ) -> Dict[str, Any]:
        self.load()
        t0 = time.perf_counter()
        if audio_16k.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(audio_16k))) < 0.0004:
            return {
                "raw_text": "",
                "roman_text": "",
                "detected_language": "",
                "language_probability": 0.0,
                "lang_probs": {},
                "avg_logprob": None,
                "min_token_prob": None,
                "infer_sec": round(time.perf_counter() - t0, 4),
            }

        dur_sec = float(audio_16k.size) / float(TARGET_SAMPLE_RATE)
        if dur_sec <= 26.0:
            chunks = [(0, int(audio_16k.size))]
        else:
            vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
            spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
                audio_16k, vad_p, max_chunk_sec=22.0, merge_gap_sec=0.65, pad_sec=0.20
            )
            if spans is None or len(spans) == 0:
                spans = [(0, int(audio_16k.size))]
            elif spans[-1][1] < audio_16k.size:
                tail_s = int(spans[-1][1])
                tail_arr = audio_16k[tail_s:]
                if tail_arr.size >= int(0.25 * TARGET_SAMPLE_RATE) and float(np.max(np.abs(tail_arr))) >= 0.0005:
                    spans = list(spans) + [(tail_s, int(audio_16k.size))]
            cap_n = int(26.0 * TARGET_SAMPLE_RATE)
            chunks = []
            for s_i, e_i in spans:
                pos = int(s_i)
                while pos < int(e_i):
                    nxt = min(int(e_i), pos + cap_n)
                    chunks.append((pos, nxt))
                    pos = nxt

        parts: List[str] = []
        langs: List[str] = []
        lps: List[float] = []
        avg_lps: List[float] = []
        min_tps: List[float] = []
        lang_probs_first: Dict[str, float] = {}

        for s_i, e_i in chunks:
            sl = audio_16k[s_i:e_i]
            if sl.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0004:
                continue
            sl_dur = float(sl.size) / float(TARGET_SAMPLE_RATE)
            sl_rms = float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0
            eff_hw = str(hotwords).strip() if (hotwords and str(hotwords).strip() and sl_dur >= 1.5 and sl_rms >= 0.012) else None

            res = self._decode_single_window(sl, hotwords=eff_hw, max_new_tokens=max_new_tokens)
            if eff_hw and res["raw_text"] and is_prompt_contamination_hallucination(
                res["raw_text"], initial_prompt=eff_hw, duration_sec=sl_dur, audio_rms=sl_rms
            ):
                retry = self._decode_single_window(sl, hotwords=None, max_new_tokens=max_new_tokens)
                if retry["raw_text"] and not is_prompt_contamination_hallucination(
                    retry["raw_text"], initial_prompt=eff_hw, duration_sec=sl_dur, audio_rms=sl_rms
                ):
                    res = retry
                else:
                    res["raw_text"] = ""

            if res["raw_text"]:
                parts.append(res["raw_text"])
            if res["detected_language"]:
                langs.append(res["detected_language"])
                lps.append(float(res["lang_prob"]))
                if not lang_probs_first:
                    lang_probs_first = res["lang_probs"]
            if res["avg_logprob"] is not None:
                avg_lps.append(float(res["avg_logprob"]))
            if res["min_token_prob"] is not None:
                min_tps.append(float(res["min_token_prob"]))

        raw_text = " ".join(parts).strip()
        roman_text = DictationProcessor.devanagari_to_roman_hinglish(raw_text).strip() if raw_text else ""
        infer_sec = round(time.perf_counter() - t0, 4)
        return {
            "raw_text": raw_text,
            "roman_text": roman_text,
            "detected_language": langs[0] if langs else "",
            "language_probability": round(lps[0], 4) if lps else 0.0,
            "lang_probs": lang_probs_first,
            "avg_logprob": round(float(sum(avg_lps) / len(avg_lps)), 4) if avg_lps else None,
            "min_token_prob": round(float(min(min_tps)), 4) if min_tps else None,
            "infer_sec": infer_sec,
        }


def _smoke_test() -> int:
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    except Exception:
        pass

    manifest = json.loads((ROOT_DIR / "data" / "benchmarks" / "phase1_corpus" / "manifest.json").read_text(encoding="utf-8"))
    unguarded_path = ROOT_DIR / "data" / "benchmarks" / "phase1_results" / "onnx_qwen3_asr_06b_int8.json"
    ug = json.loads(unguarded_path.read_text(encoding="utf-8"))

    # Collect previously foreign-script-leaking clips from M#234 and M#235
    leak_ids: List[Tuple[str, str, str]] = []
    for trk in ("m234_full_90s", "m235_verified_111"):
        for r in ug["tracks"][trk]["clips"]:
            raw = str(r.get("raw_text") or "")
            if any(
                ("\u4e00" <= c <= "\u9fff") or ("\u0600" <= c <= "\u06ff") or ("\u3040" <= c <= "\u30ff") or ("\uac00" <= c <= "\ud7af")
                for c in raw
            ):
                leak_ids.append((trk, r["id"], raw))
                if len(leak_ids) >= 8:
                    break
        if len(leak_ids) >= 8:
            break

    dec = GuardedQwen3OnnxTranscriber(num_threads=4)
    dec.load()
    print(f"[Smoke] Loaded GuardedQwen3OnnxTranscriber in {dec.load_sec}s (blocked_ids={dec._blocked_ids.size})")

    c_en = manifest["tracks"]["fleurs_en_us"][0]
    res_en = dec.transcribe_array(load_wav_as_16k_f32(c_en["wav_path"]))
    print(f"[EN] {c_en['id']} dur={c_en['duration_sec']}s RTF={res_en['infer_sec']/c_en['duration_sec']:.3f}x avg_lp={res_en['avg_logprob']}")
    print(f"  REF: {c_en['reference_text']!r}")
    print(f"  HYP: {res_en['raw_text']!r}")

    c_m234 = manifest["tracks"]["m234_full_90s"][0]
    res_m234 = dec.transcribe_array(load_wav_as_16k_f32(c_m234["wav_path"]))
    print(f"[M234] {c_m234['id']} dur={c_m234['duration_sec']}s RTF={res_m234['infer_sec']/c_m234['duration_sec']:.3f}x lang={res_m234['detected_language']} avg_lp={res_m234['avg_logprob']}")
    print(f"  REF: {c_m234['reference_text']!r}")
    print(f"  HYP: {res_m234['raw_text']!r} (roman={res_m234['roman_text']!r})")

    foreign_remaining = 0
    print(f"\n[Leak Cure Test] Testing {len(leak_ids)} previously leaking M#234/M#235 clips:")
    for trk, cid, old_raw in leak_ids:
        clip = next(c for c in manifest["tracks"][trk] if c["id"] == cid)
        res = dec.transcribe_array(load_wav_as_16k_f32(clip["wav_path"]))
        has_foreign = any(
            ("\u4e00" <= c <= "\u9fff") or ("\u0600" <= c <= "\u06ff") or ("\u3040" <= c <= "\u30ff") or ("\uac00" <= c <= "\ud7af") or ("\u0400" <= c <= "\u04ff")
            for c in res["raw_text"]
        )
        if has_foreign:
            foreign_remaining += 1
        print(
            f"  {cid}: lang={res['detected_language']} avg_lp={res['avg_logprob']} "
            f"RTF={res['infer_sec']/clip['duration_sec']:.3f}x foreign={has_foreign}"
        )
        print(f"    OLD_UNGUARDED: {old_raw!r}")
        print(f"    NEW_GUARDED  : {res['raw_text']!r} | REF={clip['reference_text']!r}")

    print(f"\n[Smoke Result] foreign_remaining={foreign_remaining}/{len(leak_ids)}")
    return 1 if foreign_remaining > 0 else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke-test", action="store_true")
    args = ap.parse_args()
    if args.smoke_test:
        raise SystemExit(_smoke_test())

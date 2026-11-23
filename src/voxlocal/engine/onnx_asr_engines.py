"""
Phase 2 Production ONNX / Guarded / Incremental / Verifier ASR Backends.

Design invariants:
- ZERO eager imports of `torch` or `transformers` (safe inside PyInstaller bundles where
  PyTorch/Transformers are excluded).
- Pure-NumPy 128-bin Whisper/Qwen3 log-mel extractor matched to HuggingFace's feature
  extractor (`max_abs_diff < 1e-4`).
- Standalone Rust `tokenizers` BPE wrapper matching Qwen2 regex/added-token IDs identically.
- Strict separation between:
    * `NemotronStreamingEngine`: genuine frame-incremental English streaming (`streaming_mode="incremental"`)
    * `GuardedQwen3OnnxEngine`: CPU INT8 Hinglish/Multilingual guarded decoder (`streaming_mode="endpointed"`)
    * `ParakeetOnnxEngine`: CPU INT8 ultra-fast English engine (`streaming_mode="endpointed"`)
    * `OriserveApexVerifierEngine`: opportunistic CUDA-only Hinglish second-pass verifier
"""

from __future__ import annotations

import gc
import importlib.util
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.core.hardware import HardwareProfiler
from voxlocal.core.logger import get_app_root, get_logger
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    clean_repetition_tail,
    is_cuda_runtime_available,
    is_hallucination,
    is_likely_person_name,
    is_prompt_contamination_hallucination,
    load_wav_as_16k_f32,
)

logger = get_logger("engine.onnx_asr")

FOREIGN_TOKEN_RE = re.compile(
    r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F\uFF00-\uFFEF"
    r"\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
    r"\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF]"
)

MODEL_SLUGS: Dict[str, Tuple[str, ...]] = {
    "qwen3_onnx": ("sherpa-onnx-qwen3-asr-0.6b-int8",),
    "nemotron": ("sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8",),
    "parakeet": ("sherpa-onnx-parakeet-tdt-0.6b-v2-int8",),
    "oriserve_apex": ("oriserve-apex-ct2-int8",),
}

_QWEN2_SPLIT_PATTERN = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}|"
    r" ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)


def _sanitize_utf8_text(text: str) -> Tuple[str, bool]:
    s = str(text or "")
    had_rep = "\ufffd" in s
    if had_rep:
        s = re.sub(r"\s+", " ", s.replace("\ufffd", "")).strip()
    return s, had_rep


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


def _is_valid_model_dir(model_key: str, path: Path) -> bool:
    if not path.is_dir():
        return False
    if model_key == "qwen3_onnx":
        return all(
            (path / rel).is_file()
            for rel in (
                "conv_frontend.onnx",
                "encoder.int8.onnx",
                "decoder.int8.onnx",
                "tokenizer/vocab.json",
                "tokenizer/merges.txt",
            )
        )
    if model_key in ("nemotron", "parakeet"):
        has_tokens = (path / "tokens.txt").is_file()
        has_enc = any(path.glob("encoder*.onnx"))
        has_dec = any(path.glob("decoder*.onnx"))
        has_joi = any(path.glob("joiner*.onnx"))
        return bool(has_tokens and has_enc and has_dec and has_joi)
    if model_key == "oriserve_apex":
        return (path / "model.bin").is_file() and (path / "config.json").is_file()
    return False


def discover_phase2_models(models_dir: Optional[str | Path] = None) -> Dict[str, Optional[Path]]:
    """Locate Phase 2 local ONNX and CT2 model directories cleanly without network calls."""
    roots: List[Path] = []
    isolated_test_dir = False
    if models_dir:
        m_path = Path(models_dir)
        roots.append(m_path)
        s_low = str(m_path).lower()
        isolated_test_dir = any(tok in s_low for tok in ("temp", "tmp", "test_", "pytest", "scratch"))
    if not isolated_test_dir:
        app_data_models = get_app_root() / "data" / "models"
        if app_data_models not in roots:
            roots.append(app_data_models)

    found: Dict[str, Optional[Path]] = {k: None for k in MODEL_SLUGS}
    for model_key, slugs in MODEL_SLUGS.items():
        for root in roots:
            for slug in slugs:
                for cand in (root / "challengers" / slug, root / slug):
                    if _is_valid_model_dir(model_key, cand):
                        found[model_key] = cand
                        break
                if found[model_key] is not None:
                    break
            if found[model_key] is not None:
                break
    return found


def get_phase2_runtime_capabilities(models_dir: Optional[str | Path] = None) -> Dict[str, Any]:
    """Inspect installed runtime packages and local Phase 2 model directories cleanly."""
    def _has_mod(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False

    discovered = discover_phase2_models(models_dir)
    return {
        "onnxruntime": _has_mod("onnxruntime"),
        "tokenizers": _has_mod("tokenizers"),
        "sherpa_onnx": _has_mod("sherpa_onnx"),
        "faster_whisper": _has_mod("faster_whisper"),
        "models": {k: (str(v) if v is not None else None) for k, v in discovered.items()},
        "models_available": {k: (v is not None) for k, v in discovered.items()},
    }


# ---------------------------------------------------------------------------
# Pure-NumPy 128-bin Whisper / Qwen3 Log-Mel Feature Extractor
# ---------------------------------------------------------------------------
_MEL_FB_CACHE: Dict[Tuple[int, int, int], np.ndarray] = {}


def _hertz_to_mel_slaney(freq: Any) -> Any:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = 27.0 / np.log(6.4)
    arr = np.asarray(freq, dtype=np.float64)
    mels = 3.0 * arr / 200.0
    if np.ndim(arr) == 0:
        val = float(arr)
        return float(min_log_mel + np.log(val / min_log_hertz) * logstep) if val >= min_log_hertz else float(mels)
    log_region = arr >= min_log_hertz
    mels[log_region] = min_log_mel + np.log(arr[log_region] / min_log_hertz) * logstep
    return mels


def _mel_to_hertz_slaney(mels: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    m = np.asarray(mels, dtype=np.float64)
    freq = 200.0 * m / 3.0
    log_region = m >= min_log_mel
    freq[log_region] = min_log_hertz * np.exp(logstep * (m[log_region] - min_log_mel))
    return freq


def compute_qwen3_mel_filterbank(
    num_frequency_bins: int = 201,
    num_mel_filters: int = 128,
    sampling_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    key = (num_frequency_bins, num_mel_filters, sampling_rate)
    cached = _MEL_FB_CACHE.get(key)
    if cached is not None:
        return cached
    fft_freqs = np.linspace(0.0, float(sampling_rate) / 2.0, num_frequency_bins)
    mel_min = float(_hertz_to_mel_slaney(0.0))
    mel_max = float(_hertz_to_mel_slaney(float(sampling_rate) / 2.0))
    mel_freqs = np.linspace(mel_min, mel_max, num_mel_filters + 2)
    filter_freqs = _mel_to_hertz_slaney(mel_freqs)
    fdiff = np.diff(filter_freqs)
    slopes = np.expand_dims(filter_freqs, 0) - np.expand_dims(fft_freqs, 1)
    down_slopes = -slopes[:, :-2] / fdiff[:-1]
    up_slopes = slopes[:, 2:] / fdiff[1:]
    fb = np.maximum(0.0, np.minimum(down_slopes, up_slopes))
    enorm = 2.0 / (filter_freqs[2 : num_mel_filters + 2] - filter_freqs[:num_mel_filters])
    fb *= np.expand_dims(enorm, 0)
    fb_f32 = fb.astype(np.float32)
    _MEL_FB_CACHE[key] = fb_f32
    return fb_f32


def compute_qwen3_log_mel_features(audio_16k: np.ndarray) -> np.ndarray:
    """
    Compute Qwen3-ASR 128-mel spectrogram in pure NumPy.
    Matches HuggingFace Qwen3ASRFeatureExtractor (min_length=8000, right-padded to 100 frames)
    to < 1e-4 max abs error.
    Returns C-contiguous shape `(1, num_frames_padded_to_100, 128)` dtype `float32`.
    """
    wav = np.asarray(audio_16k, dtype=np.float32).reshape(-1)
    n_fft = 400
    hop_length = 160
    pad_len = n_fft // 2  # 200
    min_length = 8000
    n_window_mult = 100  # 2 * n_window (n_window=50 in Qwen3ASRFeatureExtractor)
    if wav.size == 0:
        return np.zeros((1, 0, 128), dtype=np.float32)
    if wav.size < min_length:
        wav = np.pad(wav, (0, min_length - wav.size), mode="constant")
    padded = np.pad(wav, (pad_len, pad_len), mode="reflect")
    window = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    n_frames = 1 + (padded.shape[0] - n_fft) // hop_length
    if n_frames <= 1:
        return np.zeros((1, 0, 128), dtype=np.float32)
    strides = (padded.strides[0] * hop_length, padded.strides[0])
    frames = np.lib.stride_tricks.as_strided(padded, shape=(n_frames, n_fft), strides=strides)
    win_frames = frames * window[None, :]
    spec = np.fft.rfft(win_frames, n=n_fft, axis=1)
    mags = (np.abs(spec[:-1, :]) ** 2).T.astype(np.float32)  # (201, n_frames - 1)
    fb = compute_qwen3_mel_filterbank(num_frequency_bins=201, num_mel_filters=128, sampling_rate=TARGET_SAMPLE_RATE)
    mel = fb.T @ mags
    log_spec = np.log10(np.maximum(mel, np.float32(1e-10))).astype(np.float32)
    log_spec = np.maximum(log_spec, log_spec.max() - np.float32(8.0))
    log_spec = (log_spec + np.float32(4.0)) / np.float32(4.0)
    rem = int(log_spec.shape[1] % n_window_mult)
    if rem:
        log_spec = np.pad(log_spec, [(0, 0), (0, n_window_mult - rem)], mode="constant")
    return np.ascontiguousarray(log_spec[None, :, :].transpose(0, 2, 1), dtype=np.float32)


# ---------------------------------------------------------------------------
# Standalone Zero-Transformers Qwen2/Qwen3 BPE Tokenizer Wrapper
# ---------------------------------------------------------------------------
class _Qwen3StandaloneTokenizer:
    """Loads Qwen2/Qwen3 vocab + merges + added tokens strictly via `tokenizers`."""

    def __init__(self, tok_dir: Path) -> None:
        from tokenizers import AddedToken, Tokenizer, decoders, normalizers, pre_tokenizers
        from tokenizers.models import BPE

        vocab_p = tok_dir / "vocab.json"
        merges_p = tok_dir / "merges.txt"
        tok_cfg_p = tok_dir / "tokenizer_config.json"
        if not vocab_p.is_file() or not merges_p.is_file():
            raise FileNotFoundError(f"Missing vocab.json or merges.txt in {tok_dir}")

        model = BPE.from_file(str(vocab_p), str(merges_p))
        tok = Tokenizer(model)
        tok.normalizer = normalizers.NFC()

        split_pat = _QWEN2_SPLIT_PATTERN
        added: Dict[str, Any] = {}
        if tok_cfg_p.is_file():
            tc = json.loads(tok_cfg_p.read_text(encoding="utf-8"))
            added = tc.get("added_tokens_decoder", {}) or {}

        tok.pre_tokenizer = pre_tokenizers.Sequence(
            [
                pre_tokenizers.Split(pattern=split_pat, behavior="isolated", invert=False),
                pre_tokenizers.ByteLevel(add_prefix_space=False, trim_offsets=True, use_regex=False),
            ]
        )
        tok.decoder = decoders.ByteLevel()

        for _, meta in sorted(added.items(), key=lambda x: int(x[0])):
            at = AddedToken(
                meta["content"],
                special=bool(meta.get("special", True)),
                lstrip=bool(meta.get("lstrip", False)),
                rstrip=bool(meta.get("rstrip", False)),
                normalized=bool(meta.get("normalized", False)),
                single_word=bool(meta.get("single_word", False)),
            )
            if meta.get("special", True):
                tok.add_special_tokens([at])
            else:
                tok.add_tokens([at])

        self._tok = tok

    def __len__(self) -> int:
        return int(self._tok.get_vocab_size())

    def encode(self, text: str, add_special_tokens: bool = False) -> List[int]:
        return list(self._tok.encode(text, add_special_tokens=add_special_tokens).ids)

    def decode(self, ids: List[int], skip_special_tokens: bool = False) -> str:
        return str(self._tok.decode([int(i) for i in ids], skip_special_tokens=skip_special_tokens))

    def convert_tokens_to_ids(self, token: str) -> Optional[int]:
        return self._tok.token_to_id(token)


def build_foreign_script_token_ids_from_tokenizer(
    tokenizer: Any,
    keep_ids: Tuple[int, ...] = (151643, 151645, 151704),
) -> List[int]:
    keep_set = set(keep_ids)
    blocked: List[int] = []
    vocab_n = len(tokenizer)
    for tid in range(vocab_n):
        if tid in keep_set:
            continue
        piece = tokenizer.decode([tid], skip_special_tokens=False)
        if piece and FOREIGN_TOKEN_RE.search(piece):
            blocked.append(tid)
    return blocked


def _extract_spans_with_hard_cap(
    audio_16k: np.ndarray,
    *,
    max_chunk_sec: float = 22.0,
    hard_cap_sec: float = 26.0,
) -> List[Tuple[int, int]]:
    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
        audio_16k, vad_p, max_chunk_sec=max_chunk_sec, merge_gap_sec=0.65, pad_sec=0.20
    )
    if spans is None:
        spans = [(0, int(audio_16k.size))]
    elif spans and spans[-1][1] < audio_16k.size:
        tail_s = int(spans[-1][1])
        tail_arr = audio_16k[tail_s:]
        if tail_arr.size >= int(0.25 * TARGET_SAMPLE_RATE) and float(np.max(np.abs(tail_arr))) >= 0.0005:
            spans = list(spans) + [(tail_s, int(audio_16k.size))]

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


# ---------------------------------------------------------------------------
# Engine 1: Guarded Qwen3-ASR 0.6B ONNX INT8 (CPU, 0 VRAM, Endpointed)
# ---------------------------------------------------------------------------
class GuardedQwen3OnnxEngine:
    """
    Production ONNX Runtime guarded greedy KV-cached decoder for Qwen3-ASR 0.6B INT8.
    Explicitly endpointed (`streaming_mode = "endpointed"`): runs on CPU with 0 VRAM.
    """

    streaming_mode: str = "endpointed"
    asr_backend: str = "qwen3_onnx"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    model_label: str = "qwen3-asr-0.6b-onnx-int8"

    def __init__(
        self,
        onnx_dir: Optional[str | Path] = None,
        models_dir: Optional[str | Path] = None,
        num_threads: int = 4,
        repetition_penalty: float = 1.05,
    ) -> None:
        self._explicit_dir = Path(onnx_dir) if onnx_dir else None
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        self.num_threads = max(1, int(num_threads))
        self.repetition_penalty = float(repetition_penalty)

        self._lock = threading.RLock()
        self._resolved_dir: Optional[Path] = None
        self._conv_sess: Any = None
        self._enc_sess: Any = None
        self._dec_sess: Any = None
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
        self._last_track_stats: Dict[str, Dict[str, Any]] = {}

    @property
    def is_loaded(self) -> bool:
        return self._dec_sess is not None and self._enc_sess is not None and self._conv_sess is not None

    @property
    def last_track_stats(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self._last_track_stats.items()}

    def get_active_asr_provenance(self) -> Dict[str, str]:
        return {
            "asr_model": self.model_label,
            "asr_backend": self.asr_backend,
            "asr_device": self.asr_device,
            "asr_compute_type": self.asr_compute_type,
            "execution_mode": self.streaming_mode,
        }

    def _resolve_dir(self) -> Optional[Path]:
        if self._explicit_dir is not None:
            return self._explicit_dir if _is_valid_model_dir("qwen3_onnx", self._explicit_dir) else None
        return discover_phase2_models(self.models_dir).get("qwen3_onnx")

    def ensure_loaded(self) -> Tuple[bool, str]:
        with self._lock:
            if self.is_loaded:
                return True, f"Loaded {self.model_label} on CPU INT8"
            target_dir = self._resolve_dir()
            if target_dir is None:
                return False, "Local sherpa-onnx-qwen3-asr-0.6b-int8 directory not found."
            t0 = time.perf_counter()
            try:
                import onnxruntime as ort
            except Exception as exc:
                return False, f"onnxruntime unavailable: {exc}"

            try:
                tok = _Qwen3StandaloneTokenizer(target_dir / "tokenizer")
                blocked_list = build_foreign_script_token_ids_from_tokenizer(tok)
                blocked_arr = np.array(blocked_list, dtype=np.int64)

                lang_word_id = tok.encode("language", add_special_tokens=False)[0]
                allowed_lang_ids = np.array(
                    [
                        tok.encode(" None", add_special_tokens=False)[0],
                        tok.encode(" English", add_special_tokens=False)[0],
                        tok.encode(" Hindi", add_special_tokens=False)[0],
                    ],
                    dtype=np.int64,
                )
                asr_text_id = tok.encode("<asr_text>", add_special_tokens=False)[0]
                im_start = int(tok.convert_tokens_to_ids("<|im_start|>") or 151644)
                im_end = int(tok.convert_tokens_to_ids("<|im_end|>") or 151645)
                audio_start = int(tok.convert_tokens_to_ids("<|audio_start|>") or 151669)
                audio_pad = int(tok.convert_tokens_to_ids("<|audio_pad|>") or 151676)
                audio_end = int(tok.convert_tokens_to_ids("<|audio_end|>") or 151670)
                eot_id = int(tok.convert_tokens_to_ids("<|endoftext|>") or 151643)
                nl_ids = tok.encode("\n", add_special_tokens=False)

                so = ort.SessionOptions()
                so.intra_op_num_threads = self.num_threads
                so.inter_op_num_threads = 1
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                conv_sess = ort.InferenceSession(
                    str(target_dir / "conv_frontend.onnx"),
                    sess_options=so,
                    providers=["CPUExecutionProvider"],
                )
                enc_sess = ort.InferenceSession(
                    str(target_dir / "encoder.int8.onnx"),
                    sess_options=so,
                    providers=["CPUExecutionProvider"],
                )
                dec_sess = ort.InferenceSession(
                    str(target_dir / "decoder.int8.onnx"),
                    sess_options=so,
                    providers=["CPUExecutionProvider"],
                )

                self._tok = tok
                self._blocked_ids = blocked_arr
                self._lang_word_id = lang_word_id
                self._allowed_lang_ids = allowed_lang_ids
                self._asr_text_id = asr_text_id
                self._im_start = im_start
                self._im_end = im_end
                self._audio_start = audio_start
                self._audio_pad = audio_pad
                self._audio_end = audio_end
                self._eos_ids = {im_end, eot_id}
                self._nl_ids = nl_ids
                self._sys_prefix_ids = [im_start] + tok.encode("system\n", add_special_tokens=False)
                self._user_prefix_ids = (
                    [im_end] + nl_ids + [im_start] + tok.encode("user\n", add_special_tokens=False) + [audio_start]
                )
                self._assistant_suffix_ids = (
                    [audio_end, im_end] + nl_ids + [im_start] + tok.encode("assistant\n", add_special_tokens=False)
                )
                self._conv_sess = conv_sess
                self._enc_sess = enc_sess
                self._dec_sess = dec_sess
                self._resolved_dir = target_dir
                self.load_sec = round(time.perf_counter() - t0, 3)
                logger.info("Loaded %s from %s in %.2fs (CPU threads=%d)", self.model_label, target_dir, self.load_sec, self.num_threads)
                return True, f"Loaded {self.model_label} on CPU INT8 ({self.load_sec:.2f}s)"
            except Exception as exc:
                self.unload_model()
                return False, f"Failed loading {self.model_label}: {exc}"

    def unload_model(self) -> None:
        with self._lock:
            self._conv_sess = None
            self._enc_sess = None
            self._dec_sess = None
            self._tok = None
            self._blocked_ids = None
            gc.collect()

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
        with self._lock:
            ok, err = self.ensure_loaded()
            if not ok:
                raise RuntimeError(err)

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
            feats = compute_qwen3_log_mel_features(norm)  # (1, T, 128)
            if feats.shape[1] < 2:
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

            # IMPORTANT: dummy_af MUST have length 1 (never 0) to satisfy decoder GatherElements node
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
            cleaned_raw, had_rep = _sanitize_utf8_text(raw_dec)
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
        output_script: str = "roman",
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        ok, err = self.ensure_loaded()
        if not ok:
            raise RuntimeError(err)
        t0 = time.perf_counter()
        if audio_16k.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(audio_16k))) < 0.0004:
            return {
                "raw_text": "",
                "text": "",
                "roman_text": "",
                "detected_language": "",
                "language_probability": 0.0,
                "lang_probs": {},
                "avg_logprob": None,
                "min_token_prob": None,
                "num_tokens": 0,
                "had_utf8_replacement": False,
                "infer_sec": round(time.perf_counter() - t0, 4),
            }

        max_win_samples = int(26.0 * TARGET_SAMPLE_RATE)
        if audio_16k.size <= max_win_samples:
            wins = [self._decode_single_window(audio_16k, hotwords=hotwords, max_new_tokens=max_new_tokens)]
        else:
            spans = _extract_spans_with_hard_cap(audio_16k, max_chunk_sec=22.0, hard_cap_sec=26.0)
            wins = []
            for s_i, e_i in spans:
                sl = audio_16k[s_i:e_i]
                if sl.size < int(0.20 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0004:
                    continue
                wins.append(self._decode_single_window(sl, hotwords=hotwords, max_new_tokens=max_new_tokens))

        raw_parts = [w["raw_text"] for w in wins if w["raw_text"]]
        raw_full = " ".join(raw_parts).strip()
        if raw_full and output_script == "roman":
            rom_full = DictationProcessor.process(
                raw_full,
                vocabulary=vocabulary,
                cleanup_enabled=False,
                spoken_punctuation=False,
                output_script="roman",
                mode="verbatim",
                context="meeting",
            ).strip()
        else:
            rom_full = raw_full
        rom_full = clean_repetition_tail(rom_full).strip()

        if hotwords and raw_full:
            raw_rms = float(np.sqrt(np.mean(np.square(audio_16k))))
            if is_prompt_contamination_hallucination(
                raw_full, initial_prompt=hotwords, duration_sec=audio_16k.size / float(TARGET_SAMPLE_RATE), audio_rms=raw_rms
            ):
                return self.transcribe_array(
                    audio_16k,
                    hotwords=None,
                    max_new_tokens=max_new_tokens,
                    output_script=output_script,
                    vocabulary=vocabulary,
                )

        lps = [w["avg_logprob"] for w in wins if w["avg_logprob"] is not None]
        mins = [w["min_token_prob"] for w in wins if w["min_token_prob"] is not None]
        lang_votes: Dict[str, int] = {}
        for w in wins:
            dl = w["detected_language"]
            if dl:
                lang_votes[dl] = lang_votes.get(dl, 0) + max(1, int(w["num_tokens"]))
        det_lang = max(lang_votes.items(), key=lambda x: x[1])[0] if lang_votes else ""

        return {
            "raw_text": raw_full,
            "text": rom_full,
            "roman_text": rom_full,
            "detected_language": det_lang,
            "language_probability": max((w["lang_prob"] for w in wins), default=0.0),
            "lang_probs": wins[0]["lang_probs"] if wins else {},
            "avg_logprob": round(float(sum(lps) / len(lps)), 4) if lps else None,
            "min_token_prob": round(float(min(mins)), 4) if mins else None,
            "num_tokens": int(sum(w["num_tokens"] for w in wins)),
            "had_utf8_replacement": any(w["had_utf8_replacement"] for w in wins),
            "infer_sec": round(time.perf_counter() - t0, 4),
        }

    def _transcribe_track(
        self,
        wav_path: str,
        *,
        speaker_label: str,
        source_track: str,
        hotwords: Optional[str] = None,
        output_script: str = "roman",
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        eff_path = LocalWhisperTranscriber._prefer_raw_wav_path(wav_path)
        aud = load_wav_as_16k_f32(eff_path)
        total_samples = int(aud.size)
        dur_sec = float(total_samples) / float(TARGET_SAMPLE_RATE) if total_samples > 0 else 0.0
        stats: Dict[str, Any] = {
            "wav_path": eff_path,
            "samples_captured": total_samples,
            "samples_processed": 0,
            "duration_sec": round(dur_sec, 3),
            "cursor_sec": 0.0,
            "cancelled": False,
            "error": "",
        }
        if total_samples == 0:
            return [], stats

        spans = _extract_spans_with_hard_cap(aud, max_chunk_sec=22.0, hard_cap_sec=26.0)
        segments: List[Dict[str, Any]] = []
        last_processed_idx = 0

        for idx, (s_i, e_i) in enumerate(spans):
            if cancel_check and cancel_check():
                stats["cancelled"] = True
                break
            while pause_check and pause_check():
                if cancel_check and cancel_check():
                    stats["cancelled"] = True
                    break
                time.sleep(0.15)
            if stats["cancelled"]:
                break

            last_processed_idx = max(last_processed_idx, int(e_i))
            sl = aud[s_i:e_i]
            if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue

            if progress_callback and idx % 3 == 0:
                pct = min(100, int((e_i / max(1, total_samples)) * 100))
                progress_callback(f"{source_track} Qwen3-ONNX {pct}%")

            s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
            e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
            res = self.transcribe_array(
                sl,
                hotwords=hotwords,
                output_script=output_script,
                vocabulary=vocabulary,
            )
            raw_txt = str(res.get("raw_text") or "").strip()
            disp_txt = str(res.get("text") or "").strip()
            if not disp_txt or is_hallucination(disp_txt, duration_sec=max(0.1, e_sec - s_sec), initial_prompt=""):
                continue

            avg_lp = res.get("avg_logprob")
            # Derive approximate heuristic confidence only for sorting/display (explicitly uncalibrated)
            approx_conf = round(float(min(0.95, max(0.25, np.exp(float(avg_lp))))), 3) if avg_lp is not None else 0.80
            had_rep = bool(res.get("had_utf8_replacement", False))
            segments.append(
                {
                    "start_time": s_sec,
                    "end_time": e_sec,
                    "start": s_sec,
                    "end": e_sec,
                    "speaker": speaker_label,
                    "speaker_cluster": speaker_label,
                    "source_track": source_track,
                    "raw_text": raw_txt or disp_txt,
                    "normalized_text": disp_txt,
                    "corrected_text": "",
                    "text": disp_txt,
                    "asr_model": self.model_label,
                    "asr_backend": self.asr_backend,
                    "asr_device": self.asr_device,
                    "asr_compute_type": self.asr_compute_type,
                    "transcript_stage": "offline",
                    "transcript_state": "offline",
                    "raw_model_score": avg_lp,
                    "confidence": 0.50 if had_rep else approx_conf,
                    "confidence_calibrated": False,
                    "detected_language": str(res.get("detected_language") or ""),
                    "is_uncertain": had_rep,
                    "uncertainty_reasons": ["utf8_replacement_repaired"] if had_rep else [],
                    "recognition_params": {
                        "streaming_mode": self.streaming_mode,
                        "repetition_penalty": self.repetition_penalty,
                        "min_token_prob": res.get("min_token_prob"),
                    },
                    "processing_history": [
                        {
                            "stage": "offline_primary",
                            "model": self.model_label,
                            "backend": self.asr_backend,
                            "device": self.asr_device,
                            "raw_model_score": avg_lp,
                            "detected_language": str(res.get("detected_language") or ""),
                        }
                    ],
                }
            )

        if not stats["cancelled"]:
            last_processed_idx = total_samples
        stats["samples_processed"] = int(last_processed_idx)
        stats["cursor_sec"] = round(float(last_processed_idx) / float(TARGET_SAMPLE_RATE), 3)
        return segments, stats

    def transcribe_meeting_dual_track(
        self,
        mixed_wav_path: str,
        mic_wav_path: str = "",
        sys_wav_path: str = "",
        *,
        hotwords: Optional[str] = None,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        output_script: str = "roman",
        remote_speaker_label: str = "Speaker 1",
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        self._last_track_stats = {}
        ok, msg = self.ensure_loaded()
        if not ok:
            raise RuntimeError(msg)

        has_mic = bool(mic_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(mic_wav_path)))
        has_sys = bool(sys_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(sys_wav_path)))
        eff_remote = (remote_speaker_label or "Speaker 1").strip() or "Speaker 1"
        has_name = eff_remote != "Speaker 1" and is_likely_person_name(eff_remote)
        all_segs: List[Dict[str, Any]] = []

        if has_sys or has_mic:
            sys_segs: List[Dict[str, Any]] = []
            if has_sys:
                sys_segs, sys_stats = self._transcribe_track(
                    sys_wav_path,
                    speaker_label=eff_remote if has_name else "Speaker 1",
                    source_track="system",
                    hotwords=None,  # Phase 1.5 proved hotwords on Qwen3 ONNX hurt M#234 WER (42.38% -> 49.39%)
                    output_script=output_script,
                    vocabulary=vocabulary,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    pause_check=pause_check,
                )
                self._last_track_stats["system"] = sys_stats
                all_segs.extend(sys_segs)

            if has_mic and not (cancel_check and cancel_check()):
                mic_segs, mic_stats = self._transcribe_track(
                    mic_wav_path,
                    speaker_label="You (Mic)",
                    source_track="mic",
                    hotwords=None,
                    output_script=output_script,
                    vocabulary=vocabulary,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    pause_check=pause_check,
                )
                self._last_track_stats["mic"] = mic_stats
                for m_seg in mic_segs:
                    m_txt = m_seg["text"].strip().lower()
                    is_bleed = False
                    if len(m_txt) >= 12:
                        for s_seg in sys_segs:
                            s_txt = s_seg["text"].strip().lower()
                            if len(s_txt) >= 12 and abs(float(m_seg["start_time"]) - float(s_seg["start_time"])) < 2.0:
                                if m_txt == s_txt or (m_txt in s_txt and len(m_txt) > 0.65 * len(s_txt)):
                                    is_bleed = True
                                    break
                    if not is_bleed:
                        all_segs.append(m_seg)

            all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            return all_segs

        if mixed_wav_path and os.path.exists(mixed_wav_path):
            mix_segs, mix_stats = self._transcribe_track(
                mixed_wav_path,
                speaker_label="Speaker 1",
                source_track="mixed",
                hotwords=None,
                output_script=output_script,
                vocabulary=vocabulary,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                pause_check=pause_check,
            )
            self._last_track_stats["mixed"] = mix_stats
            return mix_segs

        return []


# ---------------------------------------------------------------------------
# Engine 2: Nemotron 560ms Streaming English ONNX INT8 (True Incremental)
# ---------------------------------------------------------------------------
class NemotronStreamingEngine:
    """
    Genuine frame-incremental streaming English recognizer using sherpa-onnx OnlineRecognizer
    (Nemotron Streaming 560ms INT8, CPU, 0 VRAM). Keeps completely isolated per-track streams.
    """

    streaming_mode: str = "incremental"
    asr_backend: str = "nemotron_onnx"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    model_label: str = "nemotron-streaming-en-0.6b-560ms-int8"

    def __init__(
        self,
        model_dir: Optional[str | Path] = None,
        models_dir: Optional[str | Path] = None,
        num_threads: int = 4,
    ) -> None:
        self._explicit_dir = Path(model_dir) if model_dir else None
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        self.num_threads = max(1, int(num_threads))
        self._lock = threading.RLock()
        self._recognizer: Any = None
        self._streams: Dict[str, Any] = {}
        self.load_sec: float = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._recognizer is not None

    def get_active_asr_provenance(self) -> Dict[str, str]:
        return {
            "asr_model": self.model_label,
            "asr_backend": self.asr_backend,
            "asr_device": self.asr_device,
            "asr_compute_type": self.asr_compute_type,
            "execution_mode": self.streaming_mode,
        }

    def _resolve_dir(self) -> Optional[Path]:
        if self._explicit_dir is not None:
            return self._explicit_dir if _is_valid_model_dir("nemotron", self._explicit_dir) else None
        return discover_phase2_models(self.models_dir).get("nemotron")

    def ensure_loaded(self) -> Tuple[bool, str]:
        with self._lock:
            if self._recognizer is not None:
                return True, f"Loaded {self.model_label} on CPU INT8"
            target_dir = self._resolve_dir()
            if target_dir is None:
                return False, "Local sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8 directory not found."
            t0 = time.perf_counter()
            try:
                import sherpa_onnx
            except Exception as exc:
                return False, f"sherpa_onnx unavailable: {exc}"

            try:
                enc = str(next(target_dir.glob("encoder*.onnx")))
                dec = str(next(target_dir.glob("decoder*.onnx")))
                joi = str(next(target_dir.glob("joiner*.onnx")))
                tok = str(target_dir / "tokens.txt")
                rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=tok,
                    encoder=enc,
                    decoder=dec,
                    joiner=joi,
                    model_type="nemo_transducer",
                    num_threads=self.num_threads,
                    sample_rate=TARGET_SAMPLE_RATE,
                    feature_dim=128,
                    normalize_samples=True,
                    enable_endpoint_detection=True,
                    rule1_min_trailing_silence=2.0,
                    rule2_min_trailing_silence=0.9,
                    rule3_min_utterance_length=18.0,
                    decoding_method="greedy_search",
                    provider="cpu",
                )
                self._recognizer = rec
                self.load_sec = round(time.perf_counter() - t0, 3)
                logger.info("Loaded %s in %.2fs (CPU threads=%d)", self.model_label, self.load_sec, self.num_threads)
                return True, f"Loaded {self.model_label} on CPU INT8 ({self.load_sec:.2f}s)"
            except Exception as exc:
                self.unload_model()
                return False, f"Failed loading {self.model_label}: {exc}"

    def unload_model(self) -> None:
        with self._lock:
            self._streams.clear()
            self._recognizer = None
            gc.collect()

    def create_stream(self, track_id: str) -> Any:
        return self.create_track_stream(track_id)

    def create_track_stream(self, track_id: str) -> Any:
        with self._lock:
            ok, err = self.ensure_loaded()
            if not ok:
                raise RuntimeError(err)
            st = self._recognizer.create_stream()
            self._streams[track_id] = st
            return st

    def reset_stream(self, track_id: str) -> None:
        self.reset_track_stream(track_id)

    def reset_track_stream(self, track_id: str) -> None:
        with self._lock:
            st = self._streams.get(track_id)
            if st is not None and self._recognizer is not None:
                self._recognizer.reset(st)

    def clear_all_streams(self) -> None:
        with self._lock:
            self._streams.clear()

    def feed_audio(self, track_id: str, samples_16k: np.ndarray) -> Dict[str, Any]:
        with self._lock:
            ok, err = self.ensure_loaded()
            if not ok:
                raise RuntimeError(err)
            st = self._streams.get(track_id)
            if st is None:
                st = self._recognizer.create_stream()
                self._streams[track_id] = st
            arr = np.asarray(samples_16k, dtype=np.float32).reshape(-1)
            if arr.size > 0:
                st.accept_waveform(TARGET_SAMPLE_RATE, arr.tolist())
            while self._recognizer.is_ready(st):
                self._recognizer.decode_stream(st)
            txt = str(self._recognizer.get_result(st) or "").strip()
            is_ep = bool(self._recognizer.is_endpoint(st))
            return {
                "text": txt,
                "is_endpoint": is_ep,
                "is_incremental": True,
                "execution_mode": self.streaming_mode,
            }

    def flush_stream(self, track_id: str) -> str:
        return self.flush_track_stream(track_id)

    def flush_track_stream(self, track_id: str) -> str:
        with self._lock:
            st = self._streams.get(track_id)
            if st is None or self._recognizer is None:
                return ""
            st.input_finished()
            tail = np.zeros(int(0.4 * TARGET_SAMPLE_RATE), dtype=np.float32)
            st.accept_waveform(TARGET_SAMPLE_RATE, tail.tolist())
            while self._recognizer.is_ready(st):
                self._recognizer.decode_stream(st)
            txt = str(self._recognizer.get_result(st) or "").strip()
            self._recognizer.reset(st)
            return txt


# ---------------------------------------------------------------------------
# Engine 3: Parakeet TDT v2 ONNX INT8 (CPU, 0 VRAM, Endpointed English)
# ---------------------------------------------------------------------------
class ParakeetOnnxEngine:
    """Fast CPU INT8 English-only endpointed recognizer (0.07-0.09x RTF)."""

    streaming_mode: str = "endpointed"
    asr_backend: str = "parakeet_onnx"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    model_label: str = "parakeet-tdt-0.6b-v2-onnx-int8"

    def __init__(
        self,
        model_dir: Optional[str | Path] = None,
        models_dir: Optional[str | Path] = None,
        num_threads: int = 4,
    ) -> None:
        self._explicit_dir = Path(model_dir) if model_dir else None
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        self.num_threads = max(1, int(num_threads))
        self._lock = threading.RLock()
        self._recognizer: Any = None
        self.load_sec: float = 0.0
        self._last_track_stats: Dict[str, Dict[str, Any]] = {}

    @property
    def is_loaded(self) -> bool:
        return self._recognizer is not None

    @property
    def last_track_stats(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self._last_track_stats.items()}

    def get_active_asr_provenance(self) -> Dict[str, str]:
        return {
            "asr_model": self.model_label,
            "asr_backend": self.asr_backend,
            "asr_device": self.asr_device,
            "asr_compute_type": self.asr_compute_type,
            "execution_mode": self.streaming_mode,
        }

    def _resolve_dir(self) -> Optional[Path]:
        if self._explicit_dir is not None:
            return self._explicit_dir if _is_valid_model_dir("parakeet", self._explicit_dir) else None
        return discover_phase2_models(self.models_dir).get("parakeet")

    def ensure_loaded(self) -> Tuple[bool, str]:
        with self._lock:
            if self._recognizer is not None:
                return True, f"Loaded {self.model_label} on CPU INT8"
            target_dir = self._resolve_dir()
            if target_dir is None:
                return False, "Local sherpa-onnx-parakeet-tdt-0.6b-v2-int8 directory not found."
            t0 = time.perf_counter()
            try:
                import sherpa_onnx
            except Exception as exc:
                return False, f"sherpa_onnx unavailable: {exc}"
            try:
                enc = str(next(target_dir.glob("encoder*.onnx")))
                dec = str(next(target_dir.glob("decoder*.onnx")))
                joi = str(next(target_dir.glob("joiner*.onnx")))
                tok = str(target_dir / "tokens.txt")
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=enc,
                    decoder=dec,
                    joiner=joi,
                    tokens=tok,
                    model_type="nemo_transducer",
                    num_threads=self.num_threads,
                    sample_rate=TARGET_SAMPLE_RATE,
                    feature_dim=128,
                    decoding_method="greedy_search",
                    provider="cpu",
                )
                self.load_sec = round(time.perf_counter() - t0, 3)
                logger.info("Loaded %s in %.2fs (CPU threads=%d)", self.model_label, self.load_sec, self.num_threads)
                return True, f"Loaded {self.model_label} on CPU INT8 ({self.load_sec:.2f}s)"
            except Exception as exc:
                self.unload_model()
                return False, f"Failed loading {self.model_label}: {exc}"

    def unload_model(self) -> None:
        with self._lock:
            self._recognizer = None
            gc.collect()

    def _decode_slice(self, audio_16k: np.ndarray) -> str:
        with self._lock:
            ok, err = self.ensure_loaded()
            if not ok:
                raise RuntimeError(err)
            if audio_16k.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(audio_16k))) < 0.0004:
                return ""
            norm = normalize_speech_audio(audio_16k)
            pad = np.zeros(3200, dtype=np.float32)
            st = self._recognizer.create_stream()
            st.accept_waveform(TARGET_SAMPLE_RATE, np.concatenate([norm, pad]).tolist())
            self._recognizer.decode_stream(st)
            raw = str(getattr(st.result, "text", "") or "").strip()
            return clean_repetition_tail(raw)

    def transcribe_array(
        self,
        audio_16k: np.ndarray,
        *,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        output_script: str = "roman",
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if audio_16k.size <= int(26.0 * TARGET_SAMPLE_RATE):
            raw = self._decode_slice(audio_16k)
        else:
            spans = _extract_spans_with_hard_cap(audio_16k, max_chunk_sec=22.0, hard_cap_sec=26.0)
            parts = []
            for s_i, e_i in spans:
                sl = audio_16k[s_i:e_i]
                if sl.size >= int(0.20 * TARGET_SAMPLE_RATE) and float(np.max(np.abs(sl))) >= 0.0004:
                    t = self._decode_slice(sl)
                    if t:
                        parts.append(t)
            raw = " ".join(parts).strip()

        disp = (
            DictationProcessor.process(
                raw,
                vocabulary=vocabulary,
                cleanup_enabled=False,
                spoken_punctuation=False,
                output_script=output_script,
                mode="verbatim",
                context="meeting",
            ).strip()
            if raw
            else ""
        )
        return {
            "raw_text": raw,
            "text": clean_repetition_tail(disp).strip(),
            "detected_language": "en",
            "avg_logprob": None,
            "infer_sec": round(time.perf_counter() - t0, 4),
        }

    def transcribe_meeting_dual_track(
        self,
        mixed_wav_path: str,
        mic_wav_path: str = "",
        sys_wav_path: str = "",
        *,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        output_script: str = "roman",
        remote_speaker_label: str = "Speaker 1",
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        self._last_track_stats = {}
        ok, msg = self.ensure_loaded()
        if not ok:
            raise RuntimeError(msg)

        has_mic = bool(mic_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(mic_wav_path)))
        has_sys = bool(sys_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(sys_wav_path)))
        eff_remote = (remote_speaker_label or "Speaker 1").strip() or "Speaker 1"
        has_name = eff_remote != "Speaker 1" and is_likely_person_name(eff_remote)

        def _do_track(wp: str, spk: str, trk: str) -> List[Dict[str, Any]]:
            eff_p = LocalWhisperTranscriber._prefer_raw_wav_path(wp)
            aud = load_wav_as_16k_f32(eff_p)
            tot = int(aud.size)
            dur = float(tot) / float(TARGET_SAMPLE_RATE) if tot > 0 else 0.0
            stats: Dict[str, Any] = {
                "wav_path": eff_p,
                "samples_captured": tot,
                "samples_processed": 0,
                "duration_sec": round(dur, 3),
                "cursor_sec": 0.0,
                "cancelled": False,
                "error": "",
            }
            out_segs: List[Dict[str, Any]] = []
            last_i = 0
            if tot > 0:
                spans = _extract_spans_with_hard_cap(aud, max_chunk_sec=22.0, hard_cap_sec=26.0)
                for i, (s_i, e_i) in enumerate(spans):
                    if cancel_check and cancel_check():
                        stats["cancelled"] = True
                        break
                    while pause_check and pause_check():
                        if cancel_check and cancel_check():
                            stats["cancelled"] = True
                            break
                        time.sleep(0.15)
                    if stats["cancelled"]:
                        break
                    last_i = max(last_i, int(e_i))
                    sl = aud[s_i:e_i]
                    if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                        continue
                    if progress_callback and i % 4 == 0:
                        progress_callback(f"{trk} Parakeet-ONNX {min(100, int((e_i / max(1, tot)) * 100))}%")
                    s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
                    e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
                    res = self.transcribe_array(sl, vocabulary=vocabulary, output_script=output_script)
                    txt = str(res.get("text") or "").strip()
                    if not txt or is_hallucination(txt, duration_sec=max(0.1, e_sec - s_sec), initial_prompt=""):
                        continue
                    out_segs.append(
                        {
                            "start_time": s_sec,
                            "end_time": e_sec,
                            "start": s_sec,
                            "end": e_sec,
                            "speaker": spk,
                            "speaker_cluster": spk,
                            "source_track": trk,
                            "raw_text": str(res.get("raw_text") or txt),
                            "normalized_text": txt,
                            "corrected_text": "",
                            "text": txt,
                            "asr_model": self.model_label,
                            "asr_backend": self.asr_backend,
                            "asr_device": self.asr_device,
                            "asr_compute_type": self.asr_compute_type,
                            "transcript_stage": "offline",
                            "transcript_state": "offline",
                            "raw_model_score": None,
                            "confidence": 0.85,
                            "confidence_calibrated": False,
                            "detected_language": "en",
                            "is_uncertain": False,
                            "uncertainty_reasons": [],
                            "processing_history": [
                                {
                                    "stage": "offline_primary",
                                    "model": self.model_label,
                                    "backend": self.asr_backend,
                                    "device": self.asr_device,
                                    "detected_language": "en",
                                }
                            ],
                        }
                    )
            if not stats["cancelled"]:
                last_i = tot
            stats["samples_processed"] = int(last_i)
            stats["cursor_sec"] = round(float(last_i) / float(TARGET_SAMPLE_RATE), 3)
            self._last_track_stats[trk] = stats
            return out_segs

        all_segs: List[Dict[str, Any]] = []
        if has_sys or has_mic:
            if has_sys:
                all_segs.extend(_do_track(sys_wav_path, eff_remote if has_name else "Speaker 1", "system"))
            if has_mic and not (cancel_check and cancel_check()):
                all_segs.extend(_do_track(mic_wav_path, "You (Mic)", "mic"))
            all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            return all_segs
        if mixed_wav_path and os.path.exists(mixed_wav_path):
            return _do_track(mixed_wav_path, "Speaker 1", "mixed")
        return []


# ---------------------------------------------------------------------------
# Engine 4: Oriserve Apex CT2 INT8 (Opportunistic CUDA-only Hinglish Verifier)
# ---------------------------------------------------------------------------
class OriserveApexVerifierEngine:
    """
    Opportunistic CUDA-only Hinglish second-pass verifier (`oriserve-apex-ct2-int8`).
    Refuses to allocate if CUDA/cuBLAS is unavailable or live free VRAM < min_free_vram_mb.
    Always transcribes with `language=None` (never forced 'hi', never English-only mode).
    """

    asr_backend: str = "oriserve_apex_ct2"
    asr_device: str = "cuda"
    asr_compute_type: str = "int8"
    model_label: str = "oriserve-apex-ct2-int8"

    def __init__(
        self,
        model_dir: Optional[str | Path] = None,
        models_dir: Optional[str | Path] = None,
        min_free_vram_mb: int = 1100,
    ) -> None:
        self._explicit_dir = Path(model_dir) if model_dir else None
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        self.min_free_vram_mb = max(400, int(min_free_vram_mb))
        self._lock = threading.RLock()
        self._model: Any = None
        self.load_sec: float = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _resolve_dir(self) -> Optional[Path]:
        if self._explicit_dir is not None:
            return self._explicit_dir if _is_valid_model_dir("oriserve_apex", self._explicit_dir) else None
        return discover_phase2_models(self.models_dir).get("oriserve_apex")

    def ensure_loaded(self) -> Tuple[bool, str]:
        with self._lock:
            if self._model is not None:
                return True, f"Loaded {self.model_label} on CUDA INT8"
            target_dir = self._resolve_dir()
            if target_dir is None:
                return False, "Local oriserve-apex-ct2-int8 directory not found."
            if not is_cuda_runtime_available():
                return False, "CUDA runtime/cuBLAS unavailable; skipping GPU verifier."
            _, free_mb = HardwareProfiler._query_live_gpu_vram_mb()
            if free_mb is not None and free_mb < self.min_free_vram_mb:
                return False, f"Free VRAM ({free_mb} MB) < {self.min_free_vram_mb} MB required for {self.model_label}."
            t0 = time.perf_counter()
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    str(target_dir),
                    device="cuda",
                    compute_type="int8",
                    local_files_only=True,
                )
                self.load_sec = round(time.perf_counter() - t0, 3)
                logger.info("Loaded %s on CUDA INT8 in %.2fs", self.model_label, self.load_sec)
                return True, f"Loaded {self.model_label} on CUDA INT8"
            except Exception as exc:
                self.unload_model()
                return False, f"Failed loading {self.model_label}: {exc}"

    def unload_model(self) -> None:
        with self._lock:
            if self._model is not None:
                try:
                    del self._model
                except Exception:
                    pass
                self._model = None
            gc.collect()
            try:
                if "torch" in sys.modules:
                    torch_mod = sys.modules["torch"]
                    if torch_mod.cuda.is_available():
                        torch_mod.cuda.empty_cache()
            except Exception:
                pass

    def transcribe_slice(
        self,
        audio_16k: np.ndarray,
        *,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        output_script: str = "roman",
    ) -> Dict[str, Any]:
        with self._lock:
            ok, err = self.ensure_loaded()
            if not ok:
                raise RuntimeError(err)
            if audio_16k.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(audio_16k))) < 0.0004:
                return {"raw_text": "", "text": "", "detected_language": "", "avg_logprob": None}

            norm = normalize_speech_audio(audio_16k)
            segs_gen, info = self._model.transcribe(
                norm,
                language=None,  # NEVER force language="hi"
                beam_size=2,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            segs = list(segs_gen)
            raw_txt = " ".join(str(s.text or "").strip() for s in segs if str(s.text or "").strip()).strip()
            raw_txt, had_rep = _sanitize_utf8_text(raw_txt)
            raw_txt = clean_repetition_tail(raw_txt)
            lps = [float(s.avg_logprob) for s in segs if getattr(s, "avg_logprob", None) is not None]
            avg_lp = round(float(sum(lps) / len(lps)), 4) if lps else None
            det_lang = str(getattr(info, "language", "") or "")
            disp = (
                DictationProcessor.process(
                    raw_txt,
                    vocabulary=vocabulary,
                    cleanup_enabled=False,
                    spoken_punctuation=False,
                    output_script=output_script,
                    mode="verbatim",
                    context="meeting",
                ).strip()
                if raw_txt
                else ""
            )
            return {
                "raw_text": raw_txt,
                "text": clean_repetition_tail(disp).strip(),
                "detected_language": det_lang,
                "avg_logprob": avg_lp,
                "had_utf8_replacement": had_rep,
            }

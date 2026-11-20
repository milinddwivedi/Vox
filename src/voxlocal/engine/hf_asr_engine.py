"""
Feature-Flagged Optional Hugging Face ASR Engine (`Qwen/Qwen3-ASR-0.6B-hf` & `maya-research/maya-transcribe`).

Design guarantees:
- Lazy imports of `torch` and `transformers`: safe to import when optional HF dependencies are absent.
- Avoids byte-level `no_repeat_ngram_size=5` on UTF-8 BPE tokenizers (prevents U+FFFD Devanagari truncation).
- Applies verified Phase 2.5 foreign-script blocking (CJK/Arabic/Kana/Hangul/Cyrillic) and
  English/Hindi language-header constraints for native Qwen3-ASR.
- Explicitly frees GPU allocator memory via `model.to("cpu")`, `gc.collect()`, and `torch.cuda.empty_cache()`.
- Processes 100% of audio tracks through EOF in <= 28.0s chunks without silently dropping final tails.
"""

from __future__ import annotations

import gc
import importlib.util
import os
import re
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
    is_hallucination,
    is_likely_person_name,
    is_prompt_contamination_hallucination,
    load_wav_as_16k_f32,
)

logger = get_logger("engine.hf_asr_engine")

FOREIGN_TOKEN_RE = re.compile(
    r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F\uFF00-\uFFEF"
    r"\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
    r"\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF]"
)

DEFAULT_QWEN_HOTWORDS = (
    "WFX, PLM, ERP, HubSpot, VOX, Teams, Postman, UPC, Remove Programs, System Tray, "
    "Daily Scrum, Account Management, Excel, Windows, Settings, Hemanth, Rumjhum, Vikas, Abhilash"
)


def sanitize_utf8_text(text: str) -> Tuple[str, bool]:
    """Strip UTF-8 replacement chars if any appear and flag whether replacement occurred."""
    s = str(text or "")
    had_rep = "\ufffd" in s
    if had_rep:
        s = re.sub(r"\s+", " ", s.replace("\ufffd", "")).strip()
    return s, had_rep


def build_foreign_script_token_ids(
    tokenizer: Any,
    keep_ids: Tuple[int, ...] = (151643, 151645, 151704),
) -> List[int]:
    """Collect vocabulary IDs containing CJK/Arabic/Kana/Hangul/Cyrillic script characters."""
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
    hard_cap_sec: float = 28.0,
) -> List[Tuple[int, int]]:
    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
        audio_16k, vad_p, max_chunk_sec=max_chunk_sec, merge_gap_sec=0.65, pad_sec=0.20
    )
    if spans is None:
        spans = [(0, audio_16k.size)]
    elif spans and spans[-1][1] < audio_16k.size:
        # Check tail after last VAD span so end-of-call speech is never silently skipped
        tail_s = int(spans[-1][1])
        tail_arr = audio_16k[tail_s:]
        if tail_arr.size >= int(0.25 * TARGET_SAMPLE_RATE) and float(np.max(np.abs(tail_arr))) >= 0.0005:
            spans = list(spans) + [(tail_s, audio_16k.size)]

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


class LocalHFASRTranscriber:
    """
    Lazy-loaded local Hugging Face multimodal ASR engine for Qwen3-ASR / Maya Transcribe One.
    Implements strict sequential VRAM hygiene and falls back cleanly if runtime or weights are unavailable.
    """

    def __init__(
        self,
        model_id: str = "Qwen/Qwen3-ASR-0.6B-hf",
        models_dir: Optional[str | Path] = None,
        device: str = "auto",
        dtype_name: str = "auto",
    ) -> None:
        self.model_id = str(model_id or "Qwen/Qwen3-ASR-0.6B-hf").strip()
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        self.requested_device = str(device or "auto").lower()
        self.requested_dtype = str(dtype_name or "auto").lower()
        self._lock = threading.RLock()
        self._model: Any = None
        self._processor: Any = None
        self._blocked_ids: List[int] = []
        self._blocked_ids_dev: Any = None
        self._active_device: str = "cpu"
        self._active_dtype: str = "float32"
        self._last_track_stats: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def is_runtime_available() -> bool:
        try:
            return (
                importlib.util.find_spec("torch") is not None
                and importlib.util.find_spec("transformers") is not None
            )
        except Exception:
            return False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    @property
    def last_track_stats(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self._last_track_stats.items()}

    def _resolve_local_checkpoint(self) -> Optional[Path]:
        p_direct = Path(self.model_id)
        if p_direct.is_dir() and (p_direct / "config.json").exists():
            return p_direct

        low = self.model_id.lower()
        slugs: List[str] = []
        if "maya" in low:
            slugs.append("maya-transcribe")
        elif "hinglish" in low or "moorlee" in low:
            slugs.append("qwen3-asr-0.6b-hinglish")
        elif "qwen3-asr" in low:
            slugs.append("qwen3-asr-0.6b")
        slugs.append(self.model_id.replace("/", "--"))

        for slug in slugs:
            for cand in (
                self.models_dir / "challengers" / slug,
                self.models_dir / "hf_asr" / slug,
                self.models_dir / slug,
            ):
                if (cand / "config.json").exists() and (
                    (cand / "model.safetensors").exists() or list(cand.glob("*.safetensors"))
                ):
                    return cand

        for root in (self.models_dir, Path.home() / ".cache" / "huggingface" / "hub"):
            hub = root / f"models--{self.model_id.replace('/', '--')}" / "snapshots"
            if hub.exists():
                for snap in hub.iterdir():
                    if snap.is_dir() and (snap / "config.json").exists():
                        return snap
        return None

    def ensure_loaded(self, local_only: bool = True) -> Tuple[bool, str]:
        if not self.is_runtime_available():
            return False, "Optional torch/transformers runtime is not installed."

        with self._lock:
            if self.is_loaded:
                return True, f"Loaded '{self.model_id}' on {self._active_device.upper()} ({self._active_dtype})."

            local_path = self._resolve_local_checkpoint()
            if local_only and local_path is None:
                return False, f"Local HF ASR checkpoint not found for '{self.model_id}'."
            load_target = str(local_path) if local_path is not None else self.model_id

            try:
                import torch
                from transformers import AutoModelForMultimodalLM, AutoProcessor
            except Exception as exc:
                return False, f"Failed importing HF ASR dependencies: {exc}"

            dev = self.requested_device
            if dev == "auto":
                _, free_mb = HardwareProfiler._query_live_gpu_vram_mb()
                if torch.cuda.is_available() and (free_mb is None or free_mb >= 2000):
                    dev = "cuda"
                else:
                    dev = "cpu"
            elif dev == "cuda" and not torch.cuda.is_available():
                dev = "cpu"

            dtype = torch.float16 if (dev == "cuda" and self.requested_dtype in ("auto", "float16", "fp16")) else torch.float32
            t0 = time.perf_counter()
            try:
                processor = AutoProcessor.from_pretrained(
                    load_target,
                    local_files_only=bool(local_only and local_path is not None),
                    trust_remote_code=False,
                )
                try:
                    model = AutoModelForMultimodalLM.from_pretrained(
                        load_target,
                        dtype=dtype,
                        local_files_only=bool(local_only and local_path is not None),
                        trust_remote_code=False,
                    )
                except Exception:
                    from transformers import Qwen3ASRForConditionalGeneration
                    model = Qwen3ASRForConditionalGeneration.from_pretrained(
                        load_target,
                        dtype=dtype,
                        local_files_only=bool(local_only and local_path is not None),
                    )
                model = model.to(device=dev, dtype=dtype).eval()
                blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
                blocked_dev = torch.tensor(blocked_ids, device=dev, dtype=torch.long) if blocked_ids else None

                self._model = model
                self._processor = processor
                self._blocked_ids = blocked_ids
                self._blocked_ids_dev = blocked_dev
                self._active_device = dev
                self._active_dtype = "float16" if dtype == torch.float16 else "float32"
                elapsed = time.perf_counter() - t0
                msg = f"Loaded '{self.model_id}' on {dev.upper()} ({self._active_dtype}) in {elapsed:.2f}s"
                logger.info(msg)
                return True, msg
            except Exception as exc:
                self.unload_model()
                return False, f"Failed loading HF ASR model '{self.model_id}': {exc}"

    def unload_model(self) -> None:
        with self._lock:
            model = self._model
            self._model = None
            self._processor = None
            self._blocked_ids = []
            self._blocked_ids_dev = None
            if model is not None:
                try:
                    model.to("cpu")
                except Exception:
                    pass
                try:
                    del model
                except Exception:
                    pass
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()
                    if hasattr(torch.cuda, "ipc_collect"):
                        torch.cuda.ipc_collect()
            except Exception:
                pass

    def _is_maya(self) -> bool:
        return "maya" in self.model_id.lower()

    def _build_logits_processors(self, in_len: int) -> Any:
        import torch
        from transformers import LogitsProcessor, LogitsProcessorList

        blocked = self._blocked_ids_dev
        if self._is_maya():
            class _ForeignMask(LogitsProcessor):
                def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
                    if blocked is not None and blocked.numel() > 0:
                        scores = scores.clone()
                        scores[:, blocked] = -float("inf")
                    return scores

            return LogitsProcessorList([_ForeignMask()])

        tok = self._processor.tokenizer
        lang_word_id = tok.encode("language", add_special_tokens=False)[0]
        allowed_lang_ids = torch.tensor(
            [
                tok.encode(" None", add_special_tokens=False)[0],
                tok.encode(" English", add_special_tokens=False)[0],
                tok.encode(" Hindi", add_special_tokens=False)[0],
            ],
            device=self._active_device,
            dtype=torch.long,
        )
        asr_text_id = tok.encode("<asr_text>", add_special_tokens=False)[0]

        class _NativeGuard(LogitsProcessor):
            def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
                step = int(input_ids.shape[1]) - in_len
                if step == 0:
                    masked = torch.full_like(scores, -float("inf"))
                    masked[:, lang_word_id] = scores[:, lang_word_id]
                    return masked
                if step == 1:
                    masked = torch.full_like(scores, -float("inf"))
                    masked[:, allowed_lang_ids] = scores[:, allowed_lang_ids]
                    return masked
                if step == 2:
                    masked = torch.full_like(scores, -float("inf"))
                    masked[:, asr_text_id] = scores[:, asr_text_id]
                    return masked
                if blocked is not None and blocked.numel() > 0:
                    scores = scores.clone()
                    scores[:, blocked] = -float("inf")
                return scores

        return LogitsProcessorList([_NativeGuard()])

    def decode_chunk(
        self,
        chunk_16k: np.ndarray,
        *,
        hotwords: Optional[str] = None,
        max_new_tokens: int = 320,
    ) -> Tuple[str, str, bool]:
        """
        Transcribe one <=28s normalized audio chunk.
        Returns `(raw_text, detected_language, had_utf8_replacement)`.
        """
        import torch

        if self._model is None or self._processor is None:
            raise RuntimeError("HF ASR model is not loaded")

        dur_sec = chunk_16k.size / float(TARGET_SAMPLE_RATE)
        raw_rms = float(np.sqrt(np.mean(np.square(chunk_16k)))) if chunk_16k.size else 0.0
        norm_sl = normalize_speech_audio(chunk_16k)

        def _run_once(prompt_txt: Optional[str]) -> Tuple[str, str]:
            if self._is_maya():
                inputs = self._processor.apply_transcription_request(
                    audio=norm_sl,
                    prompt="style: mixed",
                    language="Hindi",
                )
                inputs = inputs.to(self._model.device, self._model.dtype)
            else:
                inputs = self._processor.apply_transcription_request(
                    audio=norm_sl,
                    language=None,
                    prompt=prompt_txt,
                    tokenize=True,
                )
                inputs = inputs.to(self._active_device)
                if "input_features" in inputs:
                    target_dt = torch.float16 if self._active_dtype == "float16" else torch.float32
                    inputs["input_features"] = inputs["input_features"].to(dtype=target_dt)

            in_len = int(inputs["input_ids"].shape[1])
            lp_list = self._build_logits_processors(in_len)
            with torch.inference_mode():
                # Intentionally omit byte-level `no_repeat_ngram_size=5` so multi-byte Devanagari BPE sequences never emit U+FFFD.
                gen_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    repetition_penalty=1.05,
                    logits_processor=lp_list,
                )

            if self._is_maya():
                raw_txt = str(
                    self._processor.decode(gen_ids[:, in_len:], return_format="transcription_only")[0] or ""
                ).strip()
                det_lang = "hi"
            else:
                raw_dec = self._processor.decode(gen_ids[0][in_len:], skip_special_tokens=True)
                parsed = self._processor.parse_output(raw_dec)
                raw_txt = str(parsed.get("transcription") if isinstance(parsed, dict) else raw_dec).strip()
                det_lang = str(parsed.get("language") if isinstance(parsed, dict) else "")
            del inputs, gen_ids
            return raw_txt, det_lang

        # Speech-gated hotwords (prevents prompt echoes on short/quiet audio)
        eff_prompt: Optional[str] = None
        if not self._is_maya() and hotwords and dur_sec >= 1.5 and raw_rms >= 0.012:
            eff_prompt = str(hotwords)

        raw_txt, det_lang = _run_once(eff_prompt)
        if eff_prompt and raw_txt and is_prompt_contamination_hallucination(
            raw_txt, initial_prompt=eff_prompt, duration_sec=dur_sec, audio_rms=raw_rms
        ):
            retry_txt, retry_lang = _run_once(None)
            if retry_txt and not is_prompt_contamination_hallucination(
                retry_txt, initial_prompt=eff_prompt, duration_sec=dur_sec, audio_rms=raw_rms
            ):
                raw_txt, det_lang = retry_txt, retry_lang
            else:
                raw_txt = ""

        cleaned_raw, had_rep = sanitize_utf8_text(raw_txt)
        cleaned_raw = clean_repetition_tail(cleaned_raw)
        return cleaned_raw, det_lang, had_rep

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

        spans = _extract_spans_with_hard_cap(aud, max_chunk_sec=22.0, hard_cap_sec=28.0)
        segments: List[Dict[str, Any]] = []
        last_processed_idx = 0

        for idx, (s_i, e_i) in enumerate(spans):
            if cancel_check and cancel_check():
                stats["cancelled"] = True
                break
            last_processed_idx = max(last_processed_idx, int(e_i))
            sl = aud[s_i:e_i]
            if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                continue

            if progress_callback and idx % 4 == 0:
                pct = min(100, int((e_i / max(1, total_samples)) * 100))
                progress_callback(f"{source_track} HF-ASR {pct}%")

            s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
            e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
            raw_txt, det_lang, had_rep = self.decode_chunk(sl, hotwords=hotwords, max_new_tokens=320)
            if not raw_txt:
                continue

            if output_script == "roman":
                display_txt = DictationProcessor.process(
                    raw_txt,
                    vocabulary=vocabulary,
                    cleanup_enabled=False,
                    spoken_punctuation=False,
                    output_script="roman",
                    mode="verbatim",
                ).strip()
            else:
                display_txt = raw_txt

            display_txt = clean_repetition_tail(display_txt).strip()
            if not display_txt or is_hallucination(
                display_txt, duration_sec=max(0.1, e_sec - s_sec), initial_prompt=""
            ):
                continue

            segments.append(
                {
                    "start_time": s_sec,
                    "end_time": e_sec,
                    "start": s_sec,
                    "end": e_sec,
                    "speaker": speaker_label,
                    "speaker_cluster": speaker_label,
                    "source_track": source_track,
                    "raw_text": raw_txt,
                    "text": display_txt,
                    "detected_language": det_lang,
                    "confidence": 0.55 if had_rep else 0.85,
                    "confidence_calibrated": False,
                    "is_uncertain": bool(had_rep),
                    "uncertainty_reasons": ["utf8_replacement_repaired"] if had_rep else [],
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
    ) -> List[Dict[str, Any]]:
        """Transcribe complete mic and system tracks sequentially through EOF without dropping samples."""
        self._last_track_stats = {}
        ok, msg = self.ensure_loaded(local_only=True)
        if not ok:
            raise RuntimeError(msg)

        has_mic = bool(mic_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(mic_wav_path)))
        has_sys = bool(sys_wav_path and os.path.exists(LocalWhisperTranscriber._prefer_raw_wav_path(sys_wav_path)))

        eff_remote = (remote_speaker_label or "Speaker 1").strip() or "Speaker 1"
        has_name = eff_remote != "Speaker 1" and is_likely_person_name(eff_remote)
        all_segs: List[Dict[str, Any]] = []
        hw = hotwords or DEFAULT_QWEN_HOTWORDS

        if has_sys or has_mic:
            sys_segs: List[Dict[str, Any]] = []
            if has_sys:
                sys_segs, sys_stats = self._transcribe_track(
                    sys_wav_path,
                    speaker_label=eff_remote if has_name else "Speaker 1",
                    source_track="system",
                    hotwords=hw,
                    output_script=output_script,
                    vocabulary=vocabulary,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )
                self._last_track_stats["system"] = sys_stats
                all_segs.extend(sys_segs)

            if has_mic and not (cancel_check and cancel_check()):
                mic_segs, mic_stats = self._transcribe_track(
                    mic_wav_path,
                    speaker_label="You (Mic)",
                    source_track="mic",
                    hotwords=hw,
                    output_script=output_script,
                    vocabulary=vocabulary,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
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
                hotwords=hw,
                output_script=output_script,
                vocabulary=vocabulary,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            self._last_track_stats["mixed"] = mix_stats
            return mix_segs

        return []

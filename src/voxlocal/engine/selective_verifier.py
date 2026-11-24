"""
Phase 2 Configurable Selective Second-Pass Verifier & Language-Routing Guardrails.

Key properties:
- Evaluates configurable (provisional) uncertainty & language triggers per utterance.
- Explicitly excludes English homographs ('to', 'me', 'par', etc.) from Hinglish cues
  so Indian-accented English is never misrouted into Hindi ASR.
- Enforces directional safety invariants:
    * 'english' utterances NEVER route to 'oriserve_apex_ct2' (which regresses English).
    * 'hinglish' utterances NEVER route to English-only 'parakeet_onnx' or weaker 'small'.
- Loads bounded disk audio slices per flagged utterance (0 full-WAV RAM spike).
- Preserves immutable `raw_text`, appends audit records to `processing_history`, and
  unloads CUDA verifier weights immediately on completion.
"""

from __future__ import annotations

import os
import re
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.core.logger import get_logger
from voxlocal.engine.completeness import _SUSPICIOUS_SCRIPT_RE, _has_repetition_loop
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.onnx_asr_engines import (
    FOREIGN_TOKEN_RE,
    OriserveApexVerifierEngine,
    ParakeetOnnxEngine,
)
from voxlocal.engine.transcriber import LocalWhisperTranscriber, clean_repetition_tail, is_hallucination

logger = get_logger("engine.selective_verifier")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_WORD_RE = re.compile(r"[a-zA-Z\u0900-\u097F']+")

# Common short English words that also resemble Romanized Hindi syllables — NEVER trigger Hinglish alone.
ENGLISH_HOMOGRAPH_EXCLUSIONS = frozenset(
    {
        "to", "me", "par", "in", "on", "at", "or", "so", "no", "do",
        "go", "is", "it", "he", "we", "be", "as", "us", "am", "an",
    }
)

# Unambiguous Romanized Hindi discourse/function cues (derived excluding English homographs).
HINDI_ROMAN_CUES = frozenset(
    {
        "hai", "hain", "nahi", "nahin", "kya", "kyunki", "kyun", "kyon",
        "matlab", "abhi", "achha", "accha", "acha", "theek", "thik",
        "karna", "karne", "karke", "karta", "karti", "karte", "karo", "karein",
        "raha", "rahi", "rahe", "hoga", "hogi", "honge", "hua", "hui", "huye",
        "mera", "meri", "mere", "hum", "humein", "humko", "aap", "aapka", "aapko",
        "tum", "tumhara", "yahan", "wahan", "lekin", "phir", "bhi", "mein", "main",
        "woh", "yeh", "bahut", "bohot", "chahiye", "samajh", "dekho", "suno",
        "bolna", "batao", "bataiye", "baat", "kaam", "paisa", "paise", "sirf",
        "bilkul", "wala", "wali", "wale", "kal", "aaj", "kuch", "sab", "koi",
        "kaun", "kaise", "kab", "kahan", "kitna", "kitni", "liye", "saath",
        "pehle", "baad", "agar", "jab", "tab", "isse", "uske", "iske", "unke",
        "apna", "apni", "apne", "iska", "uska", "sabhi", "bhaiya", "yaar", "haan",
    }
)


def detect_hinglish_cues(text: str, detected_language: str = "") -> Tuple[bool, List[str]]:
    """
    Detect unambiguous Hindi/Hinglish presence without confusing Indian-accented English.
    English homographs ('to', 'me', 'par', etc.) are strictly excluded.
    """
    reasons: List[str] = []
    txt = str(text or "").strip()
    det = str(detected_language or "").strip().lower()

    if det in ("hi", "hindi"):
        reasons.append("detected_lang_hi")
    if txt and _DEVANAGARI_RE.search(txt):
        reasons.append("devanagari_script")

    if txt:
        tokens = [t.lower() for t in _WORD_RE.findall(txt)]
        matched = [t for t in tokens if t in HINDI_ROMAN_CUES and t not in ENGLISH_HOMOGRAPH_EXCLUSIONS]
        if len(matched) >= 2:
            reasons.append(f"roman_hindi_cues({len(matched)})")

    return bool(reasons), reasons


def evaluate_selective_triggers(
    seg: Dict[str, Any],
    *,
    config: Any = None,
    session_mode: str = "auto",
) -> Tuple[bool, List[str], str]:
    """
    Evaluate whether an utterance should be considered for selective 2nd-pass verification.
    Returns `(should_verify, trigger_reasons, utterance_profile)` where `utterance_profile`
    is `"english"` or `"hinglish"`.
    """
    lp_thresh = float(getattr(config, "selective_logprob_threshold", -0.45) or -0.45)
    min_wps = float(getattr(config, "selective_min_wps", 0.75) or 0.75)
    hinglish_cue_routing = bool(getattr(config, "selective_enable_hinglish_cue_routing", True))

    raw_txt = str(seg.get("raw_text") or seg.get("text") or "").strip()
    disp_txt = str(seg.get("text") or raw_txt).strip()
    det_lang = str(seg.get("detected_language") or "").strip().lower()
    raw_score = seg.get("raw_model_score")
    s_t = float(seg.get("start_time", seg.get("start", 0.0)))
    e_t = float(seg.get("end_time", seg.get("end", s_t)))
    dur = max(0.05, e_t - s_t)
    words = len(_WORD_RE.findall(disp_txt))
    wps = float(words) / dur

    triggers: List[str] = []

    if raw_score is not None:
        try:
            if float(raw_score) < lp_thresh:
                triggers.append("low_logprob_provisional")
        except (TypeError, ValueError):
            pass

    if dur >= 2.0 and wps < min_wps:
        triggers.append("low_wps_omission")
    elif dur >= 0.8 and words == 0:
        triggers.append("empty_text_speech")

    if _has_repetition_loop(disp_txt) or _has_repetition_loop(raw_txt):
        triggers.append("repetition_anomaly")

    if _SUSPICIOUS_SCRIPT_RE.search(disp_txt) or _SUSPICIOUS_SCRIPT_RE.search(raw_txt):
        triggers.append("foreign_script_anomaly")

    is_hi_cue, hi_reasons = detect_hinglish_cues(raw_txt or disp_txt, detected_language=det_lang)
    if session_mode != "english" and hinglish_cue_routing and is_hi_cue:
        triggers.extend(hi_reasons)

    utterance_profile = "hinglish" if (session_mode == "hinglish" or is_hi_cue) else "english"
    # Note: In auto/hinglish sessions, Hinglish cues alone only justify verification if an empirically
    # stronger Hinglish verifier is configured; English-only cues only verify on quality triggers.
    return bool(triggers), triggers, utterance_profile


def _load_wav_slice_bounded(wav_path: str, start_sec: float, end_sec: float, pad_sec: float = 0.15) -> np.ndarray:
    """Read strictly `[start_sec - pad_sec, end_sec + pad_sec]` 16k mono float32 directly from WAV file."""
    if not wav_path or not os.path.exists(wav_path):
        return np.zeros(0, dtype=np.float32)
    eff_path = LocalWhisperTranscriber._prefer_raw_wav_path(wav_path)
    if not os.path.exists(eff_path):
        eff_path = wav_path
    try:
        with wave.open(eff_path, "rb") as wf:
            sr = int(wf.getframerate())
            ch = int(wf.getnchannels())
            total = int(wf.getnframes())
            if sr <= 0 or total <= 0:
                return np.zeros(0, dtype=np.float32)
            s_idx = max(0, int((float(start_sec) - pad_sec) * sr))
            e_idx = min(total, int((float(end_sec) + pad_sec) * sr))
            if e_idx <= s_idx:
                return np.zeros(0, dtype=np.float32)
            wf.setpos(s_idx)
            raw = wf.readframes(e_idx - s_idx)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            samples = samples.reshape(-1, ch).mean(axis=1).astype(np.float32)
        if sr != TARGET_SAMPLE_RATE:
            from voxlocal.audio.devices import resample_audio_to_16k_mono
            samples = resample_audio_to_16k_mono(samples, sr)
        return samples.astype(np.float32, copy=False)
    except Exception as exc:
        logger.debug("Bounded WAV slice read failed (%s): %s", eff_path, exc)
        return np.zeros(0, dtype=np.float32)


class SelectiveSecondPassVerifier:
    """
    Runs selective second-pass verification only on flagged utterances, respecting
    hardware headroom and language-routing invariants while keeping raw_text immutable.
    """

    def __init__(self, models_dir: Optional[str | Path] = None) -> None:
        self.models_dir = models_dir

    def verify_segments(
        self,
        segments: List[Dict[str, Any]],
        *,
        execution_profile: Any,
        config: Any = None,
        mic_wav_path: str = "",
        sys_wav_path: str = "",
        mixed_wav_path: str = "",
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        output_script: str = "roman",
        cancel_check: Optional[Callable[[], bool]] = None,
        verifier_backend_override: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        t0 = time.perf_counter()
        verifier_backend = str(
            verifier_backend_override
            or getattr(execution_profile, "selective_verifier_backend", "disabled")
            or "disabled"
        ).strip().lower()
        session_mode = str(getattr(execution_profile, "session_mode", "auto") or "auto").strip().lower()
        max_utts = int(getattr(config, "selective_max_utterances_per_meeting", 40) or 40)

        stats: Dict[str, Any] = {
            "enabled": verifier_backend != "disabled",
            "verifier_backend": verifier_backend,
            "session_mode": session_mode,
            "total_segments": len(segments),
            "evaluated_segments": 0,
            "escalated_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "skipped_guard_count": 0,
            "trigger_reason_counts": {},
            "errors": [],
            "provisional_thresholds": {
                "selective_logprob_threshold": float(getattr(config, "selective_logprob_threshold", -0.45) or -0.45),
                "selective_min_wps": float(getattr(config, "selective_min_wps", 0.75) or 0.75),
                "note": "Provisional configurable heuristic parameters; require larger real-world validation.",
            },
            "duration_sec": 0.0,
        }

        if not segments or verifier_backend == "disabled" or not bool(getattr(config, "selective_second_pass_enabled", True)):
            return [dict(s) for s in segments], stats

        # Step 1: Identify eligible candidates & enforce language-routing guards BEFORE loading heavy models
        candidates: List[Tuple[int, List[str], str]] = []
        updated = [dict(s) for s in segments]

        for idx, seg in enumerate(updated):
            if cancel_check and cancel_check():
                break
            stats["evaluated_segments"] += 1
            should_ver, trig_reasons, utt_profile = evaluate_selective_triggers(
                seg, config=config, session_mode=session_mode
            )
            if not should_ver:
                continue

            for tr in trig_reasons:
                stats["trigger_reason_counts"][tr] = int(stats["trigger_reason_counts"].get(tr, 0)) + 1

            # Directional routing safety invariants:
            if utt_profile == "english" and verifier_backend == "oriserve_apex_ct2":
                stats["skipped_guard_count"] += 1
                hist = list(seg.get("processing_history") or [])
                hist.append({"stage": "selective_guard_skip", "reason": "english_never_routed_to_oriserve"})
                seg["processing_history"] = hist
                continue
            if utt_profile == "hinglish" and verifier_backend in ("parakeet_onnx", "nemotron_onnx"):
                stats["skipped_guard_count"] += 1
                hist = list(seg.get("processing_history") or [])
                hist.append({"stage": "selective_guard_skip", "reason": "hinglish_never_routed_to_english_only"})
                seg["processing_history"] = hist
                continue

            # If primary already ran HF Qwen3 FP16 and only hinglish cue triggered without quality warning, skip redundant rerun
            prim_model = str(seg.get("asr_backend") or "").lower()
            only_hi_cue = all(
                r == "detected_lang_hi" or r == "devanagari_script" or r.startswith("roman_hindi_cues")
                for r in trig_reasons
            )
            if prim_model == verifier_backend or (prim_model == "hf_qwen3_asr" and only_hi_cue and verifier_backend != "oriserve_apex_ct2"):
                stats["skipped_guard_count"] += 1
                continue

            candidates.append((idx, trig_reasons, utt_profile))
            if len(candidates) >= max_utts:
                break

        if not candidates:
            stats["duration_sec"] = round(time.perf_counter() - t0, 3)
            return updated, stats

        # Step 2: Lazy-load verifier engine (never co-residing with primary model)
        engine: Any = None
        try:
            min_vram = int(getattr(config, "gpu_min_free_vram_ct2_mb", 1100) or 1100)
            if verifier_backend == "oriserve_apex_ct2":
                engine = OriserveApexVerifierEngine(
                    models_dir=self.models_dir,
                    min_free_vram_mb=min_vram,
                    allow_cpu=bool(getattr(config, "post_call_allow_cpu_verifier", True)),
                    cpu_threads=int(getattr(execution_profile, "safe_cpu_threads", 4) or 4),
                )
            elif verifier_backend == "parakeet_onnx":
                engine = ParakeetOnnxEngine(models_dir=self.models_dir)
            else:
                stats["skipped_guard_count"] += len(candidates)
                stats["duration_sec"] = round(time.perf_counter() - t0, 3)
                return updated, stats

            ok_load, load_msg = engine.ensure_loaded()
            if not ok_load:
                stats["errors"].append(load_msg)
                stats["duration_sec"] = round(time.perf_counter() - t0, 3)
                return updated, stats
            stats["verifier_device"] = str(getattr(engine, "asr_device", "") or "cpu")

            for idx, trig_reasons, utt_profile in candidates:
                if cancel_check and cancel_check():
                    break
                seg = updated[idx]
                trk = str(seg.get("source_track") or "mixed").lower()
                wav_p = mic_wav_path if trk == "mic" else (sys_wav_path if trk == "system" else mixed_wav_path)
                if not wav_p:
                    wav_p = mixed_wav_path or mic_wav_path or sys_wav_path
                s_t = float(seg.get("start_time", seg.get("start", 0.0)))
                e_t = float(seg.get("end_time", seg.get("end", s_t)))
                sl = _load_wav_slice_bounded(wav_p, s_t, e_t, pad_sec=0.15)
                if sl.size < int(0.20 * TARGET_SAMPLE_RATE):
                    continue

                stats["escalated_count"] += 1
                try:
                    if verifier_backend == "oriserve_apex_ct2":
                        v_res = engine.transcribe_slice(sl, vocabulary=vocabulary, output_script=output_script)
                    else:
                        v_res = engine.transcribe_array(sl, vocabulary=vocabulary, output_script=output_script)
                except Exception as inf_exc:
                    stats["errors"].append(f"seg_{idx}: {inf_exc}")
                    continue

                v_raw = str(v_res.get("raw_text") or "").strip()
                v_disp = clean_repetition_tail(str(v_res.get("text") or "").strip())
                v_lp = v_res.get("avg_logprob")
                dur_s = max(0.1, e_t - s_t)

                # Quality gate: reject empty, foreign-script leaks, U+FFFD, repetition collapse, or hallucinations
                bad_second = (
                    not v_disp
                    or "\ufffd" in v_raw
                    or "\ufffd" in v_disp
                    or bool(FOREIGN_TOKEN_RE.search(v_raw))
                    or bool(FOREIGN_TOKEN_RE.search(v_disp))
                    or _has_repetition_loop(v_disp)
                    or is_hallucination(v_disp, duration_sec=dur_s, initial_prompt="")
                )

                hist = list(seg.get("processing_history") or [])
                if bad_second:
                    stats["rejected_count"] += 1
                    hist.append(
                        {
                            "stage": "selective_second_pass_rejected",
                            "verifier_backend": verifier_backend,
                            "verifier_model": getattr(engine, "model_label", verifier_backend),
                            "triggers": trig_reasons,
                            "utterance_profile": utt_profile,
                            "candidate_preview": v_disp[:80],
                        }
                    )
                    seg["processing_history"] = hist
                    continue

                stats["accepted_count"] += 1
                # Preserve original raw_text strictly immutable! Record verified text in corrected_text & text.
                orig_raw = str(seg.get("raw_text") or seg.get("text") or "").strip()
                orig_norm = str(seg.get("normalized_text") or seg.get("text") or "").strip()
                seg["raw_text"] = orig_raw
                seg["normalized_text"] = orig_norm
                seg["corrected_text"] = v_disp
                seg["text"] = v_disp
                seg["transcript_stage"] = "verified"
                seg["transcript_state"] = "verified"
                if v_lp is not None:
                    seg["raw_model_score"] = v_lp
                if v_res.get("detected_language"):
                    seg["detected_language"] = str(v_res["detected_language"])
                hist.append(
                    {
                        "stage": "selective_second_pass_accepted",
                        "verifier_backend": verifier_backend,
                        "verifier_model": getattr(engine, "model_label", verifier_backend),
                        "verifier_device": getattr(engine, "asr_device", "cuda"),
                        "triggers": trig_reasons,
                        "utterance_profile": utt_profile,
                        "raw_model_score": v_lp,
                    }
                )
                seg["processing_history"] = hist
        finally:
            if engine is not None:
                try:
                    engine.unload_model()
                except Exception:
                    pass

        stats["duration_sec"] = round(time.perf_counter() - t0, 3)
        return updated, stats

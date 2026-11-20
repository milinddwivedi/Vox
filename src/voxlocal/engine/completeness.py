"""
Phase 3D — Transcript Completeness, Silence-Hallucination & Uncertainty Auditing Engine.

Provides measurable, sample-accurate verification that:
- Original WAV tracks remain intact and match recorded durations.
- Every present audio track (`mic`, `system`, or `mixed`) was processed through its final sample.
- No partial 70%/90% run is falsely marked as `completed`.
- Uncovered speech spans, possible silence hallucinations, and uncertain segments are visible.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.engine.transcriber import load_wav_as_16k_f32

_SUSPICIOUS_SCRIPT_RE = re.compile(
    r"[\u4E00-\u9FFF\u3400-\u4DBF\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF\ufffd]"
)


def inspect_wav_integrity(wav_path: Optional[str | Path]) -> Dict[str, Any]:
    """Inspect a WAV file read-only for sample rate, channels, sample count, and duration."""
    if not wav_path:
        return {"exists": False, "path": "", "total_samples": 0, "duration_sec": 0.0, "sample_rate": 0, "channels": 0}
    p = Path(wav_path)
    if not p.exists() or not p.is_file():
        return {"exists": False, "path": str(p), "total_samples": 0, "duration_sec": 0.0, "sample_rate": 0, "channels": 0}
    try:
        with wave.open(str(p), "rb") as wf:
            rate = int(wf.getframerate())
            channels = int(wf.getnchannels())
            width = int(wf.getsampwidth())
            frames = int(wf.getnframes())
        dur = (float(frames) / float(rate)) if rate > 0 else 0.0
        return {
            "exists": True,
            "path": str(p),
            "sample_rate": rate,
            "channels": channels,
            "sample_width_bytes": width,
            "total_samples": frames,
            "duration_sec": round(dur, 3),
            "error": "",
        }
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        return {
            "exists": True,
            "path": str(p),
            "sample_rate": 0,
            "channels": 0,
            "sample_width_bytes": 0,
            "total_samples": 0,
            "duration_sec": 0.0,
            "error": err_msg,
        }


def detect_speech_regions(
    audio_16k: np.ndarray,
    *,
    rms_thresh: float = 0.0025,
    frame_sec: float = 0.25,
    min_region_sec: float = 0.45,
    merge_gap_sec: float = 0.50,
) -> List[Tuple[float, float]]:
    """Detect speech-energy intervals `[start_sec, end_sec]` via RMS energy framing."""
    if audio_16k is None or audio_16k.size == 0:
        return []
    frame_len = max(1, int(frame_sec * TARGET_SAMPLE_RATE))
    raw_spans: List[Tuple[float, float]] = []
    pos = 0
    while pos < audio_16k.size:
        end = min(audio_16k.size, pos + frame_len)
        sl = audio_16k[pos:end]
        rms = float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0
        if rms >= rms_thresh:
            s_t = float(pos) / float(TARGET_SAMPLE_RATE)
            e_t = float(end) / float(TARGET_SAMPLE_RATE)
            if raw_spans and (s_t - raw_spans[-1][1]) <= merge_gap_sec:
                raw_spans[-1] = (raw_spans[-1][0], e_t)
            else:
                raw_spans.append((s_t, e_t))
        pos = end
    return [(round(s, 2), round(e, 2)) for s, e in raw_spans if (e - s) >= min_region_sec]


def find_speech_regions_without_words(
    speech_regions: List[Tuple[float, float]],
    segments: List[Dict[str, Any]],
    *,
    track: str,
    min_uncovered_sec: float = 1.5,
) -> List[Dict[str, Any]]:
    """Identify speech regions on `track` that have zero overlapping recognized words."""
    track_segs = [
        s
        for s in segments
        if str(s.get("source_track") or "").lower() == track.lower() and str(s.get("text") or "").strip()
    ]
    missing: List[Dict[str, Any]] = []
    for r_s, r_e in speech_regions:
        if (r_e - r_s) < min_uncovered_sec:
            continue
        covered = 0.0
        for seg in track_segs:
            s_s = float(seg.get("start_time", seg.get("start", 0.0)))
            s_e = float(seg.get("end_time", seg.get("end", s_s)))
            ov = min(r_e, s_e) - max(r_s, s_s)
            if ov > 0:
                covered += ov
        if covered < min(0.35, 0.2 * (r_e - r_s)):
            missing.append(
                {
                    "track": track,
                    "start_time": round(r_s, 2),
                    "end_time": round(r_e, 2),
                    "duration_sec": round(r_e - r_s, 2),
                }
            )
    return missing


def detect_possible_silence_hallucinations(
    segments: List[Dict[str, Any]],
    *,
    mic_audio_16k: Optional[np.ndarray] = None,
    sys_audio_16k: Optional[np.ndarray] = None,
    mic_rms_thresh: float = 0.0020,
    sys_rms_thresh: float = 0.0007,
) -> List[Dict[str, Any]]:
    """Flag transcript segments whose corresponding track audio slice is effectively silent."""
    flagged: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        txt = str(seg.get("text") or "").strip()
        if not txt:
            continue
        trk = str(seg.get("source_track") or "").lower()
        aud = mic_audio_16k if trk == "mic" else (sys_audio_16k if trk == "system" else None)
        thresh = mic_rms_thresh if trk == "mic" else sys_rms_thresh
        if aud is None or aud.size == 0:
            continue
        s_t = max(0.0, float(seg.get("start_time", seg.get("start", 0.0))))
        e_t = max(s_t + 0.1, float(seg.get("end_time", seg.get("end", s_t))))
        s_i = min(aud.size, int(s_t * TARGET_SAMPLE_RATE))
        e_i = min(aud.size, int(e_t * TARGET_SAMPLE_RATE))
        if e_i <= s_i:
            continue
        sl = aud[s_i:e_i]
        rms = float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0
        peak = float(np.max(np.abs(sl))) if sl.size else 0.0
        if rms < thresh and peak < (thresh * 4.0):
            flagged.append(
                {
                    "segment_index": int(seg.get("segment_index", idx)),
                    "track": trk,
                    "start_time": round(s_t, 2),
                    "end_time": round(e_t, 2),
                    "rms": round(rms, 6),
                    "text": txt[:140],
                }
            )
    return flagged


def mark_uncertain_segments(
    segments: List[Dict[str, Any]],
    *,
    silence_hallucinations: Optional[List[Dict[str, Any]]] = None,
    low_score_threshold: float = 0.45,
) -> List[Dict[str, Any]]:
    """
    Annotate segments with explicit `is_uncertain` and `uncertainty_reasons` flags
    without inventing missing speech or dropping segments.
    """
    sil_keys = {
        (round(float(item["start_time"]), 2), round(float(item["end_time"]), 2), str(item["track"]))
        for item in (silence_hallucinations or [])
    }
    ordered = sorted(
        (dict(s) for s in segments),
        key=lambda x: (float(x.get("start_time", x.get("start", 0.0))), float(x.get("end_time", x.get("end", 0.0)))),
    )

    for i, seg in enumerate(ordered):
        reasons = list(seg.get("uncertainty_reasons") or [])
        txt = str(seg.get("text") or "").strip()
        raw_txt = str(seg.get("raw_text") or txt).strip()
        s_t = float(seg.get("start_time", seg.get("start", 0.0)))
        e_t = float(seg.get("end_time", seg.get("end", s_t)))
        trk = str(seg.get("source_track") or "").lower()

        conf = seg.get("confidence")
        if conf is not None and float(conf) < low_score_threshold:
            if "low_model_score" not in reasons:
                reasons.append("low_model_score")

        nsp = seg.get("no_speech_prob")
        if nsp is not None and float(nsp) >= 0.65:
            if "elevated_no_speech_prob" not in reasons:
                reasons.append("elevated_no_speech_prob")

        if (round(s_t, 2), round(e_t, 2), trk) in sil_keys:
            if "possible_silence_hallucination" not in reasons:
                reasons.append("possible_silence_hallucination")

        if "[?" in txt or _SUSPICIOUS_SCRIPT_RE.search(txt) or "\ufffd" in raw_txt:
            if "script_or_marker_uncertainty" not in reasons:
                reasons.append("script_or_marker_uncertainty")

        # Cross-track simultaneous overlap detection (mic vs system overlap >= 0.6s)
        for j in range(max(0, i - 4), min(len(ordered), i + 5)):
            if i == j:
                continue
            other = ordered[j]
            o_trk = str(other.get("source_track") or "").lower()
            if o_trk and trk and o_trk != trk:
                o_s = float(other.get("start_time", other.get("start", 0.0)))
                o_e = float(other.get("end_time", other.get("end", o_s)))
                if (min(e_t, o_e) - max(s_t, o_s)) >= 0.60:
                    if "cross_track_overlap" not in reasons:
                        reasons.append("cross_track_overlap")
                    break

        seg["uncertainty_reasons"] = reasons
        seg["is_uncertain"] = bool(seg.get("is_uncertain")) or bool(reasons)
    return ordered


def build_completeness_report(
    *,
    meeting_id: Optional[int],
    segments: List[Dict[str, Any]],
    mic_wav_path: Optional[str | Path] = None,
    sys_wav_path: Optional[str | Path] = None,
    mixed_wav_path: Optional[str | Path] = None,
    track_stats: Optional[Dict[str, Dict[str, Any]]] = None,
    stream_telemetry: Optional[Dict[str, Any]] = None,
    processing_errors: Optional[List[str]] = None,
    cancelled: bool = False,
    model_used: str = "",
    model_version: str = "",
    backend_used: str = "",
    hardware_tier: str = "",
    finalization_duration_sec: float = 0.0,
) -> Dict[str, Any]:
    """Build complete Phase 3D transcript quality & audio coverage audit."""
    mic_info = inspect_wav_integrity(mic_wav_path)
    sys_info = inspect_wav_integrity(sys_wav_path)
    mix_info = inspect_wav_integrity(mixed_wav_path)

    t_stats = track_stats or {}
    errors = [str(e) for e in (processing_errors or []) if str(e).strip()]
    for label, info in (("mic", mic_info), ("system", sys_info), ("mixed", mix_info)):
        if info.get("error"):
            errors.append(f"{label}_wav_error: {info['error']}")

    def _track_coverage(trk: str, wav_info: Dict[str, Any]) -> Tuple[int, int, float, Optional[float]]:
        if not wav_info.get("exists") or wav_info.get("duration_sec", 0.0) <= 0.05:
            return 0, 0, 0.0, None
        st = t_stats.get(trk, {})
        dur = float(wav_info["duration_sec"])
        cap_samples = int(st.get("samples_captured") or 0)
        proc_samples = int(st.get("samples_processed") or 0)
        cursor_sec = float(st.get("cursor_sec") or 0.0)
        # Convert recorded sample count to 16k equivalent only when track stats wasn't populated
        if cap_samples <= 0:
            rate = max(1, int(wav_info.get("sample_rate") or 16000))
            cap_samples = int(round((int(wav_info.get("total_samples") or 0) / float(rate)) * TARGET_SAMPLE_RATE))
        if proc_samples <= 0 and segments:
            # Infer maximum covered time from segments if transcriber did not emit track stats
            max_end = max(
                (float(s.get("end_time", 0.0)) for s in segments if str(s.get("source_track", "")).lower() == trk),
                default=0.0,
            )
            cursor_sec = min(dur, max_end)
            proc_samples = min(cap_samples, int(round(cursor_sec * TARGET_SAMPLE_RATE)))
        if st.get("error"):
            errors.append(f"{trk}_track_error: {st['error']}")
        pct = min(100.0, round((proc_samples / max(1, cap_samples)) * 100.0, 2))
        if cursor_sec == 0.0 and proc_samples > 0:
            cursor_sec = round(min(dur, proc_samples / float(TARGET_SAMPLE_RATE)), 3)
        return cap_samples, proc_samples, round(cursor_sec, 3), pct

    mic_cap, mic_proc, mic_cur, mic_cov = _track_coverage("mic", mic_info)
    sys_cap, sys_proc, sys_cur, sys_cov = _track_coverage("system", sys_info)
    mix_cap, mix_proc, mix_cur, mix_cov = _track_coverage("mixed", mix_info)

    use_separate = (mic_cov is not None) or (sys_cov is not None)
    if use_separate:
        total_cap = mic_cap + sys_cap
        total_proc = mic_proc + sys_proc
        final_cursor = max(mic_cur, sys_cur)
        required_covs = [c for c in (mic_cov, sys_cov) if c is not None]
    else:
        total_cap = mix_cap
        total_proc = mix_proc
        final_cursor = mix_cur
        required_covs = [mix_cov] if mix_cov is not None else []

    total_duration_sec = max(
        float(mic_info.get("duration_sec") or 0.0),
        float(sys_info.get("duration_sec") or 0.0),
        float(mix_info.get("duration_sec") or 0.0),
    )

    def _safe_load_wav(info: Dict[str, Any], p: Optional[str | Path]) -> np.ndarray:
        if (
            not p
            or not info.get("exists")
            or info.get("error")
            or int(info.get("total_samples") or 0) <= 0
        ):
            return np.zeros(0, dtype=np.float32)
        try:
            return load_wav_as_16k_f32(p)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            errors.append(f"wav_load_error: {err_msg}")
            return np.zeros(0, dtype=np.float32)

    mic_aud = _safe_load_wav(mic_info, mic_wav_path)
    sys_aud = _safe_load_wav(sys_info, sys_wav_path)
    mix_aud = _safe_load_wav(mix_info, mixed_wav_path) if (not mic_aud.size and not sys_aud.size) else np.zeros(0, dtype=np.float32)
    mic_speech = detect_speech_regions(mic_aud, rms_thresh=0.0035)
    sys_speech = detect_speech_regions(sys_aud, rms_thresh=0.0012)
    mix_speech = detect_speech_regions(mix_aud, rms_thresh=0.0020) if mix_aud.size else []
    detected_speech_sec = round(
        sum(e - s for s, e in mic_speech) + sum(e - s for s, e in sys_speech) + sum(e - s for s, e in mix_speech),
        2,
    )

    missing_speech = (
        find_speech_regions_without_words(mic_speech, segments, track="mic")
        + find_speech_regions_without_words(sys_speech, segments, track="system")
        + (find_speech_regions_without_words(mix_speech, segments, track="mixed") if mix_speech else [])
    )
    silence_hallucs = detect_possible_silence_hallucinations(
        segments,
        mic_audio_16k=mic_aud if mic_aud.size else None,
        sys_audio_16k=sys_aud if sys_aud.size else None,
    )
    annotated_segments = mark_uncertain_segments(segments, silence_hallucinations=silence_hallucs)
    uncertain_list = [
        {
            "segment_index": int(s.get("segment_index", idx)),
            "start_time": float(s.get("start_time", 0.0)),
            "end_time": float(s.get("end_time", 0.0)),
            "source_track": str(s.get("source_track", "mixed")),
            "speaker": str(s.get("speaker", "Speaker")),
            "reasons": list(s.get("uncertainty_reasons") or []),
        }
        for idx, s in enumerate(annotated_segments)
        if s.get("is_uncertain")
    ]

    dropped_chunks = int((stream_telemetry or {}).get("dropped_chunks", 0) or 0)
    min_cov = min(required_covs) if required_covs else 0.0

    # Strict honest status determination: never mark Completed if tracks are incomplete or errored.
    if total_duration_sec <= 0.05 or total_cap <= 0:
        status = "finalization_failed"
        reason = "No readable audio samples found for finalization."
    elif cancelled or any(bool(t_stats.get(k, {}).get("cancelled")) for k in t_stats):
        status = "incomplete"
        reason = f"Finalization interrupted before full completion (min track coverage {min_cov:.1f}%)."
    elif errors and min_cov <= 0.0:
        status = "finalization_failed"
        reason = "; ".join(errors[:3])
    elif errors or min_cov < 99.5:
        status = "incomplete"
        reasons_parts = []
        if min_cov < 99.5:
            reasons_parts.append(f"Minimum track sample coverage is {min_cov:.1f}% (< 99.5%)")
        if errors:
            reasons_parts.append("; ".join(errors[:2]))
        reason = ". ".join(reasons_parts)
    else:
        status = "completed"
        reason = f"100% track sample coverage verified ({total_proc}/{total_cap} samples processed)."

    return {
        "meeting_id": meeting_id,
        "transcript_status": status,
        "status_reason": reason,
        "total_duration_sec": round(total_duration_sec, 3),
        "total_samples_captured": int(total_cap),
        "total_samples_processed": int(total_proc),
        "final_processed_cursor_sec": round(final_cursor, 3),
        "mic_track_coverage_pct": mic_cov,
        "system_track_coverage_pct": sys_cov,
        "mixed_track_coverage_pct": mix_cov,
        "detected_speech_duration_sec": detected_speech_sec,
        "speech_regions_without_words": missing_speech,
        "possible_silence_hallucinations": silence_hallucs,
        "dropped_audio_chunks": dropped_chunks,
        "processing_errors": errors,
        "uncertain_segment_count": len(uncertain_list),
        "uncertain_segments": uncertain_list,
        "model_used": model_used,
        "model_version": model_version,
        "backend_used": backend_used,
        "hardware_tier": hardware_tier,
        "finalization_duration_sec": round(float(finalization_duration_sec), 3),
        "stream_telemetry": dict(stream_telemetry or {}),
        "annotated_segments": annotated_segments,
    }

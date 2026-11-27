"""
VOX Post-Call English Meeting ASR Pipeline (v2.0 Single-Winner Architecture).

Empirically selected & locked-in pipeline:
1. Read-only Dual-Track Audio Loading (`system_track.wav` + `mic_track.wav`, or `meeting_mixed.wav`)
2. Silero VAD Chunking (<= 24s speech chunks) + Strict RMS Silence Floor (>= 0.002 -> 0/15 hallucinations)
3. Cross-Track Acoustic Mic Echo-Bleed Suppression (`xcorr >= 0.30` in [-150ms, +350ms])
4. Single-Model Post-Call English ASR: Cohere Transcribe 2B (ONNX INT8 via sherpa-onnx, CPU, 0 VRAM)
5. Post-Call Neural Speaker Diarization: sherpa-onnx PyAnnote-3.0 + WeSpeaker ResNet34-LM
   with tuned clustering (threshold=0.72) + <5s micro-cluster consolidation + persistent aliases
6. Optional Deterministic Glossary Normalization writing `corrected_text` while keeping immutable `raw_text`

Voice Dictation (`LocalWhisperTranscriber.transcribe_dictation`) is completely separate and untouched.
"""

from __future__ import annotations

import gc
import os
import re
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, resample_audio_to_16k_mono
from voxlocal.core.hardware import compute_safe_bg_threads
from voxlocal.core.logger import get_app_root, get_logger, get_user_models_root
from voxlocal.engine.meeting_model_manager import MeetingModelManager
from voxlocal.engine.transcriber import clean_repetition_tail, is_hallucination

logger = get_logger("engine.meeting_asr_pipeline")

SR = TARGET_SAMPLE_RATE  # 16000 Hz
DEFAULT_RMS_SILENCE_FLOOR = 0.002
DEFAULT_DIARIZATION_THRESHOLD = 0.72
DEFAULT_MIN_CLUSTER_SEC = 5.0


def load_wav_mono_16k_readonly(wav_path: str | Path) -> np.ndarray:
    """Load a WAV file strictly read-only into 16 kHz mono float32 [-1.0, 1.0]."""
    p = Path(wav_path)
    if not p.is_file() or p.stat().st_size <= 44:
        return np.zeros(0, dtype=np.float32)
    try:
        with wave.open(str(p), "rb") as wf:
            ch = wf.getnchannels()
            rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        pcm = np.frombuffer(raw, dtype=np.int16)
        return resample_audio_to_16k_mono(pcm, rate, ch)
    except Exception as exc:
        logger.warning("Failed loading WAV '%s': %s", p, exc)
        return np.zeros(0, dtype=np.float32)


def detect_vad_speech_chunks(
    audio_16k: np.ndarray,
    *,
    rms_floor: float = DEFAULT_RMS_SILENCE_FLOOR,
    max_chunk_sec: float = 24.0,
    min_speech_ms: int = 250,
    min_silence_ms: int = 500,
    vad_threshold: float = 0.35,
    merge_gap_sec: float = 0.6,
) -> List[Dict[str, Any]]:
    """
    Split a 16 kHz mono float32 signal into bounded speech chunks using Silero VAD,
    merge short pauses (<= 0.6s) up to <= 24s, split >25s runs on the lowest-RMS valley,
    and enforce an RMS energy floor so dead air / thermal noise never enters ASR.
    """
    if audio_16k is None or audio_16k.size < int(0.2 * SR):
        return []

    total_samples = int(audio_16k.size)
    spans_samples: List[Tuple[int, int]] = []

    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        opts = VadOptions(
            threshold=float(vad_threshold),
            neg_threshold=max(0.10, float(vad_threshold) - 0.15),
            min_speech_duration_ms=int(min_speech_ms),
            min_silence_duration_ms=int(min_silence_ms),
            speech_pad_ms=200,
        )
        raw_stamps = get_speech_timestamps(audio_16k.astype(np.float32), vad_options=opts, sampling_rate=SR)
        for sp in raw_stamps:
            s_idx = max(0, int(sp.get("start", 0)))
            e_idx = min(total_samples, int(sp.get("end", 0)))
            if e_idx > s_idx:
                spans_samples.append((s_idx, e_idx))
    except Exception as vad_exc:
        logger.warning("Silero VAD unavailable (%s); falling back to RMS envelope chunker", vad_exc)
        win = int(0.05 * SR)
        hop = win
        active = False
        seg_start = 0
        last_active = 0
        for pos in range(0, total_samples - win + 1, hop):
            fr = audio_16k[pos : pos + win]
            rms = float(np.sqrt(np.mean(np.square(fr))))
            if rms >= rms_floor:
                if not active:
                    active = True
                    seg_start = max(0, pos - int(0.15 * SR))
                last_active = pos + win
                if (pos + win - seg_start) / SR >= max_chunk_sec:
                    spans_samples.append((seg_start, pos + win))
                    active = False
            elif active and (pos - last_active) >= int((min_silence_ms / 1000.0) * SR):
                spans_samples.append((seg_start, min(total_samples, last_active + int(0.15 * SR))))
                active = False
        if active:
            spans_samples.append((seg_start, min(total_samples, last_active + int(0.15 * SR))))

    max_samples = int(max_chunk_sec * SR)
    merge_gap_samples = int(merge_gap_sec * SR)
    merged: List[Tuple[int, int]] = []
    for s_idx, e_idx in spans_samples:
        if not merged:
            merged.append((s_idx, e_idx))
        else:
            ps, pe = merged[-1]
            if (s_idx - pe) <= merge_gap_samples and (e_idx - ps) <= max_samples:
                merged[-1] = (ps, e_idx)
            else:
                merged.append((s_idx, e_idx))

    hard_limit = int((max_chunk_sec + 1.0) * SR)
    chunks: List[Dict[str, Any]] = []
    for ps, pe in merged:
        cur = ps
        while (pe - cur) > hard_limit:
            search_start = cur + int(12.0 * SR)
            search_end = min(pe - int(2.0 * SR), cur + int(23.5 * SR))
            if search_end <= search_start:
                split_pt = cur + max_samples
            else:
                win_len = int(0.10 * SR)
                step = int(0.02 * SR)
                best_rms = 1e9
                split_pt = search_start + (search_end - search_start) // 2
                for pos in range(search_start, search_end - win_len, step):
                    w = audio_16k[pos : pos + win_len]
                    r = float(np.sqrt(np.mean(np.square(w))))
                    if r < best_rms:
                        best_rms = r
                        split_pt = pos + win_len // 2
            sl = audio_16k[cur:split_pt]
            if sl.size >= int(0.25 * SR):
                rms = float(np.sqrt(np.mean(np.square(sl))))
                if rms >= rms_floor:
                    chunks.append(
                        {
                            "start_sample": cur,
                            "end_sample": split_pt,
                            "start_sec": round(cur / SR, 3),
                            "end_sec": round(split_pt / SR, 3),
                            "rms": round(rms, 6),
                        }
                    )
            cur = split_pt
        if pe > cur and (pe - cur) >= int(0.25 * SR):
            sl = audio_16k[cur:pe]
            rms = float(np.sqrt(np.mean(np.square(sl))))
            if rms >= rms_floor:
                chunks.append(
                    {
                        "start_sample": cur,
                        "end_sample": pe,
                        "start_sec": round(cur / SR, 3),
                        "end_sec": round(pe / SR, 3),
                        "rms": round(rms, 6),
                    }
                )

    return chunks


def _max_normalized_xcorr(mic_seg: np.ndarray, sys_win: np.ndarray) -> float:
    """Compute peak normalized cross-correlation between mic chunk and padded system window."""
    if mic_seg.size < 320 or sys_win.size < mic_seg.size:
        return 0.0
    m = mic_seg.astype(np.float64) - float(np.mean(mic_seg))
    s = sys_win.astype(np.float64) - float(np.mean(sys_win))
    m_energy = float(np.dot(m, m))
    if m_energy < 1e-9:
        return 0.0

    # FFT cross-correlation over valid sliding offsets
    n_fft = 1 << int(np.ceil(np.log2(len(s) + len(m))))
    corr = np.fft.irfft(np.fft.rfft(s, n_fft) * np.conj(np.fft.rfft(m, n_fft)), n_fft)
    valid_len = len(s) - len(m) + 1
    if valid_len <= 0:
        return 0.0
    corr_valid = np.abs(corr[:valid_len])

    # Local energy of sys_win sliding windows via cumulative sum
    sq = np.concatenate(([0.0], np.cumsum(s * s)))
    win_energies = sq[len(m) : len(m) + valid_len] - sq[:valid_len]
    win_energies = np.maximum(win_energies, 1e-9)
    norm_corr = corr_valid / np.sqrt(m_energy * win_energies)
    return float(np.max(norm_corr))


def suppress_mic_echo_bleed(
    mic_audio: np.ndarray,
    sys_audio: np.ndarray,
    mic_chunks: List[Dict[str, Any]],
    *,
    xcorr_threshold: float = 0.30,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Filter out mic VAD chunks that are acoustic bleed from open speakers playing system audio.
    Preserves true near-end speech and double-talk where mic energy significantly exceeds bleed.
    """
    if (
        mic_audio is None
        or sys_audio is None
        or mic_audio.size == 0
        or sys_audio.size == 0
        or not mic_chunks
    ):
        kept = list(mic_chunks or [])
        return kept, {
            "mic_chunks_total": len(kept),
            "mic_chunks_suppressed_bleed": 0,
            "mic_chunks_kept": len(kept),
        }

    kept_chunks: List[Dict[str, Any]] = []
    suppressed = 0
    pad_before = int(0.15 * SR)
    pad_after = int(0.35 * SR)

    for ch in mic_chunks:
        s_idx = int(ch["start_sample"])
        e_idx = int(ch["end_sample"])
        mic_sl = mic_audio[s_idx:e_idx]
        sys_s = max(0, s_idx - pad_before)
        sys_e = min(sys_audio.size, e_idx + pad_after)
        sys_sl = sys_audio[sys_s:sys_e]

        is_bleed = False
        if sys_sl.size >= mic_sl.size + 160:
            # Evaluate RMS on direct aligned overlap inside system track
            ov_s = min(s_idx, sys_audio.size)
            ov_e = min(e_idx, sys_audio.size)
            sys_direct = sys_audio[ov_s:ov_e] if ov_e > ov_s else sys_sl
            sys_rms = float(np.sqrt(np.mean(np.square(sys_direct)))) if sys_direct.size else 0.0
            mic_rms = float(ch.get("rms") or np.sqrt(np.mean(np.square(mic_sl))))
            if sys_rms >= 0.003:
                xcorr = _max_normalized_xcorr(mic_sl, sys_sl)
                if (xcorr >= xcorr_threshold and mic_rms <= 1.8 * sys_rms) or (xcorr >= 0.45):
                    is_bleed = True

        if is_bleed:
            suppressed += 1
        else:
            kept_chunks.append(ch)

    return kept_chunks, {
        "mic_chunks_total": len(mic_chunks),
        "mic_chunks_suppressed_bleed": suppressed,
        "mic_chunks_kept": len(kept_chunks),
    }


def _strip_cohere_special_tokens(text: str) -> str:
    s = re.sub(r"<\|[^|>]*\|>", " ", str(text or ""))
    s = re.sub(r"<asr_text>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def apply_meeting_glossary(
    raw_text: str,
    vocabulary: Optional[List[Dict[str, Any]]] = None,
    glossary_items: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """
    Apply safe deterministic domain/participant spelling normalization to produce `corrected_text`
    while strictly keeping `raw_text` immutable.
    Never modifies voice-typing control phrases inside meeting transcripts.
    """
    immutable_raw = str(raw_text or "").strip()
    if not immutable_raw:
        return "", ""

    blocked = {
        "in caps", "all caps", "in a cats", "in cats", "all cats",
        "press enter", "new line", "delete that", "scratch that", "send it",
    }

    rules: List[Tuple[str, str]] = []
    seen_low: set[str] = set()

    for row in (vocabulary or []):
        if not int(row.get("is_active", 1) or 1):
            continue
        cat = str(row.get("category") or "").strip().lower()
        if "accent" in cat:
            continue
        sp = str(row.get("spoken_phrase") or "").strip()
        rep = str(row.get("replacement") or "").strip()
        if sp and rep and sp.lower() not in blocked and len(sp) >= 3 and sp.lower() not in seen_low:
            rules.append((sp, rep))
            seen_low.add(sp.lower())

    for item in (glossary_items or []):
        itype = str(item.get("item_type") or "").lower()
        if itype not in ("glossary", "acronym", "entity", "participant"):
            continue
        canonical = str(item.get("title") or "").strip()
        if not canonical or len(canonical) < 2:
            continue
        meta_raw = item.get("metadata_json") or "{}"
        meta = {}
        if isinstance(meta_raw, dict):
            meta = meta_raw
        elif isinstance(meta_raw, str):
            try:
                import json
                meta = json.loads(meta_raw) if meta_raw.strip().startswith("{") else {}
            except Exception:
                meta = {}
        aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.split(",") if a.strip()]
        for al in aliases:
            al_s = str(al).strip()
            if al_s and len(al_s) >= 3 and al_s.lower() != canonical.lower() and al_s.lower() not in seen_low:
                rules.append((al_s, canonical))
                seen_low.add(al_s.lower())

    corrected = immutable_raw
    # Sort longest spoken phrase first to avoid partial collisions
    for sp, rep in sorted(rules, key=lambda r: -len(r[0])):
        pat = re.compile(rf"(?<!\w){re.escape(sp)}(?!\w)", re.IGNORECASE)
        corrected = pat.sub(rep, corrected)

    corrected = re.sub(r"\s+", " ", corrected).strip()
    return immutable_raw, corrected


def consolidate_diarization_turns(
    raw_turns: List[Dict[str, Any]],
    *,
    min_cluster_sec: float = DEFAULT_MIN_CLUSTER_SEC,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Consolidate tiny diarization micro-clusters (< min_cluster_sec) into their closest
    dominant cluster and renumber speakers chronologically (Speaker 1, Speaker 2, ...).
    """
    if not raw_turns:
        return [], {"num_clusters_raw": 0, "num_clusters_final": 0, "speaker_durations": {}}

    turns = [dict(t) for t in sorted(raw_turns, key=lambda x: (float(x["start_sec"]), float(x["end_sec"])))]
    dur_by_spk: Dict[str, float] = {}
    for t in turns:
        spk = str(t["speaker"])
        dur_by_spk[spk] = dur_by_spk.get(spk, 0.0) + max(0.05, float(t["end_sec"]) - float(t["start_sec"]))

    raw_cluster_count = len(dur_by_spk)
    major = {s for s, d in dur_by_spk.items() if d >= min_cluster_sec}
    if not major and dur_by_spk:
        # Keep top cluster if whole meeting is short
        top_s = max(dur_by_spk.items(), key=lambda kv: kv[1])[0]
        major = {top_s}

    if len(major) < len(dur_by_spk):
        for idx, t in enumerate(turns):
            spk = str(t["speaker"])
            if spk in major:
                continue
            mid_t = 0.5 * (float(t["start_sec"]) + float(t["end_sec"]))
            best_spk = None
            best_gap = 1e12
            # Search nearest major turn temporally
            for j, cand in enumerate(turns):
                cspk = str(cand["speaker"])
                if cspk not in major:
                    continue
                cmid = 0.5 * (float(cand["start_sec"]) + float(cand["end_sec"]))
                gap = abs(cmid - mid_t)
                if gap < best_gap:
                    best_gap = gap
                    best_spk = cspk
            if best_spk is not None:
                t["speaker"] = best_spk

    # Renumber chronologically by first appearance -> Speaker 1, Speaker 2, ...
    remap: Dict[str, str] = {}
    final_durations: Dict[str, float] = {}
    for t in turns:
        orig = str(t["speaker"])
        if orig not in remap:
            remap[orig] = f"Speaker {len(remap) + 1}"
        lbl = remap[orig]
        t["speaker"] = lbl
        final_durations[lbl] = final_durations.get(lbl, 0.0) + max(0.05, float(t["end_sec"]) - float(t["start_sec"]))

    # Merge adjacent identical speaker turns separated by <= 0.35s
    merged: List[Dict[str, Any]] = []
    for t in turns:
        if merged and merged[-1]["speaker"] == t["speaker"] and (float(t["start_sec"]) - float(merged[-1]["end_sec"])) <= 0.35:
            merged[-1]["end_sec"] = max(float(merged[-1]["end_sec"]), float(t["end_sec"]))
        else:
            merged.append(t)

    stats = {
        "num_clusters_raw": raw_cluster_count,
        "num_clusters_final": len(remap),
        "speaker_durations": {k: round(v, 2) for k, v in final_durations.items()},
    }
    return merged, stats


def assign_speaker_to_segment(
    start_sec: float,
    end_sec: float,
    diar_turns: List[Dict[str, Any]],
    default_speaker: str = "Speaker 1",
) -> str:
    """Map an ASR segment to the diarized speaker with highest temporal overlap (nearest turn fallback)."""
    if not diar_turns:
        return default_speaker

    ov_scores: Dict[str, float] = {}
    for dt in diar_turns:
        s0 = max(start_sec, float(dt["start_sec"]))
        e0 = min(end_sec, float(dt["end_sec"]))
        if e0 > s0:
            spk = str(dt["speaker"])
            ov_scores[spk] = ov_scores.get(spk, 0.0) + (e0 - s0)

    if ov_scores:
        return max(ov_scores.items(), key=lambda kv: kv[1])[0]

    mid = 0.5 * (start_sec + end_sec)
    nearest_spk = default_speaker
    nearest_dist = 2.0  # Max 2.0s tolerance for nearest-turn fallback
    for dt in diar_turns:
        d = 0.0
        if mid < float(dt["start_sec"]):
            d = float(dt["start_sec"]) - mid
        elif mid > float(dt["end_sec"]):
            d = mid - float(dt["end_sec"])
        if d < nearest_dist:
            nearest_dist = d
            nearest_spk = str(dt["speaker"])
    return nearest_spk


class PostCallEnglishASRPipeline:
    """
    Production Post-Call English Meeting ASR + Diarization Pipeline.
    Single ASR model: Cohere Transcribe 2B (sherpa-onnx CPU INT8).
    """

    MODEL_ID = "cohere-transcribe-2b-en-int8"
    BACKEND_ID = "sherpa_onnx_cohere"

    def __init__(
        self,
        models_dir: Optional[str | Path] = None,
        num_threads: Optional[int] = None,
    ) -> None:
        self.models_dir = Path(models_dir) if models_dir else get_user_models_root()
        self.model_manager = MeetingModelManager(self.models_dir)
        self.num_threads = num_threads or compute_safe_bg_threads()
        self._recognizer: Optional[Any] = None
        self.last_track_stats: Dict[str, Any] = {}
        self.last_diarization_stats: Dict[str, Any] = {}

    def is_available(self) -> bool:
        return bool(self.model_manager.get_artifact_status("meeting_asr").get("installed"))

    def _ensure_asr_loaded(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        ok, msg = self.model_manager.verify_artifact("meeting_asr")
        if not ok:
            raise RuntimeError(
                f"Cohere Transcribe 2B meeting ASR model is not installed or incomplete ({msg}). "
                "Open Settings -> Notetaker -> Meeting AI Models to download/verify it."
            )
        import sherpa_onnx

        asr_dir = self.model_manager.get_artifact_dir("meeting_asr")
        t0 = time.perf_counter()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_cohere_transcribe(
            encoder=str(asr_dir / "encoder.int8.onnx"),
            decoder=str(asr_dir / "decoder.int8.onnx"),
            tokens=str(asr_dir / "tokens.txt"),
            language="en",
            use_punct=True,
            use_itn=True,
            num_threads=int(self.num_threads),
            provider="cpu",
        )
        logger.info(
            "Loaded Cohere Transcribe 2B (CPU INT8, threads=%d) in %.2fs",
            self.num_threads,
            time.perf_counter() - t0,
        )
        return self._recognizer

    def unload(self) -> None:
        self._recognizer = None
        gc.collect()

    def _transcribe_chunk(self, audio_f32: np.ndarray) -> str:
        rec = self._ensure_asr_loaded()
        st = rec.create_stream()
        st.accept_waveform(SR, audio_f32.astype(np.float32))
        rec.decode_stream(st)
        raw = _strip_cohere_special_tokens(getattr(st.result, "text", "") or "")
        return raw

    def _run_diarization(
        self,
        sys_audio: np.ndarray,
        threshold: float = DEFAULT_DIARIZATION_THRESHOLD,
        min_cluster_sec: float = DEFAULT_MIN_CLUSTER_SEC,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Run sherpa-onnx PyAnnote + WeSpeaker neural diarization on system audio track."""
        ok_dia, msg_dia = self.model_manager.verify_artifact("meeting_diarization")
        if not ok_dia:
            return [], {"enabled": False, "fallback_reason": f"Diarization model unavailable: {msg_dia}"}
        if sys_audio is None or sys_audio.size < int(1.0 * SR):
            return [], {"enabled": True, "num_clusters_final": 0, "fallback_reason": ""}

        try:
            import sherpa_onnx

            dia_dir = self.model_manager.get_artifact_dir("meeting_diarization")
            t0 = time.perf_counter()
            cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(dia_dir / "pyannote-seg-3-0" / "model.onnx"),
                    ),
                    num_threads=min(4, self.num_threads),
                    provider="cpu",
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(dia_dir / "wespeaker_en_voxceleb_resnet34_LM.onnx"),
                    num_threads=min(4, self.num_threads),
                    provider="cpu",
                ),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=-1,
                    threshold=float(threshold),
                ),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            if not cfg.validate():
                return [], {"enabled": False, "fallback_reason": "Invalid sherpa-onnx diarization config"}

            sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
            if cancel_check and cancel_check():
                return [], {"enabled": False, "fallback_reason": "Cancelled before diarization"}

            raw_turns: List[Dict[str, Any]] = []
            sd_obj = sd
            try:
                res = sd_obj.process(sys_audio.astype(np.float32)).sort_by_start_time()
                for seg in res:
                    raw_turns.append(
                        {
                            "start_sec": round(float(seg.start), 3),
                            "end_sec": round(float(seg.end), 3),
                            "speaker": f"Speaker {int(seg.speaker) + 1}",
                        }
                    )
            finally:
                del sd_obj
                del sd
                gc.collect()
            turns, stats = consolidate_diarization_turns(raw_turns, min_cluster_sec=min_cluster_sec)
            stats["enabled"] = True
            stats["elapsed_sec"] = round(time.perf_counter() - t0, 2)
            stats["threshold"] = float(threshold)
            stats["raw_turns"] = len(raw_turns)
            stats["merged_turns"] = len(turns)
            stats["fallback_reason"] = ""
            logger.info(
                "Neural diarization complete in %.2fs: %d turns -> %d speakers (raw clusters=%d)",
                stats["elapsed_sec"],
                len(turns),
                stats["num_clusters_final"],
                stats["num_clusters_raw"],
            )
            return turns, stats
        except Exception as exc:
            logger.warning("Neural diarization failed (falling back cleanly to single remote speaker): %s", exc)
            return [], {"enabled": False, "fallback_reason": f"Diarization error: {exc}"}

    def transcribe_meeting_dual_track(
        self,
        *,
        mixed_wav_path: str | Path = "",
        mic_wav_path: str | Path = "",
        sys_wav_path: str | Path = "",
        vocabulary: Optional[List[Dict[str, Any]]] = None,
        glossary_items: Optional[List[Dict[str, Any]]] = None,
        speaker_aliases: Optional[Dict[str, str]] = None,
        remote_speaker_label: str = "Speaker 1",
        mic_speaker_label: str = "You (Mic)",
        diarization_enabled: bool = True,
        diarization_threshold: float = DEFAULT_DIARIZATION_THRESHOLD,
        echo_bleed_suppression: bool = True,
        glossary_correction: bool = True,
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
        asr_checkpoint_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Transcribe a recorded meeting end-to-end (post-call) with Cohere Transcribe 2B,
        acoustic echo-bleed suppression, Silero VAD + RMS silence gate, neural speaker
        diarization, and non-destructive glossary normalization.
        """
        t_all0 = time.perf_counter()
        self.last_track_stats = {}
        self.last_diarization_stats = {}

        if progress_callback:
            progress_callback("Loading recorded audio tracks (read-only)...")

        sys_audio = load_wav_mono_16k_readonly(sys_wav_path) if sys_wav_path else np.zeros(0, dtype=np.float32)
        mic_audio = load_wav_mono_16k_readonly(mic_wav_path) if mic_wav_path else np.zeros(0, dtype=np.float32)
        mixed_only = False
        if sys_audio.size == 0 and mic_audio.size == 0 and mixed_wav_path:
            sys_audio = load_wav_mono_16k_readonly(mixed_wav_path)
            mixed_only = True

        # 1. Silero VAD + RMS silence floor per track
        if progress_callback:
            progress_callback("Running Silero VAD speech segmentation...")
        sys_chunks = detect_vad_speech_chunks(sys_audio)
        mic_chunks = detect_vad_speech_chunks(mic_audio)

        bleed_stats = {
            "mic_chunks_total": len(mic_chunks),
            "mic_chunks_suppressed_bleed": 0,
            "mic_chunks_kept": len(mic_chunks),
        }
        if echo_bleed_suppression and sys_audio.size > 0 and mic_chunks:
            mic_chunks, bleed_stats = suppress_mic_echo_bleed(mic_audio, sys_audio, mic_chunks)
            logger.info(
                "Echo-bleed filter: %d/%d mic chunks suppressed as speaker bleed, %d genuine mic chunks kept.",
                bleed_stats["mic_chunks_suppressed_bleed"],
                bleed_stats["mic_chunks_total"],
                bleed_stats["mic_chunks_kept"],
            )

        # 2. Load Cohere Transcribe 2B once & decode chunks chronologically
        self._ensure_asr_loaded()
        total_chunks = len(sys_chunks) + len(mic_chunks)
        done_chunks = 0

        raw_segments: List[Dict[str, Any]] = []
        track_chunk_counts = {"system": len(sys_chunks), "mic": len(mic_chunks)}
        track_decoded_counts = {"system": 0, "mic": 0}
        track_dropped_counts = {"system": 0, "mic": 0}

        def _decode_track(
            audio_arr: np.ndarray,
            chunks: List[Dict[str, Any]],
            track_name: str,
            base_speaker: str,
        ) -> None:
            nonlocal done_chunks
            for ch in chunks:
                if cancel_check and cancel_check():
                    return
                while pause_check and pause_check():
                    if cancel_check and cancel_check():
                        return
                    time.sleep(0.2)

                sl = audio_arr[int(ch["start_sample"]) : int(ch["end_sample"])]
                raw_txt = self._transcribe_chunk(sl)
                if (os.cpu_count() or 4) <= 8:
                    time.sleep(0.005)
                done_chunks += 1
                if progress_callback and (done_chunks == 1 or done_chunks % 10 == 0 or done_chunks == total_chunks):
                    progress_callback(f"Transcribing English speech ({done_chunks}/{max(1, total_chunks)} chunks)...")

                dur_s = max(0.05, float(ch["end_sec"]) - float(ch["start_sec"]))
                cleaned_raw = clean_repetition_tail(raw_txt).strip()
                if not cleaned_raw or is_hallucination(cleaned_raw, duration_sec=dur_s):
                    track_dropped_counts[track_name] += 1
                    continue

                if glossary_correction:
                    imm_raw, corr_txt = apply_meeting_glossary(
                        cleaned_raw,
                        vocabulary=vocabulary,
                        glossary_items=glossary_items,
                    )
                else:
                    imm_raw = cleaned_raw
                    corr_txt = ""

                final_txt = corr_txt if (corr_txt and corr_txt != imm_raw) else imm_raw
                track_decoded_counts[track_name] += 1
                raw_segments.append(
                    {
                        "start_time": round(float(ch["start_sec"]), 2),
                        "end_time": round(float(ch["end_sec"]), 2),
                        "speaker": base_speaker,
                        "source_track": "mixed" if mixed_only else track_name,
                        "raw_text": imm_raw,
                        "normalized_text": imm_raw,
                        "corrected_text": corr_txt if (corr_txt and corr_txt != imm_raw) else "",
                        "text": final_txt,
                        "confidence": 0.94,
                        "asr_model": self.MODEL_ID,
                        "asr_backend": self.BACKEND_ID,
                        "asr_device": "cpu",
                        "asr_compute_type": "int8",
                        "detected_language": "en",
                        "transcript_stage": "final",
                        "transcript_state": "final",
                    }
                )

        try:
            _decode_track(sys_audio, sys_chunks, "system", remote_speaker_label or "Speaker 1")
            if not (cancel_check and cancel_check()):
                _decode_track(mic_audio, mic_chunks, "mic", mic_speaker_label)
        finally:
            self.unload()

        if cancel_check and cancel_check():
            return []

        if asr_checkpoint_callback is not None and raw_segments:
            try:
                asr_checkpoint_callback(raw_segments)
            except Exception as ck_exc:
                logger.debug("ASR checkpoint callback skipped: %s", ck_exc)

        # 3. Neural Speaker Diarization on system track (separate pass after ASR unload)
        diar_turns: List[Dict[str, Any]] = []
        diar_stats: Dict[str, Any] = {"enabled": False, "fallback_reason": "Diarization disabled"}
        if diarization_enabled and sys_audio.size >= int(1.0 * SR):
            if progress_callback:
                progress_callback("Running neural speaker diarization (PyAnnote + WeSpeaker)...")
            diar_turns, diar_stats = self._run_diarization(
                sys_audio,
                threshold=diarization_threshold,
                min_cluster_sec=DEFAULT_MIN_CLUSTER_SEC,
                cancel_check=cancel_check,
            )

        self.last_diarization_stats = diar_stats

        # Assign speaker labels + apply persistent speaker aliases
        aliases = dict(speaker_aliases or {})
        use_diar_turns = bool(diar_turns) and int(diar_stats.get("num_clusters_final", 0)) >= 1
        for seg in raw_segments:
            if seg["source_track"] in ("system", "mixed"):
                if use_diar_turns:
                    spk = assign_speaker_to_segment(
                        float(seg["start_time"]),
                        float(seg["end_time"]),
                        diar_turns,
                        default_speaker=remote_speaker_label or "Speaker 1",
                    )
                    # If 1 remote speaker & caller passed an explicit person name (1-on-1 call), keep explicit name
                    if diar_stats.get("num_clusters_final", 0) == 1 and remote_speaker_label and remote_speaker_label != "Speaker 1":
                        spk = remote_speaker_label
                else:
                    spk = remote_speaker_label or "Speaker 1"
            else:
                spk = mic_speaker_label
            seg["speaker"] = aliases.get(spk, spk)

        raw_segments.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
        for idx, seg in enumerate(raw_segments):
            seg["segment_index"] = idx

        elapsed_total = round(time.perf_counter() - t_all0, 2)
        track_stats: Dict[str, Any] = {
            "pipeline": "post_call_cohere_en_int8",
            "elapsed_sec": elapsed_total,
            "audio_duration_sec": round(max(sys_audio.size, mic_audio.size) / SR, 2),
            "system_chunks": track_chunk_counts["system"],
            "mic_chunks_total": bleed_stats["mic_chunks_total"],
            "mic_chunks_suppressed_bleed": bleed_stats["mic_chunks_suppressed_bleed"],
            "mic_chunks_kept": bleed_stats["mic_chunks_kept"],
            "decoded_segments": len(raw_segments),
            "dropped_hallucinations_or_empty": track_dropped_counts["system"] + track_dropped_counts["mic"],
            "diarization": diar_stats,
        }
        if sys_audio.size > 0 and not mixed_only:
            track_stats["system"] = {
                "samples_captured": int(sys_audio.size),
                "samples_processed": int(sys_audio.size),
                "cursor_sec": round(float(sys_audio.size) / SR, 3),
                "cancelled": False,
            }
        if mic_audio.size > 0:
            track_stats["mic"] = {
                "samples_captured": int(mic_audio.size),
                "samples_processed": int(mic_audio.size),
                "cursor_sec": round(float(mic_audio.size) / SR, 3),
                "cancelled": False,
            }
        if mixed_only and sys_audio.size > 0:
            track_stats["mixed"] = {
                "samples_captured": int(sys_audio.size),
                "samples_processed": int(sys_audio.size),
                "cursor_sec": round(float(sys_audio.size) / SR, 3),
                "cancelled": False,
            }
        self.last_track_stats = track_stats
        logger.info(
            "Post-call Cohere EN pipeline finished in %.2fs -> %d segments (%d bleed suppressed, %s speakers)",
            elapsed_total,
            len(raw_segments),
            bleed_stats["mic_chunks_suppressed_bleed"],
            diar_stats.get("num_clusters_final", 1),
        )
        return raw_segments

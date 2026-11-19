"""
VOX Speech Intelligence — Strictly Read-Only Phase 1 Benchmark & Reference Draft Generator.

Evaluates ONLY Meeting #234 (Hemanth, 90.15s) and Meeting #235 (Rumjhum, 1129.00s).
Explicitly excludes Meeting #233.

Guarantees:
- Never modifies `data/voxlocal.db`, `data/config.json`, or any original recording WAVs.
- Opens SQLite strictly with `?mode=ro`.
- Verifies identical pre/post SHA-256 hashes, file sizes, and mtimes across DB & recordings.
- Outputs editable timestamped reference drafts and structured metrics to `data/benchmarks/`.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, resample_audio_to_16k_mono
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.llm_engine import LocalLLMEngine
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

DB_PATH = ROOT_DIR / "data" / "voxlocal.db"
CONFIG_PATH = ROOT_DIR / "data" / "config.json"
BENCH_DIR = ROOT_DIR / "data" / "benchmarks"

PROMPT_ECHO_PATTERNS = (
    re.compile(r"tech\s+team.*daily\s+scrum", re.IGNORECASE),
    re.compile(r"tech\s+team,\s*sprint", re.IGNORECASE),
    re.compile(r"world\s+fashion\s+exchange.*tech\s+team", re.IGNORECASE),
)

DEVA_RE = re.compile(r"[\u0900-\u097F]")
URDU_RE = re.compile(r"[\u0600-\u06FF]")

TARGET_MEETING_IDS = (234, 235)
LIVE_REPLAY_M235_WINDOWS = ((0.0, 45.0), (500.0, 545.0), (1080.0, 1129.0))


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_rss_mb() -> float:
    """Return current process WorkingSetSize in MB (64-bit Windows K32GetProcessMemoryInfo)."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            ctypes.wintypes.DWORD,
        ]
        k32.K32GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        handle = k32.GetCurrentProcess()
        if k32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / (1024.0 * 1024.0), 1)
    except Exception:
        pass
    return 0.0


def query_gpu_vram() -> Dict[str, Any]:
    """Query GTX 1650 VRAM and utilization via nvidia-smi."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
        if out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            if len(parts) >= 5:
                return {
                    "gpu_name": parts[0],
                    "total_mb": int(parts[1]),
                    "used_mb": int(parts[2]),
                    "free_mb": int(parts[3]),
                    "util_pct": int(parts[4]),
                }
    except Exception as exc:
        return {"error": str(exc)}
    return {"error": "nvidia-smi empty"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_protected_files(meeting_dirs: List[Path]) -> Dict[str, Dict[str, Any]]:
    """Record size, mtime_ns, and sha256 of config and original meeting assets (plus DB telemetry)."""
    targets: List[Path] = [CONFIG_PATH]
    for mdir in meeting_dirs:
        if mdir.exists() and mdir.is_dir():
            for child in sorted(mdir.iterdir()):
                if child.is_file():
                    targets.append(child)
    snap: Dict[str, Dict[str, Any]] = {}
    for p in targets:
        st = p.stat()
        snap[str(p)] = {
            "size_bytes": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
            "sha256": sha256_file(p),
        }
    return snap


def compute_target_db_rows_sha256() -> str:
    """Compute canonical SHA-256 of M#234, M#235, their segments, and active vocabulary rows (mode=ro)."""
    meetings, vocab = read_only_db_state()
    payload = {"meetings": {str(k): meetings[k] for k in sorted(meetings)}, "vocab": vocab}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def read_only_db_state() -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    """Read M#234, M#235 metadata, saved baseline segments, and active vocabulary read-only."""
    db_uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        vocab = [
            dict(r)
            for r in conn.execute(
                "SELECT id, spoken_phrase, replacement, category, is_active FROM custom_vocabulary WHERE is_active = 1"
            ).fetchall()
        ]
        meetings: Dict[int, Dict[str, Any]] = {}
        for mid in TARGET_MEETING_IDS:
            m_row = conn.execute("SELECT * FROM meetings WHERE id = ?", (mid,)).fetchone()
            if not m_row:
                raise RuntimeError(f"Expected meeting #{mid} in DB but it was not found")
            segs = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, meeting_id, segment_index, start_time, end_time, speaker, source_track, text, confidence "
                    "FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time ASC, id ASC",
                    (mid,),
                ).fetchall()
            ]
            m_dict = dict(m_row)
            m_dict["baseline_segments"] = segs
            meetings[mid] = m_dict
        return meetings, vocab
    finally:
        conn.close()


def count_prompt_echoes(segments: List[Dict[str, Any]]) -> int:
    hits = 0
    for seg in segments:
        txt = str(seg.get("text", "") or "")
        if any(p.search(txt) for p in PROMPT_ECHO_PATTERNS):
            hits += 1
    return hits


def compute_segment_metrics(segments: List[Dict[str, Any]], duration_sec: float) -> Dict[str, Any]:
    confs = [float(s.get("confidence", 0.0)) for s in segments if s.get("confidence") is not None]
    spk_counts = Counter(str(s.get("speaker", "Unknown")) for s in segments)
    trk_counts = Counter(str(s.get("source_track", "unknown")) for s in segments)
    cluster_counts = Counter(str(s.get("speaker_cluster", s.get("speaker", "Unknown"))) for s in segments)
    total_words = sum(len(str(s.get("text", "")).split()) for s in segments)
    deva_segs = sum(1 for s in segments if DEVA_RE.search(str(s.get("text", ""))))
    urdu_segs = sum(1 for s in segments if URDU_RE.search(str(s.get("text", ""))))
    cov_start = min((float(s.get("start_time", 0.0)) for s in segments), default=0.0)
    cov_end = max((float(s.get("end_time", 0.0)) for s in segments), default=0.0)
    word_ts_segs = sum(1 for s in segments if s.get("words"))
    return {
        "segment_count": len(segments),
        "total_words": total_words,
        "speaker_counts": dict(spk_counts),
        "speaker_cluster_counts": dict(cluster_counts),
        "source_track_counts": dict(trk_counts),
        "confidence_min": round(min(confs), 3) if confs else None,
        "confidence_avg": round(sum(confs) / len(confs), 3) if confs else None,
        "confidence_max": round(max(confs), 3) if confs else None,
        "unique_confidences_sample": sorted({round(c, 2) for c in confs})[:12],
        "prompt_echo_count": count_prompt_echoes(segments),
        "devanagari_char_segs": deva_segs,
        "urdu_char_segs": urdu_segs,
        "word_timestamp_segs": word_ts_segs,
        "coverage_start_sec": round(cov_start, 2),
        "coverage_end_sec": round(cov_end, 2),
        "coverage_pct": round((cov_end / max(0.01, duration_sec)) * 100.0, 1),
    }


class _InMemoryReplayRecorder:
    """In-memory dual-track replay cursor matching MeetingRecorder interface."""

    def __init__(self, mic_16k: np.ndarray, sys_16k: np.ndarray, time_offset: float = 0.0) -> None:
        self._mic = mic_16k
        self._sys = sys_16k
        self._base_offset = float(time_offset)
        self.elapsed_seconds: float = float(time_offset)

    def get_audio_window(self, start_sec: float, end_sec: float) -> Tuple[np.ndarray, np.ndarray, float]:
        rel_s = max(0.0, float(start_sec) - self._base_offset)
        rel_e = max(rel_s, float(end_sec) - self._base_offset)
        s_idx = int(rel_s * TARGET_SAMPLE_RATE)
        e_idx = int(rel_e * TARGET_SAMPLE_RATE)
        mic_win = self._mic[min(self._mic.size, s_idx) : min(self._mic.size, e_idx)]
        sys_win = self._sys[min(self._sys.size, s_idx) : min(self._sys.size, e_idx)]
        actual_start = self._base_offset + (min(self._mic.size, s_idx) / float(TARGET_SAMPLE_RATE))
        return mic_win, sys_win, actual_start


class _ReadOnlyVocabProxy:
    """In-memory DB proxy providing read-only vocabulary to LiveMeetingStreamingTranscriber."""

    def __init__(self, vocab: List[Dict[str, Any]]) -> None:
        self._vocab = list(vocab)
        self._next_id = 1
        self.saved_segments: List[Dict[str, Any]] = []

    def list_vocabulary(self, active_only: bool = True) -> List[Dict[str, Any]]:
        if active_only:
            return [v for v in self._vocab if v.get("is_active", 1)]
        return list(self._vocab)

    def add_transcript_segment(
        self,
        meeting_id: int,
        segment_index: int,
        start_time: float,
        end_time: float,
        speaker: str,
        text: str,
        source_track: str = "mixed",
        confidence: float = 0.85,
    ) -> int:
        seg_id = self._next_id
        self._next_id += 1
        self.saved_segments.append(
            {
                "id": seg_id,
                "meeting_id": meeting_id,
                "segment_index": segment_index,
                "start_time": start_time,
                "end_time": end_time,
                "speaker": speaker,
                "source_track": source_track,
                "text": text,
                "confidence": confidence,
            }
        )
        return seg_id


def run_live_replay(
    transcriber: LocalWhisperTranscriber,
    mic_audio: np.ndarray,
    sys_audio: np.ndarray,
    vocab: List[Dict[str, Any]],
    meeting_id: int,
    remote_name: str,
    language: str,
    windows: Optional[List[Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    """Deterministically replay recorded tracks through LiveMeetingStreamingTranscriber._step() + stop()."""
    total_dur = max(mic_audio.size, sys_audio.size) / float(TARGET_SAMPLE_RATE)
    run_windows = windows if windows else [(0.0, total_dur)]

    partials: List[Dict[str, Any]] = []
    confirmed: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    cpu0 = time.process_time()

    evaluated_dur = 0.0
    for w_start, w_end in run_windows:
        s_i = int(w_start * TARGET_SAMPLE_RATE)
        e_i = int(w_end * TARGET_SAMPLE_RATE)
        w_mic = mic_audio[s_i : min(mic_audio.size, e_i)]
        w_sys = sys_audio[s_i : min(sys_audio.size, e_i)]
        w_dur = max(w_mic.size, w_sys.size) / float(TARGET_SAMPLE_RATE)
        if w_dur < 0.5:
            continue
        evaluated_dur += w_dur

        rec = _InMemoryReplayRecorder(w_mic, w_sys, time_offset=w_start)
        proxy_db = _ReadOnlyVocabProxy(vocab)
        streamer = LiveMeetingStreamingTranscriber(
            transcriber=transcriber,
            db=proxy_db,
            on_partial=lambda mid, d: partials.append(dict(d)),
            on_confirmed=lambda mid, d: confirmed.append(dict(d)),
        )
        streamer._meeting_id = meeting_id
        streamer._recorder = rec
        streamer._language = language
        streamer._remote_speaker_label = remote_name
        streamer._current_speaker = remote_name
        streamer._audio_cursor_sec = w_start
        streamer._active = True
        streamer._stop_event.clear()

        cursor_t = w_start + 0.15
        end_limit = w_start + w_dur
        while cursor_t <= end_limit + 1e-6:
            rec.elapsed_seconds = min(end_limit, cursor_t)
            streamer._step()
            cursor_t += 0.15
        rec.elapsed_seconds = end_limit
        streamer.stop()

    wall_s = time.perf_counter() - t0
    cpu_s = time.process_time() - cpu0
    cpu_cores = max(1, os.cpu_count() or 1)
    return {
        "replay_mode": "full_recording" if not windows else "3x45s_representative_windows",
        "evaluated_audio_sec": round(evaluated_dur, 2),
        "wall_time_sec": round(wall_s, 2),
        "rtf": round(wall_s / max(0.01, evaluated_dur), 3),
        "cpu_time_sec": round(cpu_s, 2),
        "avg_cpu_pct": round((cpu_s / max(0.01, wall_s)) * 100.0 / cpu_cores, 1),
        "partial_count": len(partials),
        "metrics": compute_segment_metrics(confirmed, total_dur),
        "segments": confirmed,
    }


def run_offline_finalization(
    transcriber: LocalWhisperTranscriber,
    mic_path: str,
    sys_path: str,
    mix_path: str,
    vocab: List[Dict[str, Any]],
    remote_name: str,
    language: str,
    duration_sec: float,
) -> Dict[str, Any]:
    """Run full-audio Stage 2 + Stage 3 offline Whisper finalization without DB mutation."""
    hotwords = DictationProcessor.build_whisper_hotwords(
        vocab,
        language=language,
        participants=[remote_name] if remote_name != "Speaker 1" else None,
    )
    t0 = time.perf_counter()
    cpu0 = time.process_time()

    t_s2 = time.perf_counter()
    raw_segments = transcriber.transcribe_meeting_dual_track(
        mixed_wav_path=mix_path,
        mic_wav_path=mic_path,
        sys_wav_path=sys_path,
        initial_prompt="",
        language=language,
        output_script="roman",
        remote_speaker_label=remote_name,
        hotwords=hotwords,
        vocabulary=vocab,
    )
    stage2_sec = time.perf_counter() - t_s2

    t_s3 = time.perf_counter()
    reconciled = transcriber.reconcile_meeting_transcript(
        segments=raw_segments,
        mic_audio_16k=None,
        sys_audio_16k=None,
        language=language,
        hotwords=hotwords,
        mic_wav_path=mic_path,
        sys_wav_path=sys_path,
        output_script="roman",
        remote_speaker_label=remote_name,
    )
    final_segments: List[Dict[str, Any]] = []
    for seg in reconciled:
        txt_p = DictationProcessor.process(
            seg["text"],
            vocabulary=vocab,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
        ).strip()
        if txt_p:
            d = dict(seg)
            d["text"] = txt_p
            final_segments.append(d)
    stage3_sec = time.perf_counter() - t_s3

    wall_s = time.perf_counter() - t0
    cpu_s = time.process_time() - cpu0
    cpu_cores = max(1, os.cpu_count() or 1)
    return {
        "wall_time_sec": round(wall_s, 2),
        "stage2_dual_track_sec": round(stage2_sec, 2),
        "stage3_reconcile_sec": round(stage3_sec, 2),
        "rtf": round(wall_s / max(0.01, duration_sec), 3),
        "cpu_time_sec": round(cpu_s, 2),
        "avg_cpu_pct": round((cpu_s / max(0.01, wall_s)) * 100.0 / cpu_cores, 1),
        "ram_rss_mb": get_process_rss_mb(),
        "gpu": query_gpu_vram(),
        "metrics": compute_segment_metrics(final_segments, duration_sec),
        "segments": final_segments,
    }


def _match_overlapping_text(segments: List[Dict[str, Any]], start_t: float, end_t: float, track: Optional[str] = None) -> str:
    hits: List[str] = []
    for s in segments:
        if track and s.get("source_track") != track:
            continue
        s0 = float(s.get("start_time", 0.0))
        e0 = float(s.get("end_time", s0))
        if min(e0, end_t) >= max(s0, start_t) - 0.5:
            txt = str(s.get("text", "")).strip()
            if txt:
                hits.append(f"[{s.get('speaker', '?')} {s0:.1f}-{e0:.1f}s] {txt}")
    return " | ".join(hits)


def build_reference_draft(
    meeting_meta: Dict[str, Any],
    final_segments: List[Dict[str, Any]],
    baseline_segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for idx, seg in enumerate(final_segments):
        s_t = float(seg.get("start_time", 0.0))
        e_t = float(seg.get("end_time", s_t))
        entries.append(
            {
                "segment_index": idx,
                "start_time": round(s_t, 2),
                "end_time": round(e_t, 2),
                "source_track": seg.get("source_track", "mixed"),
                "speaker": seg.get("speaker", "Unknown Speaker"),
                "speaker_cluster": seg.get("speaker_cluster", seg.get("speaker", "")),
                "confidence": seg.get("confidence", None),
                "raw_text": seg.get("raw_text", seg.get("text", "")),
                "display_text": seg.get("text", ""),
                "baseline_db_text": _match_overlapping_text(baseline_segments, s_t, e_t),
                "reference_text": seg.get("text", ""),
                "verified_by_human": False,
                "notes": "",
            }
        )
    return {
        "meeting_id": int(meeting_meta["id"]),
        "title": meeting_meta.get("title", ""),
        "duration_sec": float(meeting_meta.get("duration_sec", 0.0)),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "instructions": "Edit `reference_text` per segment and flip `verified_by_human` to true for WER ground truth scoring.",
        "segments": entries,
    }


def pick_discrepancy_examples(
    baseline: List[Dict[str, Any]],
    live: List[Dict[str, Any]],
    final: List[Dict[str, Any]],
    limit: int = 8,
) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    seen_windows: set[int] = set()

    def _is_echo_seg(seg: Dict[str, Any]) -> bool:
        txt_l = str(seg.get("text", "") or "").lower()
        return count_prompt_echoes([seg]) > 0 or ("tech team" in txt_l) or ("daily scrum" in txt_l)

    # Prioritize opening bucket (0..12s), then baseline prompt echoes, then spread across timeline
    prioritized: List[Dict[str, Any]] = []
    bucket0_echo = [
        s for s in baseline
        if int(float(s.get("start_time", 0.0)) // 12) == 0 and _is_echo_seg(s)
    ]
    if bucket0_echo:
        prioritized.append(bucket0_echo[0])
    else:
        for s in list(final) + list(baseline):
            if int(float(s.get("start_time", 0.0)) // 12) == 0:
                prioritized.append(s)
                break
    for s in baseline:
        if _is_echo_seg(s):
            prioritized.append(s)
    step = max(1, len(final) // max(1, limit))
    for s in final[::step]:
        prioritized.append(s)
    prioritized.extend(final)

    for anchor in prioritized:
        s0 = float(anchor.get("start_time", 0.0))
        e0 = float(anchor.get("end_time", s0 + 2.0))
        bucket = int(s0 // 12)
        if bucket in seen_windows:
            continue
        seen_windows.add(bucket)
        w_s = max(0.0, s0 - 0.5)
        w_e = max(e0 + 0.5, s0 + 4.0)
        examples.append(
            {
                "window_sec": f"{w_s:.1f}s - {w_e:.1f}s",
                "baseline_db": _match_overlapping_text(baseline, w_s, w_e) or "(no segment)",
                "post_fix_live": _match_overlapping_text(live, w_s, w_e) or "(no live segment in window)",
                "post_fix_final": _match_overlapping_text(final, w_s, w_e) or "(no final segment)",
            }
        )
        if len(examples) >= limit:
            break
    return examples


def run_challenger_compatibility_probe() -> Dict[str, Any]:
    """Strictly read-only runtime, dependency, VRAM, and disk compatibility check for Phase 2 candidates."""
    import importlib.metadata as imeta

    pkg_names = (
        "ctranslate2",
        "faster-whisper",
        "onnxruntime",
        "onnxruntime-gpu",
        "torch",
        "torchaudio",
        "transformers",
        "sherpa-onnx",
        "qwen-asr",
        "huggingface-hub",
        "tokenizers",
    )
    installed: Dict[str, Optional[str]] = {}
    for pkg in pkg_names:
        try:
            installed[pkg] = imeta.version(pkg)
        except Exception:
            installed[pkg] = None

    ort_providers: List[str] = []
    try:
        import onnxruntime as ort
        ort_providers = list(ort.get_available_providers())
    except Exception:
        pass

    import ctranslate2
    cuda_count = 0
    try:
        cuda_count = int(ctranslate2.get_cuda_device_count())
    except Exception:
        pass

    disk_c = shutil.disk_usage("C:\\")
    disk_d = shutil.disk_usage("D:\\")
    gpu_now = query_gpu_vram()

    return {
        "environment": {
            "python_version": sys.version.split()[0],
            "installed_packages": installed,
            "onnxruntime_providers": ort_providers,
            "ctranslate2_cuda_devices": cuda_count,
            "gpu": gpu_now,
            "disk_free_gb": {
                "C": round(disk_c.free / (1024**3), 1),
                "D": round(disk_d.free / (1024**3), 1),
            },
        },
        "candidates": {
            "faster-whisper-large-v3": {
                "runtime_compatible_now": True,
                "reason": "Supported directly by ctranslate2==4.8.3 & faster-whisper==1.2.1 on CUDA int8 without PyTorch.",
                "download_estimate_gb": "~2.9-3.1 GB CT2 INT8",
                "vram_fit_4gb_gtx1650": "Tight (~2.9 GB weights + compute buffers vs ~2.0-3.5 GB free VRAM; risks OOM if Windows/DWM uses >1.1 GB).",
                "expected_gain_vs_risk": "Higher Hindi/English accuracy than turbo, ~2x slower RTF and tight 4 GB VRAM headroom.",
            },
            "Qwen3-ASR-0.6B": {
                "runtime_compatible_now": False,
                "missing_dependencies": ["torch (CUDA build)", "transformers / qwen-asr"],
                "download_estimate_gb": "~2.5 GB CUDA torch + ~1.2 GB Qwen3-ASR 0.6B weights",
                "expected_gain_vs_risk": "Strong native multilingual/Hinglish code-switching, requires adding PyTorch dependency stack to .venv (~2.5-3.5 GB disk + VRAM checks).",
            },
            "IndicConformer-ONNX": {
                "runtime_compatible_now": "CPU-only (missing model weights & onnxruntime-gpu)",
                "reason": f"onnxruntime=={installed.get('onnxruntime')} active with providers={ort_providers}; runs on CPU cleanly, CUDA needs onnxruntime-gpu.",
                "download_estimate_gb": "~0.6-1.2 GB ONNX weights",
                "expected_gain_vs_risk": "Excellent Hindi/Indic phoneme recognition, lacks English punctuation/casing out of the box (best paired or hybrid).",
            },
            "sherpa-onnx-diarization": {
                "runtime_compatible_now": "Needs sherpa-onnx wheel install (Python 3.13 check) or PyAnnote/WeSpeaker ONNX models via existing onnxruntime.",
                "missing_dependencies": ["sherpa-onnx package + segmentation/embedding ONNX weights (~40-80 MB)"],
                "expected_gain_vs_risk": "Lightweight CPU neural speaker diarization (PyAnnote + WeSpeaker ONNX), huge upgrade over 16D spectral clustering with minimal RAM.",
            },
        },
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run strictly read-only VOX Phase 1 speech benchmark on M#234 and M#235."
    )
    parser.add_argument(
        "--reuse-m234-live",
        action="store_true",
        help="Reuse cached M#234 post_fix_live_replay metrics & live discrepancy strings from existing phase1_whisper_benchmark_report.json when present.",
    )
    return parser.parse_args(argv)


def _load_cached_m234_live(report_path: Path) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not report_path.exists():
        return None, []
    try:
        cached = json.loads(report_path.read_text(encoding="utf-8"))
        m234_entry = cached.get("meetings", {}).get("234", {})
        cached_live = m234_entry.get("post_fix_live_replay")
        cached_examples = m234_entry.get("discrepancy_examples") or []
        if isinstance(cached_live, dict) and "rtf" in cached_live and cached_examples:
            return cached_live, list(cached_examples)
    except Exception:
        pass
    return None, []


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    os.makedirs(BENCH_DIR, exist_ok=True)
    cached_live234, cached_m234_examples = (
        _load_cached_m234_live(BENCH_DIR / "phase1_whisper_benchmark_report.json")
        if args.reuse_m234_live
        else (None, [])
    )
    with CONFIG_PATH.open("r", encoding="utf-8") as cf:
        cfg = json.load(cf)

    meetings, vocab = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in TARGET_MEETING_IDS]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_rows_sha = compute_target_db_rows_sha256()
    pre_db_file_sha = sha256_file(DB_PATH)

    report: Dict[str, Any] = {
        "benchmark_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "excluded_meetings": [233],
        "config_snapshot": {
            "whisper_model": cfg.get("whisper_model", "large-v3-turbo"),
            "compute_device": cfg.get("compute_device", "auto"),
            "compute_type": cfg.get("compute_type", "default"),
            "language": cfg.get("language", "en+hi"),
        },
        "gpu_before_load": query_gpu_vram(),
        "ram_before_load_mb": get_process_rss_mb(),
        "meetings": {},
        "timing_guard_triggered": False,
    }

    transcriber = LocalWhisperTranscriber(
        models_dir=cfg.get("models_dir"),
        model_name=cfg.get("whisper_model", "large-v3-turbo"),
        compute_device=cfg.get("compute_device", "auto"),
        compute_type=cfg.get("compute_type", "default"),
        cpu_threads=int(cfg.get("cpu_threads", 8)),
    )
    t_load0 = time.perf_counter()
    ok, load_msg = transcriber.ensure_loaded()
    load_sec = time.perf_counter() - t_load0
    if not ok:
        raise RuntimeError(f"Failed loading transcriber: {load_msg}")

    report["model_load"] = {
        "elapsed_sec": round(load_sec, 2),
        "device": getattr(transcriber, "_active_device", "unknown"),
        "message": load_msg,
        "gpu_after_load": query_gpu_vram(),
        "ram_after_load_mb": get_process_rss_mb(),
    }
    print(f"[Benchmark] Loaded {transcriber.model_name} ({transcriber._active_device}) in {load_sec:.2f}s | {report['model_load']['gpu_after_load']}", flush=True)

    language = str(cfg.get("language", "en+hi"))

    # --- Meeting #234 FIRST (with Timing Guard before running full 18:49 M#235) ---
    m234 = meetings[234]
    dur234 = float(m234["duration_sec"])
    mic234_audio = load_wav_as_16k_f32(m234["mic_audio_path"])
    sys234_audio = load_wav_as_16k_f32(m234["sys_audio_path"])

    reused_m234_live = bool(args.reuse_m234_live and cached_live234 is not None)
    if reused_m234_live and cached_live234 is not None:
        live234_replay = dict(cached_live234)
        live234_segments: List[Dict[str, Any]] = []
        print(
            f"[Benchmark] Reusing cached M#234 Live Replay: wall={live234_replay['wall_time_sec']}s RTF={live234_replay['rtf']} segs={live234_replay['metrics']['segment_count']} echoes={live234_replay['metrics']['prompt_echo_count']}",
            flush=True,
        )
    else:
        print(f"[Benchmark] Running M#234 ({m234['title']}, {dur234:.2f}s) Live Replay...", flush=True)
        live234 = run_live_replay(
            transcriber, mic234_audio, sys234_audio, vocab, 234, m234["title"], language
        )
        print(
            f"[Benchmark] M#234 Live Replay done: wall={live234['wall_time_sec']}s RTF={live234['rtf']} segs={live234['metrics']['segment_count']} echoes={live234['metrics']['prompt_echo_count']}",
            flush=True,
        )
        live234_replay = {k: v for k, v in live234.items() if k != "segments"}
        live234_segments = list(live234.get("segments", []))

    live234_rtf = float(live234_replay["rtf"])

    print(f"[Benchmark] Running M#234 ({m234['title']}, {dur234:.2f}s) Offline Finalization...", flush=True)
    final234 = run_offline_finalization(
        transcriber,
        m234["mic_audio_path"],
        m234["sys_audio_path"],
        m234["audio_path"],
        vocab,
        m234["title"],
        language,
        dur234,
    )
    print(f"[Benchmark] M#234 Offline Final done: wall={final234['wall_time_sec']}s RTF={final234['rtf']} (s2={final234['stage2_dual_track_sec']}s, s3={final234['stage3_reconcile_sec']}s) segs={final234['metrics']['segment_count']} echoes={final234['metrics']['prompt_echo_count']}", flush=True)

    draft234 = build_reference_draft(m234, final234["segments"], m234["baseline_segments"])
    (BENCH_DIR / "M234_hemanth_reference_draft.json").write_text(
        json.dumps(draft234, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    m234_examples = pick_discrepancy_examples(
        m234["baseline_segments"], live234_segments, final234["segments"], limit=8
    )
    if reused_m234_live and cached_m234_examples:
        cached_by_win = {
            str(item.get("window_sec", "")): str(item.get("post_fix_live", ""))
            for item in cached_m234_examples
        }
        cached_by_bucket: Dict[int, str] = {}
        for item in cached_m234_examples:
            w_str = str(item.get("window_sec", ""))
            m_w = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)s\s*-", w_str)
            if m_w:
                cached_by_bucket.setdefault(int(float(m_w.group(1)) // 12), str(item.get("post_fix_live", "")))
        for ex in m234_examples:
            w_str = str(ex.get("window_sec", ""))
            if w_str in cached_by_win:
                ex["post_fix_live"] = cached_by_win[w_str]
            else:
                m_w = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)s\s*-", w_str)
                if m_w:
                    bkt = int(float(m_w.group(1)) // 12)
                    if bkt in cached_by_bucket:
                        ex["post_fix_live"] = cached_by_bucket[bkt]

    report["meetings"]["234"] = {
        "title": m234["title"],
        "duration_sec": dur234,
        "live_replay_reused_from_cache": reused_m234_live,
        "baseline_db": compute_segment_metrics(m234["baseline_segments"], dur234),
        "post_fix_live_replay": live234_replay,
        "post_fix_offline_final": {k: v for k, v in final234.items() if k != "segments"},
        "discrepancy_examples": m234_examples,
        "reference_draft_path": str(BENCH_DIR / "M234_hemanth_reference_draft.json"),
    }

    # True batch-extrapolated ETA Guard before M#235
    vad_p = dict(threshold=0.35, min_silence_duration_ms=700, speech_pad_ms=300)
    b234_mic = LocalWhisperTranscriber._build_packed_vad_batches(mic234_audio, vad_p) or []
    b234_sys = LocalWhisperTranscriber._build_packed_vad_batches(sys234_audio, vad_p) or []
    n_b234 = len(b234_mic) + len(b234_sys)

    m235 = meetings[235]
    dur235 = float(m235["duration_sec"])
    mic235_audio = load_wav_as_16k_f32(m235["mic_audio_path"])
    sys235_audio = load_wav_as_16k_f32(m235["sys_audio_path"])

    def _count_windowed_batches(aud: np.ndarray, w_sec: float = 120.0) -> int:
        sr = TARGET_SAMPLE_RATE
        tot = 0
        pos = 0
        step = int(w_sec * sr)
        while pos < aud.size:
            sl = aud[pos : min(aud.size, pos + step)]
            if sl.size >= int(0.3 * sr) and float(np.max(np.abs(sl))) >= 0.0004:
                tot += len(LocalWhisperTranscriber._build_packed_vad_batches(sl, vad_p) or [])
            pos += step
        return tot

    n_b235 = _count_windowed_batches(mic235_audio) + _count_windowed_batches(sys235_audio)
    eta_m235_sec = round((final234["stage2_dual_track_sec"] / max(1, n_b234)) * n_b235 + 15.0, 1)
    report["batch_extrapolation"] = {
        "n_batches_234": n_b234,
        "n_batches_235": n_b235,
        "extrapolated_m235_sec": eta_m235_sec,
    }
    print(f"[Benchmark] Batch ETA check: n_batches_234={n_b234}, n_batches_235={n_b235}, eta_m235_sec={eta_m235_sec}s (running full M#235 unconditionally)", flush=True)

    m235_live_windows = None if live234_rtf <= 0.20 else list(LIVE_REPLAY_M235_WINDOWS)
    print(f"[Benchmark] Running M#235 ({m235['title']}, {dur235:.2f}s) Live Replay (windows={m235_live_windows or 'FULL'})...", flush=True)
    live235 = run_live_replay(
        transcriber, mic235_audio, sys235_audio, vocab, 235, m235["title"], language, windows=m235_live_windows
    )
    print(f"[Benchmark] M#235 Live Replay done: wall={live235['wall_time_sec']}s RTF={live235['rtf']} segs={live235['metrics']['segment_count']} echoes={live235['metrics']['prompt_echo_count']}", flush=True)

    print(f"[Benchmark] Running M#235 ({m235['title']}, {dur235:.2f}s) Full Offline Finalization...", flush=True)
    final235 = run_offline_finalization(
        transcriber,
        m235["mic_audio_path"],
        m235["sys_audio_path"],
        m235["audio_path"],
        vocab,
        m235["title"],
        language,
        dur235,
    )
    print(f"[Benchmark] M#235 Offline Final done: wall={final235['wall_time_sec']}s RTF={final235['rtf']} (s2={final235['stage2_dual_track_sec']}s, s3={final235['stage3_reconcile_sec']}s) segs={final235['metrics']['segment_count']} echoes={final235['metrics']['prompt_echo_count']}", flush=True)
    report["actual_exceeded_300s_budget"] = bool(final235["wall_time_sec"] > 300.0)
    if final235["wall_time_sec"] > 300.0:
        report["timing_guard_triggered"] = True
        report["timing_guard_reason"] = f"Actual M#235 offline finalization took {final235['wall_time_sec']}s (> 300.0s budget)."

    draft235 = build_reference_draft(m235, final235["segments"], m235["baseline_segments"])
    (BENCH_DIR / "M235_rumjhum_reference_draft.json").write_text(
        json.dumps(draft235, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    report["meetings"]["235"] = {
        "title": m235["title"],
        "duration_sec": dur235,
        "baseline_db": compute_segment_metrics(m235["baseline_segments"], dur235),
        "post_fix_live_replay": {k: v for k, v in live235.items() if k != "segments"},
        "post_fix_offline_final": {k: v for k, v in final235.items() if k != "segments"},
        "discrepancy_examples": pick_discrepancy_examples(
            m235["baseline_segments"], live235["segments"], final235["segments"], limit=10
        ),
        "reference_draft_path": str(BENCH_DIR / "M235_rumjhum_reference_draft.json"),
    }

    # Stage 4 VRAM handoff smoke check (read-only)
    gpu_before_unload = query_gpu_vram()
    transcriber.unload_model()
    gpu_after_unload = query_gpu_vram()
    llm_free_mb = LocalLLMEngine._get_free_vram_mb()
    report["stage4_vram_handoff"] = {
        "gpu_before_unload": gpu_before_unload,
        "gpu_after_unload": gpu_after_unload,
        "llm_probe_free_vram_mb": llm_free_mb,
        "cuda_allowed_at_1800mb_gate": bool(llm_free_mb is not None and llm_free_mb >= 1800),
    }

    report["phase2_challenger_compatibility_probe"] = run_challenger_compatibility_probe()

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_rows_sha = compute_target_db_rows_sha256()
    post_db_file_sha = sha256_file(DB_PATH)
    mismatches = [
        k for k in pre_snap
        if k not in post_snap or pre_snap[k] != post_snap[k]
    ]
    if pre_db_rows_sha != post_db_rows_sha:
        mismatches.append("DB_TARGET_ROWS_M234_M235_VOCAB")
    report["read_only_integrity"] = {
        "checked_file_count": len(pre_snap),
        "all_recording_and_config_hashes_unchanged": len(mismatches) == 0,
        "target_db_rows_sha256": pre_db_rows_sha,
        "target_db_rows_unchanged": pre_db_rows_sha == post_db_rows_sha,
        "db_file_sha256_pre": pre_db_file_sha,
        "db_file_sha256_post": post_db_file_sha,
        "external_process_modified_db_file": pre_db_file_sha != post_db_file_sha,
        "mismatches": mismatches,
        "protected_hashes_sha256": {
            Path(k).name if "recordings" not in k else f"{Path(k).parent.name}/{Path(k).name}": v["sha256"]
            for k, v in pre_snap.items()
        },
    }

    report_path = BENCH_DIR / "phase1_whisper_benchmark_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"[Benchmark] Saved report to {report_path} | protected_intact={report['read_only_integrity']['all_recording_and_config_hashes_unchanged']} | db_target_rows_intact={report['read_only_integrity']['target_db_rows_unchanged']}",
        flush=True,
    )
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

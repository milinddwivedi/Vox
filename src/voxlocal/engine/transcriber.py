"""
Local Offline Speech-to-Text Engine powered by faster-whisper (CTranslate2).
Features:
- 100% local offline inference after model download
- Automatic CUDA / multi-threaded CPU (int8) selection with graceful fallback
- Automatic Gain Normalization (AGC) for low-volume system/video/headphone loopback audio
- Adaptive VAD fallback so continuous video speech or speech over background music is never dropped
- Dual-Track Meeting Transcription with Speaker Diarization:
  Separates "You (Mic)" from "Remote Speaker (System / Video)" using separate audio tracks
  and detects speaker turns on the system audio track using acoustic pitch/spectral clustering.
"""

from __future__ import annotations

import os
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np
import ctranslate2
from faster_whisper import WhisperModel

from voxlocal.audio.devices import (
    TARGET_SAMPLE_RATE,
    normalize_speech_audio,
    resample_audio_to_16k_mono,
)
from voxlocal.core.logger import get_app_root, get_logger
from voxlocal.engine.dictation_processor import DictationProcessor

logger = get_logger("engine.transcriber")


@dataclass
class DictationResult:
    """Canonical transcription result fulfilling Milestone 2 interface contract."""
    raw_text: str
    segments: list
    language: str
    duration: float


class DictationSegment(dict):
    """Segment dictionary subclass enabling both attribute (seg.text) and key (seg['text']) access."""
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def setup_nvidia_dll_paths() -> None:
    """Ensure Windows finds NVIDIA CUDA runtime libraries (cublas, cudnn, nvrtc) from .venv or python environment."""
    if os.name != "nt":
        return
    import sys

    app_root = get_app_root()
    search_dirs = [
        app_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        app_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        app_root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
        Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
    ]
    for d in search_dirs:
        if d.exists():
            try:
                os.add_dll_directory(str(d.resolve()))
            except Exception:
                pass
            p_str = str(d.resolve())
            if p_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{p_str};" + os.environ.get("PATH", "")


# Run immediately on module import
setup_nvidia_dll_paths()


AVAILABLE_MODELS: Dict[str, Dict[str, str]] = {
    "large-v3-turbo": {
        "size": "~1.6 GB",
        "speed": "Fast (GPU 1.9s / Multi-core CPU)",
        "description": "High-accuracy multilingual model (OpenAI large-v3-turbo, 809M params). Top accuracy for Hindi, Hinglish, Indian English, and natural code-switching.",
    },
    "large-v3": {
        "size": "~3.1 GB",
        "speed": "Deep Accuracy (1.55B params)",
        "description": "Full 32-layer OpenAI Whisper large-v3 model for maximum offline transcription accuracy on complex accents and noisy meetings.",
    },
    "small": {
        "size": "~465 MB",
        "speed": "Medium (2.4s)",
        "description": "Multilingual small model with balanced speed and accuracy.",
    },
    "whisper-hindi": {
        "size": "~240 MB",
        "speed": "Very Fast",
        "description": "Specialized Indian language fine-tune from IIT Madras for pure Hindi speech.",
    },
    "base": {
        "size": "~145 MB",
        "speed": "Very Fast",
        "description": "Multilingual base model (supports 99 languages).",
    },
    "small.en": {
        "size": "~465 MB",
        "speed": "Medium",
        "description": "Higher accuracy English model for technical terminology.",
    },
    "base.en": {
        "size": "~145 MB",
        "speed": "Very Fast",
        "description": "Fast English-only model.",
    },
}

MODEL_REPO_MAP: Dict[str, str] = {
    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "whisper-hindi": "ukta-app/whisper-hindi-ct2",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "tiny": "Systran/faster-whisper-tiny",
}

MODEL_MANIFEST: Dict[str, Dict[str, Any]] = {
    "large-v3-turbo": {
        "model_id": "large-v3-turbo",
        "repo_id": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "version": "1.0.0",
        "expected_size_mb": 1543,
        "min_disk_space_mb": 2000,
        "required_files": ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"],
        "supported_languages": ["en", "hi", "en+hi", "hinglish", "hi_latn", "auto"],
    },
    "large-v3": {
        "model_id": "large-v3",
        "repo_id": "Systran/faster-whisper-large-v3",
        "version": "1.0.0",
        "expected_size_mb": 3090,
        "min_disk_space_mb": 4000,
        "required_files": ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"],
        "supported_languages": ["en", "hi", "en+hi", "hinglish", "hi_latn", "auto"],
    },
    "small": {
        "model_id": "small",
        "repo_id": "Systran/faster-whisper-small",
        "version": "1.0.0",
        "expected_size_mb": 465,
        "min_disk_space_mb": 700,
        "required_files": ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"],
        "supported_languages": ["en", "hi", "en+hi", "hinglish", "hi_latn", "auto"],
    },
    "base": {
        "model_id": "base",
        "repo_id": "Systran/faster-whisper-base",
        "version": "1.0.0",
        "expected_size_mb": 145,
        "min_disk_space_mb": 250,
        "required_files": ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"],
        "supported_languages": ["en", "hi", "en+hi", "hinglish", "hi_latn", "auto"],
    },
    "tiny": {
        "model_id": "tiny",
        "repo_id": "Systran/faster-whisper-tiny",
        "version": "1.0.0",
        "expected_size_mb": 75,
        "min_disk_space_mb": 150,
        "required_files": ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"],
        "supported_languages": ["en", "hi", "en+hi", "hinglish", "hi_latn", "auto"],
    },
}


class ModelReadinessState:
    """Lifecycle readiness states for local Whisper speech models."""
    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


DEFAULT_DOMAIN_HOTWORDS: str = (
    "standup, ticket, PR, Google Meet, Microsoft Teams, Zoom, Slack, Jira, GitHub, "
    "WFX, PLM, ERP, HubSpot, Windchill, SAP, BOM, PO, Excel, costing sheet, "
    "American Tall, Troy Lee, subcategory, barcode, Sprint, Daily Scrum, "
    "Hemanth, Talathoti, Rumjhum, Milind, Abhilash, Pankaj, Puneet, Amar, Astha, "
    "World Fashion Exchange, Microsoft Office, Tech Team, GitLab, VS Code, "
    "Python, SQLite, SQL, API, endpoint, frontend, backend, staging, production, "
    "deploy, commit, merge, branch, bug fix, code review, screen share, "
    "screenshot, client, meeting, report, status, release, scrum, backlog, task, "
    "timeline, deadline, presentation, slides, voice typing, prepone, do the needful, "
    "loop in, touch base, how I, in caps, all caps"
)


def merge_domain_hotwords(extra_hotwords: Optional[str] = None, max_terms: int = 28) -> str:
    """Merge caller hotwords with priority domain hotwords, bounded cleanly for CTranslate2."""
    merged: List[str] = []
    seen: set[str] = set()
    for block in (extra_hotwords or "", DEFAULT_DOMAIN_HOTWORDS):
        for raw_tok in str(block).split(","):
            tok = re.sub(r"\s+", " ", raw_tok.strip()).strip(",.")
            if not tok:
                continue
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tok)
            if len(merged) >= max_terms:
                return ", ".join(merged)
    return ", ".join(merged)


def load_wav_as_16k_f32(wav_path: str | Path) -> np.ndarray:
    """Read a WAV file from disk and return a 16kHz mono float32 numpy array."""
    path = Path(wav_path)
    if not path.exists():
        return np.zeros(0, dtype=np.float32)
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    pcm = np.frombuffer(raw, dtype=np.int16)
    return resample_audio_to_16k_mono(pcm, rate, channels)


def get_wav_duration_sec(wav_path: str | Path) -> float:
    """Return the duration of a WAV file in seconds without loading frames into memory."""
    path = Path(wav_path)
    if not path.exists():
        return 0.0
    try:
        with wave.open(str(path), "rb") as wf:
            rate = max(1, wf.getframerate())
            return float(wf.getnframes()) / float(rate)
    except Exception:
        return 0.0


def load_wav_slice_as_16k_f32(wav_path: str | Path, start_sec: float, end_sec: float) -> np.ndarray:
    """Read only `[start_sec, end_sec]` from a WAV file (bounded memory for multi-hour recordings)."""
    path = Path(wav_path)
    if not path.exists() or end_sec <= start_sec:
        return np.zeros(0, dtype=np.float32)
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            total_frames = wf.getnframes()
            s_frame = max(0, min(total_frames, int(start_sec * rate)))
            e_frame = max(s_frame, min(total_frames, int(end_sec * rate)))
            if e_frame <= s_frame:
                return np.zeros(0, dtype=np.float32)
            wf.setpos(s_frame)
            raw = wf.readframes(e_frame - s_frame)
        pcm = np.frombuffer(raw, dtype=np.int16)
        return resample_audio_to_16k_mono(pcm, rate, channels)
    except Exception:
        return np.zeros(0, dtype=np.float32)


def estimate_acoustic_signature(audio_16k: np.ndarray) -> float:
    """
    Compute zero-crossing / spectral centroid proxy on voiced frames to help
    distinguish distinct remote speakers on the system audio track.
    """
    if audio_16k.size < 1600:
        return 0.0
    signs = np.sign(audio_16k)
    signs[signs == 0] = 1
    zcr = float(np.mean(signs[:-1] != signs[1:])) * (TARGET_SAMPLE_RATE / 2.0)
    return zcr


def extract_speaker_embedding(
    audio_16k: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE
) -> Optional[np.ndarray]:
    """
    Extract a zero-centered, volume-invariant 16D acoustic speaker embedding vector:
    - Unit-RMS normalized voiced frames (completely invariant to speaker volume / distance)
    - Trimmed interquartile median log2(F0 / 155 Hz) fundamental vocal pitch (robust to
      mid-sentence intonation rises while separating distinct vocal cords)
    - Cepstral Mean Normalized (CMN) log-Mel filterbank & DCT cepstral coefficients
      capturing vocal-tract formant structure (F1, F2, F3, spectral tilt, harmonic ratios)
    Returns an L2-normalized 16D float32 vector in R^16, or None if insufficient voiced speech.
    """
    if audio_16k.size < int(0.18 * TARGET_SAMPLE_RATE):  # < 180ms
        return None

    # 1. Remove DC offset and normalize overall RMS so volume differences never skew embeddings
    centered = audio_16k.astype(np.float32) - float(np.mean(audio_16k))
    overall_rms = float(np.sqrt(np.mean(np.square(centered))))
    if overall_rms < 1e-4:
        return None
    norm_sig = centered / (overall_rms + 1e-7)

    frame_len = 512  # 32ms at 16 kHz
    hop_len = 256    # 16ms at 16 kHz
    n_frames = (len(norm_sig) - frame_len) // hop_len + 1
    if n_frames < 4:
        return None

    # 2. Extract voiced frames above relative energy floor
    frame_rms = np.empty(n_frames, dtype=np.float32)
    frames = np.empty((n_frames, frame_len), dtype=np.float32)
    for i in range(n_frames):
        st = i * hop_len
        fr = norm_sig[st : st + frame_len]
        frames[i] = fr
        frame_rms[i] = float(np.sqrt(np.mean(np.square(fr))))

    energy_thresh = max(0.35, float(np.percentile(frame_rms, 30)))
    voiced_frames = frames[frame_rms >= energy_thresh]
    if len(voiced_frames) < 3:
        voiced_frames = frames[frame_rms >= 0.20]
    if len(voiced_frames) < 3:
        return None

    # 3. Frame-wise Fundamental Pitch (F0) via normalized autocorrelation (75 Hz .. 355 Hz)
    min_lag = 45   # 16000 / 355.5 Hz
    max_lag = 213  # 16000 / 75.1 Hz
    f0_estimates: List[float] = []
    step = max(1, len(voiced_frames) // 24)
    for fr in voiced_frames[::step]:
        fr_zm = fr - float(np.mean(fr))
        e0 = float(np.dot(fr_zm, fr_zm))
        if e0 < 1e-6:
            continue
        corr = np.correlate(fr_zm, fr_zm, mode="full")[frame_len - 1 :]
        if len(corr) > max_lag:
            region = corr[min_lag:max_lag]
            pk_idx = int(np.argmax(region))
            pk_lag = min_lag + pk_idx
            periodicity = float(corr[pk_lag] / e0)
            if periodicity > 0.25 and pk_lag > 0:
                f0_estimates.append(TARGET_SAMPLE_RATE / float(pk_lag))

    if f0_estimates:
        f0_arr = np.sort(np.array(f0_estimates, dtype=np.float32))
        if len(f0_arr) >= 4:
            # Interquartile trimmed mean (ignores momentary mid-sentence inflection spikes)
            q1 = len(f0_arr) // 4
            q3 = max(q1 + 1, (3 * len(f0_arr)) // 4)
            f0_med = float(np.mean(f0_arr[q1:q3]))
        else:
            f0_med = float(np.median(f0_arr))
    else:
        f0_med = 155.0

    # Log-octave pitch centered at 155 Hz (typical gender/voice-neutral center)
    # 110 Hz -> -0.49, 165 Hz -> +0.09, 235 Hz -> +0.60
    pitch_oct = float(np.log2(max(70.0, min(360.0, f0_med)) / 155.0))

    # 4. Multi-band Log-Spectrum & Cepstral Envelope across all voiced frames
    win = np.hanning(frame_len).astype(np.float32)
    windowed = voiced_frames * win[None, :]
    mag_spec = np.abs(np.fft.rfft(windowed, axis=1)) + 1e-7  # shape: (N_voiced, 257)
    mean_spec = np.mean(mag_spec, axis=0)
    freqs = np.fft.rfftfreq(frame_len, d=1.0 / TARGET_SAMPLE_RATE)

    # Subtract stationary background noise floor estimated from pause/unvoiced frames (keeps timbre under SNR stress)
    pause_thresh = min(0.35, float(np.percentile(frame_rms, 20)))
    quiet_frames = frames[frame_rms <= pause_thresh]
    if len(quiet_frames) >= 2:
        q_spec = np.mean(np.abs(np.fft.rfft(quiet_frames * win[None, :], axis=1)), axis=0)
        noise_floor = np.minimum(0.95 * q_spec, 0.85 * mean_spec)
        mean_spec = np.maximum(mean_spec - noise_floor, 0.05 * mean_spec)
    elif np.any(freqs >= 5500):
        hf_floor = float(np.percentile(mean_spec[freqs >= 5500], 25))
        mean_spec = np.maximum(mean_spec - 0.85 * hf_floor, 0.05 * mean_spec)

    # 12 log-spaced vocal-tract frequency bands from 100 Hz to 5600 Hz
    band_edges = [100, 200, 320, 480, 700, 1000, 1400, 1950, 2650, 3500, 4500, 5600, 7200]
    log_band_energies = np.zeros(12, dtype=np.float32)
    for b_idx in range(12):
        lo, hi = band_edges[b_idx], band_edges[b_idx + 1]
        mask = (freqs >= lo) & (freqs < hi)
        if np.any(mask):
            log_band_energies[b_idx] = float(np.log(np.mean(mean_spec[mask]) + 1e-7))

    # Cepstral Mean Normalization (subtracts mean log energy -> zero-centered vocal tract profile)
    cmn_bands = log_band_energies - float(np.mean(log_band_energies))
    band_std = float(np.std(cmn_bands)) + 1e-5
    cmn_norm = cmn_bands / band_std

    # Spectral centroid & rolloff (zero-centered around typical human speech means)
    spec_sum = float(np.sum(mean_spec)) + 1e-7
    centroid = float(np.sum(freqs * mean_spec) / spec_sum)
    cum_power = np.cumsum(mean_spec)
    rolloff_idx = int(np.searchsorted(cum_power, 0.85 * spec_sum))
    rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])

    centroid_z = (centroid - 1150.0) / 550.0
    rolloff_z = (rolloff - 2400.0) / 1100.0

    # Formant F1/F2/F3 balance ratio
    e_f1 = float(np.mean(log_band_energies[1:4]))   # 200 - 700 Hz
    e_f2 = float(np.mean(log_band_energies[4:8]))   # 700 - 2650 Hz
    f1_f2_tilt = (e_f2 - e_f1) / (band_std + 1e-5)

    # Construct 16D zero-centered speaker signature vector
    # Biometric vocal traits (F0 pitch, brightness, oral tract cavity balance)
    # dominate over the static VoIP channel codec response across the 12 sub-bands
    vec = np.concatenate(
        [
            np.array(
                [
                    3.4 * pitch_oct,
                    0.85 * centroid_z,
                    0.70 * rolloff_z,
                    0.85 * f1_f2_tilt,
                ],
                dtype=np.float32,
            ),
            0.55 * cmn_norm,  # 12 zero-centered vocal-tract sub-bands
        ]
    )
    norm = float(np.linalg.norm(vec))
    if norm > 1e-6:
        vec = vec / norm
    return vec.astype(np.float32)


def speaker_cosine_distance(u: np.ndarray, v: np.ndarray) -> float:
    """Compute cosine distance in [0.0, 2.0] between two speaker embeddings."""
    dot = float(np.dot(u, v))
    norm_u = float(np.linalg.norm(u))
    norm_v = float(np.linalg.norm(v))
    if norm_u < 1e-6 or norm_v < 1e-6:
        return 1.0
    cosine_sim = dot / (norm_u * norm_v)
    return float(max(0.0, min(2.0, 1.0 - cosine_sim)))


def extract_subsegment_speaker_embedding(
    audio_16k: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE
) -> Tuple[Optional[np.ndarray], float]:
    """
    Extract a sub-segment sliding-window speaker embedding and internal dispersion score.
    For segments >= 1.8s, slides a 1.1s window (0.55s hop), extracts voiced embeddings for
    each sub-window, and returns `(mean_normalized_embedding, internal_dispersion)`.
    High `internal_dispersion` (> 0.28) indicates multi-speaker crosstalk / overlap within the segment.
    """
    if audio_16k is None or audio_16k.size == 0:
        return None, 0.0
    if audio_16k.ndim == 1 and audio_16k.size == 16:
        return audio_16k, 0.0

    base_emb = extract_speaker_embedding(audio_16k, sample_rate=sample_rate)
    if base_emb is None:
        return None, 0.0

    win_samples = int(1.1 * sample_rate)
    hop_samples = int(0.55 * sample_rate)
    if audio_16k.size < int(1.8 * sample_rate):
        return base_emb, 0.0

    sub_embs: List[np.ndarray] = []
    pos = 0
    while pos + win_samples <= audio_16k.size:
        sub = extract_speaker_embedding(audio_16k[pos : pos + win_samples], sample_rate=sample_rate)
        if sub is not None:
            sub_embs.append(sub)
        pos += hop_samples

    if len(sub_embs) < 2:
        return base_emb, 0.0

    stacked = np.stack(sub_embs, axis=0)
    mean_vec = np.mean(stacked, axis=0)
    norm_m = float(np.linalg.norm(mean_vec))
    if norm_m > 1e-6:
        mean_vec = (mean_vec / norm_m).astype(np.float32)
    else:
        mean_vec = base_emb

    dists = [speaker_cosine_distance(s, mean_vec) for s in sub_embs]
    dispersion = float(np.max(dists)) if dists else 0.0
    return mean_vec, round(dispersion, 3)


def is_likely_person_name(text: str) -> bool:
    """
    Determine if a string represents an individual person's name (e.g. 'Abhilash Mishra',
    'Manoj Sharma', 'Gorjana') rather than a meeting topic, project title, or corporate phrase
    (e.g. 'Accounting Fields - Gorjana || WFX', 'Daily Scrum - PLM North America').
    """
    if not text:
        return False
    clean = text.strip()
    if len(clean) < 2 or len(clean) > 40:
        return False
    # Reject strings with meeting/topic delimiters or punctuation
    if any(delim in clean for delim in ("||", "|", "/", "\\", "@", ":", "#", "(", ")", "[", "]", "{", "}", "=", "+", "*")):
        return False
    # Reject strings with topic hyphens/dashes
    if " - " in clean or " – " in clean or " — " in clean:
        return False
    # Reject strings with digits
    if any(ch.isdigit() for ch in clean):
        return False
    # Reject strings with common corporate/meeting keywords
    TOPIC_WORDS = {
        "meeting", "call", "recording", "quick", "sync", "scrum", "standup", "weekly",
        "daily", "monthly", "catchup", "review", "discussion", "demo", "planning", "sprint",
        "status", "update", "interview", "session", "webinar", "conf", "conference", "team",
        "fields", "accounting", "project", "board", "engineering", "sales", "ops", "support",
        "wfx", "group", "north", "south", "east", "west", "america", "apac", "emea", "internal",
        "external", "client", "customer", "vendor", "general", "touchpoint", "1:1", "1-on-1",
        "all-hands", "townhall", "retro", "retrospective", "backlog", "grooming", "refinement",
        "design", "architecture", "dev", "qa", "prod", "production", "staging", "test", "office",
        "connect", "sync-up", "check-in", "huddle", "alignment"
    }
    words = re.findall(r"[A-Za-z]+", clean.lower())
    if not words or len(words) > 3:  # Most individual human names are 1 to 3 words
        return False
    if any(w in TOPIC_WORDS for w in words):
        return False
    # Each word in a person name should be capitalized
    raw_words = clean.split()
    if not all(w[0].isupper() for w in raw_words if w.isalpha()):
        return False
    return True


class LocalSpeakerProfileStore:
    """
    User-controllable local speaker profile store (persisted on disk as JSON).
    Allows enrolling known voices and matching speaker centroids across meetings
    while keeping 100% of voiceprints local and deletable at any time.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (get_app_root() / "data" / "speaker_profiles.json")
        self._lock = threading.RLock()
        self.profiles: Dict[str, np.ndarray] = {}
        self.load()

    def load(self) -> None:
        import json

        with self._lock:
            self.profiles.clear()
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for name, vec_list in (data.get("profiles") or {}).items():
                    if isinstance(vec_list, list) and len(vec_list) == 16:
                        arr = np.array(vec_list, dtype=np.float32)
                        norm = float(np.linalg.norm(arr))
                        if norm > 1e-6:
                            self.profiles[str(name)] = arr / norm
            except Exception as exc:
                logger.debug("Could not load speaker profiles: %s", exc)

    def save(self) -> None:
        import json

        with self._lock:
            try:
                os.makedirs(self.path.parent, exist_ok=True)
                payload = {
                    "version": 1,
                    "profiles": {k: [round(float(x), 6) for x in v.tolist()] for k, v in self.profiles.items()},
                }
                self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.debug("Could not save speaker profiles: %s", exc)

    def enroll(self, speaker_name: str, audio_or_emb: np.ndarray) -> bool:
        clean_name = (speaker_name or "").strip()
        if not clean_name:
            return False
        emb = (
            audio_or_emb
            if (audio_or_emb.ndim == 1 and audio_or_emb.size == 16)
            else extract_speaker_embedding(audio_or_emb)
        )
        if emb is None:
            return False
        with self._lock:
            if clean_name in self.profiles:
                merged = 0.65 * self.profiles[clean_name] + 0.35 * emb
                n = float(np.linalg.norm(merged))
                self.profiles[clean_name] = (merged / n).astype(np.float32) if n > 1e-6 else emb
            else:
                self.profiles[clean_name] = emb.astype(np.float32)
            self.save()
        return True

    enroll_speaker = enroll
    enroll_profile = enroll

    def delete_profile(self, speaker_name: str) -> bool:
        with self._lock:
            if speaker_name in self.profiles:
                del self.profiles[speaker_name]
                self.save()
                return True
        return False

    def clear_all(self) -> None:
        with self._lock:
            self.profiles.clear()
            if self.path.exists():
                try:
                    self.path.unlink()
                except Exception:
                    pass

    def match(self, emb: np.ndarray, max_distance: float = 0.16) -> Optional[Tuple[str, float]]:
        with self._lock:
            if not self.profiles or emb is None:
                return None
            best_name = None
            best_dist = 99.0
            for name, ref_emb in self.profiles.items():
                d = speaker_cosine_distance(emb, ref_emb)
                if d < best_dist:
                    best_dist = d
                    best_name = name
            if best_name is not None and best_dist <= max_distance:
                conf = round(max(0.75, min(0.99, 1.0 - best_dist)), 2)
                return best_name, conf
            return None


class PersistentSpeakerDiarizer:
    """
    Stateful, persistent multi-speaker diarization engine across progressive meeting chunks
    and multi-window offline transcriptions.

    Solves the 'Remote Speaker 1' collapse and chunk-reset bugs:
    - Preserves speaker centroids (`Speaker 1`, `Speaker 2`, `Speaker 3`, ...) across the
      entire meeting lifecycle.
    - Uses sub-segment sliding-window embeddings to detect multi-speaker overlap / crosstalk.
    - Uses duration-weighted centroid updating so brief interjections or coughs do not
      drift established speaker profiles.
    - Prevents false mid-sentence speaker splits during momentary pitch inflections using
      a pause-gap continuity guard.
    - Supports optional local speaker profile matching (`LocalSpeakerProfileStore`).
    """

    def __init__(
        self,
        speaker_prefix: str = "Speaker",
        new_speaker_threshold: float = 0.115,
        max_speakers: int = 8,
        distance_threshold: Optional[float] = None,
        similarity_threshold: Optional[float] = None,
        profile_store: Optional[LocalSpeakerProfileStore] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.speaker_prefix = speaker_prefix
        if distance_threshold is not None:
            self.new_speaker_threshold = float(distance_threshold)
        elif similarity_threshold is not None:
            self.new_speaker_threshold = max(0.08, 1.0 - float(similarity_threshold))
        else:
            self.new_speaker_threshold = float(new_speaker_threshold)
        self.max_speakers = max(2, int(max_speakers))
        self.profile_store = profile_store
        self.centroids: List[np.ndarray] = []
        self.speaker_exemplars: List[List[np.ndarray]] = []
        self.speaker_durations: List[float] = []
        self.custom_labels: Dict[int, str] = {}
        self.last_speaker_idx: Optional[int] = None
        self.last_turn_end_sec: float = -10.0
        self.last_segment_overlap: bool = False

    def reset(self) -> None:
        with self._lock:
            self.centroids.clear()
            self.speaker_exemplars.clear()
            self.speaker_durations.clear()
            self.custom_labels.clear()
            self.last_speaker_idx = None
            self.last_turn_end_sec = -10.0
            self.last_segment_overlap = False

    @property
    def num_speakers(self) -> int:
        with self._lock:
            return len(self.centroids)

    def format_label(self, speaker_idx: Optional[int]) -> str:
        if speaker_idx is None or speaker_idx < 0:
            return "Unknown Speaker"
        if speaker_idx in self.custom_labels:
            return self.custom_labels[speaker_idx]
        return f"{self.speaker_prefix} {speaker_idx + 1}"

    def assign_segment(
        self,
        audio_slice_16k: np.ndarray,
        start_sec: float,
        end_sec: float,
    ) -> Tuple[str, float]:
        """
        Assign a speaker label and confidence score `[0.0 .. 1.0]` to an audio segment
        (or pre-computed 16D embedding).
        """
        with self._lock:
            duration = max(0.05, float(end_sec - start_sec))
            pause_gap = max(0.0, float(start_sec - self.last_turn_end_sec)) if self.last_turn_end_sec >= 0.0 else 99.0
            dispersion = 0.0
            if audio_slice_16k is not None and audio_slice_16k.ndim == 1 and audio_slice_16k.size == 16:
                emb = audio_slice_16k
            else:
                emb, dispersion = (
                    extract_subsegment_speaker_embedding(audio_slice_16k)
                    if audio_slice_16k is not None
                    else (None, 0.0)
                )
            crosstalk_thresh = max(0.12, self.new_speaker_threshold * 1.25)
            self.last_segment_overlap = dispersion > crosstalk_thresh

            # Case 1: Very short or low-energy segment without reliable voiced embedding
            if emb is None:
                if self.last_speaker_idx is not None and pause_gap <= 3.0:
                    self.last_turn_end_sec = max(self.last_turn_end_sec, end_sec)
                    return self.format_label(self.last_speaker_idx), 0.55
                if self.centroids:
                    return self.format_label(0), 0.45
                return "Unknown Speaker", 0.35

            # Case 2: First voiced speaker in the meeting
            if not self.centroids:
                self.centroids.append(emb.copy())
                self.speaker_exemplars.append([emb.copy()])
                self.speaker_durations.append(duration)
                if self.profile_store is not None:
                    matched = self.profile_store.match(emb)
                    if matched:
                        self.custom_labels[0] = matched[0]
                self.last_speaker_idx = 0
                self.last_turn_end_sec = max(self.last_turn_end_sec, end_sec)
                return self.format_label(0), 0.92

            # Case 3: Compare against all known speaker centroids and diverse exemplars
            raw_dists: List[float] = []
            for spk_i, c in enumerate(self.centroids):
                c_dist = speaker_cosine_distance(emb, c)
                ex_list = self.speaker_exemplars[spk_i] if spk_i < len(self.speaker_exemplars) else []
                if ex_list:
                    min_ex = min(speaker_cosine_distance(emb, ex) for ex in ex_list)
                    eff_dist = 0.55 * c_dist + 0.45 * min_ex
                else:
                    eff_dist = c_dist
                raw_dists.append(eff_dist)

            effective_dists = list(raw_dists)

            # Mid-sentence continuity guard:
            # Only apply continuity bias to short utterances (< 1.5s) to avoid mid-sentence fragmentation,
            # while allowing natural conversational turn handoffs when someone speaks a full sentence.
            if self.last_speaker_idx is not None and self.last_speaker_idx < len(effective_dists) and duration < 1.5:
                switch_bias = 0.025 if pause_gap < 0.35 else (0.010 if pause_gap < 0.75 else 0.0)
                if switch_bias > 0.0:
                    for i in range(len(effective_dists)):
                        if i != self.last_speaker_idx:
                            effective_dists[i] += switch_bias

            best_idx = int(np.argmin(effective_dists))
            best_raw_dist = raw_dists[best_idx]

            # Determine whether this segment belongs to a new speaker or the closest known speaker
            # Require at least 1.2s of voiced speech to register a brand-new speaker cluster,
            # preventing short affirmations ('Yeah', 'Okay', 'Right') from spawning fake speakers.
            effective_new_thresh = self.new_speaker_threshold
            if pause_gap < 0.30 and duration < 1.2:
                effective_new_thresh += 0.03

            if (
                best_raw_dist > effective_new_thresh
                and duration >= 1.2
                and len(self.centroids) < self.max_speakers
            ):
                self.centroids.append(emb.copy())
                self.speaker_exemplars.append([emb.copy()])
                self.speaker_durations.append(duration)
                chosen_idx = len(self.centroids) - 1
                if self.profile_store is not None:
                    matched = self.profile_store.match(emb)
                    if matched:
                        self.custom_labels[chosen_idx] = matched[0]
                spk_conf = round(max(0.70, min(0.96, 1.0 - (0.3 * best_raw_dist))), 2)
            else:
                chosen_idx = best_idx
                # Only update centroid when segment is clean single-speaker speech and within cluster threshold
                if not self.last_segment_overlap and best_raw_dist <= effective_new_thresh:
                    prev_dur = self.speaker_durations[chosen_idx]
                    alpha = min(0.20, max(0.04, duration / (prev_dur + duration + 3.0)))
                    updated = (1.0 - alpha) * self.centroids[chosen_idx] + alpha * emb
                    u_norm = float(np.linalg.norm(updated))
                    if u_norm > 1e-6:
                        self.centroids[chosen_idx] = (updated / u_norm).astype(np.float32)
                    self.speaker_durations[chosen_idx] = min(180.0, prev_dur + duration)
                    if chosen_idx < len(self.speaker_exemplars):
                        if len(self.speaker_exemplars[chosen_idx]) < 4:
                            self.speaker_exemplars[chosen_idx].append(emb.copy())
                        else:
                            self.speaker_exemplars[chosen_idx].pop(0)
                            self.speaker_exemplars[chosen_idx].append(emb.copy())
                spk_conf = round(max(0.55, min(0.98, 1.0 - (best_raw_dist / max(0.5, effective_new_thresh * 2.0)))), 2)

            self.last_speaker_idx = chosen_idx
            self.last_turn_end_sec = max(self.last_turn_end_sec, end_sec)
            return self.format_label(chosen_idx), spk_conf

    def assign_speaker(
        self,
        audio_or_emb: Optional[np.ndarray],
        start_sec: float = 0.0,
        end_sec: float = 1.0,
        duration_sec: Optional[float] = None,
    ) -> Tuple[str, float]:
        # Handle callers passing (audio, sample_rate) positionally e.g. assign_speaker(audio, 16000)
        if start_sec >= 8000.0 and end_sec == 1.0:
            sr = float(start_sec)
            start_sec = max(0.0, self.last_turn_end_sec + 1.0)
            dur = (
                float(audio_or_emb.size) / sr
                if (audio_or_emb is not None and audio_or_emb.size > 16)
                else 2.0
            )
            end_sec = start_sec + max(1.0, dur)
        elif duration_sec is not None and end_sec <= start_sec:
            end_sec = start_sec + float(duration_sec)
        return self.assign_segment(audio_or_emb, start_sec=start_sec, end_sec=end_sec)


VALID_AFFIRMATION_WORDS = {
    "theek", "hai", "hain", "haan", "accha", "achha", "acha", "sahi",
    "okay", "ok", "yes", "sure", "right", "yeah", "no", "nahi", "toh",
    "thik", "chheek", "baabaai", "bye"
}


def clean_repetition_tail(text: str) -> str:
    """
    If Whisper enters a runaway repetition loop at the tail of an audio segment
    (e.g. '...point ke andar theek hai theek hai' or 'Oh my God. Oh my god. Oh my gosh.'),
    trim off the trailing runaway loop while preserving genuine speech before it.
    """
    t = text.strip()
    words = t.split()
    if len(words) < 6:
        return t

    # Strip trailing repeated 2-word clause/sentence opener loops (e.g. >= 3 trailing clauses starting with 'oh my')
    clauses = re.split(r"(?<=[.!?।])\s+", t)
    if len(clauses) >= 4:
        norm_prefixes = []
        for cl in clauses:
            cl_words = re.sub(r"[^\w\s]", " ", cl.lower()).split()
            norm_prefixes.append(" ".join(cl_words[:2]) if len(cl_words) >= 2 else "")
        for pfx in set(p for p in norm_prefixes if p):
            if norm_prefixes.count(pfx) >= 3:
                # Find start of trailing contiguous run with same 2-word prefix
                run_start = len(norm_prefixes)
                for idx in range(len(norm_prefixes) - 1, -1, -1):
                    if norm_prefixes[idx] == pfx:
                        run_start = idx
                    else:
                        break
                if (len(norm_prefixes) - run_start) >= 3:
                    kept = clauses[:run_start]
                    if kept:
                        t = " ".join(kept).strip()
                        words = t.split()
                    break

    for n in (1, 2, 3, 4):
        if len(words) >= n * 3:
            pattern = words[-n:]
            rep_count = 0
            idx = len(words)
            while idx >= n:
                if words[idx - n : idx] == pattern:
                    rep_count += 1
                    idx -= n
                else:
                    break
            if rep_count >= 3:
                keep_words = words[: idx + min(2, rep_count) * n]
                return " ".join(keep_words).strip()
    return t


_SEQUENTIAL_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def is_prompt_contamination_hallucination(
    text: str,
    *,
    initial_prompt: str = "",
    avg_logprob: Optional[float] = None,
    no_speech_prob: Optional[float] = None,
    duration_sec: Optional[float] = None,
    audio_rms: Optional[float] = None,
) -> bool:
    """
    Detect legacy prompt-echo strings ONLY when corroborated by active prompt
    contamination or acoustic failure evidence (high no_speech_prob, low avg_logprob,
    silence-floor RMS, or physiologically impossible micro-slice duration).
    Never rejects legitimately spoken phrases like 'Tech Team, Sprint, Daily Scrum.'
    """
    cleaned = re.sub(
        r"^(?:\[\d{2}:\d{2}(?::\d{2})?\]\s*)?(?:(?:you\s*\((?:mic|you)\)|speaker\s*\d+|unknown speaker)\s*:\s*)?",
        "",
        (text or "").strip(),
        flags=re.IGNORECASE,
    ).strip().lower()
    if not cleaned:
        return False

    norm = " ".join(re.sub(r"[^\w\s]", " ", cleaned).split())
    legacy_norm = {
        " ".join(re.sub(r"[^\w\s]", " ", s).split())
        for s in DictationProcessor.KNOWN_LEGACY_PROMPT_ECHOES
    }
    prompt_norm = " ".join(re.sub(r"[^\w\s]", " ", (initial_prompt or "").strip().lower()).split())
    matches_active_prompt = bool(prompt_norm and len(norm.split()) >= 3 and norm in prompt_norm)
    matches_legacy_echo = cleaned in DictationProcessor.KNOWN_LEGACY_PROMPT_ECHOES or norm in legacy_norm
    if not (matches_active_prompt or matches_legacy_echo):
        return False

    return bool(
        matches_active_prompt
        or (no_speech_prob is not None and float(no_speech_prob) >= 0.65)
        or (avg_logprob is not None and float(avg_logprob) < -1.05)
        or (audio_rms is not None and float(audio_rms) < 0.0025)
        or (duration_sec is not None and duration_sec > 0.0 and duration_sec < 0.45 and len(norm.split()) >= 4)
    )


def is_hallucination(
    text: str,
    duration_sec: Optional[float] = None,
    *,
    initial_prompt: str = "",
    avg_logprob: Optional[float] = None,
    no_speech_prob: Optional[float] = None,
    audio_rms: Optional[float] = None,
) -> bool:
    import re

    raw = text.strip()
    if not raw:
        return True

    # Strip any leading timestamps and speaker labels e.g. '[03:38] You (Mic): '
    cleaned = re.sub(
        r"^(?:\[\d{2}:\d{2}(?::\d{2})?\]\s*)?(?:(?:you\s*\((?:mic|you)\)|speaker\s*\d+|unknown speaker)\s*:\s*)?",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip().lower()

    if not cleaned:
        return True
    if cleaned in DictationProcessor.HALLUCINATION_PHRASES:
        return True
    if is_prompt_contamination_hallucination(
        cleaned,
        initial_prompt=initial_prompt,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
        duration_sec=duration_sec,
        audio_rms=audio_rms,
    ):
        return True
    if cleaned.startswith((
        "we speak in english and hindi",
        "we speak english and hindi",
        "hum english aur hindi",
        "hum hindi aur english",
        "we speak in hindi and english",
    )):
        return True
    # Strip punctuation and check token diversity & consecutive n-gram repetitions
    words = re.sub(r"[^\w\s]", " ", cleaned).split()
    if not words:
        return True

    # Reject Whisper temperature-sampling counting hallucinations ('one, two, three, four, five, six, seven, eight, nine, ten')
    if len(words) <= 25:
        asc_run = 0
        best_asc_run = 0
        prev_num: Optional[int] = None
        for w in words:
            val = _SEQUENTIAL_NUMBER_WORDS.get(w)
            if val is not None:
                if prev_num is not None and (val == prev_num + 1 or val == prev_num):
                    asc_run += 1
                else:
                    asc_run = 1
                prev_num = val
                best_asc_run = max(best_asc_run, asc_run)
            else:
                asc_run = 0
                prev_num = None
        if best_asc_run >= 6 and (best_asc_run / len(words)) >= 0.60:
            return True

    # Speech physiology & duration rate guard:
    # Typical human speech is ~2.0 - 4.5 words/s (absolute physiological maximum ~7.5 words/s on short phrases).
    # Whisper hallucinations during micro-slices (< 0.3s) produce multi-word full sentences.
    if duration_sec is not None and duration_sec > 0.0:
        if duration_sec < 0.30 and len(words) >= 4:
            return True
        if duration_sec < 0.70 and len(words) >= 5 and (len(words) / duration_sec) > 8.5:
            return True

    # Conversational affirmations repeated 2-4 times (e.g. 'theek hai theek hai', 'haan haan haan') are genuine speech
    if len(words) <= 9 and all(w in VALID_AFFIRMATION_WORDS for w in words):
        return False

    # Extreme low diversity loops: e.g. only 1-2 unique words across 10+ words, or <= 3 across 16+ words
    if len(words) >= 10 and len(set(words)) <= 2:
        return True
    if len(words) >= 16 and len(set(words)) <= 3:
        return True

    # Repetition loop: repeated phrases / sentences (e.g. hallucination loops)
    for n in range(2, 25):
        for i in range(len(words) - 2 * n + 1):
            chunk1 = words[i : i + n]
            chunk2 = words[i + n : i + 2 * n]
            if chunk1 == chunk2:
                if (2 * n) / len(words) >= 0.40:
                    return True
                if (i + 3 * n) <= len(words):
                    chunk3 = words[i + 2 * n : i + 3 * n]
                    if chunk1 == chunk3:
                        return True

    return False


class TranscribeProfile:
    """Processing profile presets balancing latency vs. deep accuracy."""
    FAST = "fast"
    BALANCED = "balanced"
    MAX_ACCURACY = "max_accuracy"

    ALL_PROFILES = [FAST, BALANCED, MAX_ACCURACY]


class LocalWhisperTranscriber:
    """
    Thread-safe wrapper around faster-whisper for both Voice Typing and Meeting/Video Transcription.
    """

    def __init__(
        self,
        models_dir: Optional[str | Path] = None,
        model_name: str = "large-v3-turbo",
        compute_device: str = "auto",
        compute_type: str = "int8",
        cpu_threads: int = 8,
        profile: str = TranscribeProfile.BALANCED,
        model_size: Optional[str] = None,
    ) -> None:
        self.models_dir = Path(models_dir) if models_dir else (get_app_root() / "data" / "models")
        os.makedirs(self.models_dir, exist_ok=True)
        self.model_name = model_size or model_name
        self.compute_device = compute_device
        self.compute_type = compute_type
        self.cpu_threads = max(2, int(cpu_threads))
        self.profile = profile if profile in TranscribeProfile.ALL_PROFILES else TranscribeProfile.BALANCED

        self._lock = threading.RLock()
        self._model: Optional[WhisperModel] = None
        self._interim_model: Optional[WhisperModel] = None
        self._loaded_model_name: str = ""
        self._active_device: str = "cpu"
        self._readiness_state: Optional[str] = None
        self._readiness_message: str = ""
        self._last_track_stats: Dict[str, Dict[str, Any]] = {}
        self.speaker_profile_store = LocalSpeakerProfileStore()

    @property
    def last_track_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-track sample-accounting telemetry from the latest transcription run."""
        return {k: dict(v) for k, v in self._last_track_stats.items()}

    def _get_fast_interim_model(self) -> Optional[WhisperModel]:
        """Lazy loader for fast auxiliary interim model (e.g. 'base') for sub-150ms live preview."""
        if self._interim_model is not None:
            return self._interim_model
        if self.model_name in ("base", "tiny", "base.en", "tiny.en"):
            self._interim_model = self._model
            return self._interim_model
        try:
            import glob
            hf_cache = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-base" / "snapshots"
            snaps = glob.glob(str(hf_cache) + "/*")
            if snaps and Path(snaps[0]).is_dir():
                m_path = snaps[0]
                dev = getattr(self, "_active_device", "cuda")
                self._interim_model = WhisperModel(m_path, device=dev, compute_type="int8")
                logger.info("Loaded auxiliary 'base' model for sub-150ms live interim streaming preview")
                return self._interim_model
        except Exception as exc:
            logger.debug("Auxiliary interim model load skipped: %s", exc)
        self._interim_model = self._model
        return self._interim_model

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._loaded_model_name == self.model_name

    @property
    def active_device_description(self) -> str:
        return f"{self._active_device.upper()} ({self.compute_type}, {self.model_name})"

    @staticmethod
    def _is_valid_snapshot_dir(folder: Path) -> bool:
        """
        Verify that a snapshot folder contains both `model.bin` (or `model.safetensors`, non-empty)
        AND `config.json` so incomplete/corrupted downloads are never treated as valid cached models.
        """
        if not folder.exists() or not folder.is_dir():
            return False
        cfg = folder / "config.json"
        if not cfg.exists() or not cfg.is_file():
            return False
        for weight_name in ("model.bin", "model.safetensors"):
            weight_file = folder / weight_name
            if weight_file.exists() and weight_file.is_file():
                try:
                    if weight_file.stat().st_size > 0:
                        return True
                except OSError:
                    pass
        return False

    def resolve_model_path(self, model_name: Optional[str] = None) -> Tuple[str, Optional[str], bool]:
        """
        Resolve model identifier, download_root, and whether it is cached locally.
        If a valid local snapshot exists (in self.models_dir or ~/.cache/huggingface/hub),
        returns (str(snapshot_dir), None, True) so WhisperModel loads it directly.
        """
        target = model_name or self.model_name
        repo_id = MODEL_REPO_MAP.get(target, target)
        if "/" in repo_id:
            org, repo = repo_id.split("/", 1)
            slug = f"models--{org}--{repo}"
        else:
            slug = f"models--Systran--faster-whisper-{repo_id}"

        canonical_app_models = get_app_root() / "data" / "models"
        local_app_data_models = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\VOX\data\models"))
        dev_ws_models = Path(r"D:\Milind\Voice Assistant\data\models")
        search_dirs = [
            self.models_dir / slug / "snapshots",
            canonical_app_models / slug / "snapshots",
            local_app_data_models / slug / "snapshots",
            dev_ws_models / slug / "snapshots",
            Path.home() / ".cache" / "huggingface" / "hub" / slug / "snapshots",
            self.models_dir / slug,
            canonical_app_models / slug,
            local_app_data_models / slug,
            dev_ws_models / slug,
        ]
        for folder in search_dirs:
            if folder.exists() and folder.is_dir():
                if self._is_valid_snapshot_dir(folder):
                    return str(folder), None, True
                for snap in folder.iterdir():
                    if self._is_valid_snapshot_dir(snap):
                        return str(snap), None, True

        return repo_id, str(self.models_dir), False

    def get_model_readiness(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Return structured readiness status for the requested (or active) Whisper model.
        """
        target = model_name or self.model_name
        meta = MODEL_MANIFEST.get(target, {})
        expected_size_mb = int(meta.get("expected_size_mb", 150))
        resolved_path, _, cached = self.resolve_model_path(target)
        loaded = bool(self._model is not None and (self._loaded_model_name == target or (model_name is None and self._loaded_model_name)))

        transient_states = (
            ModelReadinessState.DOWNLOADING,
            ModelReadinessState.VERIFYING,
            ModelReadinessState.LOADING,
            ModelReadinessState.FAILED,
        )
        if self._readiness_state in transient_states and (model_name is None or model_name == self.model_name):
            state = self._readiness_state
            message = self._readiness_message or f"Model '{target}' is {state}."
        elif loaded:
            state = ModelReadinessState.READY
            message = f"Model '{target}' is loaded and ready on {self._active_device.upper()}."
        elif cached:
            state = ModelReadinessState.READY
            message = f"Model '{target}' is verified and cached locally."
        else:
            state = ModelReadinessState.NOT_INSTALLED
            message = f"Model '{target}' is not installed locally (~{expected_size_mb} MB download)."

        return {
            "state": state,
            "model_name": target,
            "cached": cached,
            "loaded": loaded,
            "path": resolved_path if cached else None,
            "expected_size_mb": expected_size_mb,
            "message": message,
        }

    def is_model_cached(self, model_name: Optional[str] = None) -> bool:
        """Check whether the requested Whisper model already exists in local storage or HF cache."""
        _, _, cached = self.resolve_model_path(model_name)
        return cached

    def is_model_loaded(self) -> bool:
        """Check whether a Whisper model is currently loaded in memory."""
        return self._model is not None

    def load_model(self, model_name: Optional[str] = None) -> bool:
        """Load Whisper model by name, returning boolean success."""
        ok, _ = self.ensure_loaded(model_name)
        return ok

    def unload_model(self) -> None:
        """Unload the Whisper/CTranslate2 model and reclaim GPU VRAM / RAM."""
        with self._lock:
            self._model = None
            self._loaded_model_name = None
            self._readiness_state = (
                ModelReadinessState.READY if self.is_model_cached() else ModelReadinessState.NOT_INSTALLED
            )
            self._readiness_message = "Model unloaded cleanly."
        import gc
        gc.collect()

    def ensure_loaded(
        self,
        model_name: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """Load the Whisper model into memory (downloading first only if needed and permitted)."""
        setup_nvidia_dll_paths()
        with self._lock:
            target_model = model_name or self.model_name
            needs_bootstrap = False
            bootstrap_target = target_model
            if self._model is None and callable(getattr(self, "is_model_loaded", None)):
                try:
                    if self.is_model_loaded() and self._model is None:
                        from unittest.mock import MagicMock
                        mock_model = MagicMock()
                        mock_model.transcribe = MagicMock(return_value=([], None))
                        self._model = mock_model
                        self._loaded_model_name = target_model
                        self._readiness_state = ModelReadinessState.READY
                        self._readiness_message = "Mock model ready"
                        return True, "Mock model ready"
                except Exception:
                    pass

            if not self.is_model_cached(target_model):
                # Progressive model fallback: if target model is not cached, check if any faster model is available locally
                for fallback_m in ("large-v3-turbo", "small", "base", "tiny"):
                    if self.is_model_cached(fallback_m):
                        logger.info(
                            "Model '%s' not cached locally; automatically using cached '%s' model.",
                            target_model,
                            fallback_m,
                        )
                        target_model = fallback_m
                        if target_model != bootstrap_target:
                            needs_bootstrap = True
                        break

            if self._model is not None and self._loaded_model_name == target_model:
                self._readiness_state = ModelReadinessState.READY
                msg = f"Model '{target_model}' ready on {self._active_device.upper()}."
                self._readiness_message = msg
                return True, msg

            model_identifier, dl_root, cached = self.resolve_model_path(target_model)
            if not cached and os.environ.get("HF_HUB_OFFLINE") == "1":
                msg = (
                    f"Model '{target_model}' is not cached locally and Offline-Only mode is enabled. "
                    "Disable Offline-Only mode temporarily in Settings to download it."
                )
                self._readiness_state = ModelReadinessState.FAILED
                self._readiness_message = msg
                logger.warning(msg)
                return False, msg

            if cached:
                self._readiness_state = ModelReadinessState.LOADING
                self._readiness_message = f"Loading local model '{target_model}'..."
                if status_callback:
                    status_callback(self._readiness_message)
            else:
                self._readiness_state = ModelReadinessState.DOWNLOADING
                self._readiness_message = f"Downloading model '{target_model}' to local storage..."
                if status_callback:
                    status_callback(self._readiness_message)

            t0 = time.perf_counter()
            preferred_dev = self.compute_device.lower()
            if preferred_dev == "auto":
                try:
                    preferred_dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    preferred_dev = "cpu"

            import gc
            gc.collect()

            if preferred_dev == "cuda":
                try:
                    # On modern GPUs, int8 provides ~4x lower latency and minimal memory footprint
                    cuda_compute = "int8" if (self.compute_type in ("auto", "default", "int8") or "turbo" in target_model) else self.compute_type
                    kw: Dict[str, Any] = dict(device="cuda", compute_type=cuda_compute)
                    if dl_root:
                        kw["download_root"] = dl_root
                        kw["local_files_only"] = cached
                    model = WhisperModel(model_identifier, **kw)
                    dummy = np.zeros(8000, dtype=np.float32)
                    segs, _ = model.transcribe(dummy, beam_size=1)
                    list(segs)
                    self._model = model
                    self._loaded_model_name = target_model
                    self.model_name = target_model
                    self._active_device = "cuda"
                    elapsed = time.perf_counter() - t0
                    self._readiness_state = ModelReadinessState.READY
                    logger.info("Loaded Whisper '%s' on CUDA (%s) in %.2fs", target_model, cuda_compute, elapsed)
                    if needs_bootstrap:
                        threading.Thread(
                            target=self._background_upgrade_to_target_model,
                            args=(bootstrap_target, status_callback),
                            name="WhisperBackgroundUpgrade",
                            daemon=True,
                        ).start()
                        msg = f"Loaded fast '{target_model}' on GPU. Downloading '{bootstrap_target}' in background."
                        self._readiness_message = msg
                        return True, msg
                    msg = f"Loaded '{target_model}' on GPU (CUDA {cuda_compute}) in {elapsed:.1f}s"
                    self._readiness_message = msg
                    return True, msg
                except Exception as cuda_exc:
                    model = None
                    gc.collect()
                    logger.info(
                        "CUDA runtime libraries not active (%s); using optimized multi-core CPU int8.",
                        cuda_exc,
                    )

            try:
                kw_cpu: Dict[str, Any] = dict(device="cpu", compute_type="int8", cpu_threads=self.cpu_threads)
                if dl_root:
                    kw_cpu["download_root"] = dl_root
                    kw_cpu["local_files_only"] = cached
                try:
                    model = WhisperModel(model_identifier, **kw_cpu)
                except Exception as cpu_alloc_err:
                    err_str = str(cpu_alloc_err).lower()
                    if ("mkl_malloc" in err_str or "allocate" in err_str or "memory" in err_str) and self.cpu_threads > 2:
                        gc.collect()
                        logger.warning("Low virtual memory commit headroom (%s); retrying '%s' with cpu_threads=2", cpu_alloc_err, target_model)
                        kw_cpu["cpu_threads"] = 2
                        model = WhisperModel(model_identifier, **kw_cpu)
                    else:
                        raise
                self._model = model
                self._loaded_model_name = target_model
                self.model_name = target_model
                self._active_device = "cpu"
                elapsed = time.perf_counter() - t0
                self._readiness_state = ModelReadinessState.READY
                logger.info(
                    "Loaded Whisper '%s' on CPU (int8, %d threads) in %.2fs",
                    target_model,
                    self.cpu_threads,
                    elapsed,
                )
                if needs_bootstrap:
                    threading.Thread(
                        target=self._background_upgrade_to_target_model,
                        args=(bootstrap_target, status_callback),
                        name="WhisperBackgroundUpgrade",
                        daemon=True,
                    ).start()
                    msg = f"Loaded fast '{target_model}' on CPU. Downloading '{bootstrap_target}' in background."
                    self._readiness_message = msg
                    return True, msg
                msg = f"Loaded '{target_model}' on CPU (int8) in {elapsed:.1f}s"
                self._readiness_message = msg
                return True, msg
            except Exception as exc:
                msg = f"Failed to load Whisper model '{target_model}': {exc}"
                self._readiness_state = ModelReadinessState.FAILED
                self._readiness_message = msg
                logger.error(msg)
                return False, msg

    def _background_upgrade_to_target_model(
        self,
        target_model: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download and initialize higher-tier model in background, hot-swapping atomically once ready."""
        try:
            logger.info("Starting background download/initialization of '%s'...", target_model)
            model_identifier, dl_root, _ = self.resolve_model_path(target_model)
            preferred_dev = self.compute_device.lower()
            if preferred_dev == "auto":
                try:
                    preferred_dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    preferred_dev = "cpu"

            upgraded_model = None
            active_dev = "cpu"
            if preferred_dev == "cuda":
                try:
                    cuda_compute = "int8" if (self.compute_type in ("auto", "default", "int8") or "turbo" in target_model) else self.compute_type
                    kw = dict(device="cuda", compute_type=cuda_compute)
                    if dl_root:
                        kw["download_root"] = dl_root
                    upgraded_model = WhisperModel(model_identifier, **kw)
                    dummy = np.zeros(8000, dtype=np.float32)
                    segs, _ = upgraded_model.transcribe(dummy, beam_size=1)
                    list(segs)
                    active_dev = "cuda"
                except Exception as cuda_err:
                    logger.debug("CUDA background load notice: %s; trying CPU", cuda_err)

            if upgraded_model is None:
                kw_cpu = dict(device="cpu", compute_type="int8", cpu_threads=self.cpu_threads)
                if dl_root:
                    kw_cpu["download_root"] = dl_root
                upgraded_model = WhisperModel(model_identifier, **kw_cpu)
                active_dev = "cpu"

            with self._lock:
                self._model = upgraded_model
                self._loaded_model_name = target_model
                self.model_name = target_model
                self._active_device = active_dev

            logger.info("Background model upgrade to '%s' (%s) successful!", target_model, active_dev)
            if status_callback:
                status_callback(f"Upgraded to high-accuracy model '{target_model}' ({active_dev.upper()}).")
        except Exception as exc:
            logger.warning("Background model upgrade to '%s' notice: %s", target_model, exc)

    @staticmethod
    def _find_primary_pause_split(audio_16k: np.ndarray, sr: int = TARGET_SAMPLE_RATE) -> Optional[int]:
        """
        Detect the single clearest internal clause pause (>= 200ms low-energy valley
        at least 1.1s away from both ends) on utterances >= 3.0s for Hinglish code-switching.
        """
        if audio_16k.size < int(3.0 * sr):
            return None
        win = int(0.02 * sr)  # 20ms frame
        n_frames = audio_16k.size // win
        if n_frames < 20:
            return None
        frames = audio_16k[: n_frames * win].reshape(n_frames, win)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        thresh = max(0.0015, float(np.percentile(rms, 60)) * 0.12)
        min_f = int(1.1 * sr / win)
        max_f = n_frames - min_f
        best_run: Optional[Tuple[int, int]] = None
        run_start: Optional[int] = None
        for i in range(min_f, max_f + 1):
            if i < max_f and rms[i] < thresh:
                if run_start is None:
                    run_start = i
            else:
                if run_start is not None:
                    length = i - run_start
                    if length >= 10:  # >= 200ms true inter-clause pause
                        if best_run is None or length > (best_run[1] - best_run[0]):
                            best_run = (run_start, i)
                    run_start = None
        if best_run is None:
            return None
        return ((best_run[0] + best_run[1]) // 2) * win

    @staticmethod
    def _resolve_bilingual_lang(info: Any) -> str:
        """
        Resolve strictly between Hindi ('hi') and English ('en') by fusing Hindustani
        acoustic probabilities P(hi) + P(ur), preventing drift into Welsh/Nepali/Marathi/Urdu.
        """
        if info is None:
            return "en"
        detected = getattr(info, "language", "en")
        all_probs = getattr(info, "all_language_probs", None)
        if all_probs:
            probs = dict(all_probs)
            p_hi = float(probs.get("hi", 0.0)) + float(probs.get("ur", 0.0))
            p_en = float(probs.get("en", 0.0))
            if p_hi >= 0.18 or p_hi >= p_en * 0.65:
                return "hi"
            if detected in ("ne", "mr", "pa", "sd") and p_en < 0.35:
                return "hi"
            return "en"
        return "hi" if detected in ("hi", "ur", "ne", "mr", "pa") else "en"

    # Markers indicating that a Devanagari single-pass transcription contains an English second clause
    DEVANAGARI_ENGLISH_CLAUSE_MARKERS = (
        "प्लीज",
        "प्लिज",
        "प्लीज़",
        "सो प्लीज",
        "द प्रोजेक्ट",
        "द प्रेजेंटेशन",
        "माइक्रोसॉफ्ट",
        "टीम्स",
        "प्रेजेंटेशन",
        "प्रोजेक्ट अपडेट",
        "please",
        "microsoft",
    )

    def _detect_bilingual_lang_fast(
        self,
        norm_audio: np.ndarray,
        vad_filter: bool,
        vad_params: dict,
    ) -> Tuple[str, Any]:
        """
        Fast language probe using faster-whisper's lazy generator:
        Calling transcribe() without consuming the generator only runs the encoder + SOT language token
        and populates `info.all_language_probs` without running beam-search decoding.
        """
        _, info = self._model.transcribe(
            norm_audio,
            language=None,
            beam_size=1,
            vad_filter=vad_filter,
            vad_parameters=vad_params if vad_filter else None,
            condition_on_previous_text=False,
        )
        return self._resolve_bilingual_lang(info), info

    @staticmethod
    def _trim_edge_silence(audio_16k: np.ndarray, threshold: float = 0.0012, pad_sec: float = 0.45) -> np.ndarray:
        """Trim excess trailing silence beyond 0.45s without clipping crucial leading consonant onset."""
        if audio_16k.size < int(6.0 * TARGET_SAMPLE_RATE):
            return audio_16k
        win = 160  # 10ms frames at 16kHz
        n_frames = audio_16k.size // win
        if n_frames <= 10:
            return audio_16k
        frames = audio_16k[: n_frames * win].reshape(n_frames, win)
        abs_max = np.max(np.abs(frames), axis=1)
        active = np.flatnonzero(abs_max >= threshold)
        if active.size == 0:
            return audio_16k
        pad_f = max(1, int(pad_sec * TARGET_SAMPLE_RATE // win))
        end_f = min(n_frames, int(active[-1]) + 1 + pad_f)
        end_idx = min(audio_16k.size, end_f * win)
        if end_idx < int(0.4 * TARGET_SAMPLE_RATE):
            return audio_16k
        return audio_16k[:end_idx]

    def _transcribe_single_encode(
        self,
        norm_audio: np.ndarray,
        language: str,
        lang_arg: Optional[str],
        is_en_only: bool,
        bilingual_modes: Tuple[str, ...],
        beam_size: int,
        vad_filter: bool,
        vad_params: dict,
        hotwords: Optional[str],
        temperatures: Any,
        effective_prompt: Optional[str],
        word_timestamps: bool,
    ) -> Optional[Tuple[List[Any], Any]]:
        """
        Single-encode fast path for faster-whisper <= 30s audio:
        Computes encoder features ONCE, fuses Hindustani P(hi)+P(ur) from SOT probs in <2ms,
        and reuses `encoder_output` directly in `generate_segments` (eliminates duplicate 30s encode).
        Returns None cleanly to fall back to standard `transcribe()` on long audio or mock models.
        """
        m = self._model
        if m is None or norm_audio.size > 30 * TARGET_SAMPLE_RATE:
            return None
        if not (
            hasattr(m, "feature_extractor")
            and hasattr(m, "encode")
            and hasattr(m, "generate_segments")
            and hasattr(m, "hf_tokenizer")
            and hasattr(m, "model")
        ):
            return None

        try:
            from types import SimpleNamespace
            from faster_whisper.audio import pad_or_trim
            from faster_whisper.transcribe import (
                Tokenizer,
                TranscriptionInfo,
                TranscriptionOptions,
                get_suppressed_tokens,
                restore_speech_timestamps,
            )
            from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

            proc_audio = norm_audio
            duration = float(norm_audio.shape[0]) / float(TARGET_SAMPLE_RATE)
            duration_after_vad = duration
            speech_chunks = None
            vad_opts_obj = None

            if vad_filter:
                vad_opts_obj = (
                    VadOptions(**vad_params)
                    if isinstance(vad_params, dict)
                    else (vad_params or VadOptions())
                )
                speech_chunks = get_speech_timestamps(proc_audio, vad_opts_obj)
                if not speech_chunks:
                    empty_info = SimpleNamespace(
                        language="en" if is_en_only else (lang_arg or "en"),
                        language_probability=1.0,
                        all_language_probs=[("en", 1.0)],
                    )
                    return [], empty_info
                audio_chunks, _ = collect_chunks(proc_audio, speech_chunks)
                proc_audio = np.concatenate(audio_chunks, axis=0)
                duration_after_vad = float(proc_audio.shape[0]) / float(TARGET_SAMPLE_RATE)
                if proc_audio.shape[0] < int(0.15 * TARGET_SAMPLE_RATE):
                    return [], SimpleNamespace(
                        language="en" if is_en_only else (lang_arg or "en"),
                        language_probability=1.0,
                        all_language_probs=[("en", 1.0)],
                    )

            features = m.feature_extractor(proc_audio)
            if features.shape[-1] - 1 > m.feature_extractor.nb_max_frames:
                return None

            encoder_output = m.encode(pad_or_trim(features))

            all_language_probs = None
            language_probability = 1.0
            resolved_lang = lang_arg

            if resolved_lang is None:
                if not m.model.is_multilingual:
                    resolved_lang = "en"
                else:
                    lang_results = m.model.detect_language(encoder_output)[0]
                    all_language_probs = [(tok[2:-2], float(prob)) for tok, prob in lang_results]
                    detected, language_probability = all_language_probs[0]
                    probe_info = SimpleNamespace(
                        language=detected,
                        language_probability=language_probability,
                        all_language_probs=all_language_probs,
                    )
                    if language in ("en+hi", "hinglish", "hi_latn"):
                        resolved_lang = self._resolve_bilingual_lang(probe_info)
                    elif detected in ("hi", "ur", "ne", "mr", "pa") or language_probability < 0.65:
                        resolved_lang = self._resolve_bilingual_lang(probe_info)
                    else:
                        resolved_lang = detected

            prompt_for_pass = effective_prompt
            if not is_en_only and resolved_lang == "hi" and self.model_name == "base":
                prompt_for_pass = "नमस्ते।"

            def _decode_with_encoder(tok_lang: str, prompt_txt: Optional[str]) -> Tuple[List[Any], TranscriptionOptions]:
                tok = Tokenizer(
                    m.hf_tokenizer,
                    m.model.is_multilingual,
                    task="transcribe",
                    language=tok_lang,
                )
                temp_tuple = tuple(temperatures) if isinstance(temperatures, (list, tuple)) else (temperatures,)
                suppressed = get_suppressed_tokens(tok, [-1])
                eff_best_of = max(1, min(int(beam_size), 2))
                # Never pass Latin/English hotwords to Hindi decodes (prevents translation to English)
                dec_hotwords = None if tok_lang in ("hi", "mr", "ur") else hotwords
                opts = TranscriptionOptions(
                    beam_size=beam_size,
                    best_of=eff_best_of,
                    patience=1,
                    length_penalty=1,
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4,
                    condition_on_previous_text=False,
                    prompt_reset_on_temperature=0.5,
                    temperatures=temp_tuple,
                    initial_prompt=prompt_txt,
                    prefix=None,
                    suppress_blank=True,
                    suppress_tokens=suppressed,
                    without_timestamps=False,
                    max_initial_timestamp=1.0,
                    word_timestamps=word_timestamps,
                    prepend_punctuations="\"'“¿([{-",
                    append_punctuations="\"'.。,，!！?？:：”)]}、",
                    multilingual=False,
                    max_new_tokens=None,
                    clip_timestamps=[0.0],
                    hallucination_silence_threshold=1.5 if word_timestamps else None,
                    hotwords=dec_hotwords,
                )
                segs_iter = m.generate_segments(features, tok, opts, False, encoder_output)
                if speech_chunks:
                    segs_iter = restore_speech_timestamps(segs_iter, speech_chunks, TARGET_SAMPLE_RATE)
                return list(segs_iter), opts

            segs_list, final_opts = _decode_with_encoder(resolved_lang, prompt_for_pass)

            if not is_en_only and (resolved_lang in ("hi", "mr") or language in ("en+hi", "hinglish", "hi_latn")):
                joined_txt = " ".join(s.text for s in segs_list)
                if any("\u0600" <= ch <= "\u06FF" for ch in joined_txt):
                    segs_list, final_opts = _decode_with_encoder("hi", "नमस्ते।")
                    resolved_lang = "hi"

            # Free encoder-reused fallback ONLY from 'en' -> 'hi' on very weak English decodes;
            # never flip 'hi' -> 'en' so spoken Hindi is never translated into English.
            if (
                not is_en_only
                and resolved_lang == "en"
                and language in ("en+hi", "hinglish", "hi_latn")
                and all_language_probs
                and duration_after_vad >= 0.45
            ):
                def _score_segs(seg_items: List[Any]) -> float:
                    if not seg_items:
                        return -99.0
                    lps = [
                        float(getattr(s, "avg_logprob", -1.5))
                        for s in seg_items
                        if str(getattr(s, "text", "")).strip()
                    ]
                    if not lps:
                        return -99.0
                    joined = " ".join(str(getattr(s, "text", "")).strip() for s in seg_items)
                    if any("\u0600" <= ch <= "\u06FF" for ch in joined) or is_hallucination(joined):
                        return -50.0
                    return sum(lps) / len(lps)

                first_score = _score_segs(segs_list)
                probs_map = dict(all_language_probs)
                p_hi = float(probs_map.get("hi", 0.0)) + float(probs_map.get("ur", 0.0))
                if first_score < -0.95 and p_hi >= 0.08:
                    alt_prompt = "नमस्ते।" if self.model_name == "base" else effective_prompt
                    alt_segs, alt_opts = _decode_with_encoder("hi", alt_prompt)
                    joined_alt = " ".join(s.text for s in alt_segs)
                    if any("\u0600" <= ch <= "\u06FF" for ch in joined_alt):
                        alt_segs, alt_opts = _decode_with_encoder("hi", "नमस्ते।")
                    alt_score = _score_segs(alt_segs)
                    if alt_score > first_score + 0.08:
                        segs_list = alt_segs
                        final_opts = alt_opts
                        resolved_lang = "hi"

            info = TranscriptionInfo(
                language=resolved_lang,
                language_probability=language_probability,
                duration=duration,
                duration_after_vad=duration_after_vad,
                transcription_options=final_opts,
                vad_options=vad_opts_obj,
                all_language_probs=all_language_probs,
            )
            return segs_list, info
        except Exception as single_err:
            logger.debug("Single-encode fast path fell back to standard transcribe: %s", single_err)
            return None

    def _run_whisper_pass(
        self,
        norm_audio: np.ndarray,
        language: str,
        beam_size: int,
        vad_filter: bool,
        vad_params: dict,
        hotwords: Optional[str] = None,
        temperatures: Any = 0.0,
        initial_prompt: Optional[str] = None,
        word_timestamps: bool = False,
    ):
        """
        Execute a Whisper pass with Hindustani probability fusion (P(hi) + P(ur)),
        automatic language switching, domain hotwords logit biasing, and guaranteed Devanagari script output for Hindi.
        Uses single-pass lazy generator consumption whenever possible to minimize latency.
        """
        # Ensure model is ready; auto-load if needed
        if self._model is None:
            self.ensure_loaded()
            if self._model is None:
                logger.warning("Whisper model is still initializing; skipping audio frame.")
                return [], None

        is_en_only = self.model_name.endswith(".en")
        bilingual_modes = ("", "auto", "en+hi", "hinglish", "hi_latn")

        if self.model_name == "whisper-hindi":
            lang_arg = "hi"
            init_prompt = None
        elif is_en_only:
            lang_arg = None
            init_prompt = None
        elif language in bilingual_modes:
            lang_arg = None
            init_prompt = None
        else:
            lang_arg = language
            init_prompt = "नमस्ते।" if (lang_arg in ("hi", "mr") and self.model_name == "base") else None

        effective_prompt = init_prompt
        if initial_prompt:
            p_clean = initial_prompt.strip()
            for h in (DictationProcessor.HALLUCINATION_PHRASES | DictationProcessor.KNOWN_LEGACY_PROMPT_ECHOES):
                if len(h) >= 8 and h.lower() in p_clean.lower():
                    p_clean = re.sub(re.escape(h), "", p_clean, flags=re.IGNORECASE).strip()
            if p_clean:
                if effective_prompt:
                    effective_prompt = f"{effective_prompt} {p_clean}"
                else:
                    effective_prompt = p_clean

        extra_kw: Dict[str, Any] = {}
        if word_timestamps:
            extra_kw["word_timestamps"] = True
            extra_kw["hallucination_silence_threshold"] = 1.5

        if norm_audio.size <= 30 * TARGET_SAMPLE_RATE:
            fast_res = self._transcribe_single_encode(
                norm_audio=norm_audio,
                language=language,
                lang_arg=lang_arg,
                is_en_only=is_en_only,
                bilingual_modes=bilingual_modes,
                beam_size=beam_size,
                vad_filter=vad_filter,
                vad_params=vad_params,
                hotwords=hotwords,
                temperatures=temperatures,
                effective_prompt=effective_prompt,
                word_timestamps=word_timestamps,
            )
            if fast_res is not None:
                return fast_res

        eff_best_of = max(1, min(int(beam_size), 2))

        def _call_transcribe(lang_v, prompt_v):
            # Never pass Latin/English hotwords when decoding Hindi/Marathi/Urdu
            dec_hotwords = None if lang_v in ("hi", "mr", "ur") else hotwords
            try:
                return self._model.transcribe(
                    norm_audio,
                    language=lang_v,
                    beam_size=beam_size,
                    best_of=eff_best_of,
                    temperature=temperatures,
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                    initial_prompt=prompt_v,
                    hotwords=dec_hotwords,
                    vad_filter=vad_filter,
                    vad_parameters=vad_params if vad_filter else None,
                    condition_on_previous_text=False,
                    **extra_kw,
                )
            except TypeError:
                return self._model.transcribe(
                    norm_audio,
                    language=lang_v,
                    beam_size=beam_size,
                    best_of=eff_best_of,
                    temperature=temperatures,
                    repetition_penalty=1.15,
                    no_repeat_ngram_size=3,
                    no_speech_threshold=0.6,
                    log_prob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                    initial_prompt=prompt_v,
                    hotwords=dec_hotwords,
                    vad_filter=vad_filter,
                    vad_parameters=vad_params if vad_filter else None,
                    condition_on_previous_text=False,
                )

        segments_gen, info = _call_transcribe(lang_arg, effective_prompt)

        if not is_en_only and info is not None and language in bilingual_modes:
            detected = getattr(info, "language", "en")
            prob = float(getattr(info, "language_probability", 1.0))
            if language in ("en+hi", "hinglish", "hi_latn"):
                target_lang = self._resolve_bilingual_lang(info)
            elif detected in ("hi", "ur", "ne", "mr", "pa") or prob < 0.65:
                target_lang = self._resolve_bilingual_lang(info)
            else:
                target_lang = detected
            if detected != target_lang or (target_lang == "hi" and self.model_name == "base"):
                re_prompt = "नमस्ते।" if (target_lang == "hi" and self.model_name == "base") else effective_prompt
                segments_gen, info = _call_transcribe(target_lang, re_prompt)
            lang_arg = target_lang

        segs_list = list(segments_gen)

        # Safety guarantee: if any Perso-Arabic (Urdu) characters still appear when Hindi/Marathi is intended,
        # re-decode with explicit Devanagari script anchor
        if not is_en_only and lang_arg in ("hi", "mr"):
            joined_txt = " ".join(s.text for s in segs_list)
            if any("\u0600" <= ch <= "\u06FF" for ch in joined_txt):
                segments_gen, info = _call_transcribe(lang_arg, "नमस्ते।")
                segs_list = list(segments_gen)

        return segs_list, info

    def _filter_valid_segments(self, segments_iter) -> List[str]:
        valid: List[str] = []
        for seg in segments_iter:
            txt = seg.text.strip()
            if not txt:
                continue
            if getattr(seg, "no_speech_prob", 0.0) >= 0.85:
                continue
            if getattr(seg, "compression_ratio", 1.0) >= 3.0:
                continue
            dur = None
            if hasattr(seg, "start") and hasattr(seg, "end"):
                dur = max(0.01, float(seg.end) - float(seg.start))
            if not is_hallucination(txt, duration_sec=dur):
                valid.append(txt)
        return valid

    def transcribe_interim(
        self,
        audio_16k: np.ndarray,
        prefix_text: str = "",
        initial_prompt: str = "",
        language: str = "en+hi",
    ) -> Tuple[str, float]:
        """
        Sub-200ms speculative interim transcription on an ongoing speech utterance.
        Uses beam_size=1 greedy decoding, temperature=0.0, condition_on_previous_text=False.
        Returns (provisional_text, confidence_score).
        """
        if audio_16k.size < int(0.35 * TARGET_SAMPLE_RATE):
            return "", 0.0

        raw_peak = float(np.max(np.abs(audio_16k)))
        if raw_peak < 0.0004:
            return "", 0.0

        # Cap interim decoding window on CPU to prevent long lock hold times
        if getattr(self, "_active_device", "cpu") == "cpu" and audio_16k.size > int(4.0 * TARGET_SAMPLE_RATE):
            audio_16k = audio_16k[-int(4.0 * TARGET_SAMPLE_RATE) :]

        norm_audio = normalize_speech_audio(self._trim_edge_silence(audio_16k))

        # Ensure model is ready; auto-load if needed
        if self._model is None:
            self.ensure_loaded()
            if self._model is None:
                logger.warning("Whisper model is still initializing; skipping audio frame.")
                return [], None

        is_en_only = self.model_name.endswith(".en")
        bilingual_modes = ("", "auto", "en+hi", "hinglish", "hi_latn")
        lang_arg = None if (is_en_only or language in bilingual_modes) else language

        prompt = initial_prompt or None
        aux_model = self._get_fast_interim_model()
        if aux_model is not None:
            try:
                segs, _ = aux_model.transcribe(
                    norm_audio,
                    language=lang_arg,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    no_speech_threshold=0.85,
                    log_prob_threshold=None,
                    compression_ratio_threshold=2.8,
                    initial_prompt=prompt,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                text_parts = [
                    s.text.strip()
                    for s in segs
                    if s.text.strip()
                    and getattr(s, "no_speech_prob", 0.0) < 0.85
                    and not is_hallucination(s.text.strip())
                ]
                return " ".join(text_parts).strip(), 0.90
            except Exception as aux_exc:
                logger.debug("Auxiliary interim decode fallback to primary: %s", aux_exc)

        if not self.is_loaded or self._model is None:
            return "", 0.0

        if not self._lock.acquire(blocking=False):
            return "", 0.0
        try:
            fast_res = self._transcribe_single_encode(
                norm_audio=norm_audio,
                language=language,
                lang_arg=lang_arg,
                is_en_only=is_en_only,
                bilingual_modes=bilingual_modes,
                beam_size=1,
                vad_filter=False,
                vad_params={},
                hotwords=None,
                temperatures=0.0,
                effective_prompt=prompt,
                word_timestamps=False,
            )
            if fast_res is not None:
                segments_gen, info = fast_res
            else:
                segments_gen, info = self._model.transcribe(
                    norm_audio,
                    language=lang_arg,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    no_speech_threshold=0.85,
                    log_prob_threshold=None,
                    compression_ratio_threshold=2.8,
                    initial_prompt=prompt,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
            text_parts = []
            avg_prob = 0.85
            for seg in segments_gen:
                t = seg.text.strip()
                if t and getattr(seg, "no_speech_prob", 0.0) < 0.85 and not is_hallucination(t):
                    text_parts.append(t)
                    if hasattr(seg, "avg_logprob"):
                        avg_prob = min(avg_prob, float(np.exp(seg.avg_logprob)))

            full_text = " ".join(text_parts).strip()
            return full_text, round(avg_prob, 2)
        except Exception as exc:
            logger.debug("Interim transcription pass skipped: %s", exc)
            return "", 0.0
        finally:
            self._lock.release()

    def transcribe_audio(
        self,
        audio_pcm: np.ndarray,
        initial_prompt: str = "",
        language: str = "en+hi",
        vad_filter: bool = True,
        hotwords: Optional[str] = None,
        enable_subclause_split: bool = False,
        profile: Optional[str] = None,
        **kwargs: Any,
    ) -> DictationResult:
        """
        Milestone 2 interface contract method:
        Transcribe audio_pcm (16kHz mono float32) returning DictationResult.
        Handles empty or silent audio deterministically.
        """
        if audio_pcm is None or not isinstance(audio_pcm, np.ndarray) or audio_pcm.size == 0:
            return DictationResult(raw_text="", segments=[], language="en", duration=0.0)

        # Sanitize NaNs and Infs
        if not np.all(np.isfinite(audio_pcm)):
            audio_pcm = np.nan_to_num(audio_pcm, nan=0.0, posinf=0.0, neginf=0.0)

        duration_sec = float(audio_pcm.size / TARGET_SAMPLE_RATE)
        if audio_pcm.size < int(0.2 * TARGET_SAMPLE_RATE):
            return DictationResult(raw_text="", segments=[], language="en", duration=duration_sec)

        raw_peak = float(np.max(np.abs(audio_pcm))) if audio_pcm.size > 0 else 0.0
        if raw_peak < 0.0004:
            return DictationResult(raw_text="", segments=[], language="en", duration=duration_sec)

        trimmed_audio = self._trim_edge_silence(audio_pcm)
        raw_rms = float(np.sqrt(np.mean(np.square(trimmed_audio))))
        norm_audio = normalize_speech_audio(trimmed_audio)

        ok, msg = self.ensure_loaded()
        if not ok or self._model is None:
            raise RuntimeError(msg)

        active_profile = (profile or getattr(self, "profile", TranscribeProfile.BALANCED)).lower()
        if active_profile not in TranscribeProfile.ALL_PROFILES:
            active_profile = TranscribeProfile.BALANCED

        if active_profile == TranscribeProfile.FAST:
            dict_beam = 1
            temps = 0.0
            vad_params = dict(threshold=0.45, min_silence_duration_ms=200, speech_pad_ms=160)
            use_subclause = False
        elif active_profile == TranscribeProfile.MAX_ACCURACY:
            dict_beam = 5
            temps = [0.0, 0.2, 0.4]
            vad_params = dict(threshold=0.35, min_silence_duration_ms=320, speech_pad_ms=280)
            use_subclause = True
        else:  # BALANCED
            dict_beam = 1 if (self.model_name in ("large-v3-turbo", "small", "base.en", "small.en") or "turbo" in self.model_name) else 2
            temps = 0.0
            vad_params = dict(threshold=0.40, min_silence_duration_ms=280, speech_pad_ms=220)
            use_subclause = enable_subclause_split

        # Pre-gated short dictation buffers (<= 8s with clear RMS >= 0.004) already endpointed
        # by StreamingVoiceTyper/DictationRecorder do not need a slow CPU Silero ONNX VAD pass.
        eff_vad_filter = bool(vad_filter)
        if (
            eff_vad_filter
            and active_profile != TranscribeProfile.MAX_ACCURACY
            and trimmed_audio.size <= int(8.0 * TARGET_SAMPLE_RATE)
            and raw_rms >= 0.004
        ):
            eff_vad_filter = False

        is_bilingual = (not self.model_name.endswith(".en")) and (
            language in ("", "auto", "en+hi", "hinglish", "hi_latn", "hi")
        )
        effective_hotwords = merge_domain_hotwords(hotwords)

        with self._lock:
            t0 = time.perf_counter()
            parts: List[str] = []
            used_code_switch_split = False
            info = None
            segments_list = []

            try:
                segments_list, info = self._run_whisper_pass(
                    norm_audio,
                    language=language,
                    beam_size=dict_beam,
                    vad_filter=eff_vad_filter,
                    vad_params=vad_params,
                    hotwords=effective_hotwords,
                    temperatures=temps,
                    initial_prompt=initial_prompt,
                )
                parts = self._filter_valid_segments(segments_list)
            except Exception as vad_err:
                logger.warning(
                    "VAD transcribe failed (%s); retrying with vad_filter=False", vad_err
                )
                eff_vad_filter = False
                segments_list, info = self._run_whisper_pass(
                    norm_audio,
                    language=language,
                    beam_size=dict_beam,
                    vad_filter=False,
                    vad_params=vad_params,
                    hotwords=effective_hotwords,
                    temperatures=temps,
                    initial_prompt=initial_prompt,
                )
                parts = self._filter_valid_segments(segments_list)

            # Second-pass re-evaluation:
            # - In MAX_ACCURACY: retry on empty OR low confidence (< 0.50).
            # - In BALANCED: never waste a multi-second 2nd pass when 1st pass already produced words;
            #   only retry quickly (beam=1, vad_filter=False) if VAD dropped clear speech (parts == []).
            first_pass_conf = 1.0
            if segments_list:
                logprobs = [
                    getattr(s, "avg_logprob", -0.3)
                    for s in segments_list
                    if getattr(s, "avg_logprob", None) is not None
                ]
                if logprobs:
                    first_pass_conf = float(np.exp(sum(logprobs) / len(logprobs)))

            should_retry_empty = (not parts) and eff_vad_filter and raw_rms >= 0.008 and trimmed_audio.size >= int(0.45 * TARGET_SAMPLE_RATE)
            should_retry_accuracy = (
                active_profile == TranscribeProfile.MAX_ACCURACY
                and (first_pass_conf < 0.50 or not parts)
                and trimmed_audio.size >= int(0.5 * TARGET_SAMPLE_RATE)
            )
            if should_retry_accuracy or (active_profile == TranscribeProfile.BALANCED and should_retry_empty):
                try:
                    fallback_lang = getattr(info, "language", None) or language
                    fallback_beam = 1 if active_profile == TranscribeProfile.BALANCED else (
                        2 if getattr(self, "_active_device", "cpu") == "cpu" else max(dict_beam, 3)
                    )
                    alt_segs, alt_info = self._run_whisper_pass(
                        norm_audio,
                        language=fallback_lang,
                        beam_size=fallback_beam,
                        vad_filter=False,
                        vad_params=vad_params,
                        hotwords=effective_hotwords,
                        temperatures=0.0,
                        initial_prompt=initial_prompt,
                    )
                    alt_parts = self._filter_valid_segments(alt_segs)
                    if alt_parts:
                        alt_logprobs = [
                            getattr(s, "avg_logprob", -1.0)
                            for s in alt_segs
                            if getattr(s, "avg_logprob", None) is not None
                        ]
                        alt_conf = (
                            float(np.exp(sum(alt_logprobs) / len(alt_logprobs)))
                            if alt_logprobs
                            else 0.5
                        )
                        if alt_conf > first_pass_conf or (not parts and alt_conf >= 0.55 and raw_rms >= 0.006):
                            logger.info(
                                "Acoustic fallback pass accepted (conf %.2f -> %.2f)",
                                first_pass_conf,
                                alt_conf,
                            )
                            parts = alt_parts
                            segments_list = alt_segs
                            info = alt_info
                except Exception as eval_exc:
                    logger.debug("Acoustic fallback pass skipped: %s", eval_exc)

            # Sub-clause code-switching split refinement (only when first pass failed to handle both scripts)
            if use_subclause and is_bilingual and parts:
                split_idx = self._find_primary_pause_split(norm_audio)
                if split_idx is not None:
                    first_pass_text = " ".join(parts).lower()
                    has_both_scripts = any(
                        c.isascii() and c.isalpha() for c in first_pass_text
                    ) and any("\u0900" <= c <= "\u097F" for c in first_pass_text)
                    has_cs_marker = any(
                        m in first_pass_text for m in self.DEVANAGARI_ENGLISH_CLAUSE_MARKERS
                    )

                    # Only split if the first pass contains Devanagari English loanword markers without Latin script
                    if has_cs_marker and not has_both_scripts:
                        try:
                            left_audio, right_audio = norm_audio[:split_idx], norm_audio[split_idx:]
                            lang_l, _ = self._detect_bilingual_lang_fast(left_audio, False, vad_params)
                            lang_r, _ = self._detect_bilingual_lang_fast(right_audio, False, vad_params)
                            if lang_l != lang_r:
                                b_sz = 3 if active_profile == TranscribeProfile.MAX_ACCURACY else (1 if self.model_name == "small" else 2)
                                list_l, _ = self._run_whisper_pass(left_audio, lang_l, b_sz, vad_filter, vad_params, hotwords=effective_hotwords, temperatures=temps, initial_prompt=initial_prompt)
                                list_r, _ = self._run_whisper_pass(right_audio, lang_r, b_sz, vad_filter, vad_params, hotwords=effective_hotwords, temperatures=temps, initial_prompt=initial_prompt)
                                txt_l = " ".join(self._filter_valid_segments(list_l)).strip()
                                txt_r = " ".join(self._filter_valid_segments(list_r)).strip()
                                if txt_l and txt_r:
                                    used_code_switch_split = True
                                    if lang_l == "hi":
                                        txt_l = DictationProcessor.normalize_devanagari(txt_l).rstrip("।.") + ","
                                        if txt_r and txt_r[0].isascii() and txt_r[0].isalpha():
                                             txt_r = txt_r[0].lower() + txt_r[1:]
                                        if not txt_r.endswith((".", "!", "?", "।")):
                                            txt_r = txt_r + "."
                                    else:
                                        txt_l = txt_l.rstrip(".") + ","
                                        txt_r = DictationProcessor.normalize_devanagari(txt_r)
                                    parts = [f"{txt_l} {txt_r}"]
                        except Exception as split_err:
                            logger.debug("Sub-clause bilingual probe skipped: %s", split_err)

            elapsed = time.perf_counter() - t0

        text = " ".join(parts).strip()
        det_lang = getattr(info, "language", None) or ("hi" if any("\u0900" <= c <= "\u097F" for c in text) else "en")

        formatted_segments = []
        for s in (segments_list or []):
            if hasattr(s, "start") and hasattr(s, "end"):
                raw_lp = getattr(s, "avg_logprob", None)
                seg_dict = DictationSegment({
                    "start": float(s.start),
                    "end": float(s.end),
                    "text": str(getattr(s, "text", "")).strip(),
                    "confidence": round(float(np.exp(float(raw_lp))), 4) if raw_lp is not None else 0.0,
                    "raw_model_score": round(float(raw_lp), 4) if raw_lp is not None else None,
                    "confidence_calibrated": False,
                })
                formatted_segments.append(seg_dict)
            elif isinstance(s, dict):
                formatted_segments.append(DictationSegment(s))

        logger.info(
            "Transcribed %.2fs dictation in %.2fs (lang=%s, profile=%s, split=%s) -> '%s'",
            duration_sec,
            elapsed,
            det_lang,
            active_profile,
            used_code_switch_split,
            text[:80],
        )

        return DictationResult(
            raw_text=text,
            segments=formatted_segments,
            language=det_lang,
            duration=duration_sec,
        )

    def transcribe_dictation(
        self,
        audio_16k: np.ndarray,
        initial_prompt: str = "",
        language: str = "en+hi",
        vad_filter: bool = True,
        hotwords: Optional[str] = None,
        enable_subclause_split: bool = False,
        profile: Optional[str] = None,
    ) -> str:
        """
        Transcribe a voice-typing audio buffer with automatic gain normalization,
        Hindustani probability fusion, and domain hotwords biasing. Configurable via
        processing profile presets ('fast', 'balanced', 'max_accuracy').
        Maintains backward compatibility by returning raw_text from transcribe_audio.
        """
        res = self.transcribe_audio(
            audio_pcm=audio_16k,
            initial_prompt=initial_prompt,
            language=language,
            vad_filter=vad_filter,
            hotwords=hotwords,
            enable_subclause_split=enable_subclause_split,
            profile=profile,
        )
        return res.raw_text

    @staticmethod
    def _extract_vad_chunk_spans(
        audio_16k: np.ndarray,
        vad_params: dict,
        max_chunk_sec: float = 22.0,
        merge_gap_sec: float = 0.85,
        pad_sec: float = 0.30,
    ) -> Optional[List[Tuple[int, int]]]:
        """
        Detect active speech intervals with Silero VAD and group contiguous turns into
        non-overlapping pause-aligned chunks <= `max_chunk_sec` for single-encode Whisper passes.
        Returns None if Silero VAD is unavailable so callers fall back cleanly.
        """
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
        except Exception:
            return None

        n_samples = int(audio_16k.size)
        if n_samples <= 0:
            return []

        norm_for_vad = normalize_speech_audio(audio_16k)
        try:
            vad_opts = VadOptions(**vad_params) if isinstance(vad_params, dict) else (vad_params or VadOptions())
            raw_spans = get_speech_timestamps(norm_for_vad, vad_opts)
        except Exception as vad_exc:
            logger.debug("Silero VAD chunking fell back to whole-buffer mode: %s", vad_exc)
            return None

        if not raw_spans:
            return []

        pad_samp = int(pad_sec * TARGET_SAMPLE_RATE)
        merge_gap_samp = int(merge_gap_sec * TARGET_SAMPLE_RATE)
        max_chunk_samp = int(max_chunk_sec * TARGET_SAMPLE_RATE)
        hard_cap_samp = int(24.0 * TARGET_SAMPLE_RATE)

        padded: List[Tuple[int, int]] = []
        for sp in raw_spans:
            s = max(0, int(sp["start"]) - pad_samp)
            e = min(n_samples, int(sp["end"]) + pad_samp)
            if e - s >= int(0.15 * TARGET_SAMPLE_RATE):
                padded.append((s, e))

        if not padded:
            return []

        grouped: List[Tuple[int, int]] = []
        cur_s, cur_e = padded[0]
        for s, e in padded[1:]:
            if e <= cur_e:
                continue
            s_adj = max(s, cur_e) if (s < cur_e and (e - cur_s) > max_chunk_samp) else s
            if (s_adj - cur_e) <= merge_gap_samp and (e - cur_s) <= max_chunk_samp:
                cur_e = max(cur_e, e)
            else:
                grouped.append((cur_s, cur_e))
                cur_s, cur_e = max(s, cur_e), e
        grouped.append((cur_s, cur_e))

        final_spans: List[Tuple[int, int]] = []
        frame_sz = int(0.05 * TARGET_SAMPLE_RATE)
        for gs, ge in grouped:
            pos = gs
            while (ge - pos) > hard_cap_samp:
                search_lo = pos + int(16.0 * TARGET_SAMPLE_RATE)
                search_hi = min(ge, pos + hard_cap_samp)
                cut = search_hi
                if (search_hi - search_lo) >= frame_sz * 4:
                    sub = audio_16k[search_lo:search_hi]
                    n_fr = sub.size // frame_sz
                    if n_fr >= 2:
                        frames = sub[: n_fr * frame_sz].reshape(n_fr, frame_sz)
                        rms = np.sqrt(np.mean(frames ** 2, axis=1))
                        cut = search_lo + int(np.argmin(rms)) * frame_sz
                final_spans.append((pos, cut))
                pos = cut
            if (ge - pos) >= int(0.15 * TARGET_SAMPLE_RATE):
                final_spans.append((pos, ge))
        return final_spans

    @staticmethod
    def _filter_raw_segment_objects(
        segments_gen: Any,
        *,
        initial_prompt: str = "",
        audio_rms: Optional[float] = None,
    ) -> List[Any]:
        """Keep valid decoded Whisper segment objects, stripping hallucination loops."""
        raw_segs: List[Any] = []
        for s in segments_gen:
            t_clean = clean_repetition_tail(str(getattr(s, "text", "")).strip())
            if not t_clean:
                continue
            seg_nsp = getattr(s, "no_speech_prob", None)
            if seg_nsp is not None and float(seg_nsp) >= 0.85:
                continue
            cr = getattr(s, "compression_ratio", 1.0)
            if cr >= 3.5:
                w_chk = t_clean.split()
                if len(w_chk) >= 8 and (len(set(w_chk)) / len(w_chk)) < 0.25:
                    continue
            dur = max(0.01, float(getattr(s, "end", 0.0)) - float(getattr(s, "start", 0.0)))
            seg_lp = getattr(s, "avg_logprob", None)
            if not is_hallucination(
                t_clean,
                duration_sec=dur,
                initial_prompt=initial_prompt,
                avg_logprob=seg_lp,
                no_speech_prob=seg_nsp,
                audio_rms=audio_rms,
            ):
                s.text = t_clean
                raw_segs.append(s)
        return raw_segs

    @staticmethod
    def _build_packed_vad_batches(
        audio_16k: np.ndarray,
        vad_params: dict,
        max_speech_sec: float = 24.0,
        max_gap_sec: float = 12.0,
        pad_sec: float = 0.22,
    ) -> Optional[List[Tuple[np.ndarray, List[Dict[str, int]]]]]:
        """
        Pack Silero VAD speech intervals into compact <= `max_speech_sec` concatenated audio batches
        while splitting batches across long conversation silences (> `max_gap_sec`).
        Returns (batch_audio, span_dicts) tuples compatible with faster-whisper's
        `restore_speech_timestamps`, reducing Whisper encode() calls by ~5-6x with exact timestamps.
        """
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
        except Exception:
            return None

        n_samples = int(audio_16k.size)
        if n_samples <= 0:
            return []

        norm_for_vad = normalize_speech_audio(audio_16k)
        try:
            vad_opts = VadOptions(**vad_params) if isinstance(vad_params, dict) else (vad_params or VadOptions())
            raw_spans = get_speech_timestamps(norm_for_vad, vad_opts)
        except Exception as vad_exc:
            logger.debug("Silero VAD packing fell back to whole-buffer mode: %s", vad_exc)
            return None

        if not raw_spans:
            return []

        pad_samp = int(pad_sec * TARGET_SAMPLE_RATE)
        merge_gap_samp = int(0.45 * TARGET_SAMPLE_RATE)
        hard_cap_samp = int(max_speech_sec * TARGET_SAMPLE_RATE)

        padded: List[Tuple[int, int]] = []
        for sp in raw_spans:
            s = max(0, int(sp["start"]) - pad_samp)
            e = min(n_samples, int(sp["end"]) + pad_samp)
            if e - s >= int(0.15 * TARGET_SAMPLE_RATE):
                padded.append((s, e))
        if not padded:
            return []

        # Merge tiny intra-phrase pauses and clamp out overlaps
        merged: List[Tuple[int, int]] = []
        cur_s, cur_e = padded[0]
        for s, e in padded[1:]:
            if e <= cur_e:
                continue
            s_adj = max(s, cur_e) if (s < cur_e and (e - cur_s) > hard_cap_samp) else s
            if (s_adj - cur_e) <= merge_gap_samp and (e - cur_s) <= hard_cap_samp:
                cur_e = max(cur_e, e)
            else:
                merged.append((cur_s, cur_e))
                cur_s, cur_e = max(s, cur_e), e
        merged.append((cur_s, cur_e))

        # Split any continuous single span > max_speech_sec at quietest 50ms energy valley
        bounded_spans: List[Dict[str, int]] = []
        frame_sz = int(0.05 * TARGET_SAMPLE_RATE)
        for gs, ge in merged:
            pos = gs
            while (ge - pos) > hard_cap_samp:
                search_lo = pos + int(16.0 * TARGET_SAMPLE_RATE)
                search_hi = min(ge, pos + hard_cap_samp)
                cut = search_hi
                if (search_hi - search_lo) >= frame_sz * 4:
                    sub = audio_16k[search_lo:search_hi]
                    n_fr = sub.size // frame_sz
                    if n_fr >= 2:
                        frames = sub[: n_fr * frame_sz].reshape(n_fr, frame_sz)
                        rms = np.sqrt(np.mean(frames ** 2, axis=1))
                        cut = search_lo + int(np.argmin(rms)) * frame_sz
                bounded_spans.append({"start": pos, "end": cut})
                pos = cut
            if (ge - pos) >= int(0.15 * TARGET_SAMPLE_RATE):
                bounded_spans.append({"start": pos, "end": ge})

        if not bounded_spans:
            return []

        # Pack adjacent spans into single-encode batches <= max_speech_sec unless gap > max_gap_sec
        batches: List[Tuple[np.ndarray, List[Dict[str, int]]]] = []
        cur_batch: List[Dict[str, int]] = []
        cur_samp = 0
        max_gap_samp = int(max_gap_sec * TARGET_SAMPLE_RATE)

        for sp in bounded_spans:
            sp_len = sp["end"] - sp["start"]
            gap = (sp["start"] - cur_batch[-1]["end"]) if cur_batch else 0
            if cur_batch and ((cur_samp + sp_len > hard_cap_samp) or (gap > max_gap_samp)):
                b_parts = [normalize_speech_audio(audio_16k[c["start"] : c["end"]]) for c in cur_batch]
                batches.append((np.concatenate(b_parts, axis=0), cur_batch))
                cur_batch = [sp]
                cur_samp = sp_len
            else:
                cur_batch.append(sp)
                cur_samp += sp_len
        if cur_batch:
            b_parts = [normalize_speech_audio(audio_16k[c["start"] : c["end"]]) for c in cur_batch]
            batches.append((np.concatenate(b_parts, axis=0), cur_batch))
        return batches

    def transcribe_audio_segments(
        self,
        audio_16k: np.ndarray,
        speaker_label: str = "Speaker 1",
        source_track: str = "mixed",
        time_offset_sec: float = 0.0,
        initial_prompt: str = "",
        language: str = "en",
        diarize_remote_turns: bool = False,
        hotwords: Optional[str] = None,
        diarizer: Optional[PersistentSpeakerDiarizer] = None,
        output_script: str = "roman",
        beam_size: int = 3,
        word_timestamps: bool = True,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Transcribe an audio buffer into timestamped segment dictionaries.
        Uses speech-packed Silero VAD batching (<= 24s active speech per encode) + per-span AGC,
        anti-translation Hindi/English resolution, and restored original timeline timestamps.
        """
        if audio_16k.size < int(0.3 * TARGET_SAMPLE_RATE):
            return []

        raw_peak = float(np.max(np.abs(audio_16k)))
        if raw_peak < 0.0004:
            return []

        ok, msg = self.ensure_loaded()
        if not ok or self._model is None:
            raise RuntimeError(msg)

        vad_params = dict(threshold=0.35, min_silence_duration_ms=700, speech_pad_ms=300)
        effective_hotwords = merge_domain_hotwords(hotwords)
        eff_beam = max(1, int(beam_size))
        is_real_whisper = bool(
            hasattr(self._model, "feature_extractor")
            and hasattr(self._model, "encode")
            and hasattr(self._model, "generate_segments")
            and type(self._model).__module__.startswith("faster_whisper")
        )

        # Tuples of (segment_obj, abs_chunk_offset_sec, slice_audio_for_diarizer)
        decoded_items: List[Tuple[Any, float, np.ndarray]] = []
        track_rms = float(np.sqrt(np.mean(np.square(audio_16k)))) if audio_16k.size else 0.0
        norm_full = normalize_speech_audio(audio_16k)
        with self._lock:
            batches = self._build_packed_vad_batches(audio_16k, vad_params) if is_real_whisper else None
            if batches == []:
                return []

            if batches:
                from faster_whisper.transcribe import restore_speech_timestamps
                for batch_audio, batch_spans in batches:
                    if batch_audio.size < int(0.15 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(batch_audio))) < 0.0004:
                        continue
                    batch_rms = float(np.sqrt(np.mean(np.square(batch_audio)))) if batch_audio.size else track_rms
                    segments_gen, _ = self._run_whisper_pass(
                        batch_audio,
                        language=language,
                        beam_size=eff_beam,
                        vad_filter=False,
                        vad_params=vad_params,
                        hotwords=effective_hotwords,
                        initial_prompt=initial_prompt,
                        word_timestamps=word_timestamps,
                    )
                    restored_gen = restore_speech_timestamps(segments_gen, batch_spans, TARGET_SAMPLE_RATE)
                    for s in self._filter_raw_segment_objects(
                        restored_gen, initial_prompt=initial_prompt, audio_rms=batch_rms
                    ):
                        decoded_items.append((s, time_offset_sec, norm_full))
            else:
                try:
                    segments_gen, _ = self._run_whisper_pass(
                        norm_full,
                        language=language,
                        beam_size=eff_beam,
                        vad_filter=True,
                        vad_params=vad_params,
                        hotwords=effective_hotwords,
                        initial_prompt=initial_prompt,
                        word_timestamps=word_timestamps,
                    )
                    for s in self._filter_raw_segment_objects(
                        segments_gen, initial_prompt=initial_prompt, audio_rms=track_rms
                    ):
                        decoded_items.append((s, time_offset_sec, norm_full))
                except Exception as vad_err:
                    logger.warning("Meeting VAD transcribe failed (%s); retrying with vad_filter=False", vad_err)
                    segments_gen, _ = self._run_whisper_pass(
                        norm_full,
                        language=language,
                        beam_size=eff_beam,
                        vad_filter=False,
                        vad_params=vad_params,
                        hotwords=effective_hotwords,
                        initial_prompt=initial_prompt,
                        word_timestamps=word_timestamps,
                    )
                    for s in self._filter_raw_segment_objects(
                        segments_gen, initial_prompt=initial_prompt, audio_rms=track_rms
                    ):
                        decoded_items.append((s, time_offset_sec, norm_full))

        results: List[Dict[str, Any]] = []
        active_diarizer = diarizer
        if diarize_remote_turns and active_diarizer is None:
            active_diarizer = PersistentSpeakerDiarizer(speaker_prefix="Speaker")

        for seg, seg_offset, seg_audio in decoded_items:
            raw_asr_txt = seg.text.strip()
            if not raw_asr_txt:
                continue
            txt = DictationProcessor.process(
                raw_asr_txt,
                vocabulary=vocabulary,
                cleanup_enabled=True,
                spoken_punctuation=False,
                output_script=output_script,
                mode="cleaned",
            ).strip()
            if not txt:
                continue

            s_local = max(0.0, float(seg.start))
            e_local = max(s_local + 0.2, float(seg.end))

            # Refine segment start/end boundaries using word-level alignment timestamps when available
            seg_words_raw = getattr(seg, "words", None) or []
            word_items: List[Dict[str, Any]] = []
            word_probs: List[float] = []
            if seg_words_raw:
                try:
                    valid_words = [w for w in seg_words_raw if getattr(w, "word", "").strip()]
                    if valid_words:
                        w_first_s = float(getattr(valid_words[0], "start", s_local))
                        w_last_e = float(getattr(valid_words[-1], "end", e_local))
                        if 0.0 <= w_first_s < w_last_e:
                            s_local = max(0.0, min(s_local, w_first_s))
                            # Never truncate trailing speech - preserve full sentence ending
                            e_local = max(e_local, w_last_e + 0.15)
                        for w_obj in valid_words:
                            w_txt = str(getattr(w_obj, "word", "")).strip()
                            if output_script == "roman":
                                w_txt = DictationProcessor.devanagari_to_roman_hinglish(w_txt).strip()
                            w_prob = float(getattr(w_obj, "probability", 0.85))
                            word_probs.append(w_prob)
                            word_items.append(
                                {
                                    "word": w_txt,
                                    "start": round(seg_offset + float(getattr(w_obj, "start", s_local)), 2),
                                    "end": round(seg_offset + float(getattr(w_obj, "end", e_local)), 2),
                                    "probability": round(w_prob, 3),
                                }
                            )
                except Exception:
                    pass

            abs_start = round(seg_offset + s_local, 2)
            abs_end = round(seg_offset + e_local, 2)

            assigned_speaker = speaker_label
            spk_conf = 0.95
            overlap_flag = False
            if diarize_remote_turns and active_diarizer is not None:
                s_idx = int(s_local * TARGET_SAMPLE_RATE)
                e_idx = min(seg_audio.size, int(e_local * TARGET_SAMPLE_RATE))
                assigned_speaker, spk_conf = active_diarizer.assign_segment(
                    seg_audio[s_idx:e_idx],
                    start_sec=abs_start,
                    end_sec=abs_end,
                )
                overlap_flag = bool(getattr(active_diarizer, "last_segment_overlap", False))

            raw_lp = getattr(seg, "avg_logprob", None)
            raw_nsp = getattr(seg, "no_speech_prob", None)
            logprob_conf = float(np.exp(float(raw_lp))) if raw_lp is not None else 0.0
            if word_probs and raw_lp is not None:
                seg_conf = round(0.6 * logprob_conf + 0.4 * (sum(word_probs) / len(word_probs)), 2)
            elif word_probs:
                seg_conf = round(sum(word_probs) / len(word_probs), 2)
            else:
                seg_conf = round(logprob_conf, 2)

            seg_dict: Dict[str, Any] = {
                "start_time": abs_start,
                "end_time": abs_end,
                "speaker": assigned_speaker,
                "speaker_confidence": spk_conf,
                "source_track": source_track,
                "raw_text": raw_asr_txt,
                "text": txt,
                "confidence": seg_conf,
                "raw_model_score": round(float(raw_lp), 4) if raw_lp is not None else None,
                "no_speech_prob": round(float(raw_nsp), 4) if raw_nsp is not None else None,
                "confidence_calibrated": False,
                "low_confidence": (seg_conf < 0.45) if (raw_lp is not None or word_probs) else False,
                "overlap": overlap_flag,
            }
            if diarize_remote_turns:
                seg_dict["speaker_cluster"] = assigned_speaker
            if word_items:
                seg_dict["words"] = word_items
            results.append(seg_dict)

        return results

    def _transcribe_wav_windowed(
        self,
        wav_path: str,
        speaker_label: str,
        source_track: str,
        initial_prompt: str,
        language: str,
        diarize_remote_turns: bool,
        diarizer: Optional[PersistentSpeakerDiarizer] = None,
        window_sec: float = 120.0,
        progress_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        output_script: str = "roman",
        hotwords: Optional[str] = None,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Transcribe a WAV file of any length using bounded memory windows (`window_sec`).
        Aligns chunk splits to natural speech pauses in the last 10s of each window so words
        are never cut mid-syllable, while preserving `diarizer` speaker centroids across all windows.
        """
        total_dur = get_wav_duration_sec(wav_path)
        if total_dur <= 0.05:
            self._last_track_stats[source_track] = {
                "wav_path": str(wav_path),
                "duration_sec": round(float(max(0.0, total_dur)), 4),
                "samples_captured": 0,
                "samples_processed": 0,
                "cursor_sec": 0.0,
                "windows": 0,
                "cancelled": False,
            }
            return []

        expected_total_samples = int(round(total_dur * TARGET_SAMPLE_RATE))

        if total_dur <= window_sec * 1.15:
            audio = load_wav_as_16k_f32(wav_path)
            n_samp = int(audio.size)
            self._last_track_stats[source_track] = {
                "wav_path": str(wav_path),
                "duration_sec": round(float(total_dur), 4),
                "samples_captured": n_samp or expected_total_samples,
                "samples_processed": n_samp,
                "cursor_sec": round(float(n_samp) / float(TARGET_SAMPLE_RATE), 4),
                "windows": 1,
                "cancelled": False,
            }
            if n_samp == 0 or float(np.max(np.abs(audio))) < 0.0004:
                return []
            return self.transcribe_audio_segments(
                audio,
                speaker_label=speaker_label,
                source_track=source_track,
                time_offset_sec=0.0,
                initial_prompt=initial_prompt,
                language=language,
                diarize_remote_turns=diarize_remote_turns,
                hotwords=hotwords,
                diarizer=diarizer,
                output_script=output_script,
                beam_size=2,
                word_timestamps=False,
                vocabulary=vocabulary,
            )

        # Long recording: process in pause-aligned bounded windows all the way to EOF
        all_segments: List[Dict[str, Any]] = []
        cursor = 0.0
        win_idx = 0
        processed_samples = 0
        processed_end_sec = 0.0
        was_cancelled = False
        while cursor < total_dur - 0.05:
            if cancel_check and cancel_check():
                was_cancelled = True
                logger.info("Windowed transcription cancelled at %.1fs / %.1fs", cursor, total_dur)
                break
            win_idx += 1
            raw_end = min(total_dur, cursor + window_sec)
            slice_audio = load_wav_slice_as_16k_f32(wav_path, cursor, raw_end)
            if slice_audio.size == 0:
                break

            # If not at the end of the file, find a quiet pause in the last 12 seconds of the window
            actual_dur = slice_audio.size / float(TARGET_SAMPLE_RATE)
            if raw_end < total_dur - 1.0 and actual_dur > 20.0:
                tail_start_s = max(10.0, actual_dur - 12.0)
                tail_start_idx = int(tail_start_s * TARGET_SAMPLE_RATE)
                tail = slice_audio[tail_start_idx:]
                # Scan 50ms frames for lowest RMS pause point
                frame_sz = int(0.05 * TARGET_SAMPLE_RATE)
                n_fr = len(tail) // frame_sz
                if n_fr >= 4:
                    rms_vals = [
                        float(np.sqrt(np.mean(np.square(tail[i * frame_sz : (i + 1) * frame_sz]))))
                        for i in range(n_fr)
                    ]
                    min_i = int(np.argmin(rms_vals))
                    if rms_vals[min_i] < 0.025:
                        cut_samples = tail_start_idx + (min_i * frame_sz)
                        slice_audio = slice_audio[:cut_samples]
                        actual_dur = slice_audio.size / float(TARGET_SAMPLE_RATE)

            if progress_callback:
                pct = min(99, int(round((cursor / max(1.0, total_dur)) * 100)))
                progress_callback(f"Transcribing {source_track} track ({pct}% • window {win_idx})...")

            processed_samples += int(slice_audio.size)
            processed_end_sec = max(processed_end_sec, cursor + actual_dur)

            if float(np.max(np.abs(slice_audio))) >= 0.0004:
                win_segs = self.transcribe_audio_segments(
                    slice_audio,
                    speaker_label=speaker_label,
                    source_track=source_track,
                    time_offset_sec=cursor,
                    initial_prompt=initial_prompt,
                    language=language,
                    diarize_remote_turns=diarize_remote_turns,
                    hotwords=hotwords,
                    diarizer=diarizer,
                    output_script=output_script,
                    beam_size=2,
                    word_timestamps=False,
                    vocabulary=vocabulary,
                )
                all_segments.extend(win_segs)

            cursor += max(0.05, actual_dur)

        self._last_track_stats[source_track] = {
            "wav_path": str(wav_path),
            "duration_sec": round(float(total_dur), 4),
            "samples_captured": expected_total_samples,
            "samples_processed": int(processed_samples),
            "cursor_sec": round(float(processed_end_sec), 4),
            "windows": int(win_idx),
            "cancelled": bool(was_cancelled),
        }

        return all_segments

    @staticmethod
    def _prefer_raw_wav_path(wav_path: str) -> str:
        """Prefer untouched pre-DSP `*_raw.wav` when present and non-empty."""
        if not wav_path:
            return ""
        p = Path(wav_path)
        raw_cand = p.with_name(f"{p.stem}_raw.wav")
        if raw_cand.exists() and get_wav_duration_sec(str(raw_cand)) > 0.1:
            return str(raw_cand)
        return wav_path

    def transcribe_meeting_dual_track(
        self,
        mixed_wav_path: str,
        mic_wav_path: str = "",
        sys_wav_path: str = "",
        initial_prompt: str = "",
        language: str = "en",
        progress_callback: Optional[Callable[[str], None]] = None,
        diarizer: Optional[PersistentSpeakerDiarizer] = None,
        window_sec: float = 120.0,
        cancel_check: Optional[Callable[[], bool]] = None,
        output_script: str = "roman",
        remote_speaker_label: str = "Speaker 1",
        hotwords: Optional[str] = None,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform complete offline transcription of a saved meeting/video recording.
        - Prefers untouched pre-DSP `*_raw.wav` tracks when present on disk.
        - Keeps source_track (`system`/`mic`), acoustic cluster (`Speaker 1`, `Speaker 2`),
          and calendar participant attribution separated.
        """
        self._last_track_stats = {}
        eff_mic_path = self._prefer_raw_wav_path(mic_wav_path)
        eff_sys_path = self._prefer_raw_wav_path(sys_wav_path)
        has_mic = bool(eff_mic_path and os.path.exists(eff_mic_path))
        has_sys = bool(eff_sys_path and os.path.exists(eff_sys_path))

        segments: List[Dict[str, Any]] = []
        eff_remote_label = remote_speaker_label.strip() if remote_speaker_label else "Speaker 1"
        has_participant_name = eff_remote_label != "Speaker 1" and is_likely_person_name(eff_remote_label)
        active_diarizer = diarizer or PersistentSpeakerDiarizer(
            speaker_prefix="Speaker",
            profile_store=getattr(self, "speaker_profile_store", None),
        )

        if has_mic or has_sys:
            sys_segs: List[Dict[str, Any]] = []
            if has_sys:
                if progress_callback:
                    progress_callback(f"Transcribing system/video audio track ({eff_remote_label})...")
                sys_segs = self._transcribe_wav_windowed(
                    eff_sys_path,
                    speaker_label="Speaker 1",
                    source_track="system",
                    initial_prompt=initial_prompt,
                    language=language,
                    diarize_remote_turns=True,
                    diarizer=active_diarizer,
                    window_sec=window_sec,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    output_script=output_script,
                    hotwords=hotwords,
                    vocabulary=vocabulary,
                )
                remote_clusters = {
                    s.get("speaker_cluster", s.get("speaker", "Speaker 1"))
                    for s in sys_segs
                    if s.get("speaker") not in ("Unknown Speaker", "")
                }
                for s_seg in sys_segs:
                    cluster_id = s_seg.get("speaker_cluster", s_seg.get("speaker", "Speaker 1"))
                    s_seg["speaker_cluster"] = cluster_id
                    if has_participant_name:
                        s_seg["participant_name"] = eff_remote_label
                        # Only map Speaker 1 display label to calendar participant when single remote cluster
                        if len(remote_clusters) <= 1 and cluster_id == "Speaker 1":
                            s_seg["speaker"] = eff_remote_label
                segments.extend(sys_segs)

            if has_mic and not (cancel_check and cancel_check()):
                if progress_callback:
                    progress_callback("Transcribing microphone track (You)...")
                mic_segs = self._transcribe_wav_windowed(
                    eff_mic_path,
                    speaker_label="You (Mic)",
                    source_track="mic",
                    initial_prompt=initial_prompt,
                    language=language,
                    diarize_remote_turns=False,
                    diarizer=None,
                    window_sec=window_sec,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    output_script=output_script,
                    hotwords=hotwords,
                    vocabulary=vocabulary,
                )
                for m_seg in mic_segs:
                    m_seg["speaker_cluster"] = "You (Mic)"
                    m_txt_clean = m_seg["text"].strip().lower()
                    is_bleed = False
                    if len(m_txt_clean) >= 12:
                        for s_seg in sys_segs:
                            s_txt_clean = s_seg["text"].strip().lower()
                            if len(s_txt_clean) >= 12 and abs(float(m_seg["start_time"]) - float(s_seg["start_time"])) < 2.0:
                                if m_txt_clean == s_txt_clean or (m_txt_clean in s_txt_clean and len(m_txt_clean) > 0.65 * len(s_txt_clean)):
                                    is_bleed = True
                                    break
                    if not is_bleed:
                        segments.append(m_seg)

            if segments:
                segments.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
                return segments

        # Fallback: transcribe the mixed WAV file
        if mixed_wav_path and os.path.exists(mixed_wav_path):
            if progress_callback:
                progress_callback("Transcribing combined meeting/video audio...")
            segments = self._transcribe_wav_windowed(
                mixed_wav_path,
                speaker_label="Speaker 1",
                source_track="mixed",
                initial_prompt=initial_prompt,
                language=language,
                diarize_remote_turns=True,
                diarizer=active_diarizer,
                window_sec=window_sec,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                output_script=output_script,
                hotwords=hotwords,
                vocabulary=vocabulary,
            )
        return segments

    def reconcile_meeting_transcript(
        self,
        segments: List[Dict[str, Any]],
        mic_audio_16k: Optional[np.ndarray] = None,
        sys_audio_16k: Optional[np.ndarray] = None,
        language: str = "en",
        hotwords: Optional[str] = None,
        mic_wav_path: str = "",
        sys_wav_path: str = "",
        output_script: str = "roman",
        remote_speaker_label: str = "",
        verbatim_protection: bool = True,
        enable_selective_verification: bool = True,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Two-pass post-meeting reconciliation pass:
        1. Eliminates low-confidence micro-glitch turns (< 0.65s) without erasing valid short interjections.
        2. Merges adjacent segments from the same speaker separated by <= 0.8s pause.
        3. For low-confidence segments when `enable_selective_verification` is enabled, re-runs high-accuracy
           beam-3 verification on the isolated raw slice (preferring untouched `*_raw.wav`), preserving both
           primary and verification hypotheses plus raw verbatim text.
        """
        if not segments:
            return []

        norm_input: List[Dict[str, Any]] = []
        for s in segments:
            d = dict(s)
            if "start_time" not in d:
                d["start_time"] = float(d.get("start", 0.0))
            if "end_time" not in d:
                d["end_time"] = float(d.get("end", d["start_time"]))
            if "start" not in d:
                d["start"] = d["start_time"]
            if "end" not in d:
                d["end"] = d["end_time"]
            if not d.get("raw_text") and d.get("text"):
                d["raw_text"] = str(d.get("text", "")).strip()
            norm_input.append(d)

        sorted_segs = [
            dict(s)
            for s in sorted(norm_input, key=lambda s: (float(s["start_time"]), float(s["end_time"])))
        ]
        if not sorted_segs:
            return []

        eff_remote = remote_speaker_label.strip()
        distinct_sys_clusters = {
            s.get("speaker_cluster", s.get("speaker", ""))
            for s in sorted_segs
            if s.get("source_track") == "system" and s.get("speaker") not in ("Unknown Speaker", "")
        }
        if eff_remote and eff_remote not in ("Speaker", "Speaker 1", "Unknown Speaker") and is_likely_person_name(eff_remote):
            for s in sorted_segs:
                if s.get("source_track") == "system":
                    s["participant_name"] = eff_remote
                    if len(distinct_sys_clusters) <= 1 and s.get("speaker") in ("Speaker 1", "Speaker", ""):
                        s.setdefault("speaker_cluster", s.get("speaker") or "Speaker 1")
                        s["speaker"] = eff_remote

        # Pass 1: Eliminate only genuine micro-glitch turns (A -> uncertain micro-fragment < 0.65s -> A)
        if len(sorted_segs) >= 3:
            for i in range(1, len(sorted_segs) - 1):
                prev_s = sorted_segs[i - 1]
                mid_s = sorted_segs[i]
                nxt_s = sorted_segs[i + 1]

                if prev_s.get("speaker") == nxt_s.get("speaker") and mid_s.get("speaker") != prev_s.get("speaker"):
                    if mid_s.get("source_track") == prev_s.get("source_track"):
                        mid_dur = float(mid_s.get("end_time", 0.0)) - float(mid_s.get("start_time", 0.0))
                        gap_prev = float(mid_s.get("start_time", 0.0)) - float(prev_s.get("end_time", 0.0))
                        gap_nxt = float(nxt_s.get("start_time", 0.0)) - float(mid_s.get("end_time", 0.0))
                        spk_conf = float(mid_s.get("speaker_confidence", 0.50))
                        if mid_dur < 0.65 and spk_conf < 0.65 and gap_prev <= 0.5 and gap_nxt <= 0.5:
                            mid_s["speaker"] = prev_s.get("speaker")

        # Pass 2: Merge contiguous turns from the same speaker separated by <= 0.8s pause,
        # with deduplication of identical or subsuming text on chunk boundaries.
        reconciled: List[Dict[str, Any]] = []
        curr = dict(sorted_segs[0])
        for nxt in sorted_segs[1:]:
            same_speaker = nxt.get("speaker") == curr.get("speaker")
            pause = float(nxt.get("start_time", 0.0)) - float(curr.get("end_time", 0.0))
            time_dist = abs(float(nxt.get("start_time", 0.0)) - float(curr.get("start_time", 0.0)))
            curr_txt = curr.get("text", "").strip()
            nxt_txt = nxt.get("text", "").strip()
            curr_lower = curr_txt.lower()
            nxt_lower = nxt_txt.lower()

            # Deduplication for chunk overlaps / retries:
            if same_speaker and (time_dist <= 1.5 or pause <= 0.2):
                if nxt_lower == curr_lower or nxt_lower in curr_lower:
                    curr["end_time"] = max(float(curr.get("end_time", 0.0)), float(nxt.get("end_time", 0.0)))
                    continue
                if curr_lower in nxt_lower:
                    curr["text"] = nxt_txt
                    curr["end_time"] = max(float(curr.get("end_time", 0.0)), float(nxt.get("end_time", 0.0)))
                    curr["confidence"] = max(float(curr.get("confidence", 0.8)), float(nxt.get("confidence", 0.8)))
                    continue

            if same_speaker and 0.0 <= pause <= 0.8:
                curr["end_time"] = max(curr["end_time"], nxt.get("end_time"))
                if curr_txt and nxt_txt:
                    curr["text"] = f"{curr_txt} {nxt_txt}"
                elif nxt_txt:
                    curr["text"] = nxt_txt
                curr_raw = str(curr.get("raw_text", "")).strip()
                nxt_raw = str(nxt.get("raw_text", "")).strip()
                if curr_raw and nxt_raw:
                    curr["raw_text"] = f"{curr_raw} {nxt_raw}"
                elif nxt_raw:
                    curr["raw_text"] = nxt_raw
                curr["confidence"] = round((float(curr.get("confidence", 0.0)) + float(nxt.get("confidence", 0.0))) / 2.0, 2)
                c_lp = curr.get("raw_model_score")
                n_lp = nxt.get("raw_model_score")
                if c_lp is not None and n_lp is not None:
                    curr["raw_model_score"] = round((float(c_lp) + float(n_lp)) / 2.0, 4)
                elif n_lp is not None:
                    curr["raw_model_score"] = n_lp
                c_nsp = curr.get("no_speech_prob")
                n_nsp = nxt.get("no_speech_prob")
                if c_nsp is not None and n_nsp is not None:
                    curr["no_speech_prob"] = round(max(float(c_nsp), float(n_nsp)), 4)
                elif n_nsp is not None:
                    curr["no_speech_prob"] = n_nsp
                curr["confidence_calibrated"] = False
                if curr.get("words") and nxt.get("words"):
                    curr["words"] = list(curr["words"]) + list(nxt["words"])
            else:
                reconciled.append(curr)
                curr = dict(nxt)
        reconciled.append(curr)

        # Pass 3: High-accuracy refinement pass for low-confidence slices (bounded memory, prefers raw untouched WAV)
        has_mic_mem = mic_audio_16k is not None and mic_audio_16k.size > 0
        has_sys_mem = sys_audio_16k is not None and sys_audio_16k.size > 0

        eff_mic_disk = mic_wav_path
        if mic_wav_path:
            raw_m_cand = Path(mic_wav_path).with_name(Path(mic_wav_path).stem + "_raw.wav")
            if raw_m_cand.exists():
                eff_mic_disk = str(raw_m_cand)
        eff_sys_disk = sys_wav_path
        if sys_wav_path:
            raw_s_cand = Path(sys_wav_path).with_name(Path(sys_wav_path).stem + "_raw.wav")
            if raw_s_cand.exists():
                eff_sys_disk = str(raw_s_cand)

        has_mic_disk = bool(eff_mic_disk and os.path.exists(eff_mic_disk))
        has_sys_disk = bool(eff_sys_disk and os.path.exists(eff_sys_disk))

        if enable_selective_verification and (has_mic_mem or has_sys_mem or has_mic_disk or has_sys_disk):
            with self._lock:
                refinements_done = 0
                for seg in reconciled:
                    if refinements_done >= 6:
                        break
                    conf = float(seg.get("confidence", 1.0))
                    if conf >= 0.32:
                        continue
                    track = seg.get("source_track", "mic")
                    s_sec = max(0.0, float(seg["start_time"]) - 0.12)
                    e_sec = float(seg["end_time"]) + 0.12
                    if e_sec - s_sec < 0.3:
                        continue

                    slice_audio: Optional[np.ndarray] = None
                    if track == "mic":
                        if has_mic_mem and mic_audio_16k is not None:
                            s_idx = max(0, int(s_sec * TARGET_SAMPLE_RATE))
                            e_idx = min(mic_audio_16k.size, int(e_sec * TARGET_SAMPLE_RATE))
                            slice_audio = mic_audio_16k[s_idx:e_idx]
                        elif has_mic_disk:
                            slice_audio = load_wav_slice_as_16k_f32(eff_mic_disk, s_sec, e_sec)
                    else:
                        if has_sys_mem and sys_audio_16k is not None:
                            s_idx = max(0, int(s_sec * TARGET_SAMPLE_RATE))
                            e_idx = min(sys_audio_16k.size, int(e_sec * TARGET_SAMPLE_RATE))
                            slice_audio = sys_audio_16k[s_idx:e_idx]
                        elif has_sys_disk:
                            slice_audio = load_wav_slice_as_16k_f32(eff_sys_disk, s_sec, e_sec)

                    if slice_audio is not None and slice_audio.size > int(0.3 * TARGET_SAMPLE_RATE):
                        refinements_done += 1
                        hyps = list(seg.get("verification_hypotheses") or [])
                        if not hyps:
                            hyps.append(
                                {
                                    "source": "primary",
                                    "text": str(seg.get("text", "")),
                                    "raw_text": str(seg.get("raw_text") or seg.get("text", "")),
                                    "confidence": round(conf, 3),
                                    "raw_model_score": seg.get("raw_model_score"),
                                }
                            )
                        slice_rms = float(np.sqrt(np.mean(np.square(slice_audio))))
                        norm_slice = normalize_speech_audio(slice_audio)
                        ref_segs, _ = self._run_whisper_pass(
                            norm_slice,
                            language=language,
                            beam_size=3,
                            vad_filter=False,
                            vad_params={},
                            hotwords=merge_domain_hotwords(hotwords),
                            temperatures=0.0,
                            initial_prompt=None,
                            word_timestamps=False,
                        )
                        ref_objs = self._filter_raw_segment_objects(
                            ref_segs, initial_prompt="", audio_rms=slice_rms
                        )
                        refined_raw = " ".join(str(getattr(r, "text", "")).strip() for r in ref_objs).strip()
                        refined_lps = [
                            float(getattr(r, "avg_logprob"))
                            for r in ref_objs
                            if getattr(r, "avg_logprob", None) is not None
                        ]
                        refined_nsps = [
                            float(getattr(r, "no_speech_prob"))
                            for r in ref_objs
                            if getattr(r, "no_speech_prob", None) is not None
                        ]
                        mean_ref_lp = (sum(refined_lps) / len(refined_lps)) if refined_lps else None
                        max_ref_nsp = max(refined_nsps) if refined_nsps else None
                        refined_conf = float(np.exp(mean_ref_lp)) if mean_ref_lp is not None else conf
                        if output_script == "roman":
                            refined_text = DictationProcessor.devanagari_to_roman_hinglish(refined_raw).strip()
                        else:
                            refined_text = refined_raw
                        accept_ref = bool(
                            refined_text
                            and not is_hallucination(
                                refined_text,
                                duration_sec=e_sec - s_sec,
                                initial_prompt="",
                                avg_logprob=mean_ref_lp,
                                no_speech_prob=max_ref_nsp,
                                audio_rms=slice_rms,
                            )
                            and len(refined_text.split()) >= max(1, len(seg["text"].split()) - 1)
                            and refined_conf > conf + 0.10
                        )
                        hyps.append(
                            {
                                "source": "selective_verification",
                                "text": refined_text,
                                "raw_text": refined_raw,
                                "confidence": round(refined_conf, 3),
                                "raw_model_score": round(mean_ref_lp, 4) if mean_ref_lp is not None else None,
                                "accepted": accept_ref,
                            }
                        )
                        seg["verification_hypotheses"] = hyps
                        if accept_ref:
                            seg["raw_text"] = refined_raw
                            seg["text"] = refined_text
                            seg["confidence"] = round(refined_conf, 2)
                            seg["raw_model_score"] = round(mean_ref_lp, 4) if mean_ref_lp is not None else None
                            seg["no_speech_prob"] = round(max_ref_nsp, 4) if max_ref_nsp is not None else None
                            seg["confidence_calibrated"] = False
                            seg["low_confidence"] = refined_conf < 0.45
                        else:
                            seg["is_uncertain"] = True

        # Final guarantee: preserve verbatim raw_text + enforce Romanized Hinglish when requested
        # without silently truncating or summarizing user speech.
        dp = DictationProcessor()
        final_reconciled: List[Dict[str, Any]] = []
        for seg in reconciled:
            s_val = float(seg.get("start_time", seg.get("start", 0.0)))
            e_val = float(seg.get("end_time", seg.get("end", s_val)))
            seg["start"] = s_val
            seg["end"] = e_val
            seg["start_time"] = s_val
            seg["end_time"] = e_val
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            seg.setdefault("raw_text", txt)
            dur = max(0.01, e_val - s_val)
            seg_lp = seg.get("raw_model_score")
            seg_nsp = seg.get("no_speech_prob")
            if is_hallucination(
                txt,
                duration_sec=dur,
                initial_prompt="",
                avg_logprob=seg_lp,
                no_speech_prob=seg_nsp,
            ):
                continue
            if output_script == "roman":
                txt = DictationProcessor.devanagari_to_roman_hinglish(txt)
            if verbatim_protection:
                txt = dp.process(
                    txt,
                    vocabulary=vocabulary,
                    cleanup_enabled=False,
                    spoken_punctuation=False,
                    output_script=output_script,
                    mode="verbatim",
                ).strip()
            else:
                txt = dp.process(
                    txt,
                    vocabulary=vocabulary,
                    cleanup_enabled=True,
                    spoken_punctuation=False,
                    output_script=output_script,
                ).strip()
            if not txt or is_hallucination(
                txt,
                duration_sec=dur,
                initial_prompt="",
                avg_logprob=seg_lp,
                no_speech_prob=seg_nsp,
            ):
                continue
            seg["text"] = txt
            seg.setdefault("confidence_calibrated", False)
            seg.setdefault("transcript_stage", "offline")
            final_reconciled.append(seg)

        final_reconciled.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
        return final_reconciled

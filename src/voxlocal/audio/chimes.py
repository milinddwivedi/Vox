"""Pure-math synthesized subtle desktop audio cues for VOX FlowBar events.

Generates gentle sine-harmonic PCM WAV chimes in a temporary local directory
on first use and plays them asynchronously without blocking the UI thread.
"""

from __future__ import annotations

import math
import struct
import tempfile
import threading
import wave
from pathlib import Path
from typing import Dict

from voxlocal.core.logger import get_logger

logger = get_logger("audio.chimes")

_CHIME_CACHE_DIR = Path(tempfile.gettempdir()) / "vox_chimes_v1"
_CHIME_PATHS: Dict[str, Path] = {}
_LOCK = threading.Lock()


def _write_tone_wav(path: Path, notes: list[tuple[float, float]], sample_rate: int = 24000) -> None:
    """Synthesize sequence of (freq_hz, duration_sec) gentle sine+octave chimes with smooth envelope."""
    frames = bytearray()
    for freq, dur in notes:
        total_samples = max(1, int(sample_rate * dur))
        attack = max(1, int(sample_rate * 0.012))
        release = max(1, int(sample_rate * 0.045))
        for i in range(total_samples):
            env = 1.0
            if i < attack:
                env = i / float(attack)
            elif i > (total_samples - release):
                env = max(0.0, (total_samples - i) / float(release))
            t = i / float(sample_rate)
            # Soft warm harmonic envelope at modest volume (~14%)
            val = (
                0.78 * math.sin(2.0 * math.pi * freq * t)
                + 0.22 * math.sin(2.0 * math.pi * (freq * 2.0) * t)
            )
            pcm = int(max(-32767, min(32767, val * env * 0.16 * 32767.0)))
            frames.extend(struct.pack("<h", pcm))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))


def _ensure_chimes() -> Dict[str, Path]:
    with _LOCK:
        if _CHIME_PATHS:
            return _CHIME_PATHS
        _CHIME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        specs = {
            "start": [(523.25, 0.065), (659.25, 0.085)],      # C5 -> E5 soft rise
            "stop": [(659.25, 0.060), (523.25, 0.085)],       # E5 -> C5 soft close
            "complete": [(587.33, 0.055), (880.00, 0.095)],   # D5 -> A5 crisp finish
            "error": [(329.63, 0.075), (261.63, 0.110)],      # E4 -> C4 muted alert
        }
        for key, notes in specs.items():
            target = _CHIME_CACHE_DIR / f"vox_{key}.wav"
            if not target.exists():
                _write_tone_wav(target, notes)
            _CHIME_PATHS[key] = target
        return _CHIME_PATHS


def play_chime(event: str = "start", enabled: bool = True) -> None:
    """Play a gentle non-blocking audio cue for FlowBar state transitions."""
    if not enabled:
        return
    try:
        paths = _ensure_chimes()
        wav_path = paths.get(event)
        if not wav_path or not wav_path.exists():
            return
        import winsound
        winsound.PlaySound(
            str(wav_path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception as exc:
        logger.debug("Audio chime skipped (%s): %s", event, exc)

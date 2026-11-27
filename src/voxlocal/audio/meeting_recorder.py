"""
Meeting recorder: Microphone + Windows system audio (WASAPI loopback on every output
device: headphones, speakers, monitor) recorded to disk in real time.

Design:
- Audio is resampled to 16 kHz mono in a streaming way and appended to
  mic_track.wav / system_track.wav as it arrives, so a 3-hour meeting does not
  sit in RAM, and audio survives an app crash.
- A wall-clock synchroniser pads silence when WASAPI loopback stops delivering
  data (Windows pauses loopback while nothing is playing), keeping both tracks aligned.
- The last 90 s of each track is kept in memory for live transcription.
"""

from __future__ import annotations

import json
import os
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np
import pyaudiowpatch as pyaudio
from scipy import signal

from voxlocal.audio.devices import (
    TARGET_SAMPLE_RATE,
    AudioDeviceInfo,
    AudioDeviceManager,
    StreamResampler,
    clean_speech_microphone_audio,
    compute_rms_level,
    normalize_speech_audio,
)
from voxlocal.core.logger import get_logger

logger = get_logger("audio.meeting")

RING_SECONDS = 300


@dataclass
class MeetingRecordingResult:
    duration_sec: float
    mixed_wav_path: str
    mic_wav_path: str
    sys_wav_path: str
    mic_device_name: str
    loopback_device_name: str
    capture_mode: str
    audio_health: Dict[str, Any] = field(default_factory=dict)


class AudioInputMonitor:
    """
    Non-destructive rolling signal-level & integrity monitor for captured audio tracks.
    Measures active RMS dBFS, noise-floor dBFS, peak dBFS, clipping ratio, silence stalls,
    device disconnections, stream interruptions, zero-byte stalls, and heartbeats without modifying PCM.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            now_mono = time.monotonic()
            self._stats: Dict[str, Dict[str, object]] = {
                trk: {
                    "enabled": False,
                    "sample_count": 0,
                    "samples_captured": 0,
                    "sum_sq": 0.0,
                    "peak": 0.0,
                    "clipped_samples": 0,
                    "clipping_samples": 0,
                    "clipping_events": 0,
                    "callbacks_received": 0,
                    "last_callback_monotonic": 0.0,
                    "last_heartbeat_monotonic": now_mono,
                    "continuous_silence_sec": 0.0,
                    "silence_stall_count": 0,
                    "silence_stall_active": False,
                    "disconnect_events": 0,
                    "stream_interruptions": 0,
                    "zero_byte_events": 0,
                    "_disconnected_latched": False,
                    "_zero_byte_latched": False,
                    "_clipping_warned": False,
                    "_last_silence_warn_ts": 0.0,
                    "_last_disconnect_warn_ts": 0.0,
                    "chunk_rms": deque(maxlen=400),
                }
                for trk in ("mic", "system")
            }
            self._low_mic_warning_fired: bool = False
            self._warnings: List[Dict[str, Any]] = []
            self._last_heartbeat_monotonic: float = now_mono

    def configure_tracks(self, *, mic_enabled: bool, system_enabled: bool) -> None:
        with self._lock:
            self._stats["mic"]["enabled"] = bool(mic_enabled)
            self._stats["system"]["enabled"] = bool(system_enabled)

    def record_callback(self, track: str, now_mono: Optional[float] = None) -> None:
        if track not in ("mic", "system"):
            return
        ts = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            st = self._stats[track]
            st["callbacks_received"] = int(st["callbacks_received"]) + 1
            st["last_callback_monotonic"] = ts
            st["_disconnected_latched"] = False
            st["_zero_byte_latched"] = False

    def observe(self, track: str, samples: np.ndarray, *, dt_sec: Optional[float] = None) -> None:
        if track not in ("mic", "system") or samples is None or samples.size == 0:
            return
        arr = np.asarray(samples, dtype=np.float32)
        abs_arr = np.abs(arr)
        chunk_sq = float(np.sum(np.square(arr, dtype=np.float64)))
        chunk_rms = float(np.sqrt(chunk_sq / float(arr.size)))
        chunk_peak = float(np.max(abs_arr))
        clipped_999 = int(np.sum(abs_arr >= 0.999))
        clipped_980 = int(np.sum(abs_arr >= 0.98))
        chunk_dur = float(dt_sec) if dt_sec is not None else (float(arr.size) / float(TARGET_SAMPLE_RATE))
        with self._lock:
            st = self._stats[track]
            st["sample_count"] = int(st["sample_count"]) + int(arr.size)
            st["samples_captured"] = int(st["samples_captured"]) + int(arr.size)
            st["sum_sq"] = float(st["sum_sq"]) + chunk_sq
            st["peak"] = max(float(st["peak"]), chunk_peak)
            st["clipped_samples"] = int(st["clipped_samples"]) + clipped_999
            st["clipping_samples"] = int(st["clipping_samples"]) + clipped_980
            if clipped_980 > 0:
                st["clipping_events"] = int(st["clipping_events"]) + 1
            if chunk_rms < 1e-4:
                prev_sil = float(st["continuous_silence_sec"])
                new_sil = prev_sil + chunk_dur
                st["continuous_silence_sec"] = new_sil
                if new_sil >= 12.0 and not bool(st["silence_stall_active"]):
                    st["silence_stall_active"] = True
                    st["silence_stall_count"] = int(st["silence_stall_count"]) + 1
            else:
                st["continuous_silence_sec"] = 0.0
                st["silence_stall_active"] = False
            if chunk_rms > 0.0:
                rms_deque = st["chunk_rms"]
                assert isinstance(rms_deque, deque)
                rms_deque.append(chunk_rms)

    def _append_warning_locked(self, code: str, track: str, message: str, elapsed_sec: float) -> None:
        self._warnings.append(
            {
                "code": code,
                "track": track,
                "elapsed_sec": round(float(elapsed_sec), 2),
                "message": message,
            }
        )
        if len(self._warnings) > 64:
            self._warnings = self._warnings[-64:]

    def heartbeat_tick(
        self,
        *,
        elapsed_sec: float,
        mic_active: bool,
        sys_active: bool,
        is_paused: bool = False,
        now_mono: Optional[float] = None,
    ) -> List[str]:
        """
        Update heartbeat & evaluate disconnect, zero-byte, silence-stall, and clipping health.
        Returns newly triggered warning messages (throttled to avoid spamming UI).
        """
        ts = now_mono if now_mono is not None else time.monotonic()
        emitted: List[str] = []
        with self._lock:
            self._last_heartbeat_monotonic = ts
            for trk, is_act in (("mic", mic_active), ("system", sys_active)):
                st = self._stats[trk]
                st["last_heartbeat_monotonic"] = ts
                if is_paused or not bool(st["enabled"]):
                    continue

                cb_n = int(st["callbacks_received"])
                last_cb = float(st["last_callback_monotonic"])
                n_samp = int(st["samples_captured"])

                # 1. Disconnect / stream stall detection
                cb_stalled = cb_n > 0 and last_cb > 0.0 and (ts - last_cb) > 3.0
                if (not is_act) or (trk == "mic" and cb_stalled):
                    if not bool(st["_disconnected_latched"]):
                        st["_disconnected_latched"] = True
                        st["disconnect_events"] = int(st["disconnect_events"]) + 1
                        st["stream_interruptions"] = int(st["stream_interruptions"]) + 1
                        if (ts - float(st["_last_disconnect_warn_ts"])) >= 15.0:
                            st["_last_disconnect_warn_ts"] = ts
                            label = "Microphone" if trk == "mic" else "System audio"
                            msg = (
                                f"{label} stream disconnected or stalled during recording; "
                                "surviving audio track continues uninterrupted."
                            )
                            self._append_warning_locked("stream_disconnected", trk, msg, elapsed_sec)
                            emitted.append(msg)

                # 2. Zero-byte stream check (>4s elapsed with zero captured samples & zero callbacks)
                if float(elapsed_sec) >= 4.0 and n_samp == 0 and cb_n == 0 and trk == "mic":
                    if not bool(st["_zero_byte_latched"]):
                        st["_zero_byte_latched"] = True
                        st["zero_byte_events"] = int(st["zero_byte_events"]) + 1
                        msg = "Microphone produced 0 bytes of audio after 4 seconds. Check mute switch or input permission."
                        self._append_warning_locked("zero_byte_stream", trk, msg, elapsed_sec)
                        emitted.append(msg)

                # 3. Persistent silence stall alert
                if trk == "mic" and bool(st["silence_stall_active"]) and float(st["continuous_silence_sec"]) >= 12.0:
                    if (ts - float(st["_last_silence_warn_ts"])) >= 45.0:
                        st["_last_silence_warn_ts"] = ts
                        msg = (
                            f"Microphone input has been silent for {float(st['continuous_silence_sec']):.0f}s "
                            "(hardware mute or disconnected headset?)."
                        )
                        self._append_warning_locked("silence_stall", trk, msg, elapsed_sec)
                        emitted.append(msg)

                # 4. Sustained clipping alert
                if n_samp >= 2 * TARGET_SAMPLE_RATE:
                    clip_ratio = float(st["clipping_samples"]) / float(max(1, n_samp))
                    if clip_ratio > 0.02 and not bool(st["_clipping_warned"]):
                        st["_clipping_warned"] = True
                        msg = (
                            f"{'Microphone' if trk == 'mic' else 'System audio'} clipping detected "
                            f"({clip_ratio * 100.0:.1f}% of samples >= -0.17 dBFS). Reduce input gain."
                        )
                        self._append_warning_locked("clipping_detected", trk, msg, elapsed_sec)
                        emitted.append(msg)

        return emitted

    @staticmethod
    def _to_dbfs(amp: float) -> float:
        return round(float(20.0 * np.log10(max(float(amp), 1e-9))), 2)

    def _track_snapshot_locked(self, track: str) -> Dict[str, Any]:
        st = self._stats[track]
        n = int(st["sample_count"])
        sum_sq = float(st["sum_sq"])
        peak = float(st["peak"])
        clipped = int(st["clipped_samples"])
        clipping_980 = int(st["clipping_samples"])
        clip_ratio = round(float(clipping_980) / float(max(1, n)), 6) if n > 0 else 0.0
        rms_list = list(st["chunk_rms"])  # type: ignore[arg-type]
        overall_rms = float(np.sqrt(sum_sq / float(max(1, n)))) if n > 0 else 0.0
        if rms_list:
            arr_r = np.asarray(rms_list, dtype=np.float64)
            noise_floor = float(np.percentile(arr_r, 20))
            p90_rms = float(np.percentile(arr_r, 90))
            top20 = float(np.mean(np.sort(arr_r)[int(0.8 * len(arr_r)) :])) if len(arr_r) >= 5 else float(np.mean(arr_r))
        else:
            noise_floor = 0.0
            p90_rms = 0.0
            top20 = overall_rms

        if not bool(st["enabled"]) and n == 0:
            trk_status = "inactive"
        elif bool(st["_disconnected_latched"]) or int(st["disconnect_events"]) > 0 and n == 0:
            trk_status = "disconnected"
        elif clip_ratio > 0.02 and n >= 2 * TARGET_SAMPLE_RATE:
            trk_status = "degraded_clipping"
        elif bool(st["silence_stall_active"]):
            trk_status = "degraded_silence"
        else:
            trk_status = "healthy"

        return {
            "enabled": bool(st["enabled"]),
            "status": trk_status,
            "sample_count": n,
            "samples_captured": int(st["samples_captured"]),
            "callbacks_received": int(st["callbacks_received"]),
            "last_callback_monotonic": round(float(st["last_callback_monotonic"]), 3),
            "last_heartbeat_monotonic": round(float(st["last_heartbeat_monotonic"]), 3),
            "overall_rms_dbfs": self._to_dbfs(overall_rms),
            "active_rms_dbfs": self._to_dbfs(top20),
            "peak_dbfs": self._to_dbfs(peak),
            "noise_floor_dbfs": self._to_dbfs(noise_floor),
            "p90_rms": round(p90_rms, 6),
            "noise_floor_amp": round(noise_floor, 6),
            "clipped_samples": clipped,
            "clipping_samples": clipping_980,
            "clipping_events": int(st["clipping_events"]),
            "clipping_ratio": clip_ratio,
            "continuous_silence_sec": round(float(st["continuous_silence_sec"]), 2),
            "silence_stall_count": int(st["silence_stall_count"]),
            "silence_stall_active": bool(st["silence_stall_active"]),
            "disconnect_events": int(st["disconnect_events"]),
            "stream_interruptions": int(st["stream_interruptions"]),
            "zero_byte_events": int(st["zero_byte_events"]),
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            mic_snap = self._track_snapshot_locked("mic")
            sys_snap = self._track_snapshot_locked("system")
            statuses = [s["status"] for s in (mic_snap, sys_snap) if s["status"] != "inactive"]
            if any(st == "disconnected" for st in statuses):
                overall = "disconnected"
            elif any(st.startswith("degraded") for st in statuses):
                overall = "degraded"
            elif statuses:
                overall = "healthy"
            else:
                overall = "inactive"
            return {
                "overall_status": overall,
                "heartbeat_monotonic": round(float(self._last_heartbeat_monotonic), 3),
                "mic": mic_snap,
                "system": sys_snap,
                "low_mic_warning_fired": self._low_mic_warning_fired,
                "warnings": list(self._warnings),
            }

    def evaluate_low_mic_warning(self, elapsed_sec: float) -> Optional[str]:
        """Fire a single warning if microphone captures speech-like modulation at very low gain."""
        if float(elapsed_sec) < 12.0:
            return None
        with self._lock:
            if self._low_mic_warning_fired:
                return None
            mic = self._track_snapshot_locked("mic")
            if int(mic["sample_count"]) < int(6.0 * TARGET_SAMPLE_RATE):
                return None
            noise_floor = float(mic["noise_floor_amp"])
            p90_rms = float(mic["p90_rms"])
            peak_db = float(mic["peak_dbfs"])
            active_db = float(mic["active_rms_dbfs"])
            if p90_rms > max(noise_floor * 2.5, 1e-4) and (peak_db < -24.0 or active_db < -35.0):
                self._low_mic_warning_fired = True
                return (
                    f"Microphone input level is very low (active RMS {active_db:.1f} dBFS, "
                    f"peak {peak_db:.1f} dBFS). Check mic positioning or Windows input volume."
                )
            return None


class _Track:
    """A 16 kHz mono track streamed to a WAV file plus an in-memory ring of recent audio."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(TARGET_SAMPLE_RATE)
        self.total = 0
        self.ring: Deque[np.ndarray] = deque()
        self.ring_len = 0
        self.ring_start = 0  # absolute sample index of ring[0][0]
        self.level = 0.0

    def append(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        self.wav.writeframes(pcm.tobytes())
        self.total += samples.size
        self.ring.append(samples.astype(np.float32))
        self.ring_len += samples.size
        limit = RING_SECONDS * TARGET_SAMPLE_RATE
        while self.ring_len - self.ring[0].size >= limit:
            dropped = self.ring.popleft()
            self.ring_len -= dropped.size
            self.ring_start += dropped.size

    def window(self, start: int, end: int) -> Tuple[np.ndarray, int]:
        actual_start = max(start, self.ring_start)
        clamped_end = min(end, self.total)
        if clamped_end <= actual_start or not self.ring:
            return np.zeros(0, dtype=np.float32), actual_start
        joined = np.concatenate(list(self.ring))
        slice_data = joined[actual_start - self.ring_start : clamped_end - self.ring_start].copy()
        return slice_data, actual_start

    def close(self) -> None:
        try:
            if self.total == 0:
                self.wav.writeframes(b"")
            self.wav.close()
        except Exception:
            pass


class MeetingRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = "idle"  # idle | recording | paused
        self._mode = "both"
        self._pa: Optional[pyaudio.PyAudio] = None
        self._mic_stream = None
        self._loop_streams: List[Tuple[AudioDeviceInfo, object]] = []
        self._keepalive = None
        self._mic_dev: Optional[AudioDeviceInfo] = None
        self._loop_devs: List[AudioDeviceInfo] = []

        self._mic_q: List[np.ndarray] = []
        self._sys_q: Dict[int, List[np.ndarray]] = {}
        self._mic_rs: Optional[StreamResampler] = None
        self._sys_rs: Dict[int, StreamResampler] = {}

        self._mic: Optional[_Track] = None
        self._sys: Optional[_Track] = None
        self._out_dir: Optional[Path] = None

        self._elapsed = 0.0
        self._resume_ts = 0.0
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.mic_gain: float = 1.0

        # Real-time microphone conditioning state (85Hz rumble filter + noise expander)
        self._mic_sos: Optional[np.ndarray] = None
        self._mic_zi: Optional[np.ndarray] = None
        self._mic_noise_floor: float = 0.001
        self._input_monitor = AudioInputMonitor()

        self.on_status_tick: Optional[Callable[[float, float, float, bool, bool], None]] = None
        self.on_device_warning: Optional[Callable[[str], None]] = None

    def get_audio_diagnostics(self) -> Dict[str, object]:
        """Return non-destructive per-track signal diagnostics (RMS dBFS, peak dBFS, noise floor, clipping)."""
        return self._input_monitor.snapshot()

    def get_audio_health_snapshot(self) -> Dict[str, Any]:
        """Return full audio capture integrity & health snapshot (heartbeats, stalls, disconnects, WAV byte sizes)."""
        snap = self._input_monitor.snapshot()
        with self._lock:
            snap["recorder_state"] = self._state
            snap["elapsed_sec"] = round(float(self.elapsed_seconds), 3)
            snap["capture_mode"] = self._mode
            mic_p = self._mic.path if self._mic else (self._out_dir / "mic_track.wav" if self._out_dir else None)
            sys_p = self._sys.path if self._sys else (self._out_dir / "system_track.wav" if self._out_dir else None)
            snap["wav_sizes_bytes"] = {
                "mic": mic_p.stat().st_size if (mic_p and mic_p.exists()) else 0,
                "system": sys_p.stat().st_size if (sys_p and sys_p.exists()) else 0,
            }
        return snap

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in ("recording", "paused")

    @is_recording.setter
    def is_recording(self, val: bool) -> None:
        with self._lock:
            self._state = "recording" if val else "idle"

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            if self._state == "recording":
                return self._elapsed + (time.perf_counter() - self._resume_ts)
            return self._elapsed

    # ------------------------------------------------------------------
    def start(self, output_dir: Path, capture_mode: str = "both",
              mic_device_index: Optional[int] = None, mic_device_name: str = "",
              loopback_device_index: Optional[int] = None, loopback_device_name: str = "",
              mic_gain: Optional[float] = None, **_ignored) -> Tuple[bool, str]:
        with self._lock:
            if self._state != "idle":
                return False, "A recording is already running."
            if mic_gain is not None:
                self.mic_gain = max(0.5, float(mic_gain))
            self._mode = capture_mode
            want_mic = capture_mode in ("both", "mixed", "mic_only")
            want_sys = capture_mode in ("both", "mixed", "system_only")
            self._mic_dev = AudioDeviceManager.resolve_microphone(mic_device_index, mic_device_name) if want_mic else None
            self._loop_devs = AudioDeviceManager.resolve_loopbacks_list(loopback_device_index, loopback_device_name) if want_sys else []
            if not self._mic_dev and not self._loop_devs:
                return False, "No microphone or system-audio device found."

            if self._mic_dev and self._loop_devs:
                bt_warn = AudioDeviceManager.detect_bluetooth_profile_conflict(self._mic_dev, self._loop_devs[0])
                if bt_warn:
                    logger.warning("%s", bt_warn)
                    if self.on_device_warning:
                        try:
                            self.on_device_warning(bt_warn)
                        except Exception:
                            pass

            self._out_dir = Path(output_dir)
            os.makedirs(self._out_dir, exist_ok=True)
            self._mic_q, self._sys_q = [], {d.index: [] for d in self._loop_devs}
            self._sys_rs = {}
            self._elapsed = 0.0
            self._stop_evt.clear()

            # Initialize real-time microphone broadcast conditioning (85Hz rumble filter + noise expander)
            try:
                self._mic_sos = signal.butter(2, 85.0, btype="highpass", fs=TARGET_SAMPLE_RATE, output="sos")
                self._mic_zi = signal.sosfilt_zi(self._mic_sos)
            except Exception:
                self._mic_sos = None
                self._mic_zi = None
            self._mic_noise_floor = 0.001
            self._input_monitor.reset()
            open_err = None
            try:
                self._pa = pyaudio.PyAudio()
                self._open_streams()
            except Exception as exc:
                open_err = exc

        if open_err is not None:
            self._close_streams()
            return False, f"Could not open audio devices: {open_err}"

        with self._lock:
            self._mic = _Track(self._out_dir / "mic_track.wav") if self._mic_stream else None
            self._sys = _Track(self._out_dir / "system_track.wav") if self._loop_streams else None
            self._input_monitor.configure_tracks(
                mic_enabled=self._mic_stream is not None,
                system_enabled=bool(self._loop_streams),
            )
            self._resume_ts = time.perf_counter()
            self._state = "recording"
            self._thread = threading.Thread(target=self._sync_loop, name="MeetingSync", daemon=True)
            self._thread.start()

            mic_label = self._mic_dev.name if self._mic_stream and self._mic_dev else "off"
            sys_label = f"{len(self._loop_streams)} output device(s)" if self._loop_streams else "off"
            logger.info("Meeting recording started (mic=%s, system=%s)", mic_label, sys_label)
            return True, f"Mic: {mic_label} | System audio: {sys_label}"

    def _open_streams(self) -> None:
        assert self._pa is not None
        if self._mic_dev is not None:
            dev = self._mic_dev

            def mic_cb(data, *_):
                if self._state == "recording" and data:
                    self._input_monitor.record_callback("mic")
                    pcm = np.frombuffer(data, dtype=np.int16).copy()
                    with self._lock:
                        if len(self._mic_q) < 200:
                            self._mic_q.append(pcm)
                return (None, pyaudio.paContinue)

            try:
                self._mic_stream = self._pa.open(
                    format=pyaudio.paInt16, channels=dev.channels, rate=dev.sample_rate, input=True,
                    input_device_index=dev.index, frames_per_buffer=int(dev.sample_rate * 0.05),
                    stream_callback=mic_cb)
                self._mic_stream.start_stream()
                self._mic_rs = StreamResampler(dev.sample_rate, dev.channels)
            except Exception as exc:
                self._mic_stream = None
                logger.warning("Microphone unavailable: %s", exc)
                if self.on_device_warning:
                    self.on_device_warning(f"Microphone unavailable: {exc}")

        if self._loop_devs:
            rate = self._loop_devs[0].sample_rate
            silence = b"\x00" * int(rate * 0.05) * 4
            try:  # keeps WASAPI clock ticking during silence on the default output
                self._keepalive = self._pa.open(
                    format=pyaudio.paInt16, channels=2, rate=rate, output=True,
                    frames_per_buffer=int(rate * 0.05),
                    stream_callback=lambda d, n, *_: (silence[: n * 4].ljust(n * 4, b"\x00"), pyaudio.paContinue))
                self._keepalive.start_stream()
            except Exception:
                self._keepalive = None

            for dev in self._loop_devs:
                def make_cb(i: int):
                    def cb(data, *_):
                        if self._state == "recording" and data:
                            self._input_monitor.record_callback("system")
                            pcm = np.frombuffer(data, dtype=np.int16).copy()
                            with self._lock:
                                q = self._sys_q.get(i)
                                if q is not None and len(q) < 200:
                                    q.append(pcm)
                        return (None, pyaudio.paContinue)
                    return cb
                try:
                    st = self._pa.open(
                        format=pyaudio.paInt16, channels=dev.channels, rate=dev.sample_rate, input=True,
                        input_device_index=dev.index, frames_per_buffer=int(dev.sample_rate * 0.05),
                        stream_callback=make_cb(dev.index))
                    st.start_stream()
                    self._loop_streams.append((dev, st))
                    self._sys_rs[dev.index] = StreamResampler(dev.sample_rate, dev.channels)
                except Exception as exc:
                    logger.debug("Loopback %s unavailable: %s", dev.name, exc)

        if self._mic_stream is None and not self._loop_streams:
            raise RuntimeError("no audio stream could be opened")

    # ------------------------------------------------------------------
    def pause(self) -> bool:
        with self._lock:
            if self._state != "recording":
                return False
            self._drain_locked()
            self._elapsed += time.perf_counter() - self._resume_ts
            self._pad_locked(self._elapsed)
            self._state = "paused"
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._state != "paused":
                return False
            self._mic_q.clear()
            for q in self._sys_q.values():
                q.clear()
            self._resume_ts = time.perf_counter()
            self._state = "recording"
            return True

    def stop(self, preserve_separate_tracks: bool = True) -> Optional[MeetingRecordingResult]:
        with self._lock:
            if self._state == "idle":
                return None
            if self._state == "recording":
                self._elapsed += time.perf_counter() - self._resume_ts
            self._state = "idle"
            self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

        with self._lock:
            self._drain_locked(final=True)
            self._pad_locked(self._elapsed)
            loop_names = [d.name for d, _ in self._loop_streams]
            mic_path = self._mic.path if self._mic else None
            sys_path = self._sys.path if self._sys else None
            for t in (self._mic, self._sys):
                if t:
                    t.close()
            self._mic = self._sys = None
            duration = self._elapsed

        # Close streams outside self._lock to prevent deadlocks with PortAudio callback threads
        self._close_streams()

        assert self._out_dir is not None
        mixed_path = self._out_dir / "meeting_mixed.wav"

        # Initialize empty valid 16kHz mono WAV header (<0.2ms) so path exists cleanly for immediate inspection
        if not mixed_path.exists():
            try:
                with wave.open(str(mixed_path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(TARGET_SAMPLE_RATE)
                    w.writeframes(b"")
            except Exception as stub_exc:
                logger.debug("Failed initializing stub mixed WAV: %s", stub_exc)

        health_snap = self._input_monitor.snapshot()
        health_snap["elapsed_sec"] = round(float(duration), 3)
        health_snap["capture_mode"] = self._mode
        health_snap["wav_sizes_bytes"] = {
            "mic": mic_path.stat().st_size if (mic_path and mic_path.exists()) else 0,
            "system": sys_path.stat().st_size if (sys_path and sys_path.exists()) else 0,
            "mixed": mixed_path.stat().st_size if mixed_path.exists() else 0,
        }
        try:
            diag_path = self._out_dir / "capture_diagnostics.json"
            existing_diag: Dict[str, Any] = {}
            if diag_path.exists():
                try:
                    existing_diag = json.loads(diag_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    existing_diag = {}
            existing_diag["audio_health"] = health_snap
            diag_path.write_text(json.dumps(existing_diag, indent=2, default=str), encoding="utf-8")
        except Exception as diag_exc:
            logger.debug("Failed writing audio_health capture_diagnostics.json: %s", diag_exc)

        res = MeetingRecordingResult(
            duration_sec=duration,
            mixed_wav_path=str(mixed_path),
            mic_wav_path=str(mic_path) if mic_path else "",
            sys_wav_path=str(sys_path) if sys_path else "",
            mic_device_name=self._mic_dev.name if self._mic_dev else "",
            loopback_device_name=", ".join(loop_names[:3]),
            capture_mode=self._mode,
            audio_health=health_snap,
        )
        logger.info("Meeting saved: %s (%.1fs)", mixed_path, duration)
        return res

    def get_audio_window(self, start_sec: float, end_sec: float) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Recent audio buffer for progressive live transcription.
        Returns (mic_samples, sys_samples, actual_start_sec).
        """
        a, b = int(start_sec * TARGET_SAMPLE_RATE), int(end_sec * TARGET_SAMPLE_RATE)
        with self._lock:
            mic, mic_act = self._mic.window(a, b) if self._mic else (np.zeros(0, dtype=np.float32), a)
            sys_, sys_act = self._sys.window(a, b) if self._sys else (np.zeros(0, dtype=np.float32), a)

        # Exact timeline synchronization: actual offset in seconds
        actual_sample = min(mic_act, sys_act) if (self._mic and self._sys and mic.size > 0 and sys_.size > 0) else (mic_act if (self._mic and mic.size > 0) else (sys_act if (self._sys and sys_.size > 0) else a))
        actual_start_sec = round(actual_sample / float(TARGET_SAMPLE_RATE), 3)
        return mic, sys_, actual_start_sec

    # ------------------------------------------------------------------
    def _drain_locked(self, final: bool = False) -> None:
        if self._mic and self._mic_rs:
            chunk = self._mic_rs.process(np.concatenate(self._mic_q)) if self._mic_q else np.zeros(0, np.float32)
            self._mic_q.clear()
            if final:
                chunk = np.concatenate([chunk, self._mic_rs.flush()])
            if chunk.size > 0:
                # 1. Real-time 85Hz High-pass Filter (cuts mechanical desk rumble & low-end AC hum)
                if self._mic_sos is not None and self._mic_zi is not None:
                    try:
                        chunk, self._mic_zi = signal.sosfilt(self._mic_sos, chunk, zi=self._mic_zi)
                        chunk = chunk.astype(np.float32)
                    except Exception:
                        pass

                # 2. Controlled gain & soft-knee peak limiting to max 0.65 (preserve raw quiet endings without downward gating)
                if self.mic_gain != 1.0:
                    scale = max(0.5, min(2.0, float(self.mic_gain)))
                    chunk = chunk * scale

                abs_c = np.abs(chunk)
                knee = 0.55
                over = abs_c > knee
                if np.any(over):
                    chunk = np.where(
                        abs_c <= knee,
                        chunk,
                        np.sign(chunk) * (knee + 0.15 * np.tanh((abs_c - knee) / 0.15)),
                    )
                chunk = np.clip(chunk, -0.68, 0.68)
            self._mic.append(chunk)
            self._input_monitor.observe("mic", chunk)
            self._mic.level = compute_rms_level(chunk[-1600:]) if chunk.size else self._mic.level * 0.5

        if self._sys:
            parts = []
            for dev, _ in self._loop_streams:
                q = self._sys_q.get(dev.index) or []
                rs = self._sys_rs[dev.index]
                out = rs.process(np.concatenate(q)) if q else np.zeros(0, np.float32)
                q.clear()
                if final:
                    out = np.concatenate([out, rs.flush()])
                if out.size and float(np.max(np.abs(out))) > 1e-4:
                    parts.append(out)
            if parts:
                n = max(p.size for p in parts)
                mix = np.zeros(n, dtype=np.float32)
                for p in parts:
                    mix[: p.size] += p
                clipped_mix = np.clip(mix, -1, 1)
                self._sys.append(clipped_mix)
                self._input_monitor.observe("system", clipped_mix)
                self._sys.level = compute_rms_level(mix[-1600:])
            else:
                self._sys.level *= 0.5

    def _pad_locked(self, elapsed: float) -> None:
        target = int(elapsed * TARGET_SAMPLE_RATE)
        tol = int(0.25 * TARGET_SAMPLE_RATE)
        for t in (self._mic, self._sys):
            if t and target - t.total > tol:
                t.append(np.zeros(target - t.total, dtype=np.float32))

    def _sync_loop(self) -> None:
        while not self._stop_evt.wait(0.15):
            with self._lock:
                if self._state == "idle":
                    break
                is_paused = self._state != "recording"
                if not is_paused:
                    self._drain_locked()
                    elapsed = self._elapsed + (time.perf_counter() - self._resume_ts)
                    self._pad_locked(elapsed)
                else:
                    elapsed = self._elapsed
                mic_ok = bool(self._mic_stream and self._mic_stream.is_active())
                sys_ok = any(st.is_active() for _, st in self._loop_streams)
                ml = self._mic.level if self._mic else 0.0
                sl = self._sys.level if self._sys else 0.0

            health_warnings = self._input_monitor.heartbeat_tick(
                elapsed_sec=elapsed,
                mic_active=mic_ok,
                sys_active=sys_ok,
                is_paused=is_paused,
            )
            low_mic_msg = self._input_monitor.evaluate_low_mic_warning(elapsed)
            if low_mic_msg:
                health_warnings.append(low_mic_msg)
            for warn_msg in health_warnings:
                logger.warning("%s", warn_msg)
                if self.on_device_warning:
                    try:
                        self.on_device_warning(warn_msg)
                    except Exception:
                        pass
            if self.on_status_tick:
                try:
                    self.on_status_tick(elapsed, ml, sl, mic_ok, sys_ok)
                except Exception:
                    pass

    def _close_streams(self, synchronous: bool = False) -> None:
        with self._lock:
            streams = [self._mic_stream, self._keepalive] + [s for _, s in self._loop_streams]
            self._mic_stream = None
            self._keepalive = None
            self._loop_streams = []
            pa = self._pa
            self._pa = None

        def _do_close():
            for st in streams:
                if st is None:
                    continue
                try:
                    if st.is_active():
                        st.stop_stream()
                except Exception:
                    pass
                try:
                    st.close()
                except Exception:
                    pass
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    pass

        if synchronous:
            _do_close()
        else:
            threading.Thread(target=_do_close, name="MeetingStreamTeardown", daemon=True).start()

    def _write_mixed(
        self,
        mixed_path: Path,
        mic_path: Optional[Path],
        sys_path: Optional[Path],
        sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> float:
        return _write_mixed(mic_path, sys_path, mixed_path)


def _read_wav_f32(path: Optional[Path]) -> np.ndarray:
    if not path or not path.exists():
        return np.zeros(0, dtype=np.float32)
    with wave.open(str(path), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def _write_mixed(mic_path: Optional[Path], sys_path: Optional[Path], out: Path, block_sec: int = 120) -> float:
    """
    Mix tracks block-by-block (strictly bounded memory) into meeting_mixed.wav with vocal enhancement.
    Even for multi-hour recordings, processes in 60s/120s blocks so memory stays flat (<15 MB).
    Returns total duration in seconds.
    """
    import shutil

    chunk_frames = 60 * TARGET_SAMPLE_RATE  # 60s bounded DSP window

    # 1. Preserve original raw mic audio untouched on disk (and maintain *_raw.wav mirror for ASR/legacy callers)
    if mic_path and mic_path.exists() and mic_path.stat().st_size > 44:
        try:
            raw_backup = mic_path.with_name(mic_path.stem + "_raw.wav")
            if not raw_backup.exists():
                try:
                    shutil.copy2(str(mic_path), str(raw_backup))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Error preserving raw mic track: %s", exc)

    # 2. Preserve original raw system audio untouched on disk (and maintain system_track_raw.wav mirror)
    if sys_path and sys_path.exists() and sys_path.stat().st_size > 44:
        try:
            raw_sys_backup = sys_path.with_name("system_track_raw.wav")
            if not raw_sys_backup.exists():
                shutil.copy2(str(sys_path), str(raw_sys_backup))
        except Exception as exc:
            logger.debug("Error preserving raw system audio: %s", exc)

    readers = []
    for p in (mic_path, sys_path):
        if p and p.exists():
            try:
                readers.append(wave.open(str(p), "rb"))
            except Exception as w_exc:
                logger.warning("Skipping unreadable track '%s' during mix: %s", p, w_exc)
                readers.append(None)
        else:
            readers.append(None)
    lengths = [r.getnframes() if r else 0 for r in readers]
    total = max(lengths) if lengths else 0

    block = block_sec * TARGET_SAMPLE_RATE
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TARGET_SAMPLE_RATE)
            done = 0
            while done < total:
                n = min(block, total - done)
                mix = np.zeros(n, dtype=np.float32)
                if readers[0]:
                    raw_m = readers[0].readframes(n)
                    mic_buf = np.frombuffer(raw_m, dtype=np.int16).astype(np.float32) / 32768.0
                    if mic_buf.size > 0:
                        try:
                            mic_dsp = clean_speech_microphone_audio(
                                mic_buf,
                                TARGET_SAMPLE_RATE,
                                hp_cutoff=85.0,
                                target_peak=0.62,
                                spectral_nr_db=18.0,
                            )
                        except Exception:
                            mic_dsp = mic_buf
                        mix[: mic_dsp.size] += np.clip(mic_dsp, -0.75, 0.75) * 0.70

                if len(readers) > 1 and readers[1]:
                    raw_s = readers[1].readframes(n)
                    sys_buf = np.frombuffer(raw_s, dtype=np.int16).astype(np.float32) / 32768.0
                    if sys_buf.size > 0:
                        sys_dsp = sys_buf - float(np.mean(sys_buf))
                        s_peak = (
                            float(np.percentile(np.abs(sys_dsp), 99.8))
                            if sys_dsp.size > 500
                            else float(np.max(np.abs(sys_dsp)))
                        )
                        if s_peak > 0.85:
                            sys_dsp = sys_dsp * (0.85 / s_peak)
                        elif 0.025 < s_peak < 0.65:
                            sys_dsp = sys_dsp * min(2.5, 0.78 / s_peak)
                        # Preserve high fidelity and intelligibility for remote meeting callers (0.85 weight)
                        mix[: sys_dsp.size] += np.clip(sys_dsp, -0.98, 0.98) * 0.85

                # True-Peak soft-knee saturation: preserves linear dynamics below 0.75, soft-rounds loud combined peaks
                abs_m = np.abs(mix)
                knee = 0.75
                over = abs_m > knee
                if np.any(over):
                    span = 0.95 - knee
                    mix = np.where(
                        abs_m <= knee,
                        mix,
                        np.sign(mix) * (knee + span * np.tanh((abs_m - knee) / span)),
                    )
                w.writeframes((np.clip(mix, -0.95, 0.95) * 32767).astype(np.int16).tobytes())
                done += n
    finally:
        for r in readers:
            if r:
                try:
                    r.close()
                except Exception:
                    pass
    return total / TARGET_SAMPLE_RATE


def compute_wav_quality_metrics(wav_path: str | Path) -> dict:
    """Compute Stage A audio acquisition diagnostics (RMS, peak, clipping ratio, DC offset)."""
    p = Path(wav_path)
    if not p.exists() or p.stat().st_size <= 44:
        return {"exists": False, "duration_sec": 0.0, "rms": 0.0, "peak": 0.0, "clipping_ratio": 0.0}
    try:
        with wave.open(str(p), "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            if n_frames <= 0:
                return {"exists": True, "duration_sec": 0.0, "rms": 0.0, "peak": 0.0, "clipping_ratio": 0.0}
            raw = np.frombuffer(wf.readframes(n_frames), dtype=np.int16).astype(np.float32) / 32768.0
        abs_raw = np.abs(raw)
        rms = float(np.sqrt(np.mean(raw ** 2)))
        peak = float(np.max(abs_raw))
        clipping_ratio = float(np.mean(abs_raw >= 0.985))
        dc_offset = float(np.mean(raw))
        return {
            "exists": True,
            "sample_rate": int(sr),
            "duration_sec": round(n_frames / float(sr), 2),
            "rms": round(rms, 5),
            "peak": round(peak, 5),
            "clipping_ratio": round(clipping_ratio, 6),
            "dc_offset": round(dc_offset, 6),
        }
    except Exception as exc:
        return {"exists": True, "error": str(exc), "rms": 0.0, "peak": 0.0, "clipping_ratio": 0.0}

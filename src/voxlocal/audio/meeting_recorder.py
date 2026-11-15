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

import os
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

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

        self.on_status_tick: Optional[Callable[[float, float, float, bool, bool], None]] = None
        self.on_device_warning: Optional[Callable[[str], None]] = None

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
            except Exception as stub_exc:
                logger.debug("Failed initializing stub mixed WAV: %s", stub_exc)

        res = MeetingRecordingResult(
            duration_sec=duration,
            mixed_wav_path=str(mixed_path),
            mic_wav_path=str(mic_path) if mic_path else "",
            sys_wav_path=str(sys_path) if sys_path else "",
            mic_device_name=self._mic_dev.name if self._mic_dev else "",
            loopback_device_name=", ".join(loop_names[:3]),
            capture_mode=self._mode,
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

                # 2. Real-time Downward Expander (Mutes fan noise/room hiss during pauses)
                c_rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0
                thresh = max(0.002, self._mic_noise_floor * 2.2)
                if c_rms < thresh:
                    self._mic_noise_floor = min(0.012, max(0.0003, self._mic_noise_floor * 0.95 + c_rms * 0.05))
                    expand_gain = max(0.18, (c_rms / max(1e-6, thresh)) ** 1.6)
                    chunk = chunk * expand_gain
                else:
                    self._mic_noise_floor = min(0.008, self._mic_noise_floor * 0.995 + c_rms * 0.005)

                # 3. Controlled gain & soft-knee peak limiting to max 0.65 (prevents shouting/clipping)
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
                self._sys.append(np.clip(mix, -1, 1))
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
                if self._state == "recording":
                    self._drain_locked()
                    elapsed = self._elapsed + (time.perf_counter() - self._resume_ts)
                    self._pad_locked(elapsed)
                else:
                    elapsed = self._elapsed
                mic_ok = bool(self._mic_stream and self._mic_stream.is_active())
                sys_ok = any(st.is_active() for _, st in self._loop_streams)
                ml = self._mic.level if self._mic else 0.0
                sl = self._sys.level if self._sys else 0.0
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

    # 1. Preserve original raw mic audio and apply Studio Broadcast DSP chain in bounded 60s chunks
    if mic_path and mic_path.exists():
        try:
            raw_backup = mic_path.with_name(mic_path.stem + "_raw.wav")
            if not raw_backup.exists():
                try:
                    shutil.copyfile(str(mic_path), str(raw_backup))
                except Exception:
                    pass

            tmp_mic = mic_path.with_name(mic_path.stem + "_dsp_tmp.wav")
            with wave.open(str(mic_path), "rb") as r_wf:
                total_m = r_wf.getnframes()
                if total_m > 0:
                    with wave.open(str(tmp_mic), "wb") as w_wf:
                        w_wf.setnchannels(1)
                        w_wf.setsampwidth(2)
                        w_wf.setframerate(TARGET_SAMPLE_RATE)
                        done_m = 0
                        while done_m < total_m:
                            n_m = min(chunk_frames, total_m - done_m)
                            buf_m = np.frombuffer(r_wf.readframes(n_m), dtype=np.int16).astype(np.float32) / 32768.0
                            if buf_m.size > 0:
                                cleaned_m = clean_speech_microphone_audio(
                                    buf_m,
                                    TARGET_SAMPLE_RATE,
                                    hp_cutoff=85.0,
                                    target_peak=0.62,
                                    spectral_nr_db=18.0,
                                )
                                w_wf.writeframes((np.clip(cleaned_m, -0.75, 0.75) * 32767).astype(np.int16).tobytes())
                            done_m += n_m
            if tmp_mic.exists() and tmp_mic.stat().st_size > 44:
                os.replace(str(tmp_mic), str(mic_path))
        except Exception as exc:
            logger.error("Error mastering mic track: %s", exc)

    # 2. Preserve untouched system_track_raw.wav (Stage A) and apply gentle DC/level conditioning
    if sys_path and sys_path.exists():
        try:
            import shutil

            raw_sys_backup = sys_path.with_name("system_track_raw.wav")
            if not raw_sys_backup.exists() and sys_path.stat().st_size > 44:
                shutil.copy2(str(sys_path), str(raw_sys_backup))

            tmp_sys = sys_path.with_name(sys_path.stem + "_dsp_tmp.wav")
            with wave.open(str(sys_path), "rb") as r_sf:
                total_s = r_sf.getnframes()
                if total_s > 0:
                    with wave.open(str(tmp_sys), "wb") as w_sf:
                        w_sf.setnchannels(1)
                        w_sf.setsampwidth(2)
                        w_sf.setframerate(TARGET_SAMPLE_RATE)
                        done_s = 0
                        while done_s < total_s:
                            n_s = min(chunk_frames, total_s - done_s)
                            buf_s = np.frombuffer(r_sf.readframes(n_s), dtype=np.int16).astype(np.float32) / 32768.0
                            if buf_s.size > 0:
                                buf_s = buf_s - float(np.mean(buf_s))
                                s_peak = (
                                    float(np.percentile(np.abs(buf_s), 99.8))
                                    if buf_s.size > 500
                                    else float(np.max(np.abs(buf_s)))
                                )
                                if s_peak > 0.85:
                                    buf_s = buf_s * (0.85 / s_peak)
                                elif 0.025 < s_peak < 0.65:
                                    # Boost quiet remote speakers gently (up to +8 dB max) for ASR clarity
                                    gain = min(2.5, 0.78 / s_peak)
                                    buf_s = buf_s * gain
                                w_sf.writeframes((np.clip(buf_s, -0.98, 0.98) * 32767).astype(np.int16).tobytes())
                            done_s += n_s
            if tmp_sys.exists() and tmp_sys.stat().st_size > 44:
                os.replace(str(tmp_sys), str(sys_path))
        except Exception as exc:
            logger.debug("Error cleaning system audio: %s", exc)

    readers = []
    for p in (mic_path, sys_path):
        readers.append(wave.open(str(p), "rb") if p and p.exists() else None)
    lengths = [r.getnframes() if r else 0 for r in readers]
    total = max(lengths) if lengths else 0

    block = block_sec * TARGET_SAMPLE_RATE
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_SAMPLE_RATE)
        done = 0
        while done < total:
            n = min(block, total - done)
            mix = np.zeros(n, dtype=np.float32)
            mic_buf = None
            if readers[0]:
                raw_m = readers[0].readframes(n)
                mic_buf = np.frombuffer(raw_m, dtype=np.int16).astype(np.float32) / 32768.0
                mix[: mic_buf.size] += mic_buf * 0.70

            if len(readers) > 1 and readers[1]:
                raw_s = readers[1].readframes(n)
                sys_buf = np.frombuffer(raw_s, dtype=np.int16).astype(np.float32) / 32768.0
                # Preserve high fidelity and intelligibility for remote meeting callers (0.85 weight)
                mix[: sys_buf.size] += sys_buf * 0.85

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
    for r in readers:
        if r:
            r.close()
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

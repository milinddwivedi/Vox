"""
Low-Latency Microphone + System Audio (WASAPI Loopback) Recorder for Universal Voice Typing.
Operates independently of MeetingRecorder so dictation never interrupts an active meeting.
When `include_system_audio=True` (default), captures both the user's Microphone AND any
active Windows System Audio (meeting participants, playing videos, browser audio, headphones,
or speakers) so whoever is talking on the PC is also recorded and transcribed!
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
import numpy as np
import pyaudiowpatch as pyaudio

from voxlocal.audio.devices import (
    AudioDeviceInfo,
    AudioDeviceManager,
    compute_rms_level,
    normalize_speech_audio,
    resample_audio_to_16k_mono,
)
from voxlocal.core.logger import get_logger

logger = get_logger("audio.dictation")


class DictationRecorder:
    """
    Captures microphone audio and (optionally) Windows WASAPI Loopback system audio
    for Universal Voice Typing.
    Provides real-time audio level callbacks and returns a 16kHz mono float32 numpy array.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._is_recording = False
        self._pa: Optional[pyaudio.PyAudio] = None
        self._mic_stream = None
        self._loop_streams: List[Tuple[AudioDeviceInfo, object]] = []

        self.FRAME_DURATION_SEC: float = 0.040  # 40ms frame slicing
        self.SAMPLES_PER_FRAME_16K: int = 640   # 16000 * 0.04 = 640 samples
        self.PRE_BUFFER_DURATION_SEC: float = 0.450  # 450ms pre-speech buffer
        self._pre_buffer_max_frames: int = int(round(self.PRE_BUFFER_DURATION_SEC / self.FRAME_DURATION_SEC))
        self._pre_buffer: collections.deque = collections.deque(maxlen=self._pre_buffer_max_frames)

        self._mic_frames: List[bytes] = []
        self._loop_frames: Dict[int, List[bytes]] = {}
        self._mic_device: Optional[AudioDeviceInfo] = None
        self._loop_devices: List[AudioDeviceInfo] = []

        self._start_time: float = 0.0
        self.last_mic_16k: np.ndarray = np.zeros(0, dtype=np.float32)
        self.last_sys_16k: np.ndarray = np.zeros(0, dtype=np.float32)
        self.mic_gain: float = 3.0

        self.on_level_change: Optional[Callable[[float], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def pre_buffer(self) -> collections.deque:
        return self._pre_buffer

    @staticmethod
    def slice_40ms_frames(audio_16k: np.ndarray) -> List[np.ndarray]:
        """Slice 16kHz mono audio into 40ms (640 samples) frames."""
        if audio_16k is None or audio_16k.size == 0:
            return []
        frame_size = 640
        num_frames = audio_16k.size // frame_size
        frames = [audio_16k[i * frame_size : (i + 1) * frame_size] for i in range(num_frames)]
        rem = audio_16k.size % frame_size
        if rem > 0:
            last = np.pad(audio_16k[num_frames * frame_size:], (0, frame_size - rem))
            frames.append(last)
        return frames

    def start(
        self,
        preferred_device_index: Optional[int] = None,
        preferred_device_name: str = "",
        include_system_audio: bool = True,
        preferred_loopback_index: Optional[int] = None,
        preferred_loopback_name: str = "",
        mic_gain: Optional[float] = None,
    ) -> Tuple[bool, str]:
        with self._lock:
            if self._is_recording:
                return True, "Already listening."

            if mic_gain is not None:
                self.mic_gain = max(0.5, float(mic_gain))

            mic_dev = AudioDeviceManager.resolve_microphone(
                preferred_device_index, preferred_device_name
            )
            loop_devs = (
                AudioDeviceManager.resolve_loopbacks_list(
                    preferred_loopback_index, preferred_loopback_name
                )
                if include_system_audio
                else []
            )

            if not mic_dev and not loop_devs:
                msg = "No working microphone or system audio device found."
                logger.error(msg)
                if self.on_error:
                    self.on_error(msg)
                return False, msg

            # Retain pre-speech rolling frames to avoid clipping initial speech onsets
            pre_speech_frames = list(self._pre_buffer)
            self._mic_frames.clear()
            self._mic_frames.extend(pre_speech_frames)
            self._loop_frames.clear()
            self._mic_device = mic_dev
            self._loop_devices = loop_devs
            self.last_mic_16k = np.zeros(0, dtype=np.float32)
            self.last_sys_16k = np.zeros(0, dtype=np.float32)

            try:
                self._pa = pyaudio.PyAudio()

                # 1. Open Microphone Stream
                if mic_dev is not None:
                    frames_per_buffer = max(512, int(mic_dev.sample_rate * self.FRAME_DURATION_SEC))

                    def _mic_cb(in_data, frame_count, time_info, status):
                        if status:
                            if status & getattr(pyaudio, "paInputOverflow", 2):
                                logger.debug("PortAudio mic input overflow")
                            if status & getattr(pyaudio, "paInputUnderflow", 1):
                                logger.debug("PortAudio mic input underflow")

                        with self._lock:
                            if not self._is_recording or not in_data:
                                if in_data:
                                    self._pre_buffer.append(in_data)
                                return (None, pyaudio.paContinue)
                            self._mic_frames.append(in_data)
                            self._pre_buffer.append(in_data)

                        if self.on_level_change and in_data:
                            try:
                                samples = np.frombuffer(in_data, dtype=np.int16)
                                boosted = samples.astype(np.float32) / 32768.0 * float(self.mic_gain)
                                lvl = compute_rms_level(boosted)
                                self.on_level_change(lvl)
                            except Exception:
                                pass
                        return (None, pyaudio.paContinue)

                    try:
                        self._mic_stream = self._pa.open(
                            format=pyaudio.paInt16,
                            channels=mic_dev.channels,
                            rate=mic_dev.sample_rate,
                            input=True,
                            input_device_index=mic_dev.index,
                            frames_per_buffer=frames_per_buffer,
                            stream_callback=_mic_cb,
                        )
                        self._mic_stream.start_stream()
                    except Exception as mic_exc:
                        logger.warning("Could not open mic '%s': %s", mic_dev.name, mic_exc)
                        self._mic_stream = None

                # 2. Open WASAPI Loopback Stream(s) to capture any playing video/meeting audio
                for lb_dev in loop_devs:
                    self._loop_frames[lb_dev.index] = []
                    lb_fpb = max(512, int(lb_dev.sample_rate * self.FRAME_DURATION_SEC))

                    def _make_lb_cb(dev_idx: int):
                        def _lb_cb(in_data, frame_count, time_info, status):
                            if status:
                                logger.debug("PortAudio loopback %s status: %s", dev_idx, status)
                            with self._lock:
                                if self._is_recording and in_data:
                                    self._loop_frames[dev_idx].append(in_data)
                            if self.on_level_change and in_data:
                                    try:
                                        samples = np.frombuffer(in_data, dtype=np.int16)
                                        lvl = compute_rms_level(samples.astype(np.float32) / 32768.0)
                                        if lvl > 0.05:
                                            self.on_level_change(lvl)
                                    except Exception:
                                        pass
                            return (None, pyaudio.paContinue)

                        return _lb_cb

                    try:
                        st = self._pa.open(
                            format=pyaudio.paInt16,
                            channels=lb_dev.channels,
                            rate=lb_dev.sample_rate,
                            input=True,
                            input_device_index=lb_dev.index,
                            frames_per_buffer=lb_fpb,
                            stream_callback=_make_lb_cb(lb_dev.index),
                        )
                        st.start_stream()
                        self._loop_streams.append((lb_dev, st))
                    except Exception as lb_exc:
                        logger.debug("Loopback '%s' skipped: %s", lb_dev.name, lb_exc)

                if self._mic_stream is None and not self._loop_streams:
                    raise RuntimeError("Could not open microphone or system audio stream.")

                self._start_time = time.perf_counter()
                self._is_recording = True
                mic_name = mic_dev.name if self._mic_stream and mic_dev else "Off"
                sys_count = len(self._loop_streams)
                logger.info(
                    "Started dictation capture (mic='%s', loopback_endpoints=%d)",
                    mic_name,
                    sys_count,
                )
                status_desc = (
                    f"Listening on {mic_name} + System Audio"
                    if sys_count > 0
                    else f"Listening on {mic_name}"
                )
                return True, status_desc
            except Exception as exc:
                self._cleanup_resources()
                msg = f"Failed to start audio capture: {exc}"
                logger.error(msg)
                if self.on_error:
                    self.on_error(msg)
                return False, msg

    def stop(self) -> Tuple[np.ndarray, float]:
        """
        Stop capture, release all audio devices, and return `(audio_16k_float32, duration_sec)`.
        Also populates `self.last_mic_16k` and `self.last_sys_16k` with normalized 16kHz tracks.
        """
        with self._lock:
            if not self._is_recording:
                return np.zeros(0, dtype=np.float32), 0.0

            self._is_recording = False
            duration = max(0.0, time.perf_counter() - self._start_time)
            raw_mic_bytes = b"".join(self._mic_frames)
            mic_dev = self._mic_device

            raw_loop_list: List[Tuple[AudioDeviceInfo, bytes]] = []
            for lb_dev, _ in self._loop_streams:
                chunks = self._loop_frames.get(lb_dev.index, [])
                if chunks:
                    raw_loop_bytes = b"".join(chunks)
                    if raw_loop_bytes:
                        raw_loop_list.append((lb_dev, raw_loop_bytes))

            self._mic_frames.clear()
            self._loop_frames.clear()
            self._cleanup_resources()

        if self.on_level_change:
            try:
                self.on_level_change(0.0)
            except Exception:
                pass

        # 1. Resample Microphone Track & Apply Digital Gain
        mic_16k = np.zeros(0, dtype=np.float32)
        if raw_mic_bytes and mic_dev:
            pcm_i16 = np.frombuffer(raw_mic_bytes, dtype=np.int16)
            mic_16k = resample_audio_to_16k_mono(pcm_i16, mic_dev.sample_rate, mic_dev.channels)
            if self.mic_gain != 1.0 and mic_16k.size > 0:
                mic_16k = np.clip(mic_16k * float(self.mic_gain), -1.0, 1.0)

        # 2. Resample & Combine Active System Audio Loopback Track(s)
        sys_16k_tracks: List[np.ndarray] = []
        for lb_dev, lb_bytes in raw_loop_list:
            pcm_lb = np.frombuffer(lb_bytes, dtype=np.int16)
            arr_16k = resample_audio_to_16k_mono(pcm_lb, lb_dev.sample_rate, lb_dev.channels)
            if arr_16k.size > 0 and float(np.max(np.abs(arr_16k))) >= 0.0001:
                sys_16k_tracks.append(arr_16k)

        sys_16k = np.zeros(0, dtype=np.float32)
        if sys_16k_tracks:
            max_sys_len = max(t.size for t in sys_16k_tracks)
            accum = np.zeros(max_sys_len, dtype=np.float32)
            for t in sys_16k_tracks:
                accum[: t.size] += normalize_speech_audio(t)
            sys_16k = np.clip(accum, -1.0, 1.0)

        self.last_mic_16k = mic_16k
        self.last_sys_16k = sys_16k

        # 3. Smart Selection / Mixing of Mic + System Audio
        mic_peak = float(np.max(np.abs(mic_16k))) if mic_16k.size > 0 else 0.0
        sys_peak = float(np.max(np.abs(sys_16k))) if sys_16k.size > 0 else 0.0

        # Check if mic has actual voice vs ambient room floor, and if system has video/meeting speech
        mic_has_voice = mic_peak >= 0.0004
        sys_has_voice = sys_peak >= 0.0004

        if sys_has_voice and not mic_has_voice:
            # Video or meeting is playing in system audio while user is silent on mic!
            final_16k = normalize_speech_audio(sys_16k)
        elif mic_has_voice and not sys_has_voice:
            final_16k = normalize_speech_audio(mic_16k)
        elif mic_has_voice and sys_has_voice:
            norm_mic = normalize_speech_audio(mic_16k)
            norm_sys = normalize_speech_audio(sys_16k)
            target_len = max(norm_mic.size, norm_sys.size)
            if norm_mic.size < target_len:
                norm_mic = np.pad(norm_mic, (0, target_len - norm_mic.size))
            if norm_sys.size < target_len:
                norm_sys = np.pad(norm_sys, (0, target_len - norm_sys.size))
            final_16k = np.clip(norm_mic * 0.75 + norm_sys * 0.75, -1.0, 1.0)
        else:
            # Fallback to whichever buffer is non-empty with normalization
            chosen = mic_16k if mic_16k.size >= sys_16k.size else sys_16k
            final_16k = normalize_speech_audio(chosen) if chosen.size > 0 else chosen

        logger.info(
            "Stopped dictation capture (duration=%.2fs, mic_samples=%d [peak=%.4f], sys_samples=%d [peak=%.4f])",
            duration,
            mic_16k.size,
            mic_peak,
            sys_16k.size,
            sys_peak,
        )
        return final_16k, duration

    def _cleanup_resources(self) -> None:
        if self._mic_stream is not None:
            try:
                if self._mic_stream.is_active():
                    self._mic_stream.stop_stream()
            except Exception:
                pass
            try:
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        for _, st in self._loop_streams:
            try:
                if st.is_active():
                    st.stop_stream()
            except Exception:
                pass
            try:
                st.close()
            except Exception:
                pass
        self._loop_streams.clear()

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

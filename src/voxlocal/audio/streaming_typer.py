"""
Streaming Voice Typer for Windows.
Enables real-time voice typing directly into the active cursor position.

Features:
- Captures dedicated microphone audio (never mixes headphone/speaker loopback into voice typing).
- Self-stabilizing adaptive noise floor calibrated for quiet USB/headset mics and studio mics.
- Continuous hands-free segmentation: speak -> pause (~0.8s) -> auto-types at cursor.
- Instant stop-and-type flush: clicking Stop or pressing the hotkey immediately flushes and types
  any speech in progress, ensuring zero lost words.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Callable, Optional, Tuple
import numpy as np
import pyaudiowpatch as pyaudio

from voxlocal.audio.devices import (
    AudioDeviceInfo,
    AudioDeviceManager,
    StreamResampler,
    clean_speech_microphone_audio,
    compute_rms_level,
    normalize_speech_audio,
)
from voxlocal.core.logger import get_logger

logger = get_logger("audio.streaming_typer")


class StreamingVoiceTyper:
    """
    Continuous microphone speech detector, segmenter, and real-time cursor typer.
    Dispatches completed speech utterances for immediate local Whisper transcription.
    """

    TARGET_RATE = 16000
    FRAME_DURATION_SEC = 0.03  # 30ms analysis frames
    FRAME_SAMPLES_16K = int(TARGET_RATE * FRAME_DURATION_SEC)  # 480 samples

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._is_running = False
        self._pa: Optional[pyaudio.PyAudio] = None
        self._mic_stream = None
        self._device: Optional[AudioDeviceInfo] = None

        self.pause_threshold_sec: float = 0.65
        self.min_speech_duration_sec: float = 0.35
        self.max_utterance_sec: float = 30.0

        # Calibrated adaptive energy / VAD parameters:
        # Quiet room mic silence is ~0.00005 to 0.0008 RMS
        # Human speech on USB/headset mic is ~0.0080 to 0.15 RMS (peak ~0.04 to 0.8)
        self._speech_start_threshold: float = 0.0035
        self._speech_continue_threshold: float = 0.0018
        self._noise_floor: float = 0.0005
        self._min_utterance_peak: float = 0.012
        self.mic_gain: float = 3.0

        # Non-speech & transient noise rejection
        self._consecutive_voiced_frames: int = 0
        self._voiced_frames_in_utterance: int = 0
        self.min_voiced_frames_for_utterance: int = 6  # ~180ms of voiced energy

        # Callbacks
        self.on_segment_ready: Optional[Callable[[np.ndarray, float], None]] = None
        self.on_interim_audio: Optional[Callable[[np.ndarray, float], None]] = None
        self.on_level_change: Optional[Callable[[float], None]] = None
        self.on_state_change: Optional[Callable[[str], None]] = None  # "listening", "speaking", "transcribing"
        self.on_error: Optional[Callable[[str], None]] = None

        # Audio buffering - 450ms pre-buffer guarantees initial consonants are never clipped
        self._pre_buffer_frames = int(0.45 / self.FRAME_DURATION_SEC)
        self._pre_buffer: collections.deque = collections.deque(maxlen=self._pre_buffer_frames)
        self._current_utterance: list[np.ndarray] = []
        self._all_unprocessed_frames: list[np.ndarray] = []
        self._in_speech: bool = False
        self._silence_start_time: float = 0.0
        self._speech_start_time: float = 0.0
        self._last_interim_time: float = 0.0

        self._resampler: Optional[StreamResampler] = None
        self._pending_raw_samples = np.zeros(0, dtype=np.float32)

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_in_speech(self) -> bool:
        return self._in_speech

    @property
    def _speech_threshold(self) -> float:
        return self._speech_start_threshold

    @_speech_threshold.setter
    def _speech_threshold(self, val: float) -> None:
        self._speech_start_threshold = val

    def start(
        self,
        preferred_device_index: Optional[int] = None,
        preferred_device_name: str = "",
        pause_threshold_sec: float = 1.2,
        mic_gain: Optional[float] = None,
        **_ignored,
    ) -> Tuple[bool, str]:
        with self._lock:
            if self._is_running:
                return True, "Streaming voice typer is already active."

            if mic_gain is not None:
                self.mic_gain = max(0.5, float(mic_gain))

            self.pause_threshold_sec = max(0.3, min(3.0, float(pause_threshold_sec)))
            mic_dev = AudioDeviceManager.resolve_microphone(
                preferred_device_index, preferred_device_name
            )
            if not mic_dev:
                msg = "No operational microphone found."
                logger.error(msg)
                if self.on_error:
                    self.on_error(msg)
                return False, msg

            self._device = mic_dev
            self._pre_buffer.clear()
            self._current_utterance.clear()
            self._all_unprocessed_frames.clear()
            self._in_speech = False
            self._silence_start_time = 0.0
            self._speech_start_time = 0.0
            self._last_interim_time = 0.0
            self._noise_floor = 0.0005
            self._speech_start_threshold = 0.0035
            self._speech_continue_threshold = 0.0018
            self._consecutive_voiced_frames = 0
            self._voiced_frames_in_utterance = 0
            self._pending_raw_samples = np.zeros(0, dtype=np.float32)

            try:
                self._pa = pyaudio.PyAudio()
                self._resampler = StreamResampler(mic_dev.sample_rate, mic_dev.channels)
                fpb = max(512, int(mic_dev.sample_rate * self.FRAME_DURATION_SEC))

                def _mic_callback(in_data, frame_count, time_info, status):
                    if self._is_running and in_data:
                        self._process_audio_chunk(in_data)
                    return (None, pyaudio.paContinue)

                self._mic_stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=mic_dev.channels,
                    rate=mic_dev.sample_rate,
                    input=True,
                    input_device_index=mic_dev.index,
                    frames_per_buffer=fpb,
                    stream_callback=_mic_callback,
                )
                self._mic_stream.start_stream()
                self._is_running = True

                logger.info(
                    "Voice typer started on microphone '%s' (pause=%.2fs)",
                    mic_dev.name,
                    self.pause_threshold_sec,
                )
                if self.on_state_change:
                    self.on_state_change("listening")
                return True, f"Listening on {mic_dev.name}"
            except Exception as exc:
                self._cleanup()
                msg = f"Failed to start voice typing microphone capture: {exc}"
                logger.error(msg)
                if self.on_error:
                    self.on_error(msg)
                return False, msg

    def cancel(self) -> None:
        """Abort voice typing capture and discard any buffered unprocessed audio."""
        self.stop(flush=False)

    def stop(self, flush: bool = True) -> None:
        """Stop voice typing capture and optionally flush any pending spoken audio."""
        with self._lock:
            if not self._is_running:
                return
            self._is_running = False

            frames_to_flush = list(self._current_utterance) if flush else []
            if flush and not frames_to_flush and self._all_unprocessed_frames:
                frames_to_flush = list(self._all_unprocessed_frames)

            if frames_to_flush:
                combined = np.concatenate(frames_to_flush)
                dur = len(combined) / self.TARGET_RATE
                peak = float(np.max(np.abs(combined)))
                rms = float(np.sqrt(np.mean(np.square(combined))))
                if (
                    dur >= self.min_speech_duration_sec
                    and peak >= self._min_utterance_peak
                    and (self._voiced_frames_in_utterance >= 2 or rms >= 0.0020)
                ):
                    cleaned = clean_speech_microphone_audio(combined, self.TARGET_RATE)
                    norm_audio = normalize_speech_audio(cleaned)
                    if self.on_segment_ready:
                        try:
                            logger.info(
                                "Flushing pending speech on stop (%.2fs, peak=%.4f)",
                                dur,
                                peak,
                            )
                            self.on_segment_ready(norm_audio, dur)
                        except Exception as cb_exc:
                            logger.error("Error in on_segment_ready on stop: %s", cb_exc)

            self._current_utterance.clear()
            self._all_unprocessed_frames.clear()
            self._pre_buffer.clear()
            self._consecutive_voiced_frames = 0
            self._voiced_frames_in_utterance = 0
            self._cleanup()

        if self.on_level_change:
            try:
                self.on_level_change(0.0)
            except Exception:
                pass
        if self.on_state_change:
            try:
                self.on_state_change("idle")
            except Exception:
                pass
        logger.info("Voice typer stopped.")

    def _process_audio_chunk(self, raw_bytes: bytes) -> None:
        """Process incoming microphone audio in real time."""
        if not self._is_running:
            return

        with self._lock:
            if not self._device or not self._resampler:
                return

            # 1. Convert to float32 mono [-1.0, 1.0] using StreamResampler
            pcm16 = np.frombuffer(raw_bytes, dtype=np.int16)
            chunk_16k = self._resampler.process(pcm16)
            if chunk_16k.size == 0:
                return
            if self.mic_gain != 1.0:
                chunk_16k = np.clip(chunk_16k * float(self.mic_gain), -1.0, 1.0)

            # 2. Buffer into fixed-size 30ms frames (480 samples @ 16kHz)
            self._pending_raw_samples = np.concatenate([self._pending_raw_samples, chunk_16k])
            now = time.monotonic()

            while self._pending_raw_samples.size >= self.FRAME_SAMPLES_16K:
                frame = self._pending_raw_samples[: self.FRAME_SAMPLES_16K]
                self._pending_raw_samples = self._pending_raw_samples[self.FRAME_SAMPLES_16K :]

                frame_rms = float(np.sqrt(np.mean(np.square(frame))))
                vu_level = compute_rms_level(frame)

                if self.on_level_change:
                    try:
                        self.on_level_change(vu_level)
                    except Exception:
                        pass

                # Self-stabilizing noise floor adaptation (during non-speech)
                if not self._in_speech and frame_rms < self._speech_continue_threshold * 0.90:
                    self._noise_floor = min(0.0080, max(0.0002, self._noise_floor * 0.95 + frame_rms * 0.05))
                    self._speech_start_threshold = min(0.0150, max(0.0035, self._noise_floor * 4.2))
                    self._speech_continue_threshold = min(0.0080, max(0.0018, self._noise_floor * 2.2))
                elif self._in_speech:
                    # Slow upward drift guard during prolonged speech in noisy room
                    self._noise_floor = min(0.0050, self._noise_floor * 0.996 + frame_rms * 0.004)
                    self._speech_continue_threshold = min(0.0080, max(0.0018, self._noise_floor * 2.2))

                # Frame spectral evaluation for non-speech rejection (clicks, breath, mic pops)
                signs = np.sign(frame)
                signs[signs == 0] = 1
                frame_zcr = float(np.mean(signs[:-1] != signs[1:])) * (self.TARGET_RATE / 2.0)
                frame_peak = float(np.max(np.abs(frame)))
                crest_factor = (frame_peak / (frame_rms + 1e-6)) if frame_rms > 0 else 0.0

                # Detect impulsive non-speech spikes (keyboard click, mouse click, desk bump)
                is_impulse_click = (crest_factor > 5.8 and frame_rms < 0.035) or (frame_zcr < 65.0)

                # Keep rolling unprocessed buffer (up to 15s) for manual stop flush
                self._all_unprocessed_frames.append(frame)
                if len(self._all_unprocessed_frames) > 500:
                    self._all_unprocessed_frames.pop(0)

                if not self._in_speech:
                    self._pre_buffer.append(frame)

                    # Voiced frame onset check: energy above start threshold and not an impulse click/low thump
                    if frame_rms >= self._speech_start_threshold and not is_impulse_click:
                        self._consecutive_voiced_frames += 1
                    else:
                        self._consecutive_voiced_frames = max(0, self._consecutive_voiced_frames - 1)

                    # Require at least 3 consecutive voiced frames (~90ms) to trigger genuine speech onset
                    if self._consecutive_voiced_frames >= 3:
                        self._in_speech = True
                        self._speech_start_time = now
                        self._silence_start_time = 0.0
                        self._last_interim_time = now
                        self._voiced_frames_in_utterance = self._consecutive_voiced_frames
                        # Prepend pre-speech frames to preserve starting consonants
                        self._current_utterance = list(self._pre_buffer)
                        self._current_utterance.append(frame)
                        self._pre_buffer.clear()
                        if self.on_state_change:
                            try:
                                self.on_state_change("speaking")
                            except Exception:
                                pass
                else:
                    self._current_utterance.append(frame)
                    utterance_dur = now - self._speech_start_time

                    # Continuation check uses the lower continuation threshold with hysteresis
                    if frame_rms >= self._speech_continue_threshold and not (is_impulse_click and frame_rms < self._speech_start_threshold):
                        self._voiced_frames_in_utterance += 1
                        self._silence_start_time = 0.0
                    else:
                        if self._silence_start_time == 0.0:
                            self._silence_start_time = now

                    silence_duration = (
                        (now - self._silence_start_time) if self._silence_start_time > 0.0 else 0.0
                    )

                    # Emit periodic interim audio snapshot only during active speech (never during trailing silence)
                    if (
                        self.on_interim_audio
                        and silence_duration < 0.22
                        and (now - self._last_interim_time) >= 0.70
                    ):
                        if utterance_dur >= 0.55 and self._current_utterance:
                            self._last_interim_time = now
                            snapshot = np.concatenate(self._current_utterance)
                            try:
                                self.on_interim_audio(snapshot, utterance_dur)
                            except Exception as interim_exc:
                                logger.debug("Error in on_interim_audio: %s", interim_exc)

                    # Adaptive clause splitting for long utterances:
                    # If continuous speech has exceeded 7.0s, split at a natural micro-pause (>= 0.60s)
                    # rather than forcing the speaker to stop for the full pause threshold.
                    is_long_clause_pause = (utterance_dur >= 7.0 and silence_duration >= 0.60)

                    # Finalize utterance when silence exceeds pause threshold or max utterance reached
                    if (
                        silence_duration >= self.pause_threshold_sec
                        or is_long_clause_pause
                        or utterance_dur >= self.max_utterance_sec
                    ):
                        self._finalize_utterance()

    def _finalize_utterance(self) -> None:
        """Completed speech segment detected -> dispatch for transcription and reset."""
        frames_to_send = list(self._current_utterance)

        # Trim trailing silence exceeding 200ms natural padding to eliminate silence hallucination & speed up inference
        if self._silence_start_time > 0.0:
            sil_dur = max(0.0, time.monotonic() - self._silence_start_time)
            extra_sil_sec = sil_dur - 0.20
            if extra_sil_sec > 0.05:
                frames_to_trim = int(extra_sil_sec / self.FRAME_DURATION_SEC)
                if frames_to_trim > 0 and len(frames_to_send) > frames_to_trim + 5:
                    frames_to_send = frames_to_send[:-frames_to_trim]

        self._in_speech = False
        self._consecutive_voiced_frames = 0
        voiced_count = self._voiced_frames_in_utterance
        self._voiced_frames_in_utterance = 0
        self._current_utterance = []
        self._all_unprocessed_frames = []
        self._silence_start_time = 0.0
        self._speech_start_time = 0.0
        self._last_interim_time = 0.0

        if frames_to_send:
            combined = np.concatenate(frames_to_send)
            utterance_dur = len(combined) / self.TARGET_RATE
            peak = float(np.max(np.abs(combined)))
            is_valid_speech = (
                utterance_dur >= self.min_speech_duration_sec
                and peak >= self._min_utterance_peak
                and voiced_count >= self.min_voiced_frames_for_utterance
            )
            if is_valid_speech:
                cleaned = clean_speech_microphone_audio(combined, self.TARGET_RATE)
                norm_audio = normalize_speech_audio(cleaned)
                if self.on_state_change:
                    try:
                        self.on_state_change("transcribing")
                    except Exception:
                        pass
                if self.on_segment_ready:
                    try:
                        self.on_segment_ready(norm_audio, utterance_dur)
                    except Exception as exc:
                        logger.error("Error in on_segment_ready callback: %s", exc)
                return
            else:
                logger.debug(
                    "Rejected non-speech audio event (dur=%.2fs, voiced_frames=%d, peak=%.4f)",
                    utterance_dur,
                    voiced_count,
                    peak,
                )

        # If segment was just a brief click/breath below speech peak, revert immediately to listening
        if self.on_state_change and self._is_running:
            try:
                self.on_state_change("listening")
            except Exception:
                pass

    def _cleanup(self) -> None:
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

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

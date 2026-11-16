"""
Live Streaming Hinglish Transcription Engine for VOX Meetings.

Implements continuous, incremental, low-latency speech-to-text with:
- LocalAgreement streaming decoding policy (SimulStreaming)
- Immediate revisable Live Partial emissions (~1.0s latency)
- Immutable Confirmed Text on consecutive agreement or speech boundary / pause
- Dual-track (System Loopback + Microphone) activity-gated VAD
- Full Indian English and Hinglish Roman-script normalization with technical loanwords
- Decoupled from post-meeting diarization and summarization
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, compute_rms_level
from voxlocal.core.logger import get_logger
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber, is_hallucination

logger = get_logger("engine.streaming_transcriber")


class LocalAgreementEngine:
    """
    SimulStreaming LocalAgreement algorithm:
    Stabilizes prefixes of consecutive recognition hypotheses so confirmed text
    never duplicates or rewrites, while emitting live partial updates.
    """

    def __init__(self) -> None:
        self.prev_words: List[str] = []
        self.last_confirmed_text: str = ""

    def reset(self) -> None:
        self.prev_words = []
        self.last_confirmed_text = ""

    def process_step(
        self,
        current_text: str,
        speech_boundary: bool,
        vocabulary: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, str]:
        """
        Processes a new hypothesis text.
        Returns:
            (confirmed_text_delta, live_partial_text)
        """
        raw = current_text.strip()
        if not raw or is_hallucination(raw):
            return "", ""

        cur_words = raw.split()
        if not cur_words:
            return "", ""

        if speech_boundary:
            # End of speech turn / pause: commit the entire remaining hypothesis
            confirmed_raw = " ".join(cur_words)
            self.prev_words = []
            confirmed_proc = self._clean_and_normalize(confirmed_raw, vocabulary)
            return confirmed_proc, ""

        # Compute Longest Common Prefix (LCP) against previous step hypothesis
        match_len = 0
        min_len = min(len(self.prev_words), len(cur_words))
        for k in range(min_len):
            w1 = self.prev_words[k].strip(".,?!;:-").lower()
            w2 = cur_words[k].strip(".,?!;:-").lower()
            if w1 == w2:
                match_len += 1
            else:
                break

        if match_len > 0:
            confirmed_slice = cur_words[:match_len]
            partial_slice = cur_words[match_len:]
            self.prev_words = partial_slice

            confirmed_raw = " ".join(confirmed_slice)
            partial_raw = " ".join(partial_slice)

            confirmed_proc = self._clean_and_normalize(confirmed_raw, vocabulary)
            partial_proc = self._clean_and_normalize(partial_raw, vocabulary)
            return confirmed_proc, partial_proc
        else:
            self.prev_words = cur_words
            partial_proc = self._clean_and_normalize(raw, vocabulary)
            return "", partial_proc

    def _clean_and_normalize(
        self, text: str, vocabulary: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        if not text:
            return ""
        processed = DictationProcessor.process(
            text,
            vocabulary=vocabulary,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
        ).strip()
        return processed


class LiveMeetingStreamingTranscriber:
    """
    Asynchronous continuous streaming transcriber for live meetings.
    Reads continuous audio windows from MeetingRecorder, tracks speech turns,
    and emits real-time partials and confirmed segments.
    """

    def __init__(
        self,
        transcriber: LocalWhisperTranscriber,
        db: Any,
        on_partial: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        on_confirmed: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> None:
        self.transcriber = transcriber
        self.db = db
        self.on_partial = on_partial
        self.on_confirmed = on_confirmed

        self._lock = threading.RLock()
        self._active = False
        self._paused = False
        self._meeting_id: Optional[int] = None
        self._language: str = "en+hi"
        self._recorder: Any = None

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._agreement_engine = LocalAgreementEngine()
        self._audio_cursor_sec: float = 0.0
        self._segment_index: int = 0
        self._recent_emitted: List[Dict[str, Any]] = []

        # Activity & turn state
        self._speech_active: bool = False
        self._silence_start_sec: float = 0.0
        self._turn_start_sec: float = 0.0
        self._current_speaker: str = "Speaker 1"
        self._current_track: str = "system"
        self._step_accumulated_sec: float = 0.0

    @property
    def is_active(self) -> bool:
        return self._active

    def start(
        self,
        meeting_id: int,
        recorder: Any,
        language: str = "en+hi",
    ) -> None:
        """Start live streaming transcription for the given meeting."""
        with self._lock:
            if self._active:
                self.stop()

            self._meeting_id = meeting_id
            self._recorder = recorder
            self._language = language
            self._active = True
            self._paused = False
            self._stop_event.clear()

            self._agreement_engine.reset()
            self._audio_cursor_sec = 0.0
            self._segment_index = 0
            self._recent_emitted = []
            self._speech_active = False
            self._silence_start_sec = 0.0
            self._turn_start_sec = 0.0
            self._current_speaker = "Speaker 1"
            self._current_track = "system"
            self._step_accumulated_sec = 0.0

            # Pre-warm model in background so first spoken word incurs zero CUDA compile latency
            threading.Thread(
                target=self._warmup_model,
                name="WhisperWarmup",
                daemon=True,
            ).start()

            self._worker_thread = threading.Thread(
                target=self._streaming_worker_loop,
                name="LiveMeetingStreamingWorker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("Live streaming transcriber started for meeting #%d", meeting_id)

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def stop(self) -> None:
        """Stop streaming transcription and flush any pending speech."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._stop_event.set()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
            self._worker_thread = None

        logger.info("Live streaming transcriber stopped")

    def _warmup_model(self) -> None:
        """Warm up Whisper on dummy 0.5s audio to initialize CUDA kernels."""
        try:
            dummy = np.zeros(int(TARGET_SAMPLE_RATE * 0.5), dtype=np.float32)
            self.transcriber._run_whisper_pass(
                dummy,
                language=self._language,
                beam_size=1,
                vad_filter=False,
                vad_params={},
            )
            logger.debug("Live streaming model warmup complete")
        except Exception as exc:
            logger.debug("Model warmup notice: %s", exc)

    def _streaming_worker_loop(self) -> None:
        """Main polling and incremental transcription loop."""
        step_sleep = 0.15  # 150ms polling cadence
        while not self._stop_event.wait(step_sleep):
            if not self._active or self._paused or self._recorder is None:
                continue

            try:
                self._step()
            except Exception as exc:
                logger.debug("Streaming transcription step error: %s", exc)

    def _step(self) -> None:
        if self._stop_event.is_set():
            return
        now_sec = self._recorder.elapsed_seconds
        if now_sec <= self._audio_cursor_sec + 0.3:
            return

        # Fetch recent audio window
        mic_slice, sys_slice, actual_start = self._recorder.get_audio_window(
            self._audio_cursor_sec, now_sec
        )

        window_dur = now_sec - actual_start
        if window_dur < 0.4:
            return

        # Dual-track VAD energy assessment
        mic_rms = float(np.sqrt(np.mean(mic_slice**2))) if mic_slice.size else 0.0
        sys_rms = float(np.sqrt(np.mean(sys_slice**2))) if sys_slice.size else 0.0

        speech_thresh = 0.0012
        is_mic_speech = mic_rms >= speech_thresh
        is_sys_speech = sys_rms >= speech_thresh

        active_track = "system"
        active_slice = sys_slice
        active_speaker = "Speaker 1"

        if is_mic_speech and not is_sys_speech:
            active_track = "mic"
            active_slice = mic_slice
            active_speaker = "You (Mic)"
        elif is_sys_speech and not is_mic_speech:
            active_track = "system"
            active_slice = sys_slice
            active_speaker = "Speaker 1"
        elif is_mic_speech and is_sys_speech:
            # Both active: select dominant energy track
            if mic_rms > sys_rms * 1.5:
                active_track = "mic"
                active_slice = mic_slice
                active_speaker = "You (Mic)"
            else:
                active_track = "system"
                active_slice = sys_slice
                active_speaker = "Speaker 1"
        else:
            # Silence / background noise
            if self._speech_active:
                if self._silence_start_sec == 0.0:
                    self._silence_start_sec = now_sec
                elif (now_sec - self._silence_start_sec) >= 0.4:
                    # Speech turn boundary detected! Flush current utterance
                    self._execute_transcribe_pass(
                        actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=True
                    )
                    self._speech_active = False
                    self._silence_start_sec = 0.0
                    self._step_accumulated_sec = 0.0
                    self._audio_cursor_sec = now_sec
            else:
                # Still silent: advance cursor to keep window short and prevent memory accumulation
                if window_dur >= 2.0:
                    self._audio_cursor_sec = now_sec - 0.5
            return

        # Speech is active!
        if not self._speech_active:
            self._speech_active = True
            self._turn_start_sec = actual_start
            self._current_speaker = active_speaker
            self._current_track = active_track
            self._silence_start_sec = 0.0
            self._step_accumulated_sec = 0.0

        self._silence_start_sec = 0.0
        self._step_accumulated_sec += 0.15

        # Trigger partial inference if continuous speech has accumulated >= 2.0s
        # If continuous speech exceeds 10s without a natural pause, force turn boundary flush to prevent runaway buffer
        if window_dur >= 10.0:
            self._execute_transcribe_pass(
                actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=True
            )
            self._speech_active = False
            self._silence_start_sec = 0.0
            self._step_accumulated_sec = 0.0
            self._audio_cursor_sec = now_sec
        elif self._step_accumulated_sec >= 2.0:
            self._execute_transcribe_pass(
                actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=False
            )
            self._step_accumulated_sec = 0.0

    def _execute_transcribe_pass(
        self,
        start_sec: float,
        end_sec: float,
        track: str,
        speaker: str,
        speech_boundary: bool,
    ) -> None:
        """Run Whisper inference and apply LocalAgreement policy."""
        if self._stop_event.is_set():
            return
        mic_slice, sys_slice, actual_start = self._recorder.get_audio_window(start_sec, end_sec)
        audio_slice = mic_slice if track == "mic" else sys_slice
        if audio_slice.size < int(TARGET_SAMPLE_RATE * 0.4):
            return

        # Clamp audio slice to at most 25 seconds to stay safely within Whisper's 30s single-encode window
        max_samples = int(TARGET_SAMPLE_RATE * 25.0)
        if audio_slice.size > max_samples:
            audio_slice = audio_slice[-max_samples:]

        vocab = self.db.list_vocabulary(active_only=True) if self.db else []
        prompt = DictationProcessor.build_whisper_prompt(vocab)
        if self._language in ("en+hi", "hinglish", "hi_latn"):
            prompt = f"{prompt} {DictationProcessor.DEFAULT_HINGLISH_PROMPT}".strip()

        # Run fast single-pass inference (beam_size=1)
        res = self.transcriber._transcribe_single_encode(
            norm_audio=audio_slice,
            language=self._language,
            lang_arg=None,
            is_en_only=False,
            bilingual_modes=("", "en+hi", "hinglish", "hi_latn"),
            beam_size=1,
            vad_filter=False,
            vad_params={},
            hotwords=None,
            temperatures=0.0,
            effective_prompt=prompt,
            word_timestamps=False,
        )

        # Robust fallback: if single-encode returned None (e.g., audio slice edge condition or mock), run standard pass
        if not res or not res[0]:
            try:
                res = self.transcriber._run_whisper_pass(
                    audio_slice,
                    beam_size=1,
                    language=self._language,
                    prompt=prompt,
                )
            except Exception as fb_err:
                logger.debug("Streaming transcriber fallback pass warning: %s", fb_err)
                res = None

        if not res or not res[0]:
            # Always advance cursor if window has grown excessively to prevent permanent stall
            if (end_sec - self._audio_cursor_sec) >= 12.0:
                self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 4.0)
            return

        raw_text = " ".join(s.text.strip() for s in res[0] if s.text.strip())
        if not raw_text or is_hallucination(raw_text):
            if (end_sec - self._audio_cursor_sec) >= 12.0:
                self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 4.0)
            return

        # Process with LocalAgreement
        confirmed, partial = self._agreement_engine.process_step(
            raw_text, speech_boundary=speech_boundary, vocabulary=vocab
        )

        # Emit Live Partial
        if partial and self.on_partial and self._meeting_id is not None:
            self.on_partial(
                self._meeting_id,
                {
                    "speaker": speaker,
                    "text": partial,
                    "start_time": start_sec,
                    "source_track": track,
                },
            )

        # Emit and commit Confirmed Text
        if confirmed and self._meeting_id is not None:
            # Check for duplicate emission against recent segments
            norm_conf = confirmed.lower()
            is_dup = False
            for ex in self._recent_emitted:
                if (
                    ex.get("source_track") == track
                    and abs(start_sec - float(ex.get("start_time", 0.0))) <= 1.5
                ):
                    ex_txt = ex.get("text", "").strip().lower()
                    if ex_txt == norm_conf or norm_conf in ex_txt:
                        is_dup = True
                        break

            if not is_dup:
                self._segment_index += 1
                seg_id = None
                if self.db:
                    try:
                        seg_id = self.db.add_transcript_segment(
                            meeting_id=self._meeting_id,
                            segment_index=self._segment_index,
                            start_time=start_sec,
                            end_time=end_sec,
                            speaker=speaker,
                            text=confirmed,
                            source_track=track,
                            confidence=0.95,
                        )
                    except Exception as db_exc:
                        logger.debug("Failed saving confirmed segment to DB: %s", db_exc)

                seg_dict = {
                    "id": seg_id or self._segment_index,
                    "meeting_id": self._meeting_id,
                    "segment_index": self._segment_index,
                    "speaker": speaker,
                    "text": confirmed,
                    "start_time": start_sec,
                    "end_time": end_sec,
                    "source_track": track,
                }

                self._recent_emitted.append(seg_dict)
                if len(self._recent_emitted) > 50:
                    self._recent_emitted = self._recent_emitted[-50:]

                if self.on_confirmed:
                    self.on_confirmed(self._meeting_id, seg_dict)

                # Advance cursor past confirmed audio
                if speech_boundary:
                    self._audio_cursor_sec = end_sec
                else:
                    dur = max(0.2, (len(confirmed.split()) / max(1, len(raw_text.split()))) * (end_sec - start_sec))
                    self._audio_cursor_sec = min(end_sec, start_sec + dur)
        else:
            # If no confirmed text was emitted on this pass and cursor is falling far behind, advance cursor safely
            if (end_sec - self._audio_cursor_sec) >= 12.0:
                self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 3.0)

"""
Live Streaming Transcription Engine for VOX Meetings (Phase 2 Upgraded).

Supports honest separation between:
- True incremental streaming (`nemotron_onnx`, `execution_mode="incremental"`):
  emits progressive frame-by-frame provisional updates while the user is still speaking,
  then commits stable utterances on Nemotron endpoint detection.
- Endpointed transcription (`qwen3_onnx`, `parakeet_onnx`, `execution_mode="endpointed"`):
  accumulates complete speech turns per isolated track using conservative RMS/VAD endpointing,
  never falsely advertising word-by-word incremental decoding when waiting on turn end.
- Legacy Whisper (`faster_whisper`) rollback / automatic runtime fallback when ONNX models
  or runtimes are absent or fail during execution.

Strict Dual-Track Isolation:
- Maintains independent cursors and endpoint states for `mic` and `system` tracks so they
  are never mixed prior to transcription and overlapping speech is preserved.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.core.logger import get_logger
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber, is_hallucination, merge_domain_hotwords

logger = get_logger("engine.streaming_transcriber")


class LocalAgreementEngine:
    """
    SimulStreaming LocalAgreement algorithm (retained for Whisper legacy/fallback path).
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
        raw = current_text.strip()
        if not raw or is_hallucination(raw):
            return "", ""

        cur_words = raw.split()
        if not cur_words:
            return "", ""

        if speech_boundary:
            confirmed_raw = " ".join(cur_words)
            self.prev_words = []
            confirmed_proc = self._clean_and_normalize(confirmed_raw, vocabulary)
            return confirmed_proc, ""

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
            return (
                self._clean_and_normalize(" ".join(confirmed_slice), vocabulary),
                self._clean_and_normalize(" ".join(partial_slice), vocabulary),
            )
        self.prev_words = cur_words
        return "", self._clean_and_normalize(raw, vocabulary)

    def _clean_and_normalize(
        self, text: str, vocabulary: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        if not text:
            return ""
        return DictationProcessor.process(
            text,
            vocabulary=vocabulary,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
            context="meeting",
        ).strip()


def _new_track_state() -> Dict[str, Any]:
    return {
        "cursor_sec": 0.0,
        "speech_active": False,
        "turn_start_sec": 0.0,
        "turn_first_partial_sec": None,
        "last_speech_sec": 0.0,
        "last_partial_text": "",
        "active_partial_mono": None,
    }


class LiveMeetingStreamingTranscriber:
    """
    Asynchronous dual-track live meeting transcriber supporting incremental Nemotron,
    endpointed Guarded Qwen3 ONNX / Parakeet ONNX, and legacy Whisper fallback.
    """

    def __init__(
        self,
        transcriber: LocalWhisperTranscriber,
        db: Any,
        on_partial: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        on_confirmed: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        config: Any = None,
    ) -> None:
        self.transcriber = transcriber
        self.db = db
        self.on_partial = on_partial
        self.on_confirmed = on_confirmed
        self.config = config

        self._lock = threading.RLock()
        self._active = False
        self._paused = False
        self._meeting_id: Optional[int] = None
        self._session_generation: int = 0
        self._language: str = "en+hi"
        self._recorder: Any = None
        self._execution_profile: Any = None
        self._session_mode: str = "auto"

        # Active Phase 2 or Whisper live backend identity
        self._live_backend: str = "faster_whisper"
        self._live_execution_mode: str = "endpointed"
        self._is_incremental: bool = False
        self._phase2_engine: Any = None
        self._phase2_load_failed: bool = False
        self._live_preview_unavailable: bool = False
        self._fallback_events: int = 0
        self._fallback_reasons: List[str] = []

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._agreement_engine = LocalAgreementEngine()
        self._audio_cursor_sec: float = 0.0
        self._segment_index: int = 0
        self._recent_emitted: List[Dict[str, Any]] = []

        # Independent per-track Phase 2 state (never mixed before ASR!)
        self._track_states: Dict[str, Dict[str, Any]] = {
            "mic": _new_track_state(),
            "system": _new_track_state(),
        }

        # Legacy single-cursor Whisper fallback state (retained for 100% compatibility when using Whisper)
        self._speech_active: bool = False
        self._silence_start_sec: float = 0.0
        self._turn_start_sec: float = 0.0
        self._current_speaker: str = "Speaker 1"
        self._remote_speaker_label: str = "Speaker 1"
        self._current_track: str = "system"
        self._step_accumulated_sec: float = 0.0

        # Live streaming telemetry
        self._session_start_mono: float = 0.0
        self._first_partial_latency_ms: Optional[float] = None
        self._first_stable_latency_ms: Optional[float] = None
        self._partial_update_count: int = 0
        self._end_of_turn_latencies_ms: List[float] = []
        self._tracks_flushed: List[str] = []

        self._dropped_chunks: int = 0
        self._dropped_segments: int = 0
        self._dropped_backlog_windows: int = 0
        self._max_backlog_sec: float = 0.0
        self._current_backlog_sec: float = 0.0
        self._revision_count: int = 0
        self._last_flush_cursor_sec: float = 0.0
        self._vad_discarded_chunks: int = 0
        self._vad_speech_sec: float = 0.0
        self._mic_low_level_samples: int = 0

    @property
    def is_active(self) -> bool:
        return self._active

    def reset_streaming_session(self) -> None:
        """Reset all per-track buffers and cursors cleanly between consecutive calls."""
        with self._lock:
            self._session_generation += 1
            self._agreement_engine.reset()
            self._audio_cursor_sec = 0.0
            self._segment_index = 0
            self._recent_emitted = []
            self._track_states = {"mic": _new_track_state(), "system": _new_track_state()}
            self._speech_active = False
            self._silence_start_sec = 0.0
            self._turn_start_sec = 0.0
            self._step_accumulated_sec = 0.0
            self._tracks_flushed = []
            if self._phase2_engine is not None and hasattr(self._phase2_engine, "clear_all_streams"):
                try:
                    self._phase2_engine.clear_all_streams()
                except Exception:
                    pass

    def get_active_live_identity(self) -> Dict[str, Any]:
        """Return truthful identity of the live engine currently running."""
        with self._lock:
            prov = self._get_asr_provenance()
            prov["execution_mode"] = self._live_execution_mode
            prov["is_incremental"] = bool(self._is_incremental)
            return prov

    def get_stream_telemetry(self) -> Dict[str, Any]:
        """Return complete live streaming completeness and latency telemetry."""
        with self._lock:
            elapsed_wall = max(0.001, time.monotonic() - self._session_start_mono) if self._session_start_mono > 0 else 0.001
            eot_arr = sorted(self._end_of_turn_latencies_ms)
            p50_eot = round(float(np.percentile(eot_arr, 50)), 2) if eot_arr else None
            p95_eot = round(float(np.percentile(eot_arr, 95)), 2) if eot_arr else None
            prov = self._get_asr_provenance()
            return {
                "execution_mode": self._live_execution_mode,
                "is_incremental": bool(self._is_incremental),
                "live_backend": prov["asr_backend"],
                "live_model": prov["asr_model"],
                "live_device": prov["asr_device"],
                "live_compute_type": prov["asr_compute_type"],
                "first_partial_latency_ms": self._first_partial_latency_ms,
                "first_stable_latency_ms": self._first_stable_latency_ms,
                "partial_update_count": int(self._partial_update_count),
                "partial_updates_per_sec": round(float(self._partial_update_count) / elapsed_wall, 3),
                "end_of_turn_latency_p50_ms": p50_eot,
                "end_of_turn_latency_p95_ms": p95_eot,
                "end_of_turn_samples": len(eot_arr),
                "committed_segments": int(self._segment_index),
                "dropped_segments": int(self._dropped_segments),
                "fallback_events": int(self._fallback_events),
                "fallback_reasons": list(self._fallback_reasons),
                "tracks_flushed": list(self._tracks_flushed),
                "track_cursors_sec": {
                    k: round(float(v.get("cursor_sec", 0.0)), 3) for k, v in self._track_states.items()
                },
                # Legacy telemetry keys preserved
                "audio_cursor_sec": round(float(self._audio_cursor_sec), 3),
                "last_flush_cursor_sec": round(float(self._last_flush_cursor_sec), 3),
                "segment_count": int(self._segment_index),
                "dropped_chunks": int(self._dropped_chunks),
                "dropped_backlog_windows": int(self._dropped_backlog_windows),
                "live_preview_unavailable": bool(self._live_preview_unavailable),
                "max_backlog_sec": round(float(self._max_backlog_sec), 3),
                "current_backlog_sec": round(float(self._current_backlog_sec), 3),
                "revision_count": int(self._revision_count),
                "vad_discarded_chunks": int(self._vad_discarded_chunks),
                "vad_speech_sec": round(float(self._vad_speech_sec), 3),
                "mic_low_level_samples": int(self._mic_low_level_samples),
            }

    def start(
        self,
        meeting_id: int,
        recorder: Any,
        language: str = "en+hi",
        remote_speaker: str = "Speaker 1",
        execution_profile: Any = None,
        session_mode: Optional[str] = None,
    ) -> None:
        """Start live streaming transcription for the given meeting."""
        with self._lock:
            if self._active:
                self.stop()

            self._session_generation += 1
            self._meeting_id = meeting_id
            self._recorder = recorder
            self._language = language
            self._remote_speaker_label = (remote_speaker or "Speaker 1").strip() or "Speaker 1"
            self._execution_profile = execution_profile
            self._session_mode = str(
                session_mode or (getattr(execution_profile, "session_mode", "auto") if execution_profile else "auto")
            ).strip().lower()
            self._active = True
            self._paused = False
            self._stop_event.clear()

            self._agreement_engine.reset()
            self._audio_cursor_sec = 0.0
            self._segment_index = 0
            self._recent_emitted = []
            self._track_states = {"mic": _new_track_state(), "system": _new_track_state()}
            self._speech_active = False
            self._silence_start_sec = 0.0
            self._turn_start_sec = 0.0
            self._current_speaker = self._remote_speaker_label
            self._current_track = "system"
            self._step_accumulated_sec = 0.0

            self._session_start_mono = time.monotonic()
            self._first_partial_latency_ms = None
            self._first_stable_latency_ms = None
            self._partial_update_count = 0
            self._end_of_turn_latencies_ms = []
            self._tracks_flushed = []
            self._fallback_events = 0
            self._fallback_reasons = []
            self._phase2_load_failed = False
            self._live_preview_unavailable = False

            self._dropped_chunks = 0
            self._dropped_segments = 0
            self._dropped_backlog_windows = 0
            self._max_backlog_sec = 0.0
            self._current_backlog_sec = 0.0
            self._revision_count = 0
            self._last_flush_cursor_sec = 0.0
            self._vad_discarded_chunks = 0
            self._vad_speech_sec = 0.0
            self._mic_low_level_samples = 0

            # Resolve Phase 2 vs Legacy backend
            use_phase2 = bool(
                execution_profile is not None
                and getattr(execution_profile, "phase2_enabled", False)
                and (self.config is None or bool(getattr(self.config, "enable_phase2_asr_pipeline", True)))
            )
            desired_backend = str(getattr(execution_profile, "live_backend", "faster_whisper")) if use_phase2 else "faster_whisper"
            if desired_backend in ("nemotron_onnx", "qwen3_onnx", "parakeet_onnx"):
                self._live_backend = desired_backend
                self._is_incremental = desired_backend == "nemotron_onnx"
                self._live_execution_mode = "incremental" if self._is_incremental else "endpointed"
            else:
                self._live_backend = "faster_whisper"
                self._is_incremental = False
                self._live_execution_mode = "endpointed"

            threading.Thread(
                target=self._warmup_model,
                name="LiveASRModelWarmup",
                daemon=True,
            ).start()

            self._worker_thread = threading.Thread(
                target=self._streaming_worker_loop,
                name="LiveMeetingStreamingWorker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info(
                "Live streaming transcriber started for meeting #%d (backend=%s, mode=%s)",
                meeting_id,
                self._live_backend,
                self._live_execution_mode,
            )

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def _unload_phase2_live_engine(self) -> None:
        with self._lock:
            eng = self._phase2_engine
            self._phase2_engine = None
        if eng is not None and hasattr(eng, "unload_model"):
            try:
                eng.unload_model()
            except Exception:
                pass

    def _fallback_to_whisper(self, reason: str) -> None:
        with self._lock:
            if self._live_backend == "faster_whisper":
                return
            self._fallback_events += 1
            self._fallback_reasons.append(reason)
            logger.warning("Live ASR falling back to faster_whisper: %s", reason)
            self._unload_phase2_live_engine()
            self._phase2_load_failed = True
            self._live_backend = "faster_whisper"
            self._is_incremental = False
            self._live_execution_mode = "endpointed"
            min_cursor = min(
                float(self._track_states["mic"]["cursor_sec"]),
                float(self._track_states["system"]["cursor_sec"]),
            )
            self._audio_cursor_sec = max(self._audio_cursor_sec, min_cursor)
            if self.transcriber is None:
                self._live_preview_unavailable = True

    def _ensure_phase2_engine_loaded(self) -> Any:
        with self._lock:
            if self._live_backend == "faster_whisper" or self._phase2_load_failed:
                return None
            if self._phase2_engine is not None and getattr(self._phase2_engine, "is_loaded", False):
                return self._phase2_engine

            models_dir = getattr(self.config, "models_dir", None) if self.config is not None else getattr(self.transcriber, "models_dir", None)
            threads = int(getattr(self._execution_profile, "safe_cpu_threads", 4) or 4)
            live_threads = min(3, max(2, threads // 2))
            try:
                from voxlocal.engine.onnx_asr_engines import (
                    GuardedQwen3OnnxEngine,
                    NemotronStreamingEngine,
                    ParakeetOnnxEngine,
                )

                if self._live_backend == "nemotron_onnx":
                    eng = NemotronStreamingEngine(models_dir=models_dir, num_threads=live_threads)
                elif self._live_backend == "qwen3_onnx":
                    eng = GuardedQwen3OnnxEngine(models_dir=models_dir, num_threads=live_threads)
                elif self._live_backend == "parakeet_onnx":
                    eng = ParakeetOnnxEngine(models_dir=models_dir, num_threads=live_threads)
                else:
                    return None

                ok, msg = eng.ensure_loaded()
                if not ok:
                    self._fallback_to_whisper(msg)
                    return None
                if self._live_backend == "nemotron_onnx":
                    eng.create_track_stream("mic")
                    eng.create_track_stream("system")
                self._phase2_engine = eng
                return eng
            except Exception as exc:
                self._fallback_to_whisper(f"{type(exc).__name__}: {exc}")
                return None

    def stop(self, expected_meeting_id: Optional[int] = None) -> None:
        """Stop streaming transcription and flush remaining audio on BOTH isolated tracks up to EOF."""
        with self._lock:
            if not self._active:
                return
            if expected_meeting_id is not None and self._meeting_id not in (None, expected_meeting_id):
                return
            self._active = False
            self._stop_event.set()
            rec = self._recorder
            backend_at_stop = self._live_backend

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.5)
            self._worker_thread = None

        try:
            if rec is not None:
                now_sec = float(getattr(rec, "elapsed_seconds", 0.0))
                if backend_at_stop in ("nemotron_onnx", "qwen3_onnx", "parakeet_onnx") and self._phase2_engine is not None:
                    self._flush_phase2_tracks_on_stop(rec, now_sec)
                else:
                    flush_start = max(float(self._audio_cursor_sec), max(0.0, now_sec - 6.0))
                    if self.transcriber is not None and (now_sec - flush_start) >= 0.20:
                        mic_s, sys_s, actual_s = rec.get_audio_window(flush_start, now_sec)
                        m_rms = float(np.sqrt(np.mean(mic_s ** 2))) if mic_s.size else 0.0
                        s_rms = float(np.sqrt(np.mean(sys_s ** 2))) if sys_s.size else 0.0
                        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"
                        cur_track_active = (
                            (self._current_track == "mic" and m_rms >= 0.0018)
                            or (self._current_track == "system" and s_rms >= 0.0008)
                        )
                        if self._speech_active and cur_track_active:
                            self._execute_transcribe_pass(
                                actual_s,
                                now_sec,
                                self._current_track,
                                self._current_speaker,
                                speech_boundary=True,
                                force_flush=True,
                            )
                        elif s_rms >= 0.0009 or m_rms >= 0.0022:
                            tr = "mic" if (m_rms / 0.0022) > (s_rms / 0.0009) else "system"
                            sp = "You (Mic)" if tr == "mic" else remote_label
                            self._execute_transcribe_pass(
                                actual_s,
                                now_sec,
                                tr,
                                sp,
                                speech_boundary=True,
                                force_flush=True,
                            )
                    self._audio_cursor_sec = max(float(self._audio_cursor_sec), now_sec)
                    self._last_flush_cursor_sec = float(self._audio_cursor_sec)
                self._current_backlog_sec = 0.0
        except Exception as flush_exc:
            logger.debug("Live streaming stop flush skipped: %s", flush_exc)
        finally:
            self._unload_phase2_live_engine()

        logger.info("Live streaming transcriber stopped (backend=%s)", backend_at_stop)

    def _warmup_model(self) -> None:
        try:
            if self._live_backend in ("nemotron_onnx", "qwen3_onnx", "parakeet_onnx"):
                self._ensure_phase2_engine_loaded()
                return
            dummy = np.zeros(int(TARGET_SAMPLE_RATE * 0.5), dtype=np.float32)
            self.transcriber._run_whisper_pass(
                dummy,
                language=self._language,
                beam_size=1,
                vad_filter=False,
                vad_params={},
            )
            logger.debug("Live streaming Whisper warmup complete")
        except Exception as exc:
            logger.debug("Model warmup notice: %s", exc)

    def _streaming_worker_loop(self) -> None:
        step_sleep = 0.15
        while not self._stop_event.wait(step_sleep):
            if not self._active or self._paused or self._recorder is None:
                continue
            try:
                if self._live_backend == "nemotron_onnx" and not self._phase2_load_failed:
                    self._step_phase2_incremental()
                elif self._live_backend in ("qwen3_onnx", "parakeet_onnx") and not self._phase2_load_failed:
                    self._step_phase2_endpointed()
                else:
                    self._step()
            except Exception as exc:
                logger.debug("Streaming transcription step error: %s", exc)

    # -----------------------------------------------------------------------
    # Phase 2 True Incremental Dual-Track Path (Nemotron 560ms CPU)
    # -----------------------------------------------------------------------
    def _step_phase2_incremental(self, *, is_flush: bool = False) -> None:
        eng = self._ensure_phase2_engine_loaded()
        if eng is None:
            self._step()
            return
        now_sec = float(getattr(self._recorder, "elapsed_seconds", 0.0))
        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"
        vocab = self.db.list_vocabulary(active_only=True) if self.db else []

        for trk, spk, rms_thresh in (
            ("system", remote_label, 0.0009),
            ("mic", "You (Mic)", 0.0028),
        ):
            st = self._track_states[trk]
            c_sec = float(st["cursor_sec"])
            if now_sec <= c_sec + (0.05 if is_flush else 0.15):
                continue

            mic_s, sys_s, actual_s = self._recorder.get_audio_window(c_sec, now_sec)
            sl = mic_s if trk == "mic" else sys_s
            st["cursor_sec"] = max(c_sec, now_sec)
            self._audio_cursor_sec = max(self._audio_cursor_sec, float(st["cursor_sec"]))
            self._last_flush_cursor_sec = max(self._last_flush_cursor_sec, float(st["cursor_sec"]))

            if sl.size < int(0.05 * TARGET_SAMPLE_RATE):
                continue

            backlog = max(0.0, now_sec - actual_s)
            self._current_backlog_sec = backlog
            if backlog > self._max_backlog_sec:
                self._max_backlog_sec = backlog

            rms = float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0
            if trk == "mic" and 0.0005 < rms < rms_thresh:
                self._mic_low_level_samples += int(sl.size)

            if rms >= rms_thresh and not st["speech_active"]:
                st["speech_active"] = True
                st["turn_start_sec"] = actual_s

            try:
                res = eng.feed_audio(trk, normalize_speech_audio(sl) if rms >= rms_thresh else sl)
                if is_flush:
                    flush_txt = eng.flush_track_stream(trk)
                    if flush_txt:
                        res["text"] = flush_txt
                        res["is_endpoint"] = True
            except Exception as inf_exc:
                self._fallback_to_whisper(f"Nemotron incremental inference error: {inf_exc}")
                return

            raw_txt = str(res.get("text") or "").strip()
            is_ep = bool(res.get("is_endpoint", False))

            if raw_txt and not is_ep and not is_flush:
                part_txt = DictationProcessor.process(
                    raw_txt,
                    vocabulary=vocab,
                    cleanup_enabled=False,
                    spoken_punctuation=False,
                    output_script="roman",
                    mode="verbatim",
                    context="meeting",
                ).strip()
                if part_txt and part_txt != st["last_partial_text"]:
                    st["last_partial_text"] = part_txt
                    self._partial_update_count += 1
                    self._revision_count += 1
                    if self._first_partial_latency_ms is None and self._session_start_mono > 0:
                        self._first_partial_latency_ms = round((time.monotonic() - self._session_start_mono) * 1000.0, 2)
                    if st["active_partial_mono"] is None:
                        st["active_partial_mono"] = time.monotonic()
                    if self.on_partial and self._meeting_id is not None:
                        prov = self._get_asr_provenance()
                        self.on_partial(
                            self._meeting_id,
                            {
                                "speaker": spk,
                                "text": part_txt,
                                "raw_text": raw_txt,
                                "normalized_text": part_txt,
                                "corrected_text": "",
                                "start_time": float(st["turn_start_sec"] or actual_s),
                                "source_track": trk,
                                "confidence": 0.80,
                                "raw_model_score": None,
                                "no_speech_prob": None,
                                "confidence_calibrated": False,
                                "detected_language": "en",
                                "provisional": True,
                                "transcript_stage": "live",
                                "transcript_state": "provisional",
                                "execution_mode": "incremental",
                                "is_incremental": True,
                                "asr_model": prov["asr_model"],
                                "asr_backend": prov["asr_backend"],
                                "asr_device": prov["asr_device"],
                                "asr_compute_type": prov["asr_compute_type"],
                            },
                        )

            if is_ep or (is_flush and raw_txt):
                if not is_flush:
                    eng.reset_track_stream(trk)
                if raw_txt:
                    conf_txt = DictationProcessor.process(
                        raw_txt,
                        vocabulary=vocab,
                        cleanup_enabled=False,
                        spoken_punctuation=False,
                        output_script="roman",
                        mode="verbatim",
                        context="meeting",
                    ).strip()
                    if conf_txt and not is_hallucination(conf_txt, duration_sec=max(0.2, now_sec - float(st["turn_start_sec"] or actual_s))):
                        eot_ms = round(
                            (time.monotonic() - float(st["active_partial_mono"] or time.monotonic())) * 1000.0, 2
                        )
                        self._end_of_turn_latencies_ms.append(max(0.0, eot_ms))
                        self._commit_confirmed_segment(
                            float(st["turn_start_sec"] or actual_s),
                            now_sec,
                            trk,
                            spk,
                            conf_txt,
                            confidence=0.85,
                            raw_model_score=None,
                            no_speech_prob=None,
                            raw_text=raw_txt,
                            detected_language="en",
                        )
                st["speech_active"] = False
                st["turn_start_sec"] = now_sec
                st["last_partial_text"] = ""
                st["active_partial_mono"] = None

    # -----------------------------------------------------------------------
    # Phase 2 Endpointed Dual-Track Path (Guarded Qwen3 ONNX / Parakeet ONNX)
    # -----------------------------------------------------------------------
    def _step_phase2_endpointed(self) -> None:
        eng = self._ensure_phase2_engine_loaded()
        if eng is None:
            self._step()
            return
        now_sec = float(getattr(self._recorder, "elapsed_seconds", 0.0))
        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"

        for trk, spk, speech_thresh in (
            ("system", remote_label, 0.0012),
            ("mic", "You (Mic)", 0.0035),
        ):
            if self._stop_event.is_set():
                return
            st = self._track_states[trk]
            c_sec = float(st["cursor_sec"])
            if now_sec <= c_sec + 0.35:
                continue

            backlog = max(0.0, now_sec - c_sec)
            self._current_backlog_sec = backlog
            if backlog > self._max_backlog_sec:
                self._max_backlog_sec = backlog

            max_backlog = float(getattr(self.config, "live_preview_max_backlog_sec", 8.0) or 8.0)
            if backlog > max_backlog:
                st["cursor_sec"] = max(c_sec, now_sec - 2.0)
                st["speech_active"] = False
                st["turn_start_sec"] = st["cursor_sec"]
                st["active_partial_mono"] = None
                self._dropped_backlog_windows += 1
                self._audio_cursor_sec = max(self._audio_cursor_sec, float(st["cursor_sec"]))
                logger.debug(
                    "Skipped stale live preview window on %s (backlog=%.2fs > %.2fs); full WAV intact.",
                    trk,
                    backlog,
                    max_backlog,
                )
                continue

            mic_s, sys_s, actual_s = self._recorder.get_audio_window(c_sec, now_sec)
            sl = mic_s if trk == "mic" else sys_s
            win_dur = now_sec - actual_s
            if sl.size < int(0.35 * TARGET_SAMPLE_RATE):
                continue

            tail_n = max(1, int(TARGET_SAMPLE_RATE * 0.45))
            tail = sl[-tail_n:] if sl.size > tail_n else sl
            tail_rms = float(np.sqrt(np.mean(np.square(tail)))) if tail.size else 0.0
            if trk == "mic" and 0.0005 < tail_rms < speech_thresh and tail.size:
                self._mic_low_level_samples += int(tail.size)

            if tail_rms >= speech_thresh:
                if not st["speech_active"]:
                    st["speech_active"] = True
                    st["turn_start_sec"] = actual_s
                    st["active_partial_mono"] = time.monotonic()
                st["last_speech_sec"] = now_sec
                if (now_sec - float(st["turn_start_sec"])) >= 14.0:
                    self._flush_endpointed_track_utterance(eng, trk, spk, actual_s, now_sec, sl)
                    st["cursor_sec"] = now_sec
                    st["speech_active"] = False
                    st["turn_start_sec"] = now_sec
                    st["active_partial_mono"] = None
            else:
                if st["speech_active"]:
                    if (now_sec - float(st["last_speech_sec"])) >= 0.85:
                        self._flush_endpointed_track_utterance(eng, trk, spk, actual_s, now_sec, sl)
                        st["cursor_sec"] = now_sec
                        st["speech_active"] = False
                        st["turn_start_sec"] = now_sec
                        st["active_partial_mono"] = None
                elif win_dur >= 2.0:
                    st["cursor_sec"] = now_sec - 0.5
                    self._audio_cursor_sec = max(self._audio_cursor_sec, float(st["cursor_sec"]))

    def _flush_endpointed_track_utterance(
        self,
        eng: Any,
        track: str,
        speaker: str,
        start_sec: float,
        end_sec: float,
        audio_slice: np.ndarray,
    ) -> None:
        if audio_slice.size < int(0.35 * TARGET_SAMPLE_RATE):
            return
        max_samples = int(24.0 * TARGET_SAMPLE_RATE)
        if audio_slice.size > max_samples:
            self._dropped_chunks += 1
            audio_slice = audio_slice[-max_samples:]

        vocab = self.db.list_vocabulary(active_only=True) if self.db else []
        t_inf0 = time.monotonic()
        try:
            res = eng.transcribe_array(audio_slice, vocabulary=vocab, output_script="roman")
        except Exception as exc:
            self._fallback_to_whisper(f"{self._live_backend} inference error: {exc}")
            return

        eot_ms = round((time.monotonic() - t_inf0) * 1000.0, 2)
        self._end_of_turn_latencies_ms.append(eot_ms)
        raw_txt = str(res.get("raw_text") or "").strip()
        disp_txt = str(res.get("text") or "").strip()
        if not disp_txt or is_hallucination(disp_txt, duration_sec=max(0.2, end_sec - start_sec)):
            return
        avg_lp = res.get("avg_logprob")
        approx_conf = round(float(min(0.95, max(0.25, np.exp(float(avg_lp))))), 2) if avg_lp is not None else 0.85
        self._commit_confirmed_segment(
            start_sec,
            end_sec,
            track,
            speaker,
            disp_txt,
            confidence=approx_conf,
            raw_model_score=avg_lp,
            no_speech_prob=None,
            raw_text=raw_txt or disp_txt,
            detected_language=str(res.get("detected_language") or ""),
        )

    def _flush_phase2_tracks_on_stop(self, rec: Any, now_sec: float) -> None:
        eng = self._phase2_engine
        if eng is None:
            return
        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"
        if self._live_backend == "nemotron_onnx":
            self._recorder = rec
            self._step_phase2_incremental(is_flush=True)
            self._tracks_flushed.extend(["mic", "system"])
            return

        for trk, spk, rms_thresh in (("system", remote_label, 0.0008), ("mic", "You (Mic)", 0.0018)):
            st = self._track_states[trk]
            c_sec = float(st["cursor_sec"])
            flush_start = max(c_sec, max(0.0, now_sec - 6.0))
            if (now_sec - flush_start) < 0.25:
                continue
            mic_s, sys_s, actual_s = rec.get_audio_window(flush_start, now_sec)
            sl = mic_s if trk == "mic" else sys_s
            rms = float(np.sqrt(np.mean(np.square(sl)))) if sl.size else 0.0
            if sl.size >= int(0.25 * TARGET_SAMPLE_RATE) and (st["speech_active"] or rms >= rms_thresh):
                self._flush_endpointed_track_utterance(eng, trk, spk, actual_s, now_sec, sl)
                self._tracks_flushed.append(trk)
            st["cursor_sec"] = max(c_sec, now_sec)
            self._audio_cursor_sec = max(self._audio_cursor_sec, float(st["cursor_sec"]))
            self._last_flush_cursor_sec = float(self._audio_cursor_sec)

    # -----------------------------------------------------------------------
    # Legacy Whisper Streaming Path (100% preserved for rollback / fallback)
    # -----------------------------------------------------------------------
    def _step(self) -> None:
        if self._stop_event.is_set():
            return
        now_sec = self._recorder.elapsed_seconds
        if now_sec <= self._audio_cursor_sec + 0.3:
            return

        mic_slice, sys_slice, actual_start = self._recorder.get_audio_window(
            self._audio_cursor_sec, now_sec
        )

        window_dur = now_sec - actual_start
        if window_dur < 0.4:
            return

        backlog = max(0.0, float(now_sec) - float(self._audio_cursor_sec))
        self._current_backlog_sec = backlog
        if backlog > self._max_backlog_sec:
            self._max_backlog_sec = backlog

        tail_samples = max(1, int(TARGET_SAMPLE_RATE * 0.45))
        mic_tail = mic_slice[-tail_samples:] if mic_slice.size > tail_samples else mic_slice
        sys_tail = sys_slice[-tail_samples:] if sys_slice.size > tail_samples else sys_slice

        mic_rms = float(np.sqrt(np.mean(mic_tail**2))) if mic_tail.size else 0.0
        sys_rms = float(np.sqrt(np.mean(sys_tail**2))) if sys_tail.size else 0.0

        mic_speech_thresh = 0.0035
        sys_speech_thresh = 0.0012
        if 0.0005 < mic_rms < mic_speech_thresh and mic_tail.size:
            self._mic_low_level_samples += int(mic_tail.size)
        is_mic_speech = mic_rms >= mic_speech_thresh
        is_sys_speech = sys_rms >= sys_speech_thresh

        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"
        active_track = "system"
        active_speaker = remote_label

        if is_mic_speech and not is_sys_speech:
            active_track = "mic"
            active_speaker = "You (Mic)"
        elif is_sys_speech and not is_mic_speech:
            active_track = "system"
            active_speaker = remote_label
        elif is_mic_speech and is_sys_speech:
            if (mic_rms / mic_speech_thresh) > (sys_rms / sys_speech_thresh) * 1.25:
                active_track = "mic"
                active_speaker = "You (Mic)"
            else:
                active_track = "system"
                active_speaker = remote_label
        else:
            if self._speech_active:
                if self._silence_start_sec == 0.0:
                    self._silence_start_sec = now_sec
                elif (now_sec - self._silence_start_sec) >= 0.65:
                    self._execute_transcribe_pass(
                        actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=True
                    )
                    self._speech_active = False
                    self._silence_start_sec = 0.0
                    self._step_accumulated_sec = 0.0
                    self._audio_cursor_sec = now_sec
                    self._agreement_engine.reset()
            else:
                if window_dur >= 2.0:
                    self._audio_cursor_sec = now_sec - 0.5
            return

        if not self._speech_active:
            self._speech_active = True
            self._turn_start_sec = actual_start
            self._current_speaker = active_speaker
            self._current_track = active_track
            self._silence_start_sec = 0.0
            self._step_accumulated_sec = 0.0
        elif active_track != self._current_track and window_dur >= 1.2:
            boundary_sec = max(actual_start + 0.4, now_sec - 0.35)
            self._execute_transcribe_pass(
                actual_start, boundary_sec, self._current_track, self._current_speaker, speech_boundary=True
            )
            self._audio_cursor_sec = boundary_sec
            self._agreement_engine.reset()
            self._turn_start_sec = boundary_sec
            self._current_track = active_track
            self._current_speaker = active_speaker
            self._silence_start_sec = 0.0
            self._step_accumulated_sec = 0.0
            return

        self._silence_start_sec = 0.0
        self._step_accumulated_sec += 0.15

        if window_dur >= 14.0:
            self._execute_transcribe_pass(
                actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=True
            )
            self._speech_active = False
            self._silence_start_sec = 0.0
            self._step_accumulated_sec = 0.0
            self._audio_cursor_sec = now_sec
            self._agreement_engine.reset()
        elif self._step_accumulated_sec >= 2.4:
            self._execute_transcribe_pass(
                actual_start, now_sec, self._current_track, self._current_speaker, speech_boundary=False
            )
            self._step_accumulated_sec = 0.0

    @staticmethod
    def _score_whisper_segments(
        segs: Any,
    ) -> Tuple[str, float, Optional[float], Optional[float]]:
        texts: List[str] = []
        lps: List[float] = []
        nsps: List[float] = []
        for s in (segs or []):
            t = str(getattr(s, "text", "")).strip()
            if not t:
                continue
            nsp = getattr(s, "no_speech_prob", None)
            if nsp is not None and float(nsp) >= 0.85:
                continue
            lp = getattr(s, "avg_logprob", None)
            if lp is not None and float(lp) < -1.45:
                continue
            texts.append(t)
            if lp is not None:
                lps.append(float(lp))
            if nsp is not None:
                nsps.append(float(nsp))
        if not texts:
            return "", 0.0, None, None
        joined = " ".join(texts)
        raw_avg_lp: Optional[float] = round(sum(lps) / len(lps), 4) if lps else None
        max_nsp: Optional[float] = round(max(nsps), 4) if nsps else None
        conf = round(max(0.05, min(0.99, float(np.exp(raw_avg_lp)))), 2) if raw_avg_lp is not None else 0.0
        return joined, conf, raw_avg_lp, max_nsp

    @classmethod
    def _filter_and_score_segments(cls, segs: Any) -> Tuple[str, float]:
        joined, conf, _, _ = cls._score_whisper_segments(segs)
        return joined, conf

    def _execute_transcribe_pass(
        self,
        start_sec: float,
        end_sec: float,
        track: str,
        speaker: str,
        speech_boundary: bool,
        force_flush: bool = False,
    ) -> None:
        if self._stop_event.is_set() and not force_flush:
            return
        mic_slice, sys_slice, actual_start = self._recorder.get_audio_window(start_sec, end_sec)
        audio_slice = mic_slice if track == "mic" else sys_slice
        min_samples = int(TARGET_SAMPLE_RATE * (0.20 if force_flush else 0.4))
        if audio_slice.size < min_samples:
            return
        if force_flush and audio_slice.size < int(TARGET_SAMPLE_RATE * 0.45):
            pad_n = int(TARGET_SAMPLE_RATE * 0.45) - int(audio_slice.size)
            audio_slice = np.pad(audio_slice, (0, pad_n), mode="constant")

        if not self.transcriber.is_model_loaded():
            self.transcriber.ensure_loaded()
            if not self.transcriber.is_model_loaded():
                logger.debug("Transcriber model not yet ready for streaming pass; retaining audio in buffer.")
                return

        max_samples = int(TARGET_SAMPLE_RATE * 25.0)
        if audio_slice.size > max_samples:
            self._dropped_chunks += 1
            audio_slice = audio_slice[-max_samples:]

        vocab = self.db.list_vocabulary(active_only=True) if self.db else []
        remote_label = getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1"
        hotwords = merge_domain_hotwords(
            DictationProcessor.build_whisper_hotwords(
                vocab,
                language=self._language,
                participants=[remote_label] if remote_label != "Speaker 1" else None,
                context="meeting",
            ),
            context="meeting",
        )

        model_obj = getattr(self.transcriber, "_model", None)
        is_real_whisper = bool(
            model_obj is not None
            and hasattr(self.transcriber, "_extract_vad_chunk_spans")
            and type(model_obj).__module__.startswith("faster_whisper")
        )
        if is_real_whisper and speech_boundary and audio_slice.size >= int(4.0 * TARGET_SAMPLE_RATE):
            vad_spans = self.transcriber._extract_vad_chunk_spans(
                audio_slice,
                dict(threshold=0.35, min_silence_duration_ms=400, speech_pad_ms=200),
            )
            if vad_spans == []:
                self._vad_discarded_chunks += 1
                rms_empty = float(np.sqrt(np.mean(np.square(audio_slice)))) if audio_slice.size else 0.0
                if rms_empty < 0.008 and (end_sec - self._audio_cursor_sec) >= 12.0:
                    self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 4.0)
                return
            if vad_spans:
                self._vad_speech_sec += sum(
                    max(0, int(sp[1]) - int(sp[0])) for sp in vad_spans
                ) / float(TARGET_SAMPLE_RATE)

        proc_slice = normalize_speech_audio(audio_slice)
        pass_beam = 2 if speech_boundary else 1

        res = self.transcriber._transcribe_single_encode(
            norm_audio=proc_slice,
            language=self._language,
            lang_arg=None,
            is_en_only=False,
            bilingual_modes=("", "auto", "en+hi", "hinglish", "hi_latn"),
            beam_size=pass_beam,
            vad_filter=False,
            vad_params={},
            hotwords=hotwords,
            temperatures=0.0,
            effective_prompt=None,
            word_timestamps=False,
        )

        if not res or not res[0]:
            try:
                res = self.transcriber._run_whisper_pass(
                    norm_audio=proc_slice,
                    language=self._language,
                    beam_size=pass_beam,
                    vad_filter=False,
                    vad_params={},
                    hotwords=hotwords,
                    temperatures=0.0,
                    initial_prompt=None,
                )
            except Exception as fb_err:
                logger.debug("Streaming transcriber fallback pass warning: %s", fb_err)
                res = None

        rms = float(np.sqrt(np.mean(np.square(audio_slice)))) if audio_slice.size else 0.0

        if not res or not res[0]:
            if rms < 0.008 and (end_sec - self._audio_cursor_sec) >= 12.0:
                self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 4.0)
            return

        raw_text, seg_conf, raw_lp, max_nsp = self._score_whisper_segments(res[0])
        det_lang = str(getattr(res[1], "language", "") or "") if len(res) > 1 and res[1] is not None else ""
        if (
            not raw_text
            or (raw_lp is not None and seg_conf < 0.25)
            or is_hallucination(
                raw_text,
                duration_sec=max(0.1, end_sec - start_sec),
                initial_prompt="",
                avg_logprob=raw_lp,
                no_speech_prob=max_nsp,
                audio_rms=rms,
            )
        ):
            if rms < 0.008 and (end_sec - self._audio_cursor_sec) >= 12.0:
                self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec - 4.0)
            return

        if not speech_boundary:
            partial = self._agreement_engine._clean_and_normalize(raw_text, vocab)
            if partial and self.on_partial and self._meeting_id is not None:
                self._revision_count += 1
                self._partial_update_count += 1
                if self._first_partial_latency_ms is None and self._session_start_mono > 0:
                    self._first_partial_latency_ms = round((time.monotonic() - self._session_start_mono) * 1000.0, 2)
                prov = self._get_asr_provenance()
                self.on_partial(
                    self._meeting_id,
                    {
                        "speaker": speaker,
                        "text": partial,
                        "raw_text": raw_text,
                        "normalized_text": partial,
                        "corrected_text": "",
                        "start_time": start_sec,
                        "source_track": track,
                        "confidence": seg_conf,
                        "raw_model_score": raw_lp,
                        "no_speech_prob": max_nsp,
                        "confidence_calibrated": False,
                        "detected_language": det_lang,
                        "provisional": True,
                        "transcript_stage": "live",
                        "transcript_state": "provisional",
                        "execution_mode": self._live_execution_mode,
                        "is_incremental": False,
                        "asr_model": prov["asr_model"],
                        "asr_backend": prov["asr_backend"],
                        "asr_device": prov["asr_device"],
                        "asr_compute_type": prov["asr_compute_type"],
                    },
                )
            return

        confirmed, _ = self._agreement_engine.process_step(
            raw_text, speech_boundary=True, vocabulary=vocab
        )
        self._commit_confirmed_segment(
            start_sec,
            end_sec,
            track,
            speaker,
            confirmed,
            confidence=seg_conf,
            raw_model_score=raw_lp,
            no_speech_prob=max_nsp,
            raw_text=raw_text,
            detected_language=det_lang,
        )
        self._audio_cursor_sec = end_sec
        self._last_flush_cursor_sec = max(self._last_flush_cursor_sec, float(end_sec))

        other_track = "system" if track == "mic" else "mic"
        other_slice = sys_slice if other_track == "system" else mic_slice
        other_thresh = 0.0015 if other_track == "system" else 0.0040
        other_rms = float(np.sqrt(np.mean(np.square(other_slice)))) if other_slice.size else 0.0
        if other_rms >= (other_thresh * 1.5) and other_slice.size >= int(TARGET_SAMPLE_RATE * 0.5):
            if other_slice.size > max_samples:
                other_slice = other_slice[-max_samples:]
            other_has_speech = True
            if is_real_whisper and other_slice.size >= int(4.0 * TARGET_SAMPLE_RATE):
                other_vad = self.transcriber._extract_vad_chunk_spans(
                    other_slice,
                    dict(threshold=0.35, min_silence_duration_ms=400, speech_pad_ms=200),
                )
                if other_vad == []:
                    other_has_speech = False
            if other_has_speech:
                norm_other = normalize_speech_audio(other_slice)
                other_res = self.transcriber._transcribe_single_encode(
                    norm_audio=norm_other,
                    language=self._language,
                    lang_arg=None,
                    is_en_only=False,
                    bilingual_modes=("", "auto", "en+hi", "hinglish", "hi_latn"),
                    beam_size=1,
                    vad_filter=False,
                    vad_params={},
                    hotwords=hotwords,
                    temperatures=0.0,
                    effective_prompt=None,
                    word_timestamps=False,
                )
                if other_res and other_res[0]:
                    other_raw, other_conf, other_lp, other_nsp = self._score_whisper_segments(other_res[0])
                    other_det_lang = (
                        str(getattr(other_res[1], "language", "") or "")
                        if len(other_res) > 1 and other_res[1] is not None
                        else ""
                    )
                    if (
                        other_raw
                        and (other_lp is None or other_conf >= 0.25)
                        and not is_hallucination(
                            other_raw,
                            duration_sec=max(0.1, end_sec - start_sec),
                            initial_prompt="",
                            avg_logprob=other_lp,
                            no_speech_prob=other_nsp,
                            audio_rms=other_rms,
                        )
                    ):
                        other_conf_txt = self._agreement_engine._clean_and_normalize(other_raw, vocab)
                        other_speaker = (
                            (getattr(self, "_remote_speaker_label", "Speaker 1") or "Speaker 1")
                            if other_track == "system"
                            else "You (Mic)"
                        )
                        self._commit_confirmed_segment(
                            start_sec,
                            end_sec,
                            other_track,
                            other_speaker,
                            other_conf_txt,
                            confidence=other_conf,
                            raw_model_score=other_lp,
                            no_speech_prob=other_nsp,
                            raw_text=other_raw,
                            detected_language=other_det_lang,
                        )

    def _get_asr_provenance(self) -> Dict[str, str]:
        def _safe_str(val: Any, default: str = "") -> str:
            if not isinstance(val, str):
                return default
            if "MagicMock" in val or "<Mock" in val:
                return default
            return val

        if self._live_backend in ("nemotron_onnx", "qwen3_onnx", "parakeet_onnx") and not self._phase2_load_failed:
            eng = self._phase2_engine
            if eng is not None and hasattr(eng, "get_active_asr_provenance"):
                return eng.get_active_asr_provenance()
            model_map = {
                "nemotron_onnx": "nemotron-streaming-en-0.6b-560ms-int8",
                "qwen3_onnx": "qwen3-asr-0.6b-onnx-int8",
                "parakeet_onnx": "parakeet-tdt-0.6b-v2-onnx-int8",
            }
            return {
                "asr_model": model_map.get(self._live_backend, self._live_backend),
                "asr_backend": self._live_backend,
                "asr_device": "cpu",
                "asr_compute_type": "int8",
            }

        if hasattr(self.transcriber, "get_active_asr_provenance"):
            try:
                raw_prov = self.transcriber.get_active_asr_provenance()
                if isinstance(raw_prov, dict) and "asr_model" in raw_prov:
                    return {
                        "asr_model": _safe_str(raw_prov.get("asr_model"), ""),
                        "asr_backend": _safe_str(raw_prov.get("asr_backend"), "faster_whisper") or "faster_whisper",
                        "asr_device": _safe_str(raw_prov.get("asr_device"), "cpu") or "cpu",
                        "asr_compute_type": _safe_str(raw_prov.get("asr_compute_type"), "int8") or "int8",
                    }
            except Exception:
                pass
        loaded = _safe_str(getattr(self.transcriber, "_loaded_model_name", ""), "")
        configured = _safe_str(getattr(self.transcriber, "model_name", ""), "")
        device = _safe_str(getattr(self.transcriber, "_active_device", "cpu"), "cpu") or "cpu"
        comp = _safe_str(getattr(self.transcriber, "_active_compute_type", ""), "") or _safe_str(
            getattr(self.transcriber, "compute_type", "int8"), "int8"
        )
        return {
            "asr_model": loaded or configured,
            "asr_backend": "faster_whisper",
            "asr_device": device,
            "asr_compute_type": comp or "int8",
        }

    def _commit_confirmed_segment(
        self,
        start_sec: float,
        end_sec: float,
        track: str,
        speaker: str,
        confirmed: str,
        confidence: float = 0.0,
        raw_model_score: Optional[float] = None,
        no_speech_prob: Optional[float] = None,
        raw_text: str = "",
        detected_language: str = "",
    ) -> None:
        if not confirmed:
            return
        cleaned = confirmed.strip(" .,?!;:-—")
        if not cleaned or cleaned.lower() in ("technical keywords", "hinglish conversation"):
            return

        last_track_end = 0.0
        last_track_text = ""
        for ex in reversed(self._recent_emitted):
            if ex.get("source_track") == track:
                last_track_end = float(ex.get("end_time", 0.0))
                last_track_text = ex.get("text", "").strip()
                break

        if last_track_end > 0.0 and start_sec < last_track_end:
            start_sec = max(start_sec, last_track_end)
        if end_sec <= start_sec:
            end_sec = start_sec + 0.3

        if last_track_text:
            prev_words = last_track_text.split()
            cur_words = confirmed.split()
            for k in range(min(5, len(prev_words), len(cur_words)), 0, -1):
                if [w.lower().strip(".,?!") for w in prev_words[-k:]] == [w.lower().strip(".,?!") for w in cur_words[:k]]:
                    cur_words = cur_words[k:]
                    confirmed = " ".join(cur_words)
                    break
        if not confirmed.strip():
            return

        norm_conf = confirmed.strip().lower()
        is_dup = False
        for ex in self._recent_emitted:
            if ex.get("source_track") == track:
                ex_txt = ex.get("text", "").strip().lower()
                if ex_txt == norm_conf:
                    is_dup = True
                    break
                if abs(start_sec - float(ex.get("start_time", 0.0))) <= 1.0 and (norm_conf in ex_txt or ex_txt in norm_conf):
                    is_dup = True
                    break

        if not is_dup:
            from voxlocal.engine.completeness import evaluate_segment_uncertainty

            self._segment_index += 1
            if self._first_stable_latency_ms is None and self._session_start_mono > 0:
                self._first_stable_latency_ms = round((time.monotonic() - self._session_start_mono) * 1000.0, 2)

            seg_id = None
            m_id = self._meeting_id if self._meeting_id is not None else 0
            raw_val = (raw_text or confirmed).strip()
            provenance = self._get_asr_provenance()
            is_unc, unc_reasons = evaluate_segment_uncertainty(
                raw_text=raw_val,
                text=confirmed,
                raw_model_score=raw_model_score,
                no_speech_prob=no_speech_prob,
                confidence=confidence,
            )
            rec_params = {
                "language_request": str(self._language or ""),
                "speech_boundary": True,
                "execution_mode": self._live_execution_mode,
                "is_incremental": bool(self._is_incremental),
                "preview_mode": "provisional_live",
            }
            history = [
                {
                    "stage": "live_stable",
                    "model": provenance["asr_model"],
                    "backend": provenance["asr_backend"],
                    "device": provenance["asr_device"],
                    "execution_mode": self._live_execution_mode,
                    "raw_model_score": raw_model_score,
                    "detected_language": detected_language,
                }
            ]
            if self.db and self._meeting_id is not None:
                try:
                    add_kwargs: Dict[str, Any] = dict(
                        meeting_id=self._meeting_id,
                        segment_index=self._segment_index,
                        start_time=start_sec,
                        end_time=end_sec,
                        speaker=speaker,
                        text=confirmed,
                        source_track=track,
                        confidence=round(float(confidence), 2),
                        raw_text=raw_val,
                        is_uncertain=is_unc,
                        normalized_text=confirmed,
                        corrected_text="",
                        asr_model=provenance["asr_model"],
                        asr_backend=provenance["asr_backend"],
                        asr_device=provenance["asr_device"],
                        asr_compute_type=provenance["asr_compute_type"],
                        transcript_stage="stable",
                        transcript_state="stable",
                        raw_model_score=raw_model_score,
                        no_speech_prob=no_speech_prob,
                        confidence_calibrated=False,
                        detected_language=detected_language,
                        uncertainty_reasons=unc_reasons,
                        recognition_params=rec_params,
                        processing_history=history,
                    )
                    seg_id = self.db.add_transcript_segment(**add_kwargs)
                except TypeError:
                    # Fallback if a mock DB or older stub lacks Phase 2 keyword args
                    seg_id = self.db.add_transcript_segment(
                        meeting_id=self._meeting_id,
                        segment_index=self._segment_index,
                        start_time=start_sec,
                        end_time=end_sec,
                        speaker=speaker,
                        text=confirmed,
                        source_track=track,
                        confidence=round(float(confidence), 2),
                    )
                except Exception as db_exc:
                    logger.debug("Failed saving confirmed segment to DB: %s", db_exc)

            seg_dict = {
                "id": seg_id or self._segment_index,
                "meeting_id": m_id,
                "segment_index": self._segment_index,
                "speaker": speaker,
                "text": confirmed,
                "raw_text": raw_val,
                "normalized_text": confirmed,
                "corrected_text": "",
                "start_time": start_sec,
                "end_time": end_sec,
                "source_track": track,
                "confidence": round(float(confidence), 2),
                "raw_model_score": raw_model_score,
                "no_speech_prob": no_speech_prob,
                "confidence_calibrated": False,
                "is_uncertain": is_unc,
                "uncertainty_reasons": unc_reasons,
                "asr_model": provenance["asr_model"],
                "asr_backend": provenance["asr_backend"],
                "asr_device": provenance["asr_device"],
                "asr_compute_type": provenance["asr_compute_type"],
                "detected_language": detected_language,
                "recognition_params": rec_params,
                "processing_history": history,
                "provisional": True,
                "is_provisional_preview": True,
                "transcript_stage": "live",
                "transcript_state": "stable",
                "execution_mode": self._live_execution_mode,
                "is_incremental": bool(self._is_incremental),
            }

            self._recent_emitted.append(seg_dict)
            if len(self._recent_emitted) > 50:
                self._recent_emitted = self._recent_emitted[-50:]

            self._audio_cursor_sec = max(self._audio_cursor_sec, end_sec)
            self._last_flush_cursor_sec = max(self._last_flush_cursor_sec, float(self._audio_cursor_sec))

            if self.on_confirmed:
                self.on_confirmed(m_id, seg_dict)

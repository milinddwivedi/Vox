# Scope: Milestone 2 — Speech Audio Pipeline, Dictation Machine & Retraction Intelligence

## Architecture
Milestone 2 encapsulates the core voice-to-text intelligence and real-time processing engine of VOX:
1. **Audio Capture Subsystem**: `src/voxlocal/audio/dictation_recorder.py`, `src/voxlocal/audio/devices.py`
   - Real-time 16kHz mono WASAPI capture.
   - 40ms frame slicing (640 samples per chunk).
   - Dynamic RMS audio level calculation for UI equalizer feeds.
   - 450ms pre-speech buffer retention to prevent clipping the beginning of speech.
2. **Fast Offline Whisper ASR Subsystem**: `src/voxlocal/engine/transcriber.py`
   - Local CTranslate2 faster-whisper implementation.
   - Automatic CUDA GPU detection and fallback to multi-threaded CPU.
   - Low-latency dictation transcription returning `DictationResult(raw_text, segments, language, duration)`.
3. **9-State Dictation State Machine**: `src/voxlocal/core/controller.py`
   - Exact Wispr Flow state machine: `DictationState(str, enum.Enum)` with 9 canonical states: `IDLE`, `INITIALIZING`, `LISTENING`, `STOPPING`, `PROCESSING`, `ERROR`, `DISMISSED`, `RETRYING`, `TESTING`.
   - Thread-safe transitions via `_state_lock` emitting `state_changed`, `audio_level_changed`, `mode_changed`.
   - Monotonic cancellation token `_dictation_session_id` eliminating cancellation race condition.
   - 10-bar parabolic center-weighted equalizer calculation.
4. **5 Transformation Modes & Verbal Self-Correction Parser**: `src/voxlocal/engine/dictation_processor.py`
   - 5 modes: Verbatim, Clean, Polished, Structured, Email.
   - Spoken punctuation processing.
   - Verbal self-correction parser detecting verbal retractions (`scratch that`, `no wait`, `never mind`, `actually`, `I mean`, `correction`) with crimson strikethrough preview (`#D32F2F`) and linguistic negation protection.
   - `DictationProcessedResult` dataclass and canonical `process_text()` entrypoint.
5. **Test Verification Suites**:
   - `tests/test_wispr_parity_features.py`
   - `tests/test_voice_typing_evolved.py`
   - `tests/test_voice_typing_lifecycle_and_target.py`
   - `tests/test_adversarial_vox_capabilities.py`
   - `tests/test_adversarial_m2_empirical.py`
   - `tests/test_challenger_stress_m2.py`
   - `tests/test_e2e_dictation_and_modes.py`
   - `tests/test_milestone2_pipeline_complete.py`
   - `tests/e2e/test_tier1_feature_coverage.py`
   - `tests/test_challenger2_stress.py`
   - `tests/test_adversarial_audio_pipeline.py`

## Feature Inventory Status
| # | Feature | Description | Milestone | Status |
|---|---------|-------------|-----------|--------|
| 5 | Audio Capture Pipeline | 16kHz mono WASAPI capture with 40ms/640-sample slicing, 450ms pre-buffer, RMS metering | M2 | DONE |
| 6 | Offline Fast-Whisper Transcription | Local CTranslate2 faster-whisper speech-to-text with auto CUDA/CPU fallback | M2 | DONE |
| 7 | 9-State Dictation State Machine | Exact lifecycle transitions (idle, initializing, listening, stopping, processing, idle, etc.) | M2 | DONE |
| 8 | 5 Transformation Modes | Verbatim, Clean, Polished, Structured, and Email modes with spoken punctuation | M2 | DONE |
| 9 | Verbal Self-Correction Parser | Retraction parser with HTML crimson strikethrough (#D32F2F) and negation protection | M2 | DONE |

## Gate Result
**PASS** — All 5 features implemented authentically. 100% test pass rate across all suites (249 tests passing, 0 failures, 0 errors). Full consensus: Reviewer 1 (APPROVE), Reviewer 2 (APPROVE), Challenger 1 (APPROVE), Challenger 2 (APPROVE), Forensic Auditor (CLEAN).

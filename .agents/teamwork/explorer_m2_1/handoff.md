# Milestone 2 — Audio Capture Pipeline & Fast Offline Whisper Transcription Investigation

## 1. Observation

### 1.1 Architecture & Requirement Contracts
- **`PROJECT.md` (§ Interface Contracts 2)**:
  - Format: 16,000 Hz, 1 channel (mono), 32-bit float PCM array normalized to `[-1.0, 1.0]`.
  - Chunking: 640 samples (40ms) per frame with dynamic RMS metering for UI equalizer.
  - Contract: `transcriber.transcribe_audio(np.ndarray audio_pcm) -> DictationResult(raw_text: str, segments: list, language: str, duration: float)`.
- **`SCOPE.md` (§ Interface Contracts 1)**:
  - Audio data: 16,000 Hz, mono, 32-bit float PCM array normalized in range `[-1.0, 1.0]`.
  - Frame rate: 40ms frames (640 samples per chunk).
  - Signature: `transcribe_audio(audio_pcm: np.ndarray) -> DictationResult(raw_text: str, segments: list, language: str, duration: float)`.

### 1.2 Audio Subsystem Implementation (`src/voxlocal/audio/`)
- **WASAPI Device Discovery & Capture (`src/voxlocal/audio/devices.py`)**:
  - Line 19: `TARGET_SAMPLE_RATE = 16000` is defined globally.
  - Line 514: `wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)` filters specifically for Windows WASAPI audio endpoints.
  - Line 543-548: Categorizes endpoints into microphone input endpoints vs. loopback capture endpoints (`get_default_wasapi_loopback()`, `isLoopbackDevice`).
  - Lines 41-49: `resample_audio_to_16k_mono(raw_pcm_int16: np.ndarray, src_rate: int, src_channels: int) -> np.ndarray` converts int16 to float32 `[-1.0, 1.0]` and resamples via `signal.resample_poly`.
    - **Defect / Signature Inconsistency**: In `devices.py:41`, parameter names are `(raw_pcm_int16, src_rate, src_channels)`. In `tests/e2e/test_tier1_feature_coverage.py:321`, the test calls:
      ```python
      resampled = resample_audio_to_16k_mono(pcm_48k, orig_sr=sr_orig, channels=1)
      ```
      This triggers `TypeError: resample_audio_to_16k_mono() got an unexpected keyword argument 'orig_sr'`.
  - Lines 52-104: `StreamResampler` provides click-free overlap-save polyphase streaming resampling to 16 kHz mono.
  - Lines 496-503: `compute_rms_level(audio: np.ndarray) -> float` calculates dBFS log scale normalized from -60 dBFS to 0 dBFS into `[0.0, 1.0]`.
  - Lines 106-447: `StudioVocalDSP` provides a 10-stage mastering chain (pole-zero DC blocker, 85Hz high-pass, 50/60Hz adaptive notch, de-clicker, spectral noise reduction, vocal EQ, de-esser, soft-knee dynamics, AGC, true-peak limiter).
- **Dictation Recorder (`src/voxlocal/audio/dictation_recorder.py`)**:
  - Line 106: Buffer chunk calculation is `frames_per_buffer = max(512, int(mic_dev.sample_rate * 0.04))`.
    - At 16,000 Hz, `16000 * 0.04 = 640` samples.
    - At native Windows WASAPI 48,000 Hz, `48000 * 0.04 = 1920` samples.
  - Lines 108-119: Stream callback `_mic_cb` appends raw incoming byte buffers directly to `self._mic_frames.append(in_data)`. It computes `lvl = compute_rms_level(boosted)` and calls `self.on_level_change(lvl)`.
  - **Pre-Speech Buffer Absence**: `DictationRecorder` does NOT maintain a rolling 450ms pre-speech buffer. Lines 94-95 call `self._mic_frames.clear()` on `start()`, recording only frames arriving after stream startup. In contrast, `StreamingVoiceTyper` (`src/voxlocal/audio/streaming_typer.py:78-79`) maintains `self._pre_buffer = collections.deque(maxlen=self._pre_buffer_frames)` where `self._pre_buffer_frames = int(0.45 / self.FRAME_DURATION_SEC)`.
  - **Error Handling & Thread Safety**:
    - Lines 108-119: `_mic_cb` does not inspect PortAudio `status` flags (e.g. `paInputUnderflow`, `paInputOverflow`, device disconnection).
    - `_lock = threading.RLock()` is used in `start()` and `stop()`, but `_mic_cb` (executing on PortAudio OS thread) reads `self._is_recording` and appends to `self._mic_frames` without holding `_lock`.
    - In `stop()`, lines 203-222 acquire `self._lock` and clear `self._mic_frames`, which can race with an active callback.

### 1.3 Offline Whisper Transcription Engine (`src/voxlocal/engine/transcriber.py`)
- **CTranslate2 & faster-whisper Architecture**:
  - Line 24: Imports `ctranslate2` and `from faster_whisper import WhisperModel`.
  - Lines 37-67: `setup_nvidia_dll_paths()` automatically inspects virtual environments and system Python packages for `nvidia.cublas`, `nvidia.cudnn`, and `nvidia.cuda_nvrtc`, calling `os.add_dll_directory` and prefixing Windows `PATH`.
  - Lines 939-971: Automatic CUDA GPU detection and fallback. Probes `ctranslate2.get_cuda_device_count() > 0`. If CUDA is present, attempts loading model on `device="cuda"`, `compute_type="int8"`. If loading fails (e.g., missing CUDA runtime DLLs or unsupported GPU), it catches `Exception as cuda_exc`, logs informative warning, and falls back to multi-threaded CPU (`device="cpu"`, `compute_type="int8"`, `cpu_threads=self.cpu_threads`).
  - Lines 846-853: Three tuning profiles: `FAST` (beam 1, temp 0.0), `BALANCED` (beam 1-2, single-encode fast path for <=30s), and `MAX_ACCURACY` (beam 5, multi-temperature `[0.0, 0.2, 0.4]`).
  - Lines 1107-1277: `_transcribe_single_encode` single-encode fast path: computes Whisper encoder features once for clips <= 30s, probes SOT language probabilities in <2ms, and directly invokes `m.generate_segments(..., encoder_output)`.
  - Lines 784-843 & 756-782: Comprehensive edge-case handling for speech physiology, runaway repetition loops (`clean_repetition_tail`), and Whisper hallucination suppression (`is_hallucination`).
- **Missing Contract Components**:
  - `DictationResult` dataclass is NOT defined in `transcriber.py` or anywhere in `src/`.
  - `transcribe_audio(self, audio_pcm: np.ndarray)` method is NOT defined in `LocalWhisperTranscriber`.
  - Currently, `LocalWhisperTranscriber` only exposes `transcribe_dictation(...) -> str`, `transcribe_audio_segments(...) -> List[Dict[str, Any]]`, and `transcribe_interim(...) -> Tuple[str, float]`.

### 1.4 Test Suite Status
- **Milestone 2 Specified Suites**:
  - `tests/test_wispr_parity_features.py` (10 tests)
  - `tests/test_voice_typing_evolved.py` (28 tests)
  - `tests/test_voice_typing_lifecycle_and_target.py` (7 tests)
  - `tests/test_adversarial_vox_capabilities.py` (15 tests)
  - `tests/test_adversarial_m2_empirical.py` (13 tests)
  - `tests/test_challenger_stress_m2.py` (16 tests)
  - **Command**: `.venv\Scripts\pytest.exe tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py tests/test_voice_typing_lifecycle_and_target.py tests/test_adversarial_vox_capabilities.py tests/test_adversarial_m2_empirical.py tests/test_challenger_stress_m2.py -q`
  - **Result**: `89 passed in 13.55s` (100% pass rate).
- **Audio & Transcription Specific Tests**:
  - `tests/test_studio_dsp_and_muxing.py`: `7 passed in 2.32s`.
  - `tests/test_call_only_capture_and_max_accuracy.py`: `9 passed in 3.10s`.
  - `tests/test_unit_suite.py`: `6 passed in 3.71s`.
  - `tests/test_vox2_speech_intelligence.py`: `8 passed in 3.18s`.
  - `tests/e2e/test_tier1_feature_coverage.py`:
    - `TestTier1Feature06Transcription`: 5/5 PASSED (100%).
    - `TestTier1Feature05AudioCapture`: 4/5 PASSED, 1 failed due to `orig_sr` keyword parameter mismatch in `resample_audio_to_16k_mono`.

---

## 2. Logic Chain

1. **Premise 1 (Contract Integrity)**:
   `PROJECT.md` (§ Interface Contracts 2) and `SCOPE.md` explicitly mandate:
   `transcriber.transcribe_audio(np.ndarray audio_pcm) -> DictationResult(raw_text: str, segments: list, language: str, duration: float)`.
2. **Premise 2 (Current Transcriber Interface)**:
   Inspection of `src/voxlocal/engine/transcriber.py` reveals `LocalWhisperTranscriber` has `transcribe_dictation` (returning `str`) and `transcribe_audio_segments` (returning `List[Dict]`), but neither `DictationResult` nor `transcribe_audio` exists.
3. **Inference 1**:
   To satisfy Milestone 2 acceptance criteria without breaking existing `controller.py` calls, `transcriber.py` must define `@dataclass class DictationResult` and implement `transcribe_audio(audio_pcm: np.ndarray, ...) -> DictationResult`. `transcribe_dictation` can delegate to `transcribe_audio` (or vice-versa).
4. **Premise 3 (Edge Case Contract)**:
   Input audio arrays may be empty, all-zero, NaN/Inf, or sub-duration (< 0.2s).
5. **Inference 2**:
   `transcribe_audio` must handle these edge cases deterministically by returning `DictationResult(raw_text="", segments=[], language="en", duration=0.0)`.
6. **Premise 4 (Audio Capture Requirements)**:
   Milestone 2 specifies 16kHz mono WASAPI capture, 40ms frame slicing (640 samples per chunk @ 16kHz), dynamic RMS metering, and 450ms pre-speech buffer retention.
7. **Premise 5 (Current Recorder Limitations)**:
   In `dictation_recorder.py`:
   - Streams open at the WASAPI native sample rate (often 48kHz), accumulating unchunked bytes into a list. Resampling to 16kHz mono happens only at `stop()`.
   - `_mic_frames.clear()` is called on `start()`, so leading consonants before keypress/speech trigger are vulnerable to truncation (no 450ms pre-speech buffer exists).
   - In `devices.py`, `resample_audio_to_16k_mono` does not accept alias kwargs `orig_sr` and `channels`, causing `TestTier1Feature05AudioCapture` to fail.
8. **Inference 3**:
   `DictationRecorder` should integrate `StreamResampler` or a 450ms pre-speech rolling deque (7,200 samples @ 16kHz, or ~11 frames of 640 samples / 40ms) and slice into 640-sample frames. `devices.py` must support `orig_sr` and `channels` kwargs.

---

## 3. Caveats

1. **Hardware-Dependent Execution**:
   Testing on Windows with non-NVIDIA GPUs or missing cuBLAS/cuDNN DLLs triggers the CPU fallback path (`int8`, multi-threaded). The CPU fallback path is thoroughly tested and operational, but CUDA inference requires NVIDIA runtime libraries installed.
2. **Audio Hardware Availability in Headless / CI Environments**:
   `AudioDeviceManager.list_devices()` requires WASAPI endpoints. In headless Windows sandbox VMs without virtual audio loopback drivers or physical microphones, `pyaudio.PyAudio()` can report zero devices. Existing tests use mock devices or synthetic numpy waveforms.
3. **Existing Milestone 1 Controller Dependencies**:
   `controller.py` currently calls `transcribe_dictation(...)` expecting a `str`. Any changes to `transcriber.py` must maintain backward compatibility with `transcribe_dictation` while introducing `transcribe_audio -> DictationResult`.

---

## 4. Conclusion & Recommended Worker Strategy

The foundation for Milestone 2 audio capture and transcription in `src/voxlocal/audio/` and `src/voxlocal/engine/` is structurally solid with high test coverage (89/89 core M2 tests passing). However, there are two primary gaps and one minor defect requiring Worker implementation:

### Required Action Items for the Worker:

1. **Implement `DictationResult` and `transcribe_audio` in `src/voxlocal/engine/transcriber.py`**:
   - Define `@dataclass class DictationResult`:
     ```python
     @dataclass
     class DictationResult:
         raw_text: str
         segments: list
         language: str
         duration: float
     ```
   - Implement `transcribe_audio(self, audio_pcm: np.ndarray, ...) -> DictationResult`:
     - Sanitize inputs: reject empty, silence, or sub-minimum (<0.2s) audio by returning `DictationResult("", [], "en", 0.0)`.
     - Run Whisper transcription with `BALANCED` (or caller-specified) profile, Hindustani probability fusion, and domain hotwords.
     - Extract raw text, segment metadata (start, end, text, confidence), detected language, and duration.
     - Export `DictationResult` from `voxlocal.engine.transcriber`.
   - Update `transcribe_dictation` to call `transcribe_audio(audio_16k).raw_text` for clean internal synergy.

2. **Enhance `DictationRecorder` in `src/voxlocal/audio/dictation_recorder.py`**:
   - Add a 450ms pre-speech rolling ring buffer (`collections.deque(maxlen=12)` of 40ms / 640-sample frames, or 7,200 samples) so speech onsets are never clipped.
   - Provide 40ms frame slicing (640 samples per chunk @ 16kHz) for downstream consumers and equalizer feeds.
   - Thread safety: Guard buffer operations and state transitions with `self._lock` in `_mic_cb`.
   - Error handling: Check PortAudio `status` in `_mic_cb` for stream disconnects / overflows and trigger `self.on_error`.

3. **Fix Kwarg Compatibility in `src/voxlocal/audio/devices.py`**:
   - Update `resample_audio_to_16k_mono` signature:
     ```python
     def resample_audio_to_16k_mono(
         raw_pcm_int16: np.ndarray,
         src_rate: Optional[int] = None,
         src_channels: Optional[int] = None,
         *,
         orig_sr: Optional[int] = None,
         channels: Optional[int] = None,
     ) -> np.ndarray:
         effective_rate = src_rate if src_rate is not None else orig_sr
         effective_channels = src_channels if src_channels is not None else (channels or 1)
         ...
     ```
   - This resolves the failure in `tests/e2e/test_tier1_feature_coverage.py:321`.

4. **Add Dedicated Unit Tests**:
   - Add tests verifying `transcribe_audio` returning `DictationResult` on clean speech, silence, and empty input.
   - Add tests verifying 40ms frame chunking and 450ms pre-speech buffer in `DictationRecorder`.

---

## 5. Verification Method

To independently verify the investigation findings and subsequent Worker implementations:

### Test Commands:
```powershell
# 1. Verify all 6 Milestone 2 core test suites (89 tests)
.venv\Scripts\pytest.exe tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py tests/test_voice_typing_lifecycle_and_target.py tests/test_adversarial_vox_capabilities.py tests/test_adversarial_m2_empirical.py tests/test_challenger_stress_m2.py -v

# 2. Verify audio and transcriber unit tests
.venv\Scripts\pytest.exe tests/test_unit_suite.py tests/test_vox2_speech_intelligence.py tests/test_studio_dsp_and_muxing.py tests/test_call_only_capture_and_max_accuracy.py -v

# 3. Verify Tier 1 Feature 05 and 06 coverage
.venv\Scripts\pytest.exe tests/e2e/test_tier1_feature_coverage.py -k "TestTier1Feature05 or TestTier1Feature06" -v
```

### Invalidation Conditions:
- If `transcribe_audio` fails to return an instance of `DictationResult`.
- If `transcribe_audio` on empty or silent audio raises an unhandled exception instead of returning empty `DictationResult`.
- If `resample_audio_to_16k_mono` fails when called with `orig_sr` or `channels` kwargs.
- If any of the 89 pre-existing Milestone 2 tests regress.

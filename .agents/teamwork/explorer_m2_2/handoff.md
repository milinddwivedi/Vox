# Milestone 2 — 9-State Dictation State Machine & Master Controller Coordination Investigation

## 1. Observation

### 1.1 Requirements & Interface Contracts
- **`PROJECT.md` (§ Interface Contracts 1 & 4)**:
  - **Win32 Hook -> Dictation State Machine (`win32/hotkeys.py` <-> `core/controller.py`)**:
    - Signal: `hotkey_triggered(str action, bool is_release)`
    - Actions: `"ptt"`, `"popo"`, `"dismiss"`, `"mode_1"`, `"mode_2"`, `"mode_3"`, `"mode_4"`, `"mode_5"`
    - Behavior:
      - `action="ptt", is_release=False` -> triggers `start_dictation()`
      - `action="ptt", is_release=True` -> triggers `stop_dictation()`
      - `action="popo", is_release=False` -> toggles dictation state
      - `action="dismiss"` -> aborts current dictation session without text insertion
  - **Controller -> Floating Pill UI (`core/controller.py` <-> `ui/floating_pill.py`)**:
    - Signals:
      - `state_changed(DictationState new_state)`
      - `audio_level_changed(float rms_level, list equalizer_levels)`
      - `live_preview_updated(str preview_text, str strikethrough_html)`
      - `mode_changed(str mode_name)`
- **`SCOPE.md` (§ Milestone 2 Item 3 & § Interface Contracts 2)**:
  - Exact Wispr Flow 9 lifecycle states:
    - Primary cycle: `idle` -> `initializing` -> `listening` -> `stopping` -> `processing` -> `idle`
    - Ancillary states: `error`, `dismissed`, `retrying`, `testing`.
  - Signals: `state_changed(DictationState new_state)`
  - States Enum: `IDLE`, `INITIALIZING`, `LISTENING`, `STOPPING`, `PROCESSING`, `ERROR`, `DISMISSED`, `RETRYING`, `TESTING`.

### 1.2 Current State Machine Implementation (`src/voxlocal/core/controller.py`)
- **State Representation**:
  - Line 76: `dictation_state_changed = pyqtSignal(str, str)  # (state, status_message)`
  - Line 205: `self._dictation_state = "idle"  # idle, starting, listening, speaking, processing, stopping, failed`
  - Line 411:
    ```python
    @property
    def dictation_state(self) -> str:
        return self._dictation_state
    ```
  - **Absence of `DictationState` Enum**: No `DictationState` enum is currently defined in `controller.py` or anywhere in `src/voxlocal/`.
  - **State Naming Discrepancies**:
    - `initializing` is currently named `"starting"` (lines 1140, 1606).
    - `error` is currently named `"failed"` (lines 483, 1175, 1629, 1770).
    - `dismissed` does not exist as an explicit state: `cancel_dictation()` (line 1697) sets `_dictation_state = "idle"` directly and emits `("idle", "Dictation cancelled")`.
    - `retrying` is not exposed: when VAD fails in `transcriber.py:1606`, it retries without VAD internally without notifying the controller or UI.
    - `testing` is not implemented: microphone test routines (such as during Onboarding Step 2) lack an explicit non-inserting `testing` state.
  - **Missing Required Qt Signals**:
    - `state_changed = pyqtSignal(object)` (or `pyqtSignal(DictationState)`) is missing.
    - `audio_level_changed = pyqtSignal(float, list)` is missing (only `dictation_level_changed = pyqtSignal(float)` exists).

### 1.3 Concurrency, Locking & Thread Safety
- **Locking Asymmetry**:
  - `self._state_lock = threading.RLock()` is initialized at line 204.
  - In lines 416-420, `meeting_state` is properly synchronized:
    ```python
    with self._state_lock:
        st = self._meeting_state
    ```
  - In contrast, `self._dictation_state` is read and mutated across multiple threads without holding `_state_lock`:
    - `dictation_state` property (line 411) reads `_dictation_state` without a lock.
    - `start_dictation()` (lines 1603-1640) checks and writes `_dictation_state` without `_state_lock`.
    - `stop_dictation_and_insert()` (lines 1645-1660) checks and writes `_dictation_state` without `_state_lock`.
    - `cancel_dictation()` (lines 1671-1698) writes `_dictation_state` without `_state_lock`.
    - `toggle_dictation()` (lines 1100-1119) checks and writes `_dictation_state` without `_state_lock`.
    - Background workers (`_process_dictation_worker` line 1764, `_process_streaming_worker` line 1568) mutate `_dictation_state` without `_state_lock`.
  - **Race Condition on Hotkey vs UI**:
    - Global hotkeys run on `Win32GlobalHotkeyThread` and spawn daemon worker threads for callbacks (`on_ptt_start`, `on_ptt_stop`, `on_dictation_toggle`, `on_cancel_dictation`).
    - UI clicks execute on the main PyQt6 thread.
    - Simultaneous hotkey release and UI cancel/stop click race without lock protection.

### 1.4 Cancellation Semantics Defect
- **Inspection of `cancel_dictation()` vs `_process_dictation_worker()`**:
  - Lines 1670-1700: `cancel_dictation()` resets state variables:
    ```python
    self._cancel_startup_requested = True
    self._hands_free_active = False
    self.dictation_interim_updated.emit("", "")
    ...
    self.dictation_recorder.stop()
    self._clear_dictation_target()
    self._dictation_state = "idle"
    self.dictation_state_changed.emit("idle", "Dictation cancelled")
    ```
  - Lines 1702-1784: `_process_dictation_worker(self, audio_16k, duration_sec: float, target)`:
    - Runs in a detached background daemon thread `DictationTranscribeWorker`.
    - Performs Whisper model inference: `raw_text = self.transcriber.transcribe_dictation(...)`.
    - **CRITICAL DEFECT**: The worker has **zero checks** for whether the dictation session was cancelled while Whisper was computing.
    - Once transcription finishes, it unconditionally calls `_format_and_commit_dictation()`, which invokes `self.text_inserter.insert_text()`, and sets `self._dictation_state = "completed"`!
    - **Result**: If a user presses `Esc` or clicks Cancel while transcription is processing, the text is still injected into the active window, and the state flips from `idle` back to `completed`!

### 1.5 Subsystem Coordination
- **DictationRecorder (`src/voxlocal/audio/dictation_recorder.py`)**:
  - `start()` opens WASAPI mic and loopback streams. Emits RMS levels to `dictation_level_changed`.
  - `stop()` returns `(audio_16k, duration_sec)`.
- **Transcriber (`src/voxlocal/engine/transcriber.py`)**:
  - Preloaded in background thread at controller startup (`_preload_model_bg`).
  - Currently exposes `transcribe_dictation(audio_16k, ...) -> str`. Missing `transcribe_audio(audio_pcm) -> DictationResult` contract.
- **DictationProcessor (`src/voxlocal/engine/dictation_processor.py`)**:
  - Invoked in `_format_and_commit_dictation()` with `self.active_writing_mode`, spoken punctuation, custom vocabulary, and retraction parser.
- **TextInserter (`src/voxlocal/win32/text_inserter.py`)**:
  - Target locked at start of dictation (`_resolve_dictation_target()`).
  - Target closed fallback preserves text to clipboard with method `"target_closed_saved_to_clipboard"`.
- **Floating Pill UI (`src/voxlocal/ui/floating_pill.py`)**:
  - Connects to `self.controller.dictation_state_changed` (line 605) and `dictation_level_changed` (line 606).
  - Maps states in `_on_controller_dictation_state` (lines 911-924):
    `starting`/`listening`/`speaking` -> `PillState.LISTENING`
    `processing`/`stopping` -> `PillState.PROCESSING`
    `completed` -> `PillState.COMMITTED`
    `failed` -> `PillState.ERROR`
    `idle` -> `PillState.IDLE`

### 1.6 Current Test Baseline
- Executing all 7 Milestone 2 test suites via `.venv\Scripts\python.exe -m pytest ...`:
  - `tests/test_wispr_parity_features.py`: 10 passed
  - `tests/test_voice_typing_evolved.py`: 28 passed
  - `tests/test_voice_typing_lifecycle_and_target.py`: 7 passed
  - `tests/test_adversarial_vox_capabilities.py`: 15 passed
  - `tests/test_adversarial_m2_empirical.py`: 13 passed
  - `tests/test_challenger_stress_m2.py`: 16 passed
  - `tests/test_e2e_dictation_and_modes.py`: 17 passed
  - **Total**: **106 passed in 8.51s** (100% pass rate).
- Note: Pre-existing tests directly read or write `ctrl._dictation_state` as strings (e.g., `ctrl._dictation_state = "starting"`, `ctrl._dictation_state = "listening"`, `assert ctrl.dictation_state == "idle"`).

---

## 2. Logic Chain

1. **State Formalization (Wispr Flow Parity)**:
   - REA forensics and `SCOPE.md` require 9 distinct states: `IDLE`, `INITIALIZING`, `LISTENING`, `STOPPING`, `PROCESSING`, `ERROR`, `DISMISSED`, `RETRYING`, `TESTING`.
   - Implementing `class DictationState(str, enum.Enum)` provides strict type safety while allowing `DictationState.IDLE == "idle"` string equality.
2. **Backwards Compatibility Guarantee**:
   - Because existing tests (e.g. `test_voice_typing_lifecycle_and_target.py:83`) write `self.controller._dictation_state = "starting"`, `AppController` must accept legacy strings (`"starting"`, `"failed"`) and map them to their canonical equivalents (`INITIALIZING`, `ERROR`).
   - Emitting both `dictation_state_changed(str, str)` AND `state_changed(DictationState)` ensures 100% compatibility with all UI widgets and future `DictationState` subscribers.
3. **Thread Safety & Race Elimination**:
   - Hotkey threads and UI threads must not perform concurrent unsynchronized reads/writes of dictation state.
   - Enclosing all transitions in `with self._state_lock:` and validating valid state transition edges prevents state corruption.
4. **Cancellation Integrity (Esc / Dismiss Guard)**:
   - Introducing a monotonic session generation counter (`self._dictation_session_id: int`) and cancellation event (`self._dictation_cancelled: bool`) eliminates the race condition where `cancel_dictation()` is bypassed by an in-flight Whisper transcription worker.
   - In `_process_dictation_worker()`, checking `if self._dictation_cancelled or session_id != self._dictation_session_id:` before calling `_format_and_commit_dictation()` guarantees that cancelled audio is never injected into the user's document.
5. **Interface Contract Completeness**:
   - Providing `audio_level_changed = pyqtSignal(float, list)` satisfies `PROJECT.md` contract §4 by supplying both raw RMS and an equalizer levels list.
   - Adding `handle_hotkey_action(action: str, is_release: bool = False)` directly fulfills the Win32 hook contract from `PROJECT.md` §1.

---

## 3. Caveats

1. **Direct String Assignments in Existing Unit Tests**:
   Several existing tests bypass API methods and assign directly: `ctrl._dictation_state = "listening"`. The Worker must ensure that `_dictation_state` remains a direct attribute or property that transparently handles both `str` and `DictationState` values.
2. **PyQt6 Queued Connections**:
   Signals emitted from background worker threads (like `DictationTranscribeWorker` or `Win32GlobalHotkeyThread`) are delivered via Qt QueuedConnection to the GUI thread. Direct UI method calls (e.g. `floating_pill.set_state(...)`) must NOT be called directly from worker threads.
3. **Double-Tap Hands-Free vs Single-Shot PTT**:
   The controller handles two distinct dictation recording pipelines:
   - Push-To-Talk / Single-Shot (`DictationRecorder`)
   - Continuous Hands-Free (`StreamingVoiceTyper`)
   The state machine transitions must cleanly govern both pipelines without cross-contamination.

---

## 4. Conclusion & Recommended Worker Strategy

The state machine architecture in `src/voxlocal/core/controller.py` requires precision refactoring to formalize the 9 Wispr Flow states, fix thread safety, plug the cancellation leakage defect, and fulfill interface contracts without disturbing the 106 passing tests.

### Recommended Implementation Roadmap for the Worker:

#### 1. Define Canonical `DictationState` Enum in `src/voxlocal/core/controller.py`
```python
import enum

class DictationState(str, enum.Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    LISTENING = "listening"
    STOPPING = "stopping"
    PROCESSING = "processing"
    ERROR = "error"
    DISMISSED = "dismissed"
    RETRYING = "retrying"
    TESTING = "testing"
```

#### 2. Declare Required Contract Signals on `AppController`
```python
# Modern Milestone 2 Interface Contract Signals
state_changed = pyqtSignal(object)                    # (DictationState)
audio_level_changed = pyqtSignal(float, list)         # (rms_level: float, equalizer_levels: list[float])
mode_changed = pyqtSignal(str)                        # ('verbatim' | 'clean' | 'polished' | 'structured' | 'email')

# Retain existing signals for 100% backwards compatibility
dictation_state_changed = pyqtSignal(str, str)        # (state_str, message)
dictation_level_changed = pyqtSignal(float)
writing_mode_changed = pyqtSignal(str)
```

#### 3. Implement Centralized, Thread-Safe Transition Engine
Create `_set_dictation_state(self, new_state: DictationState | str, message: str = "")`:
- Normalize aliases: `"starting"` -> `INITIALIZING`, `"failed"` -> `ERROR`.
- Guard with `with self._state_lock:`.
- Validate transition against valid transition graph:
  - `IDLE` -> `INITIALIZING`, `TESTING`
  - `INITIALIZING` -> `LISTENING`, `STOPPING`, `DISMISSED`, `ERROR`
  - `LISTENING` -> `STOPPING`, `DISMISSED`, `ERROR`
  - `STOPPING` -> `PROCESSING`, `DISMISSED`, `IDLE`
  - `PROCESSING` -> `RETRYING`, `IDLE`, `DISMISSED`, `ERROR`
  - `RETRYING` -> `PROCESSING`, `ERROR`, `DISMISSED`
  - `ERROR` -> `IDLE`
  - `DISMISSED` -> `IDLE`
  - `TESTING` -> `IDLE`
- Update `self._dictation_state = state_enum.value`.
- Emit both `self.state_changed.emit(state_enum)` and `self.dictation_state_changed.emit(state_enum.value, message)`.

#### 4. Eliminate Cancellation Race Condition in Worker
- Add `self._dictation_session_id: int = 0` and `self._dictation_cancelled: bool = False`.
- In `start_dictation()` and `start_hands_free_typing()`:
  - Increment `self._dictation_session_id += 1`.
  - Set `self._dictation_cancelled = False`.
  - Pass `session_id = self._dictation_session_id` to worker thread.
- In `cancel_dictation()`:
  - Set `self._dictation_cancelled = True`.
  - Transition state: `_set_dictation_state(DictationState.DISMISSED, "Dictation cancelled")`.
  - Immediately transition: `_set_dictation_state(DictationState.IDLE, "Voice typing stopped")`.
- In `_process_dictation_worker(...)`:
  - Check `if self._dictation_cancelled or session_id != self._dictation_session_id: return` immediately after Whisper inference and before text insertion.

#### 5. Implement Hotkey Action Dispatcher Contract
Add `handle_hotkey_action(self, action: str, is_release: bool = False) -> None`:
```python
def handle_hotkey_action(self, action: str, is_release: bool = False) -> None:
    action = (action or "").lower().strip()
    if action == "ptt":
        if is_release:
            self.stop_dictation_and_insert()
        else:
            self.start_dictation()
    elif action == "popo" and not is_release:
        self.toggle_dictation()
    elif action == "dismiss" and not is_release:
        self.cancel_dictation()
    elif action.startswith("mode_") and not is_release:
        mode_idx_map = {
            "mode_1": "verbatim",
            "mode_2": "clean",
            "mode_3": "polished",
            "mode_4": "structured",
            "mode_5": "email",
        }
        if action in mode_idx_map:
            self.set_writing_mode(mode_idx_map[action])
```

#### 6. Implement Retrying & Testing State Hooks
- `RETRYING`: Hook into Whisper transcription fallback (e.g. VAD fallback or CPU fallback) to emit `DictationState.RETRYING`.
- `TESTING`: Add `start_audio_test()` and `stop_audio_test()` for microphone calibration without window focus or text insertion.

#### 7. Add Equalizer Level Generator
In audio level callback:
```python
def _on_audio_level(self, lvl: float) -> None:
    self.dictation_level_changed.emit(lvl)
    # Generate 10-bar equalizer levels matching Wispr Flow capsule
    weights = [0.35, 0.55, 0.75, 0.95, 1.0, 1.0, 0.95, 0.75, 0.55, 0.35]
    eq = [min(1.0, max(0.05, lvl * w)) for w in weights]
    self.audio_level_changed.emit(lvl, eq)
```

---

## 5. Verification Method

### 1. Test Command Execution
Execute using the project virtual environment Python:
```powershell
# Run all Milestone 2 core verification suites
.\.venv\Scripts\python.exe -m pytest tests\test_wispr_parity_features.py tests\test_voice_typing_evolved.py tests\test_voice_typing_lifecycle_and_target.py tests\test_adversarial_vox_capabilities.py tests\test_adversarial_m2_empirical.py tests\test_challenger_stress_m2.py tests\test_e2e_dictation_and_modes.py -v
```

### 2. State Machine Specific Verification Suite
Add a dedicated test class (e.g. in `tests/test_voice_typing_lifecycle_and_target.py` or new test file) verifying:
1. All 9 `DictationState` enum values exist and match Wispr Flow strings.
2. `state_changed(DictationState)` signal emissions on `start_dictation()`, `stop_dictation_and_insert()`, `cancel_dictation()`.
3. Rapid cancel during transcription worker aborts text insertion and does NOT commit text.
4. Hotkey action dispatcher (`handle_hotkey_action`) triggers PTT, POPO, dismiss, and modes.
5. `audio_level_changed` emits `(float, list)` with 10 bars.

### 3. Invalidation Conditions:
- Any regression in the 106 existing passing tests.
- Releasing PTT or pressing Esc during active transcription resulting in leaked text insertion into the target window.
- Concurrently calling `start_dictation` from multiple threads resulting in duplicate recording streams.
- `state_changed` signal failing to emit `DictationState` enum instances.

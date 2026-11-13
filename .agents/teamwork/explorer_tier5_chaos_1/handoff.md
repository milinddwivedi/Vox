# Tier 5 Adversarial Chaos Suite — Forensic Investigation & Test Suite Specification

**Author**: Teamwork Explorer (`explorer_tier5_chaos_1`)  
**Target File to Author**: `tests/e2e/test_tier5_adversarial.py`  
**Authority**: `ORIGINAL_REQUEST.md`, `PROJECT.md § Feature 20`, `TEST_INFRA.md`, `SCOPE.md`  
**Target Environment**: Windows 11 x64, Python 3.13.14, PyQt6 6.11.0, pytest 9.2.0  

---

## 1. Observation

Direct code examination across the VOX codebase identified the following concrete mechanisms, line references, and behaviors:

### 1.1 Dictation State Machine & Hotkey Dispatch
- **File**: `src/voxlocal/core/controller.py`
  - Lines 509–547: `_set_dictation_state(new_state, message)` guards state mutations with `with self._state_lock:` and emits both canonical `state_changed(DictationState)` and legacy `dictation_state_changed(str, str)`.
  - Lines 548–577: `handle_hotkey_action(action, is_release)` routes `"ptt"` to `start_dictation()` (on press) and `stop_dictation_and_insert()` (on release); `"popo"` to `toggle_dictation()`; `"dismiss"` to `cancel_dictation()`; and `"mode_1".."mode_5"` to `set_writing_mode()`.
  - Lines 1700–1753: `start_dictation()` checks active meeting/screen recording states and acquires `self._state_lock`. It increments `self._dictation_session_id += 1` before invoking `self.dictation_recorder.start()`.
  - Lines 1754–1779: `stop_dictation_and_insert()` checks:
    ```python
    if self._dictation_state in ("starting", "initializing"):
        self._ptt_stop_pending = True
        return
    ```
    If state is `INITIALIZING`, it latches `_ptt_stop_pending = True`. When `start_dictation()` finishes, it checks `if self._ptt_stop_pending: self.stop_dictation_and_insert(); return True`.
  - Lines 1817–1913: `_process_dictation_worker` runs in a daemon thread `DictationTranscribeWorker`. It validates session freshness:
    ```python
    with self._state_lock:
        if self._dictation_cancelled or (session_id is not None and session_id != self._dictation_session_id):
            logger.info("Dictation cancelled before processing")
            return
    ```
    Any unhandled exception inside the worker is trapped by lines 1908–1910:
    ```python
    except Exception as exc:
        logger.error("Dictation worker error: %s", exc)
        self._set_dictation_state(DictationState.ERROR, f"Dictation error: {exc}")
    ```

### 1.2 Audio Capture & Error Callback
- **File**: `src/voxlocal/audio/dictation_recorder.py`
  - Lines 60–61: Exposes `self.on_level_change` and `self.on_error: Optional[Callable[[str], None]]`.
  - Lines 233–240: In `start()`, any PortAudio exception calls `self._cleanup_resources()` and invokes `if self.on_error: self.on_error(msg)`.
  - Lines 337–367: `_cleanup_resources()` wraps stream closing (`stop_stream()`, `close()`) and `self._pa.terminate()` in individual `try ... except Exception: pass` blocks, preventing cascading errors when devices are invalidated.
- **File**: `src/voxlocal/core/controller.py`
  - Lines 284–285: Wires `self.dictation_recorder.on_level_change = self._on_dictation_level_change` and `self.dictation_recorder.on_error = self._on_dictation_error`.
  - Lines 591–595: `_on_dictation_error(msg)` resets `self._hands_free_active = False`, sets state to `DictationState.ERROR`, and emits `notification_requested("Voice Typing Error", f"Capture error: {msg}")`.

### 1.3 Audio DSP & Buffer Sanitization
- **File**: `src/voxlocal/audio/devices.py`
  - Lines 518–525: `compute_rms_level(audio)` computes RMS in dBFS. When tested with `np.nan` and `np.inf`, Python 3.13 returns bounded floats `0.0` or `1.0` without throwing `ValueError`.
  - Lines 502–516: `normalize_speech_audio(audio)` handles quiet audio. If `audio` contains NaNs/Infs, it may propagate them.
- **File**: `src/voxlocal/engine/transcriber.py`
  - Lines 1553–1558: `transcribe_audio(audio_pcm)` explicitly sanitizes input buffers:
    ```python
    if not np.all(np.isfinite(audio_pcm)):
        audio_pcm = np.nan_to_num(audio_pcm, nan=0.0, posinf=0.0, neginf=0.0)
    ```
    And if `audio_pcm.size < int(0.2 * TARGET_SAMPLE_RATE)`, it immediately returns empty `DictationResult` with `raw_text=""`.

### 1.4 Text Insertion & Unicode/Devanagari/Clipboard Fallback
- **File**: `src/voxlocal/win32/text_inserter.py`
  - Lines 270–288: In `insert_text()`, if `len(text) > 120 or has_non_ascii or is_rich_webview_target`, `method="auto"` automatically routes to `_paste_via_clipboard`.
  - Lines 452–517: `_paste_via_clipboard()` snapshots existing clipboard text (`get_clipboard_text()`), writes the payload via `set_clipboard_text(text)`, simulates Ctrl+V via `SendInput` (or falls back to `_send_wm_char_to_target`), and restores the original clipboard content.
  - Lines 256–265: If target window was destroyed before insertion (`not user32.IsWindow(target.hwnd)`), it saves the transcribed text to clipboard and returns `(False, "target_closed_saved_to_clipboard")`.
  - Lines 383–402: In `_send_unicode_text()`, multi-byte Unicode and emoji surrogate pairs are encoded as UTF-16 code units and injected via `KEYEVENTF_UNICODE` in batches of 64.

### 1.5 Subsystem Contention (Meeting Studio vs Dictation)
- **File**: `src/voxlocal/core/controller.py`
  - Lines 1702–1707: `start_dictation()` checks:
    ```python
    if self._meeting_state in ("recording", "paused"):
        self.notification_requested.emit(
            "Voice Typing Unavailable",
            "Voice typing cannot be used while a corporate meeting is actively recording.",
        )
        return False
    ```
  - Lines 1944–1953: `start_meeting_recording()` checks and stops hands-free typing:
    ```python
    if getattr(self, "_hands_free_active", False):
        self.stop_hands_free_typing()
    if getattr(self, "meeting_recorder", None) and getattr(self.meeting_recorder, "is_recording", False):
        self.meeting_recorder.stop()
    ```

### 1.6 Database Concurrency & Transient Locks
- **File**: `src/voxlocal/storage/database.py`
  - Lines 34–43: `_connect()` opens SQLite connections with `timeout=15.0`, `PRAGMA journal_mode=WAL;`, and `PRAGMA foreign_keys=ON;`. Connections are short-lived and closed immediately in `finally: conn.close()`.
  - Thread safety is guarded by `self._lock = threading.RLock()`.
  - Multiple distinct `LocalDatabase` instances operating on the same physical file rely on SQLite's internal WAL multi-reader/single-writer locking with 15.0s busy timeout.
  - In `controller.py:1840` and `1433`, transient database errors are caught, preventing dictation pipeline abortion.

### 1.7 Malformed Payloads in Snippets & Vocabulary
- **File**: `src/voxlocal/engine/dictation_processor.py`
  - Lines 1502–1511: `apply_snippets()` uses `re.escape(trig)` so user snippets cannot inject invalid regex patterns or cause ReDoS. Non-recursive single substitution prevents infinite loops on self-referential expansions.
  - Lines 1720–1736: Custom vocabulary replacement similarly uses `re.escape(spoken)`.
  - If malformed non-dict items (e.g. `None` or integers) are passed in `snippets` or `vocabulary`, `DictationProcessor` raises `AttributeError`, which `controller._process_dictation_worker` intercepts at line 1908 and sets state to `DictationState.ERROR`.

---

## 2. Logic Chain

1. **State Machine Integrity Under Rapid Hotkey Churn**:
   - Because `handle_hotkey_action()` can be called at millisecond intervals from the Win32 hook thread, race conditions could arise if state checks and worker dispatches were uncoordinated.
   - However, `_state_lock` serializes state transitions, `_ptt_stop_pending` handles early releases during `INITIALIZING`, and `_dictation_session_id` tagging ensures obsolete worker threads discard results rather than inserting stale text.
   - Therefore, a test spamming 50 rapid PTT and POPO toggles across threads must verify that `is_recording` resets to `False`, no thread hangs, and the state settles deterministically to `IDLE` (or `ERROR`), with subsequent sessions functioning normally.

2. **Audio Hardware Invalidation Resilience**:
   - Hardware removal mid-stream (WASAPI unplugging) triggers device invalidation in the underlying PortAudio callback.
   - Because `dictation_recorder.py` catches stream errors, cleans up handles without re-throwing, and notifies `on_error`, which transitions `controller` to `DictationState.ERROR`, the system remains responsive without crashing or deadlocking Qt.
   - Therefore, a test simulating device removal must assert `DictationState.ERROR`, notification emission, handle closure, and clean return to `IDLE` on reset.

3. **Buffer Robustness**:
   - Extreme buffers (NaNs, +/-inf, 0-length, 100MB bursts) can destabilize DSP algorithms and CTranslate2.
   - Since `transcribe_audio` sanitizes NaNs/Infs via `nan_to_num` and rejects < 0.2s audio, and `compute_rms_level` returns finite numbers, the system is mathematically protected.
   - Therefore, a chaos test feeding these anomalous inputs must assert finite outputs, zero unhandled floating-point exceptions, and memory reclamation for 100MB allocations.

4. **Text Injection Safety**:
   - Long strings (> 120 chars) and non-ASCII (Devanagari/emojis) trigger automatic clipboard-paste mode.
   - The clipboard paste mechanism snapshots existing user clipboard data, transfers the payload via UTF-16 Win32 `CF_UNICODETEXT`, and restores the pre-existing clipboard content.
   - If a target window is destroyed before insertion, text is retained on the clipboard rather than lost.
   - Therefore, a test with 10,000+ characters, emojis, and Devanagari text must verify reliable encoding, clipboard restoration, and target closure recovery.

5. **Resource Contention (Meeting vs Dictation)**:
   - Voice typing and Meeting Studio share microphone devices.
   - `start_dictation()` explicitly aborts if meeting recording is active (`return False`), and `start_meeting_recording()` cleans up dictation typing.
   - Therefore, concurrent multi-threaded contention tests must verify that neither session corrupts the other and zero deadlocks occur on `_state_lock`.

6. **SQLite WAL Concurrency**:
   - Multiple threads performing concurrent writes through separate `LocalDatabase` connections rely on SQLite WAL locking and 15s timeout.
   - A stress test with 200 concurrent write operations across 10 threads must complete with 100% integrity (`PRAGMA integrity_check = ok`) and zero unhandled exceptions.

---

## 3. Caveats

- **Offscreen Execution Invariant**: All chaos tests must run headlessly (`QT_QPA_PLATFORM=offscreen`) with `cfg.disable_hotkeys = True` and mocked hardware audio devices, ensuring tests never pop up real desktop windows or capture real microphone input.
- **Windows API Mocking**: Windows APIs like `SendInput` or `SetWindowsHookExW` may return access denied or 0 in CI environments; tests must assert that the application layer handles failure gracefully via fallbacks rather than relying on live OS window focus.
- **CTranslate2 Model Preload**: Heavy Whisper models should be stubbed or use the fast mock/synthetic pipeline to keep the total test execution time under 30 seconds for Tier 5.

---

## 4. Conclusion: Concrete Chaos Test Suite Architecture (`test_tier5_adversarial.py`)

A comprehensive suite of **12 concrete chaos stress tests** is specified below for authoring in `tests/e2e/test_tier5_adversarial.py`. Each test specifies exact setup, chaos injection mechanism, and invariant assertions.

### Test Catalog:

| Test # | Test Name | Chaos Category | Failure / Recovery Invariant Assertions |
|---|---|---|---|
| 01 | `test_01_chaos_rapid_hotkey_spamming_race_condition` | State Machine Race Condition | 50 rapid PTT/POPO events; 0 deadlocks; stale workers discard; final state is IDLE; subsequent session works |
| 02 | `test_02_chaos_audio_device_disconnection_mid_stream` | Audio Hardware Removal | WASAPI device unplugged in LISTENING state; transitions to ERROR; handles closed; recovers to IDLE |
| 03 | `test_03_chaos_corrupted_extreme_audio_buffers` | Corrupted Audio & Buffer Spikes | NaNs, Infs, 0-samples, and 100MB buffer spike; all outputs finite; no OOM; memory freed |
| 04 | `test_04_chaos_extreme_long_text_injection_with_unicode` | Text Injection Stress | 12,000+ chars with emoji & Devanagari; auto-routes to clipboard; original clipboard restored; no data loss |
| 05 | `test_05_chaos_concurrent_meeting_and_dictation_pipeline_contention` | Audio Pipeline Contention | Dictation triggered during active meeting; dictation rejected; meeting unaffected; reverse preemption works |
| 06 | `test_06_chaos_heavy_database_concurrency_multi_connection_wal` | Database Concurrency | 200 concurrent writes across 3 DB instances; zero locked errors escape; PRAGMA integrity_check passes |
| 07 | `test_07_chaos_keyboard_hook_failure_and_injection_api_error` | Win32 Hook / API Errors | SetWindowsHookEx fails (WinError 5); dead HWND 0xDEADBEEF; text preserved to clipboard; no crash |
| 08 | `test_08_chaos_rapid_mode_changes_during_active_transcription` | Pipeline State Race | 100 mode switches during active ASR worker; raw_text preserved; no race exceptions; valid final output |
| 09 | `test_09_chaos_transient_sqlite_exclusive_lock_and_recovery` | Transient DB Schema Lock | Unyielding BEGIN EXCLUSIVE lock; controller handles lock cleanly via ERROR; recovers after unlock |
| 10 | `test_10_chaos_malformed_corrupted_snippets_and_dictionary_payloads` | Payload Fuzzing & Malformation | Corrupt non-dict/None/circular snippets & vocab; regex metachars escaped; no infinite recursion; handled cleanly |
| 11 | `test_11_chaos_ui_geometry_clipping_and_negative_dimension_stress` | UI Geometry Extremes | Negative dimensions (-9999, -9999), 50 rapid expand/collapse toggles; no QPainter crashes; clean recovery |
| 12 | `test_12_chaos_rapid_session_cycling_memory_and_thread_leak` | Resource Leak Stress | 50 consecutive dictation sessions cycled rapidly; threads stabilize; memory bounded; no handle leak |

---

### Detailed Test Specifications:

#### Test 1: Rapid Hotkey Spamming Race Condition
- **Setup**: `isolated_controller`, `qapp_offscreen`. Mock `dictation_recorder.start()` to succeed quickly and `transcribe_dictation` to return `"test"` with a 0.01s sleep.
- **Chaos Injection**:
  - Spawn 5 threads that concurrently call `controller.handle_hotkey_action("ptt", False)`, `controller.handle_hotkey_action("ptt", True)`, and `controller.handle_hotkey_action("popo", False)` 10 times each in tight loops (total 50 hotkey triggers).
- **Assertions**:
  - No deadlock on `_state_lock`.
  - All worker threads terminate within 3.0 seconds.
  - `controller.dictation_recorder.is_recording is False`.
  - Final state is `DictationState.IDLE` or `DictationState.ERROR`.
  - Invoking `controller.start_dictation()` immediately afterwards succeeds cleanly.

#### Test 2: Audio Device Disconnection Mid-Stream
- **Setup**: `isolated_controller`. Start dictation so state transitions to `DictationState.LISTENING`.
- **Chaos Injection**:
  - Simulate sudden WASAPI USB removal by mocking `_mic_stream.read` and `_mic_stream.is_active` to raise `OSError(0x88890004, "AUDCLNT_E_DEVICE_INVALIDATED")`.
  - Call `controller.dictation_recorder.on_error("WASAPI audio device unplugged mid-stream")`.
- **Assertions**:
  - Controller state transitions to `DictationState.ERROR`.
  - Recorded signal received for `notification_requested` with title `"Voice Typing Error"`.
  - `controller.dictation_recorder.is_recording is False`.
  - Calling `controller.cancel_dictation()` returns state to `DictationState.IDLE`.

#### Test 3: Corrupted & Extreme Audio Buffers
- **Setup**: `transcriber`, `devices`.
- **Chaos Injection**:
  1. `nan_inf_buf`: `np.array([np.nan, np.inf, -np.inf, 0.0, 1e20, -1e20] * 500, dtype=np.float32)`.
  2. `zero_buf`: `np.zeros(0, dtype=np.float32)`.
  3. `massive_buf`: 100MB buffer (`np.ones(25_000_000, dtype=np.float32) * 0.1`).
- **Assertions**:
  - `compute_rms_level(nan_inf_buf)` returns a valid float bounded between 0.0 and 1.0.
  - `transcriber.transcribe_audio(nan_inf_buf)` completes without exception and returns finite `DictationResult`.
  - `transcriber.transcribe_audio(zero_buf)` returns empty `raw_text=""`.
  - `DictationRecorder.slice_40ms_frames(zero_buf)` returns `[]`.
  - `DictationRecorder.slice_40ms_frames(massive_buf[:32000])` returns correctly chunked 640-sample frames.
  - Deleting `massive_buf` and running `gc.collect()` reclaims memory cleanly.

#### Test 4: Extreme Long Text Injection with Unicode & Devanagari
- **Setup**: `TextInserter`, `WindowFocusTracker`. Pre-populate clipboard with `"SAFE_USER_CLIPBOARD_DATA"`.
- **Chaos Injection**:
  - Construct a 12,000-character payload consisting of:
    `"नमस्ते दुनिया! Wispr Flow Exact Replica VOX adversarial testing. " + "🚀🔥⚡💡 " * 500 + "A" * 5000`.
  - Call `inserter.insert_text(payload, method="auto", restore_clipboard=True)`.
- **Assertions**:
  - Returns `(True, "clipboard_paste")` (or simulated success).
  - No `UnicodeEncodeError` on UTF-16 surrogate pairs or Devanagari characters.
  - Following the paste delay, clipboard contains `"SAFE_USER_CLIPBOARD_DATA"`.

#### Test 5: Concurrent Meeting & Dictation Pipeline Contention
- **Setup**: `isolated_controller`.
- **Chaos Injection**:
  1. Call `controller.start_meeting_recording(title="Active Meeting")`. State is `"recording"`.
  2. Attempt to call `controller.start_dictation()` and `controller.toggle_dictation()`.
  3. Attempt reverse: put controller in `DictationState.LISTENING`, then call `controller.start_meeting_recording()`.
- **Assertions**:
  - In step 2, `start_dictation()` returns `False`, dictation state remains unchanged, and `notification_requested` emits `"Voice Typing Unavailable"`. Meeting recording continues uninterrupted.
  - In step 3, starting meeting automatically stops/cancels dictation, frees dictation recorder, and transitions meeting state to `"recording"`.

#### Test 6: Heavy Database Concurrency under WAL Write Load
- **Setup**: `isolated_tmp_dir`. Create 3 independent `LocalDatabase` instances on `test_chaos_wal.db`.
- **Chaos Injection**:
  - Launch 10 worker threads performing 20 write transactions each (total 200 writes across dictation entries, scratchpad notes, meeting segments, and vocabulary items) simultaneously without sleep.
- **Assertions**:
  - All 10 threads finish cleanly without unhandled `sqlite3.OperationalError: database is locked`.
  - Total records count across tables equals 200.
  - SQLite `PRAGMA integrity_check` returns `[('ok',)]`.

#### Test 7: Keyboard Hook Failure & Injection Windows API Error
- **Setup**: `GlobalHotkeyManager`, `TextInserter`.
- **Chaos Injection**:
  1. Patch `user32.SetWindowsHookExW` to return 0 with `GetLastError() = 5` (Access Denied).
  2. Call `hotkey_mgr.start()`.
  3. Construct a dead target `TargetWindowInfo(hwnd=0xDEADBEEF, title="Dead", process_name="dead.exe")`.
  4. Call `text_inserter.insert_text("Important transcription", target=dead_target)`.
- **Assertions**:
  - `hotkey_mgr.start()` logs the error and exits hook loop without crashing or raising unhandled exceptions.
  - `text_inserter.insert_text` detects `IsWindow(0xDEADBEEF) == False`, saves the text to Windows clipboard, and returns `(False, "target_closed_saved_to_clipboard")`.
  - `get_clipboard_text()` contains `"Important transcription"`.

#### Test 8: Rapid Mode Changes During Active Transcription
- **Setup**: `isolated_controller`.
- **Chaos Injection**:
  - Launch a background thread that loops 100 times switching `controller.set_writing_mode(m)` across `["verbatim", "clean", "polished", "structured", "email", "invalid_mode", ""]` every 1ms.
  - Simultaneously call `controller._format_and_commit_dictation(raw_text="The quick brown fox", duration_sec=1.5, vocab=[], out_script="auto", target=None)` 10 times from another thread.
- **Assertions**:
  - No `KeyError`, `AttributeError`, or unhandled exceptions.
  - All 10 commits return valid 5-tuples `(final_text, ok, method, title, proc)`.
  - Dual-column raw preservation: `raw_text` in SQLite WAL entries equals `"The quick brown fox"`.

#### Test 9: Transient SQLite Exclusive Lock & Recovery
- **Setup**: `isolated_controller`, `isolated_db`.
- **Chaos Injection**:
  1. Open a raw SQLite connection and execute `BEGIN EXCLUSIVE` on the DB file.
  2. In `isolated_controller.db`, temporarily set connection timeout to 0.05s.
  3. Trigger `isolated_controller._process_dictation_worker(synthetic_pcm, 2.0, target=None)`.
  4. Release the exclusive lock (`ROLLBACK`).
  5. Trigger `isolated_controller._process_dictation_worker(synthetic_pcm, 2.0, target=None)`.
- **Assertions**:
  - During lock: `_process_dictation_worker` handles `OperationalError` gracefully, logs it, transitions state to `DictationState.ERROR`, and does not crash.
  - After rollback: Subsequent dictation worker succeeds and commits to database cleanly.

#### Test 10: Malformed & Corrupted Snippets and Dictionary Payloads
- **Setup**: `DictationProcessor`, `isolated_controller`.
- **Chaos Injection**:
  - Test `DictationProcessor.process_detailed` with corrupted snippets:
    `[{"trigger_phrase": "(a+)+$", "expansion": "DOS"}, {"trigger_phrase": "/sig", "expansion": "Thanks, /sig"}, {"trigger_phrase": "a" * 2000, "expansion": "b" * 2000}]`.
  - Test `controller._process_dictation_worker` with malformed DB payloads containing `None` items.
- **Assertions**:
  - Regex metacharacters are safely escaped via `re.escape`; no ReDoS hangs.
  - Circular snippet expansions expand exactly once without runaway recursion.
  - Corrupted `None` payloads in worker transition to `DictationState.ERROR` without unhandled crashes.

#### Test 11: UI Geometry Extremes & Negative Coordinates Stress
- **Setup**: `offscreen_widgets`. Instantiate `pill` and `drawer`.
- **Chaos Injection**:
  - Call `pill.move(-99999, -99999)`.
  - Call `pill.resize(-100, -100)`.
  - Loop 50 times rapidly toggling `pill.set_expanded(True)` and `pill.set_expanded(False)` without waiting for animation completion.
- **Assertions**:
  - Qt clamps dimensions cleanly; no QPainter or C++ wrapper exceptions.
  - Final state of pill is valid and stable.
  - `qapp_offscreen.processEvents()` flushes event queue without warnings.

#### Test 12: Rapid Session Cycling & Resource Reclamation
- **Setup**: `isolated_controller`.
- **Chaos Injection**:
  - Rapidly execute 50 full dictation cycles: `start_dictation()` -> short synthetic audio -> `stop_dictation_and_insert()` -> wait for idle.
- **Assertions**:
  - `controller._recent_utterance_history` has length `<= 5` (strictly bounded).
  - Background thread count stabilizes back to baseline.
  - Database contains 50 distinct entries without duplicate IDs or open transactions.

---

## 5. Verification Method

To verify the Tier 5 Adversarial Chaos Suite once authored:

```powershell
# 1. Run the Tier 5 Adversarial Chaos Suite in isolation:
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier5_adversarial.py -v

# 2. Run the complete 5-Tier E2E regression suite:
.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v

# 3. Verify standalone production binary with smoke test:
$p = Start-Process -FilePath ".\dist\VoxLocal\VoxLocal.exe" -ArgumentList "--smoke-test-ms 1500" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Executable smoke test failed" }
```

### Invalidation Conditions:
- Any test fails or throws an unhandled exception.
- Any test pops up an on-screen window, steals foreground focus, or hangs the test runner.
- State machine fails to return to `IDLE` or `ERROR` after fault injection.
- Total execution time of Tier 5 exceeds 45 seconds.

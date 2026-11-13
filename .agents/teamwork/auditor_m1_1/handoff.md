# Forensic Audit Report: Milestone 1 (Codebase Triage & Startup Registry Resilience)

**Work Product**: Milestone 1 Implementation (`src/voxlocal/win32/startup.py`, `src/voxlocal/core/controller.py`, `tests/test_unit_suite.py`, root file deletions, whitelist preservation)
**Profile**: General Project
**Integrity Mode**: Development (per `ORIGINAL_REQUEST.md` line 8)

Verdict: CLEAN

---

## 1. Observation

### 1.1 Source Code Inspection
1. **`src/voxlocal/win32/startup.py`**:
   - Lines 25–26: `_warned_access_denied: bool = False` introduces a single-warning latch.
   - Lines 43–56 (`is_available()`): Empirically probes `winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)`. Returns `True` if writable, catches `(PermissionError, OSError)` and returns `False`.
   - Lines 58–77 (`is_enabled()`): Implements least-privilege query using `winreg.KEY_QUERY_VALUE`. Catches `FileNotFoundError` (returns `False`), and `(PermissionError, OSError)` (logs at debug level and returns `False`).
   - Lines 79–114 (`set_enabled()`): Executes genuine `winreg.SetValueEx` or `winreg.DeleteValue` on the `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key. On `PermissionError` or `OSError`, logs a single warning on first occurrence, subsequently logs debug, and returns `False` without raising unhandled exceptions.
   - **Forensic Check**: No hardcoded test bypasses, no dummy constants, no facade implementation.

2. **`src/voxlocal/core/controller.py`**:
   - Lines 149–156: Guarded startup registry synchronization:
     ```python
     if StartupManager.is_available():
         if self.config.launch_at_login != StartupManager.is_enabled():
             StartupManager.set_enabled(
                 self.config.launch_at_login,
                 minimized=self.config.start_minimized_to_tray,
             )
     else:
         logger.debug("Windows startup registry is not accessible; skipping login sync.")
     ```
   - **Forensic Check**: Controller cleanly checks availability before attempting write operations, preventing `WinError 5` log spam or crashes during startup.

3. **`tests/test_unit_suite.py`**:
   - Lines 68–87 (`test_windows_startup_registry_roundtrip`):
     - When `StartupManager.is_available()` is `False` (sandboxed/restricted token):
       - Asserts `self.assertFalse(StartupManager.is_enabled())`.
       - Asserts `self.assertFalse(StartupManager.set_enabled(True, minimized=True))`.
       - Asserts `self.assertFalse(StartupManager.set_enabled(False))`.
       - Exercises write logic via `unittest.mock.patch` with `winreg.OpenKey`, `winreg.SetValueEx`, and `winreg.DeleteValue`, verifying `mock_set.called` and `mock_del.called`.
     - When `StartupManager.is_available()` is `True` (unrestricted host):
       - Executes live registry round-trip: enables startup, asserts `is_enabled() == True`, disables startup, asserts `is_enabled() == False`, and restores original state in `finally` block.
   - **Forensic Check**: The test does NOT skip execution (`@unittest.skip` is absent; `pytest.skip` is absent). It asserts genuine behavior in both environments.

4. **Win32 Subsystems Inspection**:
   - `src/voxlocal/win32/hotkeys.py`: Genuine native Win32 `WH_KEYBOARD_LL` hook with dedicated message pump thread, virtual key mapping, modifier chord tracking, and non-elevated global hotkey interception.
   - `src/voxlocal/win32/text_inserter.py`: Genuine 3-tier insertion engine using Win32 `SendInput(KEYEVENTF_UNICODE)`, UIPI `WM_CHAR` direct message fallback, and safe atomic clipboard snapshot/paste/restoration.

---

### 1.2 Deletion & Root Triage Verification
1. **Target Deleted Files**:
   Checked existence of `temp_dbg.mp4`, `temp_target.txt`, `temp_test_src.mp4`, `test_dummy.mp4`, `test_pts.mp4`, `test_rate.mp4`, `test_rate5.mp4`, `test_remux.mp4`, `test_remux2.mp4`, and `patch_precision.py`.
   - Result: All 10 files confirmed deleted from root.
2. **Hidden / Relocation Check**:
   Executed recursive scan across the entire workspace `d:\Milind\Voice Assistant` for all 10 target filenames:
   - Tool Command:
     ```powershell
     Get-ChildItem -Path "d:\Milind\Voice Assistant" -Recurse -File -Include "temp_dbg.mp4","temp_target.txt","temp_test_src.mp4","test_dummy.mp4","test_pts.mp4","test_rate.mp4","test_rate5.mp4","test_remux.mp4","test_remux2.mp4","patch_precision.py" | Select-Object FullName
     ```
   - Verbatim Output: Zero files returned (`0` matches). The deleted files were NOT moved or renamed into other directories.
3. **Stray Directory Deletions**:
   - `d:\Milind\Voice Assistant\MagicMock`: `Test-Path` returned `False`. Recursive search confirmed non-existence.
   - `d:\Milind\Voice Assistant\base`: `Test-Path` returned `False`.

---

### 1.3 Whitelist Preservation Verification
Verified that required project components, tests, configuration, documentation, and distributions remain intact and uncorrupted:
- `d:\Milind\Voice Assistant\main.py`: 10,928 bytes (intact)
- `d:\Milind\Voice Assistant\requirements.txt`: 204 bytes (intact)
- `d:\Milind\Voice Assistant\VoxLocal.spec`: 4,488 bytes (intact)
- `d:\Milind\Voice Assistant\PROJECT.md`: 11,832 bytes (intact)
- `d:\Milind\Voice Assistant\dist\VoxLocal\VoxLocal.exe`: 19,073,758 bytes (intact)
- Directory trees `src/`, `tests/`, `data/`, `models/`, `assets/`, `scripts/`: all confirmed intact.

---

### 1.4 Independent Test Executions
1. **Unit Test Suite (`tests/test_unit_suite.py`)**:
   - Command:
     `& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_unit_suite.py -v`
   - Verbatim Output:
     ```
     ============================= test session starts =============================
     platform win32 -- Python 3.13.14, pytest-9.2.0, pluggy-1.7.0 -- D:\Milind\Voice Assistant\.venv\Scripts\python.exe
     cachedir: .pytest_cache
     rootdir: D:\Milind\Voice Assistant
     configfile: pytest.ini
     plugins: anyio-4.15.1
     collecting ... collected 6 items

     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_database_retention_and_wipe PASSED [ 16%]
     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_dictation_processor_punctuation_and_vocab PASSED [ 33%]
     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_hotkey_parser_and_conflict_guard PASSED [ 50%]
     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_missing_device_graceful_fallback PASSED [ 66%]
     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_repeated_start_stop_cycles PASSED [ 83%]
     tests/test_unit_suite.py::VoxLocalHardeningTestSuite::test_windows_startup_registry_roundtrip PASSED [100%]

     ============================== 6 passed in 4.71s ==============================
     ```
   - Exit code: `0`. Failures: `0`.

2. **Windows Installation and Recovery Suite (`tests/test_phase13_windows_installation_and_recovery.py`)**:
   - Command:
     `& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_phase13_windows_installation_and_recovery.py -v`
   - Verbatim Output:
     ```
     ============================= test session starts =============================
     platform win32 -- Python 3.13.14, pytest-9.2.0, pluggy-1.7.0 -- D:\Milind\Voice Assistant\.venv\Scripts\python.exe
     cachedir: .pytest_cache
     rootdir: D:\Milind\Voice Assistant
     configfile: pytest.ini
     plugins: anyio-4.15.1
     collecting ... collected 6 items

     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_database_snapshot_and_rollback_readiness PASSED [ 16%]
     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_installer_script_paths_and_syntax PASSED [ 33%]
     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_standalone_distribution_assets_and_binary PASSED [ 50%]
     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_startup_manager_command_construction PASSED [ 66%]
     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_startup_manager_registry_mock_toggle PASSED [ 83%]
     tests/test_phase13_windows_installation_and_recovery.py::TestPhase13WindowsInstallationAndRecovery::test_unclean_meeting_startup_recovery PASSED [100%]

     ============================== 6 passed in 1.45s ==============================
     ```
   - Exit code: `0`. Failures: `0`.

3. **Controller Startup and Shutdown Cleanliness**:
   - Command:
     ```powershell
     & "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -c "
     import sys
     sys.path.insert(0, 'src')
     from voxlocal.core.config import AppConfig
     from voxlocal.core.controller import AppController
     cfg = AppConfig.load()
     ctrl = AppController(cfg)
     print('Controller initialized successfully without crash.')
     ctrl.shutdown()
     "
     ```
   - Verbatim Output:
     ```
     2026-11-12 06:42:11 | INFO    | voxlocal | [system] VoxLocal logging initialized at D:\Milind\Voice Assistant\data\logs\voxlocal.log (RAM=23.4MB, handles=170, threads=1, free_disk=76706MB)
     2026-11-12 06:42:14 | INFO    | voxlocal.win32.hotkeys | [system] Installed Win32 global keyboard hook (thread_id=24136)
     Controller initialized successfully without crash.
     2026-11-12 06:42:14 | INFO    | voxlocal.controller | [system] Shutting down AppController...
     2026-11-12 06:42:14 | INFO    | voxlocal.win32.hotkeys | [system] Uninstalled Win32 global keyboard hook.
     ```
   - Exit code: `0`. Total `ERROR` messages: `0`.

4. **Standalone Binary Smoke Test**:
   - Command:
     `& "d:\Milind\Voice Assistant\dist\VoxLocal\VoxLocal.exe" --smoke-test-ms 1000`
   - Verbatim Output: Exit code `0` (clean shutdown).

5. **Broader Regression Suite (`tests/test_wispr_parity_features.py`)**:
   - Command:
     `& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_wispr_parity_features.py -v`
   - Verbatim Output: `10 passed in 7.71s`. Exit code `0`. Failures: `0`.

---

## 2. Logic Chain

1. **Root Triage Logic**:
   - Observation 1.2 confirmed that 10 obsolete debug files (`temp_*.mp4`, `test_*.mp4`, `patch_precision.py`) and 2 obsolete directories (`MagicMock`, `base`) are completely gone from the repository root.
   - Observation 1.2 also empirically verified that these files were not secretly moved or renamed to fake clean status (recursive search returned 0 matches).
   - Therefore, the codebase cleanup is genuine and complete.

2. **Registry Resilience Logic**:
   - Observation 1.1 showed that `StartupManager.is_available()` performs a real `winreg.OpenKey` call with write permissions to detect whether write access is permitted.
   - When running under sandboxed/restricted environments, `PermissionError` is caught, logged once as a warning, and returns `False` instead of aborting the process or failing with an unhandled exception.
   - `AppController` inspects `StartupManager.is_available()` before attempting startup registration, preventing boot crashes and log clutter.
   - Therefore, the startup manager resilience is genuinely implemented without facades or dummy returns.

3. **Test Integrity Logic**:
   - Observation 1.1 and 1.4 confirmed that `tests/test_unit_suite.py` was not modified to skip the test or blindly return `True`.
   - When registry access is denied, it asserts that `is_enabled()` and `set_enabled()` return `False` without throwing exceptions, and then exercises the mock patch path to verify `winreg.SetValueEx` and `winreg.DeleteValue` call validity.
   - All 6 tests in `test_unit_suite.py` executed independently and passed with 0 failures.
   - Therefore, the test suite genuinely validates functionality.

4. **Preservation Logic**:
   - Observation 1.3 confirmed all core files (`main.py`, `PROJECT.md`, `requirements.txt`, `dist/VoxLocal/VoxLocal.exe`, etc.) and directories (`src/`, `tests/`) remain intact.
   - Observation 1.4 confirmed regression suites and the standalone distribution binary execute cleanly without breakage.
   - Therefore, no regression or corruption occurred.

---

## 3. Caveats

1. **Environment-Dependent Registry Writes**:
   - On unrestricted Windows user profiles, `StartupManager.is_available()` returns `True` and live registry keys are manipulated. In restricted sandbox environments, it returns `False` and falls back gracefully. Both execution branches are covered by `test_unit_suite.py`.
2. **Preservation of `scratch/`**:
   - The `scratch/` folder contains development scratch scripts and audio fixtures. In accordance with `SCOPE.md`, cleanup was strictly focused on root clutter (`*.mp4`, `patch_precision.py`, `MagicMock/`, `base/`). `scratch/` was preserved as intended.

---

## 4. Conclusion

Verdict: CLEAN

All changes submitted for Milestone 1 are genuine, robust, and verified empirically. There are:
- NO hardcoded test results or bypasses.
- NO dummy or facade implementations.
- NO fabricated test results or pre-populated outputs.
- NO evasion of file deletions.
- Complete preservation of all whitelisted assets and executables.
- 100% test pass rate on all unit, regression, and binary smoke tests.

---

## 5. Verification Method

To independently reproduce the forensic verification findings, run the following commands in PowerShell from `d:\Milind\Voice Assistant`:

1. **Verify Root Deletions (0 *.mp4 files)**:
   ```powershell
   $mp4Count = (Get-ChildItem -Path "d:\Milind\Voice Assistant" -File -Filter "*.mp4" | Measure-Object).Count
   if ($mp4Count -ne 0) { throw "Expected 0 mp4 files in root, found $mp4Count" }
   Test-Path "d:\Milind\Voice Assistant\temp_target.txt"     # Expected: False
   Test-Path "d:\Milind\Voice Assistant\patch_precision.py"   # Expected: False
   Test-Path "d:\Milind\Voice Assistant\MagicMock"            # Expected: False
   Test-Path "d:\Milind\Voice Assistant\base"                # Expected: False
   ```

2. **Run Unit Hardening Test Suite**:
   ```powershell
   & "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_unit_suite.py -v
   ```
   *Expected*: `6 passed in ~4s`, exit code `0`.

3. **Run Windows Installation and Recovery Suite**:
   ```powershell
   & "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_phase13_windows_installation_and_recovery.py -v
   ```
   *Expected*: `6 passed in ~1.5s`, exit code `0`.

4. **Verify Clean AppController Boot**:
   ```powershell
   & "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -c "
   import sys
   sys.path.insert(0, 'src')
   from voxlocal.core.config import AppConfig
   from voxlocal.core.controller import AppController
   cfg = AppConfig.load()
   ctrl = AppController(cfg)
   print('Controller initialized successfully without crash.')
   ctrl.shutdown()
   "
   ```
   *Expected*: Prints `Controller initialized successfully without crash.`, exit code `0`, zero `ERROR` logs.

5. **Verify Standalone Executable**:
   ```powershell
   & "d:\Milind\Voice Assistant\dist\VoxLocal\VoxLocal.exe" --smoke-test-ms 1000
   ```
   *Expected*: Clean exit with return code `0`.

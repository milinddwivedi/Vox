# Test Harness Architecture & Integration Specification for Tiers 4 & 5

**Author**: Test Harness Explorer  
**Target Suites**: `tests/e2e/test_tier4_scenarios.py` and `tests/e2e/test_tier5_adversarial.py`  
**State Folder**: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_tier4_harness_1\`  
**Date**: 2026-11-12T13:43:30Z  

---

## 1. Observation

Direct inspection of the VOX E2E test harness and test execution yielded the following concrete observations:

### 1.1 Test Runner & Execution Baseline
- **Configuration File** (`pytest.ini`):
  ```ini
  [pytest]
  testpaths = tests/e2e tests
  python_files = test_*.py
  python_classes = Test*
  python_functions = test_*
  addopts = -v --tb=short
  filterwarnings =
      ignore::DeprecationWarning
      ignore::ResourceWarning
      ignore::UserWarning
  ```
- **Existing E2E Test Suite Execution**:
  Command: `.\.venv\Scripts\python.exe -m pytest tests/e2e/ -q`
  Result:
  ```
  collected 222 items
  tests\e2e\test_tier1_feature_coverage.py (100 passed)
  tests\e2e\test_tier2_boundaries.py (100 passed)
  tests\e2e\test_tier3_pairwise.py (22 passed)
  ============================ 222 passed in 38.64s =============================
  ```
  Pass rate: 100% (0 failures, 0 errors, 0 warnings).

### 1.2 Shared Fixtures in `tests/e2e/conftest.py`
Direct lines observed in `tests/e2e/conftest.py`:
1. **`qapp_offscreen`** (Lines 54-64):
   ```python
   @pytest.fixture(scope="session")
   def qapp_offscreen() -> Generator[QApplication, None, None]:
       os.environ["QT_QPA_PLATFORM"] = "offscreen"
       app = QApplication.instance()
       if app is None:
           app = QApplication(["vox_e2e_runner", "-platform", "offscreen"])
       app.setQuitOnLastWindowClosed(False)
       yield app
       app.processEvents()
   ```
2. **`isolated_env`** (Lines 66-73):
   ```python
   @pytest.fixture(autouse=True)
   def isolated_env(monkeypatch) -> None:
       monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
       monkeypatch.setattr(StartupManager, "is_enabled", staticmethod(lambda: False))
       monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda *a, **k: True))
   ```
3. **`isolated_tmp_dir`** (Lines 75-81):
   Creates isolated sandboxed directory per test with `data/`, `snapshots/`, and `logs/` subfolders.
4. **`isolated_db`** (Lines 84-90):
   Yields fresh `LocalDatabase(isolated_tmp_dir / "voxlocal_test.db")` initialized in SQLite WAL mode.
5. **`isolated_config`** (Lines 92-113):
   Yields sandboxed `AppConfig` with `storage_dir=str(isolated_tmp_dir)`, `models_dir=str(isolated_tmp_dir / "models")`, `launch_at_login=False`, `llm_enabled=False`, `auto_record_calls=False`, `prompt_on_call=False`, `progressive_transcription=False`, `sound_effects=False`, `disable_hotkeys=True`, and `disable_model_preload=True`.
6. **`isolated_controller`** (Lines 115-128):
   ```python
   @pytest.fixture
   def isolated_controller(
       qapp_offscreen: QApplication,
       isolated_config: AppConfig,
       isolated_env: None,
   ) -> Generator[AppController, None, None]:
       ctrl = AppController(isolated_config)
       try:
           yield ctrl
       finally:
           ctrl.shutdown()
           qapp_offscreen.processEvents()
   ```
7. **`synthetic_audio`** (Lines 130-149):
   Factory function `synthetic_audio(duration_sec=1.0, freq_hz=440.0, amplitude=0.5, silence=False) -> np.ndarray` returning 16kHz mono `np.float32` arrays in range `[-1.0, 1.0]`. Safely returns empty arrays when `duration_sec <= 0`.
8. **`mock_calendar`** (Lines 152-158):
   Yields authenticated `MockCalendarProvider` with methods `inject_meeting(CalendarMeeting)` and `get_upcoming_meetings(window_minutes)`.
9. **`offscreen_widgets`** (Lines 160-228):
   Tracks and yields factory dict for: `"pill"`, `"main_window"`, `"scratchpad"`, `"drawer"`, `"wizard"`, `"palette"`, `"meeting_studio"`.
   Teardown automatically executes `w.close()`, `w.deleteLater()`, followed by `qapp_offscreen.processEvents()`.

### 1.3 Established Mocking Patterns in Tiers 1-3
Directly extracted from `tests/e2e/test_tier3_pairwise.py` (lines 98-143, 212-239, 428-454, 787-810):
- **Mocking Text Inserter**:
  ```python
  inserted_texts: List[str] = []
  isolated_controller.text_inserter.insert_text = MagicMock(
      side_effect=lambda text, **kw: (inserted_texts.append(text), (True, "sendinput_unicode"))[1]
  )
  ```
- **Mocking Speech Transcriber**:
  ```python
  isolated_controller.transcriber.transcribe_dictation = MagicMock(
      return_value="Transcribed speech payload"
  )
  ```
- **Mocking Audio Capture**:
  ```python
  dummy_audio = np.zeros(16000, dtype=np.float32)  # > 3200 samples threshold
  isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 1.0))
  ```
- **Direct Hotkey Triggering via Controller**:
  ```python
  isolated_controller.handle_hotkey_action("ptt", is_release=False)  # start
  qapp_offscreen.processEvents()
  isolated_controller.handle_hotkey_action("ptt", is_release=True)   # stop & commit
  qapp_offscreen.processEvents()
  ```
- **Waiting for Background Worker Thread Completion**:
  ```python
  for _ in range(50):
      if inserted_texts or isolated_controller.dictation_state in ("idle", "completed"):
          break
      time.sleep(0.04)
      qapp_offscreen.processEvents()
  ```

---

## 2. Logic Chain

1. **Test Isolation Invariant**:
   - `isolated_config` forces `disable_hotkeys = True` and `disable_model_preload = True`, preventing low-level OS keyboard hooking and large Whisper model weight downloads.
   - Any scenario test (Tier 4) or chaos test (Tier 5) requiring dictation must mock `isolated_controller.transcriber.transcribe_dictation` and `isolated_controller.dictation_recorder.stop`.
   - Bypassing this or failing to mock will trigger live microphone capture or model downloads, breaking the offline execution budget and causing CI test timeouts.

2. **Qt Signal & Event Loop Mechanics**:
   - `AppController` coordinates subsystems across background worker threads (`_streaming_worker_thread`, `_interim_worker_thread`, and dictation worker threads).
   - Signals emitted across thread boundaries in Qt (QueuedConnection) are only dispatched when the recipient thread's event loop executes.
   - In offscreen pytest execution, the main thread does not run `app.exec()`. Therefore, test steps MUST call `qapp_offscreen.processEvents()` immediately after:
     - Invoking UI actions or setting widget inputs (`txt_search.setText(...)`, `setCurrentIndex(...)`).
     - Emitting controller signals (`isolated_controller.calendar_upcoming_changed.emit(...)`).
     - Waiting on background worker threads in polling loops (`time.sleep(0.03)` + `qapp_offscreen.processEvents()`).

3. **Multi-Step State Machine Transitions**:
   - Controller dictation state follows: `idle` -> `initializing` -> `listening` -> `stopping` -> `processing` -> `idle`.
   - Calling `handle_hotkey_action("ptt", is_release=False)` enters `listening`.
   - Calling `handle_hotkey_action("ptt", is_release=True)` triggers `stop_dictation_and_insert()`.
   - If audio sample count is below minimum threshold (3200 samples / 200ms), controller treats it as accidental click and reverts to `idle` without transcribing. Therefore, mocked audio returned by `dictation_recorder.stop` MUST have `len(audio) >= 3200` (e.g. 16,000 samples for 1 second).

4. **Database & Teardown Cleanliness**:
   - `isolated_controller` runs in SQLite WAL mode. On teardown, `isolated_controller` fixture calls `ctrl.shutdown()`, which cancels timers, sets `_cancel_ask_vox = True`, sets `_interim_worker_running = False`, stops recorders, and frees database handles.
   - In Tier 5 chaos testing, when intentionally stressing SQLite with concurrent threads, all test-spawned threads must be joined before fixture teardown (`for t in threads: t.join()`), otherwise unjoined threads writing to SQLite will conflict with database file removal by `tmp_path`.

5. **No Duplication of Setup Code**:
   - Tiers 4 and 5 must NOT declare custom `QApplication` instances, create manual SQLite databases in root folders, or monkeypatch `StartupManager` ad-hoc.
   - All tests in `test_tier4_scenarios.py` and `test_tier5_adversarial.py` should directly accept the canonical fixtures: `isolated_controller`, `qapp_offscreen`, `offscreen_widgets`, `synthetic_audio`, `mock_calendar`, `isolated_db`, `isolated_config`, `isolated_tmp_dir`.

---

## 3. Caveats

1. **Hardware Profiler & GPU Fallback**: Tests run in non-CUDA offscreen environments. Whisper GPU operations fall back to CPU multi-threading. Model inference is bypassed via mocking in E2E tests for speed.
2. **Win32 Hooks**: Testing global keyboard interception does not install real `WH_KEYBOARD_LL` hooks into the Windows OS during pytest (as this would intercept the user's real keyboard during test execution). Instead, tests invoke `isolated_controller.handle_hotkey_action()` or unit-test `parse_hotkey_string` and `normalize_vk` directly.
3. **No Audio Device Dependency**: Real physical microphones may not be present on build machines or CI containers. The test harness relies strictly on `synthetic_audio` and mocked `DictationRecorder` returns.

---

## 4. Conclusion & Test Authoring Specifications

### 4.1 Canonical Imports for Tier 4 and Tier 5 Test Authors
Test authors should adopt the exact standard import block:
```python
from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, DictationState
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.win32.text_inserter import TextInserter
from voxlocal.audio.devices import compute_rms_level, StudioVocalDSP
from voxlocal.engine.transcriber import (
    estimate_acoustic_signature,
    extract_speaker_embedding,
)
from voxlocal.engine.dictation_processor import (
    DictationProcessor,
    DictationProcessedResult,
    TransformationMode,
)
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.main_window import GlobalSearchDialog, MainWindow
from voxlocal.ui.command_palette import PaletteCommand, VoxCommandPaletteDialog
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.tray_manager import SystemTrayManager
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.calendar.mock_provider import MockCalendarProvider
```

### 4.2 Tier 4 Real-World Application Scenarios Roadmap (`tests/e2e/test_tier4_scenarios.py`)
Must deliver at least 10 comprehensive multi-step workflow scenarios:
1. `test_scenario_01_first_run_onboarding_to_first_dictation`: Complete 4-step wizard, persist preferences, trigger initial PTT dictation, verify WAL database history.
2. `test_scenario_02_multimode_productivity_session`: Sequential dictation across all 5 modes (Verbatim code -> Clean message -> Polished proposal -> Structured task list -> Email client reply).
3. `test_scenario_03_live_verbal_self_correction_workflow`: Dictate multi-phrase statement with spoken corrections ("scratch that", "no wait"), verify preview bubble strikethrough styling and clean text injection.
4. `test_scenario_04_snippet_expansion_and_vocabulary_adaptation`: Add technical jargon to custom dictionary + shortcuts to snippets -> dictate phrase -> verify full expansion and usage counters.
5. `test_scenario_05_pre_meeting_calendar_alert_and_notetaker_capture`: Calendar fires 15m/5m advance alert -> popup launches Notetaker drawer -> user logs quick thoughts -> verify SQLite persistence.
6. `test_scenario_06_meeting_studio_diarized_recording_and_mom_generation`: Record multi-turn meeting -> populate diarized transcript -> generate summary, decisions, action items -> verify persistence.
7. `test_scenario_07_keyboard_only_command_palette_power_user`: Use `Ctrl+K` palette to search commands, navigate workspaces, switch modes, and toggle dictation purely via keyboard handlers.
8. `test_scenario_08_scratchpad_document_authoring_and_version_revisions`: Multi-step scratchpad authoring -> revision saves -> version history listing -> note content reloading.
9. `test_scenario_09_desktop_overlay_pill_interaction_and_waveform`: Live RMS audio streaming updates equalizer bars -> mode badge switching -> stop button triggers graceful text insertion.
10. `test_scenario_10_offline_data_portability_and_raw_transcript_audit`: Multi-session dictation followed by dual-column raw text recovery and JSON/Markdown export verification.

### 4.3 Tier 5 Adversarial Chaos Suite Roadmap (`tests/e2e/test_tier5_adversarial.py`)
Must deliver at least 10 chaos and stress tests:
1. `test_chaos_01_rapid_hotkey_mashing_and_reentrancy`: 100 rapid start/stop/cancel toggles within 500ms to stress state machine locks and verify return to `idle`.
2. `test_chaos_02_corrupted_audio_stream_nan_inf_extremes`: Ingest NaN, Inf, extreme clipping (+50dB), and 0-sample chunks; verify DSP sanitization without application crashes.
3. `test_chaos_03_sqlite_wal_high_concurrency_race`: 8 simultaneous worker threads continuously performing concurrent writes, reads, note creations, and snippet updates under WAL mode.
4. `test_chaos_04_unfinalized_meeting_recovery_on_abrupt_exit`: Simulate sudden application kill during active recording; verify `_recover_unfinalized_meetings()` restores state to `interrupted`.
5. `test_chaos_05_transcriber_exception_containment`: Mock transcriber raising `RuntimeError("CUDA Out of Memory")`; verify controller recovers cleanly to `error`/`idle` without unhandled PyQt6 crashes.
6. `test_chaos_06_massive_payload_stress_100k_chars`: Feed 100,000+ character strings and Devanagari/astral emoji text through processor and text inserter.
7. `test_chaos_07_text_inserter_cascading_fallback`: Simulate SendInput failure and UIPI restriction; verify graceful fallback to clipboard paste.
8. `test_chaos_08_hostile_environment_missing_audio_devices`: Run controller when audio device enumeration returns empty lists; verify graceful notifications rather than null pointer exceptions.
9. `test_chaos_09_signal_event_flood_ui_resilience`: Flood `audio_level_changed` with 5,000 rapid signals while concurrently navigating Hub workspace tabs.
10. `test_chaos_10_memory_and_thread_leak_endurance`: Run 40 successive dictation and note creation cycles; verify QObject deletion, thread joins, and zero dangling WAL locks.

---

## 5. Verification Method

### 5.1 Independent Test Suite Verification Commands
```powershell
# 1. Verify existing 222 tests in Tiers 1-3 remain 100% passing:
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier1_feature_coverage.py tests/e2e/test_tier2_boundaries.py tests/e2e/test_tier3_pairwise.py -v

# 2. Run Tier 4 Scenarios once authored:
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier4_scenarios.py -v

# 3. Run Tier 5 Adversarial Chaos once authored:
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier5_adversarial.py -v

# 4. Full 5-Tier Turnkey Regression Suite:
.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v
```

### 5.2 Acceptance Criteria for Release
- **Total Test Count**: >= 242 tests across all 5 tiers (Tier 1: 100, Tier 2: 100, Tier 3: 22, Tier 4: >= 10, Tier 5: >= 10).
- **Pass Rate**: 100% (0 failures, 0 errors).
- **Execution Time**: Complete 5-tier suite executes in <= 90 seconds.
- **Headless Guarantee**: `QT_QPA_PLATFORM=offscreen`, 0 desktop popups, 0 OS window focus steals.

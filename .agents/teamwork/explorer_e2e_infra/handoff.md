# E2E Testing Infrastructure Exploration Report

**State Folder**: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_infra`  
**Milestone**: `M_E2E_0: Test Infrastructure & TEST_INFRA.md`  
**Date**: 2026-11-12  

---

## 1. Observation

### 1.1 Python Environment & Tooling Verification
Direct command execution:
```powershell
.\.venv\Scripts\python.exe -V
# Output: Python 3.13.14

.\.venv\Scripts\python.exe -m pytest --version
# Output: pytest 9.2.0

.\.venv\Scripts\python.exe -m pip list
```
Key packages installed:
- `PyQt6` (6.11.0), `PyQt6-Qt6` (6.11.2), `PyQt6_sip` (13.13.0)
- `faster-whisper` (1.2.1), `ctranslate2` (4.8.3), `onnxruntime` (1.31.0)
- `pywin32` (312), `pywin32-ctypes` (0.2.3)
- `numpy` (2.5.4), `scipy` (1.18.1), `PyAudioWPatch` (0.2.12.9), `sounddevice` (0.5.6)
- `pyinstaller` (6.22.3), `anyio` (4.15.1), `iniconfig` (2.3.1), `pluggy` (1.7.0)

`pytest-qt` and `pytest-mock` are **not installed**; standard library `unittest.mock` and standard `monkeypatch` / `tmp_path` fixtures in `pytest` are used.

### 1.2 Existing Tests Layout & Configuration
Direct inspection of workspace filesystem:
- Root directory contains no `pytest.ini`, `setup.cfg`, or `pyproject.toml`.
- `tests/` directory contains 55 flat test scripts (e.g., `test_wispr_parity_features.py`, `test_e2e_dictation_and_modes.py`, `test_header_compliance.py`).
- There is currently **no `tests/conftest.py`** and **no `tests/e2e/`** subdirectory.
- Test run verification:
  - `.\.venv\Scripts\python.exe -m pytest tests/test_e2e_dictation_and_modes.py` -> **17 passed in 0.65s**.
  - `.\.venv\Scripts\python.exe -m pytest tests/test_wispr_parity_features.py -k "test_home_inline_correction_autolearn_undo_and_transform"` -> **1 passed in 3.93s**.

### 1.3 Standalone Binary Verification (Feature 19)
- Executable located at: `dist/VoxLocal/VoxLocal.exe` (File size: 19,073,758 bytes).
- Subprocess execution:
  ```powershell
  $p = Start-Process -FilePath ".\dist\VoxLocal\VoxLocal.exe" -ArgumentList "--smoke-test-ms 1500" -Wait -PassThru
  $p.ExitCode
  # Output: 0
  ```
  The production binary initialises its complete Qt runtime and exits cleanly with exit code 0 when invoked with `--smoke-test-ms`.

### 1.4 Headless Qt & Offscreen Execution Verification
- Setting `os.environ["QT_QPA_PLATFORM"] = "offscreen"` enables full Qt widget creation and event handling without opening any desktop window:
  ```powershell
  .\.venv\Scripts\python.exe -c "import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; from PyQt6.QtWidgets import QApplication, QWidget; app = QApplication([]); w = QWidget(); w.show(); print('QPA:', app.platformName(), 'Visible:', w.isVisible())"
  # Output: QPA: offscreen Visible: True
  ```

### 1.5 Controller Hooks, Background Threads & Isolation Flags
Direct inspection of `src/voxlocal/core/controller.py`:
- Line 293: `if not getattr(self.config, "disable_hotkeys", False):` -> Setting `cfg.disable_hotkeys = True` bypasses `WH_KEYBOARD_LL` Windows hook installation.
- Line 314: `if not getattr(self.config, "disable_model_preload", False):` -> Setting `cfg.disable_model_preload = True` bypasses background Whisper model loading.
- Lines 3505-3530: `ctrl.shutdown()` terminates `_focus_timer`, `_call_timer`, background worker threads, and active audio capture streams.
- Lines 41-60 of `src/voxlocal/win32/startup.py`: `StartupManager.is_enabled()` and `set_enabled()` attempt direct access to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Without mock in restricted sandbox, this raises `[WinError 5] Access is denied`. Mocking `StartupManager.is_enabled = staticmethod(lambda: False)` and `StartupManager.set_enabled = staticmethod(lambda *a, **k: True)` eliminates registry dependency.

### 1.6 Database WAL & Call Semantics
Direct inspection of `src/voxlocal/storage/database.py`:
- `LocalDatabase(db_path)` enables SQLite WAL mode (`PRAGMA journal_mode=WAL`).
- Lines 1891-1914: `add_dictation(self, raw_text: str, cleaned_text: str = "", final_text: str = "", duration_sec: float = 0.0, target_window: str = "", ...)` preserves `raw_text` immutably. Keyword arguments are required for non-default fields.

### 1.7 Current Root `TEST_INFRA.md` Status
Direct inspection of `d:\Milind\Voice Assistant\TEST_INFRA.md`:
- Current file was authored during an earlier milestone (dated 2026-11-08) covering an older 18-feature inventory and older test files.
- It needs to be updated to match the new 20-feature inventory in `PROJECT.md`, the 4-tier directory layout in `tests/e2e/`, and the test runner command `.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v`.

---

## 2. Logic Chain

1. **Environment Compatibility (Observation 1.1)**:
   The existing Python virtual environment (`.\.venv\Scripts\python.exe`) contains Python 3.13.14, pytest 9.2.0, PyQt6 6.11.0, numpy 2.5.4, and pywin32. All core dependencies for running VOX and executing tests are functional.
2. **Headless Safety & Invariance (Observation 1.4)**:
   E2E tests must never disrupt the user's active desktop, pop up dialogs, steal window focus, or install global keyboard hooks. By setting `QT_QPA_PLATFORM=offscreen` before any Qt widget is initialized, the Qt event engine runs headlessly in memory.
3. **Hardware Independence & Audio Synthesis (Observations 1.1 & 1.5)**:
   Tests cannot rely on a physical microphone hardware device or WASAPI loopback endpoint being present in automated CI. Because `src/voxlocal/audio/` and `src/voxlocal/engine/` operate on standard 16kHz mono float32 numpy arrays, tests can feed synthetic numpy arrays (silence, sine waves, noise bursts) directly into the processing pipeline.
4. **Clean Process Lifecycle (Observations 1.5 & 1.6)**:
   By establishing an isolated temporary workspace directory per test (`tmp_path`), configuring `cfg.disable_hotkeys = True`, `cfg.disable_model_preload = True`, and ensuring `ctrl.shutdown()` runs in fixture teardown, tests run in strict isolation with zero memory leaks, zero hanging threads, and zero registry pollution.
5. **20-Feature Coverage across 4 Tiers (Observation 1.7 & SCOPE.md)**:
   To satisfy `PROJECT.md` and `SCOPE.md`, tests must be partitioned into:
   - `tests/e2e/test_tier1_feature_coverage.py` (>=5 tests per feature for all 20 features = 100+ tests)
   - `tests/e2e/test_tier2_boundaries.py` (>=5 tests per feature for all 20 features = 100+ tests)
   - `tests/e2e/test_tier3_pairwise.py` (20+ pairwise integration tests)
   - `tests/e2e/test_tier4_scenarios.py` (10+ real-world user workflow tests)
6. **Unified Test Harness (`tests/e2e/conftest.py`)**:
   Placing shared fixtures inside `tests/e2e/conftest.py` ensures that all E2E test files automatically receive pre-configured headless Qt (`qapp_offscreen`), sandboxed configuration (`isolated_config`), sandboxed controller (`isolated_controller`), sandboxed database (`isolated_db`), and synthetic audio generators without duplicate boilerplate.

---

## 3. Caveats

1. **Hardware ASR vs Mock Inference**:
   Running real `faster-whisper` CTranslate2 model loading in every unit test is slow (5-15s per model load) and requires multi-hundred MB model weights in `data/models/`. For Tier 1-3 tests, ASR contracts should be exercised via mocked or pre-warmed audio arrays, while Tier 4 can test complete pipeline integration.
2. **Pre-existing Tests Coexistence**:
   The existing 55 test files under `tests/` must remain untouched to avoid regressions. New E2E tests are placed exclusively in `tests/e2e/` so they can be invoked either independently (`pytest tests/e2e/`) or as part of the full test suite (`pytest tests/`).
3. **No Code Modification During Exploration**:
   Per the Read-Only Investigation constraint, no files in `src/`, `tests/`, or the project root have been modified by this explorer agent. The complete content for `TEST_INFRA.md` and `tests/e2e/conftest.py` is provided below as the definitive implementation blueprint for the sub-orchestrator and writer agents.

---

## 4. Conclusion & Actionable Blueprints

### 4.1 Recommended Contents for `TEST_INFRA.md` (Project Root)

Below is the complete, exact recommended text to be written to `d:\Milind\Voice Assistant\TEST_INFRA.md`:

````markdown
# VOX (Wispr Flow Exact Replica) — End-to-End Test Infrastructure Specification

**Author**: E2E Testing Track Orchestration  
**Authority**: `ORIGINAL_REQUEST.md`, `PROJECT.md § Feature Inventory`, `SCOPE.md`  
**Target Environment**: Windows 11 x64, Python 3.13.14, PyQt6 6.11.0, pytest 9.2.0  

---

## 1. Executive Summary & Test Philosophy

This document defines the binding End-to-End (E2E) Test Infrastructure specification for Project VOX — the exact, high-performance running replica of Wispr Flow on Windows.

The test infrastructure enforces four fundamental invariants:
1. **Opaque-Box Verification**: Tests validate components exclusively through their public API contracts, Qt signals, database schemas, CLI entry points, and filesystem outputs. No tests couple to private internal variables or monkey-patch undocumented internals.
2. **Immutable Raw Transcript Preservation**: Regardless of the active transformation mode (`Verbatim`, `Clean`, `Polished`, `Structured`, `Email`), the raw acoustic transcript is immutably preserved in the SQLite WAL database and remains retroactively recoverable.
3. **Headless Execution & Zero Desktop Interference**: All tests run headlessly (`QT_QPA_PLATFORM=offscreen`) with global keyboard hooks disabled (`cfg.disable_hotkeys = True`) and sandboxed registry access. Tests never pop up desktop windows, steal window focus, or hijack user keystrokes.
4. **Local-First Privacy & Zero Cloud Egress**: All transcription, formatting, database persistence, and meeting intelligence execute strictly on-device without outbound cloud network requests.

---

## 2. 4-Tier Test Case Design Methodology

The E2E test suite is organized into a strict 4-Tier architecture:

### 2.1 Tier 1: Feature Coverage (Category-Partition)
- **Target File**: `tests/e2e/test_tier1_feature_coverage.py`
- **Volume**: >= 5 test cases per feature across all 20 features (>= 100 tests total).
- **Scope**: Happy-path functional verification of each feature in isolation using valid, representative inputs and standard operating parameters.

### 2.2 Tier 2: Boundary & Corner Cases (Boundary Value Analysis)
- **Target File**: `tests/e2e/test_tier2_boundaries.py`
- **Volume**: >= 5 test cases per feature across all 20 features (>= 100 tests total).
- **Scope**: Extremes, limits, empty inputs, maximum buffer sizes, Unicode/Devanagari text, network dropouts, file system locks, permission denials, and rapid state toggles.

### 2.3 Tier 3: Cross-Feature Combinations (Pairwise Interaction)
- **Target File**: `tests/e2e/test_tier3_pairwise.py`
- **Volume**: >= 20 combinatorial test cases.
- **Scope**: Pairwise interactions between features (e.g. Hotkey PTT + Mode Switch + Text Injection; Calendar 5-min alert + Meeting Studio capture + SQLite WAL write; Dictation retraction parsing + Snippet expansion + Output insertion).

### 2.4 Tier 4: Real-World Application Scenarios (End-to-End Workflows)
- **Target File**: `tests/e2e/test_tier4_scenarios.py`
- **Volume**: >= 10 full user workflow scenarios.
- **Scope**: Complete multi-step user journeys simulating realistic desktop productivity sessions from start to finish.

---

## 3. Feature Inventory Coverage Mapping Matrix

All 20 features defined in `PROJECT.md § Feature Inventory` are systematically covered across all 4 tiers:

| # | Feature Name | Primary Target Module | Tier 1 (>=5) | Tier 2 (>=5) | Tier 3 (Pairwise) | Tier 4 (Workflows) |
|---|--------------|----------------------|:------------:|:------------:|:-----------------:|:------------------:|
| 1 | Codebase Triage & Cleanup | Filesystem / `src/voxlocal/` | 5 | 5 | Clean tree + Config | Complete install hygiene |
| 2 | Robust Windows Startup Manager | `win32/startup.py` | 5 | 5 | Startup + Config sync | Login launch flow |
| 3 | Global Hotkey Interception | `win32/hotkeys.py` | 5 | 5 | Hotkey + State machine | Dictation shortcut journey |
| 4 | Atomic Win32 Text Injection | `win32/text_inserter.py` | 5 | 5 | Text inserter + Modes | Insertion into external target |
| 5 | Audio Capture Pipeline | `audio/dictation_recorder.py` | 5 | 5 | Audio capture + Transcriber | Live dictation audio flow |
| 6 | Offline Fast-Whisper Transcription | `engine/transcriber.py` | 5 | 5 | Transcriber + Hardware fallback | Full speech transcription flow |
| 7 | 9-State Dictation State Machine | `core/controller.py` | 5 | 5 | State machine + UI Pill | Complete PTT/POPO lifecycle |
| 8 | 5 Transformation Modes | `engine/dictation_processor.py` | 5 | 5 | Mode transform + Snippets | Multi-mode document authoring |
| 9 | Verbal Self-Correction Parser | `engine/dictation_processor.py` | 5 | 5 | Retraction + Text inserter | Live self-correction dictation |
| 10 | Floating Voice Pill (FlowBar) | `ui/floating_pill.py` | 5 | 5 | Pill UI + Audio Equalizer | Desktop overlay interaction |
| 11 | Flow Hub & 8 Workspaces | `ui/main_window.py` | 5 | 5 | Hub routes + Search | Workspace navigation session |
| 12 | Command Palette (`Ctrl+K`) | `ui/command_palette.py` | 5 | 5 | Palette + Route navigation | Keyboard-only power user flow |
| 13 | Notetaker Drawer & Scratchpad | `ui/notetaker_drawer.py` | 5 | 5 | Scratchpad + SQLite notes | Quick meeting thought capture |
| 14 | 3-Section Meeting Studio | `ui/meeting_studio.py` | 5 | 5 | Studio + MoM Generator | Full meeting recording & MoM |
| 15 | 3-Tier Outlook Calendar Engine | `calendar/calendar_manager.py`| 5 | 5 | Calendar + Notification popup | Pre-meeting priming & capture |
| 16 | Onboarding Wizard & System Tray | `ui/onboarding_wizard.py` | 5 | 5 | Wizard + Settings persist | First-run onboarding tour |
| 17 | Local SQLite WAL Storage | `storage/database.py` | 5 | 5 | DB WAL + Dual-column raw | Data integrity & recovery |
| 18 | Turnkey Test Suite (Tiers 1-4) | `tests/e2e/` harness | 5 | 5 | Test runner + Fixtures | Automated release test pass |
| 19 | Standalone Executable Distribution| `dist/VoxLocal/VoxLocal.exe`| 5 | 5 | Exe smoke test + Mutex | Production binary verification |
| 20 | Adversarial Coverage Hardening | All subsystems / Core | 5 | 5 | Stress + Resource limits | Endurance & crash recovery |
| **TOTAL** | **20 Features** | | **100+** | **100+** | **20+** | **10+** |

---

## 4. Test Suite Directory Organization

```
tests/
├── e2e/
│   ├── __init__.py
│   ├── conftest.py                       # Shared opaque-box fixtures
│   ├── test_tier1_feature_coverage.py    # 100+ tests (>=5 per feature)
│   ├── test_tier2_boundaries.py          # 100+ tests (>=5 per feature)
│   ├── test_tier3_pairwise.py            # 20+ cross-feature tests
│   └── test_tier4_scenarios.py           # 10+ end-to-end user workflows
├── pytest.ini                            # Pytest configuration
└── ... (existing 55 legacy test suites preserved)
```

---

## 5. Test Execution Commands & Pass/Fail Semantics

### 5.1 Test Runner Commands
```powershell
# Run the entire E2E test suite with verbose output:
.\.venv\Scripts\python.exe -m pytest tests/e2e/ -v

# Run individual test tiers:
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier1_feature_coverage.py -v
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier2_boundaries.py -v
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier3_pairwise.py -v
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier4_scenarios.py -v

# Run standalone executable smoke test:
$p = Start-Process -FilePath ".\dist\VoxLocal\VoxLocal.exe" -ArgumentList "--smoke-test-ms 1500" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Executable smoke test failed" }
```

### 5.2 Pass/Fail Semantics & Invariants
- **Zero Failures**: 100% of collected tests must pass (0 failures, 0 errors).
- **Execution Time Budget**:
  - Tier 1: <= 20 seconds total.
  - Tier 2: <= 25 seconds total.
  - Tier 3: <= 15 seconds total.
  - Tier 4: <= 30 seconds total.
  - Entire E2E suite <= 90 seconds.
- **Headless Invariant**: No desktop window, taskbar flash, or UI popup may appear during execution.

---

## 6. Release Certification & Quality Gates

The test track enforces a strict progression through four quality gates:
- **Gate 0 (M_E2E_0)**: Infrastructure setup (`TEST_INFRA.md`, `tests/e2e/conftest.py`, `pytest.ini`) verified.
- **Gate 1 (M_E2E_1)**: Tier 1 Feature Coverage (100+ tests) passes 100%.
- **Gate 2 (M_E2E_2)**: Tier 2 Boundary Cases (100+ tests) passes 100%.
- **Gate 3 (M_E2E_3)**: Tier 3 Pairwise Combinations (20+ tests) passes 100%.
- **Gate 4 (M_E2E_4)**: Tier 4 Scenarios (10+ tests) passes 100%.
- **Gate Final (M_E2E_FINAL)**: Full suite clean execution + Production executable verification + `TEST_READY.md` published.
````

---

### 4.2 Recommended Setup for `tests/e2e/conftest.py`

Below is the complete, exact recommended source code for `tests/e2e/conftest.py`:

```python
"""
Shared Opaque-Box E2E Test Fixtures for Project VOX.

Provides:
- Headless offscreen QApplication singleton (qapp_offscreen)
- Sandboxed filesystem and SQLite WAL database (isolated_tmp_dir, isolated_db)
- Safe AppConfig without OS hooks or Whisper pre-download (isolated_config)
- Fully wired AppController with deterministic shutdown (isolated_controller)
- Synthetic 16kHz mono float32 PCM audio generators (synthetic_audio)
- Deterministic Mock Calendar Provider (mock_calendar)
- Registered offscreen UI widget factory (offscreen_widgets)
- Win32 startup registry sandbox (isolated_env)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional
import numpy as np
import pytest

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Force headless offscreen Qt platform before any Qt modules load
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.engine.dictation_processor import DictationProcessor, TransformationMode
from voxlocal.ui.floating_pill import DualTierFloatingPill, PillState
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.scratchpad_window import VoxScratchpadWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.onboarding_wizard import VoxOnboardingWizard
from voxlocal.ui.command_palette import VoxCommandPaletteDialog
from voxlocal.ui.meeting_studio import VoxMeetingStudioWorkspace
from voxlocal.ui.calendar_prompt import VoxMeetingPromptPopup


@pytest.fixture(scope="session")
def qapp_offscreen() -> Generator[QApplication, None, None]:
    """Provide a session-scoped headless QApplication running on the offscreen QPA platform."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["vox_e2e_runner", "-platform", "offscreen"])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch) -> None:
    """Isolate OS-level side effects (registry, notifications, audio devices)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    # Mock StartupManager to prevent WinError 5 Access Denied in restricted environments
    monkeypatch.setattr(StartupManager, "is_enabled", staticmethod(lambda: False))
    monkeypatch.setattr(StartupManager, "set_enabled", staticmethod(lambda *a, **k: True))


@pytest.fixture
def isolated_tmp_dir(tmp_path: Path) -> Path:
    """Create a temporary sandbox directory with data, snapshots, and logs subfolders."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "snapshots").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def isolated_db(isolated_tmp_dir: Path) -> Generator[LocalDatabase, None, None]:
    """Provide a clean, isolated LocalDatabase in SQLite WAL mode."""
    db_file = isolated_tmp_dir / "voxlocal_test.db"
    db = LocalDatabase(db_file)
    yield db


@pytest.fixture
def isolated_config(isolated_tmp_dir: Path, monkeypatch) -> AppConfig:
    """Provide a safe AppConfig that disables global hooks, Whisper preload, and auto-calls."""
    cfg_path = isolated_tmp_dir / "config.json"
    monkeypatch.setenv("VOXLOCAL_CONFIG_PATH", str(cfg_path))
    
    cfg = AppConfig(
        storage_dir=str(isolated_tmp_dir),
        models_dir=str(isolated_tmp_dir / "models"),
        launch_at_login=False,
        llm_enabled=False,
        auto_record_calls=False,
        prompt_on_call=False,
        progressive_transcription=False,
        sound_effects=False,
    )
    # Critical test isolation flags
    cfg.disable_hotkeys = True
    cfg.disable_model_preload = True
    cfg.save()
    return cfg


@pytest.fixture
def isolated_controller(
    qapp_offscreen: QApplication,
    isolated_config: AppConfig,
    isolated_env: None,
) -> Generator[AppController, None, None]:
    """Provide an initialized AppController and guarantee clean shutdown on teardown."""
    ctrl = AppController(isolated_config)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        qapp_offscreen.processEvents()


@pytest.fixture
def synthetic_audio() -> Callable[..., np.ndarray]:
    """Factory fixture generating 16kHz mono float32 audio arrays."""
    def _generate(
        duration_sec: float = 1.0,
        freq_hz: float = 440.0,
        amplitude: float = 0.5,
        silence: bool = False,
    ) -> np.ndarray:
        sample_rate = 16000
        total_samples = int(sample_rate * duration_sec)
        if total_samples <= 0:
            return np.zeros(0, dtype=np.float32)
        if silence:
            return np.zeros(total_samples, dtype=np.float32)
        t = np.linspace(0, duration_sec, total_samples, False)
        wave = amplitude * np.sin(2 * np.pi * freq_hz * t)
        return np.clip(wave.astype(np.float32), -1.0, 1.0)

    return _generate


@pytest.fixture
def mock_calendar() -> MockCalendarProvider:
    """Provide a fresh, authenticated MockCalendarProvider instance."""
    provider = MockCalendarProvider()
    provider.authenticate()
    return provider


@pytest.fixture
def offscreen_widgets(
    qapp_offscreen: QApplication,
    isolated_controller: AppController,
) -> Generator[Dict[str, Any], None, None]:
    """
    Factory creating tracked offscreen UI widgets tied to isolated_controller.
    Automatically cleans up widgets on teardown.
    """
    created_widgets: List[QWidget] = []

    def _create_pill() -> DualTierFloatingPill:
        pill = DualTierFloatingPill(parent=None, controller=isolated_controller)
        created_widgets.append(pill)
        return pill

    def _create_main_window(floating_pill: Optional[DualTierFloatingPill] = None) -> MainWindow:
        pill = floating_pill or _create_pill()
        win = MainWindow(controller=isolated_controller, floating_widget=pill)
        created_widgets.append(win)
        return win

    def _create_scratchpad() -> VoxScratchpadWindow:
        scratch = VoxScratchpadWindow(controller=isolated_controller)
        created_widgets.append(scratch)
        return scratch

    def _create_drawer() -> VoxNotetakerDrawer:
        drawer = VoxNotetakerDrawer(controller=isolated_controller)
        created_widgets.append(drawer)
        return drawer

    def _create_wizard() -> VoxOnboardingWizard:
        wizard = VoxOnboardingWizard(controller=isolated_controller)
        created_widgets.append(wizard)
        return wizard

    def _create_palette() -> VoxCommandPaletteDialog:
        palette = VoxCommandPaletteDialog(controller=isolated_controller)
        created_widgets.append(palette)
        return palette

    def _create_meeting_studio() -> VoxMeetingStudioWorkspace:
        studio = VoxMeetingStudioWorkspace(db=isolated_controller.db)
        created_widgets.append(studio)
        return studio

    factory = {
        "pill": _create_pill,
        "main_window": _create_main_window,
        "scratchpad": _create_scratchpad,
        "drawer": _create_drawer,
        "wizard": _create_wizard,
        "palette": _create_palette,
        "meeting_studio": _create_meeting_studio,
    }

    yield factory

    for w in created_widgets:
        try:
            w.close()
            w.deleteLater()
        except Exception:
            pass
    qapp_offscreen.processEvents()
```

---

### 4.3 Recommended `pytest.ini` (Project Root)

Below is the recommended `pytest.ini` to guarantee that test runs remain anchored strictly inside the project workspace:

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

---

## 5. Verification Method

To independently verify all findings and test infrastructure components:

1. **Verify Python & Pytest Toolchain**:
   ```powershell
   .\.venv\Scripts\python.exe -V
   .\.venv\Scripts\python.exe -m pytest --version
   ```
   *Expected*: Python 3.13.14, pytest 9.2.0.

2. **Verify Headless Offscreen Qt Platform**:
   ```powershell
   .\.venv\Scripts\python.exe -c "import os; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; from PyQt6.QtWidgets import QApplication, QWidget; app = QApplication([]); w = QWidget(); w.show(); print('QPA:', app.platformName(), 'Visible:', w.isVisible())"
   ```
   *Expected*: `QPA: offscreen Visible: True`.

3. **Verify Production Executable Smoke Test**:
   ```powershell
   $p = Start-Process -FilePath ".\dist\VoxLocal\VoxLocal.exe" -ArgumentList "--smoke-test-ms 1500" -Wait -PassThru; $p.ExitCode
   ```
   *Expected*: `0`.

4. **Verify Existing Passing Test Suites**:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_e2e_dictation_and_modes.py -v
   ```
   *Expected*: 17 passed in < 1 second.

5. **Invalidation Conditions**:
   - If `QT_QPA_PLATFORM=offscreen` fails to initialize `QApplication`, or displays GUI artifacts on the physical desktop.
   - If `AppController(cfg)` hangs or fails to shut down with `ctrl.shutdown()`.
   - If `dist/VoxLocal/VoxLocal.exe --smoke-test-ms 1500` exits with non-zero code.

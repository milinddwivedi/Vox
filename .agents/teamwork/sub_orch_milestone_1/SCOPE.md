# Scope: Milestone 1 - Codebase Triage, Startup Resilience & Win32 Integration

## Architecture
Milestone 1 establishes the clean base repository and verifies OS-level integration for Project VOX on Windows:
1. **Repository Triage**: Cleans stray debug media files (`temp_*.mp4`, `test_remux*.mp4`, `test_rate*.mp4`, `temp_target.txt`) and obsolete scratch scripts from the root directory while preserving all required core components under `src/voxlocal/`, `tests/`, and project documentation.
2. **Startup Manager Resilience (`src/voxlocal/win32/startup.py`)**:
   - Manages Windows Run registry key (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
   - Handles restricted, un-elevated, or sandboxed environments gracefully without raising unhandled exceptions (`PermissionError: [WinError 5] Access is denied`).
   - Added `is_available()`, least-privilege `KEY_QUERY_VALUE`, single-warning latching (`_warned_access_denied`), and graceful fallback.
3. **Win32 Hotkeys & Text Inserter Alignment**:
   - `src/voxlocal/win32/hotkeys.py`: `WH_KEYBOARD_LL` low-level keyboard hook functions without admin elevation; supports PTT, POPO, dismiss, and mode switching.
   - `src/voxlocal/win32/text_inserter.py`: 3-tier text inserter (SendInput Unicode, UIPI WM_CHAR fallback, and atomic clipboard paste with full restoration).
   - `src/voxlocal/win32/clipboard.py` and `window_focus.py`: Validated clipboard snapshot/restore and foreground window focus handling.
4. **Test Verification**:
   - `tests/test_unit_suite.py` executes cleanly and passes with 0 failures (6/6).

## Feature Inventory Mapping
| # | Feature | Description | Scope Target | Status |
|---|---------|-------------|--------------|--------|
| 1 | Codebase Triage & Cleanup | Prune legacy root media files (`temp_*.mp4`, etc.) and obsolete scratch scripts; preserve core codebase | Root cleanup | DONE |
| 2 | Startup Registry Resilience | Resilient registry management for automatic Windows startup with non-elevated sandbox fallback | `src/voxlocal/win32/startup.py` | DONE |
| 3 | Global Hotkey Interception | `WH_KEYBOARD_LL` hook for PTT, POPO, and mode shortcuts without admin elevation | `src/voxlocal/win32/hotkeys.py` | DONE |
| 4 | Atomic Win32 Text Injection | 3-tier text inserter (SendInput Unicode, UIPI fallback, safe clipboard paste) | `src/voxlocal/win32/text_inserter.py` | DONE |
| 5 | Unit Test Verification | `tests/test_unit_suite.py` passes 100% (0 failures) | `tests/test_unit_suite.py` | DONE |

## Work Breakdown & Acceptance Criteria
- [x] Stray debug files (`temp_*.mp4`, `test_remux*.mp4`, `test_rate*.mp4`, `temp_target.txt`) removed from root (0 `*.mp4` files remain).
- [x] `src/voxlocal/win32/startup.py` handles `PermissionError` / `WinError 5` / sandbox environments gracefully without throwing fatal errors.
- [x] `src/voxlocal/win32/hotkeys.py` and `src/voxlocal/win32/text_inserter.py` conform to interface contracts and Wispr Flow behavior.
- [x] `tests/test_unit_suite.py` runs with exit code 0 and 0 failures (6 passed in 4.30s).
- [x] Forensic integrity audit passes with CLEAN status.

## Write Ownership Boundaries
- Worker exclusively owns:
  - Deletions of stray root debug media/scratch files identified in triage.
  - Modifications to `src/voxlocal/win32/startup.py`.
  - Adjustments to `src/voxlocal/core/controller.py` lines 148-156.
  - Adjustments to `tests/test_unit_suite.py`.
- Reviewers, Challengers, and Auditor are read-only.

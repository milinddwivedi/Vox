# Task Assignment: Milestone 5 Remediation Worker

## Objective
Apply the exact remediations identified by Reviewer 1 in `.agents/teamwork/reviewer_m5_1/review.md` to resolve the 4 failing tests in `tests/test_meeting_lifecycle_simulation.py` and achieve 61/61 passing tests with zero regressions.

## Inputs & Context
1. `ORIGINAL_REQUEST.md`: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
2. `PROJECT.md`: `d:\Milind\Voice Assistant\PROJECT.md`
3. Reviewer 1 Report: `d:\Milind\Voice Assistant\.agents\teamwork\reviewer_m5_1\review.md`
4. Target Test Suite: `d:\Milind\Voice Assistant\tests\test_meeting_lifecycle_simulation.py`

## Specific Defect Remediations Required:
1. **Defect 1 (`src/voxlocal/audio/call_detector.py`)**:
   In `clean_meeting_title`: The regex stripping trailing bracket annotations (`[Call]`, `(Meeting)`) is evaluated *before* stripping the platform suffix (`| Microsoft Teams`). Suffix removal must happen *before* trailing bracket annotation stripping, or apply the trailing bracket stripping again after platform suffix removal, so titles like `Project Sync [Call] | Microsoft Teams` have `[Call]` stripped.
   Target test: `test_03_special_characters_and_emojis_in_title`

2. **Defect 2 (`src/voxlocal/core/controller.py`)**:
   In `stop_meeting_recording`: Wrap `self.meeting_recorder.stop(preserve_separate_tracks=self.config.preserve_separate_tracks)` in a `try...except Exception as exc:` block. Log a warning on exception, set `result = None`, and continue cleanly with the state cleanup rather than letting an audio hardware crash escape.
   Target test: `test_02_audio_recorder_stop_returns_false`

3. **Defect 3 (`src/voxlocal/audio/meeting_recorder.py`)**:
   Add an `@is_recording.setter` to `MeetingRecorder`:
   ```python
   @is_recording.setter
   def is_recording(self, val: bool) -> None:
       with self._lock:
           self._state = "recording" if val else "idle"
   ```
   Target test: `test_04_orphan_audio_recorder_auto_healing`

4. **Defect 4 (`tests/test_meeting_lifecycle_simulation.py`)**:
   Verify that `test_04_ui_signal_emission_from_dead_worker` has `qapp_offscreen.processEvents()` before `assert delivered == [888]` so queued cross-thread signals are delivered.

5. **Verification**:
   Execute the test suite using `.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v`.
   Ensure all 61 tests pass. Also run the other 5 meeting test suites to verify 0 regressions:
   - `tests/test_challenger_stress_m3_meeting.py`
   - `tests/test_meeting_detection_and_intelligence.py`
   - `tests/test_meeting_recording_robustness.py`
   - `tests/test_screen_sharing_comprehensive_matrix.py`
   - `tests/test_cross_feature_coexistence.py`

## State Folder & Reporting
State folder: `d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_remediation\`
Write `handoff.md` with:
- Observation (what was fixed)
- Logic Chain
- Exact test commands and stdout/stderr results
- Layout compliance
- Conclusion


## 2026-11-12T19:18:34Z
You are teamwork_worker for Milestone 5 Remediation.
Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_remediation\

Read your dispatch assignment in:
d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_remediation\DISPATCH.md

MANDATORY CONTEXT:
Read ORIGINAL_REQUEST.md before starting work:
d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

Read PROJECT.md for architecture and contracts:
d:\Milind\Voice Assistant\PROJECT.md

Read Reviewer 1 report for exact details on the 4 defects:
d:\Milind\Voice Assistant\.agents\teamwork\reviewer_m5_1\review.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your exclusive write ownership:
- src/voxlocal/audio/call_detector.py
- src/voxlocal/core/controller.py
- src/voxlocal/audio/meeting_recorder.py
- tests/test_meeting_lifecycle_simulation.py

Tasks:
1. Fix clean_meeting_title in call_detector.py to strip platform suffixes before trailing bracket annotations so titles like "Project Sync [Call] | Microsoft Teams" have [Call] removed.
2. In controller.py:stop_meeting_recording, wrap self.meeting_recorder.stop(...) in a try...except Exception block, logging a warning and setting result = None on error.
3. In meeting_recorder.py, add @is_recording.setter.
4. Verify tests/test_meeting_lifecycle_simulation.py lines 846-854 pump events with qapp_offscreen.processEvents().
5. Run tests:
   .venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v
   Ensure all 61 tests pass. Also run existing meeting suites to verify 0 regressions.
6. Write your comprehensive handoff report to d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_remediation\handoff.md.
7. Send message to parent with completion status and report path.

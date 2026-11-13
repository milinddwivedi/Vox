# Task Assignment: Worker Milestone 7

## Context
You are assigned to verify Milestone 7: Live Notetaker 2+1 Context-Aware Tabs & Compact Floating Bar Streamlining.

## Key Files to Read
- ORIGINAL_REQUEST.md: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- PROJECT.md: `d:\Milind\Voice Assistant\PROJECT.md`
- TEST_INFRA.md: `d:\Milind\Voice Assistant\TEST_INFRA.md`
- Implementation: `src/voxlocal/ui/notetaker_drawer.py`
- Test suite: `tests/test_notetaker_context_aware_e2e.py`


## 2026-11-12T21:14:48Z
You are worker_m7, a teamwork_worker subagent.

Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\worker_m7\

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Key Documentation:
1. ORIGINAL_REQUEST.md: Read `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` before starting work.
2. PROJECT.md: Read `d:\Milind\Voice Assistant\PROJECT.md` (Focus on Milestone 7 and Interface Contract 1).
3. TEST_INFRA.md: Read `d:\Milind\Voice Assistant\TEST_INFRA.md`.

Your Mission:
Verify Milestone 7 (Live Notetaker 2+1 Context-Aware Tabs & Compact Floating Bar Streamlining):
1. Read and inspect `src/voxlocal/ui/notetaker_drawer.py` and `tests/test_notetaker_context_aware_e2e.py`.
2. Verify:
   - 2+1 dynamic tab model ("My thoughts", "Transcript", dynamic "+ Summary" visible only after call/processing).
   - Hidden "Tasks" tab during active calls, revealed dynamically post-finalization.
   - Integrated bottom "Ask AI" drawer with "Ask anything..." input and "[ What did I miss? ]" prompt chip.
   - Screen edge snapping (`height = workArea.height`, snapped flush to display edge).
   - Compact floating capsule controls with 4-bar mini waveform, recording pulse, timer, and hover tray.
3. Run the automated test suite using `.venv\Scripts\python.exe -m pytest tests/test_notetaker_context_aware_e2e.py -v` (or python -m pytest if venv path differs).
4. Verify all 50/50 tests pass cleanly. If any tests fail or any logic is missing, fix it cleanly in `src/voxlocal/ui/notetaker_drawer.py` (you own this file exclusively).
5. Document the test commands and full execution results in your report.
6. Write a comprehensive, self-contained handoff report to `d:\Milind\Voice Assistant\.agents\teamwork\worker_m7\handoff.md` and send a completion message with your verdict.

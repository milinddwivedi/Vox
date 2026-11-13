# Task Assignment: Milestone 6 FlowBar Empirical Challenger

## Objective
Empirically challenge the FlowBar 8-state machine and spring geometry animations under adversarial conditions:
1. Rapid consecutive state transitions (e.g. 50 rapid morphs in under 1 second without crash, memory leak, or invalid dimensions).
2. Win32 focus invariance: Assert zero focus theft during state morphs.
3. Multi-meeting stack badge clamping and display when active meeting count varies from 0 to 50.
4. Auto-start fill underlay progress bounds (0.0 to 1.0) and caret menu action triggering.
5. Geometry bounds and offscreen clamping.

## Inputs & Context
1. `ORIGINAL_REQUEST.md`: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
2. `PROJECT.md`: `d:\Milind\Voice Assistant\PROJECT.md`
3. Target test suite: `tests/test_toggle_bar_states_e2e.py`

## Verification Instructions
1. Run test suite:
   `.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v`
2. Construct and run empirical stress tests against `VoxFlowBar`.
3. Determine verdict: **APPROVE** or **REQUEST_CHANGES**.
4. Write report to `d:\Milind\Voice Assistant\.agents\teamwork\challenger_m6_flowbar\handoff.md`.
5. Send completion message to parent.


## 2026-11-12T20:16:53Z
You are challenger_m6_flowbar (teamwork_challenger).
Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\challenger_m6_flowbar\

Read your dispatch assignment:
d:\Milind\Voice Assistant\.agents\teamwork\challenger_m6_flowbar\DISPATCH.md

MANDATORY CONTEXT:
Read ORIGINAL_REQUEST.md before starting:
d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

Read PROJECT.md for architecture and contracts:
d:\Milind\Voice Assistant\PROJECT.md

Empirically challenge the FlowBar 8-state machine and spring geometry animations under rapid transitions, focus preservation, stack badge stress, and offscreen bounds.
Run tests:
.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v

Determine your verdict: APPROVE or REQUEST_CHANGES.
Write report to handoff.md and send completion message to parent.

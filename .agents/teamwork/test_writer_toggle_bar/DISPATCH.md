# Dispatch: Test Writer 2 — Toggle Bar 8-State Polymorphism & Morphing

Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\test_writer_toggle_bar
Exclusive target file ownership: `tests/test_toggle_bar_states_e2e.py` (DO NOT touch any file in `src/` or other test files)
Original Request: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
Project Architecture: d:\Milind\Voice Assistant\PROJECT.md
Scope: d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\SCOPE.md
Test Infra: d:\Milind\Voice Assistant\TEST_INFRA.md
Blueprint & Specifications: d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md §3.2

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Mission:
Author the complete, opaque-box E2E test suite in `tests/test_toggle_bar_states_e2e.py` covering Features 6 through 10 across Tiers 1–4 per the blueprint in `explorer_e2e_target_suites\handoff.md`:
- Tier 1: Feature Coverage (>=5 tests per feature, 25 tests total across Features 6-10: Resting, Mini, Card, Active, L4 preview states, cubic spring easing 420ms, notification popup consolidation, platform badges & emerald dot, split join button & auto-start progress)
- Tier 2: Boundary & Corner Cases (>=5 tests per feature, 25 tests total: rapid state morphs <50ms, extreme titles 300+ chars, auto-start 0s delay/clamping, stack badge counts 1..10+, window focus non-stealing)
- Tier 3: Cross-Feature Combinations (5 pairwise tests: morph + live equalizer, calendar alert hover, split join start, dock edge changes, fan stack view)
- Tier 4: Real-World Workload Scenarios (5 realistic workflows)
Total: ~60 tests.

Requirements:
1. Put `pytest_plugins = ["tests.e2e.conftest"]` at the top of `tests/test_toggle_bar_states_e2e.py`.
2. Use headless Qt fixtures (`qapp_offscreen`, `isolated_controller`, `offscreen_widgets`, `isolated_db`, `isolated_config`) so tests run completely offscreen with zero desktop interference.
3. Test against public APIs, contracts, signals, and widget geometries.
4. Execute and verify your tests using:
   `.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v`
   Ensure 100% of tests pass with exit code 0.
5. Document results in `handoff.md` in your state folder and notify the orchestrator.

## 2026-11-12T18:44:04Z
You are Test Writer 2 for VOX E2E Testing Track.
Working directory: `d:\Milind\Voice Assistant`
State folder: `d:\Milind\Voice Assistant\.agents\teamwork\test_writer_toggle_bar`
Exclusive write target: `tests/test_toggle_bar_states_e2e.py` (DO NOT touch any file in `src/` or other test files)

Read your dispatch instructions and blueprint:
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` (Read first)
- `d:\Milind\Voice Assistant\.agents\teamwork\test_writer_toggle_bar\DISPATCH.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md` §3.2
- `d:\Milind\Voice Assistant\TEST_INFRA.md`
- `d:\Milind\Voice Assistant\PROJECT.md`

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Mission:
Author the complete, opaque-box E2E test suite in `tests/test_toggle_bar_states_e2e.py` covering Features 6-10 across Tiers 1-4 per the blueprint in `explorer_e2e_target_suites\handoff.md` §3.2:
- Tier 1: Feature Coverage (25 tests total across Features 6-10: 8-state machine dimensions, 420ms cubic spring easing, notification popup consolidation, platform badges & emerald dot, split join button & auto-start progress)
- Tier 2: Boundary & Corner Cases (25 tests total: rapid state morphs <50ms, extreme titles 300+ chars, auto-start 0s delay/clamping, stack badge counts 1..10+, window focus non-stealing)
- Tier 3: Pairwise Combinations (5 tests)
- Tier 4: Real-World Workload Scenarios (5 tests)
Total: ~60 tests.

Include `pytest_plugins = ["tests.e2e.conftest"]` at the top.
Use offscreen Qt fixtures (`qapp_offscreen`, `isolated_controller`, `offscreen_widgets`, `isolated_db`, `isolated_config`).
Run and verify all your tests:
`.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v`
Ensure 100% pass with exit code 0.
Write `handoff.md` in your state folder and send a completion message to the orchestrator.


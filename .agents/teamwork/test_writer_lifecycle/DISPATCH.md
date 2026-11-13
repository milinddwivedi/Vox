# Dispatch: Test Writer 1 — Meeting Lifecycle Simulation

Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\test_writer_lifecycle
Exclusive target file ownership: `tests/test_meeting_lifecycle_simulation.py` (DO NOT touch any file in `src/` or other test files)
Original Request: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
Project Architecture: d:\Milind\Voice Assistant\PROJECT.md
Scope: d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\SCOPE.md
Test Infra: d:\Milind\Voice Assistant\TEST_INFRA.md
Blueprint & Specifications: d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md §3.1

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Mission:
Author the complete, opaque-box E2E test suite in `tests/test_meeting_lifecycle_simulation.py` covering Features 1 through 5 across Tiers 1–4 per the blueprint in `explorer_e2e_target_suites\handoff.md`:
- Tier 1: Feature Coverage (>=5 tests per feature, 25 tests total across Features 1-5)
- Tier 2: Boundary & Corner Cases (>=5 tests per feature, 25 tests total)
- Tier 3: Cross-Feature Combinations (6 pairwise tests)
- Tier 4: Real-World Workload Scenarios (5 realistic workflows including Scenarios 1-5 from SCOPE.md)
Total: ~61 tests.

Requirements:
1. Put `pytest_plugins = ["tests.e2e.conftest"]` at the top of `tests/test_meeting_lifecycle_simulation.py`.
2. Use headless Qt fixtures (`qapp_offscreen`, `isolated_controller`, `offscreen_widgets`, `isolated_db`, `isolated_config`, `synthetic_audio`) so tests run completely offscreen with zero desktop interference.
3. Test against public APIs, contracts, and signals. Use duck-typing where appropriate to support clean execution.
4. Execute and verify your tests using:
   `.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v`
   Ensure 100% of tests pass with exit code 0.
5. Document results in `handoff.md` in your state folder and notify the orchestrator.


## 2026-11-12T18:43:57Z
You are Test Writer 1 for VOX E2E Testing Track.
Working directory: `d:\Milind\Voice Assistant`
State folder: `d:\Milind\Voice Assistant\.agents\teamwork\test_writer_lifecycle`
Exclusive write target: `tests/test_meeting_lifecycle_simulation.py` (DO NOT touch any file in `src/` or other test files)

Read your dispatch instructions and blueprint:
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` (Read first)
- `d:\Milind\Voice Assistant\.agents\teamwork\test_writer_lifecycle\DISPATCH.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md` §3.1
- `d:\Milind\Voice Assistant\TEST_INFRA.md`
- `d:\Milind\Voice Assistant\PROJECT.md`

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Mission:
Author the complete, opaque-box E2E test suite in `tests/test_meeting_lifecycle_simulation.py` covering Features 1-5 across Tiers 1-4 per the blueprint in `explorer_e2e_target_suites\handoff.md` §3.1:
- Tier 1: Feature Coverage (25 tests total across Features 1-5: decoupled capture state, platform window transitions, salted session IDs, clean audio teardown, guarded UI signals)
- Tier 2: Boundary & Corner Cases (25 tests total: rapid transitions <100ms, corrupt titles, slug/suppression limits, audio failures, signal concurrency)
- Tier 3: Pairwise Combinations (6 tests)
- Tier 4: Real-World Workload Scenarios (5 tests: back-to-back same-platform Teams, manual stop/restart, overlapping stack, dismissed re-prompt, finalization failure resilience)
Total: ~61 tests.

Include `pytest_plugins = ["tests.e2e.conftest"]` at the top.
Use offscreen Qt fixtures (`qapp_offscreen`, `isolated_controller`, `offscreen_widgets`, `isolated_db`, `isolated_config`, `synthetic_audio`).
Run and verify all your tests:
`.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v`
Ensure 100% pass with exit code 0.
Write `handoff.md` in your state folder and send a completion message to the orchestrator.

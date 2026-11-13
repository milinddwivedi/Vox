# Explorer Dispatch: E2E Target Test Suites & Entry Point Mapping

Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites
Original Request: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
Project: d:\Milind\Voice Assistant\PROJECT.md
Scope: d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\SCOPE.md
Test Infra: d:\Milind\Voice Assistant\TEST_INFRA.md

Mission:
Survey the codebase and test architecture to map out the exact implementation requirements for the 4 target E2E test suites in `tests/`:
1. `tests/test_meeting_lifecycle_simulation.py` (Features 1-5, Tiers 1-4)
2. `tests/test_toggle_bar_states_e2e.py` (Features 6-10, Tiers 1-4)
3. `tests/test_notetaker_context_aware_e2e.py` (Features 11-14, Tiers 1-4)
4. `tests/test_ux_accessibility_regressions.py` (Features 15-18, Tiers 1-4)

Investigate and document in `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md`:
1. Public APIs, classes, and signals in `src/voxlocal/core/controller.py`, `src/voxlocal/audio/call_detector.py`, `src/voxlocal/ui/floating_pill.py`, `src/voxlocal/ui/notetaker_drawer.py`, `src/voxlocal/ui/theme.py`, etc.
2. Existing test infrastructure in `tests/` and `tests/e2e/conftest.py`, pytest configuration (`pytest.ini`), and how headless Qt (`offscreen`) is initialized.
3. Test suite organization and blueprint for each of the 4 target test files to ensure >=5 Tier 1, >=5 Tier 2, pairwise Tier 3, and realistic Tier 4 tests per feature group, ensuring 100% clean pass with zero source modifications.
4. Verify by running `.venv\Scripts\python.exe -m pytest tests/` or `--collect-only` to confirm test discovery behavior.


## 2026-11-12T18:31:07Z
Sender: dd74b12a-525a-486c-8be5-bea0eecce0d8
Priority: MESSAGE_PRIORITY_HIGH
Content:
You are the E2E Test Architecture Explorer for VOX.
Your working directory is: `d:\Milind\Voice Assistant`
Your state folder is: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\`

Read these key documents first:
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` (Authoritative user requirements)
- `d:\Milind\Voice Assistant\PROJECT.md` (Global architecture & Feature Inventory)
- `d:\Milind\Voice Assistant\TEST_INFRA.md` (Test philosophy & methodology)
- `d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\SCOPE.md` (E2E Track scope)
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\DISPATCH.md`

Your objective:
Survey the codebase and test architecture to deliver a concrete, comprehensive blueprint for implementing the 4 required test suites:
1. `tests/test_meeting_lifecycle_simulation.py` (Features 1-5: decoupled state machine, window transition engine, salted session ID, clean audio teardown, guarded UI signal routing; Tiers 1-4)
2. `tests/test_toggle_bar_states_e2e.py` (Features 6-10: 8-state machine, spring geometry animations, notification popup consolidation, mini-toggle styling/badges, split join & auto-start; Tiers 1-4)
3. `tests/test_notetaker_context_aware_e2e.py` (Features 11-14: dynamic 2+1 tabs, integrated bottom drawer, screen edge snapping, compact floating bar; Tiers 1-4)
4. `tests/test_ux_accessibility_regressions.py` (Features 15-18: semantic tokens, polished empty states, settings & live mic meter, screen reader & keyboard accessibility; Tiers 1-4)

Specifically:
- Check current implementations in `src/voxlocal/core/controller.py`, `src/voxlocal/audio/call_detector.py`, `src/voxlocal/ui/floating_pill.py`, `src/voxlocal/ui/notetaker_drawer.py`, `src/voxlocal/ui/theme.py`, `src/voxlocal/ui/settings_dialog.py`, etc., to verify actual class names, method signatures, signals, and properties.
- Check `tests/e2e/conftest.py` and `tests/conftest.py` (if any), `pytest.ini`, and verify how tests in `tests/` can cleanly inherit or share headless Qt fixtures.
- Test runner invocation: verify `.venv\Scripts\python.exe -m pytest --collect-only` or check current test suite health.
- Formulate a precise test case structure (class names, test names, setup patterns, assertion strategies) for each file covering Tiers 1-4 cleanly so test writers can implement them directly without breaking anything or modifying `src/`.

Write your full findings and blueprint to `d:\Milind\Voice Assistant\.agents\teamwork\explorer_e2e_target_suites\handoff.md`, update your `progress.md`, and send a completion message back to me.

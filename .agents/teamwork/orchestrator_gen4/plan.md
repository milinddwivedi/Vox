# Plan: Milestone 8 & Milestone 9 Execution

## Objectives
1. Resolve all 7 failing tests in `tests/test_ux_accessibility_regressions.py` to achieve 50/50 passing tests for Milestone 8 (App-Wide UX Polish & Accessibility).
2. Execute Milestone 9: Full comprehensive verification across all 4 test suites (221 tests total) with 100% pass rate.
3. Perform rigorous multi-agent validation (Reviewers, Challengers, Forensic Auditor) and produce final completion report.

## Phase 1: Milestone 8 Execution (App-Wide UX Polish & Accessibility)
1. **Explore**: Dispatch `teamwork_explorer` to examine the 7 test failures in `tests/test_ux_accessibility_regressions.py` and their target source files:
   - `test_05_snippets_empty_state_quick_start`
   - `test_02_live_mic_meter_updates_deflection_on_level_changed`
   - `test_03_mic_meter_inactive_when_settings_closed`
   - `test_03_enter_and_space_trigger_button_click`
   - `test_02_mic_selection_updates_meter_and_saves_to_config`
   - `test_03_keyboard_nav_through_empty_state_to_starter_prompt`
   - `test_scenario_2_audio_settings_mic_selection_and_live_meter_test`
   Target files: `src/voxlocal/ui/settings_dialog.py`, `src/voxlocal/ui/components.py`, `src/voxlocal/ui/hub_pages.py`.
2. **Implement**: Dispatch `teamwork_worker` to patch the source code, run `pytest tests/test_ux_accessibility_regressions.py -v`, and verify 50/50 tests passing.
3. **Review**: Dispatch 2 `teamwork_reviewer` subagents to review UX polish, accessibility compliance, and code quality.
4. **Challenge**: Dispatch 2 `teamwork_challenger` subagents to stress-test accessibility, keyboard focus, and mic deflection meters.
5. **Audit**: Dispatch `teamwork_auditor` to verify code integrity and confirm no hardcoded facades or cheats.
6. **Gate**: Evaluate Gate criteria in `GATE_STATUS.md`.

## Phase 2: Milestone 9 Full Verification & Victory
1. Dispatch verification worker / test runner to execute the full comprehensive regression suite:
   - `pytest tests/test_meeting_lifecycle_simulation.py -v` (61 tests)
   - `pytest tests/test_toggle_bar_states_e2e.py -v` (60 tests)
   - `pytest tests/test_notetaker_context_aware_e2e.py -v` (50 tests)
   - `pytest tests/test_ux_accessibility_regressions.py -v` (50 tests)
2. Total tests: 221 tests across 4 suites. Target: 221 passed, 0 failed.
3. Prepare final completion report for user and parent.

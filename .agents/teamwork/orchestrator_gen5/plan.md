# Execution Plan: Milestone 8 & Milestone 9 Victory Run

## Objective
Finalize Milestone 8 surgical accessibility & UX fixes (achieve 50/50 passing in `test_ux_accessibility_regressions.py`), execute Milestone 9 full multi-tier regression suite (221/221 tests passing), and complete final forensic audit for project completion.

## Step 1: Milestone 8 Surgical Implementation
- **Target**: Fix 7 failing tests in `tests/test_ux_accessibility_regressions.py`:
  1. `VoxAudioVisualizerBar.set_level` in `src/voxlocal/ui/components.py`:
     - Provide immediate level setting or disable decay during direct deflection signals.
     - Ensure meter resets / stops when settings dialog is closed.
  2. Snippets empty state in `src/voxlocal/ui/hub_pages.py`:
     - Ensure starter prompt focus / quick-start click behavior inserts template snippet.
  3. Enter/Space keyboard trigger handling on accessible buttons:
     - Ensure keyboard navigation and activation triggers expected actions.
- **Worker**: `teamwork_worker` (worker_m8_surgical).
- **Verification**: Run `pytest tests/test_ux_accessibility_regressions.py -v` (must be 50/50 passed).

## Step 2: Milestone 8 Gate Verification
- Reviewer: `teamwork_reviewer` to review code diff and test output.
- Auditor: `teamwork_auditor` to verify genuine implementation and clean audit.

## Step 3: Milestone 9 Multi-Tier Comprehensive Regression Run
- Execute all 4 major suites:
  1. `pytest tests/test_meeting_lifecycle_simulation.py -v` (61 tests)
  2. `pytest tests/test_toggle_bar_states_e2e.py -v` (60 tests)
  3. `pytest tests/test_notetaker_context_aware_e2e.py -v` (50 tests)
  4. `pytest tests/test_ux_accessibility_regressions.py -v` (50 tests)
- Total tests: 221. All must pass 100%.

## Step 4: Final Victory Audit & Synthesis
- Forensic auditor checks entire codebase for integrity, no mock bypasses, genuine logic.
- Synthesize findings, update PROJECT.md status, and report final victory to user/parent.

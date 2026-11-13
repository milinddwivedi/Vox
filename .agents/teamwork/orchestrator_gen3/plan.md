# Orchestration Plan: Generation 3 (Milestone 7, 8 & Final Verification)

## Objective
Resume execution from Milestone 7, verify and sign off Milestone 7, remediate and sign off Milestone 8 (7 failing tests -> 50/50), and execute Milestone 9 full regression verification across all test suites to satisfy all user requirements R1-R4.

---

## Phase 1: Milestone 7 Verification & Gate Sign-off
1. **Worker Dispatch**:
   - Agent: `worker_m7`
   - Scope: Inspect `src/voxlocal/ui/notetaker_drawer.py`, run `tests/test_notetaker_context_aware_e2e.py` (verify 50/50 passing), confirm 2+1 tabs, hidden Tasks during active calls, bottom drawer with "Ask anything...", and compact floating capsule controls.
2. **Reviewers Dispatch**:
   - Agents: `reviewer_m7_1`, `reviewer_m7_2`
   - Scope: Verify interface contracts, visual parity against Wispr Flow specifications, and test suite rigor.
3. **Challengers Dispatch**:
   - Agents: `challenger_m7_1`, `challenger_m7_2`
   - Scope: Adversarial verification of edge cases (dynamic tab changes, drawer show/hide, screen resizing).
4. **Forensic Auditor Dispatch**:
   - Agent: `auditor_m7`
   - Scope: Check for dummy facades, test hardcoding, and verify genuine functionality.
5. **Gate Evaluation**:
   - Document verdicts in `GATE_STATUS.md`. If all pass, mark Milestone 7 as DONE.

---

## Phase 2: Milestone 8 Remediation & Gate Sign-off
1. **Worker Dispatch**:
   - Agent: `worker_m8`
   - Scope: Address the 7 failing tests in `tests/test_ux_accessibility_regressions.py` (empty states quick start, live mic deflection meter, enter/space keyboard navigation). Ensure all 50 tests pass cleanly.
2. **Reviewers & Auditor Dispatch**:
   - Reviewers examine code changes in `settings_dialog.py`, `theme.py`, etc.
   - Forensic Auditor confirms clean implementation with no regressions.
3. **Gate Evaluation**:
   - Record in `GATE_STATUS.md`. If passing, mark Milestone 8 as DONE.

---

## Phase 3: Milestone 9 Full Regression & Final Verification
1. **Full Regression Test Run**:
   - Run all test suites across the repository (`tests/test_meeting_lifecycle_simulation.py`, `tests/test_toggle_bar_states_e2e.py`, `tests/test_notetaker_context_aware_e2e.py`, `tests/test_ux_accessibility_regressions.py`, etc.).
2. **Audit Verification**:
   - Ensure 100% tests pass cleanly.
3. **Final Report Preparation**:
   - Synthesize all findings and report final completion back to user.

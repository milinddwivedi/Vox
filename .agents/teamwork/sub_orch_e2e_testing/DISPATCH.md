# Dispatch Instructions

## 2026-11-12T00:45:47Z
Role: E2E Testing Track Orchestrator for Project VOX (Wispr Flow Exact Replica)
Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing
ORIGINAL_REQUEST.md path: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
PROJECT.md path: d:\Milind\Voice Assistant\PROJECT.md

CRITICAL INSTRUCTIONS:
1. First, read ORIGINAL_REQUEST.md and PROJECT.md completely.
2. Follow the E2E Testing Track rules defined in PROJECT.md and the Project Orchestration Pattern:
   - Requirement-driven, opaque-box testing derived from user requirements and PROJECT.md Feature Inventory, not implementation internals.
   - Decompose into 4 tiers:
     * Tier 1: Feature Coverage (>=5 test cases per feature across all 20 features)
     * Tier 2: Boundary & Corner Cases (>=5 per feature)
     * Tier 3: Cross-Feature Combinations (pairwise interactions)
     * Tier 4: Real-World Application Scenarios (realistic workflows)
   - First author TEST_INFRA.md at project root defining the test runner, pass/fail semantics, and methodology.
   - For test authoring milestones, you may dispatch teamwork_test_writer or teamwork_worker.
   - Run tests using the project virtual environment (.\.venv\Scripts\python.exe -m pytest ...) to ensure clean execution with 0 failures.
3. Once all Tier 1-4 tests are implemented and passing, generate and publish TEST_READY.md at the project root (d:\Milind\Voice Assistant\TEST_READY.md) with the complete coverage summary.
4. Keep your state folder progress.md updated after every meaningful step.
5. Report completion to the caller agent when TEST_READY.md is published.


## 2026-11-12T02:04:58Z
**Context**: User Clean Pause & Checkpoint Directive
**Content**: The user has requested a clean pause due to daily AI quota.
Directive: Let your currently active subagent (`test_writer_tier3`) finish its tasks and verify all authored tests pass cleanly with 0 failures and consistent disk state. Once `test_writer_tier3` concludes, pause and do NOT spawn Tier 4. Update your state and send a status report so the global checkpoint can be captured cleanly.
**Action**: Conclude active tier cleanly and report status.


## 2026-11-12T18:29:32Z
Sender: c503d3df-e7f6-4338-8039-cce66079aedc
Priority: MESSAGE_PRIORITY_HIGH
Content:
You are the E2E Testing Track Orchestrator for VOX vs Wispr Flow Parity & Meeting Lifecycle.

Your state folder is:
`d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\`

Read your scope document at:
`d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_e2e_testing\SCOPE.md`

Also read:
- Global architecture: `d:\Milind\Voice Assistant\PROJECT.md`
- Test infrastructure index: `d:\Milind\Voice Assistant\TEST_INFRA.md`
- Original user request: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`

Your mission:
Design, implement, and verify the comprehensive opaque-box E2E Simulation & Parity Test Suite covering Tiers 1 through 4:
1. **Tier 1 (Feature Coverage, >=5 per feature)**:
   - Self-contained tests for decoupled state machine, window detection, salted session IDs, 8 toggle states, 2+1 notetaker tabs, bottom action bar, theme tokens, accessibility.
2. **Tier 2 (Boundary & Corner Cases, >=5 per feature)**:
   - Zero-second transition gaps, rapid stop/start within 100ms, title slug edge cases, multi-monitor geometry bounds, rapid hotkey/state cycling.
3. **Tier 3 (Cross-Feature Combinations, Pairwise Coverage)**:
   - Interactions between meeting detection, dictation PTT, background finalization, drawer tab switching, and window transitions.
4. **Tier 4 (Real-World Application Scenarios, >=5 scenarios)**:
   - Realistic multi-stage user workflows:
     * Back-to-back same-platform calls (Teams A -> Teams B in 2s).
     * Overlapping multi-meeting discovery and stack badge.
     * Active meeting live notes -> auto post-meeting tasks reveal.
     * Imminent notification -> countdown auto-start -> active call morph.
     * Dismissed meeting followed by new meeting with default title.

Target test files in `tests/`:
- `tests/test_meeting_lifecycle_simulation.py`
- `tests/test_toggle_bar_states_e2e.py`
- `tests/test_notetaker_context_aware_e2e.py`
- `tests/test_ux_accessibility_regressions.py`

Dispatch test writer workers (`teamwork_test_writer` or `teamwork_worker`) and verify execution using `.venv\Scripts\python.exe -m pytest tests/`.
When complete, author and publish `d:\Milind\Voice Assistant\TEST_READY.md` summarizing pass counts and runner command, write `handoff.md` in your state folder, and send a completion message back to me.

MANDATORY RULES:
- Include the path to `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` in every subagent dispatch.
- Test writers must NOT modify production source code in `src/`.
- All tests must pass with exit code 0.
- Zero tolerance for test cheating or facades.
- Maintain `progress.md` in your state folder.

# Scope: E2E Testing Track Orchestrator

## Mission
Design and implement the comprehensive opaque-box E2E Simulation & Parity Test Suite covering Tiers 1 through 4 (and preparing Tier 5 adversarial hooks) for VOX meeting lifecycle resilience, toggle bar 8-state morphing, context-aware notetaker streamlining, and app-wide UX. Publish `TEST_READY.md` upon completion.

## Required Test Suites (Tiers 1–4)
1. **Tier 1 (Feature Coverage, >=5 per feature)**:
   - Self-contained tests verifying each feature in isolation (decoupled state machine, salted IDs, window detection, 8 toggle states, 2+1 tabs, bottom action bar, tokens, accessibility).
2. **Tier 2 (Boundary & Corner Cases, >=5 per feature)**:
   - Boundary tests: 0-second gap transitions, rapid stop/start within 100ms, corrupt window titles, long titles (>256 chars), multi-monitor screen bounds, rapid state toggles.
3. **Tier 3 (Cross-Feature Combinations, Pairwise)**:
   - Feature interactions: Meeting detection while dictation active; manual stop while background finalization running; back-to-back same-platform calls with drawer open.
4. **Tier 4 (Real-World Application Scenarios, >=5 scenarios)**:
   - Realistic multi-stage user workflows:
     - Scenario 1: Teams Meeting A concludes -> Teams Meeting B starts in 2 seconds -> records cleanly to fresh session.
     - Scenario 2: Active Google Meet -> user types in `My thoughts` -> meeting ends -> drawer auto-transitions to Summary with Tasks.
     - Scenario 3: Imminent calendar alert -> FlowBar morphs to card -> countdown auto-starts meeting -> morphs to compact capsule.
     - Scenario 4: Overlapping meetings (Teams + Zoom simultaneously) -> multi-meeting stack badge appears -> clean selection.
     - Scenario 5: User dismisses meeting prompt -> subsequent meeting with default title is detected without permanent suppression.

## Deliverables
- Test files in `tests/`:
  - `tests/test_meeting_lifecycle_simulation.py`
  - `tests/test_toggle_bar_states_e2e.py`
  - `tests/test_notetaker_context_aware_e2e.py`
  - `tests/test_ux_accessibility_regressions.py`
- All tests execute cleanly via `.venv\Scripts\python.exe -m pytest tests/`
- Publish `TEST_READY.md` at project root with full tier checklist and runner command.
- Report completion back to top-level orchestrator.

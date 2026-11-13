# Orchestration Plan — VOX & Wispr Flow Parity (Gen 2)

## Objectives
1. **Milestone 5 Remediation & Gate Pass**:
   - Dispatch Worker `worker_m5_remediation` to fix the 4 issues highlighted in `.agents/teamwork/reviewer_m5_1/review.md` (`call_detector.py` title regex order, `controller.py` try...except around `stop()`, `meeting_recorder.py` `@is_recording.setter`, and any remaining test issues in `tests/test_meeting_lifecycle_simulation.py`).
   - Run tests and verify 61/61 passing in `tests/test_meeting_lifecycle_simulation.py`.
   - Dispatch Reviewer 2, Challengers 1 & 2, and Forensic Auditor to complete Milestone 5 gate.

2. **Milestone 6: Toggle Bar Polymorphic 8-State Engine & Spring Morphing**:
   - Inspect requirements R1 and reference video `External References/Toggle Bar Comparision.mp4`.
   - Dispatch Worker to implement the 8-state FlowBar (`RESTING`, `MINI_MEETING`, `CARD_NOTIFICATION`, `ACTIVE_RECORDING`, `L4_PREVIEW`, etc.), 420ms cubic spring geometry animations, notification consolidation, platform badges (Meet, Zoom, Teams, Slack), split Join button, and emerald dot.
   - Verify 60/60 passing in `tests/test_toggle_bar_states_e2e.py`.
   - Reviewer, Challenger, Auditor gate sign-off.

3. **Milestone 7: Live Notetaker Context-Aware Tabs & Floating Bar Streamlining**:
   - Inspect requirements R2 and reference video `External References/Notetaker comparision.mp4`.
   - Implement dynamic 2+1 tab model during live calls (`My thoughts`, `Transcript`, `+ Summary`), hide `Tasks` until post-finalization.
   - Implement persistent integrated bottom drawer (`Ask anything...`, `[ What did I miss? ]`).
   - Implement full work-area height screen edge snapping and compact floating capsule with 4-bar mini waveform.
   - Verify 50/50 passing in `tests/test_notetaker_context_aware_e2e.py`.
   - Reviewer, Challenger, Auditor gate sign-off.

4. **Milestone 8: Comprehensive App-Wide UX Polish & Accessibility**:
   - Validate design tokens in `theme.py`.
   - Validate settings live mic meter and empty states.
   - Verify screen reader accessibility and keyboard navigation.
   - Verify `tests/test_ux_accessibility_regressions.py`.
   - Reviewer, Challenger, Auditor gate sign-off.

5. **Milestone 9: Final E2E Suite Pass, Adversarial Hardening & Smoke Test**:
   - Run complete test suite across all 5 tiers.
   - Verify zero regressions and standalone executable readiness.
   - Synthesize final human report.

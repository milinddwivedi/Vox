# Orchestration Plan: VOX vs Wispr Flow UX Parity & Back-to-Back Meeting Lifecycle Bug Resolution

## Objective
Execute a rigorous multi-agent overhaul addressing:
1. Complete state comparison & refinement of the VOX Toggle Bar (including mini-toggle) across all 8 states to eliminate visual clutter, sluggishness, and awkward spacing to match Wispr Flow.
2. Streamlining the Live Notetaker (removing upfront tabs during live meetings) and refining the primary floating notetaker bar (compact footprint, clear recording feedback, smooth expand/collapse).
3. Root-cause analysis and complete resolution of the back-to-back meeting lifecycle bug, ensuring clean session termination, audio handle and timer cleanup, independent subsequent meeting detection, and fresh session launch without app restarts.
4. Comprehensive application-wide UX refinements (navigation, onboarding, settings, history, shortcuts, accessibility) with preservation of working features, backed by simulation harnesses and regression suites.

## Orchestration Phases

### Phase 0: Survey & Deep Analysis (Current)
- Dispatch 3 parallel Explorers:
  - **Explorer 1 (UX & Toggle Bar Specialist)**: Inspect `External References/Toggle Bar Comparision.mp4`, running Wispr Flow & VOX code. Map all 8 toggle bar states, visual styling, animations, typography, spacing, mini-toggle, plus app-wide UX touchpoints.
  - **Explorer 2 (Notetaker & Floating Bar Specialist)**: Inspect `External References/Notetaker comparision.mp4`, live notetaker window/components, floating bar, active recording indicator, tab structure (Live Transcript, Summary), expansion/collapse behavior.
  - **Explorer 3 (Meeting Lifecycle Specialist)**: Audit meeting detection, session lifecycle state machine, audio capture handles, recording streams, IPC messaging, and timers. Identify exact root cause for why back-to-back meetings fail to launch fresh sessions.
- Synthesize findings into `PROJECT.md` Feature Inventory, Architecture, and Milestones.

### Phase 1: Milestone Decomposition & Track Launch
- Formulate modular Milestones:
  - **Milestone 1**: Toggle Bar & Mini-Toggle State Overhaul (all 8 states, fluid animations, typography, spacing, low-latency transitions).
  - **Milestone 2**: Live Notetaker & Floating Notetaker Bar Streamlining (context-aware minimal UI, hiding tabs during active meeting, compact floating pill, controls).
  - **Milestone 3**: Meeting Lifecycle Architecture & Back-to-Back Session Fix (clean teardown, resource release, independent re-detection, fresh session initialization, overlapping/rapid transition resilience).
  - **Milestone 4**: Full Application UX Polish (navigation, history, settings, shortcuts, notifications, onboarding).
  - **E2E Testing Track**: Simulation harnesses for meeting transitions (back-to-back, rapid stop/start, overlapping), state verification, and UI regression tests.
  - **Milestone 5 / Final Milestone**: 100% E2E test pass, adversarial stress testing, and final packaging smoke verification.

### Phase 2: Iteration & Execution
- Delegate each milestone to dedicated sub-orchestrators or worker-reviewer-challenger-auditor loops.
- Enforce strict integrity: zero hardcoding, zero facade implementations, forensic audit veto.

### Phase 3: Final Verification & Turnkey Delivery
- Verify 100% test pass on live simulations and regressions.
- Deliver comprehensive human report.

Use specialised agents for UX analysis, meeting lifecycle debugging, UI implementation and independent QA. Assign clear ownership to avoid conflicting changes. Perform a deep UX comparison between VOX and Wispr Flow across all states and workflows, implement UI refinements directly in VOX to match Wispr Flow's fluid, minimal standard, and resolve the critical back-to-back meeting lifecycle bug.

Working directory: d:\Milind\Voice Assistant
Integrity mode: development

Reference Material:
- Toggle Bar Comparison Video: `D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4`
- Notetaker Comparison Video: `D:\Milind\Voice Assistant\External References\Notetaker comparision.mp4`
- Locally installed Wispr Flow running alongside VOX

## Requirements

### R1. Toggle Bar Complete State Comparison & Refinement
Inspect both the comparison video (`External References/Toggle Bar Comparision.mp4`) and the local running instances of Wispr Flow and VOX across all states (no meeting active, upcoming/awaiting, notifications and joining, active meeting, ending/disconnecting, app/window switching, back-to-back meetings, and failed detection/permissions/recovery). Refine the VOX toggle bar (including the mini-toggle) directly in code to eliminate visual clutter, sluggishness, and awkward spacing, matching Wispr Flow's fluid animations, crisp typography, clean layout, and low-latency state transitions.

### R2. Live Notetaker & Floating Bar Interface Streamlining
Audit the entire notetaker experience across all lifecycle stages (before meeting, detection/activation, live meeting, recording/transcription, ending/processing, summaries/actions, and errors/recovery) using the comparison video (`External References/Notetaker comparision.mp4`) and running applications. Make the VOX notetaker minimal and context-aware by removing unnecessary upfront tabs (such as Live Transcript and Summary) during live meetings. Separately audit and refine the primary floating notetaker bar visible during active meetings to ensure a compact visual footprint, clear recording feedback, unobtrusive controls, and smooth expansion/collapse behavior.

### R3. Back-to-Back Meeting Lifecycle Bug Resolution
Investigate and resolve the critical bug where consecutive or overlapping meetings fail to initiate a fresh session in VOX. Implement a reliable meeting lifecycle that correctly finalizes prior sessions, releases all audio and recording resources, independently detects subsequent meetings, and launches fresh notetaker sessions without app restarts, duplicate sessions, stale UI states, or recording conflicts. Ensure robust handling of edge cases including rapid transitions, overlapping meetings, manual stop/start, and reconnection.

### R4. Comprehensive VOX UX Audit & Direct Application Refinements
Conduct an end-to-end UX audit of the complete VOX application against Wispr Flow workflows with equal priority to core components, covering navigation, onboarding, notifications, meeting history, settings, shortcuts, loading/empty states, error handling, and accessibility. Directly implement root-cause fixes and refinements in the VOX codebase while preserving existing working functionality and user data. Create test harnesses or simulation scripts to simulate and verify meeting transitions and regression scenarios.

## Acceptance Criteria

### Toggle Bar & Live Notetaker Experience
- [ ] Toggle bar and mini-toggle display polished visuals, correct spacing, readable typography, and smooth transitions across all 8 defined meeting states without visual glitches or lag.
- [ ] Live notetaker interface presents only context-relevant information during active meetings, hiding unnecessary tabs (Live Transcript, Summary) until relevant.
- [ ] Primary floating notetaker bar remains compact and unobtrusive on screen with clear live recording status and responsive controls.

### Meeting Lifecycle & Stability
- [ ] Back-to-back meetings transition cleanly: concluding Meeting A and initiating Meeting B immediately and independently starts a fresh notetaker session without restarting VOX.
- [ ] All recording resources, audio handles, event listeners, and timers from prior sessions are fully released with no memory leaks or recording conflicts.
- [ ] Rapid transitions, overlapping meetings, manual stop/start, and network reconnections handle state changes gracefully without duplicate sessions or stale UI states.

### Full Application UX & Verification
- [ ] End-to-end UX improvements across navigation, settings, history, and notifications are implemented directly in the VOX codebase.
- [ ] The team executes and verifies test scenarios (using live meetings or custom simulation harnesses) demonstrating that existing functionality is preserved and regressions are prevented.
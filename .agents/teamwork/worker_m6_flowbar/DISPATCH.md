# Task Assignment: Milestone 6 (Toggle Bar 8-State Engine & Spring Morphing)

## Objective
Implement Requirement R1: Polymorphic VoxFlowBar 8-State Engine, Spring Geometry Morphing, and Notification Consolidation directly in `src/voxlocal/ui/floating_pill.py` (and related controller routing in `src/voxlocal/core/controller.py`).

## Inputs & Context
1. `ORIGINAL_REQUEST.md`: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
2. `PROJECT.md`: `d:\Milind\Voice Assistant\PROJECT.md` (Features 6–10, Interface Contract 3)
3. Explorer Audit Report: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m6_m7_ui\handoff.md`
4. Reference Video: `D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4`
5. Target Test Suite: `d:\Milind\Voice Assistant\tests\test_toggle_bar_states_e2e.py`

## Exclusive Write Ownership
- `src/voxlocal/ui/floating_pill.py`
- `src/voxlocal/core/controller.py` (only lines related to FlowBar state routing and deprecating `meeting_prompt_popup`)

## Mandatory Implementation Requirements
1. **Polymorphic 8-State Machine**:
   - Extend `PillState` or define `FlowBarState` / polymorphic states in `floating_pill.py`:
     * `RESTING` (30×6px capsule)
     * `MINI_MEETING` (416×48px `layMini` layout)
     * `CARD_NOTIFICATION` (416×80px `layCard` layout)
     * `ACTIVE_RECORDING` (120×36px compact obsidian capsule)
     * `L4_PREVIEW` (416×322px agenda preview)
     * Dictation states (`READY`, `LISTENING`, `PROCESSING`, `COMMITTED`, `ERROR`)
   - Implement `set_pill_state(new_state, metadata=None, animated=True)` method.
2. **Spring Geometry Animations**:
   - Implement `QVariantAnimation` with cubic easing curve (`QEasingCurve.Type.OutCubic`) over 420ms to smoothly interpolate width and height between states without discrete snapping.
   - Smooth element opacity transition: fade-out current layout elements in 80ms, fade-in new layout elements after 100ms.
3. **Mini-Toggle Platform Badges & Layout (`MINI_MEETING`)**:
   - 416×48px capsule with:
     * Platform SVG / colored badge (Google Meet, Zoom, Microsoft Teams, Slack)
     * Meeting title with 20px alpha fade gradient mask
     * Emerald status dot (`#10B981`)
     * Live countdown / meeting timer
     * Corner stack badge indicator when multiple meetings are active (`get_active_meeting_count() > 1`)
4. **Card Notification Layout (`CARD_NOTIFICATION`)**:
   - 416×80px notification card with:
     * Split Join CTA button
     * Animated auto-start countdown progress fill underlay
     * Caret dropdown menu (Dismiss, Ignore, Snooze)
5. **Notification Consolidation**:
   - Route incoming meeting detection and calendar alert events directly into `VoxFlowBar` via `set_pill_state`.
   - Ensure a single unified window at bottom-center of the screen; eliminate duplicate bottom-right toast popups.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Verification
Run tests:
`.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v`
Ensure all 60 tests pass. Also run `tests/test_meeting_lifecycle_simulation.py` to ensure 0 regressions.



## 2026-11-12T19:56:14Z
From: c64cac7c-8375-4ba8-953b-a6a6e6719488
You are worker_m6_flowbar (teamwork_worker).
Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\worker_m6_flowbar\

Read your dispatch assignment:
d:\Milind\Voice Assistant\.agents\teamwork\worker_m6_flowbar\DISPATCH.md

MANDATORY CONTEXT:
Read ORIGINAL_REQUEST.md before starting:
d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

Read PROJECT.md for architecture and contracts:
d:\Milind\Voice Assistant\PROJECT.md

Read Explorer Audit Report:
d:\Milind\Voice Assistant\.agents\teamwork\explorer_m6_m7_ui\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your exclusive write ownership:
- src/voxlocal/ui/floating_pill.py
- src/voxlocal/core/controller.py (only FlowBar alert routing / popup consolidation)

Implement Requirement R1:
1. Polymorphic 8-state machine in VoxFlowBar (RESTING, MINI_MEETING, CARD_NOTIFICATION, ACTIVE_RECORDING, L4_PREVIEW).
2. QVariantAnimation 420ms cubic spring geometry morphing.
3. Mini-toggle layMini 416x48px with platform badges, emerald dot #10B981, title fade, countdown timer.
4. Card notification layCard 416x80px with split Join button and auto-start fill underlay.
5. Consolidate call alerts into FlowBar (deprecate duplicate meeting_prompt_popup).

Run tests:
.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v
.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v

Write your report to handoff.md and send completion message to parent.


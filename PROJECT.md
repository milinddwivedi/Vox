# Project: VOX (Wispr Flow Exact Parity & Resilient Meeting Lifecycle)

## Architecture
VOX is an exact, high-performance running replica of the installed Wispr Flow desktop application on Windows 11, branded under the name **VOX**. 

### Architectural Overview
- **Runtime Environment**: High-performance native Windows PyQt6 application running on Python 3.13, packaged as a standalone zero-dependency executable (`dist/VoxLocal/VoxLocal.exe`).
- **Core Architecture Components**:
  1. **Win32 Hook & Injection Layer**: Low-level `WH_KEYBOARD_LL` Windows keyboard hook for global Push-To-Talk (PTT), Push-On-Push-Off (POPO), and mode shortcuts (`Win+F1..F5`) without requiring administrator elevation.
  2. **Speech & Audio Pipeline**: Real-time WASAPI microphone capture at 16kHz mono with 40ms (640-sample) chunking, dynamic RMS calculation, and pre-speech buffer. Speech transcription powered by `faster-whisper` (CTranslate2) with automatic CUDA and multi-threaded CPU fallback.
  3. **Decoupled Dual-Track Meeting Lifecycle**:
     - `_capture_state`: Fast, non-blocking capture state machine (`idle`, `recording`, `paused`) that resets to `idle` in <300ms upon meeting conclusion.
     - `finalization_queue`: Background worker pool processing transcription, speaker diarization, and MoM summaries without blocking incoming call detection or new recording sessions.
     - `CallDetector`: Window-tracking and audio detection engine with salted session IDs, multi-meeting stack tracking, and platform-specific window lifecycle management preventing same-platform window rebounding.
  4. **Polymorphic FlowBar (Toggle Bar)**: Single, unified bottom-docked window (`VoxFlowBar`) that morphs across all 8 meeting and dictation states via 420ms cubic spring geometry animations (`QVariantAnimation`):
     - `RESTING` (30×6px capsule)
     - `MINI_MEETING` (416×48px `layMini` with platform badge, title with 20px alpha fade, emerald dot `#10B981`, countdown timer, and corner stack badge)
     - `CARD_NOTIFICATION` (416×80px `layCard` with split Join button, auto-start progress fill, and caret menu)
     - `ACTIVE_RECORDING` (120×36px compact capsule with 4-bar mini waveform, recording pulse, timer, and hover tray)
     - `L4_PREVIEW` (416×322px agenda and participant preview)
  5. **Context-Aware Live Notetaker Drawer**:
     - Snapped to screen edge at full work-area height (`height = workArea.height`).
     - Dynamic 2+1 tab model: Displays only `My thoughts`, `ılı Transcript`, and `+ Summary` during active calls; reveals `Tasks (count)` post-finalization.
     - Integrated bottom drawer with `Ask anything...` and `[ What did I miss? ]` chip.
  6. **App-Wide UX & Accessibility**:
     - Standardized semantic design tokens (`theme.py`).
     - Empty states with 1-click quick-starts.
     - Live microphone input level meter in Settings.
     - Comprehensive screen reader accessibility (`accessibleName`, `accessibleDescription`, ARIA roles).

---

## Feature Inventory

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Decoupled Meeting Capture State | Decouple `_capture_state` from background finalization queue, unblocking immediate session starts | M5 | Lifecycle Survey §1.1 |
| 2 | Platform Window Transition Engine | Differentiate window resizes from back-to-back calls on Teams/Zoom; eliminate window rebounding | M5 | Lifecycle Survey §1.2 |
| 3 | Salted Session ID & Suppression Pruning | Incorporate HWND and timestamp into session IDs; prune dismissed/ignored sets on session end | M5 | Lifecycle Survey §1.3 |
| 4 | Clean Audio & Detector Teardown | Reset `CallDetector.active_session` on manual stop; ensure zero WASAPI/COM handle leaks | M5 | Lifecycle Survey §1.4 |
| 5 | Guarded UI Signal Routing | Ensure asynchronous finalization signals do not overwrite or reset actively recording sessions | M5 | Lifecycle Survey §1.5 |
| 6 | Polymorphic FlowBar 8-State Engine | Single unified window morphing across all 8 states (Resting, Mini, Card, Active, Ending, Switch, Stack, Error) | M6 | Toggle Bar Survey §3.1 |
| 7 | Spring Geometry Morphing | Hardware/smooth `QVariantAnimation` (420ms cubic easing) replacing discrete `setFixedSize` snapping | M6 | Toggle Bar Survey §4.1 |
| 8 | Notification Popup Consolidation | Route call detection and calendar alerts into FlowBar; deprecate duplicate bottom-right popups | M6 | Toggle Bar Survey §2.2 |
| 9 | Mini-Toggle Platform Badges & Styling | 416×48px layout with SVG badges (Meet, Zoom, Teams, Slack), masked title fade, emerald status dot | M6 | Toggle Bar Survey §4.1 |
| 10 | Split Join Button & Auto-Start Progress | Split CTA with animated auto-start countdown fill underlay and caret menu | M6 | Toggle Bar Survey §4.1 |
| 11 | Context-Aware Notetaker Tabs | Dynamic 2+1 tab model during live calls (`My thoughts`, `Transcript`, `+ Summary`); hide `Tasks` until complete | M7 | Notetaker Survey §1.3 |
| 12 | Integrated Notetaker Bottom Drawer | Persistent bottom action bar with `Ask anything...` input and `[ What did I miss? ]` quick prompt | M7 | Notetaker Survey §1.3 |
| 13 | Notetaker Screen Edge Snapping | Snap notetaker window flush to display work area edge at full available height | M7 | Notetaker Survey §2.3 |
| 14 | Compact Floating Notetaker Bar | Obsidian black capsule with 4-bar mini waveform, recording pulse, timer, and hover tray (Mute, Pause, Bookmark, Stop) | M7 | Notetaker Survey §2.4 |
| 15 | App-wide Semantic Design Tokens | Eliminate hardcoded hex codes across UI; centralize tokens in `theme.py` | M8 | UX Audit §5.1 |
| 16 | Polished Empty States & Quick Starts | Rich illustration widgets and 1-click starter prompts for empty History and Snippets | M8 | UX Audit §5.4 |
| 17 | Settings UX Polish & Live Mic Meter | Live audio input deflection meter in Audio Settings and user-friendly Whisper compute labels | M8 | UX Audit §5.5 |
| 18 | Screen Reader & Keyboard Accessibility | Full keyboard navigation in floating overlays and `setAccessibleName`/`Description` across all controls | M8 | UX Audit §5.6 |
| 19 | Meeting Simulation Test Suite (T1-T4) | Automated simulation suite for back-to-back transitions, overlapping meetings, and edge cases | E2E Track | Testing Methodology |
| 20 | Final E2E Suite Pass & Production Smoke | Verify 100% test pass on all suites, adversarial hardening (Tier 5), and standalone binary build | M9 | Acceptance Criteria |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M5 | Meeting Lifecycle Architecture & Back-to-Back Bug Resolution | Decoupled dual-track state machine, window rebound fix, salted session IDs, detector reset, audio handle teardown, UI signal guards | None | DONE |
| M6 | Toggle Bar Polymorphic 8-State Engine & Spring Morphing | Single polymorphic `VoxFlowBar`, 8-state machine, spring geometry animations, notification consolidation, platform badges | M5 | PLANNED |
| M7 | Live Notetaker Context-Aware Tabs & Floating Bar Streamlining | Context-aware tabs (2+1 model), hidden Tasks during calls, integrated bottom drawer, screen edge snapping, compact floating capsule | M5 | PLANNED |
| M8 | Comprehensive App-Wide UX Polish & Accessibility | Semantic design tokens, empty states, settings live mic meter, user-friendly labels, accessibility attributes | None | PLANNED |
| E2E | E2E Simulation & Parity Test Suite Track | Build comprehensive simulation harnesses for meeting transitions, toggle bar states, notetaker UX, and regressions | Parallel with M5-M8 | PLANNED |
| M9 | Final E2E Test Suite Pass, Adversarial Hardening & Verification | 100% E2E test pass across all tiers, adversarial coverage hardening, standalone executable smoke test | M5, M6, M7, M8, E2E | PLANNED |

---

## Interface Contracts

### 1. Meeting Lifecycle -> FlowBar & Notetaker (`core/controller.py` <-> `ui/floating_pill.py` & `ui/notetaker_drawer.py`)
- **Signals**:
  - `meeting_detected(MeetingSession session)`: Triggers FlowBar morph to `CARD_NOTIFICATION` or `MINI_MEETING`.
  - `meeting_recording_started(int meeting_id, str title, str platform)`: Triggers FlowBar morph to `ACTIVE_RECORDING` with mini waveform; triggers Notetaker drawer snap with live `My thoughts` tab.
  - `meeting_recording_stopped(int meeting_id)`: Triggers FlowBar transition to `RESTING` in 1.9s; switches Notetaker drawer to `Summary` tab and displays background finalization indicator.
  - `meeting_finalized(int meeting_id, dict deliverables)`: Delivers final summary and action items; dynamically reveals `Tasks (count)` tab if `meeting_id == drawer.current_meeting_id`. If another meeting is currently recording, drawer does NOT interrupt active recording controls.

### 2. Detector Engine -> Lifecycle Controller (`audio/call_detector.py` <-> `core/controller.py`)
- **Contract**:
  - `poll() -> Optional[CallEvent]`: Returns `CallEvent(event_type="started"|"ended", session_id=str, title=str, platform=str, hwnd=int)`.
  - `end_active_session_immediately()`: Instantly clears `active_session`, resets detector state to `IDLE`, and prunes suppression sets, permitting immediate detection of subsequent meetings.
  - `get_active_meeting_count() -> int`: Returns current count of concurrent or detected meetings for the FlowBar stack badge.

### 3. FlowBar State Engine -> Visual Layouts (`ui/floating_pill.py`)
- **State Machine**:
  - `set_pill_state(PillState new_state, dict metadata=None, bool animated=True)`
  - Dimensions:
    * `RESTING`: `(30, 6)`
    * `MINI_MEETING`: `(416, 48)`
    * `CARD_NOTIFICATION`: `(416, 80)`
    * `ACTIVE_RECORDING`: `(120, 36)`
    * `L4_PREVIEW`: `(416, 322)`
  - Spring Morph: `QVariantAnimation` with `QEasingCurve.Type.OutCubic`, duration `420ms`. Sub-elements fade out in `80ms` and fade in after `100ms` delay.

---

## Code Layout

```
d:\Milind\Voice Assistant\
├── src/
│   └── voxlocal/
│       ├── audio/                 # Audio capture, device manager, and call detector
│       │   ├── call_detector.py   # Window tracking, call detection, meeting state machine
│       │   ├── meeting_recorder.py# WASAPI loopback + mic capture for meetings
│       │   └── dictation_recorder.py
│       ├── core/                  # Application controller & state machines
│       │   ├── controller.py      # AppController, dual-track meeting lifecycle, finalization queue
│       │   └── meeting_stack.py   # Multi-meeting stack manager & session tracking
│       ├── ui/                    # User interface components
│       │   ├── floating_pill.py   # Polymorphic VoxFlowBar, 8-state machine, spring morphing
│       │   ├── notetaker_drawer.py# Context-aware Notetaker drawer, edge snapping, 2+1 tabs
│       │   ├── notetaker_page.py  # Meeting history, empty states, summary viewing
│       │   ├── theme.py           # Central semantic design tokens
│       │   ├── main_window.py     # Shell window, navigation, status indicator
│       │   └── settings_dialog.py # Settings, live mic meter, compute options
└── tests/                         # Automated test suites
    ├── test_meeting_lifecycle_simulation.py # Back-to-back, rapid transition & overlap simulations
    ├── test_toggle_bar_states_e2e.py       # 8-state morphing & geometry verification
    ├── test_notetaker_context_aware_e2e.py  # Context-aware tab & floating capsule tests
    └── test_ux_accessibility_regressions.py # Semantic tokens, accessibility & settings tests
```

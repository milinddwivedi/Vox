# Comprehensive Survey & Technical Investigation Report: VOX Toggle Bar & UX Audit

**Author:** Explorer 1 — Toggle Bar & UX Specialist  
**Target:** Requirement 1 (Toggle Bar Complete State Comparison & Refinement) & Requirement 4 (Comprehensive VOX UX Audit)  
**Date:** 2026-11-12  
**Codebase:** `d:\Milind\Voice Assistant`  
**Reference Video:** `D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4`  
**Benchmark Target:** Wispr Flow Desktop (v1.6.1457)  

---

## 1. Executive Summary

This investigation provides an exhaustive technical and design audit comparing the VOX voice assistant application against Wispr Flow (v1.6.1457), utilizing forensic frame-by-frame demuxing of the reference video (`Toggle Bar Comparision.mp4`), decompilation and static analysis of the locally installed Wispr Flow Electron ASAR bundle (`resources/app.asar`), and an end-to-end code audit of the VOX codebase (`src/voxlocal/`).

### Core Discoveries & Architectural Divergence:
1. **The Single Polymorphic Floating Pill Paradox:**
   - In Wispr Flow, the desktop floating element is a **single, unified, polymorphic surface** (`pillRoot` / `pillCard`) that smoothly morphs across all states: resting capsule (`data-state=rest`, 30×6px), active dictation, meeting notification mini-toggle (`data-state=mini`, 416×48px), expanded invitation card (`data-state=card`, 416×80px), multi-meeting fan view (`level=fan`), and deep agenda preview (`data-state=l4`, 416×322px).
   - In VOX, the floating bar (`VoxFlowBar` in `src/voxlocal/ui/floating_pill.py`) is restricted solely to dictation. When a meeting is detected or upcoming, VOX launches a completely separate, disconnected window: `MeetingPromptPopup` (`src/voxlocal/ui/meeting_prompt_popup.py`) or `VoxMeetingPromptPopup` (`src/voxlocal/ui/calendar_prompt.py`, 440×152px) in the bottom-right corner of the display. This creates cognitive fragmentation, window stacking conflicts, and visual clutter.
2. **Missing Mini-Toggle State in VOX:**
   - Wispr Flow features a dedicated 416×48px resting mini-toggle (`layMini`) displaying a 24×24px platform badge (Google Meet, Zoom, MS Teams, Slack Huddle), meeting title with an elegant 20px right-hand alpha fade mask, an emerald status dot (`#10b981`), live countdown ("in 2m", "now", "ongoing"), and an overlapping circular corner badge (`cornerBadge`) indicating multi-meeting stacks.
   - VOX has no mini-toggle concept. During an active meeting, it appends an awkward, hardcoded button `QPushButton("● 00:00 Notetaker")` with a `#FF5757` border to the side of the dictation pill (`floating_pill.py:576`), resulting in an asymmetrical layout with zero smooth transitions.
3. **Motion Physics & Spring Morphing Discrepancy:**
   - Wispr Flow utilizes continuous hardware-accelerated CSS spring morphing (`--pill-morph-duration: 0.42s; var(--spring-easing); cubic-bezier(0.16, 1, 0.3, 1)`) with coordinated sub-element opacity fades (180ms ease with 100ms delay).
   - VOX relies on discrete, instantaneous Qt geometry changes (`setFixedSize`), which results in jarring resizing, visual snapping, and layout flicker.
4. **App-wide UX Inconsistencies (R4):**
   - VOX exhibits inconsistent design tokens (mixed hardcoded hex colors alongside `theme.py`), unstyled empty states in meeting history and snippets, upfront cognitive overload in `notetaker_drawer.py` (which exposes 5 complex tabs simultaneously during live recording instead of focusing on private thoughts), modal-heavy settings workflows, and missing accessibility attributes (`accessibleName`, `accessibleDescription`, ARIA roles).

---

## 2. Reference Material & Codebase Exploration

### 2.1 Video Forensic Analysis (`Toggle Bar Comparision.mp4`)
- **Container & Streams:** 1918×1078 AVC/H.264 video at 30.00 fps, constant frame rate, duration 64.67s (1,940 decoded frames). Silent audio stream (mono AAC).
- **Timeline Segmentation:**
  - **00.00s – 43.50s (VOX Current Behavior):**
    - The bottom-center floating dictation pill sits in its resting state (dark capsule).
    - Concurrently, when a meeting is active or detected, an entirely separate white/light card (`MeetingPromptPopup` or `calendar_prompt`) sits anchored at `(w - 480, h - 228)` in the bottom-right corner.
    - Two separate floating surfaces compete for the user's attention. As the meeting starts, an asymmetrical `● 00:00 Notetaker` chip attaches itself to the bottom-center pill, and `VoxNotetakerDrawer` opens with full transcript and summary tabs upfront.
  - **44.00s – 51.50s (Wispr Flow Reference Standard):**
    - Screen cuts to Wispr Flow operating on desktop.
    - Noticeable absence of any bottom-right toast or auxiliary popup.
    - The single bottom-center pill morphs from resting capsule (`30×6px`) directly into the 416×48px `mini` toggle bar, displaying the platform icon, meeting title, emerald pulse dot, and countdown.
    - On hover, the mini-toggle smoothly expands into the 416×80px card containing the split Join button with auto-start fill indicator and dismiss controls.
  - **52.00s – 64.67s (Direct Contrast Walkthrough):**
    - Demonstrates window switching and state persistence. Wispr Flow maintains z-index and screen-dock invariance without flashing or focus-stealing, whereas VOX exhibits multi-window layering glitches when switching applications.

### 2.2 Wispr Flow ASAR Decompilation & Static Architecture
Decompilation of Wispr Flow v1.6.1457 (`C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar`) revealed the exact production architecture inside `.webpack/renderer/status/index.js` and `.webpack/renderer/overlay/index.js`:
- **State Reducer (`Rt`):** Manages level transitions: `"mini"` <-> `"card"` <-> `"fan"` <-> `"l4"`.
- **Action Dispatchers:** `park`, `unpark`, `stackChanged`, `autoExpand`, `holdExpand`, `autoExpandElapsed`, `zoneEnter`, `zoneLeave`, `rowEnter`, `previewToggle`, `graceElapsed`.
- **Semantic CSS Token Mappings (`r.locals`):**
  - Container Root: `.pillRoot`, `.hotZone`, `.stackSlot`, `.pillCard`
  - Sub-views: `.lay` (absolute inset, opacity 0), `.layMini` (48px height), `.layCard` (80px height), `.prereadBody` (322px height)
  - Typography & Badges: `.miniTitle` (14px, weight 500, `#fcfcfb`), `.miniMeta` (12px, weight 400, `#71716e`), `.statusDot` (6×6px, `#10b981`), `.cornerBadge` (20×20px, `#2a2a29`)
  - Split Button: `.splitButton`, `.splitMain`, `.autoStartFill` (`var(--lumen-600)`), `.splitCaret`, `.splitButtonArmed`

---

## 3. Toggle Bar Complete State Comparison Across All 8 States

The table and analysis below compare VOX's current implementation against Wispr Flow's benchmark standard across all 8 meeting lifecycle states:

### 3.1 8-State Comparison Matrix

| State # | State Name | Wispr Flow Behavior & Specs | VOX Current Behavior | Divergence & Flaws in VOX |
| :--- | :--- | :--- | :--- | :--- |
| **State 1** | **No meeting active (idle)** | Resting capsule docked to screen edge. Bottom: 30×6px (`border-radius: 6px; background: rgba(0,0,0,0.5); border: 1px solid hsla(0,0%,100%,0.5)`). Side: 8×40px. Fully translucent, zero visual distraction. | Resting pill is 40×8px (`rgba(0,0,0,0.52)`). Expands on hover to 50×30px with scratchpad button and hover tooltip. | Minor geometry discrepancy (40×8 vs 30×6). In VOX, hover expansion is snappy without spring interpolation. |
| **State 2** | **Upcoming / awaiting meeting** | Pill morphs from 30×6px to **416×48px `layMini`**. Shows platform badge (24×24px), meeting title (with 20px end-fade mask), emerald dot (`#10b981`), and countdown (e.g. "in 5m"). Corner badge shows total meeting count if overlapping. | Pill remains in dictation idle state (40×8px). A separate 440×152px bulky dialog (`calendar_prompt.py`) or toast popup spawns in bottom-right corner. | **Severe architectural flaw:** Disjointed separate popup window. Blocks taskbar/tray and external app notifications. Floating bar remains oblivious to upcoming meetings. |
| **State 3** | **Notifications & joining (Imminent < 2m)** | Auto-expands to **416×80px `layCard`** for 10s (`autoExpand`). Shows Split Join CTA button with animated auto-start progress bar (`autoStartFill`), caret menu ("Record without joining", "Snooze"), and top-left dismiss button (24×24px circle). Collapses back to `mini` if untouched. | Bottom-right popup displays countdown timer and "Record Meeting" button (`meeting_prompt_popup.py`). Bottom-center pill still unchanged. | Cluttered separate window. Missing split Join button, missing auto-start progress bar, missing smooth morphing between mini and card states. |
| **State 4** | **Active meeting** | Pill maintains compact meeting indicator: displays live duration counter, audio activity visualizer / waveform, unobtrusive status dot. Left click opens context-aware notes drawer. | FlowBar appends an asymmetrical button `QPushButton("● 00:00 Notetaker")` (`floating_pill.py:576`) with red border `#FF5757` next to dictation waveform. Also opens heavy drawer or studio. | Asymmetrical visual glitch. Layout jumps horizontally when meeting starts. Hardcoded red border clashes with minimal palette. Drawer opens with 5 tabs upfront. |
| **State 5** | **Ending / disconnecting** | Meeting disconnect triggers subtle commit state: status dot fades, audio handles release cleanly, prompt offers "Meeting completed — View summary". Gracefully collapses to resting 30×6px in 1.9s. | Controller meeting state fires `meeting_state_changed("idle")`. Notetaker chip abruptly vanishes or gets stuck in "Finalizing...". | Potential hang if `MeetingRecorder` thread is blocked. Abrupt removal without transition animation. Audio device handle conflicts on subsequent call. |
| **State 6** | **App / window switching** | Window maintains `WS_EX_NOACTIVATE | WS_EX_TOPMOST`. Never steals cursor or document focus. Smoothly retains screen position. Background opacity slightly dims if inactive window hovers. | Sets `WA_ShowWithoutActivating` and `apply_non_activating_floating_style`. Updates tooltip to "Dictate into {app}". | Window z-order conflicts between `VoxFlowBar`, `MeetingPromptPopup`, and `LiveSpeechPreviewBubble`. Multi-monitor repositioning can jitter. |
| **State 7** | **Back-to-back meetings** | Pill state machine tracks meeting stack (`count > 1`). Displays circular count badge (`cornerBadge: 20×20px`). On hover, enters `level=fan` showing stacked cards. Concluding Meeting A instantly shifts focus to Meeting B without restart. | VOX has NO stack concept. Previous meeting session locks recorder. Starting Meeting B fails or appends to Meeting A transcript, causing duplicate sessions or crashes. | **Critical bug:** Inability to handle back-to-back meetings. UI has zero support for stacked meeting representations. |
| **State 8** | **Failed detection / permissions / recovery** | Pill morphs to error/recovery mode (border `#FF5757`), inline status text ("No mic permission", "Audio blocked"), and split caret offering "Run as Admin" or "Select Microphone". | Pill sets `PillState.ERROR` (`floating_pill.py:811`) with text truncated to 14 chars ("No audio" or "Error"). | Error messages are uninformative, lack inline recovery actions, and auto-reset after 2.2s without helping user resolve permission/device issue. |

---

## 4. Deep Technical Analysis: Mini-Toggle & Expanded Card Specifications

From the extracted Wispr Flow CSS bundle (`wispr_pill_semantic_css.txt`), the exact layout, dimensions, typography, and transition mechanics are detailed below:

```
+-----------------------------------------------------------------------------------------------+
|  Wispr Flow Polymorphic Pill Container: var(--pill-zone-w, 428px)                             |
|                                                                                               |
|  [State: rest]      30px x 6px capsule (rgba(0,0,0,0.5), border hsla(0,0%,100%,0.5))          |
|                                                                                               |
|  [State: mini]      416px x 48px, border-radius: 16px, background: #1F1F1F, border: 1.5px    |
|  +-----------------------------------------------------------------------------------------+  |
|  | [Icon 24x24]  Product Architecture Sync (fade mask)  [Dot 6x6 #10b981] in 2m  [Badge 2] |  |
|  +-----------------------------------------------------------------------------------------+  |
|                                                                                               |
|  [State: card]      416px x 80px, border-radius: 16px, background: #1F1F1F, border: 1.5px    |
|  +-----------------------------------------------------------------------------------------+  |
|  | [Icon 24x24]  Product Architecture Sync                       [ Split Join Button ]     |  |
|  |               Organizer * 4 participants                      [ Join & Record | v ]     |  |
|  +-----------------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------------------+
```

### 4.1 Detailed CSS/Qt Property Mapping

1. **Root Pill Container (`pillRoot` / `pillCard`):**
   - **Resting:** `width: 30px; height: 6px; border-radius: 6px; background-color: rgba(0, 0, 0, 0.52); border: 1px solid rgba(255, 255, 255, 0.45);`
   - **Mini-Toggle:** `width: 416px; height: 48px; border-radius: 16px; background-color: #1F1F1F; border: 1.5px solid #30302F; box-shadow: 0 8px 32px rgba(0,0,0,0.38);`
   - **Expanded Card:** `width: 416px; height: 80px; border-radius: 16px; background-color: #1F1F1F; border: 1.5px solid #30302F;`
   - **Deep Preview (L4):** `width: 416px; height: 322px; border-radius: 16px;`

2. **Mini-Toggle Content Layout (`layMini`):**
   - Layout: Horizontal box (`QHBoxLayout`), `padding: 0px 12px; gap: 8px; align: Center`.
   - **Platform Badge Slot (`platformBadgeSlot`):** 24×24px fixed size. Renders SVG platform badge:
     - Google Meet: Multi-color video glyph (`#4285F4`, `#34A853`, `#FBBC05`, `#EA4335`) or monochrome `#DEDDD7`.
     - Zoom: Blue video camera glyph (`#2D8CFF`).
     - Microsoft Teams: Purple glyph (`#6264A7`).
     - Slack Huddle: Teal/multicolor headphone glyph (`#4A154B`).
   - **Title Label (`miniTitle`):** `font-size: 14px; font-weight: 500; color: #FCFCFB; line-height: 20px;`
     - Fade Mask: `mask-image: linear-gradient(to right, #000 calc(100% - 20px), transparent);`
   - **Metadata Stack (`miniMeta`):** `display: inline-flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 400; color: #71716E;`
   - **Status Dot (`statusDot`):** `width: 6px; height: 6px; border-radius: 2px; background-color: #10B981;`
   - **Corner Badge (`cornerBadge`):** `min-width: 20px; height: 20px; border-radius: 10px; background-color: #2A2A29; border: 1px solid #343432; color: #DEDDD7; font-size: 11px; font-weight: 600; padding: 0 5px;` (positioned at `top: -10px; right: -6px;`).

3. **Split Action Button (`splitButton`):**
   - Background: `var(--lumen-400)` / Sand-Light Gold (`#E8DFC8` or `#F0D7FF` in Dawn mode).
   - Main CTA: "Join & Record" with meeting platform icon.
   - Auto-Start Progress Fill: Dynamic underlay width proportional to remaining countdown time: `calc(100% * (1.0 - progress))`.
   - Caret Chevron: 18×18px dropdown toggle invoking context menu.

4. **Animation & Transition Timing:**
   - Pill Morph Duration: `420ms` using cubic-bezier easing `(0.16, 1.0, 0.3, 1.0)` (simulated in PyQt6 via `QVariantAnimation` with `QEasingCurve.Type.OutCubic` or `OutBack`).
   - Content Fade Out: `80ms ease`.
   - Content Fade In: `180ms ease` with a `100ms delay` to allow width/height expansion before content appears.

---

## 5. Comprehensive VOX App-Wide UX Audit (Requirement 4)

Beyond the toggle bar, an end-to-end UX audit of the VOX application was conducted against Wispr Flow workflows.

### 5.1 Navigation & Global Desktop Shell (`main_window.py`)
- **Strengths:** VOX implements a structured navigation sidebar with keyboard accelerators (`Ctrl+1` through `Ctrl+7`, `Ctrl+,`) and a functional global search palette (`Ctrl+K`).
- **Gaps & Friction Points:**
  1. *Visual Weight of the Sidebar:* The VOX sidebar buttons are tall and heavy with high-contrast text, unlike Wispr Flow's lightweight 36px icon + text tabs with subtle 4px active indicators.
  2. *Status Indicator:* Window header lacks an ambient sync/status pill indicating engine readiness, Whisper model load state, or active audio device.
  3. *Window Management:* When closed via the title bar `X`, VOX hides to system tray, but lacks a non-intrusive first-run toast explaining that the application is running in the background.

### 5.2 Onboarding Experience (`onboarding_wizard.py`)
- **Strengths:** 5-step wizard covering Welcome, Microphone test, Hotkey selection, Model setup, and First Dictation test.
- **Gaps & Friction Points:**
  1. *Static Dialog Form Factor:* Rendered as a standard modal dialog (`QDialog`, fixed 560×440px) with generic gray wizard steps, creating an "installer" feel rather than a modern, interactive product tour.
  2. *Wispr Flow Parity Gap:* Wispr Flow utilizes a transparent overlay tour (`feature_tour`) that guides the user directly on their live desktop, teaching them how to hold `Ctrl+Win`, watch the pill expand, dictate into Notepad, and see the formatted output.
  3. *Audio Test Feedback:* Microphone check in VOX lacks live audio bar deflection; user has to click "Test" and wait rather than seeing real-time input meter reactivity.

### 5.3 Notifications & Alerts (`calendar_prompt.py`, `meeting_prompt_popup.py`)
- **Strengths:** Solid Win32 API usage (`WS_EX_NOACTIVATE`, `SetWindowDisplayAffinity` for privacy exclusion during screen share).
- **Gaps & Friction Points:**
  1. *Duplicate Notification Windows:* Two completely separate implementations exist (`VoxMeetingPromptPopup` in `calendar_prompt.py` and `MeetingPromptPopup` in `meeting_prompt_popup.py`).
  2. *Screen Real Estate Consumption:* Spawning a 440×152px card in the bottom-right corner creates visual clutter, occluding native Windows toast notifications (Teams, Slack, Windows Action Center).
  3. *Architectural Misalignment:* As proved by Wispr Flow, meeting notifications belong **inside the polymorphic toggle bar / pill**, collapsing to a 48px mini-toggle rather than maintaining an invasive floating card.

### 5.4 Meeting History & Notetaker Workspace (`notetaker_page.py`, `notetaker_drawer.py`, `meeting_studio.py`)
- **Strengths:** Rich backend storage (`meetings`, `transcripts`, `diarization`, `action_items`). Good integration with SQLite and export options (TXT, Markdown, SRT).
- **Gaps & Friction Points:**
  1. *Live Meeting Cognitive Overload:* During live calls, `VoxNotetakerDrawer` displays 5 tabs upfront ("My Notes", "Transcript", "Summary", "Tasks", "Ask AI") with search inputs. Wispr Flow deliberately hides the Transcript and Summary tabs during active recording, presenting only a clean, serene notes field with a duration clock and stop button.
  2. *Empty State Polish:* In `notetaker_page.py`, when no meetings exist, the UI shows a plain text label `"No meetings recorded yet."` without helpful contextual illustrations, quick-start guides, or sample meeting demonstrations.
  3. *Speaker Attribution Editing:* Editing speaker names requires multiple clicks through a dialog instead of inline click-to-rename functionality on the speaker badge.

### 5.5 Settings & Preferences (`settings_dialog.py`)
- **Strengths:** Comprehensive settings coverage across General, Audio, Dictation, Notetaker, Shortcuts, Model/Compute, and MCP.
- **Gaps & Friction Points:**
  1. *Dense Layout:* Settings pages have inconsistent padding (ranging from 10px to 22px), leading to uneven visual rhythm.
  2. *Audio Device Configuration:* Selecting an audio input device does not show a live input level meter in the settings view, forcing the user to test blindly.
  3. *Whisper Model Selection:* Displays raw compute options (`float16`, `int8`, `int8_float16`) which intimidate non-technical users. Should use user-friendly labels (e.g. "Fastest (Lower Memory)", "Balanced", "Maximum Accuracy").

### 5.6 Shortcuts & Accessibility (`command_palette.py`, `SHORTCUTS.md`)
- **Strengths:** Universal `Ctrl+K` command palette with search filtering and direct action triggers.
- **Gaps & Friction Points:**
  1. *Keyboard Navigation within Floating Overlays:* The floating bar and prompt popups do not support full keyboard navigation (Tab/Shift+Tab focus traversal, Escape to dismiss, Space/Enter to trigger actions).
  2. *Screen Reader Support:* Missing `setAccessibleName()` and `setAccessibleDescription()` across custom widgets (`DynamicWaveformCapsule`, `_RoundPillButton`, `MeetingQuickStartDialog`), failing basic accessibility audits.
  3. *High Contrast & Text Scaling:* Font sizes in the floating bar are hardcoded in pixels rather than scaling with Windows DPI / accessibility scaling settings.

---

## 6. Concrete Recommendations & Refactoring Roadmap

To eliminate visual clutter, resolve the 8-state transition lag, and achieve Wispr Flow parity, the following technical refactorings are recommended for the implementation team:

### Recommendation 1: Unify `VoxFlowBar` into a Polymorphic 8-State Pill
- **Target File:** `src/voxlocal/ui/floating_pill.py`
- **Action:** Expand `PillState` and `VoxFlowBar` to absorb all meeting notification and active states directly.
  - Add states: `PillState.RESTING` (30×6px), `PillState.MINI_MEETING` (416×48px), `PillState.CARD_NOTIFICATION` (416×80px), `PillState.ACTIVE_RECORDING` (120×36px), `PillState.L4_PREVIEW` (416×322px).
  - Implement `layMini` inside `VoxFlowBar` with platform badge, masked title, status dot, and countdown.
  - Implement `layCard` inside `VoxFlowBar` with split Join button and auto-start underlay fill.
  - Retire `meeting_chip` (remove line 576 button append) in favor of the integrated layout.
  - Add smooth spring geometry animations via `QVariantAnimation` (420ms cubic easing) replacing instant `setFixedSize()` calls.

### Recommendation 2: Deprecate Separate Floating Popups in Favor of the Pill
- **Target Files:** `src/voxlocal/ui/meeting_prompt_popup.py`, `src/voxlocal/ui/calendar_prompt.py`, `src/voxlocal/core/controller.py`
- **Action:** Route all call detection events (`on_call_detected`) and calendar reminders through `controller.flow_bar.show_meeting_reminder(session_info)`.
  - When a call is detected, the flow bar morphs to `PillState.CARD_NOTIFICATION` (auto-expanded for 10s), then gracefully collapses to `PillState.MINI_MEETING`.
  - Eliminate the duplicate bottom-right window `MeetingPromptPopup`, instantly removing screen occlusion.

### Recommendation 3: Streamline Active Meeting Notetaker (Requirement 2 Parity)
- **Target File:** `src/voxlocal/ui/notetaker_drawer.py`
- **Action:**
  - During live meetings (`_meeting_active == True`), hide the "Transcript", "Summary", "Tasks", and "Ask AI" tabs by default.
  - Present a serene, focused **"My Notes"** editor with session duration and minimal recording controls.
  - When the user stops recording (or clicks an explicit "View Live Transcript" link), reveal the full multi-tab interface.

### Recommendation 4: Architect Robust Back-to-Back Meeting Stack Machine (Requirement 3 Parity)
- **Target Files:** `src/voxlocal/core/controller.py`, `src/voxlocal/audio/call_detector.py`, `src/voxlocal/audio/meeting_recorder.py`
- **Action:**
  - Introduce `MeetingStack` data structure in `AppController` managing concurrent or back-to-back sessions.
  - Render `cornerBadge` on the mini-toggle when `len(stack) > 1`.
  - In `finalize_meeting_recording()`, implement guaranteed, non-blocking cleanup of WASAPI loopback streams, process handles, and temporary files before allowing a new session initialization.
  - Provide an independent session ID generator that resets all state machine hooks on transition.

### Recommendation 5: Polish App-wide Design Tokens & Accessibility (Requirement 4 Parity)
- **Target Files:** `src/voxlocal/ui/theme.py`, `src/voxlocal/ui/main_window.py`, `src/voxlocal/ui/settings_dialog.py`
- **Action:**
  - Replace all hardcoded CSS hex codes with central semantic tokens (`surface_pill`, `border_pill`, `text_pill_primary`, `text_pill_muted`, `accent_emerald`).
  - Add `setAccessibleName` and `setAccessibleDescription` to all interactive buttons, waveforms, and chips.
  - Standardize container padding: 16px for cards, 12px for toolbars, 8px for internal button groups.
  - Create rich empty-state illustration widgets with 1-click action triggers for History and Snippets.

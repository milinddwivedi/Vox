# Comprehensive Technical Survey & Architecture Report: Live Notetaker & Floating Bar Interface Streamlining (Requirement 2)

**Author**: Explorer 2 (Live Notetaker & Floating Bar Interface Specialist)  
**Date**: November 12, 2026  
**Target Application**: VOX (`src/voxlocal`) vs Wispr Flow (1.6.1457)  
**Reference Video Artifacts**:  
- `D:\Milind\Voice Assistant\External References\Notetaker comparision.mp4` (177.57s, 30fps, 1918x1078)  
- `D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4` (64.67s, 30fps, 1918x1078)  
- Installed Wispr Flow Electron Application (`C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar`)  

---

## 1. Executive Summary

A comprehensive, frame-by-frame and source-code reverse-engineering investigation was conducted comparing the live call notetaker and floating bar interfaces of VOX against the industry-standard Wispr Flow (v1.6.1457). 

### Key Findings:
1. **Cluttered Upfront Tab Model in VOX**:  
   During live meetings, VOX's companion drawer (`src/voxlocal/ui/notetaker_drawer.py`) currently forces **5 static tabs upfront**: `["My Notes", "Transcript", "Summary", "Tasks", "Ask AI"]`. Both the `Summary` and `Tasks` tabs are completely empty while a meeting is underway, creating confusion and cognitive load. Furthermore, `Ask AI` is isolated into an independent tab, requiring users to navigate away from their live notes or transcript to ask questions.
2. **Wispr Flow's Context-Aware Minimal Paradigm**:  
   Reverse-engineering Wispr Flow's renderer bundle (`.webpack/renderer/meeting_recorder/index.js`) reveals an elegant, conditional tab orchestration function (`YS`). During an active meeting, Wispr Flow strictly presents **3 tabs**:
   - `My thoughts` (Personal private scratchpad — active default, encouraging note-taking)
   - `ılı Transcript` (Live streaming transcript featuring a 3-bar animated live audio visualizer)
   - `+ Summary` (Decorative plus/sparkle icon indicating post-meeting availability; clicking it explains: *"Summaries appear when a meeting ends / No summary yet"*)
   - `Tasks` and `Docs` are dynamically conditionally appended (`...vn ? [Tasks] : []`) **only after** meeting completion when AI action items exist.
   - `Ask AI` is **not a tab**, but a permanent, fluid bottom interaction drawer (`[ Ask anything, or type / for Skills ]` with a quick `[ What did I miss? ]` chip) accessible across all views.
3. **Window Geometry & Floating Bar Clutter**:  
   - VOX opens an arbitrary floating window (`500x420` expanding to `920x660`) in an unpredictable location that frequently covers Zoom/Teams participant videos and collides with its own floating FlowBar chip.
   - In contrast, Wispr Flow docks its Notetaker window flush against the right screen edge at full work-area height (`x = workArea.x + workArea.width - width, y = workArea.y, height = workArea.height`).
   - When collapsed, Wispr Flow maintains an ultra-compact edge-docked capsule (`● 08:20 Notetaker`) featuring subtle hover controls (Mute, Pause, Bookmark, Stop) and zero window collision.

---

## 2. Reference Material & Technical Evidence Base

### 2.1 Video Analysis: `Notetaker comparision.mp4` (0:00 – 2:57)
- **0:00 – 0:30 (Meeting In-Progress)**:  
  - Left screen displays `VOX — Notetaker` with tabs `My Notes | Transcript | Summary | Tasks | Ask AI` and heavy audio telemetry badges (`Mic: Live (0%)`, `Call Audio: Live (64%)`, `Call-Only Privacy Guard`).  
  - Center shows Zoom meeting (`Milind Krishna Dwivedi` participant tile, shared spreadsheet).  
  - Right screen displays Wispr Flow Notetaker (`STRATA x WFX - Implementation & Planning`) snapped to the right edge with clean serif title, attendee avatar chips (`M A B D +7 Today`), and streamlined tabs: `My thoughts | ılı Transcript | + Summary`.  
  - Bottom left of center screen shows VOX's floating capsule: `● 04:18 Notetaker`.  
  - Right screen edge displays Wispr Flow's docked vertical pill.
- **1:30 – 1:35 (User clicks "My Notes")**:  
  - In VOX, clicking `My Notes` displays empty editor with placeholder `"Jot down your private thoughts during the meeting..."`.
  - In Wispr Flow, `My thoughts` tab has placeholder: `"Write your notes here. We'll make a separate summary at the end."`
- **2:30 – 2:45 (User clicks "Summary" and "Tasks")**:  
  - In VOX, clicking `Summary` shows a blank disabled box `"Meeting summary will appear automatically once finalized."`  
  - In VOX, clicking `Tasks` shows a completely blank white box with no visual explanation.  
  - In Wispr Flow, clicking `+ Summary` displays an elegant, centered placeholder:  
    `"Summaries appear when a meeting ends"`  
    `"No summary yet"`.  
  - Wispr Flow does not even render a `Tasks` tab until tasks exist!

### 2.2 Video Analysis: `Toggle Bar Comparision.mp4` (0:00 – 1:04)
- **0:16 – 0:30 (Active Call Docking)**:  
  - Frame 016s: Wispr Flow displays an edge-docked pill on the right screen border (`● 08:08 Notetaker`) with a left chevron `<` to expand the docked notetaker panel.
  - Frame 048s: Direct zoomed comparison callouts:  
    - VOX: Clumsy circular floating bubble with high-contrast pause bars floating on top of active windows.  
    - Wispr Flow: Sleek obsidian capsule docked flush to the screen edge with red recording pulse `● 08:08 Notetaker`.
  - Frame 060s: VOX docked on left screen border displays a fragmented vertical strip with an awkward horizontal popup banner `"Upcoming meetings"` and an overlapping round widget colliding with the Notetaker Drawer window behind it.

### 2.3 Reverse-Engineered Architecture: Wispr Flow 1.6.1457
Inspection of `.webpack/main/index.js` and `.webpack/renderer/meeting_recorder/index.js` uncovered the exact implementation rules:
1. **Window Sizing & Snapping Logic**:
   ```javascript
   // Wispr Flow main process window bounds calculation
   const _ = () => w(screen.getPrimaryDisplay().workArea).recorder;
   // Where w(...) computes:
   recorder: {
     x: workArea.x + workArea.width - recorderWidth,
     y: workArea.y,
     width: Math.min(workArea.width, Math.max(minWidth, Math.round(0.5 * workArea.width))),
     height: workArea.height // Full height of display work area!
   }
   ```
2. **Tab Availability Rules (`Ja` in renderer)**:
   ```javascript
   const Ja = [
     // 1. My Thoughts (active scratchpad)
     ...hasNotes ? [{ key: 'notes', label: 'My thoughts' }] : [],
     // 2. Transcript with live 3-bar animated audio wave
     { key: 'transcript', label: 'Transcript', leading: <Waveform barCount={3} animated={isRecording} /> },
     // 3. Summary with sparkle/plus icon
     { key: 'summary', label: 'Summary', leading: <SparkleIcon size={11} /> },
     // 4. Tasks (ONLY rendered if post-processing completed and tasks exist)
     ...tasksAvailable ? [{ key: 'tasks', label: 'Tasks', badge: `(${completed}/${total})` }] : [],
     // 5. Docs (ONLY rendered if attachments exist)
     ...docCount > 0 ? [{ key: 'docs', label: 'Docs', badge: `(${docCount})` }] : []
   ];
   ```
3. **Tab Selection Controller (`YS`)**:
   ```javascript
   // If meeting is active and user has not explicitly clicked Summary,
   // it ALWAYS defaults to 'notes' (My thoughts) or 'transcript', NEVER Summary!
   const YS = ({ requestedTab, visibleTabSet, userPicked, meetingHasEnded, hasEndedBefore }) => {
     if (visibleTabSet.has(requestedTab)) {
       if (userPicked || hasEndedBefore || requestedTab !== 'summary' || meetingHasEnded || !visibleTabSet.has('notes')) {
         return requestedTab;
       }
       return 'notes';
     }
     return visibleTabSet.has('notes') ? 'notes' : 'summary';
   };
   ```
4. **Automatic Post-Meeting Tab Switch**:
   ```javascript
   // When meeting ends and was recording, automatically switch view to Summary
   useEffect(() => {
     if (meetingHasEnded && wasRecording && summaryExpected && !userOverrode) {
       setRequestedTab('summary');
     }
   }, [meetingHasEnded, wasRecording, summaryExpected]);
   ```

---

## 3. Full Notetaker Lifecycle Audit (Stages 1 – 6)

| Lifecycle Stage | Current VOX Implementation (`src/voxlocal`) | Observed Issues & Clutter in VOX | Wispr Flow Standard (Target State) | Recommended Refactoring in VOX |
| :--- | :--- | :--- | :--- | :--- |
| **Stage 1: Before Meeting** *(Docked / Hidden / Awaiting)* | `CalendarManager` polls every 60s. `VoxFlowBar` (`floating_pill.py`) docks to screen edge. `NotetakerPage` shows upcoming calendar strip. | When docked on left/right edges, FlowBar renders fragmented geometry. Protruding `"Upcoming meetings"` popup card obstructs workspace. | Sleek edge pill resting flush (40x8 bottom / 8x40 side). Clean hover tooltip with upcoming meeting time without layout shifts. | Refactor `VoxFlowBar` side-dock geometry in `floating_pill.py`. Polish upcoming calendar chip to a subtle badge inside the capsule. |
| **Stage 2: Detection / Activation** *(Meeting Launch)* | `CallDetector` detects audio session / HWND. `MeetingPromptPopup` or `CalendarPrompt` triggers. User clicks "Start Notetaker". `controller.start_meeting_recording()` runs. | Popup dialogs steal focus or hide behind full-screen video apps. Starts a floating 500x420 window in arbitrary position + separate floating pill chip. | Non-intrusive notification bubble docked near FlowBar. 1-click capture. Drawer slides smoothly out snapped to screen edge. | Ensure non-activating popup style (`WS_EX_NOACTIVATE`). Snap `VoxNotetakerDrawer` to display work area edge on launch. |
| **Stage 3: Live Meeting** *(Active Recording & Transcription)* | `VoxNotetakerDrawer` displays 5 static tabs (`My Notes`, `Transcript`, `Summary`, `Tasks`, `Ask AI`). Progressive chunking into `QListWidget`. Separate `meeting_chip` on FlowBar. | Cluttered tabs: `Summary` and `Tasks` are empty/useless. Search input occupies vertical space. Header has 6 buttons/badges. Dual floating objects collide. | Streamlined 2+1 tabs (`My thoughts`, `ılı Transcript`, `+ Summary`). `Tasks` hidden. Bottom `Ask AI` input + `What did I miss?`. Docked edge pill with hover controls. | Streamline tabs in `notetaker_drawer.py`. Hide `Tasks`. Embed bottom AI prompt bar. Add hover controls (Mute, Pause, Bookmark, Stop) to `meeting_chip`. |
| **Stage 4: Ending / Processing** *(Stop & Finalization)* | User clicks `Stop`. `_meeting_state = "transcribing"`. `_finalize_meeting_worker` executes 4 stages (video mux, remaining audio, diarization, local LLM). | Only indicator is `btn_stop` showing `"Finalizing..."`. No granular progress. Floating pill chip disappears or resets abruptly. | Visual processing state with shimmering progress indicator and subtle stage status (e.g., *"Aligning speakers & synthesizing summary..."*). | Add smooth indeterminate progress bar / step indicator to `VoxNotetakerDrawer` header during `transcribing` state. |
| **Stage 5: Summaries / Actions** *(Post-Meeting Review)* | `meeting_saved_and_transcribed` fires. `_on_meeting_finalized()` reloads meeting data and sets controls to `idle`. | Does NOT switch to `Summary` tab automatically! Renders summary in unstyled plain `QTextEdit`. `Tasks` tab stays an unformatted list. | Automatically transitions active tab to `Summary`. Renders rich formatted MoM, key decisions, and dynamically reveals `Tasks` tab with count badge `(2/5)`. | Connect `meeting_saved_and_transcribed` in `VoxNotetakerDrawer` to automatically select `Summary` tab and dynamically reveal `Tasks` tab. |
| **Stage 6: Errors / Recovery** *(Failures & Edge Cases)* | On error, controller sets state to `error` and emits signal. `_sync_controls_state()` sets dot to grey and button to `"Start Notetaker"`. | No clear error banner or retry action in drawer. If background threads fail or crash, UI can freeze in `"Finalizing..."` or orphan audio handles. | Clear inline non-blocking toast/banner with actionable retry button (e.g., *"Audio device disconnected. Reconnect to resume."*). | Add error banner widget in `VoxNotetakerDrawer` with retry/cancel actions. Ensure audio handle cleanup on all failure paths. |

---

## 4. Context-Aware Live Notetaker Streamlining (Deep Dive)

### 4.1 The Problem with Upfront Tabs During Live Meetings
In the current VOX implementation (`src/voxlocal/ui/notetaker_drawer.py`, lines 108–185):
```python
# CURRENT VOX IMPLEMENTATION
self._tab_buttons: list[QPushButton] = []
for idx, label in enumerate(["My Notes", "Transcript", "Summary", "Tasks", "Ask AI"]):
    b = QPushButton(label)
    b.setObjectName("pillTabBtn")
    ...
```
This design has major flaws:
1. **Premature Content Expectation**: Displaying `Summary` and `Tasks` while a call is in progress prompts users to click them, only to encounter empty placeholders.
2. **Context Fragmentation**: `Ask AI` being a dedicated tab means the user cannot reference the transcript or their private notes while chatting with VOX AI.
3. **Screen Real Estate Waste**: Five pill buttons plus the `Full View` button crowd the top navigation bar, forcing narrow tab labels and visual clutter.

### 4.2 Streamlined Context-Aware Model (Wispr Flow Standard)
During an active meeting, the notetaker should adapt dynamically:

```
+-------------------------------------------------------------------------+
| < Back     Meeting Notes — Standup Sync             ...  Share  _  □  ✕ |
+-------------------------------------------------------------------------+
| [ My thoughts ]    [ ılı Transcript ]    [ + Summary ]                  |
|                                                                         |
| (Active Content: Private Thoughts Editor or Live Transcript Stream)     |
|                                                                         |
|                                                                         |
|                                                                         |
+-------------------------------------------------------------------------+
| ■ Stop 04:18 | [ Ask anything, or type / for Skills... ] | [What did I miss?]|
|             Always get consent when transcribing others. Learn more     |
+-------------------------------------------------------------------------+
```

#### Exact Rules for Surfacing Tabs:
1. **Live State (`recording` or `paused`)**:
   - **Tab 1: `My thoughts` (Default)**: Personal private scratchpad for immediate note-taking. Notes auto-save with a 500ms debounce to `meetings.notes`. Placeholder: *"Write your notes here. We'll make a separate summary at the end."*
   - **Tab 2: `ılı Transcript`**: Live streaming diarized transcript. Tab button contains an active 3-bar animated sound wave icon (`barCount: 3`). Top subtle chip: *"After the meeting, VOX will refine the transcript & label speakers"*.
   - **Tab 3: `+ Summary`**: Styled with a plus/sparkle icon. Clicking during an active meeting shows a clean empty state:
     - Heading: *"Summaries appear when a meeting ends"*
     - Subtext: *"No summary yet. Focus on your conversation — VOX local AI will synthesize action items, decisions, and executive notes upon stop."*
   - **`Tasks` Tab**: **HIDDEN** during live meeting.
   - **`Docs` Tab**: **HIDDEN** unless files are attached.
2. **Post-Meeting State (`completed` / `idle`)**:
   - **Automatic Transition**: Upon `meeting_saved_and_transcribed`, the UI **automatically switches active tab to `Summary`**.
   - **Reveal `Tasks` Tab**: If action items were detected by `LocalMeetingSummarizer`, dynamically insert `Tasks` tab with count badge: `Tasks (3)`.
   - **Tab Order Post-Meeting**: `[ Summary ]  [ My thoughts ]  [ Transcript ]  [ Tasks (3) ]`.

### 4.3 Integrated Bottom Action Bar (Replacing `Ask AI` Tab)
Instead of a separate tab, integrate an always-present bottom bar in `VoxNotetakerDrawer`:
- **Stop Button**: Left-aligned high-contrast button (`■ Stop [MM:SS]`).
- **Prompt Field**: Seamless input box with placeholder `Ask anything about this meeting...`. Hitting Enter calls `controller.query_meeting_memory(query, meeting_id)`.
- **Quick Action Pill**: `[ What did I miss? ]` button. Clicking sends the pre-built prompt *"Summarize the last 5 minutes of discussion and key takeaways"* to the local LLM and displays the answer in a slide-up ephemeral drawer or toast.

---

## 5. Primary Floating Notetaker Bar Audit & Refinement

### 5.1 Current VOX Deficiencies vs Wispr Flow Standard
In `src/voxlocal/ui/floating_pill.py` (lines 575–585, 823–827, 940–944):
- The meeting chip is currently implemented as a simple secondary `QPushButton` appended to `VoxFlowBar`:
  `self.meeting_chip = QPushButton("● 00:00 Notetaker", self)`
- **Issues Direct from Video Comparison**:
  1. **Visual Footprint**: When docked to side edges, the pill layout becomes vertically distorted, causing text truncation or awkward stretching.
  2. **Lack of Live Feedback**: The chip displays static text without audio responsiveness. It lacks the pulsing dot and animated waveform bars present in Wispr Flow.
  3. **No Unobtrusive Controls**: Users cannot pause, mute, or bookmark a moment directly from the floating bar. They are forced to open the full drawer window.
  4. **Window Collisions**: In VOX, when the Notetaker Drawer is open, the floating bar chip remains visible on screen, frequently overlapping the drawer itself or creating duplicate timers on the desktop.

### 5.2 Floating Notetaker Bar Specification & Target Architecture

#### 1. Resting Capsule State (Unhovered):
- **Dimensions**: `114px x 28px` (height matches FlowBar 28-30px standard).
- **Background**: `#000000` (obsidian black) with `1px solid #2B2A27` border and `14px` border-radius.
- **Components**:
  - Live Pulsing Dot: `#FF5757` (red) pulsing at 1.2Hz during active recording; `#FFA946` (amber) when paused.
  - Timer: `MM:SS` in `Consolas` / `Segoe UI Variable Text` monospace (`11px`, `600` weight, `#FCFCFB`).
  - Audio Waveform: 4-bar mini live amplitude visualizer reacting to `meeting_tick` levels.
  - Label: `Notetaker` (`11px`, `500` weight, `#DEDDD7`).

#### 2. Hovered Expanded State:
When hovered for >150ms without clicking, the capsule smoothly expands horizontally (`210px x 28px`) to expose four quick action icon buttons:
1. `btn_mute`: Microphone toggle (Mute/Unmute mic track).
2. `btn_pause`: Pause / Resume call capture.
3. `btn_bookmark`: Instant timestamp bookmark (`controller.add_meeting_bookmark(meeting_id, timestamp)`).
4. `btn_stop`: Stop & Save recording (red square icon).
5. Clicking anywhere outside the buttons expands/focuses the Notetaker Drawer.

#### 3. Edge Docking & Mutual Exclusivity:
- When the Notetaker Drawer is snapped open on the right screen edge, the floating bar on that edge should smoothly dock behind or fade to an edge tab, eliminating visual duplication.
- Strict Win32 flags: Maintain `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW` to ensure hovering and clicking controls never steals keyboard focus from Zoom, Teams, PowerPoint, or Word.

---

## 6. Technical Refactoring Architecture & Implementation Blueprint

### 6.1 Target File Modifications

| Target File Path | Component / Area | Required Refactoring |
| :--- | :--- | :--- |
| `src/voxlocal/ui/notetaker_drawer.py` | `VoxNotetakerDrawer` | 1. Replace 5 static tabs with context-aware 2+1 tabs (`My thoughts`, `Transcript`, `+ Summary`).<br>2. Conditionally append `Tasks` tab only when action items exist.<br>3. Remove dedicated `Ask AI` tab; implement persistent bottom action bar with `Ask anything` field and `[ What did I miss? ]` chip.<br>4. Implement right-edge display docking on startup (`workArea.height`).<br>5. Connect `meeting_saved_and_transcribed` to auto-switch to `Summary`. |
| `src/voxlocal/ui/floating_pill.py` | `DualTierFloatingPill` / `VoxFlowBar` | 1. Replace `self.meeting_chip` with `FloatingNotetakerCapsule` supporting 4-bar mini waveform, live pulse, and monospace timer.<br>2. Add hover expand tray exposing Mute, Pause, Bookmark, and Stop quick actions.<br>3. Fix side-docking vertical geometry and eliminate awkward `"Upcoming meetings"` card protrusion.<br>4. Coordinate mutual visibility with `VoxNotetakerDrawer`. |
| `src/voxlocal/ui/floating_widget.py` | `FloatingWidget` / `FloatingInfoBubble` | Ensure backward compatibility and clean event forwarding to the enhanced FlowBar. |
| `src/voxlocal/ui/theme.py` | Stylesheets & Tokens | 1. Add `.notetakerDock` and `.notetakerBottomBar` styles.<br>2. Refine `pillTabBtn` to use subtle underline/minimal indicator matching Wispr Flow instead of chunky boxed borders.<br>3. Add styles for waveform bars and hover control buttons. |
| `src/voxlocal/core/controller.py` | Meeting Signals | Ensure `meeting_saved_and_transcribed`, `meeting_tick`, and `open_notetaker_drawer_requested` pass required metadata cleanly to UI components. |

### 6.2 Proposed CSS/QSS Styling Rules (`src/voxlocal/ui/theme.py`)

```css
/* Clean Wispr Flow-style Notetaker Tabs */
QPushButton.notetakerTab {
    background: transparent;
    color: #8C887B;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton.notetakerTab:hover {
    color: #FCFCFB;
}
QPushButton.notetakerTab[active="true"] {
    color: #FCFCFB;
    border-bottom: 2px solid #F0D7FF; /* Prismatic Dawn accent */
    font-weight: 600;
}

/* Bottom Persistent Ask AI Action Bar */
QFrame#notetakerBottomBar {
    background-color: #141414;
    border-top: 1px solid #262626;
    padding: 8px 12px;
    border-bottom-left-radius: 12px;
    border-bottom-right-radius: 12px;
}
QLineEdit#notetakerPromptInput {
    background-color: #1F1F1F;
    color: #FCFCFB;
    border: 1px solid #333333;
    border-radius: 16px;
    padding: 6px 12px;
    font-size: 12px;
}
QLineEdit#notetakerPromptInput:focus {
    border: 1px solid #F0D7FF;
}
QPushButton#whatDidIMissBtn {
    background-color: #262626;
    color: #DEDDD7;
    border: 1px solid #3D3D3D;
    border-radius: 14px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#whatDidIMissBtn:hover {
    background-color: #363636;
    color: #FCFCFB;
}

/* Floating Notetaker Capsule Hover Tray */
QFrame#floatingNotetakerCapsule {
    background-color: #000000;
    border: 1px solid #2B2A27;
    border-radius: 15px;
}
QPushButton.notetakerHoverBtn {
    background: transparent;
    border: none;
    border-radius: 11px;
    width: 22px;
    height: 22px;
    color: #DEDDD7;
}
QPushButton.notetakerHoverBtn:hover {
    background-color: #262626;
    color: #FCFCFB;
}
QPushButton.notetakerHoverBtnStop:hover {
    background-color: #4D1A1A;
    color: #FF5757;
}
```

---

## 7. Next Steps for Implementation Team

1. **Phase 1: Notetaker Drawer Streamlining (`notetaker_drawer.py`)**:
   - Refactor tab layout to 2+1 context-aware structure (`My thoughts`, `Transcript`, `+ Summary`).
   - Implement bottom persistent input bar with `Ask anything` and `What did I miss?`.
   - Wire `meeting_saved_and_transcribed` to auto-switch to `Summary` and reveal `Tasks`.
2. **Phase 2: Display Snapping & Geometry Refinement**:
   - Add display work area edge snapping (`dock_to_display_edge()`) for a distraction-free side-panel experience.
3. **Phase 3: Floating Notetaker Bar Hover Controls & Waveform (`floating_pill.py`)**:
   - Replace simple meeting chip with interactive capsule supporting waveform feedback, pulse indicator, and hover action tray (Mute, Pause, Bookmark, Stop).
4. **Phase 4: QA & Verification**:
   - Verify all 6 lifecycle stages across Zoom and Teams calls using simulated and live audio feeds.

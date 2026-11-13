# Handoff Report: Live Notetaker & Floating Bar Interface Specialist (Requirement 2)

**Agent**: Explorer 2  
**Date**: November 12, 2026  
**State Folder**: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_notetaker\`  
**Target Recipient**: Orchestrator (`c503d3df-e7f6-4338-8039-cce66079aedc`) and UI Implementation Agent  

---

## 1. Observation

1. **Video Reference Analysis (`Notetaker comparision.mp4`)**:
   - At timestamp `00:30`, `VOX — Notetaker` window is floating on the left displaying five static tabs: `["My Notes", "Transcript", "Summary", "Tasks", "Ask AI"]`, along with high-contrast telemetry badges (`Mic: Live (0%)`, `Call Audio: Live (64%)`, `Call-Only Privacy Guard`).
   - At timestamp `00:30` and `01:30`, Wispr Flow Notetaker (`STRATA x WFX - Implementation & Planning`) is snapped to the right edge with clean serif typography, attendee avatar chips (`M A B D +7 Today`), and only three tabs: `My thoughts | ılı Transcript | + Summary`.
   - At timestamp `02:30` (frame 165s), in VOX, clicking `Summary` displays a disabled empty box `"Meeting summary will appear automatically once finalized."`, and clicking `Tasks` displays an empty white container.
   - In Wispr Flow, clicking `+ Summary` displays a centered message: `"Summaries appear when a meeting ends / No summary yet"`. Wispr Flow does not render a `Tasks` tab during live meetings.
   - At timestamp `01:10` to `02:50`, Wispr Flow features an integrated bottom drawer containing `[ Stop ]`, an input field `[ Ask anything, or type / for Skills ]`, and a quick action pill `[ What did I miss? ]`. In contrast, VOX has `Ask AI` isolated in a dedicated 5th tab.

2. **Video Reference Analysis (`Toggle Bar Comparision.mp4`)**:
   - At timestamp `00:48` (frame 048s), direct magnifying callouts contrast the two floating bars:
     - VOX: Clumsy circular floating bubble with high-contrast pause bars (`| |`) floating across text and content.
     - Wispr Flow: Sleek obsidian black capsule docked flush to the screen edge with a red recording dot: `● 08:08 Notetaker`.
   - At timestamp `01:00` (frame 060s), VOX docked on the left screen border renders a vertically fragmented bar with an awkward horizontal popup banner `"Upcoming meetings"` and an overlapping round widget colliding with the open Notetaker Drawer behind it.

3. **Wispr Flow Reverse-Engineered Architecture (`app.asar`)**:
   - Main process window geometry in `.webpack/main/index.js`:
     ```javascript
     recorder: {
       x: workArea.x + workArea.width - recorderWidth,
       y: workArea.y,
       width: Math.min(workArea.width, Math.max(minWidth, Math.round(0.5 * workArea.width))),
       height: workArea.height
     }
     ```
     Wispr Flow snaps the notetaker window to the display's right edge at full work-area height.
   - Renderer tab orchestration in `.webpack/renderer/meeting_recorder/index.js`:
     ```javascript
     const Ja = [
       ...hasNotes ? [{ key: 'notes', label: 'My thoughts' }] : [],
       { key: 'transcript', label: 'Transcript', leading: <Waveform barCount={3} animated={isRecording} /> },
       { key: 'summary', label: 'Summary', leading: <SparkleIcon size={11} /> },
       ...tasksAvailable ? [{ key: 'tasks', label: 'Tasks', badge: `(${completed}/${total})` }] : [],
       ...docCount > 0 ? [{ key: 'docs', label: 'Docs', badge: `(${docCount})` }] : []
     ];
     ```
     `Tasks` tab is conditionally rendered (`...vn ? [Tasks] : []`) **only** when post-processing completes and tasks exist (`"complete" === pn || null !== ne && (Yt.nativeSummaryCompleted || tn.length > 0)`).
   - Tab default selection logic (`YS`):
     ```javascript
     YS = ({ requestedTab, visibleTabSet, userPicked, meetingHasEnded, hasEndedBefore }) => {
       if (visibleTabSet.has(requestedTab)) {
         if (userPicked || hasEndedBefore || requestedTab !== 'summary' || meetingHasEnded || !visibleTabSet.has('notes')) {
           return requestedTab;
         }
         return 'notes';
       }
       return visibleTabSet.has('notes') ? 'notes' : 'summary';
     };
     ```
     During an active meeting (`meetingHasEnded: false`), defaulting to `Summary` is blocked; it redirects to `notes` (`My thoughts`).

4. **VOX Codebase Direct Inspection**:
   - `src/voxlocal/ui/notetaker_drawer.py`:
     - Line 50: `self.resize(500, 420)` with `self.setMinimumSize(300, 200)` — arbitrary floating dialog geometry.
     - Lines 111–123: Five hardcoded tabs `["My Notes", "Transcript", "Summary", "Tasks", "Ask AI"]` and `Full View` button created unconditionally upfront.
     - Lines 126–185: Stack index 2 is disabled text `Meeting summary will appear automatically once finalized.`; index 3 is empty `QListWidget` for tasks; index 4 is separate Q&A page.
     - Lines 324–327: `_on_meeting_finalized()` merely reloads database fields; it fails to switch to `Summary` tab or highlight generated tasks.
   - `src/voxlocal/ui/floating_pill.py`:
     - Line 576: `self.meeting_chip = QPushButton("● 00:00 Notetaker", self)` is a simple `QPushButton` attached to `VoxFlowBar`.
     - Lines 823–827 & 940–944: Updates text to `● MM:SS Notetaker` with static styling. Lacks audio-reactive waveform bars and hover control buttons (Mute, Pause, Bookmark, Stop).
     - Lines 831–834: Side-docking geometry renders `pw, ph = (8, 40)`, causing awkward element overlap and card collision as seen in `Toggle Bar Comparision.mp4`.

---

## 2. Logic Chain

1. **Premise 1 (Observation 1, 3, 4)**: During an active meeting, no AI summary or extracted tasks exist because the meeting is still underway. Displaying empty `Summary` and `Tasks` tabs upfront creates visual clutter and leads to user confusion when clicked.
2. **Premise 2 (Observation 1, 3, 4)**: In Wispr Flow, users focus either on typing live personal notes (`My thoughts`) or following the live conversation (`Transcript`). Placing `Summary` as a decorative post-meeting placeholder (`+ Summary`) and hiding `Tasks` until action items are extracted reduces cognitive overload to zero.
3. **Premise 3 (Observation 1, 4)**: Placing `Ask AI` into an isolated tab isolates the user from their notes and transcript. Moving `Ask AI` into a persistent bottom action bar with a 1-click `[ What did I miss? ]` chip matches user intent without context-switching.
4. **Premise 4 (Observation 2, 3, 4)**: Arbitrary floating dialog placement (`500x420`) obscures video conference controls. Snapping the notetaker window to the display work area edge at full height (`height = workArea.height`) provides a clean side-panel experience.
5. **Premise 5 (Observation 2, 4)**: The current `meeting_chip` on `VoxFlowBar` is passive and lacks direct in-place controls. Implementing a 4-bar mini waveform, live pulse dot, and hover action tray (Mute, Pause, Bookmark, Stop) transforms the floating bar into an unobtrusive, high-utility companion.

---

## 3. Caveats

1. **Hardware / Display Differences**: Screen edge snapping must account for multi-monitor setups and Windows taskbar orientation (bottom, top, left, right) using `QGuiApplication.primaryScreen().availableGeometry()`.
2. **LLM Generation Latency**: Generating summaries and action items via local small language models (GGUF/ONNX) can take 5–15 seconds on CPU. Smooth progress state indications must be preserved in the drawer header to avoid appearing frozen during Stage 4.
3. **Preservation of Existing Functionality**: All existing database persistence (`meetings.notes`, `meetings.summary`, `action_items`), telemetry tracking, and export features must remain fully functional.

---

## 4. Conclusion

VOX's Live Notetaker and Floating Bar must be streamlined to match the Wispr Flow benchmark through four concrete structural changes:
1. **Context-Aware Tabs in `notetaker_drawer.py`**: Transition from 5 static tabs to a dynamic model: show only `My thoughts`, `ılı Transcript`, and `+ Summary` during active calls; dynamically reveal `Tasks (count)` post-finalization.
2. **Integrated Bottom Action Bar**: Replace the `Ask AI` tab with an always-accessible bottom input bar (`Ask anything...`) and `[ What did I miss? ]` quick prompt chip.
3. **Edge Snapping**: Snap `VoxNotetakerDrawer` to the active screen edge at full work-area height instead of arbitrary floating coordinates.
4. **Interactive Floating Capsule in `floating_pill.py`**: Upgrade the passive meeting chip into an interactive capsule with a 4-bar mini waveform, red recording pulse, monospace timer, and hover action tray (Mute, Pause, Bookmark, Stop).

---

## 5. Verification Method

1. **Codebase Inspection**:
   - Inspect `src/voxlocal/ui/notetaker_drawer.py`: Verify that tab generation logic creates only `My thoughts`, `Transcript`, and `+ Summary` when `_meeting_state in ("recording", "paused")`.
   - Inspect `src/voxlocal/ui/floating_pill.py`: Verify that `meeting_chip` includes hover controls and mini waveform hooks.
2. **Simulated Lifecycle Execution**:
   - Run VOX via Python:
     ```powershell
     .\.venv\Scripts\python.exe -m voxlocal.main
     ```
   - Start an ad-hoc meeting recording. Confirm:
     - Drawer opens snapped to screen edge.
     - Only `My thoughts`, `Transcript`, and `+ Summary` are visible.
     - `Tasks` tab is hidden.
     - Bottom bar shows `Ask anything` field and `[ What did I miss? ]`.
     - Floating pill shows `● MM:SS Notetaker` with live waveform bars and exposes Mute/Pause/Bookmark/Stop on hover without stealing focus.
   - Click Stop. Confirm:
     - Drawer automatically transitions to `Summary` tab.
     - `Tasks` tab appears with the number of generated action items.
3. **Existing Test Suite Verification**:
   - Run full pytest suite:
     ```powershell
     .\.venv\Scripts\pytest.exe tests/ -q
     ```
     Ensure all existing tests pass with zero regressions.

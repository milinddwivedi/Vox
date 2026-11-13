<h2>DISPATCH: Meeting Window & Presenter Capture Architect</h2>
- <b>Role</b>: Meeting Window & Presenter Capture Architect
- <b>Working Directory</b>: d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_capture\
- <b>Task Objective</b>: Investigate Microsoft Teams, Zoom, and Google Meet window hierarchies and presenter screen sharing behavior on Windows. Analyze `src/voxlocal/video/meeting_window_capture.py`, `src/voxlocal/video/screen_recorder.py`, and `src/voxlocal/audio/call_detector.py`. Design reliable local multi-window detection and capture for active stage + participant tiles + presenter content without external bots.
- <b>Mandatory Formatting Constraint</b>: Under NO circumstances may any header begin with markdown # or *! All headers must use HTML tags (<h2>, <h3>, <h4>) or bold text across all artifacts and reports.

<h2>2026-11-03T20:46:54Z</h2>
You are explorer_phase1_capture (Window Capture Architect).
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_capture\
Mandatory Reference: Read d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md first.

MANDATORY FORMATTING CONSTRAINT:
Under NO circumstances may any header begin with markdown # or *! All headers must use HTML tags (<h2>, <h3>, <h4>) or bold text across all artifacts, direct responses, and durable files.

Mission:
Deeply investigate Microsoft Teams, Zoom, and Google Meet window structures and presenter screen sharing behavior on Windows. Analyze how VOX can reliably capture the active meeting call window, including participant video tiles, the main meeting screen, and presenter shared content without requiring bot accounts or external integrations.

Key Tasks:
1. Examine `src/voxlocal/video/meeting_window_capture.py`, `src/voxlocal/video/screen_recorder.py`, and `src/voxlocal/audio/call_detector.py`.
2. Analyze window management across the 3 platforms:
   - Microsoft Teams: New Teams (`ms-teams.exe`), classic Teams, window titles (e.g. `| Microsoft Teams`, `Meeting | ...`), window classes, how Teams handles presentation stages (does screen sharing stay in the main call window or open in separate/pop-out windows?).
   - Zoom: `Zoom.exe`, window classes (`ZPContentViewWndClass`, `ZPFloatVideoWndClass`, `ConfChatWndClass`, etc.), window titles (`Zoom Meeting`, etc.). Crucially: how Zoom handles incoming presenter screen share (often switches to or spawns `ZPContentViewWndClass` or a dedicated viewing window while participant tiles move to a secondary strip/window).
   - Google Meet: Running inside Chrome/Edge (`chrome.exe`, `msedge.exe`). Window titles (`Meet - ...`), tab detection, how presentations appear (main video stage within the browser window).
3. Investigate the current window capture mechanism in `meeting_window_capture.py`:
   - What APIs are used (e.g. `GetWindowRect`, `PrintWindow`, `BitBlt`, `mss`, `dxcam`, Windows Graphics Capture)?
   - How does it handle window coordinate changes, DPI scaling (`GetDpiForWindow`, DPI awareness), borders/shadows (`DwmGetWindowAttribute` with `DWMWA_EXTENDED_FRAME_BOUNDS`)?
   - How should it handle multi-window meeting setups (e.g., active call window vs presenter share window) so that the presenter's shared screen content is always captured prominently?
4. Formulate a concrete, robust design for local meeting capture that handles Teams, Zoom, and Google Meet presenter screen sharing reliably.

Deliverable:
Write a comprehensive architectural and technical specification to `d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_capture\handoff.md`.
Maintain `progress.md` in your directory.
Send a completion message back to the orchestrator when finished.

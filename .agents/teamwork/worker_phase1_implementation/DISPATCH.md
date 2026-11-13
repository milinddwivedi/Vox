<h2>2026-11-03T21:01:47Z</h2>

You are worker_phase1_implementation (Phase 1 Lead Implementation Worker).
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\worker_phase1_implementation\

Mandatory References to Read First:
1. d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
2. d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\handoff.md
3. d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_capture\handoff.md
4. d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_privacy\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY FORMATTING CONSTRAINT:
Under NO circumstances may any header begin with markdown # or *! All headers must use HTML tags (<h2>, <h3>, <h4>) or bold text across all artifacts, direct responses, and durable files.

Files Owned Exclusively:
- src/voxlocal/video/meeting_capture.py (referenced as meeting_window_capture.py)
- src/voxlocal/video/screen_recorder.py
- src/voxlocal/audio/call_detector.py
- tests/test_meeting_window_presenter_and_privacy.py (and relevant test suites)

Mission & Core Implementation Requirements:

1. Fix Ctypes RECT Type Collision:
   - In `src/voxlocal/video/meeting_capture.py`, eliminate the conflicting duplicate `class RECT` and unify strictly on `ctypes.wintypes.RECT` across the entire file (and cross-check `screen_recorder.py` and `call_detector.py`).
   - Ensure `user32.GetWindowRect.argtypes` and all calls to `GetWindowRect` use the exact same type, eliminating `ctypes.ArgumentError` and process-wide poisoning.

2. Local Meeting Window & Presenter Screen Capture (Microsoft Teams, Zoom, Google Meet):
   - In `meeting_capture.py` (and `screen_recorder.py`):
     - Microsoft Teams: Detect presenter screen sharing accurately both when presenting locally and when receiving a remote presenter's share (main stage window, pop-out stage window `Chrome_WidgetWin_1`). Capture active meeting call window, participant tiles, and presentation stage without requiring external bots.
     - Zoom: Fix `find_zoom_sharing_session()`: Recognize incoming presenter screen share window `ZPContentViewWndClass` with titles like "Zoom Meeting", "Zoom Workplace" (do not require "sharing" in the title). Handle detached participant video strip (`ZPFloatVideoWndClass`) and composite or prioritize presentation stage. Fix `presentation_hwnd = 0` desktop-blitting bug so window boundaries are strictly respected.
     - Google Meet: Accurately capture Meet call stage and presentation in browser/PWA window.
     - Safe DirectX/Hardware-Accelerated Window Capture: Support modern hardware-accelerated DirectX/WebView2 surfaces without black frames while strictly preventing desktop leakage or background window leakage via occlusion verification.

3. Window Minimization Handling & Privacy Safeguards:
   - In both `meeting_capture.py` and `screen_recorder.py`:
     - Inside the active recording loop (`_record_worker`), continuously check `user32.IsIconic(hwnd)` and window placement.
     - When the meeting window is minimized, NEVER capture the user's general desktop or background windows.
     - Inhibit cursor capture and ripple rendering during minimization or occlusion.
     - Never fall back to `StretchBlt(hdc_screen)` capturing the full desktop.
     - Inject a clean, branded VOX privacy screen placeholder into the video pipeline:
       - Dark theme (Obsidian background, subtle Slate card, VOX branding badge, clear legible text: "Meeting capture paused for privacy", status/liveness indicator).
       - Maintain monotonic presentation timestamps (PTS) and steady frame pacing at the configured FPS so the MP4 container and A/V sync remain intact with zero frame drops or stream corruption.
     - When restored or maximized, seamlessly resume capturing the call content.
     - Handle resolution/size changes smoothly (e.g. aspect-ratio preserving letterboxing/pillarboxing to keep output MP4 stream dimensions constant).

4. Programmatic Verification & Automated Tests:
   - Implement comprehensive programmatic verification tests in `tests/test_meeting_window_presenter_and_privacy.py`:
     - Use synthetic/simulated local windows representing meeting states, presenter screen shares, and minimize/restore transitions with distinct color/pattern canaries.
     - Record real MP4 video files.
     - Use PyAV to inspect resulting MP4 frames and verify:
       a) Meeting/presentation content recorded during active states.
       b) Branded VOX privacy placeholder recorded during minimized states.
       c) ZERO desktop leakage (verified against canary colors).
       d) Audio-video synchronization and monotonic PTS verified.
   - Run the automated test suite using `.venv\Scripts\python.exe -m pytest` to verify 100% pass and zero regressions.

Deliverable:
- Update source and test files.
- Deliver `handoff.md` in `worker_phase1_implementation/` following the 5-component protocol with strict HTML header formatting.
- Send a completion message to the orchestrator when all changes and test passes are verified.

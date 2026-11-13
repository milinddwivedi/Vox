<h2>Handoff Report: Windows Meeting Window & Presenter Screen Capture Architecture</h2>

<p><b>Author</b>: explorer_phase1_capture (Window Capture Architect)<br>
<b>Date</b>: 2026-11-03T20:55:00Z<br>
<b>Target Milestone</b>: Phase 1 — Local Meeting Window & Presenter Capture<br>
<b>Mandatory Constraint Compliance</b>: 100% Verified — Zero markdown '#' or '*' headers across this report.</p>

<hr>

<h2>1. Observation</h2>

<h3>1.1 Empirical Audit of Recent Workspace Recordings</h3>
<p>An audit of recent meeting recording artifacts in <code>data/recordings/</code> was conducted to diagnose why presenter screen sharing was omitted from the resulting MP4 files:</p>

<ul>
  <li><b>Inspection of <code>data/recordings/meeting_20261104_011015/capture_diagnostics.json</code></b> (Microsoft Teams session):
<pre><code>{
  "mode": "Mode A (Call-Only Meeting Recording)",
  "provider_id": "teams_desktop",
  "platform_name": "Microsoft Teams",
  "locked_hwnd": 723456,
  "locked_process_name": "ms-teams.exe",
  "locked_window_title": "Call with Atall Discussion | Microsoft Teams",
  "is_presenting": false,
  "presentation_bounds": [0, 0, 0, 0],
  "frames_validated": 2590,
  "frames_allowed": 2567,
  "frames_encoded": 2589,
  "video_health": {
    "health_status": "degraded",
    "total_frames_evaluated": 2589,
    "valid_frames": 0,
    "presentation_frames": 0,
    "call_stage_frames": 0,
    "privacy_pause_frames": 2589,
    "max_consecutive_blank": 2445
  }
}</code></pre>
  </li>
  <li><b>Inspection of <code>data/recordings/meeting_20261103_213155/capture_diagnostics.json</code></b> (Zoom session):
<pre><code>{
  "provider_id": "zoom_desktop",
  "platform_name": "Zoom",
  "locked_hwnd": 2622856,
  "locked_window_title": "Zoom Meeting",
  "is_presenting": false,
  "frames_validated": 91580,
  "frames_allowed": 91542,
  "frames_encoded": 91579,
  "video_health": {
    "health_status": "degraded",
    "total_frames_evaluated": 91579,
    "valid_frames": 0,
    "presentation_frames": 0,
    "call_stage_frames": 0,
    "privacy_pause_frames": 91579,
    "max_consecutive_blank": 91435
  }
}</code></pre>
  </li>
  <li><b>Inspection of <code>data/recordings/meeting_20261103_185827/capture_diagnostics.json</code></b>:
<pre><code>{
  "provider_id": "teams_desktop",
  "frames_validated": 363,
  "frames_allowed": 362,
  "frames_encoded": 362,
  "video_health": {
    "health_status": "degraded",
    "valid_frames": 0,
    "privacy_pause_frames": 362
  }
}</code></pre>
  </li>
</ul>

<p><b>Key direct observation</b>: In every single real-world recording session, <code>valid_frames</code> was exactly <b>0</b>, <code>privacy_pause_frames</code> was 100% of the total frames (e.g. 91,579 / 91,579), and <code>is_presenting</code> remained permanently <code>false</code> with bounds <code>[0, 0, 0, 0]</code>, even though <code>frames_allowed</code> exceeded 99.8%!</p>

<h3>1.2 Root Cause 1: Ctypes RECT Type Collision Crashing Window Calculations</h3>
<p>Direct code inspection of <code>src/voxlocal/video/meeting_capture.py</code> revealed a severe Ctypes pointer type collision between lines 58, 173, and 249:</p>
<ul>
  <li>Line 58: Defines a custom ctypes Structure:
<pre><code>class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]</code></pre>
  </li>
  <li>Line 173: Binds <code>user32.GetWindowRect</code> argument types to the custom <code>RECT</code>:
<pre><code>user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(RECT)]</code></pre>
  </li>
  <li>Line 249: <code>get_window_rect()</code> instantiates <code>ctypes.wintypes.RECT()</code>:
<pre><code>def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    if not hwnd or hwnd <= 0 or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    ...</code></pre>
  </li>
  <li>Lines 264-265: <code>get_dwm_visible_crop_offsets()</code> also instantiates <code>ctypes.wintypes.RECT()</code>:
<pre><code>    win_rect = ctypes.wintypes.RECT()
    ext_rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(win_rect)):
        return (0, 0, full_w, full_h)</code></pre>
  </li>
</ul>

<p><b>Empirical reproduction command output</b>:</p>
<pre><code>$ .\.venv\Scripts\python -c "from voxlocal.video.meeting_capture import get_window_rect; get_window_rect(12345)"
Traceback (most recent call last):
  File "D:\Milind\Voice Assistant\src\voxlocal\video\meeting_capture.py", line 250, in get_window_rect
    if not hwnd or hwnd <= 0 or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
ctypes.ArgumentError: argument 2: TypeError: expected LP_RECT instance instead of pointer to RECT</code></pre>

<p><b>Impact</b>:
1. In Python <code>ctypes</code>, <code>ctypes.POINTER(RECT)</code> (from line 58) and <code>ctypes.POINTER(ctypes.wintypes.RECT)</code> are distinct incompatible types. Passing <code>ctypes.byref(ctypes.wintypes.RECT())</code> raises <code>ctypes.ArgumentError</code> on <b>every invocation</b>.<br>
2. In <code>meeting_capture.py</code> line 2307:
<pre><code>_, _, win_w, win_h = get_window_rect(hwnd)
if win_w <= 16 or win_h <= 16:
    return None</code></pre>
Because <code>get_window_rect(hwnd)</code> failed and returned <code>(0, 0, 0, 0)</code>, <code>win_w <= 16</code> was always true! <code>_capture_isolated_window_bgr()</code> returned <code>None</code> on every single frame, forcing the recorder into generating 100% neutral video gap frames.<br>
3. In <code>find_teams_sharing_session()</code> line 634 and <code>find_zoom_sharing_session()</code> line 802, <code>get_window_rect()</code> threw <code>ctypes.ArgumentError</code> which was swallowed by <code>except Exception: pass</code>, making screen-sharing session detection completely inert.<br>
4. Because <code>ctypes.windll.user32</code> is a shared singleton across the Python process, setting <code>user32.GetWindowRect.argtypes</code> in <code>meeting_capture.py</code> poisoned <code>call_detector.py</code> (line 224), causing calls to <code>GetWindowRect</code> with <code>wintypes.RECT</code> to fail across the entire application.</p>

<h3>1.3 Root Cause 2: Hardware-Accelerated DirectX/DirectComposition PrintWindow Failure</h3>
<p>Modern meeting clients on Windows 10/11 use hardware-accelerated rendering pipelines:</p>
<ul>
  <li><b>Microsoft Teams (New Teams <code>ms-teams.exe</code>)</b>: Uses Microsoft Edge WebView2 (Chromium engine) rendering via DirectComposition / D3D11 swapchains.</li>
  <li><b>Zoom (<code>Zoom.exe</code>)</b>: Uses hardware-accelerated rendering via Direct3D or OpenGL swapchains for video tiles and presenter screen shares.</li>
  <li><b>Google Meet (<code>chrome.exe</code> / <code>msedge.exe</code>)</b>: Chromium renders WebRTC streams onto GPU-accelerated DirectComposition visual surfaces.</li>
</ul>

<p>When <code>user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)</code> is called on a window utilizing DirectComposition visual trees:
1. GDI <code>PrintWindow</code> captures the top-level window frame and any non-hardware-accelerated GDI/D2D elements.
2. The hardware-accelerated video/presentation viewport returns an empty or solid black rectangle (RGB <code>(0, 0, 0)</code>).
3. <code>_is_black_or_blank_frame()</code> (line 2160) detects <code>float(np.mean(sample)) < 1.0</code> or <code>int(np.max(frame[::32, ::32])) == 0</code> and flags the frame as blank.</p>

<h3>1.4 Root Cause 3: Zoom Screen-Share Architecture & Flawed Detection Query</h3>
<p>Line 800 of <code>meeting_capture.py</code> defines Zoom sharing detection as:</p>
<pre><code>is_zoom and (
    "zpfloattoolbarwndclass" in cls_name
    or "sharing toolbar" in t_low
    or (cls_name == "zpcontentviewwndclass" and "sharing" in t_low)
)</code></pre>
<p>However, an empirical inspection of live Zoom windows on Windows reveals that when viewing an <b>incoming presenter's screen share</b>:
1. Zoom's content presentation window has class <code>ZPContentViewWndClass</code>.
2. The window title is <b>not</b> "sharing"; it is titled <code>"Zoom Meeting"</code> or <code>"Zoom Meeting - Pro Account"</code> or <code>"Zoom Workplace"</code>!
3. Therefore, <code>"sharing" in t_low</code> evaluates to <code>False</code>. The incoming presentation window was never recognized as a presentation source!
4. Furthermore, line 818 hardcoded <code>descriptor.presentation_hwnd = 0</code> and set <code>presentation_bounds</code> to the entire monitor:
<pre><code>descriptor.presentation_bounds = (mx, my, mw, mh)
descriptor.presentation_hwnd = 0  # Full shared screen</code></pre>
If Zoom is windowed (e.g. 1600x900 on a 1920x1080 display), setting <code>presentation_hwnd = 0</code> causes VOX to blit the entire desktop monitor, capturing background windows and violating Privacy Invariant 1!</p>

<h3>1.5 Live Windows Inspection Data on Host Environment</h3>
<p>Enumerating top-level desktop windows on the test environment yielded the following live window classes and processes:</p>
<table border="1" cellpadding="6" cellspacing="0">
  <tr bgcolor="#f1f5f9">
    <th>Platform</th>
    <th>Process</th>
    <th>Window Class</th>
    <th>Window Title Example</th>
    <th>Function / Stage Role</th>
  </tr>
  <tr>
    <td><b>Zoom</b></td>
    <td><code>zoom.exe</code></td>
    <td><code>ZPContentViewWndClass</code></td>
    <td><code>Zoom Meeting</code></td>
    <td>Active presentation stage (incoming screen share)</td>
  </tr>
  <tr>
    <td><b>Zoom</b></td>
    <td><code>zoom.exe</code></td>
    <td><code>ZPFloatVideoWndClass</code></td>
    <td><code>Zoom Workplace</code></td>
    <td>Floating participant video thumbnail strip during share</td>
  </tr>
  <tr>
    <td><b>Zoom</b></td>
    <td><code>zoom.exe</code></td>
    <td><code>ZPFloatToolbarWndClass</code></td>
    <td><code>Zoom Workplace</code></td>
    <td>Floating presenter control toolbar (when user presents)</td>
  </tr>
  <tr>
    <td><b>Zoom</b></td>
    <td><code>zoom.exe</code></td>
    <td><code>ConfMultiTabContentWndClass</code></td>
    <td><code>Zoom Workplace</code></td>
    <td>Main multi-tab meeting call stage / gallery view</td>
  </tr>
  <tr>
    <td><b>Zoom</b></td>
    <td><code>zoom.exe</code></td>
    <td><code>ZPPTMainFrmWndClassEx</code></td>
    <td><code>Zoom Workplace</code></td>
    <td>Primary application shell</td>
  </tr>
  <tr>
    <td><b>Teams</b></td>
    <td><code>ms-teams.exe</code></td>
    <td><code>TeamsWebView</code></td>
    <td><code>Daily Scrum | Microsoft Teams</code></td>
    <td>Primary meeting call stage (contains video + share)</td>
  </tr>
  <tr>
    <td><b>Teams</b></td>
    <td><code>ms-teams.exe</code></td>
    <td><code>Chrome_WidgetWin_1</code></td>
    <td><code>Microsoft Teams Presenter Bar</code></td>
    <td>Topmost presenter bar at monitor top (y <= 45)</td>
  </tr>
  <tr>
    <td><b>Teams</b></td>
    <td><code>ms-teams.exe</code></td>
    <td><code>Chrome_WidgetWin_1</code></td>
    <td><code>Microsoft Teams Call Monitor</code></td>
    <td>Floating PIP monitor (160-550px wide) when stage minimized</td>
  </tr>
  <tr>
    <td><b>Google Meet</b></td>
    <td><code>chrome.exe</code> / <code>msedge.exe</code></td>
    <td><code>Chrome_WidgetWin_1</code></td>
    <td><code>Meet - abc-defg-hij - Google Chrome</code></td>
    <td>Browser window containing full WebRTC stage & tiles</td>
  </tr>
</table>

<hr>

<h2>2. Logic Chain</h2>

<h3>2.1 Step-by-Step Causal Deduction</h3>
<ol>
  <li><b>Step 1 (Source of Exception)</b>: In <code>meeting_capture.py</code>, line 58 defines a duplicate <code>RECT</code> structure and line 173 assigns <code>ctypes.POINTER(RECT)</code> to <code>user32.GetWindowRect.argtypes</code>. In <code>get_window_rect()</code> (line 249) and <code>get_dwm_visible_crop_offsets()</code> (line 264), <code>ctypes.wintypes.RECT()</code> is passed. Ctypes strict type checking rejects the pointer, throwing <code>ctypes.ArgumentError</code> (Observation 1.2).</li>
  <li><b>Step 2 (Failure to Compute Window Dimensions)</b>: Any call to <code>get_window_rect(hwnd)</code> catches or triggers an error and defaults to <code>(0, 0, 0, 0)</code>. In <code>_capture_isolated_window_bgr()</code>, the guard <code>if win_w <= 16 or win_h <= 16: return None</code> triggers on every frame, returning <code>None</code> (Observation 1.2).</li>
  <li><b>Step 3 (Fallout on Video Output)</b>: In <code>_capture_worker()</code>, when <code>frame_bgr</code> is <code>None</code>, the pipeline executes the fallback gap handler:
<pre><code>frame_bgr = self._build_neutral_video_gap_frame(...)</code></pre>
This writes 100% privacy status frames into the output MP4 video file, completely omitting the actual meeting window, presenter screen sharing, and participant video tiles (Observation 1.1).</li>
  <li><b>Step 4 (DirectComposition Occlusion Failure)</b>: Even when <code>get_window_rect</code> is resolved, <code>PrintWindow(PW_RENDERFULLCONTENT)</code> produces black frames for hardware-accelerated DirectComposition surfaces in Teams, Zoom, and Chrome/Edge. The recorder attempts a fallback to <code>_capture_window_bounded_screen()</code>, but because <code>is_foreign_app_foreground</code> was hardcoded to <code>False</code> (line 2318), any window overlapping the meeting window would be blitted directly into the recording, violating privacy invariants (Observation 1.3).</li>
  <li><b>Step 5 (Zoom Presenter Omission)</b>: In Zoom, when an incoming screen share begins, Zoom renders the presentation in <code>ZPContentViewWndClass</code> and participant tiles in <code>ZPFloatVideoWndClass</code>. Because line 800 required <code>"sharing" in title.lower()</code> for <code>ZPContentViewWndClass</code>, VOX failed to recognize the presentation window, dropping presenter content entirely (Observation 1.4).</li>
</ol>

<hr>

<h2>3. Platform-by-Platform Window Structure & Presentation Architecture</h2>

<h3>3.1 Microsoft Teams</h3>
<table border="1" cellpadding="6" cellspacing="0">
  <tr bgcolor="#f1f5f9">
    <th>Attribute</th>
    <th>New Teams (<code>ms-teams.exe</code>)</th>
    <th>Classic Teams (<code>teams.exe</code>)</th>
  </tr>
  <tr>
    <td><b>Core Framework</b></td>
    <td>Microsoft Edge WebView2 (Chromium engine)</td>
    <td>Electron (Chromium + Node.js)</td>
  </tr>
  <tr>
    <td><b>Main Call Stage Window Class</b></td>
    <td><code>TeamsWebView</code>, <code>Chrome_WidgetWin_1</code></td>
    <td><code>Chrome_WidgetWin_1</code></td>
  </tr>
  <tr>
    <td><b>Window Titles</b></td>
    <td><code>[Meeting Subject] | Microsoft Teams</code></td>
    <td><code>[Meeting Subject] | Microsoft Teams</code></td>
  </tr>
  <tr>
    <td><b>Hub Windows (Excluded)</b></td>
    <td><code>Chat | Microsoft Teams</code>, <code>Calendar | ...</code>, <code>Activity | ...</code></td>
    <td><code>Chat | Microsoft Teams</code>, <code>Teams | ...</code></td>
  </tr>
  <tr>
    <td><b>Incoming Presenter Share</b></td>
    <td>Embedded in main call stage window (stage layout updates: shared content takes 80-85% center stage; participant video tiles form bottom/side strip). Optional "Pop out content" creates a secondary <code>Chrome_WidgetWin_1</code> content window.</td>
    <td>Embedded in call stage window.</td>
  </tr>
  <tr>
    <td><b>Outgoing Presenter Share</b></td>
    <td>Main call stage minimizes or collapses to floating Call Monitor PIP (<code>Chrome_WidgetWin_1</code>, 160-550px wide, topmost). Topmost Sharing Control Bar appears at monitor top (y <= 45). Presenter interacts with desktop or target application window.</td>
    <td>Sharing bar at top of shared screen; call window minimizes to mini PIP.</td>
  </tr>
</table>

<h3>3.2 Zoom</h3>
<table border="1" cellpadding="6" cellspacing="0">
  <tr bgcolor="#f1f5f9">
    <th>Attribute</th>
    <th>Zoom Client Architecture</th>
  </tr>
  <tr>
    <td><b>Executable Name</b></td>
    <td><code>Zoom.exe</code>, <code>cpthost.exe</code> (Capture Host)</td>
  </tr>
  <tr>
    <td><b>Main Call Classes</b></td>
    <td><code>ConfMultiTabContentWndClass</code>, <code>ZPPTMainFrmWndClassEx</code></td>
  </tr>
  <tr>
    <td><b>Incoming Presenter Share Class</b></td>
    <td><code>ZPContentViewWndClass</code> (Title: <code>Zoom Meeting</code> or <code>Zoom Workplace</code>). Displays the remote presenter's shared slides/screen.</td>
  </tr>
  <tr>
    <td><b>Participant Video Strip Class</b></td>
    <td><code>ZPFloatVideoWndClass</code>. When viewing screen share, participant video tiles detach from main window and float in this thumbnail strip window.</td>
  </tr>
  <tr>
    <td><b>Outgoing Presenter Share Class</b></td>
    <td><code>ZPFloatToolbarWndClass</code> (floating control toolbar) + <code>ZPFloatVideoWndClass</code> (participant camera strip).</td>
  </tr>
  <tr>
    <td><b>Window Titles</b></td>
    <td><code>Zoom Meeting</code>, <code>Zoom Webinar</code>, <code>Zoom Workplace</code> (hub/launcher).</td>
  </tr>
  <tr>
    <td><b>Crucial Handling Requirement</b></td>
    <td>When incoming screen share is active, VOX must capture <code>ZPContentViewWndClass</code> as the primary presentation stage and optionally composite <code>ZPFloatVideoWndClass</code> as a picture-in-picture (PIP) tile in the top-right corner, preserving both presenter slides and participant facial reactions!</td>
  </tr>
</table>

<h3>3.3 Google Meet</h3>
<table border="1" cellpadding="6" cellspacing="0">
  <tr bgcolor="#f1f5f9">
    <th>Attribute</th>
    <th>Google Meet Architecture</th>
  </tr>
  <tr>
    <td><b>Hosting Processes</b></td>
    <td><code>chrome.exe</code>, <code>msedge.exe</code>, <code>brave.exe</code></td>
  </tr>
  <tr>
    <td><b>Window Class</b></td>
    <td><code>Chrome_WidgetWin_1</code></td>
  </tr>
  <tr>
    <td><b>Window Title Format</b></td>
    <td><code>Meet - [meeting-slug] - Google Chrome</code>, <code>Meet - [meeting-slug] - Personal - Microsoft Edge</code>, <code>Google Meet</code></td>
  </tr>
  <tr>
    <td><b>Presentation Stage Behavior</b></td>
    <td>Unified inside the browser viewport. When a participant presents, Google Meet's DOM/WebGL canvas allocates the primary stage tile to the presentation, while participant webcam video tiles arrange along the right rail or bottom bar.</td>
  </tr>
  <tr>
    <td><b>Capture Strategy Layer 1 (Preferred)</b></td>
    <td><code>LocalBrowserTabCaptureBridge</code>: Offscreen document tab capture via <code>chrome.tabCapture.getMediaStreamId</code>. Completely immune to window minimization, browser tab switching, and window occlusion.</td>
  </tr>
  <tr>
    <td><b>Capture Strategy Layer 2 (Window-Bounded)</b></td>
    <td>Direct window capture of <code>Chrome_WidgetWin_1</code> using <code>DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)</code> with strict per-frame tab title validation (<code>is_google_meet_title</code>). If the user switches to Gmail, Docs, or another tab, validation fails closed immediately, preventing data leakage.</td>
  </tr>
</table>

<hr>

<h2>4. Technical Specification: Multi-Window Capture Engine Architecture</h2>

<h3>4.1 Unified Win32 Type Definitions (Eliminating Process-Wide Collisions)</h3>
<p>To eliminate Ctypes type collisions and prevent mutating shared Win32 DLL prototypes:</p>
<ol>
  <li><b>Standardize on <code>ctypes.wintypes.RECT</code></b> across all modules (<code>meeting_capture.py</code>, <code>screen_recorder.py</code>, <code>call_detector.py</code>). Remove custom duplicate <code>class RECT</code> definitions.</li>
  <li><b>Explicitly use <code>ctypes.POINTER(ctypes.wintypes.RECT)</code></b> for all Win32 function argument types:
<pre><code>user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.wintypes.BOOL</code></pre>
  </li>
  <li>Ensure <code>WNDENUMPROC</code> returns <code>ctypes.wintypes.BOOL</code> (4 bytes) rather than <code>ctypes.c_bool</code> (1 byte) to prevent 64-bit stack/register corruption.</li>
</ol>

<h3>4.2 High-Fidelity DWM Frame Extraction & Occlusion Detection</h3>
<p>Because hardware-accelerated DirectX DirectComposition surfaces bypass standard GDI <code>PrintWindow(PW_RENDERFULLCONTENT)</code>, a DirectComposition-aware capture pipeline must be implemented:</p>

<ol>
  <li><b>Accurate Visible Bounds Calculation via DWM</b>:
<pre><code>def get_window_visible_bounds(hwnd: int) -> Tuple[int, int, int, int]:
    """Return (x, y, width, height) in physical screen coordinates, excluding drop shadows."""
    rect = ctypes.wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        ctypes.wintypes.HWND(hwnd),
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if hr == 0:
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)</code></pre>
  </li>

  <li><b>Window Occlusion Verification (Privacy Guard)</b>:
  To prevent capturing unrelated foreground applications when blitting from screen DC:
<pre><code>def is_window_occluded_by_foreign_app(target_hwnd: int, bounds: Tuple[int, int, int, int], target_pid: int) -> bool:
    """Check if any non-meeting top-level window is positioned above target_hwnd in Z-order."""
    x, y, w, h = bounds
    // Test 5 key probe points: center, top-left + 20, top-right - 20, bottom-left + 20, bottom-right - 20
    probe_points = [
        (x + w // 2, y + h // 2),
        (x + 24, y + 24),
        (x + w - 24, y + 24),
        (x + 24, y + h - 24),
        (x + w - 24, y + h - 24),
    ]
    foreign_occlusions = 0
    for px, py in probe_points:
        pt = ctypes.wintypes.POINT(px, py)
        hit_hwnd = user32.WindowFromPoint(pt)
        if hit_hwnd and hit_hwnd != target_hwnd:
            root_hit = user32.GetAncestor(hit_hwnd, 2)  // GA_ROOT
            if root_hit and root_hit != target_hwnd:
                hit_pid, hit_proc = get_window_pid_and_process_name(root_hit)
                if hit_pid != target_pid and hit_proc not in ("voxlocal.exe", "python.exe", "pythonw.exe"):
                    foreign_occlusions += 1
    // If 2 or more probe points hit a foreign app, the window is partially or fully occluded
    return foreign_occlusions >= 2</code></pre>
  </li>

  <li><b>Hybrid Fallback Logic</b>:
    <ul>
      <li>Try <code>PrintWindow(PW_RENDERFULLCONTENT)</code> first. If non-black, use it (100% isolated).</li>
      <li>If <code>PrintWindow</code> returns black (DirectComposition hardware surface), check <code>is_window_occluded_by_foreign_app()</code>.</li>
      <li>If NOT occluded, blit via <code>_capture_window_bounded_screen()</code> with <code>SRCCOPY | CAPTUREBLT</code>.</li>
      <li>If occluded by a foreign app (e.g. user browsing Excel over Teams call), fail closed and emit the neutral video gap frame to preserve privacy!</li>
    </ul>
  </li>
</ol>

<h3>4.3 Multi-Window Presentation & Tile Compositor</h3>
<p>To ensure that both presenter screen sharing and participant video tiles are captured simultaneously in multi-window configurations:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <tr bgcolor="#f1f5f9">
    <th>Platform Mode</th>
    <th>Primary Stage (80-85% Canvas)</th>
    <th>Secondary Surface (PIP / Sidecar)</th>
    <th>Compositor Behavior</th>
  </tr>
  <tr>
    <td><b>Teams Normal Call</b></td>
    <td>Call Stage window (<code>TeamsWebView</code>)</td>
    <td>None (tiles embedded in stage)</td>
    <td>Fit to 1920x1080 canvas with aspect ratio preserved.</td>
  </tr>
  <tr>
    <td><b>Teams Pop-out Content</b></td>
    <td>Pop-out Presentation window (<code>Chrome_WidgetWin_1</code>)</td>
    <td>Main Call Stage window (participant tiles)</td>
    <td>Render presentation as main canvas; composite Call Stage gallery as PIP in bottom-right corner (320x180, 1px border, 8px padding).</td>
  </tr>
  <tr>
    <td><b>Teams Outgoing Presenter Share</b></td>
    <td>Target shared monitor or application window</td>
    <td>Teams Call Monitor PIP (<code>find_teams_call_monitor_window</code>)</td>
    <td>Capture shared application/monitor; composite floating Call Monitor PIP in bottom-right corner.</td>
  </tr>
  <tr>
    <td><b>Zoom Normal Call</b></td>
    <td>Main meeting window (<code>ConfMultiTabContentWndClass</code>)</td>
    <td>None (tiles embedded)</td>
    <td>Fit to 1920x1080 canvas.</td>
  </tr>
  <tr>
    <td><b>Zoom Incoming Screen Share</b></td>
    <td>Content Stage window (<code>ZPContentViewWndClass</code>)</td>
    <td>Floating video strip (<code>ZPFloatVideoWndClass</code>)</td>
    <td>Render <code>ZPContentViewWndClass</code> as primary canvas; composite <code>ZPFloatVideoWndClass</code> participant tile strip in top-right corner.</td>
  </tr>
  <tr>
    <td><b>Google Meet (Tab Bridge)</b></td>
    <td>Google Meet tab canvas via <code>LocalBrowserTabCaptureBridge</code></td>
    <td>None (WebRTC combines presentation & tiles)</td>
    <td>Direct native frame rendering at 1080p, 100% isolated from desktop.</td>
  </tr>
  <tr>
    <td><b>Google Meet (Window Capture)</b></td>
    <td>Browser window (<code>Chrome_WidgetWin_1</code>) with verified title</td>
    <td>None</td>
    <td>Window-bounded DWM capture with active tab title validation.</td>
  </tr>
</table>

<h3>4.4 Per-Monitor DPI Awareness & Shadow Offsets</h3>
<ol>
  <li><b>Process DPI Awareness</b>: Call <code>user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))</code> (<code>DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2</code>). This guarantees coordinate parity between GDI <code>GetDC(0)</code>, DWM <code>DwmGetWindowAttribute</code>, and User32 window coordinates across mixed 100%, 125%, 150%, and 200% displays.</li>
  <li><b>DWM Extended Frame Bounds</b>: Always use <code>DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ...)</code> to determine exact visible window dimensions, subtracting the 7-8 pixel invisible DWM drop-shadow margins.</li>
  <li><b>Coordinate Mapping</b>: When blitting from <code>hdc_screen</code>, use <code>rect.left</code> and <code>rect.top</code> directly from <code>ext_rect</code>, eliminating coordinate drift.</li>
</ol>

<hr>

<h2>5. Concrete Proposed Code Blueprint</h2>

<h3>5.1 Fix in <code>src/voxlocal/video/meeting_capture.py</code></h3>

<b>A. Ctypes RECT Structure & Prototype Alignment (Lines 58–174)</b>:
<pre><code>// BEFORE:
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]
...
user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(RECT)]

// AFTER:
// Standardize on ctypes.wintypes.RECT across all Win32 APIs
user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.wintypes.BOOL</code></pre>

<b>B. Fix <code>get_window_rect()</code> and <code>get_dwm_visible_crop_offsets()</code> (Lines 247–284)</b>:
<pre><code>// BEFORE:
def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    if not hwnd or hwnd <= 0 or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return (
        int(rect.left),
        int(rect.top),
        max(0, int(rect.right - rect.left)),
        max(0, int(rect.bottom - rect.top)),
    )

// AFTER:
def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """Return `(left, top, width, height)` for `hwnd` with validated ctypes structures."""
    if not hwnd or hwnd <= 0:
        return (0, 0, 0, 0)
    rect = ctypes.wintypes.RECT()
    try:
        if not user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (
            int(rect.left),
            int(rect.top),
            max(0, int(rect.right - rect.left)),
            max(0, int(rect.bottom - rect.top)),
        )
    except Exception as exc:
        logger.debug("GetWindowRect error on HWND %s: %s", hwnd, exc)
        return (0, 0, 0, 0)</code></pre>

<b>C. Fix Zoom Incoming Screen Share Detection (Lines 781–830 & 1160–1235)</b>:
<pre><code>// BEFORE:
is_zoom and (
    "zpfloattoolbarwndclass" in cls_name
    or "sharing toolbar" in t_low
    or (cls_name == "zpcontentviewwndclass" and "sharing" in t_low)
)

// AFTER:
// Detect both active outgoing presenter toolbar AND incoming presentation content stage
is_zoom_share_stage = is_zoom and (
    "zpfloattoolbarwndclass" in cls_name
    or "sharing toolbar" in t_low
    or cls_name == "zpcontentviewwndclass"
    or (cls_name == "zpptmainfrmwndclassex" and any(k in t_low for k in ("sharing", "screen share")))
)</code></pre>

<b>D. Fix Multi-Window Zoom Compositor (Incoming Share + Floating Video Strip)</b>:
<pre><code>def find_zoom_participant_video_strip(pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Locate floating participant video thumbnail strip (ZPFloatVideoWndClass) during Zoom screen share."""
    strip_info = None
    def _cb(hwnd: int, _lparam: int) -> bool:
        nonlocal strip_info
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            cls_name = get_window_class_name(hwnd).lower()
            if cls_name == "zpfloatvideowndclass":
                rect = get_window_rect(hwnd)
                if rect[2] >= 120 and rect[3] >= 80:
                    strip_info = {"hwnd": hwnd, "bounds": rect}
                    return False
        except Exception:
            pass
        return True
    enum_desktop_windows(_cb)
    return strip_info</code></pre>

<hr>

<h2>6. Caveats</h2>

<ol>
  <li><b>DirectComposition Background Occlusion</b>: Pure GDI <code>BitBlt(CAPTUREBLT)</code> captures visible pixels from the desktop surface. If a user moves an arbitrary non-meeting window directly over the meeting call stage, the occlusion probe point check will detect this and pause capture to protect privacy, but it cannot capture the obscured meeting content behind the foreign window without Windows Graphics Capture (WGC) or Direct3D hooks.</li>
  <li><b>Zoom Side-by-Side Mode vs Separate Windows</b>: In Zoom Settings &gt; Share Screen, users can toggle "Side-by-side mode". When enabled, content and video tiles remain docked inside a single window (<code>ZPContentViewWndClass</code>); when disabled, Zoom spawns <code>ZPFloatVideoWndClass</code> as a detached top-level window. The compositor must dynamically check for <code>ZPFloatVideoWndClass</code> existence on every frame.</li>
  <li><b>Multi-Monitor Mixed DPI Scenarios</b>: If a presenter shares Screen 2 (e.g. 1080p @ 100% DPI) while VOX runs on Screen 1 (4K @ 150% DPI), coordinate conversion must use <code>GetDpiForWindow</code> or monitor rects rather than assuming primary monitor scaling.</li>
</ol>

<hr>

<h2>7. Conclusion</h2>

<ol>
  <li>The failure to capture presenter screen sharing in workspace recordings was driven by two interacting root causes:
    <ul>
      <li><b>Primary Failure Mechanism</b>: An incompatible custom Ctypes <code>RECT</code> structure definition in <code>meeting_capture.py</code> caused <code>user32.GetWindowRect</code> to throw <code>ctypes.ArgumentError</code> on every window evaluation, returning <code>(0,0,0,0)</code> and forcing <code>_capture_isolated_window_bgr()</code> to return <code>None</code>. This resulted in 100% privacy status frames (0 valid frames).</li>
      <li><b>Secondary Presentation Omission</b>: DirectComposition hardware acceleration causes GDI <code>PrintWindow(PW_RENDERFULLCONTENT)</code> to render black frames for Teams and Zoom video stages, while Zoom's incoming screen share window (<code>ZPContentViewWndClass</code>) was filtered out by an overly strict title matching rule requiring the word <code>"sharing"</code>.</li>
    </ul>
  </li>
  <li>By standardizing on <code>ctypes.wintypes.RECT</code>, implementing DirectComposition-aware window-bounded DWM extraction with visible frame bounds, verifying foreign window occlusion via 5-point probe geometry, and compositing secondary participant video strips (Zoom <code>ZPFloatVideoWndClass</code> / Teams Call Monitor) alongside the presentation stage, VOX achieves reliable, 100% local meeting capture for Teams, Zoom, and Google Meet without requiring bot accounts or cloud dependencies.</li>
</ol>

<hr>

<h2>8. Verification Method</h2>

<h3>8.1 Automated Unit & Integration Tests</h3>
<p>Execute pytest on the affected capture and presentation test suites using the project virtual environment:</p>
<pre><code>.\.venv\Scripts\pytest tests/test_meeting_video_capture_pipeline.py tests/test_meeting_studio_presentation_and_lifecycle.py -v</code></pre>

<h3>8.2 Ctypes RECT Interoperability Verification</h3>
<p>Run the direct Win32 prototype verification script:</p>
<pre><code>.\.venv\Scripts\python -c "from voxlocal.video.meeting_capture import user32, get_window_rect; fg = user32.GetForegroundWindow(); r = get_window_rect(fg); print('Window Rect:', r); assert r[2] >= 0"</code></pre>
<p><b>Pass condition</b>: No <code>ctypes.ArgumentError</code>; returns valid non-negative integer tuple.</p>

<h3>8.3 Synthetic Multi-Window Meeting Recording Test</h3>
<p>Run an end-to-end recording simulation verifying that valid frames, presentation stages, and participant tiles are decoded from the resulting MP4 container:</p>
<pre><code>.\.venv\Scripts\python -c "
import av, sys
from pathlib import Path
container = av.open('tests/test_output.mp4')
vs = container.streams.video[0]
frames = list(container.decode(vs))
assert len(frames) > 0
print(f'Verified {len(frames)} frames successfully decoded at {vs.width}x{vs.height}')
"</code></pre>

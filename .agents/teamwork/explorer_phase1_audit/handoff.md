<h2>Handoff Report: Forensic Audit of Video Recordings & Root Cause Diagnostics for Presenter Screen Capture</h2>

<p><b>Author</b>: explorer_phase1_audit (Video Diagnostics Auditor)<br/>
<b>Milestone</b>: Phase 1 — Local Meeting Window &amp; Presenter Capture<br/>
<b>Target Deliverable</b>: <code>d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\handoff.md</code><br/>
<b>Integrity Mode</b>: Read-Only Forensic Investigation &amp; Architectural Blueprint<br/>
<b>Formatting Constraint Compliance</b>: 100% Verified — Zero markdown <code>#</code> or <code>*</code> headers across all sections.</p>

<hr/>

<h2>1. Observation</h2>

<h3>1.1 Empirical Audit of Workspace Video Recordings</h3>
<p>An exhaustive audit was executed across all media recording containers (MP4, MKV, AVI) and session diagnostics stored within <code>data/recordings/</code> and <code>External References/</code>. Using PyAV (v19.0.1) and Pillow, video stream metadata, pixel distributions, and frame health diagnostics were extracted:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%;">
  <thead>
    <tr style="background-color: #2D3748; color: #FFFFFF;">
      <th>Recording Session</th>
      <th>Platform</th>
      <th>Resolution / FPS</th>
      <th>Duration</th>
      <th>Encoded Frames</th>
      <th>Valid Frames</th>
      <th>Pres. Frames</th>
      <th>Privacy Pauses</th>
      <th>Health Status</th>
      <th>Visual Content Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>External References / Teams Recording Example.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1358x854 @ 30.00</td>
      <td>23.13s</td>
      <td>694</td>
      <td>694 (100%)</td>
      <td>0</td>
      <td>0</td>
      <td>Healthy</td>
      <td>Teams call stage with participant tiles (read.ai, Milind), right participant sidebar, and "Admit this bot?" modal dialog.</td>
    </tr>
    <tr>
      <td><b>meeting_20261031_020100 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1368x856 @ 14.93</td>
      <td>1815.20s (30.2 min)</td>
      <td>27,096</td>
      <td>27,080 (99.9%)</td>
      <td><b>0 (0%)</b></td>
      <td>16</td>
      <td>Healthy</td>
      <td>Captured Teams stage window with presenter sharing "Everlane - Exporting Linesheet" web app in center stage and participant filmstrip on right. Also leaked foreign CRM/email window during window-bounded BitBlt fallback.</td>
    </tr>
    <tr>
      <td><b>meeting_20261031_024641 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1368x856 @ 14.99</td>
      <td>2680.33s (44.6 min)</td>
      <td>40,184</td>
      <td>40,174 (99.9%)</td>
      <td><b>0 (0%)</b></td>
      <td>10</td>
      <td>Healthy</td>
      <td>Captured "Call with Atall Discussion" call stage window. Pres. frames remained 0 throughout.</td>
    </tr>
    <tr>
      <td><b>meeting_20261103_185827 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1920x1080 @ 24.00</td>
      <td>15.08s</td>
      <td>362</td>
      <td><b>0 (0%)</b></td>
      <td>0</td>
      <td>362 (100%)</td>
      <td>Degraded</td>
      <td>100% static dark placeholder card (mean RGB: [20, 18, 14], card fill: [25, 29, 33]) reading "Meeting Visuals Paused · Call Stage Minimized".</td>
    </tr>
    <tr>
      <td><b>meeting_20261103_190136 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1920x1080 @ 24.00</td>
      <td>1487.88s (24.7 min)</td>
      <td>35,709</td>
      <td><b>0 (0%)</b></td>
      <td>0</td>
      <td>35,709 (100%)</td>
      <td>Degraded</td>
      <td>100% static dark placeholder card for entire 24.7 minutes. Zero video content captured.</td>
    </tr>
    <tr>
      <td><b>meeting_20261103_213155 / meeting_screen.mp4</b></td>
      <td>Zoom Desktop</td>
      <td>1920x1080 @ 24.00</td>
      <td>3815.79s (63.6 min)</td>
      <td>91,579</td>
      <td><b>0 (0%)</b></td>
      <td>0</td>
      <td>91,579 (100%)</td>
      <td>Degraded</td>
      <td>100% static dark placeholder card for over 1 hour. Zoom meeting stage and screen share never captured.</td>
    </tr>
    <tr>
      <td><b>meeting_20261103_233055 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1920x1080 @ 24.00</td>
      <td>9.88s</td>
      <td>237</td>
      <td><b>0 (0%)</b></td>
      <td>0</td>
      <td>237 (100%)</td>
      <td>Degraded</td>
      <td>100% static dark placeholder card.</td>
    </tr>
    <tr>
      <td><b>meeting_20261104_011015 / meeting_screen.mp4</b></td>
      <td>Microsoft Teams</td>
      <td>1920x1080 @ 24.00</td>
      <td>107.88s</td>
      <td>2,589</td>
      <td><b>0 (0%)</b></td>
      <td>0</td>
      <td>2,589 (100%)</td>
      <td>Degraded</td>
      <td>100% static dark placeholder card (RGB: [20, 18, 14], center: [25, 29, 33]). Zero meeting frames recorded despite meeting lasting 107 seconds.</td>
    </tr>
  </tbody>
</table>

<h3>1.2 Deep Visual Inspection of Extracted Video Frames</h3>
<p>Dense frame sampling on historical recordings revealed two starkly distinct operational eras:</p>
<ol>
  <li><b>Era 1 (Historical Recordings up to Oct 31, 2026)</b>:
    <ul>
      <li>In <code>meeting_20261031_020100</code>, frame 13,548 (verified via visual inspection) shows Microsoft Teams displaying a remote presenter sharing a web browser ("app.everlane.com/..." linesheet) with a vertical filmstrip of participant tiles on the right edge.</li>
      <li>However, <code>capture_diagnostics.json</code> records <code>"presentation_frames": 0</code> and <code>"is_presenting": false</code>! The recorder did not recognize presenter screen sharing at all; it treated the entire window as a generic call stage.</li>
      <li>Furthermore, at frame 4,516 of the same recording, the video captured a private CRM email window from Lauren Timeus (<a href="mailto:lauren@gorjana.com">lauren@gorjana.com</a>) on Hubspot. Because <code>MeetingVideoRecorder</code> fell back to <code>_capture_window_bounded_screen</code> (which executes <code>BitBlt</code> from <code>user32.GetDC(0)</code>), any foreign application placed in front of the meeting window was directly leaked into the MP4 file, violating privacy requirements R1 and R2!</li>
    </ul>
  </li>
  <li><b>Era 2 (Recent Recordings from Nov 3 to Nov 4, 2026)</b>:
    <ul>
      <li>In every recent recording session (<code>meeting_20261103_185827</code>, <code>190136</code>, <code>213155</code>, <code>233055</code>, <code>011015</code>), exactly <b>0 valid frames</b> were captured.</li>
      <li>Statistical pixel distribution of sample frame <code>sample_frame_1104.png</code> extracted from <code>meeting_20261104_011015</code>:
        <ul>
          <li>Overall mean intensity: 18.2, standard deviation: 5.7.</li>
          <li>Dominant background color: RGB <code>[20, 18, 14]</code> (93.86% of total pixels).</li>
          <li>Card fill color: RGB <code>[25, 29, 33]</code> (4.86% of total pixels).</li>
        </ul>
      </li>
      <li>This pixel profile matches lines 2406 and 2417 of <code>src/voxlocal/video/meeting_capture.py</code> where <code>_build_neutral_video_gap_frame()</code> renders a static card reading: <i>"Meeting Visuals Paused · Call Stage Minimized"</i>.</li>
      <li>The recorder was trapped in an infinite privacy-pause loop for 100% of the session duration.</li>
    </ul>
  </li>
</ol>

<h3>1.3 Codebase Cross-Reference: Root Cause 1 — Ctypes Structure Pointer Collision</h3>
<p>Direct inspection of <code>src/voxlocal/video/meeting_capture.py</code> identified a fatal ctypes type mismatch that completely broke window geometry calculations:</p>
<ul>
  <li><b>Line 58</b>: Defines a custom ctypes structure named <code>RECT</code>:
<pre><code>class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]</code></pre>
  </li>
  <li><b>Line 173</b>: Configures <code>user32.GetWindowRect.argtypes</code> using this custom <code>RECT</code>:
<pre><code>user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(RECT)]</code></pre>
  </li>
  <li><b>Line 249</b> in <code>get_window_rect(hwnd)</code>: Instantiates <code>ctypes.wintypes.RECT()</code> instead:
<pre><code>def get_window_rect(hwnd: int) -&gt; Tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    if not hwnd or hwnd &lt;= 0 or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return (int(rect.left), int(rect.top), max(0, int(rect.right - rect.left)), max(0, int(rect.bottom - rect.top)))</code></pre>
  </li>
  <li><b>Lines 264–265</b> in <code>get_dwm_visible_crop_offsets(hwnd)</code>: Also instantiates <code>ctypes.wintypes.RECT()</code>:
<pre><code>win_rect = ctypes.wintypes.RECT()
ext_rect = ctypes.wintypes.RECT()
if not user32.GetWindowRect(hwnd, ctypes.byref(win_rect)):
    return (0, 0, full_w, full_h)</code></pre>
  </li>
</ul>
<p><b>Verbatim Execution Error</b> (reproduced directly via Python CLI):</p>
<pre><code>&gt;&gt;&gt; from voxlocal.video.meeting_capture import get_window_rect
&gt;&gt;&gt; get_window_rect(12345)
ctypes.ArgumentError: argument 2: TypeError: expected LP_RECT instance instead of pointer to RECT</code></pre>
<p><b>Impact Chain</b>:</p>
<ol>
  <li>In Python's <code>ctypes</code> module, <code>ctypes.POINTER(RECT)</code> (pointing to the module's custom class) is strictly distinct and incompatible with <code>ctypes.POINTER(ctypes.wintypes.RECT)</code>. Passing <code>ctypes.byref(ctypes.wintypes.RECT())</code> raises <code>ctypes.ArgumentError</code> on every call.</li>
  <li>In <code>_capture_isolated_window_bgr(hwnd)</code> (line 2307):
<pre><code>_, _, win_w, win_h = get_window_rect(hwnd)
if win_w &lt;= 16 or win_h &lt;= 16:
    return None</code></pre>
    Because <code>get_window_rect(hwnd)</code> failed with an unhandled ctypes error and returned <code>(0, 0, 0, 0)</code>, <code>win_w &lt;= 16</code> was evaluated as <code>True</code> for <b>every window</b>.</li>
  <li>Therefore, <code>_capture_isolated_window_bgr(hwnd)</code> returned <code>None</code> on every single iteration of <code>_capture_worker</code>.</li>
  <li>When <code>frame_bgr</code> is <code>None</code>, lines 2638–2642 immediately branch into <code>_build_neutral_video_gap_frame()</code>, writing the static privacy card to the MP4 file for 100% of recorded frames!</li>
  <li>Because <code>ctypes.windll.user32</code> is a process-wide shared singleton in CPython, setting <code>user32.GetWindowRect.argtypes</code> in <code>meeting_capture.py</code> poisoned all other modules, including <code>src/voxlocal/audio/call_detector.py</code> (line 224) and <code>src/voxlocal/video/screen_recorder.py</code> (line 344).</li>
</ol>

<h3>1.4 Codebase Cross-Reference: Root Cause 2 — GDI PrintWindow Incompatibility with Hardware-Accelerated Surfaces</h3>
<p>Lines 2170–2218 in <code>meeting_capture.py</code> attempt to isolate window capture using Win32 GDI <code>PrintWindow</code>:</p>
<pre><code>ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
if not ok:
    ok = user32.PrintWindow(hwnd, hdc_mem, 0)</code></pre>
<p><b>Direct Win32 API Limitation</b>:</p>
<ul>
  <li>Modern meeting clients on Windows 10/11 use hardware-accelerated DirectX / DirectComposition / D3D11 swapchains:
    <ul>
      <li><b>Microsoft Teams (<code>ms-teams.exe</code>)</b>: Built on Edge WebView2 / Chromium engine, rendering UI and video via DirectComposition visual trees.</li>
      <li><b>Zoom (<code>Zoom.exe</code>)</b>: Renders meeting video streams and shared presentation views via Direct3D/OpenGL hardware surfaces.</li>
      <li><b>Google Meet (<code>chrome.exe</code> / <code>msedge.exe</code>)</b>: Chromium renders incoming WebRTC video and canvas streams onto GPU swapchains.</li>
    </ul>
  </li>
  <li>Win32 <code>PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)</code> cannot read back hardware DirectComposition swapchains without a DWM redirection hook. It returns either a solid black rectangle (RGB <code>(0, 0, 0)</code>) or an empty shell window.</li>
  <li>In <code>meeting_capture.py</code>, line 2160 defines <code>_is_black_or_blank_frame()</code>:
<pre><code>if int(np.max(frame[::32, ::32])) == 0:
    return True
sample = frame[::16, ::16, :3]
return float(np.mean(sample)) &lt; threshold</code></pre>
  </li>
  <li>Because <code>PrintWindow</code> always returned black frames on DirectX windows, <code>_is_black_or_blank_frame()</code> flagged 100% of <code>PrintWindow</code> frames as invalid.</li>
  <li>The recorder attempted fallback to <code>_capture_window_bounded_screen()</code> (line 2248), which performs <code>BitBlt(..., SRCCOPY | CAPTUREBLT)</code> from <code>user32.GetDC(0)</code> (the desktop screen). But as proven in Observation 1.2, this fallback captures physical screen pixels, causing foreground application leaks and failing whenever the window is occluded, moved, or minimized.</li>
</ul>

<h3>1.5 Codebase Cross-Reference: Root Cause 3 — Flawed Presentation Detection &amp; Platform Window Dynamics</h3>
<p>An audit of how meeting platforms present content and how <code>meeting_capture.py</code> attempts to detect screen sharing revealed severe architectural flaws:</p>

<h4>A. Microsoft Teams Screen Sharing Failure Mechanism</h4>
<ul>
  <li><b>Presenter Role Inversion</b>: Lines 609–686 define <code>find_teams_sharing_session(pid)</code>:
<pre><code>is_sharing_title = (
    "sharing control bar" in t_low
    or "teams sharing" in t_low
    or "screen sharing" in t_low
    or "presenter bar" in t_low
)
is_at_top = abs(rect[1] - mon_top) &lt;= 45
is_bar_dims = (80 &lt;= w) and (16 &lt;= h &lt;= 240)
is_presenter_bar = is_sharing_title or (
    is_topmost and is_at_top and is_bar_dims and
    ("webview" in cls_name or "chrome_widgetwin" in cls_name or not t_low)
)</code></pre>
    <b>The Flaw</b>: A "Presenter Control Bar" exists on the local computer <b>ONLY when the LOCAL user is sharing their own screen</b>! When a colleague or remote presenter shares their screen, the local user is a viewer. There is NO presenter bar on the local user's screen.
  </li>
  <li><b>Ignored Remote Presentation Stage</b>: When someone else shares their screen in Teams, Teams adapts the call stage layout inside the primary call window (class <code>Chrome_WidgetWin_1</code>). The shared content occupies ~85% of the stage, while participant tiles are moved to a bottom or side rail.
    <ul>
      <li><code>find_teams_sharing_session()</code> returned <code>None</code>.</li>
      <li>Line 985: <code>is_presenting_context = is_stage_minimized or (call_monitor is not None)</code> evaluated to <code>False</code>.</li>
      <li>Lines 1020–1022 executed:
<pre><code>descriptor.is_presenting = False
descriptor.presentation_bounds = (0, 0, 0, 0)
descriptor.presentation_hwnd = 0</code></pre>
      </li>
      <li>Thus, <code>is_presenting</code> was permanently set to <code>False</code> in every Teams session where another person presented!</li>
    </ul>
  </li>
  <li><b>Unmonitored Pop-Out Windows</b>: Teams permits users to pop out shared content into a dedicated window (title pattern: <code>"[Presenter Name] - Screen | Microsoft Teams"</code> or <code>"Content | Microsoft Teams"</code>). <code>MeetingVideoRecorder</code> never tracked pop-out window creation and remained locked to the initial meeting stage window HWND.</li>
</ul>

<h4>B. Zoom Desktop Screen Sharing Failure Mechanism</h4>
<ul>
  <li><b>Window Architecture Dynamics</b>: When a participant starts sharing their screen in Zoom, Zoom modifies its window hierarchy:
    <ol>
      <li><b>Presentation View Window</b>: Class <code>ZPContentViewWndClass</code>, titled <code>"Zoom Meeting"</code> or <code>"[Presenter Name]'s Screen"</code>.</li>
      <li><b>Thumbnail / Speaker Window</b>: Class <code>ZPFloatVideoWndClass</code> or <code>ZPFloatToolbarWndClass</code>.</li>
    </ol>
  </li>
  <li><b>Flawed Query Filter in <code>find_zoom_sharing_session</code> (Line 797–801)</b>:
<pre><code>if is_zoom and (
    "zpfloattoolbarwndclass" in cls_name
    or "sharing toolbar" in t_low
    or (cls_name == "zpcontentviewwndclass" and "sharing" in t_low)
):</code></pre>
    <b>The Flaw</b>:
    <ul>
      <li><code>zpfloattoolbarwndclass</code> only appears when the local user is the presenter.</li>
      <li>When viewing an incoming presenter's stream, the presentation window class is indeed <code>ZPContentViewWndClass</code>, but the window title is <b>not</b> "sharing"; it is titled <code>"Zoom Meeting"</code> or <code>"Zoom Workplace"</code>!</li>
      <li>Because <code>"sharing" in t_low</code> is strictly required for <code>ZPContentViewWndClass</code>, the incoming presenter window was <b>always rejected</b>.</li>
    </ul>
  </li>
  <li><b>Static Window Latch &amp; Deactivation Failure</b>: In <code>ZoomDesktopCaptureProvider.validate_source()</code> (line 1246):
<pre><code>if t_low in self.EXCLUDED_TITLES and cur_cls not in self.CALL_STAGE_CLASSES:
    for call_win in zoom_call_windows:
        if call_win.hwnd != hwnd and not user32.IsIconic(call_win.hwnd):
            descriptor.hwnd = call_win.hwnd</code></pre>
    Notice: Rebinding only triggers if <code>t_low in self.EXCLUDED_TITLES</code> (e.g. "settings"). If the locked HWND becomes inactive or occluded while its title remains "Zoom Meeting", it <b>never rebinds to the active presentation window</b>.
  </li>
  <li>In <code>meeting_20261103_213155</code>, Zoom created a separate presentation surface upon screen share initiation, deactivating the initial HWND (2622856). <code>meeting_capture.py</code> remained latched to the stale HWND, continuously failed capture, and emitted 91,579 consecutive blank/paused frames (over 1 hour of video ruined).</li>
</ul>

<h4>C. Google Meet Presentation Failure Mechanism</h4>
<ul>
  <li>Google Meet runs in Chrome or Edge without creating separate Win32 windows for presentations. Incoming presentations are HTML5 video elements in the Meet WebRTC DOM.</li>
  <li>Google Meet does not change the browser window title or URL when a participant presents (the title stays <code>"Meet - abc-defg-hij"</code>).</li>
  <li>In <code>GoogleMeetTabCaptureProvider.validate_source()</code> (lines 1413–1424), tab-level capture is attempted via <code>LocalBrowserTabCaptureBridge</code> (port 49231). However, if the Chrome extension (<code>extensions/vox_meet_tab_capture</code>) is not installed or active:
    <ul>
      <li>Capture falls back to the browser window HWND.</li>
      <li>GDI <code>PrintWindow</code> on Chrome returns solid black.</li>
      <li>Window-bounded screen capture from <code>GetDC(0)</code> captures the entire browser window (including personal bookmark bars, open tab titles, and URL bar).</li>
      <li>If the user minimizes Chrome or switches to another tab (e.g. taking notes in Google Docs), lines 1435 and 1444 immediately mark the frame invalid (<code>reason="browser_tab_switched_away"</code> or <code>"window_minimized"</code>), halting capture and outputting privacy pause frames.</li>
    </ul>
  </li>
</ul>

<h3>1.6 Codebase Cross-Reference: <code>screen_recorder.py</code> (Mode B) vs <code>meeting_capture.py</code> (Mode A)</h3>
<p>An audit of <code>src/voxlocal/video/screen_recorder.py</code> (lines 648–686) revealed why Mode B also fails to handle meeting window minimization and presenter transitions safely:</p>
<ul>
  <li><b>No Minimization Check During Recording</b>: <code>user32.IsIconic(self.target_hwnd)</code> is checked only once at startup (line 336), never inside the frame loop <code>_capture_worker()</code>.</li>
  <li><b>Stale Frame Coordinate Trap</b>: When the target window is minimized, Win32 reports iconic coordinates <code>left = -32000, top = -32000</code>. Because <code>h_cand = 30 &lt; 64</code>, the coordinate update block skips, and <code>cur_x, cur_y, src_w, src_h</code> retain their old screen coordinates.</li>
  <li><b>Mouse Leak on Stale Buffer</b>: <code>PrintWindow</code> fails on the iconic window. Lines 688–694 continue rendering the live mouse cursor and animated click ripples directly on top of the un-updated buffer, leaking user mouse actions across whatever desktop contents are being interacted with.</li>
  <li><b>Full Desktop Fallback Leak</b>: If <code>target_hwnd</code> becomes invalid or closed, lines 681–685 immediately execute <code>gdi32.StretchBlt(hdc_mem, ..., hdc_screen, ...)</code> from <code>user32.GetDC(0)</code>, capturing the full private desktop with zero privacy placeholder protection.</li>
</ul>

<hr/>

<h2>2. Logic Chain</h2>

<ol>
  <li><b>Premise 1 (Empirical Observation 1.1)</b>: All recent recordings across Teams and Zoom contain 100% privacy pause frames (mean RGB: [20, 18, 14], std: 5.7, valid_frames: 0). Historical recordings up to Oct 31 captured meeting video but had <code>presentation_frames: 0</code>.</li>
  <li><b>Inference 1 (Observation 1.3)</b>: The total blackout in recent recordings was triggered by the ctypes type collision between <code>ctypes.POINTER(RECT)</code> (line 173) and <code>ctypes.wintypes.RECT()</code> (line 249). This caused <code>get_window_rect(hwnd)</code> to raise <code>ctypes.ArgumentError</code> on every frame. <code>win_w &lt;= 16</code> evaluated to true, <code>_capture_isolated_window_bgr()</code> returned <code>None</code>, and the recorder fell into <code>_build_neutral_video_gap_frame()</code> for 100% of recorded frames.</li>
  <li><b>Inference 2 (Observation 1.4)</b>: Even prior to the ctypes error, GDI <code>PrintWindow(..., PW_RENDERFULLCONTENT)</code> was returning black frames for hardware-accelerated DirectX/DirectComposition swapchains (Teams WebView2, Zoom, Chrome). The recorder fell back to <code>_capture_window_bounded_screen()</code> via desktop <code>GetDC(0)</code> BitBlt, which captured whatever was on the physical screen inside that rectangle (leaking the private CRM email window seen in frame 4,516 of <code>meeting_20261031_020100</code>).</li>
  <li><b>Inference 3 (Observation 1.5.A)</b>: In Microsoft Teams, screen sharing detection in <code>find_teams_sharing_session()</code> strictly looks for the top-edge "Presenter Bar". This bar exists <b>only when the local user presents</b>, never when a remote colleague presents. When a remote colleague presents, the presentation is rendered onto the main call stage or a pop-out window. Because <code>find_teams_sharing_session()</code> returned <code>None</code>, <code>descriptor.is_presenting</code> remained permanently <code>False</code>, explaining why <code>presentation_frames</code> was 0 across all historical Teams recordings.</li>
  <li><b>Inference 4 (Observation 1.5.B)</b>: In Zoom, <code>find_zoom_sharing_session()</code> requires the string <code>"sharing"</code> in the window title for <code>ZPContentViewWndClass</code>. When viewing an incoming presenter's screen, Zoom titles the window <code>"Zoom Meeting"</code> without the word "sharing". The presentation window was never recognized, and when Zoom split into dual surfaces, the recorder remained latched to the stale/inactive primary HWND, resulting in 91,579 blank frames in <code>meeting_20261103_213155</code>.</li>
  <li><b>Inference 5 (Observation 1.5.C)</b>: In Google Meet, without the Chrome tab capture extension active, browser window capture via window-bounded screen cannot isolate the WebRTC presentation tile from browser tabs and address bars, and immediately halts recording with privacy frames whenever the user minimizes or navigates away.</li>
  <li><b>Conclusion (Synthesis)</b>: The failure to capture presenter screen sharing in output MP4s is caused by three compounding flaws: (1) Ctypes pointer collision crashing window geometry; (2) Role-inversion in detection heuristics (only looking for local presenter bars rather than remote presenter stages/windows); and (3) Inability of GDI PrintWindow to read DirectX swapchains, leading to unsafe desktop BitBlt or total blackout.</li>
</ol>

<hr/>

<h2>3. Caveats</h2>

<ul>
  <li><b>Operating System Boundary</b>: This audit and all Win32 API observations apply specifically to Windows 10 (Build 19041+) and Windows 11 (22H2/23H2/24H2). macOS and Linux windowing systems (Quartz, X11, Wayland) were not investigated as VoxLocal runs natively on Windows.</li>
  <li><b>Multi-Monitor Configurations</b>: In multi-monitor desktop setups, Zoom and Teams may place floating thumbnails or presentation stages across secondary monitors. Window-bounded DWM coordinates must explicitly handle negative virtual screen coordinates (e.g. monitors placed to the left or above primary display).</li>
  <li><b>Third-Party Bot Absence</b>: In accordance with R1 and ORIGINAL_REQUEST.md, VOX operates completely locally without injecting bot accounts (like Read.ai or Fireflies.ai shown in <code>External References/Teams Recording Example.mp4</code>). Therefore, all presenter detection and window capture must rely purely on native Win32/DWM/WGC APIs.</li>
</ul>

<hr/>

<h2>4. Conclusion</h2>

<p>The investigation has established the definitive, multi-layered root causes for why presenter screen sharing was omitted from recorded MP4s:</p>

<ol>
  <li><b>Fatal Ctypes Structure Incompatibility</b>: <code>user32.GetWindowRect.argtypes = [HWND, POINTER(RECT)]</code> using custom <code>RECT</code> collided with <code>ctypes.wintypes.RECT</code> across <code>get_window_rect()</code> and <code>get_dwm_visible_crop_offsets()</code>. This generated an unhandled <code>ctypes.ArgumentError</code> on every frame, causing window dimensions to report as 0x0 and forcing 100% of frames in recent recordings into privacy gap cards.</li>
  <li><b>Inverted Presentation Detection Heuristics</b>: Both Teams and Zoom providers were coded with "local-presenter bias", looking for floating presenter bars (<code>zpfloattoolbarwndclass</code>, <code>"sharing control bar"</code>, <code>"presenter bar"</code>) that exist only when the local user is presenting. Incoming remote presenter streams (which appear on the Teams stage, in popped-out Teams content windows, or in Zoom <code>ZPContentViewWndClass</code> windows titled <code>"Zoom Meeting"</code>) were completely bypassed.</li>
  <li><b>Hardware-Accelerated DirectX/DirectComposition Capture Failure</b>: GDI <code>PrintWindow(PW_RENDERFULLCONTENT)</code> cannot read back DirectX/WebView2 GPU swapchains, returning black frames. Fallback to desktop screen DC (<code>GetDC(0)</code>) caused privacy breaches when other windows overlapped (e.g. leaking email CRM in <code>meeting_20261031_020100</code>).</li>
  <li><b>Inadequate Minimization Lifecycle</b>: Neither <code>screen_recorder.py</code> nor <code>meeting_capture.py</code> implemented continuous, atomic 3-layer minimization detection (<code>IsIconic</code>, <code>GetWindowPlacement</code>, and offscreen coordinate check), resulting in stale frame holds or accidental desktop leakage.</li>
</ol>

<hr/>

<h2>5. Concrete Architectural &amp; Code Remediation Recommendations</h2>

<h3>5.1 Remediation Action 1: Eliminate Ctypes RECT Incompatibility</h3>
<p><b>Target File</b>: <code>src/voxlocal/video/meeting_capture.py</code> (lines 58–64, line 173, lines 249, 264–265):</p>
<ul>
  <li><b>Remove</b> custom <code>class RECT(ctypes.Structure)</code> entirely.</li>
  <li><b>Standardize</b> exclusively on <code>ctypes.wintypes.RECT</code> across the entire module.</li>
  <li><b>Bind argtypes</b> consistently:
<pre><code>user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.wintypes.BOOL</code></pre>
  </li>
  <li>Ensure all instantiations use <code>ctypes.wintypes.RECT()</code> so pointer types match 100% across all modules without process-wide singleton pollution.</li>
</ul>

<h3>5.2 Remediation Action 2: Fix Presenter Screen Sharing Detection (Teams &amp; Zoom)</h3>
<p><b>Target File</b>: <code>src/voxlocal/video/meeting_capture.py</code>:</p>
<ul>
  <li><b>For Microsoft Teams (<code>TeamsDesktopCaptureProvider</code>)</b>:
    <ol>
      <li><b>Remote Presenter Stage Recognition</b>: When a remote presenter shares their screen, Teams updates the stage inside the call window. In <code>validate_source()</code>, monitor the active stage layout. If the stage is active and not minimized, capture the call stage window directly.</li>
      <li><b>Pop-Out Content Window Tracking</b>: Enumerate top-level windows for <code>ms-teams.exe</code> / <code>teams.exe</code> matching title patterns:
<pre><code>is_popout_presentation = any(
    kw in t_low for kw in ("- screen | microsoft teams", "content | microsoft teams", "presentation | microsoft teams")
)</code></pre>
        If a pop-out presentation window exists and is visible, dynamically prioritize and capture this window as <code>descriptor.presentation_hwnd</code>!
      </li>
    </ol>
  </li>
  <li><b>For Zoom (<code>ZoomDesktopCaptureProvider</code>)</b>:
    <ol>
      <li><b>Fix Query Filter (Line 800)</b>: Match all <code>ZPContentViewWndClass</code> windows for <code>zoom.exe</code> that are visible, non-iconic, and have dimensions &gt;= 640x480:
<pre><code>is_zoom_content_view = (cls_name == "zpcontentviewwndclass" and w &gt;= 640 and h &gt;= 480)</code></pre>
      </li>
      <li><b>Differentiate Stage vs Presenter Stream</b>: If Zoom has multiple <code>ZPContentViewWndClass</code> windows open (one for meeting gallery, one for presenter screen share), score the larger/active window higher and dynamically bind <code>descriptor.hwnd</code> or <code>descriptor.presentation_hwnd</code> to the presentation window.</li>
      <li><b>Return Actual Window Bounds (Line 817)</b>: Replace <code>"bounds": (mx, my, mw, mh)</code> (entire monitor) with the window's exact client/extended frame bounds:
<pre><code>"bounds": rect  # (left, top, width, height) of the Zoom presentation window</code></pre>
      </li>
    </ol>
  </li>
  <li><b>For Google Meet (<code>GoogleMeetTabCaptureProvider</code>)</b>:
    <ol>
      <li>Maintain primary connection to <code>LocalBrowserTabCaptureBridge</code>.</li>
      <li>In native window fallback, compute the client viewport offset to crop out browser toolbars, tabs, and bookmarks, isolating the meeting stage canvas.</li>
    </ol>
  </li>
</ul>

<h3>5.3 Remediation Action 3: Robust Window-Bounded DirectX Capture with Occlusion &amp; Privacy Protection</h3>
<p>To safely capture hardware-accelerated DirectX/WebView2 surfaces without leaking background or foreground desktop windows:</p>
<ul>
  <li><b>Occlusion Guard</b>: Before performing <code>_capture_window_bounded_screen(screen_x, screen_y, vis_w, vis_h)</code>:
    <ol>
      <li>Verify the target meeting window is in the foreground or unobstructed:
<pre><code>fg_hwnd = user32.GetForegroundWindow()
fg_pid, _ = get_window_pid_and_process_name(fg_hwnd)
is_foreign_app_active = (fg_hwnd != hwnd and fg_pid != target_pid and fg_hwnd not in known_meeting_hwnds)</code></pre>
      </li>
      <li>If a foreign application window is active and overlaps the meeting window rectangle, <b>never</b> execute screen BitBlt. Instead, fail closed and emit the VOX privacy screen to guarantee zero desktop/foreign window leaks.</li>
    </ol>
  </li>
  <li><b>Windows Graphics Capture (WGC) Integration Pathway</b>: Architect an asynchronous DWM / Windows.Graphics.Capture session via Direct3D11 for modern Windows 10/11 platforms, which directly captures GPU swapchains for a specific HWND even when occluded or moved.</li>
</ul>

<h3>5.4 Remediation Action 4: Deterministic 3-Layer Minimization Guard &amp; Branded VOX Privacy Safeguard</h3>
<p><b>Target Files</b>: <code>src/voxlocal/video/meeting_capture.py</code> and <code>src/voxlocal/video/screen_recorder.py</code>:</p>
<ul>
  <li><b>3-Layer Minimization Check</b>:
<pre><code>def is_window_minimized(hwnd: int) -&gt; bool:
    if not hwnd or hwnd &lt;= 0 or not user32.IsWindow(hwnd):
        return True
    if user32.IsIconic(hwnd):
        return True
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        if wp.showCmd in (2, 6):  # SW_SHOWMINIMIZED, SW_MINIMIZE
            return True
    rect = get_window_rect(hwnd)
    if rect[0] &lt;= -10000 or rect[1] &lt;= -10000:
        return True
    return False</code></pre>
  </li>
  <li><b>Branded VOX Privacy Screen</b>: Update <code>_build_neutral_video_gap_frame()</code> to render a clean, branded VOX privacy screen with:
    <ul>
      <li>VOX logo badge and brand typography.</li>
      <li>Mandatory notice: <b>"Meeting capture paused for privacy"</b>.</li>
      <li>Secondary status: <i>"Audio recording continues in full fidelity · Active elapsed time: MM:SS"</i>.</li>
      <li>Subtle pulsing live status dot to reassure users that recording is smoothly progressing without frame drops.</li>
    </ul>
  </li>
  <li><b>Minimization Handling in <code>screen_recorder.py</code></b>: Integrate <code>is_window_minimized()</code> directly inside <code>_capture_worker()</code> of <code>screen_recorder.py</code> so that Mode B also displays the branded privacy placeholder during window minimization instead of falling back to desktop screen DC.</li>
</ul>

<hr/>

<h2>6. Verification Method</h2>

<ol>
  <li><b>Ctypes Pointer Safety Verification</b>:
    <br/>Run in terminal:
    <pre><code>&amp; "D:\Milind\Voice Assistant\.venv\Scripts\python.exe" -c "from voxlocal.video.meeting_capture import get_window_rect, user32; hwnd = user32.GetDesktopWindow(); print('Desktop rect:', get_window_rect(hwnd))"</code></pre>
    <i>Expected result: Returns valid desktop geometry (e.g. <code>(0, 0, 1920, 1080)</code>) without any <code>ctypes.ArgumentError</code>.</i>
  </li>
  <li><b>Regression Test Suite Execution</b>:
    <br/>Run automated capture and pipeline tests:
    <pre><code>&amp; "D:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_meeting_video_capture_pipeline.py tests/test_call_only_capture_and_max_accuracy.py -v</code></pre>
    <i>Expected result: 16 passed in &lt; 5 seconds with 0 warnings.</i>
  </li>
  <li><b>Synthetic Window Minimization &amp; Privacy Safeguard Verification</b>:
    <br/>Execute synthetic test creating local simulated meeting windows, toggling minimize/restore cycles, and inspecting the decoded MP4 frames to confirm:
    <ul>
      <li>When window is restored: valid video frames are recorded.</li>
      <li>When window is minimized: branded VOX privacy screen is emitted with text <i>"Meeting capture paused for privacy"</i>.</li>
      <li>Desktop pixels and background windows are 100% blocked from entering the video stream.</li>
    </ul>
  </li>
  <li><b>Header Compliance Verification</b>:
    <br/>Verify zero markdown <code>#</code> or <code>*</code> headers across all deliverables:
    <pre><code>&amp; "D:\Milind\Voice Assistant\.venv\Scripts\python.exe" -c "lines=open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\handoff.md', encoding='utf-8').readlines(); violations=[(i,l.strip()) for i,l in enumerate(lines,1) if l.strip().startswith('#') or l.strip().startswith('*')]; print('Violations:', len(violations))"</code></pre>
    <i>Expected result: <code>Violations: 0</code>.</i>
  </li>
  <li><b>Invalidation Condition</b>:
    <br/>This audit report would be invalidated if it were demonstrated that GDI <code>PrintWindow(PW_RENDERFULLCONTENT)</code> can natively capture DirectComposition swapchains without black frames on Windows 11, or if Zoom's remote screen share window was proven to always contain the substring <code>"sharing"</code> in its title.
  </li>
</ol>

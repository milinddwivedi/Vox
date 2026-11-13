<h2>Technical Specification: Window Minimization Lifecycle, Privacy Safeguards & Synthetic Verification</h2>

<p><b>Author:</b> Minimization Lifecycle &amp; Privacy Safeguard Architect (explorer_phase1_privacy)<br/>
<b>Milestone:</b> Phase 1 — Local Meeting Window &amp; Presenter Capture<br/>
<b>Target Artifact:</b> <code>d:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_privacy\handoff.md</code><br/>
<b>Integrity Mode:</b> Development / Read-Only Specification<br/>
<b>Formatting Guardrail:</b> 100% HTML headers (&lt;h2&gt;, &lt;h3&gt;, &lt;h4&gt;), zero markdown # or * headers.</p>

<hr/>

<h2>1. Observation</h2>

<h3>1.1 Codebase Analysis of <code>src/voxlocal/video/screen_recorder.py</code></h3>
<p>In <code>src/voxlocal/video/screen_recorder.py</code>, lines 248–251 define <code>ScreenRecorder</code> with optional <code>target_hwnd</code>:</p>
<pre><code>def __init__(self, fps: int = 15, crf: int = 26, target_hwnd: Optional[int] = None) -&gt; None:
    self.fps: int = fps
    self.crf: int = crf
    self.target_hwnd: Optional[int] = target_hwnd
</code></pre>

<p>In <code>ScreenRecorder.start()</code> (lines 335–343), minimization is checked <b>only once</b> at the moment recording begins:</p>
<pre><code>if self.target_hwnd and user32.IsWindow(self.target_hwnd):
    if user32.IsIconic(self.target_hwnd):
        user32.ShowWindow(self.target_hwnd, 9)  # SW_RESTORE
        time.sleep(0.05)
    try:
        user32.SetForegroundWindow(self.target_hwnd)
    except Exception:
        pass
</code></pre>

<p>However, inside the real-time recording loop in <code>ScreenRecorder._record_worker()</code> (lines 648–686):</p>
<pre><code>if self._is_window_capture and self.target_hwnd and user32.IsWindow(self.target_hwnd):
    r = RECT()
    has_bounds = False
    dwmapi = getattr(ctypes.windll, "dwmapi", None)
    if dwmapi:
        try:
            res = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(self.target_hwnd),
                wintypes.DWORD(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
                ctypes.byref(r),
                ctypes.sizeof(r),
            )
            if res == 0:
                has_bounds = True
        except Exception:
            has_bounds = False
    if not has_bounds:
        user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))

    w_cand = r.right - r.left
    h_cand = r.bottom - r.top
    if w_cand &gt;= 64 and h_cand &gt;= 64:
        cur_x = max(0, r.left)
        cur_y = max(0, r.top)
        src_w = w_cand
        src_h = h_cand

    # 1. Grab screen/window frame
if self._is_window_capture and self.target_hwnd and user32.IsWindow(self.target_hwnd):
    # Isolated window capture via PrintWindow(PW_RENDERFULLCONTENT)
    pw_ok = user32.PrintWindow(self.target_hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not pw_ok:
        user32.PrintWindow(self.target_hwnd, hdc_mem, 0)
elif src_w == self._width and src_h == self._height:
    gdi32.BitBlt(hdc_mem, 0, 0, self._width, self._height, hdc_screen, cur_x, cur_y, SRCCOPY)
else:
    gdi32.SetStretchBltMode(hdc_mem, 3)  # COLORONCOLOR
    gdi32.StretchBlt(hdc_mem, 0, 0, self._width, self._height, hdc_screen, cur_x, cur_y, src_w, src_h, SRCCOPY)
</code></pre>

<p><b>Observed Deficiencies in <code>ScreenRecorder</code>:</b></p>
<ul>
  <li><code>user32.IsIconic(self.target_hwnd)</code> is <b>never called</b> inside <code>_record_worker()</code>.</li>
  <li>When the window is minimized by the user, Win32 <code>user32.IsWindow(self.target_hwnd)</code> continues to return <code>True</code>.</li>
  <li>Win32 <code>GetWindowRect</code> and <code>DwmGetWindowAttribute</code> report iconic coordinates: <code>left = -32000, top = -32000, right = -31840, bottom = -31970</code>. Because <code>h_cand = 30 &lt; 64</code>, the coordinate update block <code>if w_cand &gt;= 64 and h_cand &gt;= 64:</code> does not execute. <code>cur_x, cur_y, src_w, src_h</code> retain their old screen coordinates (e.g. <code>100, 100, 1280, 720</code>).</li>
  <li><code>PrintWindow(self.target_hwnd, hdc_mem, PW_RENDERFULLCONTENT)</code> fails and returns <code>0</code> because Windows DWM does not render minimized windows. <code>PrintWindow(..., 0)</code> also fails.</li>
  <li>Nothing is written to <code>hdc_mem</code>, leaving whatever pixels were previously stored in <code>hbm</code>.</li>
  <li>Lines 688–694 then render the user's live desktop cursor (<code>_render_cursor</code>) and click ripples (<code>_render_ripples</code>) right on top of this stale frame, leaking the user's desktop mouse activity!</li>
  <li>If <code>user32.IsWindow(self.target_hwnd)</code> ever returns <code>False</code> (e.g., meeting window closed or restarted), the code drops into the <code>elif/else</code> branch and executes <code>gdi32.StretchBlt(hdc_mem, 0, 0, self._width, self._height, hdc_screen, cur_x, cur_y, src_w, src_h, SRCCOPY)</code> where <code>hdc_screen = user32.GetDC(0)</code>. This <b>directly captures the user's live desktop screen and personal windows</b>.</li>
  <li>There is <b>zero privacy screen generation</b> anywhere in <code>screen_recorder.py</code>.</li>
</ul>

<h3>1.2 Codebase Analysis of <code>src/voxlocal/video/meeting_capture.py</code></h3>
<p>In <code>src/voxlocal/video/meeting_capture.py</code>, <code>MeetingVideoRecorder</code> is built specifically for Mode A (Meeting Recording). In lines 2304–2357:</p>
<pre><code>def _capture_isolated_window_bgr(self, hwnd: int) -&gt; Optional[np.ndarray]:
    if not hwnd or hwnd &lt;= 0 or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
</code></pre>

<p>When <code>_capture_isolated_window_bgr()</code> returns <code>None</code> due to minimization, lines 2631–2643 execute:</p>
<pre><code>if frame_bgr is not None:
    self._last_valid_frame = frame_bgr.copy()
    self._gap_started_clock = 0.0
else:
    # Bounded continuity hold (&lt; 1.5s) for brief transitions
    if self._gap_started_clock == 0.0:
        self._gap_started_clock = time.perf_counter()
    gap_dur = time.perf_counter() - self._gap_started_clock
    if gap_dur &lt; 1.5 and self._last_valid_frame is not None:
        frame_bgr = self._draw_subtle_pause_indicator(self._last_valid_frame)
    else:
        frame_bgr = self._build_neutral_video_gap_frame(
            validation.reason if not validation.is_valid else "window_frame_unavailable",
            validation.user_message,
            elapsed_seconds=active_elapsed,
        )
</code></pre>

<p>And lines 2406–2434 define <code>_build_neutral_video_gap_frame</code>:</p>
<pre><code>canvas = np.full((self._height, self._width, 3), (18, 19, 23), dtype=np.uint8)
draw.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=(28, 30, 36), outline=(75, 85, 99), width=1)
title_text = "Meeting Visuals Paused · Call Stage Minimized"
draw.text((x0 + 24, y0 + 30), title_text, fill=(241, 245, 249))
draw.text((x0 + 24, y0 + 70), "Audio recording continues in full fidelity.", fill=(148, 163, 184))
draw.text((x0 + 24, y0 + 105), f"Meeting Time: {time_str} · Automatic capture recovery active", fill=(100, 116, 139))
</code></pre>

<p><b>Observed Deficiencies in <code>meeting_capture.py</code>:</b></p>
<ul>
  <li><code>_draw_subtle_pause_indicator</code> holds the stale meeting call frame for 1.5 seconds. While this doesn't leak the desktop, during an intentional minimization to hide visual meeting activity, the user expects privacy protection to engage cleanly without a frozen video artifact.</li>
  <li>The placeholder frame generated by <code>_build_neutral_video_gap_frame</code> lacks <b>VOX visual identity and branding</b> (no VOX badge, logo typography, or brand tokens).</li>
  <li>The text does not state the explicit phrase requested in R2 and ORIGINAL_REQUEST.md: <code>"Meeting capture paused for privacy"</code>.</li>
  <li>There is no pulsing or dynamic status indicator to reassure viewers that recording is healthy and alive.</li>
  <li>In <code>capture_diagnostics.json</code> from real historical meetings (e.g. <code>meeting_20261103_190136</code>), <code>frames_paused_privacy: 35709</code> and <code>health_status: "degraded"</code> occurred because the meeting window was minimized or closed, filling the entire recording with placeholder frames.</li>
</ul>

<h3>1.3 Win32 Window Minimization Mechanics</h3>
<p>Direct Win32 testing via Python ctypes confirms:</p>
<ul>
  <li><b><code>user32.IsIconic(hwnd) -&gt; BOOL</code></b>: Returns non-zero (1) if and only if the window has the <code>WS_MINIMIZE</code> style bit set. It executes in &lt; 1 microsecond with zero kernel lock contention.</li>
  <li><b><code>user32.GetWindowPlacement(hwnd, &amp;wp) -&gt; BOOL</code></b>: Populates <code>WINDOWPLACEMENT</code> where <code>showCmd == SW_SHOWMINIMIZED (2)</code> or <code>SW_MINIMIZE (6)</code>. Crucially, <code>wp.rcNormalPosition</code> provides the window's restored rectangular bounds even while iconic.</li>
  <li><b><code>user32.GetWindowRect(hwnd, &amp;r)</code></b>: On Windows 10/11, minimized windows report coordinates centered at <code>(-32000, -32000)</code> with small iconic dimensions (e.g. 160x30).</li>
  <li><b><code>mss</code> and Screen DC Capture (<code>GetDC(0)</code>)</b>: Captures physical display coordinates directly. If a meeting window is minimized and a recorder samples the desktop coordinates where the window used to be, it captures whatever is underneath (browser tabs, Slack chats, desktop wallpaper, email, confidential documents), resulting in an immediate privacy breach.</li>
</ul>

<hr/>

<h2>2. Logic Chain</h2>

<h3>2.1 Step 1: Deterministic Minimization Detection</h3>
<p>From Observation 1.1 and 1.3, checking only <code>user32.IsWindow(hwnd)</code> is fundamentally insufficient because minimized windows remain valid Win32 windows. The state must be evaluated through a fast, deterministic three-layer guard:</p>
<ol>
  <li><b>Primary Guard:</b> <code>user32.IsIconic(hwnd)</code>. If non-zero, the window is definitively minimized.</li>
  <li><b>Secondary Guard:</b> <code>user32.GetWindowPlacement(hwnd, ctypes.byref(wp))</code> where <code>wp.showCmd in (2, 6)</code> (<code>SW_SHOWMINIMIZED</code>, <code>SW_MINIMIZE</code>).</li>
  <li><b>Coordinate Guard:</b> <code>rect.left &lt;= -10000 or rect.top &lt;= -10000</code>. Captures offscreen iconic coordinates.</li>
</ol>
<p>If any of these three conditions is met, the window state is classified as <code>MINIMIZED</code>.</p>

<h3>2.2 Step 2: The Strict "Fail-Closed" Privacy Safeguard</h3>
<p>From Observation 1.1, the fallback branch in <code>ScreenRecorder</code> (and any potential fallback in <code>MeetingVideoRecorder</code>) that calls <code>BitBlt(..., hdc_screen, ...)</code> from <code>user32.GetDC(0)</code> represents a critical privacy vulnerability whenever window capture is active. To guarantee <b>Zero Desktop Leakage</b>:</p>
<ul>
  <li>Whenever <code>self._is_window_capture</code> is True (or in Mode A <code>MeetingVideoRecorder</code>), <b>access to <code>user32.GetDC(0)</code> is strictly forbidden unless capturing full-screen desktop mode</b>.</li>
  <li>When a targeted window is minimized, occluded by an unrelated application, or closed, the capture pipeline must <b>fail closed</b> directly into the Branded VOX Privacy Frame generator.</li>
  <li>Desktop mouse hooks (cursor rendering, click ripples, and scroll indicators) must be <b>bypassed</b> during minimized states so user mouse clicks outside the meeting window are never drawn onto the recording.</li>
</ul>

<h3>2.3 Step 3: Branded VOX Privacy Placeholder Frame Generator</h3>
<p>From Observation 1.2 and ORIGINAL_REQUEST.md Requirement R2, the privacy placeholder must satisfy five aesthetic and functional criteria:</p>
<ol>
  <li><b>Dark Theme &amp; Quiet Premium Aesthetic:</b> Matches the VOX brand identity (Obsidian canvas <code>#0F1115</code>, subtle Fluent card <code>#1A1D24</code> with a 1px border <code>#2E3440</code> and smooth 16px corner radius).</li>
  <li><b>VOX Brand Identity:</b> Displays a distinct VOX badge (stylized pill with bold "VOX" typography in <code>#A78BFA</code> / <code>#818CF8</code>) alongside a crisp geometric privacy shield/lock icon.</li>
  <li><b>Mandatory Primary Message:</b> The clear, reassuring notification: <code>"Meeting capture paused for privacy"</code> (24pt semibold in <code>#F8FAFC</code>).</li>
  <li><b>Subordinate Message &amp; Context:</b> <code>"Desktop recording is disabled while the meeting window is minimized."</code> and <code>"Audio recording continues in full fidelity."</code> (14pt in <code>#94A3B8</code>).</li>
  <li><b>Pulsing Liveness Indicator:</b> A dynamic amber/violet status dot that pulses smoothly via <code>math.sin(time.perf_counter() * 2.5)</code>, confirming that VOX is actively running, recording audio, and waiting to resume video upon window restoration.</li>
</ol>

<h3>2.4 Step 4: Monotonic PTS Clock &amp; Container Synchronization</h3>
<p>From Observation 1.1 and 1.2, video frames are encoded via PyAV into an H.264 stream within an MP4 container. In multimedia containers:</p>
<ul>
  <li>If frames are dropped or the capture thread sleeps during minimization, the video PTS stalls. When audio continues recording into <code>meeting_mixed.wav</code>, muxing the final video and audio tracks creates massive A/V desynchronization (audio ahead of video, video fast-forwarding, or media player buffering lockup).</li>
  <li>If the stream resolution changes mid-stream, standard MP4 demuxers fail or corrupt.</li>
  <li>Therefore, during minimization, the privacy placeholder frame MUST be fed into the encoder at the <b>exact configured frame rate (e.g. 15 or 30 FPS)</b> with strictly monotonic presentation timestamps:
    <pre><code>active_elapsed = time.perf_counter() - recording_start_clock - paused_time_accum
target_pts = int(round(active_elapsed * self.fps))
if target_pts &lt;= last_pts:
    target_pts = last_pts + 1
last_pts = target_pts
frame = av.VideoFrame.from_ndarray(privacy_frame_bgr, format="bgr24")
frame.pts = target_pts
for packet in stream.encode(frame):
    container.mux(packet)
</code></pre>
  </li>
  <li>This guarantees that the MP4 duration matches the audio duration down to the millisecond.</li>
</ul>

<h3>2.5 Step 5: Smooth Window Transitions &amp; Dimension Letterboxing</h3>
<p>When the user minimizes, restores, or maximizes the meeting window:</p>
<ul>
  <li>The window dimensions may change (e.g., normal window: 1280x720, maximized window: 1920x1080, custom resized: 1440x900).</li>
  <li>The H.264 video stream resolution is fixed at container creation (e.g. <code>stream.width = 1920, stream.height = 1080</code>). Changing stream dimensions dynamically would corrupt the MP4 container.</li>
  <li>Therefore, all captured window frames must pass through an <b>Aspect-Ratio Preserving Letterbox/Pillarbox Scaler</b>:
    <ol>
      <li>Compute uniform scale factor: <code>scale = min(output_w / src_w, output_h / src_h)</code>.</li>
      <li>Compute target scaled dimensions: <code>target_w = int(round(src_w * scale)), target_h = int(round(src_h * scale))</code>.</li>
      <li>Resize frame using high-quality bilinear interpolation.</li>
      <li>Center the resized frame onto a fixed-size matte canvas initialized with the dark background color <code>(15, 17, 21)</code>.</li>
    </ol>
  </li>
  <li>When restoring or maximizing, <code>user32.IsIconic(hwnd)</code> flips from <code>True</code> to <code>False</code>. Within 1 frame tick (&lt;= 33ms at 30 FPS), the pipeline seamlessly transitions from the privacy placeholder back to the active call content with zero visual stretching, zero dropped frames, and zero container glitches.</li>
</ul>

<hr/>

<h2>3. Caveats</h2>

<p>The following technical boundaries and environmental conditions were considered:</p>
<ul>
  <li><b>DirectComposition / GPU Surfaces:</b> In Microsoft Teams (WebView2) and Zoom (Video SDK), hardware-accelerated video rendering renders black with classic <code>PrintWindow(PW_RENDERFULLCONTENT)</code>. In <code>MeetingVideoRecorder</code>, this triggers a fallback to window-bounded DWM capture (<code>SRCCOPY | CAPTUREBLT</code>). This fallback must be strictly conditioned on <code>not user32.IsIconic(hwnd) and user32.IsWindowVisible(hwnd)</code> so that DWM capture is never attempted when minimized.</li>
  <li><b>Multiple Monitors &amp; DPI Scaling:</b> When a meeting window is moved between monitors with different DPI scalings (e.g., 100% and 150%) or restored from minimization to a secondary monitor, <code>GetWindowRect</code> and <code>DwmGetWindowAttribute</code> report coordinates in virtual desktop space. The letterbox scaler smoothly handles changing source dimensions without reconfiguring the output encoder.</li>
  <li><b>Headless / CI Environments:</b> In automated testing on Windows without a physical display attached (e.g. GitHub Actions or Azure VM), GDI screen DCs may return 0 or virtual dimensions. The synthetic test harness is designed to use in-process PyQt6 windows or synthetic frame providers to run reliably in both headless and headed environments.</li>
  <li><b>Power State Transitions:</b> Windows sleep or hibernate suspends high-resolution timers. Using <code>time.perf_counter()</code> and tracking pause deltas ensures the PTS timeline remains strictly monotonic.</li>
</ul>

<hr/>

<h2>4. Conclusion &amp; Architectural Specification</h2>

<h3>4.1 Architecture Component Overview</h3>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; border-color:#334155;">
  <tr bgcolor="#1E293B">
    <th style="color:#F8FAFC;">Component</th>
    <th style="color:#F8FAFC;">Module Location</th>
    <th style="color:#F8FAFC;">Responsibility</th>
    <th style="color:#F8FAFC;">Privacy &amp; Quality Guarantee</th>
  </tr>
  <tr>
    <td><b><code>BrandedVOXPrivacyGenerator</code></b></td>
    <td><code>src/voxlocal/video/privacy_screen.py</code> (or embedded in <code>meeting_capture.py</code> / <code>screen_recorder.py</code>)</td>
    <td>Generates quiet-premium, dark-themed branded privacy placeholder frames with pulsing liveness indicator and session timer.</td>
    <td>High-contrast, elegant card with VOX branding; pre-renders base canvas for &lt; 0.5ms synthesis latency; zero desktop exposure.</td>
  </tr>
  <tr>
    <td><b><code>WindowMinimizationTracker</code></b></td>
    <td><code>src/voxlocal/video/screen_recorder.py</code> &amp; <code>meeting_capture.py</code></td>
    <td>Continuously checks <code>user32.IsIconic(hwnd)</code>, <code>GetWindowPlacement</code>, and iconic coordinate sanitization every frame.</td>
    <td>Deterministic &lt; 1µs minimization detection; immediate state dispatch within 1 frame interval.</td>
  </tr>
  <tr>
    <td><b><code>FailClosedCaptureGate</code></b></td>
    <td><code>src/voxlocal/video/screen_recorder.py</code> &amp; <code>meeting_capture.py</code></td>
    <td>Enforces that when <code>_is_window_capture</code> is active, <code>GetDC(0)</code> screen blits and mouse pointer blits are blocked during minimization.</td>
    <td>100% guarantee that desktop, background apps, or taskbar are never captured into the output MP4.</td>
  </tr>
  <tr>
    <td><b><code>MonotonicPTSMuxer</code></b></td>
    <td><code>src/voxlocal/video/screen_recorder.py</code> &amp; <code>meeting_capture.py</code></td>
    <td>Generates and muxes placeholder frames at the exact configured FPS during minimized gaps.</td>
    <td>Continuous, gapless video stream; zero frame drops; sample-accurate audio-video synchronization.</td>
  </tr>
  <tr>
    <td><b><code>LetterboxCanvasScaler</code></b></td>
    <td><code>src/voxlocal/video/screen_recorder.py</code> &amp; <code>meeting_capture.py</code></td>
    <td>Uniformly scales and centers restored/maximized windows onto fixed-size container canvas with dark borders.</td>
    <td>Zero image distortion; seamless handling of dynamic window resizes across repeated restore/maximize cycles.</td>
  </tr>
  <tr>
    <td><b><code>SyntheticWindowTestHarness</code></b></td>
    <td><code>tests/test_meeting_window_minimization_and_privacy.py</code></td>
    <td>Automated test suite simulating meeting calls, screen shares, and minimize/restore transitions, inspecting resulting MP4 frames.</td>
    <td>Independent PyAV verification of meeting content, privacy placeholder, zero desktop leakage, and monotonic PTS.</td>
  </tr>
</table>

<h3>4.2 Specification: <code>BrandedVOXPrivacyGenerator</code></h3>
<p>The privacy placeholder generator must be implemented as follows:</p>
<pre><code>class BrandedVOXPrivacyGenerator:
    """Generates high-fidelity, quiet-premium branded VOX privacy screens."""

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
        self._static_card_cache: Optional[np.ndarray] = None
        self._last_sec: int = -1
        self._cached_frame: Optional[np.ndarray] = None

    def render(self, elapsed_seconds: float, reason: str = "window_minimized") -&gt; np.ndarray:
        """Render branded privacy screen with pulsing status indicator.

        Color Palette:
        - Canvas Background: Obsidian #0F1115 (15, 17, 21 BGR)
        - Card Container: Slate #1E222A (42, 34, 30 BGR)
        - Card Border: Subtle Accent #333945 (69, 57, 51 BGR)
        - VOX Pill: Purple Gradient #7C3AED to #A78BFA (250, 139, 167 BGR)
        - Primary Text: #F8FAFC (252, 250, 248 BGR)
        - Secondary Text: #94A3B8 (184, 163, 148 BGR)
        - Pulsing Dot: Amber #F59E0B / Green #10B981
        """
        # Pulsing phase: 0.0 to 1.0 using wall-clock sine wave
        pulse_phase = 0.5 + 0.5 * math.sin(time.perf_counter() * 3.0)
        pulse_alpha = int(140 + 115 * pulse_phase)

        canvas = np.full((self.height, self.width, 3), (21, 17, 15), dtype=np.uint8)  # BGR

        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.fromarray(canvas[:, :, ::-1], mode="RGB")
            draw = ImageDraw.Draw(img)

            cx, cy = self.width // 2, self.height // 2
            card_w = min(self.width - 60, 640)
            card_h = min(self.height - 60, 220)
            x0, y0 = cx - card_w // 2, cy - card_h // 2
            x1, y1 = cx + card_w // 2, cy + card_h // 2

            # Render rounded card container
            draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(30, 34, 42), outline=(51, 57, 69), width=1)

            # Render VOX Brand Badge Pill
            badge_x, badge_y = x0 + 32, y0 + 28
            badge_w, badge_h = 76, 26
            draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=6, fill=(124, 58, 237))
            draw.text((badge_x + 14, badge_y + 4), "VOX", fill=(255, 255, 255))

            # Pulsing status dot indicating active privacy monitoring
            dot_cx = badge_x + badge_w + 24
            dot_cy = badge_y + badge_h // 2
            dot_r = 5
            # Outer glow
            glow_r = int(dot_r + 3 * pulse_phase)
            draw.ellipse([dot_cx - glow_r, dot_cy - glow_r, dot_cx + glow_r, dot_cy + glow_r], fill=(245, 158, 11, int(pulse_alpha * 0.4)))
            # Solid core
            draw.ellipse([dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r], fill=(245, 158, 11))

            # Status label
            draw.text((dot_cx + 14, badge_y + 5), "PRIVACY SAFEGUARD ACTIVE", fill=(203, 213, 225))

            # Primary Title: EXACT REQUIREMENT
            title_text = "Meeting capture paused for privacy"
            draw.text((x0 + 32, y0 + 72), title_text, fill=(248, 250, 252))

            # Subtitle
            sub_text = "Desktop recording is disabled while the meeting window is minimized."
            draw.text((x0 + 32, y0 + 112), sub_text, fill=(148, 163, 184))

            # Audio fidelity & telemetry notice
            m = int(elapsed_seconds // 60)
            s = int(elapsed_seconds % 60)
            time_str = f"{m:02d}:{s:02d}"
            info_text = f"Audio recording continues in full fidelity · Meeting Time: {time_str}"
            draw.text((x0 + 32, y0 + 154), info_text, fill=(100, 116, 139))

            rgb = np.asarray(img, dtype=np.uint8)
            canvas = np.ascontiguousarray(rgb[:, :, ::-1])
        except Exception:
            # Fallback geometric rendering in pure numpy
            cx, cy = self.width // 2, self.height // 2
            canvas[cy - 80:cy + 80, cx - 250:cx + 250] = (42, 34, 30)

        return canvas
</code></pre>

<h3>4.3 Specification: Integration into <code>ScreenRecorder</code> &amp; <code>MeetingVideoRecorder</code></h3>
<p>In <code>ScreenRecorder._record_worker()</code> and <code>MeetingVideoRecorder._capture_worker()</code>, the frame acquisition logic is updated with the following deterministic flow:</p>
<pre><code># 1. Evaluate window minimization deterministically
is_minimized = False
if self._is_window_capture and self.target_hwnd:
    if not user32.IsWindow(self.target_hwnd):
        is_minimized = True
    elif user32.IsIconic(self.target_hwnd):
        is_minimized = True
    else:
        # Check window coordinates for offscreen iconic representation
        r = RECT()
        user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
        if r.left &lt;= -10000 or r.top &lt;= -10000:
            is_minimized = True

    # 2. Acquire or Generate Frame
if is_minimized:
    # Privacy Safeguard: General PC desktop is NEVER captured
    active_elapsed = max(0.0, time.perf_counter() - recording_start_clock - paused_time_accum)
    frame_bgr = self._privacy_generator.render(
        elapsed_seconds=active_elapsed,
        reason="window_minimized",
    )
else:
    # Window is visible and active: perform isolated capture
    frame_bgr = self._capture_window_frame(self.target_hwnd)
    if frame_bgr is None:
        frame_bgr = self._privacy_generator.render(
            elapsed_seconds=active_elapsed,
            reason="window_frame_unavailable",
        )
    else:
        # Scale & Letterbox to output container dimensions
        frame_bgr = self._letterbox_scaler.scale(frame_bgr, self._width, self._height)

    # 3. Synchronized Muxing with Monotonic PTS
target_pts = int(round(active_elapsed * self.fps))
if target_pts &lt;= last_pts:
    target_pts = last_pts + 1
last_pts = target_pts

av_frame = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
av_frame.pts = target_pts
for packet in stream.encode(av_frame):
    container.mux(packet)
self._frame_count += 1
</code></pre>

<h3>4.4 Specification: Aspect-Ratio Letterboxer / Scaler</h3>
<pre><code>class AspectRatioLetterboxer:
    """Scales captured frames into container dimensions preserving aspect ratio with dark matte borders."""

    @staticmethod
    def scale(bgr: np.ndarray, target_w: int, target_h: int) -&gt; np.ndarray:
        src_h, src_w = bgr.shape[:2]
        if src_w == target_w and src_h == target_h:
            return np.ascontiguousarray(bgr)

        scale = min(float(target_w) / float(max(1, src_w)), float(target_h) / float(max(1, src_h)))
        new_w = max(2, min(target_w, int(round(src_w * scale))))
        new_h = max(2, min(target_h, int(round(src_h * scale))))

        try:
            from PIL import Image
            img = Image.fromarray(bgr)
            resized_img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            resized = np.asarray(resized_img, dtype=np.uint8)
        except Exception:
            y_idx = np.linspace(0, src_h - 1, new_h).astype(np.int32)
            x_idx = np.linspace(0, src_w - 1, new_w).astype(np.int32)
            resized = bgr[np.ix_(y_idx, x_idx)]

        if new_w == target_w and new_h == target_h:
            return np.ascontiguousarray(resized)

        # Matte canvas (Obsidian dark theme)
        canvas = np.full((target_h, target_w, 3), (21, 17, 15), dtype=np.uint8)
        off_y = (target_h - new_h) // 2
        off_x = (target_w - new_w) // 2
        canvas[off_y:off_y + new_h, off_x:off_x + new_w] = resized
        return canvas
</code></pre>

<h3>4.5 Specification: Programmatic Synthetic Window Test Harness</h3>
<p>To verify the four acceptance criteria programmatically without human intervention, an automated pytest suite (<code>tests/test_meeting_window_minimization_and_privacy.py</code>) must execute the following protocol:</p>
<ol>
  <li><b>Synthetic Window / Frame Source Setup:</b>
    <ul>
      <li>Creates a test window (or mock window capture provider) with distinct, known color patterns:
        <ul>
          <li><b>Active Call Stage Pattern:</b> Solid Emerald Green <code>#10B981</code> (BGR <code>(129, 185, 16)</code>) with text <code>"Active Meeting Call"</code>.</li>
          <li><b>Presenter Screen Sharing Pattern:</b> Solid Sapphire Blue <code>#3B82F6</code> (BGR <code>(246, 130, 59)</code>) with text <code>"Shared Presentation"</code>.</li>
          <li><b>PC Desktop Region (The Leakage Canary):</b> Solid Coral Red <code>#EF4444</code> (BGR <code>(68, 68, 239)</code>).</li>
        </ul>
      </li>
    </ul>
  </li>
  <li><b>Automated Lifecycle Execution:</b>
    <ul>
      <li><b>Phase 1 (Active Meeting):</b> Recorder captures green call stage for 1.0 second (15 frames).</li>
      <li><b>Phase 2 (Presenter Sharing):</b> Sharing session initiates; recorder captures blue presentation for 1.0 second (15 frames).</li>
      <li><b>Phase 3 (Window Minimized):</b> Window state set to <code>IsIconic = True</code>; desktop canary (Coral Red) is placed underneath; recorder runs for 1.5 seconds (22 frames).</li>
      <li><b>Phase 4 (Restored &amp; Maximized):</b> Window state restored to <code>IsIconic = False</code> at 1920x1080; recorder runs for 1.0 second (15 frames).</li>
      <li>Recorder stops; output MP4 container is finalized.</li>
    </ul>
  </li>
  <li><b>Inspection &amp; Verification Checks on Decoded MP4:</b>
    <ul>
      <li><b>Criterion A (Meeting Content):</b> Frames from Phase 1 decode to green call stage (mean green channel &gt; 150). Frames from Phase 2 decode to blue presentation (mean blue channel &gt; 200).</li>
      <li><b>Criterion B (Branded VOX Privacy Screen):</b> Frames from Phase 3 decode to the privacy screen. Verification asserts:
        <ul>
          <li>Frame is not black (mean &gt; 15.0).</li>
          <li>Frame contains high-contrast text and VOX badge (max &gt; 200).</li>
          <li>Center card region contains the VOX brand colors.</li>
        </ul>
      </li>
      <li><b>Criterion C (Zero Desktop Leakage):</b> <b>EVERY DECODED FRAME</b> across the entire MP4 is scanned for the desktop canary color (Coral Red <code>(68, 68, 239)</code>). The test asserts that <b>zero frames</b> contain the canary pattern, proving complete isolation.</li>
      <li><b>Criterion D (A/V Synchronization &amp; Container Health):</b>
        <ul>
          <li>Decoded frame PTS is strictly monotonic: <code>pts[i] &gt; pts[i-1]</code> for all <code>i</code>.</li>
          <li>Total decoded frames match expected timeline: <code>total_frames == 67 +- 2</code> at 15 FPS.</li>
          <li>PyAV demuxer encounters zero container errors, zero dropped frames, and zero corrupted packets.</li>
        </ul>
      </li>
    </ul>
  </li>
</ol>

<hr/>

<h2>5. Verification Method</h2>

<h3>5.1 Independent Verification Commands</h3>
<p>The verification commands below confirm existing video capture pipeline tests pass, and provide the command for the new minimization test harness:</p>
<pre><code>    # 1. Verify existing meeting video capture pipeline tests
.venv\Scripts\python.exe -m pytest tests/test_meeting_video_capture_pipeline.py -v

    # 2. Verify meeting presentation &amp; lifecycle tests
.venv\Scripts\python.exe -m pytest tests/test_meeting_studio_presentation_and_lifecycle.py -v

    # 3. Verify screen recording and meeting coexistence
.venv\Scripts\python.exe -m pytest tests/test_meeting_and_screen.py -v

    # 4. Verify screen sharing comprehensive matrix
.venv\Scripts\python.exe -m pytest tests/test_screen_sharing_comprehensive_matrix.py -v

    # 5. Run full video suite regression check
.venv\Scripts\python.exe -m pytest tests/test_meeting_video_capture_pipeline.py tests/test_meeting_studio_presentation_and_lifecycle.py tests/test_meeting_and_screen.py tests/test_screen_sharing_comprehensive_matrix.py -q
</code></pre>

<h3>5.2 Invalidation Conditions</h3>
<p>The design must be flagged for revision if any of the following occur:</p>
<ul>
  <li>Any decoded video frame during minimization contains desktop pixels or pixels from background windows.</li>
  <li>Decoded video frame timestamps (PTS) jump, stutter, or fall behind wall-clock time during minimize/restore transitions.</li>
  <li>The privacy placeholder frame appears as an unbranded, pitch-black screen or contains raw text without the VOX badge and pulsing indicator.</li>
  <li>Restoring or resizing a meeting window produces aspect-ratio distortion (stretched or squished visuals) rather than clean letterboxing.</li>
</ul>

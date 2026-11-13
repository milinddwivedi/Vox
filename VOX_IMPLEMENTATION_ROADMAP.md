<h2>VOX Desktop Productivity Assistant — Comprehensive Implementation Roadmap</h2>

<p><b>Document Identifier:</b> VOX-ROADMAP-2026-V2<br/>
<b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Document Purpose:</b> Authoritative, phased engineering implementation roadmap, file ownership matrix, risk mitigations, test criteria, and rollback strategies across all four product pillars (Voice Intelligence, Screen Intelligence, Meeting Intelligence, VOX Brain).<br/>
<b>Execution Baseline:</b> Enterprise Release Candidate Core, Target Architecture Specification, Product Requirements Document.<br/>
<b>Formatting Mandate:</b> Strict HTML header compliance (zero markdown <code>#</code> or <code>*</code> headers across all sections).</p>

<hr/>

<h2>1. Executive Phasing Strategy &amp; Engineering Governance</h2>

<p>The VOX engineering roadmap transitions the existing functional desktop core into the unified, high-polish target experience demonstrated in Reference Images 1 and 2 and verified in the Wispr Flow forensic analysis. Development is organized into four concurrent engineering tracks executed across four disciplined release milestones:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Milestone</th>
      <th>Primary Focus &amp; Theme</th>
      <th>Target Deliverables &amp; Outcomes</th>
      <th>Verification Gates</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Milestone 1 (M1)</b></td>
      <td><b>Foundational Voice &amp; Dual-Tier Pill</b></td>
      <td>Complete dual-tier floating pill widget (Reference Image 2), live dynamic waveform bars, mode chips (Magic, Structured, Email), live red strikethrough preview bubble, sub-220ms active window text injection.</td>
      <td><code>VER-VT-01</code> through <code>VER-VT-03</code>, <code>UX-VER-01</code> through <code>UX-VER-04</code>.</td>
    </tr>
    <tr>
      <td><b>Milestone 2 (M2)</b></td>
      <td><b>Screen Intelligence &amp; Privacy Guard</b></td>
      <td>Call-only window bounding, privacy status frame replacement on minimize, A/V drift &lt; 66.6ms, animated click ripples, fragmented MP4 streaming container.</td>
      <td><code>VER-SC-01</code> through <code>VER-SC-02</code>, <code>SEC-VER-03</code>.</td>
    </tr>
    <tr>
      <td><b>Milestone 3 (M3)</b></td>
      <td><b>Meeting Intelligence &amp; 3-Tab Workspace</b></td>
      <td>Unified 3-tab workspace (My Thoughts, Transcript, Summary), 7-state decision evolution table (ADR-001), grounded action item matrix (ADR-003), 1-click follow-up email generator, per-participant checklists, scoped Ask Vox RAG (ADR-004).</td>
      <td><code>VER-MT-01</code> through <code>VER-MT-03</code>, <code>UX-VER-05</code> through <code>UX-VER-07</code>.</td>
    </tr>
    <tr>
      <td><b>Milestone 4 (M4)</b></td>
      <td><b>VOX Brain, MCP Bridge &amp; Hardening</b></td>
      <td>Local loopback MCP server with dictation isolation, calendar/Slack local integrations with pre-transmission preview, client review MoM sanitization, end-to-end performance hardening.</td>
      <td><code>VER-BR-01</code> through <code>VER-BR-02</code>, <code>SEC-VER-01</code> through <code>SEC-VER-06</code>.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>2. Track 1: Core 1 — Voice Intelligence &amp; Desktop Floating Pill</h2>

<h3>2.1 Scope &amp; Target Capabilities</h3>
<p>Implement the complete dual-tier desktop floating pill control inspired by Reference Image 2 and Wispr Flow ergonomics, backed by high-speed neural transcription, live self-correction strikethrough, and focus-invariant Win32 injection.</p>

<h3>2.2 Work Breakdown &amp; Actionable Tasks</h3>
<ul>
  <li><b>Task 1.1: Dual-Tier Floating Pill UI (<code>floating_widget.py</code>):</b>
    <ul>
      <li>Implement <code>FloatingPillWidget</code> matching exact Reference Image 2 geometry: Upper tier (272px &times; 44px) housing Cancel (<code>&times;</code>), waveform capsule (84px &times; 24px), session timer (<code>MM:SS</code>), and coral-red stop button (<code>#E05638</code>); Lower tier (340px &times; 36px) housing mode selector chips (Magic [Win+F1], Structured [Win+F2], Email [Win+F3]) and selector chevron (<code>&#x2965;</code>).</li>
      <li>Enforce Win32 styles: <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> with <code>Qt.WindowType.FramelessWindowHint</code>.</li>
      <li>Implement 5-stage state machine: Idle, Listening, Processing, Committed, Error.</li>
    </ul>
  </li>
  <li><b>Task 1.2: Embedded Dynamic Waveform Equalizer (<code>animations.py</code>):</b>
    <ul>
      <li>Embed 12-bar dynamic equalizer inside the pill's highlight capsule, driven at 30 FPS by the RMS energy of incoming audio chunks.</li>
      <li>Implement smooth spring physics for bar heights and transition to dotted progress animation during LLM processing.</li>
    </ul>
  </li>
  <li><b>Task 1.3: Real-Time Speech Preview with Red Strikethrough (<code>floating_widget.py</code>):</b>
    <ul>
      <li>Implement floating speech preview card floating 12px above the upper pill.</li>
      <li>Integrate real-time disfluency detector in <code>DictationProcessor</code> to wrap filler sounds ("umm", "uh") and false starts ("no actually", "wait sorry") in <code>&lt;span style="color:#D32F2F; text-decoration:line-through;"&gt;</code> during active dictation.</li>
      <li>Implement 180ms ease-out collapse into the finalized cleaned sentence.</li>
    </ul>
  </li>
  <li><b>Task 1.4: Multi-Mode Rewriting Engine (<code>dictation_processor.py</code>):</b>
    <ul>
      <li>Implement dedicated transformation handlers:
        <ul>
          <li><i>Magic Mode (Win+F1):</i> Conversational cleanup and concise prose rewrite.</li>
          <li><i>Structured Mode (Win+F2):</i> Markdown outlines, bullet points, and action summaries.</li>
          <li><i>Email Mode (Win+F3):</i> Executive email drafting with salutation, body, and sign-off.</li>
        </ul>
      </li>
      <li>Hook hotkey chords in <code>GlobalHotkeyManager</code> to toggle active modes instantly.</li>
    </ul>
  </li>
  <li><b>Task 1.5: Active Window Injection Optimization (<code>text_inserter.py</code>):</b>
    <ul>
      <li>Tune 3-tier injection cascade: <code>SendInput(KEYEVENTF_UNICODE)</code> &rarr; Direct <code>WM_CHAR</code> fallback &rarr; Atomic clipboard paste fallback for Electron apps.</li>
      <li>Guarantee sub-220ms release-to-insertion latency on CUDA hardware.</li>
    </ul>
  </li>
</ul>

<h3>2.3 Files Owned</h3>
<ul>
  <li><code>src/voxlocal/ui/floating_widget.py</code></li>
  <li><code>src/voxlocal/ui/animations.py</code></li>
  <li><code>src/voxlocal/engine/dictation_processor.py</code></li>
  <li><code>src/voxlocal/win32/text_inserter.py</code></li>
  <li><code>src/voxlocal/win32/hotkeys.py</code></li>
  <li><code>src/voxlocal/core/controller.py</code></li>
</ul>

<h3>2.4 Risk Mitigations &amp; Fallback</h3>
<ul>
  <li><i>Risk:</i> Low-level keyboard hook latency or conflict with game overlays.<br/>
  <i>Mitigation:</i> Run <code>WH_KEYBOARD_LL</code> hook in dedicated OS thread with non-blocking event queue; support user-customizable hotkey chords in settings.</li>
  <li><i>Risk:</i> Electron apps (Teams, Slack) dropping characters during high-speed <code>SendInput</code>.<br/>
  <i>Mitigation:</i> Automatically detect Electron window classes and route through atomic clipboard paste with 15ms clipboard restoration.</li>
</ul>

<hr/>

<h2>3. Track 2: Core 2 — Screen Intelligence &amp; Privacy Bounding</h2>

<h3>3.1 Scope &amp; Target Capabilities</h3>
<p>Deliver hardware-synchronized display, window, and snip capture with strict call-only meeting window isolation, privacy fail-closed protection, and fragmented container resilience.</p>

<h3>3.2 Work Breakdown &amp; Actionable Tasks</h3>
<ul>
  <li><b>Task 2.1: Call-Only Window Privacy Bounding (<code>src/voxlocal/video/</code>):</b>
    <ul>
      <li>Bind video capture strictly to verified meeting call stage HWNDs (Teams, Zoom, Meet).</li>
      <li>Strictly prohibit fallback to desktop DC (<code>GetDC(0)</code>) during meeting recording sessions.</li>
    </ul>
  </li>
  <li><b>Task 2.2: Privacy Fail-Closed Status Frame Generator:</b>
    <ul>
      <li>Detect when the target meeting window is minimized, obscured, or closed.</li>
      <li>Immediately halt pixel sampling from external surfaces and substitute an opaque privacy placeholder frame (<i>"Meeting window minimized &mdash; Privacy Guard Active"</i>).</li>
      <li>Generate frames with exact wall-clock Presentation Time Stamps (PTS) to maintain lip-sync drift &lt; 66.6ms.</li>
    </ul>
  </li>
  <li><b>Task 2.3: Animated Cursor Dynamics &amp; Click Ripples (<code>src/voxlocal/ui/animations.py</code>):</b>
    <ul>
      <li>Render hardware cursor shapes via Win32 <code>DrawIconEx</code>.</li>
      <li>Draw distinct animated click ripples: Champagne Gold for left-click, Sapphire Blue for right-click, with directional scroll chevrons.</li>
    </ul>
  </li>
  <li><b>Task 2.4: Fragmented MP4 Muxing &amp; Interrupted Recovery:</b>
    <ul>
      <li>Configure PyAV / FFmpeg container muxer with <code>movflags=frag_keyframe+empty_moov+default_base_moof</code>.</li>
      <li>Ensure videos survive sudden power termination without moov atom corruption.</li>
    </ul>
  </li>
</ul>

<h3>3.3 Files Owned</h3>
<ul>
  <li><code>src/voxlocal/video/recorder.py</code></li>
  <li><code>src/voxlocal/video/capture.py</code></li>
  <li><code>src/voxlocal/ui/animations.py</code></li>
  <li><code>src/voxlocal/annotation/canvas.py</code></li>
</ul>

<hr/>

<h2>4. Track 3: Core 3 — Meeting Intelligence &amp; Unified 3-Tab Workspace</h2>

<h3>4.1 Scope &amp; Target Capabilities</h3>
<p>Consolidate meeting operations into a streamlined 3-tab workspace (My Thoughts, Transcript, Summary), powered by native bot-free call detection, 7-state decision evolution, grounded action items, 1-click follow-up emails, and scoped Ask Vox RAG.</p>

<h3>4.2 Work Breakdown &amp; Actionable Tasks</h3>
<ul>
  <li><b>Task 3.1: Unified 3-Tab Meeting Workspace UI (<code>main_window.py</code> &amp; <code>components.py</code>):</b>
    <ul>
      <li>Restructure Meeting Studio interface into three distinct, primary tabs:
        <ol>
          <li><b>Tab 1: "My Thoughts":</b> Personal private scratchpad, timestamp note marker button (<code>[MM:SS]</code>), summary inclusion toggle, and autosave.</li>
          <li><b>Tab 2: "Transcript":</b> Chronological speaker-diarized feed, interactive clickable seeking timestamps, inline speaker renaming, FTS5 search filter, and dual-track audio playback bar.</li>
          <li><b>Tab 3: "Summary":</b> Executive overview, 7-state decision evolution table, grounded action items matrix, risks vs blockers register, 1-click follow-up email button, participant checklists, and embedded Ask Vox pane.</li>
        </ol>
      </li>
    </ul>
  </li>
  <li><b>Task 3.2: 7-State Decision Evolution Engine (<code>meeting_intelligence.py</code>):</b>
    <ul>
      <li>Implement orthogonal classification: Business Status (proposed, confirmed, rejected, unresolved) &times; Evolutionary State (new, repeated, superseded, reversed).</li>
      <li>Enforce foreign key constraints: <code>supersedes_id REFERENCES decisions(id) ON DELETE RESTRICT</code> (ADR-001).</li>
    </ul>
  </li>
  <li><b>Task 3.3: Grounded Action Items &amp; Date Arithmetic (<code>meeting_intelligence.py</code>):</b>
    <ul>
      <li>Extract action items with mandatory <code>evidence_quote</code>, timestamp, and owner.</li>
      <li>Implement deterministic relative date parsing (ADR-003) resolving expressions ("by Friday") to ISO dates based on meeting timestamp anchor; ambiguous dates resolve to <code>due_date=None</code>.</li>
      <li>Enforce user-edit preservation across AI regeneration cycles (ADR-002).</li>
    </ul>
  </li>
  <li><b>Task 3.4: 1-Click Follow-Up Email &amp; Checklists Generator:</b>
    <ul>
      <li>Implement single-click generator synthesizing executive email drafts with decisions, owner-grouped tasks, and deadlines.</li>
      <li>Add interactive modal with options: Copy Rich HTML, Copy Markdown, Open in Default Mail Client (<code>mailto:</code>).</li>
      <li>Implement Per-Participant Checklist Matrix with one-click Slack markdown export.</li>
    </ul>
  </li>
  <li><b>Task 3.5: Scoped In-Report "Ask Vox" RAG Copilot (ADR-004):</b>
    <ul>
      <li>Implement conversational Q&amp;A pane along the bottom of Tab 3.</li>
      <li>Enforce hard meeting-level filtering: candidate chunks strictly quarantined to active meeting.</li>
      <li>Return answers with verbatim transcript citations and clickable timestamp seek links.</li>
    </ul>
  </li>
</ul>

<h3>4.3 Files Owned</h3>
<ul>
  <li><code>src/voxlocal/ui/main_window.py</code></li>
  <li><code>src/voxlocal/ui/components.py</code></li>
  <li><code>src/voxlocal/engine/meeting_intelligence.py</code></li>
  <li><code>src/voxlocal/audio/call_detector.py</code></li>
  <li><code>src/voxlocal/audio/meeting_recorder.py</code></li>
  <li><code>src/voxlocal/storage/database.py</code></li>
</ul>

<hr/>

<h2>5. Track 4: Core 4 — VOX Brain, Integrations &amp; Security Architecture</h2>

<h3>5.1 Scope &amp; Target Capabilities</h3>
<p>Implement the local Model Context Protocol (MCP) server with dictation isolation, local calendar and Slack connectors with pre-transmission preview, and client review export safeguards.</p>

<h3>5.2 Work Breakdown &amp; Actionable Tasks</h3>
<ul>
  <li><b>Task 4.1: Local MCP Loopback Server (<code>src/voxlocal/mcp/</code>):</b>
    <ul>
      <li>Implement local stdio/SSE Model Context Protocol server.</li>
      <li>Enforce strict dictation boundary: zero MCP access to personal voice typing dictations.</li>
      <li>Expose read tools for meetings, summaries, and transcripts.</li>
      <li>Implement interactive desktop preview &amp; authorization modal before returning payloads.</li>
    </ul>
  </li>
  <li><b>Task 4.2: Local Calendar &amp; Slack Integrations:</b>
    <ul>
      <li>Implement read-only calendar sync (Google / Outlook) via local loopback OAuth.</li>
      <li>Store tokens encrypted on disk using Windows DPAPI (<code>CryptProtectData</code>).</li>
      <li>Implement Slack action broadcast with pre-transmission preview modal and automated PII/secret redaction.</li>
    </ul>
  </li>
  <li><b>Task 4.3: Client Review Export Sanitization (<code>exporter.py</code>):</b>
    <ul>
      <li>Implement bifurcated export pipeline: Internal MoM vs Client Review MoM.</li>
      <li>Enforce 100% suppression of Tab 1 "My Thoughts" scratchpad from client exports.</li>
      <li>Implement automated post-generation sanitization audit verifying zero scratchpad text leaks.</li>
    </ul>
  </li>
</ul>

<h3>5.3 Files Owned</h3>
<ul>
  <li><code>src/voxlocal/storage/exporter.py</code></li>
  <li><code>src/voxlocal/storage/database.py</code></li>
  <li><code>src/voxlocal/win32/startup.py</code></li>
  <li><code>src/voxlocal/core/config.py</code></li>
</ul>

<hr/>

<h2>6. Unified File Ownership &amp; Component Dependency Matrix</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>File Path in Codebase</th>
      <th>Primary Subsystem / Component</th>
      <th>Assigned Track</th>
      <th>Direct Architectural Dependencies</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>src/voxlocal/ui/floating_widget.py</code></td>
      <td>Dual-Tier Floating Pill &amp; Speech Preview Bubble</td>
      <td>Track 1 (Core 1)</td>
      <td><code>animations.py</code>, <code>dictation_processor.py</code>, <code>controller.py</code></td>
    </tr>
    <tr>
      <td><code>src/voxlocal/ui/animations.py</code></td>
      <td>Dynamic Waveform Bars &amp; Click Ripples</td>
      <td>Track 1 &amp; 2</td>
      <td>PyQt6 QPainter, RMS Audio Telemetry Signals</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/ui/main_window.py</code></td>
      <td>Unified 3-Tab Meeting Workspace &amp; Navigation</td>
      <td>Track 3 (Core 3)</td>
      <td><code>components.py</code>, <code>meeting_intelligence.py</code>, <code>database.py</code></td>
    </tr>
    <tr>
      <td><code>src/voxlocal/ui/components.py</code></td>
      <td>Meeting Cards, Decisions Table, Action Matrix</td>
      <td>Track 3 (Core 3)</td>
      <td>PyQt6 Widgets, Theme Tokens, ADR Schemas</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/engine/dictation_processor.py</code></td>
      <td>Modes (Magic, Structured, Email), Self-Correction</td>
      <td>Track 1 (Core 1)</td>
      <td>Hinglish Regex, Punctuation Rules, LLM Engine</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/engine/meeting_intelligence.py</code></td>
      <td>7-State Decisions, Citations, Date Parser, Ask Vox</td>
      <td>Track 3 (Core 3)</td>
      <td><code>database.py</code>, LLM Engine, ADR-001/002/003/004</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/engine/transcriber.py</code></td>
      <td>Faster-Whisper Turbo int8, Silero VAD</td>
      <td>Track 1 (Core 1)</td>
      <td>CTranslate2, PyTorch / OnnxRuntime, Sinc Resampler</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/audio/call_detector.py</code></td>
      <td>Windows Registry Telemetry &amp; Window Classifier</td>
      <td>Track 3 (Core 3)</td>
      <td>Win32 Registry, EnumWindows, Consent Store</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/audio/meeting_recorder.py</code></td>
      <td>Dual-Track WASAPI Mic + Loopback Recorder</td>
      <td>Track 3 (Core 3)</td>
      <td>Windows Core Audio COM, Lock-Free Ring Buffers</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/video/recorder.py</code></td>
      <td>Call-Only Window Bounded Video Recorder</td>
      <td>Track 2 (Core 2)</td>
      <td>DXGI / GDI, PyAV / FFmpeg, PTS Generator</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/storage/database.py</code></td>
      <td>SQLite WAL Relational Schema &amp; ADR Constraints</td>
      <td>Cross-Track</td>
      <td>SQLite3 WAL, FTS5, Verified Snapshot Engine</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/storage/exporter.py</code></td>
      <td>Bifurcated Exporter &amp; Client MoM Sanitization</td>
      <td>Track 4 (Core 4)</td>
      <td>Markdown, HTML/PDF Builders, Redaction Engine</td>
    </tr>
    <tr>
      <td><code>src/voxlocal/win32/text_inserter.py</code></td>
      <td>SendInput, WM_CHAR, Atomic Clipboard Paste</td>
      <td>Track 1 (Core 1)</td>
      <td>Win32 SendInput, Clipboard API, UIPI Guard</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>7. Risk Assessment, Failure Modes, &amp; Rollback Strategies</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Failure Mode / Risk Scenario</th>
      <th>Severity</th>
      <th>Root Cause Analysis</th>
      <th>Preventative Mitigation</th>
      <th>Automated Rollback &amp; Fallback Strategy</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>FM-01: Audio Device Disconnect Mid-Session</b></td>
      <td>High</td>
      <td>USB or Bluetooth headset unplugged while recording.</td>
      <td>Listen to <code>IMMNotificationClient</code> device arrival/removal callbacks in WASAPI thread.</td>
      <td>Seamlessly switch capture to system default audio endpoint without tearing down transcription pipeline or dropping recorded buffers.</td>
    </tr>
    <tr>
      <td><b>FM-02: Win32 SendInput Blocked by Target App</b></td>
      <td>Medium</td>
      <td>Target application is running as Administrator (UIPI privilege violation) or is an Electron app.</td>
      <td>Detect foreground window privilege and process class before emission.</td>
      <td>Tier 2 direct <code>WM_CHAR</code> message fallback &rarr; Tier 3 atomic safe clipboard paste fallback (restoring prior clipboard in &lt; 15ms).</td>
    </tr>
    <tr>
      <td><b>FM-03: SQLite Database Lock Contention</b></td>
      <td>Medium</td>
      <td>Concurrent writes from Whisper worker and meeting intelligence worker.</td>
      <td>Configure SQLite in WAL mode with <code>busy_timeout = 5000ms</code> and thread-local connections.</td>
      <td>Queue database write transactions through a serial worker thread; retry with exponential backoff on <code>sqlite3.OperationalError</code>.</td>
    </tr>
    <tr>
      <td><b>FM-04: High-DPI Display Scaling Blur</b></td>
      <td>Low</td>
      <td>Windows display scaling set to 125%, 150%, or 175% causing raster blurring.</td>
      <td>Enable <code>Qt.ApplicationAttribute.AA_EnableHighDpiScaling</code> and <code>AA_UseHighDpiPixmaps</code>.</td>
      <td>Use vector SVG icons and device-pixel-ratio proportional rendering; fallback to integer 2&times; assets if fractional DPI fails.</td>
    </tr>
    <tr>
      <td><b>FM-05: Excessive Memory Consumption</b></td>
      <td>Medium</td>
      <td>Continuous meeting exceeding 3 hours with large VAD buffers and video frames.</td>
      <td>Stream audio chunks to disk; flush video frames directly to fragmented MP4 container.</td>
      <td>Trigger automatic garbage collection and memory cache compaction if process RSS exceeds 650 MB.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>8. Forensic Verification Gate &amp; Acceptance Register</h2>

<p>Before any production build is approved for release, all twenty-four verification criteria across the specification suite must be validated by the automated test runner:</p>

<ul>
  <li><b>Test Battery 1: Unit &amp; E2E Voice Typing:</b> <code>pytest tests/test_voice_typing_e2e.py -v</code>
    <br/>Validates push-to-talk release latency &lt; 220ms, live strikethrough tagging, mode switches (Win+F1/F2/F3), and active window text injection.</li>
  <li><b>Test Battery 2: Audio &amp; Video Quality Suite:</b> <code>pytest tests/test_phase9_audio_video_quality.py -v</code>
    <br/>Validates dual-track WASAPI separation, A/V sync drift &lt; 66.6ms, click ripples, and fragmented MP4 recovery.</li>
  <li><b>Test Battery 3: Local AI &amp; Meeting Intelligence:</b> <code>pytest tests/test_phase10_local_ai_and_meeting_intelligence.py -v</code>
    <br/>Validates 7-state decision evolution (ADR-001), user edit preservation (ADR-002), relative date parsing (ADR-003), and quarantined Ask Vox RAG (ADR-004).</li>
  <li><b>Test Battery 4: Product Experience &amp; Workflow:</b> <code>pytest tests/test_phase11_product_experience_and_workflow.py -v</code>
    <br/>Validates dual-tier pill geometry, focus invariance (WS_EX_NOACTIVATE), 3-tab meeting workspace, follow-up email generator, and participant checklists.</li>
  <li><b>Test Battery 5: Security &amp; Privacy Hardening:</b> <code>pytest tests/test_phase12_security_privacy_hardening.py -v</code>
    <br/>Validates zero cloud sockets, call-only window privacy bounds, MCP dictation boundary quarantine, DPAPI encryption, and Client Review MoM sanitization.</li>
</ul>

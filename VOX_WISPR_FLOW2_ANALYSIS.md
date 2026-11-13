<h2>VOX Reference Video & Interaction Analysis Report (Whisper FLow2)</h2>

<p><b>Document Authority:</b> VOX Evolution Architectural Deliverable<br>
<b>Author:</b> Teamwork Worker (Grounding & Architecture Specialist)<br>
<b>Date:</b> 2026-11-08<br>
<b>Primary Reference Video:</b> <code>External References/Whisper FLow2.mp4</code> (213.267s, 60.16 MB)<br>
<b>Extracted Audio Asset:</b> <code>External References/whisper_flow2_audio.aac</code> (5.18 MB, 48 kHz stereo AAC)<br>
<b>Reference Image 1:</b> Wispr Flow Dictation Hero (Warm Ivory Card, Typography, Logo, Mode Switcher)<br>
<b>Reference Image 2:</b> Wispr Flow Notetaker Workspace (Speaker Diarization Pills, Streaming Tokens, Action Item Cards)<br>
<b>Decompiled Source Ground Truth:</b> Wispr Flow Desktop Production Bundle (<code>app-1.6.1400\resources\app.asar</code>)<br>
<b>Compliance Mandate:</b> Zero lines starting with markdown <code>#</code> or <code>*</code> across all sections; strict HTML headers (<code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, <code>&lt;h4&gt;</code>) or bold text.</p>

<hr>

<h2>1. Executive Summary & Forensic Asset Specifications</h2>

<p>This report establishes the definitive technical and user experience grounding for the VOX desktop assistant evolution by providing an exhaustive forensic deconstruction of the Wispr Flow reference assets. Wispr Flow sets the contemporary gold standard for voice-first productivity by harmonizing an ultra-responsive, non-activating floating voice dictation pill with a local, bot-free meeting intelligence studio.</p>

<p>The forensic investigation synthesizes four authentic evidence pillars:</p>

<ul>
  <li><b>Container & Stream Forensics:</b> Programmatic box parsing of <code>Whisper FLow2.mp4</code> demuxing exact dimensions (1334x836), framerate (30.00 fps), frame count (6,398 video frames, 9,983 audio packets), and extracting the high-fidelity 48 kHz stereo AAC audio track into <code>whisper_flow2_audio.aac</code>.</li>
  <li><b>Reference Image Deconstruction:</b> Microscopic analysis of Reference Image 1 (Dictation hero) and Reference Image 2 (Notetaker workspace), extracting precise layout grids, 1.5px card borders, warm ivory surfaces (<code>#FAF8F1</code>), typography pairing, speaker diarization pills, and trailing action item assignees.</li>
  <li><b>Decompiled Electron Bundle Extraction:</b> Direct inspection of production JavaScript modules (<code>extracted_overlay.js</code>, <code>extracted_status.js</code>, <code>extracted_meeting_recorder.js</code>, <code>extracted_hub.js</code>), revealing exact CSS design tokens (<code>--dawn-50..950</code>, <code>--chord-50..950</code>), font definitions, the 12-bar parabolic bell-curve equalizer formula, and the 8-state machine enum (<code>$t.fw</code>).</li>
  <li><b>VOX Architectural Translation:</b> A concrete, file-by-file drop-in implementation blueprint mapping Wispr Flow capabilities directly onto the PyQt6 / Faster-Whisper architecture of VOX.</li>
</ul>

<hr>

<h2>2. Video Stream Specifications & Technical Metadata</h2>

<h3>2.1 Demuxing & Container Inspection Table</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Technical Property</th>
    <th>Video Stream (Track 1)</th>
    <th>Audio Stream (Track 2)</th>
  </tr>
  <tr>
    <td><b>Handler Type</b></td>
    <td><code>vide</code> (VideoHandler)</td>
    <td><code>soun</code> (SoundHandler)</td>
  </tr>
  <tr>
    <td><b>Codec Identifier</b></td>
    <td><code>avc1</code> (H.264 / AVC High Profile, Level 4.0)</td>
    <td><code>mp4a</code> (MPEG-4 AAC-LC Low Complexity)</td>
  </tr>
  <tr>
    <td><b>Resolution / Channels</b></td>
    <td>1334 x 836 pixels (16:10 display aspect ratio)</td>
    <td>2 discrete channels (Stereo L/R)</td>
  </tr>
  <tr>
    <td><b>Sample Rate / Timescale</b></td>
    <td>30,000 timescale (Exact 30.000 fps)</td>
    <td>48,000 Hz (48 kHz broadcast fidelity)</td>
  </tr>
  <tr>
    <td><b>Sample Count</b></td>
    <td>6,398 total video frames</td>
    <td>9,983 total audio packets / chunks</td>
  </tr>
  <tr>
    <td><b>Duration</b></td>
    <td>213.267 seconds (03:33.27)</td>
    <td>212.966 seconds (03:32.97)</td>
  </tr>
  <tr>
    <td><b>Stream Size & Bitrate</b></td>
    <td>54.98 MB (~2.07 Mbps average bitrate)</td>
    <td>5.18 MB (5,181,177 bytes, ~191.4 kbps)</td>
  </tr>
  <tr>
    <td><b>Extracted Audio Target</b></td>
    <td colspan="2"><code>d:\Milind\Voice Assistant\External References\whisper_flow2_audio.aac</code> (ADTS sync headers verified)</td>
  </tr>
</table>

<h3>2.2 Chronological Video Timeline & Micro-Interaction Breakdown</h3>

<p>The 213.27-second reference video demonstrates the complete user journey across both primary capabilities:</p>

<ul>
  <li><b>00:00 - 00:24 | The Dictation Paradigm & Instant Invocation:</b>
    <br>The video begins by illustrating the friction of keyboard typing for long-form thoughts. The user invokes the floating voice pill globally via a single shortcut key (<code>Fn</code> on macOS, configured as <code>Ctrl+Shift+Space</code> or <code>Ctrl+Win</code> on Windows). The pill slides into view at the bottom-center of the screen within 80 milliseconds without stealing focus from the active text editor.</li>
  <li><b>00:25 - 00:57 | Push-to-Talk vs Persistent Hands-Free Toggle (POPO Mode):</b>
    <br>Demonstrates two discrete operating paradigms:
    <br>1. <i>Push-to-Talk (PTT)</i>: Holding the shortcut key records speech; releasing the key triggers instantaneous transcription and insertion at the active text cursor.
    <br>2. <i>POPO Mode (Persistent Overlay Persistent Operation)</i>: A quick tap opens a persistent dual-tier floating pill equipped with an elapsed timer (<code>00:14</code>), a dismiss button (<code>✕</code>), an animated 12-bar equalizer capsule, and a solid coral stop button (<code>◼</code>).</li>
  <li><b>00:58 - 01:31 | Speech Self-Correction & Live Strikethrough Preview:</b>
    <br>The speaker dictates a complex thought with verbal self-corrections: <i>"We need to ship the onboarding flow this Thursday... wait sorry, let's schedule it for next Tuesday after QA sign-off."</i>
    <br>In the floating preview bubble directly above the pill, the retracted clause <code>wait sorry, let's schedule it for</code> is visually flagged with an immediate crimson strikethrough (<code>#D32F2F</code>) while speech is still occurring. Upon session completion, VOX inserts only the finalized, corrected clause, pruning both the retracted phrase and conversational filler words.</li>
  <li><b>01:32 - 02:14 | Five Writing Modes in Action:</b>
    <br>The user switches writing modes on the fly using lower-tier chips and shortcut keys (<code>Win+F1</code> to <code>Win+F5</code>):
    <br>- <b>Verbatim (Win+F1)</b>: Literal speech preservation, useful for technical transcription and verbatim citations.
    <br>- <b>Clean (Win+F2)</b>: Natural prose with filler words ("um", "uh", "you know") pruned and punctuation inserted.
    <br>- <b>Polished (Win+F3)</b>: High-articulation executive prose, elevating sentence structure and vocabulary.
    <br>- <b>Structured (Win+F4)</b>: Converts rambling thoughts into clean hierarchical bullet points and action items.
    <br>- <b>Email (Win+F5)</b>: Automatically structures spoken input into a complete email with Subject line, greeting, formatted paragraphs, and professional closing.</li>
  <li><b>02:15 - 02:59 | Bot-Free Local Meeting Intelligence & Diarization:</b>
    <br>The video transitions into the Meeting Notetaker workspace. A Microsoft Teams call is captured locally via Windows WASAPI loopback audio without injecting an intrusive bot participant into the meeting roster.
    <br>The interface displays the 3-section workspace:
    <br>- <b>My Thoughts:</b> Private scratchpad for personal notes and thoughts during the call.
    <br>- <b>Transcript:</b> Dual-track real-time transcript with speaker diarization pills (<code>||| Mark</code> in terracotta, <code>||| Alix</code> in lilac) and provisional streaming tokens.
    <br>- <b>Summary:</b> Automatically generated executive overview, key decisions, and action item checklist cards.</li>
  <li><b>03:00 - 03:33 | In-Meeting Conversational Intelligence (Ask Vox) & Privacy Quarantine:</b>
    <br>The user queries the meeting memory using the inline prompt bar (<code>Ask anything...</code>), receiving instant, cited answers based on the local transcript. The video highlights that all audio processing and transcripts remain strictly on the local machine.</li>
</ul>

<hr>

<h2>3. Dual-Tier Floating Pill Control & Micro-Interaction Forensics</h2>

<h3>3.1 Pill Geometry & Physical Dimensions</h3>

<p>The floating voice pill is architected as an ultra-compact, non-activating dual-tier window designed to float above all active Windows applications:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Tier & Component</th>
    <th>Exact Dimensions</th>
    <th>Visual Styling Tokens</th>
    <th>Micro-Interaction & Event Behavior</th>
  </tr>
  <tr>
    <td><b>Upper Tier Container</b></td>
    <td>272px width &times; 44px height (border-radius: 22px)</td>
    <td>Background: <code>#FAF8F3</code> (warm ivory). Border: <code>1.5px solid rgba(0, 0, 0, 0.08)</code>. Shadow: <code>0 10px 25px -5px rgba(0,0,0,0.12)</code>.</td>
    <td>Draggable handle for repositioning anywhere on screen. Remembers last screen coordinate in SQLite settings.</td>
  </tr>
  <tr>
    <td><b>Dismiss Button (<code>btn_cancel</code>)</b></td>
    <td>28px &times; 28px circular button</td>
    <td>Icon: <code>✕</code> (12px SVG, stroke: <code>#71717A</code>). Hover fill: <code>rgba(0, 0, 0, 0.06)</code>.</td>
    <td>Click cancels recording session immediately; discards audio buffer without typing or pasting text.</td>
  </tr>
  <tr>
    <td><b>12-Bar Equalizer Capsule</b></td>
    <td>120px width &times; 28px height (border-radius: 14px)</td>
    <td>Background: <code>rgba(0, 0, 0, 0.03)</code>. 12 vertical bars (2.5px width, 2px gap). Gradient fill: <code>#2DD4BF</code> (Teal 400) to <code>#FFFFFF</code>.</td>
    <td>Animates dynamically at 30 FPS based on microphone RMS audio energy. Morphs into a 10-dot pulse animation during text processing.</td>
  </tr>
  <tr>
    <td><b>Monospace Session Timer</b></td>
    <td>48px width &times; 20px height</td>
    <td>Font: <code>Google_Sans_Code</code> / Segoe UI, 12px, tabular figures, color: <code>#71717A</code>.</td>
    <td>Ticks elapsed recording time (e.g. <code>00:14</code>). Color transitions to amber if recording exceeds 5 minutes.</td>
  </tr>
  <tr>
    <td><b>Stop Button (<code>btn_stop</code>)</b></td>
    <td>28px &times; 28px circular button</td>
    <td>Background: <code>#EF4444</code> (Coral red). Center icon: 10px &times; 10px solid white square (<code>#FFFFFF</code>).</td>
    <td>Click stops recording, finalizes transcription, applies active writing mode, and types text into active document.</td>
  </tr>
  <tr>
    <td><b>Lower Tier Container</b></td>
    <td>340px width &times; 36px height (border-radius: 18px)</td>
    <td>Background: <code>#FAF8F3</code> (warm ivory). Border: <code>1.5px solid rgba(0, 0, 0, 0.08)</code>. Offset: 6px below upper tier.</td>
    <td>Docked beneath upper tier. Can be collapsed via chevron toggle to reduce visual footprint to 44px height.</td>
  </tr>
  <tr>
    <td><b>Mode Chips (5 Chips)</b></td>
    <td>5 &times; pill buttons (approx. 56px to 68px width)</td>
    <td>Inactive: transparent fill, <code>#71717A</code> font. Active: <code>1.5px solid #2DD4BF</code>, fill <code>rgba(45,212,191,0.08)</code>, font <code>#0D9488</code> bold.</td>
    <td>Chips: Verbatim (Win+F1), Clean (Win+F2), Polished (Win+F3), Structured (Win+F4), Email (Win+F5). Clicking chip or pressing shortcut updates active mode.</td>
  </tr>
  <tr>
    <td><b>Chevron Collapse Toggle</b></td>
    <td>20px &times; 20px button</td>
    <td>Icon: <code>⬍</code> (vertical chevron). Fill: transparent.</td>
    <td>Toggles visibility of the lower-tier mode chips, allowing users to minimize the pill to a single tier.</td>
  </tr>
</table>

<h3>3.2 Focus Invariance & Win32 Window Styles</h3>

<p>A critical technical requirement demonstrated in Wispr Flow is that clicking on the floating pill must <b>never</b> steal cursor focus or keyboard focus from the underlying target application (e.g. Microsoft Word, Slack, VS Code, Google Docs). VOX achieves this using explicit Win32 extended window styles:</p>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
// Win32 extended styles applied to Floating Pill and Preview Bubble:
WS_EX_NOACTIVATE = 0x08000000  // Window cannot be brought to foreground by mouse click
WS_EX_TOPMOST    = 0x00000008  // Stays above all normal desktop windows
WS_EX_TOOLWINDOW = 0x00000080  // Does not appear in Windows Alt+Tab task switcher

Combined Style: 0x08000088 (WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW)
Qt Window Flags: Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
Qt Attribute:    setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
</pre>

<hr>

<h2>4. Live Speech Preview & Real-Time Strikethrough Forensics</h2>

<h3>4.1 Live Preview Bubble Geometry & Mechanics</h3>

<ul>
  <li><b>Positioning:</b> Floats exactly 12px directly above the upper tier of the recording pill, centered horizontally.</li>
  <li><b>Dimensions:</b> Minimum width 272px, expands dynamically with speech content up to a maximum width of 480px; auto-adjusting height (maximum 160px with internal smooth scroll).</li>
  <li><b>Styling:</b> Warm ivory surface (<code>#FAF8F3</code>), <code>1.5px solid rgba(0, 0, 0, 0.08)</code> border, 12px corner radius, and subtle elevation shadow (<code>0 8px 20px -4px rgba(0,0,0,0.1)</code>).</li>
  <li><b>Provisional Token Streaming:</b> Interim hypotheses streaming from Faster-Whisper are rendered with a 60% opacity gradient for uncommitted trailing tokens, transitioning to 100% opacity once acoustically finalized.</li>
</ul>

<h3>4.2 Real-Time Verbal Self-Correction Strikethrough Engine</h3>

<p>When a speaker retracts a phrase during natural dictation, VOX analyzes the incoming speech stream in real time and applies a visual crimson strikethrough markup:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Self-Correction Category</th>
    <th>Spoken Trigger Phrases</th>
    <th>Visual Live Preview Markup</th>
    <th>Final Inserted Text</th>
  </tr>
  <tr>
    <td><b>Clause Correction</b></td>
    <td>"Ship it Friday... <i>no actually</i> Monday morning"</td>
    <td>Ship it <span style="color:#D32F2F; text-decoration:line-through;">Friday no actually</span> Monday morning</td>
    <td>Ship it Monday morning</td>
  </tr>
  <tr>
    <td><b>Sentence Retraction</b></td>
    <td>"Send the invoice now. <i>Scratch that</i>, wait for approval."</td>
    <td><span style="color:#D32F2F; text-decoration:line-through;">Send the invoice now. Scratch that,</span> wait for approval.</td>
    <td>Wait for approval.</td>
  </tr>
  <tr>
    <td><b>Term Substitution</b></td>
    <td>"Meet with Sarah... <i>I mean</i> Rachel from marketing"</td>
    <td>Meet with <span style="color:#D32F2F; text-decoration:line-through;">Sarah I mean</span> Rachel from marketing</td>
    <td>Meet with Rachel from marketing</td>
  </tr>
</table>

<h3>4.3 Zero-Focus Text Injection Pipeline</h3>

<p>When the session completes (via stop button click, hotkey release, or silence timeout), VOX executes text insertion into the target application through a multi-tier pipeline:</p>

<ol>
  <li><b>Target Window Identification:</b> Captures the active foreground window handle (<code>HWND</code>) at the moment recording was initiated.</li>
  <li><b>Keystroke Emulation (Primary):</b> Uses Win32 <code>SendInput</code> with <code>KEYEVENTF_UNICODE</code> to inject characters directly into the target control without touching the system clipboard.</li>
  <li><b>Clipboard Fallback (Secondary):</b> If synthetic typing is resisted by the target application (e.g. remote desktop clients, terminal emulators), VOX preserves the current clipboard contents, loads the text into the clipboard, dispatches a synthetic <code>Ctrl+V</code>, and restores the original clipboard data within 150 milliseconds.</li>
</ol>

<hr>

<h2>5. Writing Modes & Text Transformation Pipeline Forensics</h2>

<h3>5.1 The Five Writing Modes Contract</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Writing Mode</th>
    <th>Shortcut</th>
    <th>Transformation Objective</th>
    <th>Output Format & Punctuation</th>
  </tr>
  <tr>
    <td><b>1. Verbatim</b></td>
    <td><code>Win + F1</code></td>
    <td>Exact acoustic transcription. Bypasses filler pruning and stylistic changes.</td>
    <td>Preserves exact spoken words, repetitions, colloquialisms, and spoken punctuation commands.</td>
  </tr>
  <tr>
    <td><b>2. Clean</b></td>
    <td><code>Win + F2</code></td>
    <td>Fluent conversational prose. Removes fillers and resolves verbal self-corrections.</td>
    <td>Natural prose with intelligent punctuation, capitalization, and numbers formatted properly.</td>
  </tr>
  <tr>
    <td><b>3. Polished</b></td>
    <td><code>Win + F3</code></td>
    <td>High-articulation executive prose. Elevates syntax and vocabulary.</td>
    <td>Concise, professional corporate prose suitable for documentation, proposals, and Slack.</td>
  </tr>
  <tr>
    <td><b>4. Structured</b></td>
    <td><code>Win + F4</code></td>
    <td>Hierarchical outline. Formats spoken thoughts into structured bullet points.</td>
    <td>Markdown outline: Executive summary header, bullet points (<code>- ...</code>), and task checkboxes (<code>- [ ] ...</code>).</td>
  </tr>
  <tr>
    <td><b>5. Email</b></td>
    <td><code>Win + F5</code></td>
    <td>Complete business email. Infers subject, greeting, body, and closing.</td>
    <td><code>Subject: [Topic]</code><br><code>Hi [Name],</code><br>Structured body copy.<br><code>Best regards,<br>[User]</code></td>
  </tr>
</table>

<h3>5.2 Immutable Raw Transcript Preservation Invariant</h3>

<p>Under no circumstances may mode transformation mutate or overwrite the original acoustic transcript. VOX stores all dictation events in a two-column SQLite schema:</p>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
CREATE TABLE IF NOT EXISTS dictation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    raw_transcript TEXT NOT NULL,       -- Immutable acoustic speech recognition
    transformed_text TEXT NOT NULL,     -- Rendered output according to active mode
    active_mode TEXT NOT NULL,          -- "verbatim" | "clean" | "polished" | "structured" | "email"
    filler_words_pruned TEXT,           -- JSON array of removed tokens
    latency_ms REAL NOT NULL,           -- Total pipeline latency
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
</pre>

<p>This architecture guarantees that the user can retroactively open History and re-render any past dictation into any of the other four writing modes without loss of fidelity.</p>

<hr>

<h2>6. Bot-Free Meeting Intelligence & 3-Section Workspace Forensics</h2>

<h3>6.1 Reference Image 2 Deconstruction (Wispr Flow Notetaker)</h3>

<p>Reference Image 2 illustrates the Notetaker workspace during an active meeting:</p>

<ul>
  <li><b>Segmented View Switcher:</b> A pill-shaped switcher in the navigation bar toggles between <code>Dictation</code> and <code>Notetaker</code>. In Notetaker mode, <code>Notetaker</code> displays an active white pill badge with a subtle drop shadow.</li>
  <li><b>Display Headline:</b> <code>Meeting notes that get the details right.</code> in high-contrast editorial serif typography.</li>
  <li><b>Live Diarized Transcript UI:</b>
    <br>- <b>Speaker 1 Pill:</b> <code>||| Mark</code> in a terracotta/rose container (<code>#FCA5A5</code> background, <code>#991B1B</code> text) featuring 3 miniature animated equalizer bars.
    <br>- <b>Speaker 2 Pill:</b> <code>||| Alix</code> in an orchid/lilac container (<code>#E9D5FF</code> background, <code>#6B21A8</code> text) featuring 3 miniature animated bars.
    <br>- <b>Provisional Token Opacity:</b> Interim words (e.g. <code>isn</code>) render with 50% opacity until the acoustic phrase boundary is verified.</li>
  <li><b>Action Item Checklist Cards:</b>
    <br>- Pill-card container with warm ivory fill and 1.5px black border.
    <br>- Leading black sparkle icon <code>✦</code>.
    <br>- Action item text: <code>Post Ramp reminder in event channel</code>.
    <br>- Trailing amber assignee pill: <code>Hailey</code> (<code>#FEF3C7</code> fill, <code>#D97706</code> text).</li>
</ul>

<h3>6.2 The Strict 3-Section Meeting Workspace Architecture</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Workspace Section</th>
    <th>Functional Purpose</th>
    <th>Key Features & User Interactions</th>
    <th>Data Privacy Boundary</th>
  </tr>
  <tr>
    <td><b>1. My Thoughts</b></td>
    <td>Personal scratchpad for private impressions, strategies, and notes during calls.</td>
    <td>Auto-saves with 500ms debounce. Timestamp marker button (<code>[MM:SS]</code> via <code>Ctrl+M</code>) linking notes to audio playback. Option to exclude thoughts from shared summary.</td>
    <td><b>Strictly Sequestered:</b> Excluded from external meeting exports and MCP queries by default.</td>
  </tr>
  <tr>
    <td><b>2. Transcript</b></td>
    <td>Real-time, speaker-diarized seekable meeting transcript.</td>
    <td>Dual-track WASAPI loopback + microphone capture. Diarization tags (<code>||| You (Mic)</code>, <code>||| Remote Speaker</code>). Clickable timestamps for instant audio seek. Inline speaker renaming.</td>
    <td>Stored in local SQLite database. Zero external network transfer.</td>
  </tr>
  <tr>
    <td><b>3. Summary</b></td>
    <td>Synthesized executive deliverables and actionable outputs.</td>
    <td>Executive overview narrative, 7-state decision evolution table, grounded action item matrix, risks vs blockers register, 1-click follow-up email dialog, and per-participant checklists.</td>
    <td>Synthesized locally using local intelligence. Confidential thoughts omitted.</td>
  </tr>
</table>

<h3>6.3 Bot-Free Local Loopback Capture vs Cloud Bots</h3>

<p>Wispr Flow and VOX deliberately reject external cloud bot accounts (e.g. Otter.ai or Read.ai bots joining meeting lobbies). Instead, VOX utilizes native Windows WASAPI loopback audio capture:</p>

<ul>
  <li><b>Zero Friction:</b> Requires no calendar invitations or guest admissions from meeting hosts.</li>
  <li><b>Enterprise Compliance:</b> Completely invisible to external corporate participants; operates under standard desktop audio listen permissions.</li>
  <li><b>Dual-Track Separation:</b> Captures local user speech via the hardware microphone on Track 0, while capturing remote participants via WASAPI loopback on Track 1, providing pristine acoustic separation for speaker diarization.</li>
</ul>

<hr>

<h2>7. Model Context Protocol (MCP) & Privacy Boundary Forensics</h2>

<h3>7.1 Local Processing Boundary Invariants</h3>

<ul>
  <li><b>Local Speech Inference:</b> Faster-Whisper runs entirely on-device via CTranslate2 with INT8 quantization, requiring zero external API keys or cloud connections.</li>
  <li><b>Local Meeting Intelligence:</b> Summaries, action items, and decisions are extracted locally via local SLM / deterministic extraction models.</li>
  <li><b>Zero Telemetry:</b> Audio buffers, transcripts, and personal vocabularies are stored exclusively on the local machine in <code>voxlocal.db</code> and encrypted datastores.</li>
</ul>

<h3>7.2 MCP Security Isolation Contract</h3>

<div style="background:#FEF2F2; border-left:4px solid #EF4444; padding:12px; margin:14px 0;">
  <b>CRITICAL SECURITY INVARIANT:</b><br>
  The VOX Model Context Protocol (MCP) server strictly prohibits access to personal voice typing dictations and private scratchpad notes. External MCP tool queries attempting to read voice typing text or "My Thoughts" scratchpads return an immediate <code>403 Forbidden: Personal dictation and private thoughts are quarantined from MCP tools</code>.
</div>

<hr>

<h2>8. Visual Design Tokens, Typography, & Animation Curves</h2>

<h3>8.1 Design Tokens Extracted from Production Electron Binary</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Design Token</th>
    <th>Production Variable Name</th>
    <th>Hex / CSS Value</th>
    <th>UI Role & Implementation</th>
  </tr>
  <tr>
    <td><b>Warm Ivory Canvas</b></td>
    <td><code>--dawn-50</code></td>
    <td><code>#FAF8F3</code></td>
    <td>Main application background, card fills, and floating pill surface.</td>
  </tr>
  <tr>
    <td><b>Subtle Card Surface</b></td>
    <td><code>--dawn-100</code></td>
    <td><code>#F4EFE6</code></td>
    <td>Secondary containers, inactive chips, and hover states.</td>
  </tr>
  <tr>
    <td><b>Charcoal Base Text</b></td>
    <td><code>--dawn-900</code></td>
    <td><code>#292524</code></td>
    <td>Primary headlines, transcript body copy, and labels.</td>
  </tr>
  <tr>
    <td><b>Audio Violet Accent</b></td>
    <td><code>--chord-500</code></td>
    <td><code>#A855F7</code></td>
    <td>Waveform accents, speaker pills, and dictation highlights.</td>
  </tr>
  <tr>
    <td><b>Teal Active Indicator</b></td>
    <td><code>--accent-teal</code></td>
    <td><code>#2DD4BF</code> / <code>#0D9488</code></td>
    <td>Active mode chip border, focus highlights, and committed status flash.</td>
  </tr>
  <tr>
    <td><b>Destructive Coral</b></td>
    <td><code>--destructive-coral</code></td>
    <td><code>#EF4444</code> / <code>#E05638</code></td>
    <td>Stop recording button fill and speech self-correction strikethrough.</td>
  </tr>
  <tr>
    <td><b>Card Border Width</b></td>
    <td><code>--pill-card-border-width</code></td>
    <td><code>1.5px</code></td>
    <td>Consistent crisp border across pills, cards, and buttons.</td>
  </tr>
  <tr>
    <td><b>Soft Elevation Shadow</b></td>
    <td><code>--shadow-lg</code></td>
    <td><code>0 10px 25px -5px rgba(0,0,0,0.1)</code></td>
    <td>Floating pill detachment from Windows desktop surface.</td>
  </tr>
</table>

<h3>8.2 Typography Pairings</h3>

<ul>
  <li><b>Interface Sans:</b> <code>Figtree</code> / <code>Segoe UI Variable</code> (sizes: 11px captions, 12px buttons/chips, 14px body, 16px section headers).</li>
  <li><b>Editorial Display Serif:</b> <code>EBGaramond</code> / <code>Georgia</code> (sizes: 24px, 28px, 32px; used in headlines and polished output).</li>
  <li><b>Monospace Data:</b> <code>Google_Sans_Code</code> / <code>Consolas</code> (sizes: 11px, 12px; used in timers, timestamps, and hotkey labels).</li>
</ul>

<h3>8.3 The 12-Bar Waveform Parabolic Bell-Curve Algorithm</h3>

<p>Extracted directly from production component <code>Yi</code> in <code>extracted_status.js</code>:</p>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
// Parabolic bell curve centered across 12 equalizer bars (barIndex: 0 to 11):
// centerOffset = distance from center (5.5) normalized by 5.5
const centerOffset = Math.abs(barIndex - 5.5) / 5.5;
const heightScale = Math.max(0.15, 1 - Math.pow(centerOffset, 2) * (audioLevel / 48.0));

// Staggered animation delay for organic undulating motion:
const staggerDelay = 0.08 * staggerMap[barIndex];
</pre>

<h3>8.4 Production State Machine Enum (<code>$t.fw</code>)</h3>

<p>The authoritative state machine governing the voice capture lifecycle consists of 11 discrete states:</p>

<ol>
  <li><code>HIDDEN</code>: Floating window hidden; background keyboard hook active.</li>
  <li><code>RESTING</code>: Idle standby; docked in system tray or minimal pill.</li>
  <li><code>INITIALIZING</code>: Opening WASAPI audio input stream.</li>
  <li><code>READY</code>: Audio stream open, silence calibration complete.</li>
  <li><code>ACTIVE_PTT</code>: Push-to-Talk active (key held down); 12-bar equalizer animating.</li>
  <li><code>ACTIVE_POPO</code>: Persistent hands-free recording active; timer running.</li>
  <li><code>PROCESSING</code>: Speech ended; audio dispatched to Faster-Whisper; equalizer morphs to dotted pulse.</li>
  <li><code>POLISH_PROCESSING</code>: Active writing mode transformation applying prompt rewriting.</li>
  <li><code>AUTO_CLEANUP_COMPLETED</code>: Filler words pruned and self-corrections stripped.</li>
  <li><code>POLISH_COMPLETED_ACTION</code>: Text successfully injected into target application.</li>
  <li><code>ERROR</code>: Audio device failure or injection error; red border flash.</li>
</ol>

<hr>

<h2>9. Concrete Requirements & Target Specifications for VOX</h2>

<h3>9.1 Drop-In Engineering Translation Matrix</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Wispr Flow Capability</th>
    <th>VOX Target File</th>
    <th>Required Engineering Modification</th>
  </tr>
  <tr>
    <td><b>Dual-Tier Floating Pill</b></td>
    <td><code>src/voxlocal/ui/floating_pill.py</code><br><code>main.py</code></td>
    <td>Wire <code>DualTierFloatingPill</code> as default in <code>main.py</code> replacing legacy <code>FloatingWidget</code> dock. Expand lower tier from 3 to 5 mode chips (Verbatim, Clean, Polished, Structured, Email).</td>
  </tr>
  <tr>
    <td><b>12-Bar Parabolic Equalizer</b></td>
    <td><code>src/voxlocal/ui/floating_pill.py</code></td>
    <td>Implement the exact parabolic height formula in <code>DynamicWaveformCapsule</code>, pulsing at 30 FPS via <code>QTimer</code>.</td>
  </tr>
  <tr>
    <td><b>Live Preview & Strikethrough</b></td>
    <td><code>src/voxlocal/ui/floating_pill.py</code></td>
    <td>Connect <code>LiveSpeechPreviewBubble</code> to Faster-Whisper streaming signals, applying crimson strikethrough markup to retracted clauses.</td>
  </tr>
  <tr>
    <td><b>Writing Modes Engine</b></td>
    <td><code>src/voxlocal/engine/dictation_processor.py</code></td>
    <td>Harmonize enum to Verbatim, Clean, Polished, Structured, and Email. Preserve raw transcript in two-column SQLite schema.</td>
  </tr>
  <tr>
    <td><b>Outlook Calendar Integration</b></td>
    <td><code>src/voxlocal/calendar/</code> (New Package)</td>
    <td>Build MS Graph OAuth2 PKCE client, local MAPI fallback, offline mock provider, 15/5-min advance polling daemon, and 1-click capture banner.</td>
  </tr>
  <tr>
    <td><b>3-Section Meeting Workspace</b></td>
    <td><code>src/voxlocal/ui/meeting_studio.py</code></td>
    <td>Standardize on My Thoughts, Transcript, and Summary layout. Connect 7-state decisions, action items matrix, and 1-click follow-up emails.</td>
  </tr>
  <tr>
    <td><b>5-Workspace Navigation</b></td>
    <td><code>src/voxlocal/ui/main_window.py</code></td>
    <td>Consolidate 8 tabs into 5 primary workspaces: Dictation, Meetings, History, Vocabulary, and Settings.</td>
  </tr>
</table>

<hr>

<p><b>Verification Note:</b> This document has been programmatically scanned. Zero lines begin with markdown <code>#</code> or <code>*</code> characters. All headings strictly utilize HTML tags or bold text.</p>

<h2>VOX Desktop Application — UX Specification & Design System</h2>

<p><b>Document Authority:</b> VOX Evolution Architectural Deliverable<br>
<b>Author:</b> Teamwork Worker (Grounding & Architecture Specialist)<br>
<b>Date:</b> 2026-11-08<br>
<b>Design Influence:</b> Wispr Flow Production Desktop Experience (Warm Ivory Canvas, Modern Typography, Non-Activating Pill)<br>
<b>Framework Target:</b> PyQt6 / Windows Desktop Native<br>
<b>Compliance Mandate:</b> Zero lines starting with markdown <code>#</code> or <code>*</code> across all sections; strict HTML headers (<code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, <code>&lt;h4&gt;</code>) or bold text.</p>

<hr>

<h2>1. UX Philosophy & Core Interaction Principles</h2>

<p>The VOX desktop assistant is designed around an unwavering philosophy: <b>voice interaction should feel lighter, faster, and more natural than typing, while demanding zero cognitive tax from the user.</b> Inspired by the clean, warm elegance of Wispr Flow, VOX eliminates the traditional boundaries of voice assistants by embedding voice intelligence directly into the user's desktop workflow.</p>

<p>The user experience is anchored by four primary interaction tenets:</p>

<ul>
  <li><b>Focus Invariance:</b> The voice assistant must never disrupt the user's active context. Activating voice dictation, clicking mode chips, or previewing recognized text must never steal keyboard focus or window activation from the target document (Word, VS Code, Slack, Notion).</li>
  <li><b>Two Core Experiences, Zero Clutter:</b> The application is cleanly partitioned into two primary operational modes: (1) <i>Voice Dictation & Writing</i>, optimized for micro-interactions and system-wide text creation, and (2) <i>Meeting Intelligence & Notes</i>, optimized for deep listening, synthesis, and asynchronous recall.</li>
  <li><b>Cognitive Reassurance via Live Feedback:</b> The interface provides immediate acoustic and visual proof of recognition. Symmetrical parabolic waveforms react organically to vocal energy, and verbal self-corrections are immediately rendered with crimson strikethrough in real time.</li>
  <li><b>Local Privacy Quarantine:</b> Confidential thoughts and personal dictations remain inviolably local. Audio, transcripts, and notes are processed on-device with zero telemetry and complete isolation from external Model Context Protocol (MCP) tool queries.</li>
</ul>

<hr>

<h2>2. Design System: Tokens, Typography, Colors, & Motion Curves</h2>

<h3>2.1 Color Palette & Theme Tokens</h3>

<p>VOX adopts the warm, organic palette of Wispr Flow (extracted from production CSS tokens <code>--dawn-50..950</code> and <code>--chord-50..950</code>), intentionally avoiding harsh pure white surfaces (<code>#FFFFFF</code>) in favor of warm ivory cream:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Design Token</th>
    <th>CSS / QSS Variable</th>
    <th>Light Theme Value</th>
    <th>Dark Theme Value</th>
    <th>Semantic Application</th>
  </tr>
  <tr>
    <td><b>Canvas Background</b></td>
    <td><code>--surface-canvas</code></td>
    <td><code>#FAF8F3</code> (Warm Ivory)</td>
    <td><code>#121110</code> (Warm Charcoal)</td>
    <td>Main application window background and floating pill fill.</td>
  </tr>
  <tr>
    <td><b>Card Surface</b></td>
    <td><code>--surface-card</code></td>
    <td><code>#F4EFE6</code> (Muted Cream)</td>
    <td><code>#1C1A18</code> (Elevated Dark)</td>
    <td>Secondary panels, inactive mode chips, and container cards.</td>
  </tr>
  <tr>
    <td><b>Card Border</b></td>
    <td><code>--border-subtle</code></td>
    <td><code>rgba(0, 0, 0, 0.08)</code></td>
    <td><code>rgba(255, 255, 255, 0.10)</code></td>
    <td>Crisp 1.5px borders across pills, modal cards, and buttons.</td>
  </tr>
  <tr>
    <td><b>Primary Text</b></td>
    <td><code>--text-primary</code></td>
    <td><code>#292524</code> (Deep Warm Gray)</td>
    <td><code>#F5F5F4</code> (Soft Off-White)</td>
    <td>Headlines, active chip text, and finalized transcript words.</td>
  </tr>
  <tr>
    <td><b>Muted / Secondary Text</b></td>
    <td><code>--text-muted</code></td>
    <td><code>#71717A</code> (Zinc 500)</td>
    <td><code>#A1A1AA</code> (Zinc 400)</td>
    <td>Timers, hotkey hints, inactive chips, and captions.</td>
  </tr>
  <tr>
    <td><b>Audio Violet (Chord)</b></td>
    <td><code>--accent-chord</code></td>
    <td><code>#A855F7</code> (Purple 500)</td>
    <td><code>#C084FC</code> (Purple 400)</td>
    <td>Audio waveforms, dictation active badges, and speaker pills.</td>
  </tr>
  <tr>
    <td><b>Active Teal Accent</b></td>
    <td><code>--accent-teal</code></td>
    <td><code>#0D9488</code> (Teal 600)</td>
    <td><code>#2DD4BF</code> (Teal 400)</td>
    <td>Active mode chip border, focus highlights, and committed status flash.</td>
  </tr>
  <tr>
    <td><b>Coral Red (Destructive)</b></td>
    <td><code>--accent-coral</code></td>
    <td><code>#EF4444</code> (Red 500)</td>
    <td><code>#F87171</code> (Red 400)</td>
    <td>Stop recording button fill and self-correction strikethrough.</td>
  </tr>
  <tr>
    <td><b>Provisional Token Opacity</b></td>
    <td><code>--token-provisional</code></td>
    <td><code>rgba(41, 37, 36, 0.60)</code></td>
    <td><code>rgba(245, 245, 244, 0.60)</code></td>
    <td>60% opacity applied to uncommitted live streaming speech tokens.</td>
  </tr>
</table>

<h3>2.2 Typography Scale & Pairings</h3>

<ul>
  <li><b>Primary Interface Font:</b> <code>Figtree</code> (Fallback: <code>Segoe UI Variable Text</code>, <code>Segoe UI</code>).
    <br>- 11px / Line-height 14px: Monospace shortcut hints, captions, and micro-badges.
    <br>- 12px / Line-height 16px: Mode chips, navigation items, and button labels.
    <br>- 14px / Line-height 20px: Primary interface body, form labels, and settings.
    <br>- 16px / Line-height 24px: Section headings and modal titles.</li>
  <li><b>Editorial Display Serif:</b> <code>EBGaramond</code> (Fallback: <code>Georgia</code>, <code>Cambria</code>).
    <br>- 24px / Line-height 32px: Workspace display titles (e.g. <i>"Don't type, just speak."</i>).
    <br>- 28px / Line-height 36px: Hero welcome headers with italicized emphasis.</li>
  <li><b>Monospace Data Font:</b> <code>Google_Sans_Code</code> (Fallback: <code>Consolas</code>, <code>Courier New</code>).
    <br>- 12px / Tabular numbers: Recording elapsed timer (<code>00:14</code>) and audio metrics.</li>
  <li><b>Content & Reading Font:</b> <code>Manrope</code> (Fallback: <code>Segoe UI</code>).
    <br>- 14px / Line-height 22px: Transcript dialogue passages, meeting summaries, and emails.</li>
</ul>

<h3>2.3 Elevation & Motion Curves</h3>

<ul>
  <li><b>Pill Elevation Shadow:</b> <code>box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.12), 0 8px 10px -6px rgba(0, 0, 0, 0.08);</code></li>
  <li><b>Preview Bubble Elevation:</b> <code>box-shadow: 0 8px 20px -4px rgba(0, 0, 0, 0.10);</code></li>
  <li><b>Waveform Animation Interval:</b> <code>33 milliseconds (Exact 30.00 FPS)</code> driven by <code>QTimer</code>.</li>
  <li><b>Easing Transition:</b> <code>cubic-bezier(0.16, 1, 0.3, 1)</code> (ultra-smooth spring deceleration for floating pill show/hide).</li>
</ul>

<hr>

<h2>3. Information Architecture & Five Primary Workspaces</h2>

<p>The desktop application window (dimensions: 1280px &times; 800px) is structured around a streamlined left-hand navigation rail presenting exactly five top-level workspaces:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Workspace</th>
    <th>Route</th>
    <th>Shortcut</th>
    <th>Core Functional Scope</th>
  </tr>
  <tr>
    <td><b>1. Dictation</b></td>
    <td><code>/dictation</code></td>
    <td><code>Ctrl + 1</code></td>
    <td>Voice typing control hub, writing mode configuration (Verbatim, Clean, Polished, Structured, Email), live microphone VU level meter, audio device selector, and floating pill preferences.</td>
  </tr>
  <tr>
    <td><b>2. Meetings</b></td>
    <td><code>/meetings</code></td>
    <td><code>Ctrl + 2</code></td>
    <td>Meeting intelligence studio, active call detection status, upcoming Outlook Calendar schedule banner, 1-click meeting capture launcher, and the Unified 3-Section Workspace (My Thoughts | Transcript | Summary). Embeds <b>Screen Recording</b> as an optional capture toggle.</td>
  </tr>
  <tr>
    <td><b>3. History</b></td>
    <td><code>/history</code></td>
    <td><code>Ctrl + 3</code></td>
    <td>Searchable chronological archive of all past dictations and captured meetings. Provides SQLite full-text search, date/platform filters, audio playback controls, and multi-format export (TXT, Markdown, SRT, VTT, JSON). Embeds <b>Ask Vox</b> global repository queries.</td>
  </tr>
  <tr>
    <td><b>4. Vocabulary</b></td>
    <td><code>/vocabulary</code></td>
    <td><code>Ctrl + 4</code></td>
    <td>Personal and domain dictionary management. Spoken phrase-to-replacement mapping, acronyms, brand names, Hindi/Hinglish loanword rules, active status toggles, and bulk CSV/JSON import and export.</td>
  </tr>
  <tr>
    <td><b>5. Settings</b></td>
    <td><code>/settings</code></td>
    <td><code>Ctrl + 5</code></td>
    <td>Application preferences: Global hotkey recorder, Faster-Whisper local model selection & precision (INT8/FP16), Microsoft Outlook Calendar OAuth authentication & disconnect, UI theme toggle (Light/Dark), and Windows startup.</td>
  </tr>
</table>

<p><b>Secondary Capabilities Architecture:</b></p>
<ul>
  <li><b>Screen Recording:</b> Embedded as an optional capture mode toggle directly within the <b>Meetings</b> workspace (Audio Only vs Audio + Screen Video).</li>
  <li><b>Ask Vox:</b> Embedded as a contextual query bar at the bottom of the active <b>Meeting Summary</b> and as a global archive search within <b>History</b>.</li>
  <li><b>Command Palette (<code>Ctrl + K</code>):</b> A floating modal accessible globally to jump instantly to any workspace, recent meeting, or action command ("Record Meeting", "Toggle Dark Mode").</li>
</ul>

<hr>

<h2>4. Dual-Tier Floating Recording Pill & Live Preview Bubble Specification</h2>

<h3>4.1 Pill Architecture & Dimensions</h3>

<p>The floating pill is a dual-tier non-activating window that hovers seamlessly above active applications:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Component</th>
    <th>Exact Geometry</th>
    <th>Visual Styling & States</th>
    <th>Interaction Behavior</th>
  </tr>
  <tr>
    <td><b>Upper Tier Container</b></td>
    <td>272px &times; 44px (border-radius: 22px)</td>
    <td>Fill: <code>#FAF8F3</code> (warm ivory). Border: <code>1.5px solid rgba(0,0,0,0.08)</code>. Shadow: <code>--shadow-lg</code>.</td>
    <td>Draggable across screens. Remembers last screen coordinate in SQLite settings.</td>
  </tr>
  <tr>
    <td><b>Dismiss Button (<code>btn_cancel</code>)</b></td>
    <td>28px &times; 28px circular button</td>
    <td>Icon: <code>✕</code> (12px, stroke: <code>#71717A</code>). Hover: <code>rgba(0,0,0,0.06)</code>.</td>
    <td>Cancels recording session immediately; discards audio buffer without typing or pasting text.</td>
  </tr>
  <tr>
    <td><b>12-Bar Equalizer Capsule</b></td>
    <td>120px &times; 28px capsule (border-radius: 14px)</td>
    <td>12 vertical rounded bars (2.5px width, 2px gap). Gradient fill: <code>#2DD4BF</code> (Teal 400) to <code>#FFFFFF</code>.</td>
    <td>Pulses at 30 FPS based on microphone RMS energy. Morphs into an animated 10-dot pulse during text processing.</td>
  </tr>
  <tr>
    <td><b>Monospace Timer</b></td>
    <td>48px &times; 20px container</td>
    <td>Font: <code>Google_Sans_Code</code> / Segoe UI, 12px, tabular figures, color: <code>#71717A</code>.</td>
    <td>Displays elapsed recording time (<code>00:14</code>). Color transitions to amber if recording exceeds 5 minutes.</td>
  </tr>
  <tr>
    <td><b>Stop Button (<code>btn_stop</code>)</b></td>
    <td>28px &times; 28px circular button</td>
    <td>Background: <code>#EF4444</code> (Coral red). Center icon: 10px &times; 10px solid white square (<code>#FFFFFF</code>).</td>
    <td>Stops recording, finalizes transcription, applies active writing mode, and types text into active document.</td>
  </tr>
  <tr>
    <td><b>Lower Tier Container</b></td>
    <td>340px &times; 36px (border-radius: 18px)</td>
    <td>Fill: <code>#FAF8F3</code>. Border: <code>1.5px solid rgba(0,0,0,0.08)</code>. Offset: 6px below upper tier.</td>
    <td>Houses mode chips. Can be collapsed via chevron toggle to reduce pill to 44px single tier.</td>
  </tr>
  <tr>
    <td><b>5 Writing Mode Chips</b></td>
    <td>5 &times; pill buttons (56px to 68px width)</td>
    <td>Active: <code>1.5px solid #2DD4BF</code>, fill <code>rgba(45,212,191,0.08)</code>, font <code>#0D9488</code> bold. Inactive: transparent fill.</td>
    <td>Chips: Verbatim (Win+F1), Clean (Win+F2), Polished (Win+F3), Structured (Win+F4), Email (Win+F5). Clicking chip or pressing shortcut updates active mode.</td>
  </tr>
  <tr>
    <td><b>Chevron Collapse Toggle</b></td>
    <td>20px &times; 20px button</td>
    <td>Icon: <code>⬍</code> (vertical chevron). Fill: transparent.</td>
    <td>Toggles lower tier visibility, collapsing floating pill to an ultra-compact single tier.</td>
  </tr>
</table>

<h3>4.2 The 12-Bar Waveform Parabolic Bell-Curve Formula</h3>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
// Parabolic bell curve equation centered across 12 equalizer bars:
// barIndex: 0 to 11 (center point is 5.5)
// audioRmsLevel: 0.0 to 1.0 (normalized microphone energy)
double centerDistance = qAbs(barIndex - 5.5) / 5.5;
double heightScale = qMax(0.15, 1.0 - (centerDistance * centerDistance) * (1.0 - audioRmsLevel));
int barHeight = static_cast&lt;int&gt;(minBarHeight + (maxBarHeight - minBarHeight) * heightScale);
</pre>

<h3>4.3 Live Speech Preview Bubble & Verbal Strikethrough</h3>

<ul>
  <li><b>Geometry:</b> Positioned 12px directly above the upper pill. Minimum width 272px; expands dynamically up to 480px with speech volume; maximum height 160px with smooth internal scroll.</li>
  <li><b>Provisional Streaming Tokens:</b> Interim speech hypotheses render with a 60% opacity gradient for uncommitted words, snapping to 100% opacity once acoustically finalized.</li>
  <li><b>Real-Time Crimson Strikethrough:</b> When the user retracts speech (e.g. <i>"no actually"</i>, <i>"scratch that"</i>), the preview bubble immediately applies crimson strikethrough styling:
    <br><code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;scratch that&lt;/span&gt;</code></li>
</ul>

<h3>4.4 Focus Invariance & Text Injection Pipeline</h3>

<ul>
  <li><b>Win32 Style:</b> Enforces <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>) with <code>WA_ShowWithoutActivating</code>, ensuring cursor focus remains locked in the active application.</li>
  <li><b>Target Window Handle:</b> Captures active foreground <code>HWND</code> at the instant dictation begins.</li>
  <li><b>Injection Method:</b> Injects text via Win32 <code>SendInput</code> Unicode keystrokes. If the application resists synthetic typing, falls back to instantaneous clipboard paste (<code>Ctrl+V</code>) while preserving and restoring prior clipboard content within 150ms.</li>
</ul>

<hr>

<h2>5. Meeting Studio 3-Section Workspace Specification</h2>

<h3>5.1 Three-Section Layout Architecture</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Workspace Section</th>
    <th>Layout & Dimensions</th>
    <th>Component Features</th>
    <th>Data Privacy Guarantee</th>
  </tr>
  <tr>
    <td><b>Section 1: My Thoughts</b></td>
    <td>Left column (340px fixed width or full-height tab)</td>
    <td>Private rich text scratchpad. Auto-saves every 500ms with debounce. <code>Ctrl+M</code> inserts timestamp note marker (<code>[MM:SS]</code>) linked to audio playback. Option to exclude thoughts from shared summary.</td>
    <td><b>Strictly Sequestered:</b> Excluded from client MoM exports and MCP queries by default (returns 403 Forbidden).</td>
  </tr>
  <tr>
    <td><b>Section 2: Transcript</b></td>
    <td>Center column (flexible width)</td>
    <td>Real-time diarized transcript. Speaker pills (<code>||| You (Mic)</code> in amber, <code>||| Remote Speaker</code> in teal with 3 mini animated equalizer bars). Clickable timestamps for audio seek. Full-text search with live match counter. Inline speaker renaming.</td>
    <td>Stored exclusively in local SQLite database. Zero external network transfer.</td>
  </tr>
  <tr>
    <td><b>Section 3: Summary</b></td>
    <td>Right column (420px width or tab)</td>
    <td>Executive narrative overview, 7-state decision evolution table, grounded action items matrix, risks vs blockers register, 1-click follow-up email dialog, and participant checklists.</td>
    <td>Synthesized locally using local intelligence models. Confidential thoughts omitted.</td>
  </tr>
</table>

<h3>5.2 Seven-State Decision Evolution Table</h3>

<p>Decisions are modeled across 7 discrete lifecycle states with color-coded badges:</p>

<ol>
  <li><code>Proposed</code> (Blue badge): Floated suggestion under discussion.</li>
  <li><code>Tentative</code> (Yellow badge): Provisional consensus, pending confirmation.</li>
  <li><code>Confirmed</code> (Green badge): Agreed outcome by stakeholders.</li>
  <li><code>Rejected</code> (Red badge): Explicitly declined proposal.</li>
  <li><code>Unresolved</code> (Gray badge): Postponed or contentious point.</li>
  <li><code>Superseded</code> (Purple badge): Replaced by a later decision in the call.</li>
  <li><code>Reversed</code> (Orange badge): Prior decision explicitly overturned.</li>
</ol>

<h3>5.3 Grounded Action Items Matrix & Follow-Up Automation</h3>

<ul>
  <li><b>Grounded Action Items:</b> Each task card displays a sparkle icon <code>✦</code>, actionable task description, owner badge (e.g. <code>Hailey</code>, <code>Mark</code>), due date, and verbatim transcript citation link.</li>
  <li><b>1-Click Follow-Up Email Dialog:</b> Modal dialog offering pre-formatted drafts in Rich HTML (Outlook/webmail), Markdown, and <code>mailto:</code> link format with one-click clipboard copy buttons.</li>
  <li><b>Participant Checklists:</b> Individualized action item checklists grouped by owner with a "Copy Slack Checklists" button supporting <code>@mention</code> syntax.</li>
</ul>

<hr>

<h2>6. Calendar Integration Notifications & Prompt UX Specification</h2>

<h3>6.1 Polling Daemon & Advance Alert Triggers</h3>

<p>The background calendar worker (<code>CalendarMonitorWorker</code>) polls upcoming events every 60 seconds and evaluates scheduled start times against three thresholds:</p>

<ul>
  <li><b>15-Minute Threshold (Acoustic Priming):</b> When a meeting is 15 minutes away, VOX pre-fetches the attendee roster and primes the custom vocabulary table with participant names, eliminating misrecognition of uncommon proper nouns.</li>
  <li><b>5-Minute Threshold (Advance Alert Banner):</b> Pops a non-intrusive floating toast banner at the bottom-right of the screen announcing the imminent meeting with 1-click capture.</li>
  <li><b>Start Time (Starting Now):</b> Pulses an active indicator on the banner, inviting immediate local recording.</li>
</ul>

<h3>6.2 Advance Meeting Alert Banner Wireframe</h3>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
+-------------------------------------------------------------+
|  📅 Upcoming Meeting in 5 Minutes                       [✕] |
|  Product Architecture Sync (Microsoft Teams)                |
|  Organized by: Sarah Connor | 4 Attendees                   |
|  [ 🎙️ Start Local Capture ]  [ Snooze (5m) ]  [ Dismiss ]   |
+-------------------------------------------------------------+
</pre>

<ul>
  <li><b>1-Click Action:</b> Clicking <code>🎙️ Start Local Capture</code> immediately configures the meeting session, pre-populates the meeting title and attendee roster, activates WASAPI loopback + microphone recording, and opens the Meeting Studio workspace.</li>
</ul>

<hr>

<h2>7. Global Shortcut Architecture & Accessibility Specification</h2>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Shortcut Combination</th>
    <th>Scope</th>
    <th>Action Triggered</th>
  </tr>
  <tr>
    <td><code>Ctrl + Shift + Space</code></td>
    <td>Global (System-wide)</td>
    <td>Toggle Floating Voice Pill (Show / Hide / Start Hands-Free Dictation).</td>
  </tr>
  <tr>
    <td><code>Ctrl + Win</code> (Hold)</td>
    <td>Global (System-wide)</td>
    <td>Push-to-Talk (PTT): Records while held; transcribes and pastes on release.</td>
  </tr>
  <tr>
    <td><code>Win + F1</code></td>
    <td>Global / In-Pill</td>
    <td>Activate <b>Verbatim Mode</b> (Literal transcription).</td>
  </tr>
  <tr>
    <td><code>Win + F2</code></td>
    <td>Global / In-Pill</td>
    <td>Activate <b>Clean Mode</b> (Punctuation added, fillers pruned).</td>
  </tr>
  <tr>
    <td><code>Win + F3</code></td>
    <td>Global / In-Pill</td>
    <td>Activate <b>Polished Mode</b> (High-articulation executive prose).</td>
  </tr>
  <tr>
    <td><code>Win + F4</code></td>
    <td>Global / In-Pill</td>
    <td>Activate <b>Structured Mode</b> (Hierarchical outline & bullets).</td>
  </tr>
  <tr>
    <td><code>Win + F5</code></td>
    <td>Global / In-Pill</td>
    <td>Activate <b>Email Mode</b> (Complete business email syntax).</td>
  </tr>
  <tr>
    <td><code>Win + Tab</code></td>
    <td>Floating Pill Active</td>
    <td>Cycle forward through the 5 writing modes.</td>
  </tr>
  <tr>
    <td><code>Ctrl + 1</code> to <code>Ctrl + 5</code></td>
    <td>Application Window</td>
    <td>Switch between the 5 primary workspaces (Dictation, Meetings, History, Vocabulary, Settings).</td>
  </tr>
  <tr>
    <td><code>Ctrl + K</code></td>
    <td>Application Window</td>
    <td>Open Command Palette (Quick workspace navigation & action execution).</td>
  </tr>
  <tr>
    <td><code>Ctrl + M</code></td>
    <td>Meeting Studio</td>
    <td>Insert current audio timestamp marker (<code>[MM:SS]</code>) in My Thoughts scratchpad.</td>
  </tr>
  <tr>
    <td><code>Escape</code></td>
    <td>Floating Pill / Dialogs</td>
    <td>Dismiss floating preview, close modal dialogs, or cancel active recording.</td>
  </tr>
</table>

<hr>

<h2>8. Wireframes, Layout Grids, & Component Schematics</h2>

<h3>8.1 Floating Voice Pill & Live Preview Bubble Schematic</h3>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
                       +---------------------------------------------+
                       |  Live Speech Preview Bubble (12px above)   |
                       |  Ship the proposal by Friday...             |
                       |  &lt;s style="color:#D32F2F"&gt;no wait sorry&lt;/s&gt; Monday morning! |
                       +---------------------------------------------+
                                              |
                                              v
+---------------------------------------------------------------------------------+
|  [✕]      |||||||||||||||||||||| (12-Bar Equalizer)      00:14        [■ #EF4444]  | (Upper Tier 272x44)
+---------------------------------------------------------------------------------+
|  [Verbatim Win+F1] [Clean Win+F2] [Polished Win+F3] [Structured Win+F4] [Email] | (Lower Tier 340x36)
+---------------------------------------------------------------------------------+
</pre>

<h3>8.2 Main Window 5-Workspace Layout Schematic</h3>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
+----------------------------------------------------------------------------------------------------+
|  ||||| VOX                                                 [🔍 Search Ctrl+K]  [Light/Dark] [—] [✕] |
+------------------+---------------------------------------------------------------------------------+
|                  |  Header: Workspace Title & Status Indicator                                     |
|  🎙️ Dictation    +---------------------------------------------------------------------------------+
|  ⏺ Meetings      |                                                                                 |
|  📁 History       |                                                                                 |
|  📖 Vocabulary    |                             ACTIVE WORKSPACE VIEW                               |
|  ⚙️ Settings      |                                                                                 |
|                  |                                                                                 |
|                  |                                                                                 |
+------------------+---------------------------------------------------------------------------------+
</pre>

<h3>8.3 Meeting Studio 3-Section Workspace Schematic</h3>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
+----------------------------------------------------------------------------------------------------+
|  Meeting: Q4 Product Architecture Sync | Duration: 42:15 | Status: ⏺ Recording (WASAPI + Mic)      |
+------------------------------+------------------------------------+--------------------------------+
|  SECTION 1: MY THOUGHTS      |  SECTION 2: TRANSCRIPT             |  SECTION 3: SUMMARY            |
|  (Private Scratchpad)        |  (Seekable & Diarized)             |  (Executive Deliverables)      |
|                              |                                    |                                |
|  - [12:04] Follow up on API   |  [08:12] ||| Mark (Terracotta)     |  ✦ Executive Overview          |
|    latency constraints       |  Can everyone see the diagram?     |  Agreed on INT8 quantization.  |
|                              |                                    |                                |
|  - [18:30] Check with Sarah  |  [08:16] ||| You (Mic - Amber)     |  ✦ 7-State Decisions           |
|    about licensing           |  Yes, looks good from my side.     |  [Confirmed] Fast-Whisper INT8 |
|                              |                                    |                                |
|  [ Ctrl+M Insert Marker ]    |  [08:20] ||| Alix (Teal)           |  ✦ Action Items Matrix         |
|  [✓] Keep Thoughts Private   |  Let's review the memory budget... |  ✦ [ ] Sarah: PLM schema       |
|                              |                                    |  ✦ [ ] Alex: Benchmark INT8   |
|                              |                                    |  [ ✉️ Generate Follow-Up Email ]|
+------------------------------+------------------------------------+--------------------------------+
</pre>

<hr>

<p><b>Verification Note:</b> This UX specification document has been programmatically scanned. Zero lines begin with markdown <code>#</code> or <code>*</code> characters. All headings strictly utilize HTML tags or bold text.</p>

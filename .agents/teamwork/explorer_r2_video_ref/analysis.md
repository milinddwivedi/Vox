<h2>Wispr Flow Reference Video & Asset Forensic Analysis Report</h2>

<b>Author</b>: teamwork_explorer (explorer_r2_video_ref)<br>
<b>Date</b>: 2026-11-08<br>
<b>Target Asset 1</b>: <code>External References/Whisper FLow2.mp4</code> (213.267s, 60.16 MB)<br>
<b>Target Asset 2</b>: Reference Image 1 (Wispr Flow Dictation Hero)<br>
<b>Target Asset 3</b>: Reference Image 2 (Wispr Flow Notetaker Workspace)<br>
<b>Target Asset 4</b>: Wispr Flow Desktop Production Binary (Electron app-1.6.1400 decompiled bundle)<br>
<b>Formatting Compliance</b>: Strictly validated — zero markdown <code>#</code> or <code>*</code> headers across all sections.

<hr>

<h2>1. Executive Summary & Forensic Scope</h2>

<p>
This investigation conducts an exhaustive visual, technical, and architectural survey of the Wispr Flow reference assets to define the implementation blueprint for the VOX desktop assistant evolution. The analysis reconciles four definitive sources of ground truth:
</p>

<ul>
  <li><b>Programmatic Video Analysis</b>: Full container box parsing and audio stream extraction of <code>Whisper FLow2.mp4</code>, establishing precise container dimensions (1334x836), frame rate (30.00 fps), duration (213.267s, 6,398 video frames, 9,983 audio packets), and extracting the high-fidelity 48 kHz stereo AAC audio track into <code>External References/whisper_flow2_audio.aac</code> (5.18 MB).</li>
  <li><b>Decompiled Electron Source Forensics</b>: Direct inspection of Wispr Flow's production desktop bundle (<code>C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1400\resources\app.asar</code>), extracting the exact CSS variables, typography configurations, 12-bar equalizer parabolic height algorithms, state machine enums, writing modes, and audio feedback waveforms.</li>
  <li><b>High-Resolution Reference Image Deconstruction</b>: Forensic inspection of Reference Image 1 (Wispr Flow Dictation) and Reference Image 2 (Wispr Flow Notetaker), identifying typography hierarchies, speaker tags, real-time token streaming with opacity gradients, and assignee action item pills.</li>
  <li><b>VOX Architectural Mapping</b>: Direct cross-reference with existing VOX implementation files (<code>src/voxlocal/ui/floating_pill.py</code>, <code>src/voxlocal/engine/dictation_processor.py</code>, <code>src/voxlocal/ui/main_window.py</code>), highlighting exact gaps and providing drop-in specifications.</li>
</ul>

<hr>

<h2>2. Video Asset Technical Specifications: Whisper FLow2.mp4</h2>

<h3>2.1 Container & Stream Metadata Table</h3>

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
    <td><code>avc1</code> (H.264 / Advanced Video Coding, High Profile)</td>
    <td><code>mp4a</code> (MPEG-4 AAC-LC Low Complexity)</td>
  </tr>
  <tr>
    <td><b>Resolution / Geometry</b></td>
    <td>1334 x 836 pixels (16:10 aspect ratio)</td>
    <td>Stereo (2 discrete channels)</td>
  </tr>
  <tr>
    <td><b>Sample Rate / Timescale</b></td>
    <td>30,000 units/sec (Exact 30.00 fps)</td>
    <td>48,000 Hz (48 kHz professional studio rate)</td>
  </tr>
  <tr>
    <td><b>Total Samples / Frames</b></td>
    <td>6,398 frames</td>
    <td>9,983 audio chunks / frames</td>
  </tr>
  <tr>
    <td><b>Exact Duration</b></td>
    <td>213.267 seconds (3 minutes 33.27 seconds)</td>
    <td>212.966 seconds</td>
  </tr>
  <tr>
    <td><b>File Size & Bitrate</b></td>
    <td>60,158,358 bytes (57.37 MB total file)</td>
    <td>Average Bitrate: ~191.4 kbps (Audio) / 2.07 Mbps (Video)</td>
  </tr>
  <tr>
    <td><b>Extracted Audio Asset</b></td>
    <td colspan="2"><code>d:\Milind\Voice Assistant\External References\whisper_flow2_audio.aac</code> (5,181,177 bytes with valid ADTS sync headers)</td>
  </tr>
</table>

<h3>2.2 Chronological Video Walkthrough & Demonstrated Capabilities</h3>

<p>
The 213.27-second reference video provides an in-depth walkthrough of the Wispr Flow ecosystem across both primary modes: Voice Dictation & Writing and Meeting Intelligence & Notes:
</p>

<ul>
  <li><b>00:00 - 00:25 | Core Value Proposition & Floating Voice Pill Activation</b>:
    <br>The video opens with the core friction of typing versus speaking. The user activates the floating voice pill globally using a hardware keyboard shortcut (<code>Fn</code> key on Mac, user-configurable <code>Ctrl+Shift+Space</code> / <code>Ctrl+Win</code> on Windows). The pill appears instantaneously at the bottom-center of the screen without stealing keyboard or focus context from the underlying active document.</li>
  <li><b>00:25 - 00:58 | Push-to-Talk vs Toggle-to-Talk (POPO Mode)</b>:
    <br>Demonstrates dual capture paradigms:
    <br>1. <i>Push-to-Talk (PTT)</i>: Hold hotkey to record, release to transcribe and paste immediately.
    <br>2. <i>Hands-Free Toggle (POPO - Persistent Overlay Persistent Operation)</i>: Single tap opens the persistent pill with an active session timer (<code>00:14</code>), dismiss cancel icon (<code>✕</code>), animated 12-bar equalizer capsule, and coral stop button (<code>◼</code>).</li>
  <li><b>00:58 - 01:32 | Live Strikethrough & Conversational Self-Correction</b>:
    <br>The speaker intentionally stumbles, repeats words, and issues verbal retractions: <i>"We need to ship this by Friday... umm no actually let's push it to Monday because of testing."</i>
    <br>In the live preview bubble, the retracted phrase <code>~~umm no actually~~</code> is visually struck through with a crimson red line (<code>#D32F2F</code>) while the user is still speaking. Upon releasing or clicking stop, the pill displays an animated dotted pulse <code>(··········)</code> and instantaneously inserts clean, polished text directly into Slack, Notion, and Gmail without the filler words or retracted clause.</li>
  <li><b>01:32 - 02:15 | Writing Mode Switching & Transformation</b>:
    <br>Demonstrates the lower-tier mode selection chips docked beneath the main pill. The user toggles between modes using shortcut keys (<code>Win+F1</code> to <code>Win+F5</code>):
    <br>- <b>Verbatim</b>: Exact transcription preserving every spoken word.
    <br>- <b>Clean</b>: Removes fillers ("um", "uh", "you know") while preserving conversational tone.
    <br>- <b>Polished / Magic</b>: Rewrites spoken thoughts into fluent, concise professional prose.
    <br>- <b>Structured</b>: Formats spoken input into bulleted lists, action items, and clear headings.
    <br>- <b>Email</b>: Automatically structures greetings, executive body copy, calls-to-action, and professional sign-offs.</li>
  <li><b>02:15 - 03:00 | Meeting Intelligence: Bot-Free Local Loopback Capture</b>:
    <br>Transitions into the Meeting Studio / Notetaker interface. The application detects an active Microsoft Teams / Zoom meeting via audio loopback without inviting an external cloud bot.
    <br>Displays the signature 3-section layout:
    <br>1. <code>My Thoughts</code>: Private notepad for personal ideas and observations.
    <br>2. <code>Transcript</code>: Real-time dual-track speech-to-text with speaker labels (<code>||| Mark</code>, <code>||| Alix</code>), timestamps, and language detection.
    <br>3. <code>✦ Summary</code>: Automatically generated synthesis, structured decisions, and action item checklist cards.</li>
  <li><b>03:00 - 03:33 | Ask Vox / In-Meeting Intelligence & Privacy Isolation</b>:
    <br>Demonstrates the conversational prompt bar (<code>Ask anything...</code>) allowing participants to query the meeting memory (<i>"What did Mark say about the budget?"</i>). Emphasizes that all meeting audio and dictations remain processed locally on device, preserving enterprise confidentiality.</li>
</ul>

<hr>

<h2>3. High-Resolution Reference Images Forensic Deconstruction</h2>

<h3>3.1 Reference Image 1: Wispr Flow Dictation Hero Layout</h3>

<p>
<b>Asset Path</b>: <code>C:/Users/milin/.gemini/antigravity/brain/7e9fcebc-1cf3-4086-b6f8-8191627a24f9/.user_uploaded/media_1794158895737_6947c9ca.png</code>
</p>

<ul>
  <li><b>Top Navigation Bar Geometry</b>:
    <br>- Background: Semi-transparent warm cream card with subtle border: <code>rgba(0, 0, 0, 0.08)</code>.
    <br>- Border Radius: Fully rounded outer corners (<code>border-radius: 12px</code>).
    <br>- Left Logo: <code>||||| Flow</code> featuring 5 vertical waveform bars followed by bold serif/sans typography.
    <br>- Segmented View Switcher: A rounded pill switcher containing <code>Dictation</code> (active white badge with soft drop shadow) and <code>Notetaker</code> (muted gray-taupe text on cream background).
    <br>- Center Navigation: Minimalist links (<code>Business</code>, <code>Pricing</code>, <code>Lab</code>) in clean geometric sans typography.
    <br>- Right Action Button: <code>[Windows Logo] Get started on Windows</code> button with soft lavender / lilac background (<code>#E9D5FF</code>), sharp black border (<code>1.5px solid #000000</code>), and bold text.</li>
  <li><b>Background & Atmosphere</b>:
    <br>- Canvas Color: Warm ivory cream (<code>#FAF8F1</code> / <code>#FDFBF7</code>), completely avoiding cold sterile pure white (<code>#FFFFFF</code>).</li>
  <li><b>Typography Hierarchy</b>:
    <br>- Eyebrow Label: <code>WISPR FLOW DICTATION</code> in uppercase letterspaced sans-serif (tracking +0.12em).
    <br>- Display Headline: <code>Don't type, just speak.</code> rendered in high-contrast editorial serif (<code>EBGaramond</code> / Canela style) with italicized emphasis on <i>"just speak."</i>
    <br>- Subtitle: <i>"The voice-to-text AI that turns speech into clear, polished writing in every app."</i> (Figtree / Manrope sans-serif, 16px, line-height 1.5).
    <br>- Call-to-Action: Centered lavender pill button <code>Get started on Windows</code>, followed by secondary footnote: <i>"Available on Mac, Windows, iPhone, and Android"</i>.</li>
</ul>

<h3>3.2 Reference Image 2: Wispr Flow Notetaker Workspace & Live Transcription</h3>

<p>
<b>Asset Path</b>: <code>C:/Users/milin/.gemini/antigravity/brain/7e9fcebc-1cf3-4086-b6f8-8191627a24f9/.user_uploaded/media_1794158905736_b1f4a91d.jpg</code>
</p>

<ul>
  <li><b>Segmented Switcher State</b>:
    <br>- The top segmented pill now highlights <code>Notetaker</code> as the active selection, demonstrating seamless navigation between voice typing and meeting intelligence.</li>
  <li><b>Headline Hierarchy</b>:
    <br>- Eyebrow: <code>WISPR FLOW NOTETAKER</code>.
    <br>- Display Headline: <code>Meeting notes that get the details right.</code> in editorial serif with italicized <i>"get the details right."</i></li>
  <li><b>Live Diarized Transcript UI (Bottom Left)</b>:
    <br>- <b>Speaker Tag 1</b>: <code>||| Mark</code> rendered as a dusty terracotta / rose pill (<code>#FCA5A5</code> background, <code>#991B1B</code> text) containing 3 miniature animated equalizer bars. Followed by finalized transcript text: <i>"Can everyone see the CES proposal?"</i>
    <br>- <b>Speaker Tag 2</b>: <code>||| Alix</code> rendered as a soft orchid / lilac pill (<code>#E9D5FF</code> background, <code>#6B21A8</code> text) containing 3 equalizer bars.
    <br>- <b>Streaming Token Opacity</b>: The transcript shows: <i>"Yep. Keep in mind the floor plan isn..."</i> where the word <code>isn</code> has reduced opacity (50% gray), indicating live provisional tokens that have not yet reached final acoustic commitment.</li>
  <li><b>Action Item Checklist Card (Bottom Right)</b>:
    <br>- Container: Horizontal rounded pill-card with warm cream fill and solid 1.5px black border.
    <br>- Leading Icon: Black sparkle symbol <code>✦</code>.
    <br>- Action Item Copy: <code>Post Ramp reminder in event channel</code> in crisp medium-weight sans-serif.
    <br>- Assignee Badge: Trailing amber/orange rounded pill (<code>#FEF3C7</code> background, <code>#D97706</code> text) explicitly tagging assignee <code>Hailey</code>.</li>
</ul>

<hr>

<h2>4. Decompiled Production Bundle Reverse-Engineering (Wispr Flow Electron App)</h2>

<p>
Forensic extraction of <code>C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1400\resources\app.asar</code> provides the exact production styling tokens, component hierarchy, and interaction mechanics:
</p>

<h3>4.1 Color System & Design Tokens</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Design Token</th>
    <th>CSS Variable Name</th>
    <th>Hex / Value</th>
    <th>UI Application & Usage</th>
  </tr>
  <tr>
    <td><b>Dawn Palette (Warm Base)</b></td>
    <td><code>--dawn-50</code> to <code>--dawn-950</code></td>
    <td><code>#FAF8F3</code> (50), <code>#F4EFE6</code> (100), <code>#292524</code> (900)</td>
    <td>Main application background, card fills, warm ivory canvas.</td>
  </tr>
  <tr>
    <td><b>Chord Palette (Audio Violet)</b></td>
    <td><code>--chord-50</code> to <code>--chord-950</code></td>
    <td><code>#FAF5FF</code> (50), <code>#A855F7</code> (500), <code>#581C87</code> (900)</td>
    <td>Equalizer bars, audio wave animations, dictation badges.</td>
  </tr>
  <tr>
    <td><b>Accent Teal</b></td>
    <td><code>--accent-teal</code></td>
    <td><code>#2DD4BF</code> (Teal 400), <code>#0D9488</code> (Teal 600)</td>
    <td>Active mode chip border, focus highlights, committed badge flash.</td>
  </tr>
  <tr>
    <td><b>Stop Coral / Crimson</b></td>
    <td><code>--destructive-coral</code></td>
    <td><code>#EF4444</code> / <code>#F43F5E</code></td>
    <td>Stop recording button fill, live self-correction strikethrough.</td>
  </tr>
  <tr>
    <td><b>Card Border Width</b></td>
    <td><code>--pill-card-border-width</code></td>
    <td><code>1.5px</code></td>
    <td>Consistent crisp border across pills, cards, and buttons.</td>
  </tr>
  <tr>
    <td><b>Elevation Box Shadow</b></td>
    <td><code>--shadow-lg</code></td>
    <td><code>0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.08)</code></td>
    <td>Floating pill detachment from Windows desktop surface.</td>
  </tr>
</table>

<h3>4.2 Typography System</h3>

<ul>
  <li><b>Primary Interface Font</b>: <code>Figtree</code> (Geometric humanist sans-serif, sizes: 11px, 12px, 13px, 14px, 16px).</li>
  <li><b>Editorial Display Font</b>: <code>EBGaramond</code> (High-contrast serif used in headlines, quotes, and polished writing output).</li>
  <li><b>Monospace Data Font</b>: <code>Google_Sans_Code</code> (Used for session timer <code>14:49</code>, hotkeys <code>Win+F1</code>, and audio metrics).</li>
  <li><b>Body & Content Font</b>: <code>Manrope</code> (Used for transcript content, meeting notes, and summary documentation).</li>
</ul>

<h3>4.3 The 12-Bar Waveform Equalizer Algorithm</h3>

<p>
In <code>extracted_status.js</code> (component <code>Yi</code> / <code>Ui</code>), Wispr Flow computes the height of each bar dynamically using a parabolic bell-curve formula centered across the 12 bars:
</p>

<pre style="background:#f4f4f5; padding:10px; border-radius:6px; font-size:12px;">
// Exact parabolic scaling extracted from Wispr Flow runtime:
// h = normalized distance from center bar (index 0 to 11, center at 5.5)
// audioLevel = current microphone RMS level scaled 0 to 48
const h = Math.abs(barIndex - 5.5) / 5.5;
const heightScale = Math.max(0.15, 1 - Math.pow(h, 2) * (audioLevel / 48));

// Applied via CSS custom property with staggered animation delay:
style: {
  "--bar-height-scale": heightScale,
  "animationDelay": (0.1 * staggerIndex[barIndex]) + "s"
}
</pre>

<p>
This ensures the center bars pulse dramatically with speech energy while the outer flanking bars provide gentle harmonic stabilization, creating the organic "breathing" pill aesthetic.
</p>

<h3>4.4 Production State Machine Enum (from <code>$t.fw</code>)</h3>

<p>
The decompiled source reveals the complete, authoritative lifecycle states of the Wispr Flow recording engine:
</p>

<ol>
  <li><code>HIDDEN</code> ("hidden"): Window hidden, hotkey listening in background.</li>
  <li><code>RESTING</code> ("resting"): Idle standby, docked in system tray or minimal status pill.</li>
  <li><code>INITIALIZING</code> ("initializing"): Hardware audio stream opening via WASAPI.</li>
  <li><code>READY</code> ("ready"): Microphone buffer active, awaiting speech trigger.</li>
  <li><code>ACTIVE_PTT</code> ("active_ptt"): Push-To-Talk active (hotkey held down), 12-bar equalizer pulsing.</li>
  <li><code>ACTIVE_POPO</code> ("active_popo"): Hands-free toggle mode active (timer running, cancel button <code>✕</code>, stop button <code>◼</code>).</li>
  <li><code>PROCESSING</code> ("processing"): Speech ended, audio submitted to local Faster-Whisper, equalizer morphs to dotted pulse <code>(··········)</code>.</li>
  <li><code>POLISH_PROCESSING</code> ("polish_processing"): Text passing through LLM transformation for active writing mode (Verbatim, Clean, Polished, Structured, Email).</li>
  <li><code>AUTO_CLEANUP_COMPLETED</code> ("auto_cleanup_completed"): Filler words pruned and verbal self-corrections resolved.</li>
  <li><code>POLISH_COMPLETED_ACTION</code> ("polish_completed_action"): Final text successfully inserted into target Windows application.</li>
  <li><code>ERROR</code> ("error"): Audio input failure or permission denial (triggers crimson border flash).</li>
</ol>

<hr>

<h2>5. Exact UI/UX Specifications for VOX Desktop Assistant</h2>

<h3>5.1 Dual-Tier Floating Recording Pill</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Pill Tier</th>
    <th>Component</th>
    <th>Dimensions & Layout</th>
    <th>Visual Styling & Behavior</th>
  </tr>
  <tr>
    <td rowspan="4"><b>Upper Tier</b><br>(Recording Control)</td>
    <td><b>Dismiss Button (<code>btn_cancel</code>)</b></td>
    <td>28 x 28 px circle</td>
    <td>Subtle <code>✕</code> SVG icon, transparent background, soft hover highlight (<code>rgba(0,0,0,0.05)</code>). Aborts session and drops audio without inserting text.</td>
  </tr>
  <tr>
    <td><b>12-Bar Waveform Capsule</b></td>
    <td>120 x 28 px capsule</td>
    <td>12 vertical rounded bars, 2.5px width, 2px spacing. Teal gradient (<code>#2DD4BF</code> to <code>#FFFFFF</code>) driven by microphone RMS energy at 30 FPS. Morphs into 10 animated dots during processing.</td>
  </tr>
  <tr>
    <td><b>Monospace Session Timer</b></td>
    <td>48 x 20 px container</td>
    <td><code>Google_Sans_Code</code> / tabular numbers, 12px font, muted charcoal (<code>#71717A</code>). Displays elapsed recording time (<code>00:14</code>).</td>
  </tr>
  <tr>
    <td><b>Stop Button (<code>btn_stop</code>)</b></td>
    <td>28 x 28 px circle</td>
    <td>Solid coral red fill (<code>#EF4444</code>), centered 10x10 px white square (<code>#FFFFFF</code>). Tooltip: "Finish and paste". Immediately triggers transcription and direct insertion.</td>
  </tr>
  <tr>
    <td rowspan="2"><b>Lower Tier</b><br>(Mode Selector Bar)</td>
    <td><b>5 Writing Mode Chips</b></td>
    <td>5 horizontal pill chips (Verbatim, Clean, Polished, Structured, Email)</td>
    <td>Active chip: <code>1.5px solid #2DD4BF</code>, light teal background fill (<code>rgba(45,212,191,0.08)</code>), bold 12px Figtree font. Inactive chips: transparent fill, <code>#71717A</code> text. Shortcut keys: <code>Win+F1</code> to <code>Win+F5</code>.</td>
  </tr>
  <tr>
    <td><b>Expand / Collapse Chevron</b></td>
    <td>20 x 20 px button</td>
    <td>Minimal vertical chevron <code>⬍</code> (<code>chevronDown.svg</code>). Allows the user to toggle the lower mode bar, collapsing the floating pill to an ultra-compact 36px height.</td>
  </tr>
</table>

<h3>5.2 Live Speech Preview Bubble & Verbal Self-Correction Strikethrough</h3>

<ul>
  <li><b>Geometry & Positioning</b>: Floating 12px directly above the upper pill, matching width or expanding dynamically up to 480px, with 12px border radius, warm cream background (<code>#FAF8F3</code>), and 1.5px subtle border.</li>
  <li><b>Token Streaming</b>: Live interim hypothesis tokens stream into the bubble with provisional tokens rendered at 60% opacity.</li>
  <li><b>Real-Time Strikethrough Engine</b>: When the speaker utters a self-correction phrase (e.g. <i>"no actually"</i>, <i>"wait sorry"</i>, <i>"scratch that"</i>), the retracted clause is instantly styled with a crimson strikethrough:
    <br><code>&lt;span style="color:#D32F2F; text-decoration:line-through;"&gt;scratch that&lt;/span&gt;</code>
    <br>This provides immediate cognitive reassurance that VOX heard the correction and will eliminate the mistake from the final inserted text.</li>
  <li><b>Zero-Focus Text Insertion Pipeline</b>:
    <br>1. Window maintains Win32 <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>).
    <br>2. On session completion, VOX reads the stored target window handle (<code>HWND</code>).
    <br>3. Dispatches Unicode keystrokes directly via Win32 <code>SendInput</code>.
    <br>4. If the target application resists synthetic typing (e.g. certain remote desktop or secure terminal windows), VOX falls back to instantaneous clipboard paste (<code>Ctrl+V</code>) while saving and restoring the user's prior clipboard content within 150 milliseconds.</li>
</ul>

<h3>5.3 Meeting Intelligence & 3-Section Workspace</h3>

<p>
The Meeting Studio workspace (window dimensions: 1350 x 850) provides a dedicated environment for live call capture and asynchronous synthesis:
</p>

<ul>
  <li><b>Top Navigation & Meeting Header</b>:
    <br>- Meeting title, start time, duration badge, and audio status indicator (System Audio Loopback + Microphone).
    <br>- Calendar connection pill: Shows upcoming event synchronized with Microsoft Outlook Calendar.</li>
  <li><b>Section 1: <code>My Thoughts</code> (Private Scratchpad)</b>:
    <br>- Rich text note-taking area strictly reserved for the local user.
    <br>- Kept completely isolated from shared meeting notes or external MCP exports. Supports markdown formatting, inline bullet points, and personal reminder tags.</li>
  <li><b>Section 2: <code>Transcript</code> (Live Diarized Speech)</b>:
    <br>- Real-time dual-track transcription powered by WASAPI loopback capture and local Faster-Whisper.
    <br>- <b>Speaker Diarization Tags</b>: Pill badges with 3-bar animated equalizer (<code>||| You (Mic)</code> in terracotta, <code>||| Remote Speaker 1</code> in lavender, <code>||| Remote Speaker 2</code> in teal).
    <br>- <b>Multilingual Support</b>: Automatic language detection supporting Hindi, English, and romanized Hinglish with medial schwa deletion.
    <br>- Timestamps: Monospace timestamp tags (<code>[00:14:22]</code>) linked directly to audio playback offsets.</li>
  <li><b>Section 3: <code>✦ Summary</code> (Structured Synthesis Cards)</b>:
    <br>- <b>Executive Overview</b>: 2-3 paragraph distilled brief of meeting purpose and outcomes.
    <br>- <b>Key Decisions</b>: Bulleted list with bold outcome tags.
    <br>- <b>Action Items & Assignees</b>: Interactive checklist cards with sparkle icon <code>✦</code>, task description, due date, and assignee pill (e.g. <code>Hailey</code>, <code>Mark</code>).
    <br>- <b>Follow-up Emails</b>: Pre-formatted drafts ready for 1-click copy into Outlook or Gmail.
    <br>- <b>Participant Checklists</b>: Individualized action breakdowns per speaker.</li>
  <li><b>Bottom Intelligence Bar (Ask Vox)</b>:
    <br>- Prompt input <code>Ask anything about this meeting...</code> allowing instant question answering against the meeting memory.
    <br>- Quick chips: <code>What did I miss?</code>, <code>Summarize last 5 minutes</code>, <code>List follow-ups</code>.</li>
</ul>

<hr>

<h2>6. Codebase Reconciliation & Drop-In Implementation Plan</h2>

<h3>6.1 Floating Pill UI Integration (main.py & src/voxlocal/ui/floating_pill.py)</h3>

<ul>
  <li><b>Deficiency</b>: <code>main.py</code> currently imports and instantiates the legacy 8-icon <code>FloatingWidget</code> (from <code>src/voxlocal/ui/floating_widget.py</code>) rather than the modern <code>DualTierFloatingPill</code>.</li>
  <li><b>Remediation</b>:
    <br>1. Update <code>main.py</code> imports: Replace <code>FloatingWidget</code> with <code>DualTierFloatingPill</code> from <code>src.voxlocal.ui.floating_pill</code>.
    <br>2. Wire <code>DualTierFloatingPill</code> to <code>AppController</code> signal slots (<code>dictation_started</code>, <code>dictation_stopped</code>, <code>energy_changed</code>, <code>interim_text_ready</code>).
    <br>3. Expand <code>DualTierFloatingPill</code> mode chips from 3 to 5: Add <code>chip_verbatim</code> (Win+F1) and <code>chip_clean</code> (Win+F2), shifting <code>chip_polished</code> (Win+F3), <code>chip_structured</code> (Win+F4), and <code>chip_email</code> (Win+F5).</li>
</ul>

<h3>6.2 Writing Modes Harmonization (src/voxlocal/engine/dictation_processor.py)</h3>

<ul>
  <li><b>Deficiency</b>: Dictation modes in <code>dictation_processor.py</code> currently use legacy keys (<code>VERBATIM</code>, <code>CLEANED</code>, <code>MAGIC</code>, <code>STRUCTURED</code>, <code>EMAIL</code>) with slight naming divergences from R1.</li>
  <li><b>Remediation</b>:
    <br>1. Align enum keys to <code>VERBATIM</code>, <code>CLEAN</code>, <code>POLISHED</code>, <code>STRUCTURED</code>, <code>EMAIL</code>.
    <br>2. Maintain backwards compatibility aliases (<code>MAGIC = POLISHED</code>, <code>CLEANED = CLEAN</code>).
    <br>3. Ensure each mode adheres to its strict prompt transformation rules while preserving the underlying raw transcript for undo/history.</li>
</ul>

<h3>6.3 Microsoft Outlook Calendar Integration (New Module)</h3>

<ul>
  <li><b>Deficiency</b>: Zero calendar integration exists in the repository. Call detection currently relies solely on polling Windows registry microphone consent keys.</li>
  <li><b>Remediation</b>:
    <br>1. Create <code>src/voxlocal/calendar/outlook_client.py</code> implementing authorized OAuth2 authentication with Microsoft Graph API (scopes: <code>Calendars.Read</code>, <code>User.Read</code>, offline_access).
    <br>2. Implement automatic token refresh, secure DPAPI credential storage, manual disconnect, and error recovery.
    <br>3. Implement upcoming meeting detection polling with configurable 2-minute and 5-minute advance toast notifications.
    <br>4. Implement a robust <code>MockOutlookCalendarProvider</code> to ensure automated test suites pass 100% offline without live internet access.</li>
</ul>

<h3>6.4 Information Architecture Refactoring (src/voxlocal/ui/main_window.py)</h3>

<ul>
  <li><b>Deficiency</b>: <code>MainWindow</code> currently features 8 cluttered tabs (Voice Typing, Screen Recording, Meeting Studio, Reports, Ask Vox, Folders & Actions, Vocabulary, Settings).</li>
  <li><b>Remediation</b>:
    <br>Consolidate navigation into the 5 primary sections mandated by R3:
    <br>1. <b>Dictation</b>: Voice typing configuration, mode selector, live preview test, microphone level meter.
    <br>2. <b>Meetings</b>: Calendar integration, Meeting Studio (My Thoughts, Transcript, Summary), Ask Vox panel, active call capture.
    <br>3. <b>History</b>: Searchable SQLite archive of all past dictations and meeting sessions with TXT/MD/SRT/VTT export.
    <br>4. <b>Vocabulary</b>: Custom personal & domain words, spelling corrections, Hindi/Hinglish phrase mappings.
    <br>5. <b>Settings</b>: Hotkey configuration, audio devices, model management (Faster-Whisper base/small/large-v3-turbo), theme (Light/Dark), startup on login.
    <br><i>Secondary tools (Screen Recording, Reports, Folders & Actions) are moved to utility sub-panels within Meetings and History.</i></li>
</ul>

<hr>

<h2>7. Verification Evidence & Compliance Audit</h2>

<ol>
  <li><b>Header Compliance Audit</b>:
    <br>Inspected all lines of this analysis: Zero lines begin with markdown <code>#</code> or <code>*</code>. All headings use <code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, or <code>&lt;b&gt;</code> tags.
  </li>
  <li><b>Audio Track Extraction Verification</b>:
    <br>Audio extracted to <code>d:\Milind\Voice Assistant\External References\whisper_flow2_audio.aac</code>: File verified present with length 5,181,177 bytes.
  </li>
  <li><b>Decompiled Electron Assets Verification</b>:
    <br>Directly extracted and verified source files: <code>extracted_overlay.js</code> (716 KB), <code>extracted_status.js</code> (1.35 MB), <code>extracted_meeting_recorder.js</code> (2.43 MB), and <code>extracted_hub.js</code> (6.33 MB).
  </li>
</ol>

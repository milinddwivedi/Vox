<h2>VOX Desktop Productivity Assistant — Product UX &amp; Ergonomics Specification</h2>

<p><b>Document Identifier:</b> VOX-UX-SPEC-2026-V2<br/>
<b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Document Purpose:</b> Authoritative desktop user experience, interaction architecture, component design, and ergonomics specification across all four product pillars (Voice Intelligence, Screen Intelligence, Meeting Intelligence, VOX Brain).<br/>
<b>Reference Benchmarks:</b> Wispr Flow Reference Experience, Reference Image 1 (Ecosystem &amp; Universal Typing), Reference Image 2 (Dual-Tier Floating Pill &amp; 4 Pillars), Letterly Desktop Ergonomics.<br/>
<b>Formatting Mandate:</b> Strict HTML header compliance (zero markdown <code>#</code> or <code>*</code> headers across all sections).</p>

<hr/>

<h2>1. Executive UX Philosophy &amp; Ergonomic Principles</h2>

<p>VOX is engineered from first principles as an <b>ambient, non-disruptive, intelligent desktop companion</b>. The design philosophy centers on zero-friction thought translation: allowing professionals, executives, developers, and writers to move seamlessly through the <b>Capture &rarr; Understand &rarr; Create &rarr; Execute</b> lifecycle without keyboard strain, context-switching overhead, or privacy compromises.</p>

<h3>1.1 The Seven Foundational UX Laws of VOX</h3>
<ul>
  <li><b>Law 1: Zero Caret Intrusion (Focus Invariance):</b> The assistant must never steal window focus, hijack keyboard caret position, or activate window chrome away from the user's active working context. Every overlay, pill, and preview card operates with Win32 <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> semantics.</li>
  <li><b>Law 2: Ephemeral Affordance, Persistent Power:</b> Voice input and recording affordances appear dynamically when invoked, occupy minimal screen footprint (&lt; 2.5% of display surface), and dissolve smoothly once committed, leaving zero visual clutter.</li>
  <li><b>Law 3: Real-Time Feedback &amp; Psychological Reassurance:</b> Speech capture must provide instantaneous multi-sensory confirmation: dynamic acoustic waveforms reflecting vocal volume, elapsed duration timers, and live speech preview chips with dynamic in-line red strikethrough of verbal fillers and retracted thoughts so the user knows they are understood.</li>
  <li><b>Law 4: Deterministic Transformation Modes:</b> Users must retain intuitive, single-keystroke control over how their spoken thoughts are formatted: verbatim transcription, cleaned prose, AI Magic restructuring [Win+F1], structured outlines [Win+F2], or executive email drafts [Win+F3].</li>
  <li><b>Law 5: Three-Tab Cognitive Separation:</b> Meeting intelligence strictly separates personal internal reflections (<i>My Thoughts</i>), ground-truth chronological evidence (<i>Transcript</i>), and synthesis deliverables (<i>Summary</i>). Internal musings are physically and logically quarantined from external minutes.</li>
  <li><b>Law 6: Non-Intrusive Bot-Free Meeting Etiquette:</b> Meeting intelligence operates silently on the local PC via Windows audio APIs without dispatching awkward, robotic third-party accounts into meeting rosters. Prompts for meeting capture require explicit 1-click user consent.</li>
  <li><b>Law 7: High-DPI Legibility &amp; Dark/Light Adaptability:</b> Every typography token, stroke, icon, and surface adapts crisply to display scaling (100% to 200%) and honors system color mode (Deep Charcoal Dark Mode and Clean Modern Light Mode).</li>
</ul>

<hr/>

<h2>2. Desktop Floating Pill Control Specification (Reference Image 2)</h2>

<p>The VOX Floating Pill is the central physical anchor for ambient desktop intelligence. Positioned by default at the bottom-center of the primary display or magnetically docked to the active window edge, it consolidates recording controls, acoustic telemetry, and transformation modes into a sleek, unified dual-tier widget.</p>

<h3>2.1 Physical Geometry, Layout, &amp; Visual Architecture</h3>
<p>As established in Reference Image 2, the floating control is organized into a stacked dual-tier architecture:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Component Tier</th>
      <th>Dimensions (W &times; H)</th>
      <th>Visual Styling &amp; Materials</th>
      <th>Contained Controls &amp; Visual Anchors</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Upper Tier: Primary Recording Pill</b></td>
      <td>272px &times; 44px (border-radius: 22px)</td>
      <td>Dark Charcoal Acrylic (<code>rgba(24, 24, 28, 0.92)</code>) with 1px border stroke (<code>rgba(255, 255, 255, 0.16)</code>), 16px backdrop blur, subtle ambient drop shadow (<code>0 8px 32px rgba(0,0,0,0.36)</code>).</td>
      <td>
        - <b>Dismiss / Cancel Button (<code>&times;</code>):</b> 28px circular button on far left.<br/>
        - <b>Waveform Display Capsule:</b> Embedded 84px &times; 28px highlight capsule housing 12 dynamic audio equalizer bars.<br/>
        - <b>Session Timer Display:</b> High-contrast digital elapsed timer (<code>MM:SS</code>), 14px monospace font.<br/>
        - <b>Stop / Finalize Button:</b> 28px &times; 28px rounded square button in Coral-Red / Orange (<code>#E05638</code>) with crisp stop icon (<code>&#9632;</code>).
      </td>
    </tr>
    <tr>
      <td><b>Lower Tier: Mode &amp; Preset Selector Bar</b></td>
      <td>340px &times; 36px (docked 6px below upper tier)</td>
      <td>Horizontal layout of pill chips with frosted glass background (<code>rgba(18, 18, 22, 0.84)</code>), connected visually to upper tier via subtle alignment.</td>
      <td>
        - <b>Magic Mode Chip [Win+F1]:</b> Polished prose rewriting.<br/>
        - <b>Structured Mode Chip [Win+F2]:</b> Outlines, notes, bullet points (active state highlighted with 1.5px solid white border).<br/>
        - <b>Email Mode Chip [Win+F3]:</b> Executive email drafts.<br/>
        - <b>Selector Chevron (<code>&#x2965;</code> / <code>&#x2B0D;</code>):</b> 24px circular popover toggle to reveal additional custom templates and settings.
      </td>
    </tr>
  </tbody>
</table>

<h3>2.2 Detailed Upper Tier Anatomy</h3>
<ul>
  <li><b>Cancel / Discard Control (<code>&times;</code>):</b>
    <ul>
      <li><b>Geometry:</b> 28px &times; 28px circular touch target, 6px inset from left margin.</li>
      <li><b>Visuals:</b> Muted gray glyph (<code>#A0A0AB</code>) on transparent background; on hover transforms to solid circular pill (<code>rgba(255, 255, 255, 0.12)</code>) with bright white glyph (<code>#FFFFFF</code>).</li>
      <li><b>Behavior:</b> Single-click immediately aborts the active audio capture session, clears speech buffers, cancels pending inference, suppresses all text injection, and smoothly collapses the pill to Idle state. Hotkey: <code>Escape</code>.</li>
    </ul>
  </li>
  <li><b>Live Dynamic Waveform Capsule:</b>
    <ul>
      <li><b>Geometry:</b> 84px &times; 24px inner capsule with subtle inset border (<code>rgba(255, 255, 255, 0.08)</code>) and dark recessed background (<code>rgba(10, 10, 12, 0.60)</code>).</li>
      <li><b>Acoustic Equalizer Bars:</b> 12 vertical bars (width: 3px, gap: 3px, rounded caps).</li>
      <li><b>Dynamics:</b> Driven at 30 FPS by the RMS energy of incoming audio frames. Normal speech modulates bar heights from 4px (resting) up to 20px (peak). Bars exhibit smooth spring physics (tension: 180, friction: 12) to prevent jitter.</li>
      <li><b>Color Gradient:</b> In recording state, bars display a vertical linear gradient from Soft Teal (<code>#2DD4BF</code>) to Pure White (<code>#FFFFFF</code>). In processing state, bars morph into a pulsing horizontal dotted line.</li>
    </ul>
  </li>
  <li><b>Elapsed Session Timer:</b>
    <ul>
      <li><b>Geometry:</b> 52px width, centered adjacent to waveform capsule.</li>
      <li><b>Typography:</b> 13px JetBrains Mono / SF Mono / Consolas tabular numbers, weight 600, color <code>#F4F4F5</code>.</li>
      <li><b>Formatting:</b> Renders elapsed duration formatted as <code>MM:SS</code> (e.g., <code>00:14</code>, <code>14:49</code>). Automatically expands to <code>HH:MM:SS</code> if a continuous meeting recording exceeds 59 minutes.</li>
    </ul>
  </li>
  <li><b>Stop / Finalize Button (<code>&#9632;</code>):</b>
    <ul>
      <li><b>Geometry:</b> 28px &times; 28px rounded square (border-radius: 8px) on far right.</li>
      <li><b>Visuals:</b> Vivid Coral-Red / Orange background (<code>#E05638</code>; hover: <code>#F06548</code>; active press: <code>#C94528</code>) with an 11px &times; 11px solid white square icon centered.</li>
      <li><b>Behavior:</b> Single-click immediately flushes remaining audio frames through the transcriber, locks the utterance, triggers transformation/cleanup, injects text into the active cursor position, and transitions the pill through Processing to Dismissed state. Hotkey: <code>Enter</code> or <code>Fn release</code>.</li>
    </ul>
  </li>
</ul>

<h3>2.3 Detailed Lower Tier Mode Bar Anatomy</h3>
<p>Directly beneath the recording capsule, the Mode Bar presents three primary transformation modes with persistent keyboard shortcut badges, enabling instantaneous tactile mode switching without opening configuration windows:</p>

<ul>
  <li><b>Mode 1: Magic Chip [Win+F1]:</b>
    <ul>
      <li><b>Text &amp; Badge:</b> <code>Magic ⊞ F1</code> (or <code>⌘ F1</code> on macOS keyboard profiles).</li>
      <li><b>Visuals:</b> Semi-transparent pill capsule with purple/lavender accent dot (<code>#A855F7</code>).</li>
      <li><b>Transformation Behavior:</b> Ingests spoken stream, strips disfluencies and false starts, and performs an instantaneous intelligent rewrite. It cleans ramblings into articulate, punchy, professional prose while preserving 100% of the speaker's semantic intent. Ideal for general drafting, Slack messages, and documentation.</li>
    </ul>
  </li>
  <li><b>Mode 2: Structured Chip [Win+F2] (Active State in Reference Image 2):</b>
    <ul>
      <li><b>Text &amp; Badge:</b> <code>Structured ⊞ F2</code>.</li>
      <li><b>Visuals:</b> Highlighted active state indicated by a crisp 1.5px solid white border (<code>#FFFFFF</code>), elevated background (<code>rgba(255, 255, 255, 0.18)</code>), and bold white text (<code>#FFFFFF</code>).</li>
      <li><b>Transformation Behavior:</b> Analyzes spoken discourse and decomposes it into structured markdown: high-level summary header, bulleted key points, enumerated steps, and explicit action items. Ideal for post-meeting debriefs, bug reports, and complex instructions.</li>
    </ul>
  </li>
  <li><b>Mode 3: Email Chip [Win+F3]:</b>
    <ul>
      <li><b>Text &amp; Badge:</b> <code>Email ⊞ F3</code>.</li>
      <li><b>Visuals:</b> Semi-transparent pill capsule with soft amber/gold accent dot (<code>#F59E0B</code>).</li>
      <li><b>Transformation Behavior:</b> Converts spoken thoughts into an executive email draft: generates a compelling subject line, formal/conversational salutation, structured body paragraphs, clear call-to-action (CTA), and professional sign-off. Ideal for Outlook, Gmail, and CRM correspondence.</li>
    </ul>
  </li>
  <li><b>Mode Cycle &amp; Popover Chevron (<code>&#x2965;</code> / <code>&#x2B0D;</code>):</b>
    <ul>
      <li><b>Visuals:</b> 28px circular button housing vertical up/down chevrons (<code>&#x2965;</code>).</li>
      <li><b>Interaction:</b> Clicking the chevron opens an elevated popover menu revealing secondary modes:
        <ul>
          <li><i>Verbatim Mode:</i> Exact word-for-word acoustic transcription with zero disfluency filtering.</li>
          <li><i>Cleaned Mode:</i> Basic filler removal and punctuation normalization without rewriting.</li>
          <li><i>Code / Tech Mode:</i> Formats spoken variables in <code>camelCase</code>, <code>snake_case</code>, or code fences.</li>
          <li><i>Custom User Prompt Template:</i> User-defined system prompts configured in settings.</li>
        </ul>
      </li>
      <li><b>Mouse Wheel Ergonomics:</b> Scrolling the mouse wheel while hovering anywhere over the Mode Bar cycles instantly between active modes with subtle haptic/visual slide transitions.</li>
    </ul>
  </li>
</ul>

<h3>2.4 Complete 5-Stage Floating Pill State Machine</h3>
<p>The floating pill transitions across five strictly defined operational states:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>State ID</th>
      <th>State Name</th>
      <th>Visual Geometry &amp; Display</th>
      <th>Audio / Engine Activity</th>
      <th>Trigger &amp; Transition Conditions</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>ST-01</b></td>
      <td><b>Idle / Collapsed</b></td>
      <td>Collapsed into a sleek, ultra-minimal docked capsule (112px &times; 30px) displaying VOX logo icon + subtle status dot, or completely hidden when configured for pure hotkey PTT.</td>
      <td>Audio devices open in low-power standby; ring buffer primed (450ms pre-buffering); 0% CPU consumption.</td>
      <td>Default state. Transitions to <code>Listening</code> upon Push-to-Talk hotkey press (<code>Fn</code> down, <code>Ctrl+Win</code>) or hands-free toggle.</td>
    </tr>
    <tr>
      <td><b>ST-02</b></td>
      <td><b>Listening / Recording</b></td>
      <td>Full dual-tier pill expanded (272px &times; 44px top + 340px &times; 36px bottom); live dynamic waveform bars pulsing; elapsed timer counting up (<code>MM:SS</code>); coral stop button illuminated; speech preview card floating 12px above.</td>
      <td>Active WASAPI microphone stream recording; VAD streaming chunks (160ms); real-time provisional Whisper decoding; self-correction matcher running.</td>
      <td>Triggered by hotkey down or toggle. Transitions to <code>Processing</code> upon key release, Stop button click, or pause timeout in hands-free mode. Transitions to <code>Idle</code> on Cancel (<code>&times;</code>).</td>
    </tr>
    <tr>
      <td><b>ST-03</b></td>
      <td><b>Processing / Rewriting</b></td>
      <td>Upper tier waveform morphs into animated dotted progress bar: <code>(&middot;&middot;&middot;&middot;&middot;&middot;&middot;&middot;&middot;&middot;)</code>; timer freezes; stop button dims; preview card displays shimmer loading animation.</td>
      <td>Acoustic capture terminated; pending audio flushed; Faster-Whisper finalizes segment; local quantized LLM executes selected mode transformation (Magic, Structured, Email).</td>
      <td>Triggered by recording finalization. Duration: 180ms - 420ms. Transitions automatically to <code>Committed / Dismissed</code> upon text injection.</td>
    </tr>
    <tr>
      <td><b>ST-04</b></td>
      <td><b>Committed / Dismissed</b></td>
      <td>Upper pill flashes a brief subtle green checkmark (<code>&#10003; Done</code>) for 350ms; speech preview card dissolves upward; pill smoothly collapses back to <code>Idle</code>.</td>
      <td>Text injected into active target application via Win32 <code>SendInput(KEYEVENTF_UNICODE)</code> or atomic clipboard paste; SQLite session record committed.</td>
      <td>Triggered by successful text injection. Smooth 200ms cubic-bezier ease-out collapse.</td>
    </tr>
    <tr>
      <td><b>ST-05</b></td>
      <td><b>Error / Degraded</b></td>
      <td>Pill border transitions to warning amber (<code>#F59E0B</code>) or error red (<code>#EF4444</code>); waveform replaced with exclamation icon; tooltip displays concise diagnostic (e.g., <i>"Microphone Disconnected"</i> or <i>"Active Window Blocked"</i>).</td>
      <td>Audio stream recovery initiated; automatic fallback to default audio endpoint; clipboard fallback activated.</td>
      <td>Triggered by hardware disconnect, audio buffer underrun, or UIPI privilege error. Auto-recovers or dismisses after 3.0s.</td>
    </tr>
  </tbody>
</table>

<h3>2.5 Real-Time Speech Preview Bubble with Dynamic Red Strikethrough</h3>
<p>Inspired by the breakthrough interaction demonstrated in Wispr Flow (Phase 12, frames 85.0s – 88.0s), VOX features a dedicated floating speech preview card that provides instantaneous visual verification of self-correction:</p>

<ul>
  <li><b>Placement &amp; Anchor:</b> Floats exactly 12px directly above the upper recording pill, horizontally centered. Width dynamically scales between 280px and 480px based on content length.</li>
  <li><b>Material &amp; Typography:</b> Warm Ivory card (<code>#FAF8F3</code> in Light Mode; <code>#1E1E24</code> in Dark Mode), 18px border radius, 16px internal padding, elegant 15px sans-serif typography (1.45 line-height).</li>
  <li><b>Live Strikethrough Behavior:</b>
    <ul>
      <li>As the user speaks, provisional tokens stream into the card in real time.</li>
      <li>When the user utters conversational filler words (<i>"umm"</i>, <i>"uh"</i>) or verbal correction pivots (<i>"no actually..."</i>, <i>"wait sorry..."</i>, <i>"scratch that..."</i>), the self-correction engine immediately wraps the rejected clause in a high-visibility crimson red strikethrough:
        <br/><code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;umm no actually&lt;/span&gt;</code></li>
      <li>This gives the user immediate visual confidence that the system recognized their verbal retrial, eliminating the urge to stop and reach for the keyboard.</li>
    </ul>
  </li>
  <li><b>Instantaneous Sentence Collapsing (Transition to Cleaned Rewrite):</b>
    <ul>
      <li>Upon key release or stop, the entire card smoothly cross-fades (180ms ease-out) into the restructured sentence. The crossed-out disfluencies and discarded clauses dissolve away, leaving the final polished text ready for injection.</li>
    </ul>
  </li>
</ul>

<h3>2.6 Window Ergonomics, Edge Docking, &amp; Drag Interaction</h3>
<ul>
  <li><b>Brand Grip Handle:</b> A subtle 3-dot or ribbed grip handle on the left end of the pill allows the user to click-and-drag the widget to any screen location.</li>
  <li><b>Magnetic Edge Snapping:</b> When dragged within 32px of any monitor screen edge (top, bottom, left, right), the pill smoothly snaps to the boundary. When docked to a vertical side edge, it automatically rotates into a compact vertical edge-rail.</li>
  <li><b>Multi-Monitor Coordinate Persistence:</b> The window coordinates (screen index, normalized X/Y ratios) are persistently saved to <code>data/voxlocal.db</code> across system restarts. If an external monitor is disconnected, VOX automatically clamps coordinates to the visible primary viewport.</li>
</ul>

<hr/>

<h2>3. Unified 3-Tab Meeting Workspace Specification</h2>

<p>The VOX Meeting Workspace serves as the central mission-control room for multi-party conversational intelligence. Moving away from disjointed sub-screens, all meeting lifecycle context is unified into a modern, 3-tab layout:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Workspace Tab</th>
      <th>Tab Purpose &amp; Privacy Boundary</th>
      <th>Key Sub-Components &amp; Widgets</th>
      <th>Primary User Actions &amp; Deliverables</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Tab 1: "My Thoughts"</b></td>
      <td><b>Personal Private Scratchpad:</b> 100% confidential, user-only workspace for private notes, negotiation strategies, and personal reflections during calls. Completely isolated from shared minutes.</td>
      <td>
        - Rich Markdown Scratchpad Editor.<br/>
        - Timestamp Note Anchor Button (<code>&#9201; Mark Note</code>).<br/>
        - Summary Inclusion Toggle (<i>"Include in AI Summary"</i>, default: OFF).<br/>
        - Autosave Status &amp; Word Counter.
      </td>
      <td>
        - Private thought capture.<br/>
        - In-meeting time-anchored reminders.<br/>
        - Selective elevation into summary generation.
      </td>
    </tr>
    <tr>
      <td><b>Tab 2: "Transcript"</b></td>
      <td><b>Interactive Ground-Truth Feed:</b> Complete, millisecond-accurate, chronological transcript diarized between local speaker ("You") and remote attendees.</td>
      <td>
        - Speaker Diarization Badges &amp; Avatars.<br/>
        - Clickable Timestamp Seeking Anchors.<br/>
        - Live Filter &amp; Search Bar (FTS5).<br/>
        - Inline Speaker Renaming Control.<br/>
        - Dual-Track Audio Playback Scrub Bar.
      </td>
      <td>
        - Real-time reading during calls.<br/>
        - Auditing spoken commitments.<br/>
        - Seeking audio/video to exact quotes.<br/>
        - Re-attributing speaker identities.
      </td>
    </tr>
    <tr>
      <td><b>Tab 3: "Summary"</b></td>
      <td><b>Executive MoM &amp; Execution Studio:</b> High-impact executive meeting deliverables, grounded action items, and conversational copilot.</td>
      <td>
        - Executive Overview &amp; Key Discussion Topics.<br/>
        - 7-State Decision Evolution Table.<br/>
        - Grounded Action Items Matrix.<br/>
        - Risks vs Blockers Register.<br/>
        - One-Click Follow-Up Email Generator.<br/>
        - Per-Participant Commitment Checklists.<br/>
        - Scoped In-Report "Ask Vox" RAG Pane.
      </td>
      <td>
        - 1-Click Follow-up Email dispatch.<br/>
        - Exporting checklists to Slack/Teams.<br/>
        - Cross-meeting decision audit.<br/>
        - Interactive transcript Q&amp;A.
      </td>
    </tr>
  </tbody>
</table>

<h3>3.1 Meeting Workspace Chrome &amp; Navigation Header</h3>
<ul>
  <li><b>Window Frame:</b> Modern Windows 11 Fluent frame with subtle Mica / Acrylic background styling, rounded corners (12px), and minimal window controls (Minimize, Maximize, Close).</li>
  <li><b>Header Architecture:</b>
    <ul>
      <li><b>Back Button (<code>&lt;</code>):</b> Returns to Meetings Library / Dashboard.</li>
      <li><b>Meeting Title:</b> Rendered in an elegant, high-contrast serif typeface (e.g., <i>"Product Launch &amp; Channel Strategy Review"</i>). Double-clicking allows inline title editing.</li>
      <li><b>Metadata Subtitle:</b> Platform badge (Teams / Zoom / Meet), formatted date (<code>Tuesday, Nov 3, 2026 &bull; 10:30 AM</code>), total duration (<code>42m 18s</code>), and participant count (<code>4 Attendees</code>).</li>
      <li><b>Primary Tab Strip:</b> Three prominent tabs with icon badges:
        <ol>
          <li><code>&#9998; My Thoughts</code></li>
          <li><code>&#9835; Transcript</code> (with animated teal equalizer indicator when recording is active)</li>
          <li><code>&#10024; Summary</code></li>
        </ol>
      </li>
      <li><b>Utility Action Cluster (Right):</b>
        <ul>
          <li><i>Export Button:</i> Dropdown menu (Markdown, PDF, HTML, Client-Safe MoM, SRT, Standalone MP4).</li>
          <li><i>Compare Button:</i> Launches cross-meeting differential analysis against prior sessions.</li>
          <li><i>Delete / Archive Button:</i> Secure local deletion.</li>
        </ul>
      </li>
    </ul>
  </li>
</ul>

<h3>3.2 Tab 1: "My Thoughts" (Personal Private Scratchpad)</h3>
<p>The "My Thoughts" tab directly solves the executive privacy dilemma: professionals need a place to jot down private notes during high-stakes negotiations, confidential impressions of client body language, or internal salary/budget figures without risking accidental disclosure when meeting minutes are exported or shared with external attendees.</p>

<ul>
  <li><b>Strict Data Isolation:</b>
    <ul>
      <li>Notes created in "My Thoughts" are stored in a dedicated SQLite table: <code>meeting_thoughts</code>.</li>
      <li><b>Zero Egress Guarantee:</b> These notes are automatically and permanently excluded from default meeting export formats (Markdown, PDF, Client Review MoM).</li>
      <li><b>MCP Sequestering:</b> The local Model Context Protocol (MCP) server strictly blocks external AI tools (Claude, ChatGPT) from reading "My Thoughts" unless the user explicitly toggles the per-note <i>"Allow External AI Context"</i> authorization.</li>
    </ul>
  </li>
  <li><b>Timestamp Note Markers (<code>&#9201; Mark Note</code>):</b>
    <ul>
      <li>Pressing <code>Ctrl+M</code> or clicking the <i>Mark Note</i> button inserts a prominent, clickable timestamp pill into the scratchpad (e.g., <code>[18:42]</code>).</li>
      <li>Clicking that timestamp anywhere in the scratchpad automatically seeks the Tab 2 audio/video player to that exact millisecond, allowing instant playback review of what was being said when the thought was recorded.</li>
    </ul>
  </li>
  <li><b>Selective Summary Inclusion Toggle:</b>
    <ul>
      <li>A discrete toggle switch at the top of the scratchpad: <i>"Include My Thoughts in AI Summary Generation"</i> (Default: <b>OFF</b>).</li>
      <li>When switched to <b>ON</b>, the local LLM treats the user's personal thoughts as high-priority contextual guidance (e.g., incorporating the user's private conclusion into the final executive summary), while still omitting raw verbatim private thoughts from the shared report.</li>
    </ul>
  </li>
  <li><b>Editor Capabilities:</b> Markdown-enabled rich text editor supporting headings, bold/italics, bulleted checklists, code snippets, auto-save with debounce (500ms), and word/character metrics.</li>
</ul>

<h3>3.3 Tab 2: "Transcript" (Interactive Diarized Feed)</h3>
<p>The Transcript tab provides an auditable, chronological feed of every spoken word across all attendees:</p>

<ul>
  <li><b>Hardware-Separated Dual-Track Diarization:</b>
    <ul>
      <li>Utterances originating from the local microphone are assigned to <b>"You (Local)"</b> with a Champagne Gold avatar pill (<code>#D97706</code>).</li>
      <li>Utterances originating from the WASAPI system loopback track are assigned to remote speaker clusters (e.g., <b>"Speaker 1"</b>, <b>"Speaker 2"</b>) with distinct color accents (Emerald Teal, Sapphire Blue, Amethyst Purple).</li>
    </ul>
  </li>
  <li><b>Interactive Seeking &amp; A/V Synchronization:</b>
    <ul>
      <li>Every dialogue card features a clickable millisecond timestamp (e.g., <code>04:12</code>).</li>
      <li>Clicking any word or timestamp immediately commands the synchronized media player to seek to that exact timecode. The active playing word highlights with an animated glow, providing a karaoke-style reading experience.</li>
    </ul>
  </li>
  <li><b>Inline Speaker Re-Attribution:</b>
    <ul>
      <li>Clicking any speaker name badge opens a lightweight popover: <i>"Rename Speaker 1"</i> (e.g., change to <i>"Sarah Jenkins (Client VP)"</i>).</li>
      <li>Confirming the rename retroactively updates all transcript segments, action item ownership assignments, and summary references across the entire meeting record.</li>
    </ul>
  </li>
  <li><b>Search &amp; Text Filtering:</b>
    <ul>
      <li>Top toolbar contains an integrated FTS5 search input with live match count (e.g., <code>"budget" &bull; 14 matches</code>).</li>
      <li>Pressing <code>Enter</code> or <code>F3</code> cycles smoothly through matches, auto-scrolling the transcript view and highlighting matches in soft yellow (<code>#FEF08A</code>).</li>
    </ul>
  </li>
  <li><b>Bilingual Code-Switching Rendering:</b>
    <ul>
      <li>Seamlessly renders mixed English, Devanagari Hindi, and Romanized Hinglish segments without font substitution clipping or layout jitter.</li>
    </ul>
  </li>
</ul>

<h3>3.4 Tab 3: "Summary" (Executive MoM &amp; Deliverables Studio)</h3>
<p>The Summary tab transforms conversation into structured business execution through seven dedicated sections:</p>

<h4>3.4.1 Executive Overview &amp; Discussion Topics</h4>
<ul>
  <li><b>Executive Narrative:</b> A concise, high-level briefing paragraph synthesizing the meeting's primary objective, critical milestones discussed, and strategic consensus.</li>
  <li><b>Key Discussion Topics:</b> Expandable accordions detailing major topics, arguments raised, and consensus reached, each citing verified transcript timecodes.</li>
</ul>

<h4>3.4.2 7-State Grounded Decision Evolution Table</h4>
<p>Decouples business status from longitudinal evolution, providing complete traceability across project lifecycles:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Decision ID</th>
      <th>Decision Description</th>
      <th>Business Status</th>
      <th>Evolutionary State</th>
      <th>Evidence Quote &amp; Timestamp</th>
      <th>Lineage / Supersedes</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>DEC-042</code></td>
      <td>Move 30% of Q4 marketing budget from paid social into developer advocacy and technical content.</td>
      <td><span style="background:#DCFCE7; color:#166534; padding:2px 8px; border-radius:10px; font-weight:600;">confirmed</span></td>
      <td><span style="background:#E0E7FF; color:#3730A3; padding:2px 8px; border-radius:10px; font-weight:600;">superseded</span></td>
      <td><i>"Let's officially reallocate thirty percent away from paid social into dev advocacy."</i> (<code>18:24</code> &bull; Sarah)</td>
      <td>Supersedes <code>DEC-019</code> (from meeting <i>"Q3 Budget Planning"</i> &bull; Oct 14).</td>
    </tr>
    <tr>
      <td><code>DEC-043</code></td>
      <td>Postpone public beta launch date to Nov 24 to complete security penetration testing.</td>
      <td><span style="background:#FEF3C7; color:#92400E; padding:2px 8px; border-radius:10px; font-weight:600;">proposed</span></td>
      <td><span style="background:#F3F4F6; color:#1F2937; padding:2px 8px; border-radius:10px; font-weight:600;">new</span></td>
      <td><i>"I propose we push beta launch by two weeks so audit finishes cleanly."</i> (<code>31:05</code> &bull; Alex)</td>
      <td>None (New baseline).</td>
    </tr>
  </tbody>
</table>

<h4>3.4.3 Action Items Matrix with Verifiable Grounding</h4>
<ul>
  <li><b>Mandatory Grounding Guard:</b> Every extracted action item is strictly anchored to an exact transcript quote, timestamp, and speaker. No synthetic tasks are ever hallucinated.</li>
  <li><b>Deterministic Due Date Arithmetic:</b> Relative dates ("by Friday", "in two weeks") are resolved to exact ISO calendar dates (e.g., <code>2026-11-06</code>) anchored to the meeting timestamp. Ambiguous dates ("sometime soon") resolve to <code>due_date = None</code> with an explicit flag <code>resolution_status = "ambiguous"</code>.</li>
  <li><b>Interactive Checkboxes &amp; Edit Preservation:</b> Users can mark tasks complete, edit descriptions, or assign owners. Re-running AI finalization preserves all user edits (ADR-002).</li>
</ul>

<h4>3.4.4 Risks vs. Blockers Register</h4>
<ul>
  <li><b>Risks:</b> Potential future impediments identified during discussion (e.g., <i>"Third-party payment gateway API changes scheduled for next month"</i>) with severity and mitigation strategy.</li>
  <li><b>Blockers:</b> Immediate, active obstacles halting progress (e.g., <i>"Staging environment credentials not provisioned for QA team"</i>) with designated unblocker owner.</li>
</ul>

<h4>3.4.5 One-Click Executive Follow-Up Email Generator</h4>
<ul>
  <li><b>Dedicated Action Button:</b> Prominent button at the top of Tab 3: <code>&#9993; Generate Follow-Up Email</code>.</li>
  <li><b>Synthesis Architecture:</b>
    <ul>
      <li>Automatically synthesizes an executive email incorporating meeting context, key decisions agreed upon, owner-grouped action items with resolved deadlines, and proposed next sync date.</li>
      <li>Opens in a sleek modal with instant options:
        <ul>
          <li><b>Copy Rich HTML:</b> Copies formatted email with tables and bullet points directly to clipboard for Outlook / Webmail.</li>
          <li><b>Copy Markdown:</b> Copies clean markdown for Slack / Notion.</li>
          <li><b>Open in Default Mail Client:</b> Generates a <code>mailto:</code> URI with pre-filled subject and body encoding, launching Outlook, Thunderbird, or Windows Mail in one click.</li>
        </ul>
      </li>
    </ul>
  </li>
</ul>

<h4>3.4.6 Per-Participant Commitment Checklists</h4>
<ul>
  <li><b>Deconstructed Checklist View:</b> A specialized tab or toggle in the summary breaking down all action items into individual, person-by-person task cards (e.g., <i>"Checklist for Alex"</i>, <i>"Checklist for Sarah"</i>, <i>"Checklist for You"</i>).</li>
  <li><b>Team Export:</b> Single-click action: <i>"Copy Slack Checklists"</i> formats each person's tasks with user mentions (<code>@Alex</code>, <code>@Sarah</code>) and markdown checkboxes (<code>- [ ]</code>) ready for immediate posting in team channels.</li>
</ul>

<h4>3.4.7 Scoped In-Report "Ask Vox" RAG Copilot Pane</h4>
<ul>
  <li><b>Docked Action Bar:</b> Positioned along the bottom of the workspace: an <code>Ask anything about this meeting...</code> input field paired with suggestion chips (e.g., <i>"What did Alex say about budget?"</i>, <i>"Summarize key disagreements"</i>, <i>"What did I miss?"</i>).</li>
  <li><b>Strict Meeting Isolation Boundary (ADR-004):</b> Queries submitted to Ask Vox are 100% quarantined to the active meeting's transcript and notes. Candidate chunks from other meetings are strictly excluded, eliminating cross-meeting confidentiality leaks.</li>
  <li><b>Evidence-Backed Responses:</b> Answers include verbatim citations with clickable timestamp links that highlight the exact supporting sentence in Tab 2.</li>
</ul>

<hr/>

<h2>4. Global Shortcuts, Hotkey Management, &amp; System Tray</h2>

<h3>4.1 Global Hotkey Architecture</h3>
<p>VOX establishes a reliable, non-conflicting hotkey schema managed via native Win32 low-level keyboard hooks (<code>WH_KEYBOARD_LL</code>):</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Hotkey Chord</th>
      <th>Trigger Mechanism</th>
      <th>Operational Function</th>
      <th>Context &amp; Fallback</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Fn (Hold)</b></td>
      <td>Push-to-Talk (PTT)</td>
      <td>Press-and-hold to dictate; release to stop, process, and inject text.</td>
      <td>Default on laptops supporting hardware Fn reporting. If BIOS intercepts Fn, falls back to <code>Ctrl+Win</code> or <code>F8</code>.</td>
    </tr>
    <tr>
      <td><b>Win+Alt+V</b></td>
      <td>Toggle (Hands-Free)</td>
      <td>Start / stop continuous voice typing with automated VAD pause segmentation.</td>
      <td>Global desktop shortcut. Configurable in settings.</td>
    </tr>
    <tr>
      <td><b>Win+F1</b></td>
      <td>Mode Switch</td>
      <td>Switch voice typing transformation mode to <b>Magic Mode</b> (Rewriting).</td>
      <td>Operates instantly during or prior to active dictation.</td>
    </tr>
    <tr>
      <td><b>Win+F2</b></td>
      <td>Mode Switch</td>
      <td>Switch voice typing transformation mode to <b>Structured Mode</b> (Outlines).</td>
      <td>Active state highlighted in floating pill.</td>
    </tr>
    <tr>
      <td><b>Win+F3</b></td>
      <td>Mode Switch</td>
      <td>Switch voice typing transformation mode to <b>Email Mode</b> (Executive Draft).</td>
      <td>Formats dictation as professional email.</td>
    </tr>
    <tr>
      <td><b>Ctrl+Shift+R</b></td>
      <td>Toggle Recording</td>
      <td>Start / stop screen video recording.</td>
      <td>Global shortcut; invokes region or full display capture.</td>
    </tr>
    <tr>
      <td><b>Ctrl+Shift+S</b></td>
      <td>Instant Snip</td>
      <td>Launch region snip &amp; annotation studio.</td>
      <td>Freezes frame, allows drawing, copies directly to clipboard.</td>
    </tr>
    <tr>
      <td><b>Ctrl+Shift+D</b></td>
      <td>Summon Workspace</td>
      <td>Show / hide unified VOX main window.</td>
      <td>Smoothly brings window to foreground without reload flicker.</td>
    </tr>
    <tr>
      <td><b>Escape</b></td>
      <td>Instant Discard</td>
      <td>Cancel active recording / dictation session and discard audio buffers.</td>
      <td>Active when floating pill or recording overlay is visible.</td>
    </tr>
  </tbody>
</table>

<h3>4.2 System Tray &amp; Background Lifecycle</h3>
<ul>
  <li><b>Non-Terminating Close:</b> Clicking the close button (<code>&times;</code>) on the main workspace hides the window to the Windows System Tray rather than killing background workers.</li>
  <li><b>Tray Icon States:</b>
    <ul>
      <li><i>Resting (White/Gray VOX Logo):</i> System idle; hotkeys active; audio devices in standby.</li>
      <li><i>Recording (Pulsing Red Dot):</i> Active voice typing or meeting capture in progress.</li>
      <li><i>Processing (Amber Dot):</i> LLM inference or video muxing executing.</li>
      <li><i>Muted / Offline (Gray Logo with Slash):</i> Microphone muted or offline mode enforced.</li>
    </ul>
  </li>
  <li><b>Tray Context Menu:</b> One-click access to <i>Open VOX Workspace</i>, <i>Start Voice Typing</i>, <i>Record Screen</i>, <i>Meetings Dashboard</i>, <i>Settings</i>, and <i>Quit VOX Completely</i>.</li>
</ul>

<h3>4.3 Windows Native Toast Notifications</h3>
<ul>
  <li><b>Discreet Action Confirmations:</b> Emits native Windows Action Center notifications for asynchronous operations (e.g., <i>"Meeting Processed: Product Launch Review — 4 Action Items Extracted"</i>, <i>"Screen Recording Saved to Videos/VOX"</i>).</li>
  <li><b>Actionable Buttons:</b> Notifications include one-click actions: <code>View Summary</code>, <code>Copy Email Draft</code>, <code>Open Video</code>.</li>
</ul>

<hr/>

<h2>5. Design Tokens, Typography, Color Palette, &amp; Theming</h2>

<p>The VOX visual system is built on modern, premium design tokens calibrated for maximum legibility and reduced ocular fatigue during long workdays:</p>

<h3>5.1 Color Palette Tokens</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Token Name</th>
      <th>Dark Mode Value</th>
      <th>Light Mode Value</th>
      <th>Semantic Application</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>--surface-canvas</code></td>
      <td><code>#0E0E12</code> (Deep Slate)</td>
      <td><code>#F8F9FA</code> (Off-White)</td>
      <td>Main application window background canvas.</td>
    </tr>
    <tr>
      <td><code>--surface-card</code></td>
      <td><code>#18181E</code> (Charcoal Slate)</td>
      <td><code>#FFFFFF</code> (Pure White)</td>
      <td>Cards, content containers, meeting transcript capsules.</td>
    </tr>
    <tr>
      <td><code>--surface-acrylic</code></td>
      <td><code>rgba(24, 24, 30, 0.90)</code></td>
      <td><code>rgba(255, 255, 255, 0.92)</code></td>
      <td>Floating pill body, frosted overlays, preview popovers.</td>
    </tr>
    <tr>
      <td><code>--surface-highlight</code></td>
      <td><code>rgba(255, 255, 255, 0.08)</code></td>
      <td><code>rgba(0, 0, 0, 0.04)</code></td>
      <td>Hover states, subtle borders, recessed waveform capsule.</td>
    </tr>
    <tr>
      <td><code>--text-primary</code></td>
      <td><code>#F4F4F6</code> (96% White)</td>
      <td><code>#18181B</code> (Deep Zinc)</td>
      <td>Headings, primary content, transcript body.</td>
    </tr>
    <tr>
      <td><code>--text-secondary</code></td>
      <td><code>#A1A1AA</code> (Muted Zinc)</td>
      <td><code>#71717A</code> (Slate Gray)</td>
      <td>Metadata, timestamps, subheaders, shortcuts badges.</td>
    </tr>
    <tr>
      <td><code>--accent-brand</code></td>
      <td><code>#2DD4BF</code> (Teal / Cyan)</td>
      <td><code>#0D9488</code> (Deep Teal)</td>
      <td>Primary brand accent, waveform bars, active transcript tab.</td>
    </tr>
    <tr>
      <td><code>--accent-stop</code></td>
      <td><code>#E05638</code> (Coral Orange-Red)</td>
      <td><code>#DC2626</code> (Vivid Crimson)</td>
      <td>Stop recording button, recording active indicator.</td>
    </tr>
    <tr>
      <td><code>--accent-strike</code></td>
      <td><code>#EF4444</code> (Red Strikethrough)</td>
      <td><code>#D32F2F</code> (Crimson Strikethrough)</td>
      <td>Real-time speech disfluency and false-start rejection line.</td>
    </tr>
    <tr>
      <td><code>--accent-gold</code></td>
      <td><code>#F59E0B</code> (Champagne Amber)</td>
      <td><code>#D97706</code> (Warm Amber)</td>
      <td>Local speaker "You" avatar badge, email mode accent.</td>
    </tr>
    <tr>
      <td><code>--border-subtle</code></td>
      <td><code>rgba(255, 255, 255, 0.12)</code></td>
      <td><code>rgba(0, 0, 0, 0.08)</code></td>
      <td>Card dividers, pill borders, table cell outlines.</td>
    </tr>
  </tbody>
</table>

<h3>5.2 Typography System</h3>
<ul>
  <li><b>Editorial Headline Typeface:</b> <code>Newsreader</code>, <code>Georgia</code>, or <code>Cambria</code> (Serif, weights: 500, 600). Applied exclusively to meeting titles and major executive deliverables to evoke refined editorial professionalism.</li>
  <li><b>System UI &amp; Transcript Typeface:</b> <code>Segoe UI Variable</code>, <code>Inter</code>, or system-native <code>Segoe UI</code> (Sans-serif, weights: 400, 500, 600). Optimized for maximum legibility at small sizes and high-DPI scaling.</li>
  <li><b>Telemetry &amp; Code Typeface:</b> <code>JetBrains Mono</code>, <code>Cascadia Code</code>, or <code>Consolas</code> (Monospace, weights: 500, 600). Applied to elapsed timers, hotkey badges, and evidence quotes.</li>
</ul>

<hr/>

<h2>6. Forensic UX Acceptance Criteria</h2>

<p>Every implementation of the VOX user experience must satisfy these verifiable acceptance benchmarks:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Verification ID</th>
      <th>UX Component</th>
      <th>Quantitative / Qualitative Benchmark</th>
      <th>Verification Method</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>UX-VER-01</b></td>
      <td>Floating Pill Dimensions</td>
      <td>Upper tier matches 272px &times; 44px (&plusmn;10%); Lower tier matches 340px &times; 36px (&plusmn;10%).</td>
      <td>Automated Qt widget geometry test in <code>test_phase11_product_experience_and_workflow.py</code>.</td>
    </tr>
    <tr>
      <td><b>UX-VER-02</b></td>
      <td>Waveform Dynamics</td>
      <td>Waveform bars animate at &ge; 30 FPS with RMS-proportional height scaling without UI freeze.</td>
      <td>FPS timer hook and signal-emission validation.</td>
    </tr>
    <tr>
      <td><b>UX-VER-03</b></td>
      <td>Focus Invariance</td>
      <td>Clicking pill, mode chips, cancel, or stop never transfers focus from active Notepad / Word caret.</td>
      <td>Win32 <code>GetForegroundWindow()</code> and <code>GetFocus()</code> HWND assertion suite.</td>
    </tr>
    <tr>
      <td><b>UX-VER-04</b></td>
      <td>Real-Time Strikethrough</td>
      <td>Uttered filler phrases ("umm", "no actually") render with <code>&lt;span style="color:#D32F2F; text-decoration:line-through;"&gt;</code> within 250ms of utterance.</td>
      <td>Dictation self-correction streaming test.</td>
    </tr>
    <tr>
      <td><b>UX-VER-05</b></td>
      <td>3-Tab Meeting Workspace</td>
      <td>Workspace cleanly renders Tab 1 (My Thoughts), Tab 2 (Transcript), and Tab 3 (Summary).</td>
      <td>Meeting Studio widget tab index and rendering test.</td>
    </tr>
    <tr>
      <td><b>UX-VER-06</b></td>
      <td>My Thoughts Isolation</td>
      <td>Client Review MoM export contains 0 characters of text from Tab 1 scratchpad.</td>
      <td>Export sanitization test in <code>test_phase12_security_privacy_hardening.py</code>.</td>
    </tr>
    <tr>
      <td><b>UX-VER-07</b></td>
      <td>Follow-Up Email Generator</td>
      <td>Clicking Follow-Up Email generates structured email with verified action items and decisions in &lt; 800ms.</td>
      <td>End-to-end follow-up email synthesis test.</td>
    </tr>
    <tr>
      <td><b>UX-VER-08</b></td>
      <td>High-DPI Fidelity</td>
      <td>Pill and workspace typography and borders render with 0 pixel blur at 125%, 150%, and 200% DPI scaling.</td>
      <td>High-DPI device pixel ratio rendering assertion.</td>
    </tr>
  </tbody>
</table>

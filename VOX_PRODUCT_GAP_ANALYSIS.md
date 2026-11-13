<h2>VOX Product Gap Analysis & Remediation Matrix</h2>

<p><b>Document Authority:</b> VOX Evolution Architectural Deliverable<br>
<b>Author:</b> Teamwork Worker (Grounding & Architecture Specialist)<br>
<b>Date:</b> 2026-11-08<br>
<b>Benchmark Reference:</b> Wispr Flow Production Benchmark (Video, Reference Images, Electron Binary)<br>
<b>Current Baseline:</b> VOX Codebase Audit (v1.0 Baseline in <code>src/voxlocal/</code>)<br>
<b>Target Standard:</b> Full Wispr Flow Parity & Requirements R1–R5 Compliance<br>
<b>Compliance Mandate:</b> Zero lines starting with markdown <code>#</code> or <code>*</code> across all sections; strict HTML headers (<code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, <code>&lt;h4&gt;</code>) or bold text.</p>

<hr>

<h2>1. Executive Summary & Strategic Gap Assessment</h2>

<p>This document presents the definitive gap analysis comparing the current VOX desktop implementation against the production benchmark of Wispr Flow and the binding requirements established in <code>ORIGINAL_REQUEST.md</code>. VOX already possesses a high-performance offline speech engine powered by Faster-Whisper, local WASAPI audio loopback, and a rich PyQt6 UI layer. However, crucial architectural disconnections, missing integrations, and UI layout issues prevent VOX from delivering the cohesive, frictionless experience of Wispr Flow.</p>

<p>The gap analysis identifies four critical remediation vectors:</p>

<ul>
  <li><b>Floating Voice Pill Disconnect & Mode Deficit (P0 & P1):</b> The modern dual-tier floating pill exists in <code>src/voxlocal/ui/floating_pill.py</code>, but <code>main.py</code> still instantiates the legacy 8-icon dock. Furthermore, the floating pill only provides 3 writing mode chips, omitting Verbatim and Clean.</li>
  <li><b>Microsoft Outlook Calendar Integration (P0 Blocker — 0% Implemented):</b> VOX has no calendar integration whatsoever. Call detection is currently purely reactive via registry polling. Building a three-tier calendar subsystem (MS Graph OAuth2 PKCE, local MAPI COM fallback, and offline Mock provider) with 15/5-minute advance alerts is a prerequisite for R2 compliance.</li>
  <li><b>Meeting Intelligence & 3-Section Workspace Refinement (P1):</b> While <code>meeting_studio.py</code> already contains the 3-section layout (My Thoughts | Transcript | Summary), it requires tighter integration with calendar events, strict privacy sequestering of the personal scratchpad from MCP queries, and refined follow-up automations.</li>
  <li><b>Information Architecture Overhaul (P1):</b> <code>MainWindow</code> presents 8 cluttered tabs instead of the 5 primary workspaces (Dictation, Meetings, History, Vocabulary, Settings) mandated by R3. Secondary features like Screen Recording and Ask Vox must be embedded contextually.</li>
</ul>

<hr>

<h2>2. Side-by-Side Feature Gap Matrix (Wispr Flow vs. Current VOX vs. Target VOX)</h2>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Feature Capability</th>
    <th>Wispr Flow Production Benchmark</th>
    <th>Current VOX Codebase</th>
    <th>Target VOX Evolution</th>
    <th>Severity</th>
    <th>Milestone</th>
  </tr>
  <tr>
    <td><b>Floating Voice Pill</b></td>
    <td>Dual-tier pill, 12-bar equalizer, session timer, dismiss '✕', stop button.</td>
    <td><code>DualTierFloatingPill</code> exists in <code>floating_pill.py</code>, but <code>main.py</code> launches legacy 8-icon vertical dock.</td>
    <td>Wire <code>DualTierFloatingPill</code> as default in <code>main.py</code>, synchronized with audio engine lifecycle.</td>
    <td><b>P0 (Blocker)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Five Writing Modes</b></td>
    <td>Verbatim, Clean, Polished, Structured, Email with instant keyboard shortcuts.</td>
    <td><code>dictation_processor.py</code> has 5 modes with legacy keys (<code>CLEANED</code>, <code>MAGIC</code>). Pill only shows 3 chips.</td>
    <td>Expand pill to 5 mode chips (Win+F1 to Win+F5). Align enum to Verbatim, Clean, Polished, Structured, Email.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Raw Transcript Invariance</b></td>
    <td>Maintains raw acoustic recognition intact while rendering transformed text dynamically.</td>
    <td>Partially implemented in processor, but SQLite history schema needs explicit two-column preservation.</td>
    <td>Enforce immutable <code>raw_transcript</code> and dynamic <code>transformed_text</code> in SQLite schema with retroactive re-rendering.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Speech Self-Correction & Strikethrough</b></td>
    <td>Live preview bubble renders retracted phrases with crimson strikethrough in real time.</td>
    <td><code>detect_verbal_self_corrections</code> parses markers and outputs HTML; preview bubble has basic rendering.</td>
    <td>Wire real-time streaming tokens into <code>LiveSpeechPreviewBubble</code> with instant crimson strikethrough styling.</td>
    <td><b>P2 (High)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Non-Activating Window Focus</b></td>
    <td>Clicking floating pill or chips never steals focus from target application.</td>
    <td><code>apply_non_activating_floating_style</code> applies <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code>.</td>
    <td>Preserve existing Win32 styles; verify zero focus loss across Notepad, Word, Teams, and Chrome.</td>
    <td><b>P2 (High)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Bilingual Hindi/English/Hinglish</b></td>
    <td>High-accuracy Indian English, Devanagari loanwords, and conversational Hinglish.</td>
    <td>Comprehensive <code>DEVA_ORTHOGRAPHIC_FIXES</code>, <code>DEVA_TO_ENGLISH_LOANWORDS</code>, and schwa deletion implemented.</td>
    <td>Preserve and expand loanword rules; ensure active title context harvesting primes vocabulary.</td>
    <td><b>P3 (Moderate)</b></td>
    <td>Milestone M2</td>
  </tr>
  <tr>
    <td><b>Microsoft Outlook Calendar Client</b></td>
    <td>Syncs upcoming meetings, displays advance notifications, primes vocabulary.</td>
    <td><b>0% Implemented:</b> No calendar client, OAuth, MAPI, or polling code exists in repository.</td>
    <td>Build <code>src/voxlocal/calendar/</code> with MS Graph OAuth2 PKCE, local MAPI fallback, and offline mock provider.</td>
    <td><b>P0 (Blocker)</b></td>
    <td>Milestone M3</td>
  </tr>
  <tr>
    <td><b>Advance Meeting Alerts & 1-Click Capture</b></td>
    <td>Notifies user before call starts; 1-click launch captures local loopback without cloud bots.</td>
    <td>Reactive only via registry microphone consent polling after call has already started.</td>
    <td>Implement background polling daemon (15-min and 5-min alerts) and floating 1-click capture banner.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M3</td>
  </tr>
  <tr>
    <td><b>3-Section Meeting Workspace</b></td>
    <td>Strict layout: My Thoughts | Transcript | Summary.</td>
    <td>Implemented in <code>meeting_studio.py</code> via Page 0 tabs, but Page 1 retains 8 legacy tabs.</td>
    <td>Standardize on My Thoughts, Transcript, and Summary. Cleanly integrate calendar events and export options.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M3</td>
  </tr>
  <tr>
    <td><b>Meeting Intelligence Deliverables</b></td>
    <td>Executive summary, 7-state decisions, action items matrix, risks, follow-up emails, participant checklists.</td>
    <td>Implemented in <code>meeting_intelligence.py</code> and <code>VoxFollowUpEmailDialog</code>.</td>
    <td>Wire deliverables seamlessly into Summary tab; verify Slack-ready checklist copy and mailto links.</td>
    <td><b>P2 (High)</b></td>
    <td>Milestone M3</td>
  </tr>
  <tr>
    <td><b>Local Data Privacy & MCP Quarantine</b></td>
    <td>Audio, transcripts, and AI processing strictly local; zero external leakage.</td>
    <td>Local inference via Faster-Whisper; SQLite database; MCP personal typing quarantine defined in spec.</td>
    <td>Enforce strict quarantine of personal dictations and "My Thoughts" scratchpad from external MCP tool queries.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M3</td>
  </tr>
  <tr>
    <td><b>5-Workspace Navigation Architecture</b></td>
    <td>Minimal 5-section navigation: Dictation, Meetings, History, Vocabulary, Settings.</td>
    <td><code>main_window.py</code> has 8 top-level tabs across 3 groups, creating visual clutter.</td>
    <td>Consolidate into 5 primary workspaces; embed Screen Recording and Ask Vox contextually; add Ctrl+K Command Palette.</td>
    <td><b>P1 (Critical)</b></td>
    <td>Milestone M4</td>
  </tr>
  <tr>
    <td><b>Visual Design & Design Tokens</b></td>
    <td>Warm ivory canvas (<code>#FAF8F1</code>), 1.5px card borders, Figtree/EBGaramond typography.</td>
    <td>QSS dark/light themes exist; floating pill styling is modern, but main window needs styling alignment.</td>
    <td>Apply <code>--dawn</code> warm palette, 1.5px borders, and refined typography across all main window views.</td>
    <td><b>P2 (High)</b></td>
    <td>Milestone M4</td>
  </tr>
  <tr>
    <td><b>Automated Testing & Build Verification</b></td>
    <td>Comprehensive unit, integration, and packaging test coverage.</td>
    <td>52 test files with 431 test functions; baseline suites passing; sandbox UWP execution alias restriction.</td>
    <td>Add unit tests for 5 writing modes, calendar providers, and 5 workspaces; rebuild production Windows executable.</td>
    <td><b>P0 (Blocker)</b></td>
    <td>Milestone M5</td>
  </tr>
</table>

<hr>

<h2>3. Deep Dive Gap 1: Voice Dictation & 5 Writing Modes Delta</h2>

<h3>3.1 Root Cause of Floating Pill Disconnection</h3>

<p>In <code>main.py</code> (lines 28 and 165), the application imports <code>FloatingWidget</code> from <code>voxlocal.ui.floating_widget</code> rather than <code>DualTierFloatingPill</code> from <code>voxlocal.ui.floating_pill</code>. While <code>DualTierFloatingPill</code> was authored with superior aesthetics, non-activating window styles, and a 12-bar equalizer, it was never wired into the runtime application controller or global hotkey callbacks.</p>

<p><b>Remediation Action (M2):</b></p>
<ol>
  <li>Update <code>main.py</code> imports: Replace <code>FloatingWidget</code> with <code>DualTierFloatingPill</code>.</li>
  <li>Connect signals from <code>AppController</code> (dictation started, dictation stopped, microphone energy level, interim text) directly to <code>DualTierFloatingPill</code> slots.</li>
  <li>Ensure window state persistence stores and restores floating pill screen coordinates across application launches.</li>
</ol>

<h3>3.2 Writing Modes Expansion & Nomenclature Alignment</h3>

<p>The current implementation of <code>DualTierFloatingPill</code> contains only 3 mode chips (<code>chip_magic</code>, <code>chip_structured</code>, and <code>chip_email</code>), bound to <code>Win+F1</code>, <code>Win+F2</code>, and <code>Win+F3</code>. <b>Verbatim</b> and <b>Clean</b> modes are entirely absent from the floating UI.</p>

<p>Furthermore, in <code>src/voxlocal/engine/dictation_processor.py</code>, the modes are enumerated as <code>VERBATIM</code>, <code>CLEANED</code>, <code>MAGIC</code>, <code>STRUCTURED</code>, and <code>EMAIL</code>. Requirement R1 explicitly defines the 5 modes as Verbatim, Clean, Polished, Structured, and Email.</p>

<p><b>Remediation Action (M2):</b></p>
<ol>
  <li>Update <code>TransformationMode</code> enum in <code>dictation_processor.py</code>:
    <br>- <code>VERBATIM = "verbatim"</code>
    <br>- <code>CLEAN = "clean"</code>
    <br>- <code>POLISHED = "polished"</code> (with alias <code>MAGIC = "polished"</code>)
    <br>- <code>STRUCTURED = "structured"</code>
    <br>- <code>EMAIL = "email"</code></li>
  <li>Expand <code>DualTierFloatingPill</code> lower tier to instantiate 5 interactive chips:
    <br>- <code>chip_verbatim</code> (Win+F1)
    <br>- <code>chip_clean</code> (Win+F2)
    <br>- <code>chip_polished</code> (Win+F3)
    <br>- <code>chip_structured</code> (Win+F4)
    <br>- <code>chip_email</code> (Win+F5)</li>
  <li>Update <code>_cycle_next_mode</code> and keyboard shortcut manager to cycle across all five modes sequentially.</li>
</ol>

<hr>

<h2>4. Deep Dive Gap 2: Microsoft Outlook Calendar Integration Delta</h2>

<h3>4.1 Comprehensive 0% Gap Analysis</h3>

<p>Microsoft Outlook Calendar integration represents the single largest functional omission in the VOX codebase. Currently, VOX has:</p>

<ul>
  <li><b>No Calendar Client:</b> Zero code interacting with Microsoft Graph API or Outlook MAPI COM.</li>
  <li><b>No Authentication Flow:</b> Zero OAuth 2.0 PKCE implementation, token storage, or refresh logic.</li>
  <li><b>No Upcoming Meeting Poller:</b> Zero scheduled background workers checking calendar events.</li>
  <li><b>No Advance Notification Banner:</b> Zero UI for alerting users 15 minutes or 5 minutes before meetings.</li>
  <li><b>No Mock Calendar Provider:</b> Zero test fixtures for offline automated calendar verification.</li>
</ul>

<h3>4.2 Architecture of the Calendar Subsystem (<code>src/voxlocal/calendar/</code>)</h3>

<p>To achieve full compliance with Requirement R2, a new package <code>src/voxlocal/calendar/</code> will be established with four core modules:</p>

<ol>
  <li><b><code>base_provider.py</code>:</b> Defines the abstract interface <code>BaseCalendarProvider</code> with methods <code>authenticate()</code>, <code>get_upcoming_events(window_minutes)</code>, <code>disconnect()</code>, and <code>is_authenticated()</code>, along with the <code>CalendarEvent</code> dataclass.</li>
  <li><b><code>graph_provider.py</code>:</b> Implements OAuth 2.0 PKCE flow against Microsoft Graph API. Requests minimal scopes (<code>Calendars.Read</code> and <code>offline_access</code>). Stores serialized tokens encrypted via Windows DPAPI (<code>CryptProtectData</code>). Executes proactive token refresh before expiration.</li>
  <li><b><code>mapi_provider.py</code>:</b> Local fallback for corporate workstations where tenant policies block third-party Graph API client apps. Automates local Outlook desktop via <code>win32com.client.Dispatch("Outlook.Application")</code> without cloud network egress.</li>
  <li><b><code>mock_provider.py</code>:</b> Deterministic offline test fixture for CI/CD and unit testing. Injects pre-configured meeting queues (e.g. meeting in 14 minutes, meeting in 4 minutes, cancelled meeting) and simulates error scenarios (401 Unauthorized, network loss, token expiration).</li>
  <li><b><code>polling_daemon.py</code>:</b> Background <code>QThread</code> polling upcoming events every 60 seconds. Triggers 15-minute acoustic vocabulary priming (adding attendee names to the custom vocabulary table) and pops the 5-minute advance notification banner.</li>
</ol>

<hr>

<h2>5. Deep Dive Gap 3: Meeting Workspace & Follow-Up Automation Delta</h2>

<h3>5.1 3-Section Meeting Studio Harmonization</h3>

<p>While <code>src/voxlocal/ui/meeting_studio.py</code> contains the 3-tab layout on Page 0 (<code>VoxMyThoughtsTab</code>, <code>VoxInteractiveTranscriptTab</code>, <code>VoxSummaryDeliverablesTab</code>), several gaps remain:</p>

<ul>
  <li><b>Page 1 Legacy Clutter:</b> Page 1 still contains an 8-tab Advanced Studio mode that creates architectural redundancy. The 3-section layout must be established as the definitive primary interface.</li>
  <li><b>Calendar Event Association:</b> Captured meetings are not currently linked to scheduled calendar events or attendee rosters. Integrating with the new calendar client will automatically populate the meeting title, start time, and attendee list.</li>
  <li><b>Privacy Boundary Enforcement:</b> The personal scratchpad (<code>VoxMyThoughtsTab</code>) must remain strictly quarantined from external exports and external MCP AI queries, returning <code>403 Forbidden</code> unless explicitly authorized by the user.</li>
</ul>

<h3>5.2 Meeting Intelligence Deliverables Polish</h3>

<ul>
  <li><b>7-State Decision Tracker:</b> Ensure decision evolution states (<code>proposed</code>, <code>tentative</code>, <code>confirmed</code>, <code>rejected</code>, <code>unresolved</code>, <code>superseded</code>, <code>reversed</code>) are visually distinct with color-coded badges and click-to-seek timestamp links.</li>
  <li><b>1-Click Follow-Up Email Dialog:</b> Verify that <code>VoxFollowUpEmailDialog</code> generates Rich HTML, Markdown, and <code>mailto:</code> links with full attendee pre-population.</li>
  <li><b>Participant Checklists:</b> Verify that tasks are properly partitioned by owner and include a 1-click "Copy Slack Checklists" button with <code>@mention</code> formatting.</li>
</ul>

<hr>

<h2>6. Deep Dive Gap 4: Product Information Architecture & UI Refinement Delta</h2>

<h3>6.1 Deconstruction of the 8-Tab Navigation Clutter</h3>

<p>In <code>src/voxlocal/ui/main_window.py</code> (lines 514-521), navigation is organized across 8 top-level tabs:</p>

<ol>
  <li><code>🎙️ Voice Typing</code> (Core)</li>
  <li><code>🖥️ Screen Recording</code> (Secondary capability)</li>
  <li><code>⏺ Meeting Studio</code> (Core)</li>
  <li><code>📊 Reports</code> (Secondary / historical archive)</li>
  <li><code>🧠 Ask Vox</code> (Secondary knowledge query)</li>
  <li><code>📁 Folders & Actions</code> (File archive / management)</li>
  <li><code>📖 Vocabulary</code> (Configuration)</li>
  <li><code>⚙️ Settings</code> (Configuration)</li>
</ol>

<p>This layout overwhelms the user with too many competing top-level options, departing from the minimalist, focused desktop experience of Wispr Flow.</p>

<h3>6.2 Consolidation into 5 Primary Workspaces</h3>

<p>Requirement R3 mandates restructuring the application into exactly 5 primary workspaces:</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Workspace</th>
    <th>Shortcut</th>
    <th>Primary Workflow & Subordinated Capabilities</th>
  </tr>
  <tr>
    <td><b>1. Dictation</b></td>
    <td><code>Ctrl + 1</code></td>
    <td>Voice typing control hub, writing mode configuration (Verbatim, Clean, Polished, Structured, Email), live microphone VU level meter, floating pill settings.</td>
  </tr>
  <tr>
    <td><b>2. Meetings</b></td>
    <td><code>Ctrl + 2</code></td>
    <td>Meeting intelligence studio, active recording launcher, upcoming Outlook Calendar schedule banner, 3-section workspace (My Thoughts | Transcript | Summary). Embeds <b>Screen Recording</b> as an optional capture mode.</td>
  </tr>
  <tr>
    <td><b>3. History</b></td>
    <td><code>Ctrl + 3</code></td>
    <td>Unified searchable archive of past dictations and meeting sessions. Absorbs previous <i>Reports</i> and <i>Folders & Actions</i> tabs. Provides full-text SQLite search, media playback, and batch export (TXT, MD, SRT, VTT). Embeds <b>Ask Vox</b> global repository queries.</td>
  </tr>
  <tr>
    <td><b>4. Vocabulary</b></td>
    <td><code>Ctrl + 4</code></td>
    <td>Personal and domain dictionary, spoken phrase to replacement mapping, acronyms, brand names, Hindi/Hinglish loanword management, CSV/JSON import and export.</td>
  </tr>
  <tr>
    <td><b>5. Settings</b></td>
    <td><code>Ctrl + 5</code></td>
    <td>Global hotkey configuration, audio device selector, Faster-Whisper model selection & precision (INT8/FP16), Microsoft Outlook Calendar account connection & OAuth, theme toggle (Light/Dark), startup preferences.</td>
  </tr>
</table>

<p><b>Command Palette (<code>Ctrl + K</code>):</b> A lightweight floating modal allowing instant keyboard navigation to any workspace, recent meeting, or action command ("Record Meeting", "Toggle Dark Mode", "Export Last MoM").</p>

<hr>

<h2>7. Prioritized Implementation Backlog & Risk Register</h2>

<h3>7.1 Implementation Roadmap by Milestone</h3>

<ul>
  <li><b>Milestone M2: Voice Dictation Engine & Floating UI</b>
    <br>- Wire <code>DualTierFloatingPill</code> as default in <code>main.py</code>.
    <br>- Add <code>chip_verbatim</code> and <code>chip_clean</code> to lower tier; configure <code>Win+F1</code> through <code>Win+F5</code> shortcuts.
    <br>- Harmonize <code>TransformationMode</code> enum in <code>dictation_processor.py</code> with backwards compatibility aliases.
    <br>- Enforce two-column SQLite schema (<code>raw_transcript</code>, <code>transformed_text</code>).
    <br>- Connect live streaming tokens with crimson strikethrough preview in <code>LiveSpeechPreviewBubble</code>.</li>
  <li><b>Milestone M3: Meeting Intelligence & Microsoft Calendar</b>
    <br>- Implement <code>src/voxlocal/calendar/</code> package (Base, MS Graph OAuth2, Local MAPI, Mock provider, Polling daemon).
    <br>- Implement 15-minute vocabulary priming and 5-minute advance meeting notification banner.
    <br>- Refine 3-section Meeting Studio workspace (My Thoughts, Transcript, Summary).
    <br>- Enforce privacy quarantine: scratchpad notes locked against external exports and MCP queries.
    <br>- Wire 7-state decisions, action item matrix, follow-up emails, and participant checklists.</li>
  <li><b>Milestone M4: Main Window Restructure & Performance Optimization</b>
    <br>- Reorganize <code>main_window.py</code> into 5 primary workspaces (Dictation, Meetings, History, Vocabulary, Settings).
    <br>- Contextually embed Screen Recording within Meetings and Ask Vox within History.
    <br>- Implement Ctrl+K Command Palette.
    <br>- Optimize cold/warm startup times, idle CPU/RAM utilization, and transcription latency.</li>
  <li><b>Milestone M5: Comprehensive Verification, Reports & Executable Rebuild</b>
    <br>- Execute full unit and integration test suite across existing 52 test files and new tests.
    <br>- Author <code>VOX_IMPLEMENTATION_CHANGELOG.md</code> and <code>VOX_FINAL_VALIDATION_REPORT.md</code>.
    <br>- Rebuild production Windows executable (<code>VoxLocal.exe</code>) via <code>VoxLocal.spec</code>.
    <br>- Audit entire repository for strict zero `#` or `*` header compliance.</li>
</ul>

<h3>7.2 Risk Register & Mitigation Strategies</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%;">
  <tr style="background:#f4f4f5;">
    <th>Risk ID</th>
    <th>Identified Risk Description</th>
    <th>Likelihood</th>
    <th>Impact</th>
    <th>Proactive Mitigation Strategy</th>
  </tr>
  <tr>
    <td><b>RSK-01</b></td>
    <td>Microsoft Graph OAuth blocked by corporate enterprise tenant policies.</td>
    <td>Medium</td>
    <td>High</td>
    <td>Implement Local MAPI provider as automated fallback via Win32 COM, requiring zero tenant administration or cloud app registration.</td>
  </tr>
  <tr>
    <td><b>RSK-02</b></td>
    <td>Unit tests fail in offline/CI environments due to calendar network calls.</td>
    <td>Low</td>
    <td>Critical</td>
    <td>Implement <code>MockCalendarProvider</code> that runs 100% offline with zero network dependency, injected into all automated test suites.</td>
  </tr>
  <tr>
    <td><b>RSK-03</b></td>
    <td>Target applications resist synthetic keystroke typing via <code>SendInput</code>.</td>
    <td>Low</td>
    <td>Medium</td>
    <td>Provide instantaneous clipboard paste (<code>Ctrl+V</code>) fallback that preserves and restores previous clipboard contents within 150ms.</td>
  </tr>
  <tr>
    <td><b>RSK-04</b></td>
    <td>Navigation refactoring introduces regressions in existing tab widgets.</td>
    <td>Medium</td>
    <td>High</td>
    <td>Preserve existing widget classes (<code>VoiceTypingWidget</code>, <code>VoxMeetingStudioWorkspace</code>, <code>VocabularyManagerWidget</code>) and embed them cleanly within the 5 workspace stacks.</td>
  </tr>
  <tr>
    <td><b>RSK-05</b></td>
    <td>Windows sandbox denies execution of Microsoft Store Python alias.</td>
    <td>High</td>
    <td>Low</td>
    <td>Rely on direct Node.js programmatic verification and standalone PyInstaller executable execution in <code>dist/VoxLocal/</code>.</td>
  </tr>
</table>

<hr>

<p><b>Verification Note:</b> This gap analysis report has been programmatically scanned. Zero lines begin with markdown <code>#</code> or <code>*</code> characters. All headings strictly utilize HTML tags or bold text.</p>

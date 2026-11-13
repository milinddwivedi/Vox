<h2>VOX Desktop Productivity Assistant — Integration, Security, &amp; Privacy Specification</h2>

<p><b>Document Identifier:</b> VOX-SEC-SPEC-2026-V2<br/>
<b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Document Purpose:</b> Authoritative specification for local-first data governance, zero-cloud privacy architecture, bot-free native meeting detection, permission-controlled Model Context Protocol (MCP) server, external integrations (Slack, Calendar), and client export sanitization.<br/>
<b>Reference Standards:</b> Local-First Software Manifesto, Model Context Protocol Specification (2024.11), NIST SP 800-88 Data Sanitization, OWASP Desktop Application Security Standards.<br/>
<b>Formatting Mandate:</b> Strict HTML header compliance (zero markdown <code>#</code> or <code>*</code> headers across all sections).</p>

<hr/>

<h2>1. Executive Security &amp; Data Governance Philosophy</h2>

<p>VOX is designed to operate under a <b>Zero-Trust, Zero-Egress, Local-First Architecture</b>. In an era where third-party meeting note-takers and cloud voice assistants route sensitive enterprise conversations, executive deliberations, and intellectual property through external cloud servers, VOX establishes a non-negotiable architectural guarantee: <b>Your workstation is the security perimeter.</b></p>

<h3>1.1 The Five Invariant Security Guarantees</h3>
<ul>
  <li><b>Guarantee 1: 100% Offline Default:</b> All audio capture, neural speech recognition (Faster-Whisper), language comprehension (Llama-3.2-1B-Instruct), speaker diarization, and SQLite storage execute completely offline. VOX functions with 100% feature fidelity when the host machine is completely air-gapped or disconnected from the network.</li>
  <li><b>Guarantee 2: Zero Telemetry &amp; Zero Audio Egress:</b> VOX transmits zero acoustic frames, zero keystroke logs, zero screen pixels, and zero transcripts to any cloud endpoint. No unconsented analytics or telemetry pings exist in the codebase.</li>
  <li><b>Guarantee 3: Bot-Free In-Session Privacy:</b> VOX never joins a video conference as an external bot account. Meetings are captured locally and quietly via native Windows audio APIs, preventing third-party platform data harvesting.</li>
  <li><b>Guarantee 4: Absolute Voice Typing Dictation Isolation:</b> Personal voice typing dictations are strictly sequestered from external integration bridges. The Model Context Protocol (MCP) server has zero access to ephemeral dictation logs.</li>
  <li><b>Guarantee 5: Explicit Interactive Authorization:</b> Any external interaction&mdash;whether querying meeting context via MCP or posting an action item to Slack&mdash;requires explicit, interactive user preview and consent. VOX strictly prohibits background autonomous data egress.</li>
</ul>

<hr/>

<h2>2. Bot-Free Native Desktop Meeting Detection &amp; Session Isolation</h2>

<p>Traditional meeting note-takers (e.g., Otter, Read.ai, Fireflies) dispatch cloud-hosted bot accounts into conference calls. This introduces severe organizational friction: corporate security firewalls block external attendees, participants feel surveilled, and confidential conversations are exposed to third-party cloud infrastructure. VOX replaces this paradigm with <b>Native Desktop Session Telemetry</b>.</p>

<h3>2.1 Windows Audio Session &amp; Microphone Registry Telemetry</h3>
<p>VOX detects the onset of a conference call without external network inspection by monitoring Windows OS audio subsystem telemetry (<code>CallDetector</code> in <code>src/voxlocal/audio/call_detector.py</code>):</p>

<ul>
  <li><b>Microphone Consent Store Registry Polling:</b>
    <ul>
      <li>Windows logs active microphone usage in the registry:
        <br/><code>HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\[PackageFamilyName_or_NonPackaged]</code></li>
      <li>VOX periodically samples the <code>LastUsedTimeStop</code> registry keys. When an application sets <code>LastUsedTimeStop = 0</code>, Windows confirms that the application has opened a live, active audio recording handle to a physical microphone.</li>
    </ul>
  </li>
  <li><b>Win32 Top-Level Window Classification:</b>
    <ul>
      <li>Upon detecting active microphone usage, the detector correlates the active process ID (PID) against top-level window titles using Win32 <code>EnumWindows()</code> and <code>GetWindowText()</code>:</li>
    </ul>
  </li>
</ul>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Platform Name</th>
      <th>Executable Image Name</th>
      <th>Call Stage Window Title Heuristics</th>
      <th>Excluded Background Surfaces</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Microsoft Teams</b></td>
      <td><code>ms-teams.exe</code>, <code>Teams.exe</code></td>
      <td>Window class <code>TeamsWebView</code> with title containing <code>"| Microsoft Teams"</code> or <code>"Meeting"</code>, excluding chat list.</td>
      <td>General Teams channel chats, direct message threads, file browsers.</td>
    </tr>
    <tr>
      <td><b>Zoom Workplace</b></td>
      <td><code>Zoom.exe</code></td>
      <td>Window class <code>ZPContentViewWndClass</code>, <code>ZPControlPanelWndClass</code> with title containing <code>"Zoom Meeting"</code> or <code>"Zoom Webinar"</code>.</td>
      <td>Zoom home dashboard, contact directory, settings window.</td>
    </tr>
    <tr>
      <td><b>Google Meet</b></td>
      <td><code>chrome.exe</code>, <code>msedge.exe</code>, <code>brave.exe</code></td>
      <td>Browser tab title matching regex: <code>^Meet\s*[-–]\s*[a-z]{3}-[a-z]{4}-[a-z]{3}</code>.</td>
      <td>Other browser tabs, Gmail inbox, general web surfing.</td>
    </tr>
    <tr>
      <td><b>Slack Huddles</b></td>
      <td><code>slack.exe</code></td>
      <td>Window title containing <code>"Huddle"</code> with active microphone stream.</td>
      <td>Slack channel history, direct messages, canvas documents.</td>
    </tr>
    <tr>
      <td><b>Cisco Webex</b></td>
      <td><code>CiscoCollabHost.exe</code>, <code>webex.exe</code></td>
      <td>Window title containing <code>"Webex Meeting"</code> or <code>"Personal Room"</code>.</td>
      <td>Webex messaging list and calendar overview.</td>
    </tr>
  </tbody>
</table>

<h3>2.2 1-Click Explicit Consent Workflow &amp; Deduplication</h3>
<ul>
  <li><b>Non-Intrusive Floating Prompt (<code>CallConsentDialog</code>):</b> When an active meeting is confirmed, VOX surfaces a lightweight, non-activating floating prompt at the bottom-right of the screen:
    <br/><i>"Active Call Detected: [Platform Name] &bull; Record Meeting?"</i>
  </li>
  <li><b>User Selection Options:</b>
    <ul>
      <li><b>Audio Only:</b> Captures dual-track WASAPI mic and loopback audio.</li>
      <li><b>Audio + Video:</b> Captures dual-track audio plus source-bound meeting stage screen recording.</li>
      <li><b>Ignore / Dismiss:</b> Immediately closes prompt; zero audio buffers or video frames are retained.</li>
    </ul>
  </li>
  <li><b>Fail-Closed Privacy Guard:</b> If the user ignores the prompt or closes it without clicking an option, VOX discards all pre-buffered frames. No audio is ever recorded without affirmative user opt-in.</li>
  <li><b>Session Deduplication:</b> VOX generates a persistent, deterministic session hash:
    <br/><code>session_uid = SHA256(platform + process_id + window_title + start_date_hour)</code>
    <br/>Resizing, dragging, minimizing, or switching tabs within the meeting application never re-triggers redundant consent dialogs.</li>
</ul>

<h3>2.3 Strict Call-Only Window Bounding &amp; Privacy Fail-Closed</h3>
<p>When the user elects to record video alongside audio, VOX enforces strict cryptographic isolation of the video stream:</p>

<ul>
  <li><b>HWND-Locked Video Capture:</b> Capture coordinates are strictly locked to the verified meeting call stage window handle (<code>HWND</code>). Capture never falls back to the full desktop DC (<code>GetDC(0)</code>).</li>
  <li><b>Zero Background Bleed:</b> If the user switches focus to personal or confidential applications (e.g., corporate email, personal WhatsApp, bank account, IDE), zero frames of those background applications are ingested into the recording pipeline.</li>
  <li><b>Privacy Test Card Replacement on Minimize:</b>
    <ul>
      <li>If the meeting window is minimized, hidden behind other windows, or moved off-screen, the video capture worker immediately ceases sampling pixels from the window.</li>
      <li>It substitutes an opaque, neutral status frame:
        <br/><i>"Meeting window minimized &mdash; Privacy Guard Active"</i></li>
      <li>Crucially, the worker generates these placeholder frames with identical wall-clock Presentation Time Stamps (PTS), ensuring that audio-video lip-sync remains mathematically synchronized without capturing private screen surfaces.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>3. Local Model Context Protocol (MCP) Server Architecture</h2>

<p>VOX integrates with external AI assistants (e.g., Claude Desktop, ChatGPT Desktop, local agent frameworks) via the <b>Model Context Protocol (MCP)</b>. Rather than uploading user transcripts to cloud vector databases, VOX implements a local, loopback-only MCP server (<code>VoxMcpServer</code>) that enables AI models to interrogate meeting knowledge under strict user surveillance.</p>

<h3>3.1 Transport &amp; Cryptographic Loopback Binding</h3>
<ul>
  <li><b>Transport Protocols:</b> Standard Input/Output (<code>stdio</code>) for desktop agents spawned locally, or Loopback Server-Sent Events (<code>SSE</code>) bound strictly to <code>127.0.0.1</code> on an ephemeral, non-privileged port.</li>
  <li><b>Loopback-Only Invariant:</b> The MCP server binds exclusively to the IPv4 loopback adapter (<code>127.0.0.1</code>) and refuses connections originating from external network interfaces or remote IP addresses.</li>
  <li><b>Session Token Authentication:</b> Each VOX instance generates a cryptographically secure 256-bit random session token (<code>VOX_MCP_TOKEN</code>) stored in memory. Inbound SSE requests must provide this bearer token in the <code>Authorization: Bearer &lt;token&gt;</code> HTTP header.</li>
</ul>

<h3>3.2 The Fundamental Dictation Boundary Guarantee</h3>
<p>As verified in the Wispr Flow forensic investigation (Phase 10, frame 54.0s), desktop users require absolute separation between ephemeral personal typing and multi-party meeting records:</p>

<div style="background:#FEF2F2; border-left:4px solid #EF4444; padding:12px; margin:16px 0;">
  <b>NON-NEGOTIABLE SECURITY INVARIANT:</b><br/>
  <b>The VOX MCP server strictly prohibits access to personal voice typing dictations.</b><br/>
  Zero tools, endpoints, or database queries exist within the MCP server codebase that can access, search, or stream ephemeral dictation transcripts, audio recordings, or text-insertion logs. Only persistent, user-finalized meeting notes and transcripts are queryable.
</div>

<h3>3.3 Exposed MCP Tools &amp; Granular Permission Scopes</h3>
<p>The VOX MCP server exposes four discrete tools with fine-grained access boundaries:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Tool Name</th>
      <th>Required Scope</th>
      <th>Input Parameters</th>
      <th>Returned Payload &amp; Constraints</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>vox_list_meetings</code></td>
      <td><code>meetings:list</code></td>
      <td><code>limit: int = 10, offset: int = 0, query: str = None</code></td>
      <td>List of meeting metadata: <code>id</code>, <code>title</code>, <code>platform</code>, <code>date</code>, <code>duration</code>, <code>attendees</code>. Zero transcript text returned.</td>
    </tr>
    <tr>
      <td><code>vox_get_meeting_summary</code></td>
      <td><code>meetings:summary</code></td>
      <td><code>meeting_id: int</code></td>
      <td>Executive overview, confirmed decisions (ADR-001), grounded action items (ADR-003), and risks. Suppresses all internal scratchpad thoughts.</td>
    </tr>
    <tr>
      <td><code>vox_search_transcripts</code></td>
      <td><code>transcripts:search</code></td>
      <td><code>query: str, meeting_id: int = None, limit: int = 5</code></td>
      <td>Chronological transcript snippets matching query via SQLite FTS5 with speaker tags, quotes, and timestamps.</td>
    </tr>
    <tr>
      <td><code>vox_get_my_thoughts</code></td>
      <td><code>thoughts:read</code> (<b>LOCKED BY DEFAULT</b>)</td>
      <td><code>meeting_id: int</code></td>
      <td>Returns Tab 1 personal scratchpad notes <b>ONLY IF</b> the user has explicitly enabled <code>allow_external_mcp = 1</code> on that specific meeting. Otherwise returns <code>403 Forbidden</code>.</td>
    </tr>
  </tbody>
</table>

<h3>3.4 Interactive User Preview &amp; Consent Dialog</h3>
<p>External AI agents must never silently harvest meeting data in the background. Whenever an external tool invokes a VOX MCP endpoint, VOX intercepts the call and presents an interactive authorization dialog:</p>

<ul>
  <li><b>Visual Interception Modal:</b>
    <ul>
      <li>Displays requesting client identity (e.g., <i>"Claude Desktop (PID 18402)"</i>).</li>
      <li>Displays requested tool and target meeting: <i>"Requesting Summary for Meeting: 'Q4 Budget &amp; Product Launch Review'"</i>.</li>
      <li>Shows an expandable raw payload preview so the user sees the exact text that will be transmitted.</li>
    </ul>
  </li>
  <li><b>Three-State Authorization Choices:</b>
    <ul>
      <li><b>Allow Once:</b> Permits this single tool execution. Subsequent calls re-prompt.</li>
      <li><b>Allow for Session (1 Hour):</b> Permits repeated queries for the specified meeting until VOX closes or 60 minutes elapse.</li>
      <li><b>Deny:</b> Immediately rejects the MCP request, returning an authorization denied error to the calling model.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>4. Calendar &amp; Slack Integration Security</h2>

<p>VOX integrates with local calendars (Google Calendar, Outlook Calendar) and team collaboration platforms (Slack) to prime custom vocabulary and streamline follow-up delivery.</p>

<h3>4.1 Local Credential Storage &amp; DPAPI Encryption</h3>
<ul>
  <li><b>Zero Cloud Proxy Tokens:</b> VOX never stores user OAuth refresh tokens on a cloud server. Tokens are negotiated directly between the local client and Google / Microsoft / Slack endpoints via loopback OAuth redirection (<code>http://127.0.0.1:[port]/oauth/callback</code>).</li>
  <li><b>Windows DPAPI Cryptographic Protection:</b> Stored tokens (access tokens, refresh tokens, webhook URLs) are encrypted on disk using the <b>Windows Data Protection API (DPAPI)</b> via <code>CryptProtectData()</code> with <code>CRYPTPROTECT_LOCAL_MACHINE</code> or user-level entropy.</li>
  <li><b>Token Isolation:</b> Tokens cannot be decrypted by standard processes running under other user accounts on the same workstation.</li>
</ul>

<h3>4.2 Read-Only Calendar Context Priming</h3>
<ul>
  <li><b>Scope Restriction:</b> Calendar integrations request strictly read-only scopes:
    <br/><code>https://www.googleapis.com/auth/calendar.events.readonly</code> (Google)
    <br/><code>Calendars.Read</code> (Microsoft Graph)
  </li>
  <li><b>Metadata Ingestion:</b> VOX queries upcoming events for the current day, extracting event titles, meeting links, and attendee display names.</li>
  <li><b>Acoustic Priming:</b> Extracted attendee names (e.g., "Anouk", "Bastien", "Soraya", "Zepto") are injected into the custom vocabulary table to bias Faster-Whisper's phonetic decoding during the upcoming call.</li>
  <li><b>Zero Body Scraping:</b> Meeting attachments, invite descriptions, and sensitive calendar notes are never ingested or indexed.</li>
</ul>

<h3>4.3 Explicit Pre-Transmission Slack Preview &amp; Redaction</h3>
<p>Delivering action items or checklists to Slack requires explicit user confirmation before any network packet leaves the machine:</p>

<ul>
  <li><b>Mandatory Preview Modal:</b> When the user clicks <i>"Share to Slack"</i>, VOX renders an interactive modal displaying:
    <ul>
      <li>Target Slack Workspace &amp; Channel Name.</li>
      <li>Exact formatted Slack markdown message payload (including mentions: <code>@Sarah</code>, <code>@Alex</code>).</li>
      <li>List of action items and due dates.</li>
    </ul>
  </li>
  <li><b>Automated Sensitive Entity Redaction:</b>
    <ul>
      <li>Before rendering the preview, the local redaction engine scans the message for sensitive patterns:
        <ul>
          <li>API Keys &amp; Secrets: High-entropy strings matching regex: <code>(?i)(bearer|token|secret|api_key|password)\s*[:=]\s*['"]?([a-zA-Z0-9_\-]{16,})['"]?</code></li>
          <li>Payment Card Numbers: 13-19 digit sequences satisfying the Luhn checksum algorithm.</li>
          <li>PII (Social Security / National ID numbers): Formatted entity patterns.</li>
        </ul>
      </li>
      <li>Detected sensitive tokens are automatically masked (e.g., <code>[REDACTED_API_KEY]</code>, <code>****-****-****-1092</code>) with a visual indicator alerting the user to the redaction.</li>
    </ul>
  </li>
  <li><b>Affirmative User Send:</b> The outbound HTTPS POST to Slack's webhook or API endpoint executes <b>only</b> after the user explicitly clicks <i>"Confirm &amp; Send to Slack"</i>.</li>
</ul>

<hr/>

<h2>5. Client Review Export Safeguards &amp; Internal Notes Suppression</h2>

<p>One of the greatest operational risks in consulting, legal, and enterprise settings is the accidental disclosure of internal commentary when sharing meeting minutes with clients or external stakeholders.</p>

<h3>5.1 Dual-Mode Export Architecture: Internal vs. Client Review MoM</h3>
<p>VOX establishes a strict architectural bifurcation in its export pipeline (<code>Exporter</code> in <code>src/voxlocal/storage/exporter.py</code>):</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Export Artifact</th>
      <th>Target Audience</th>
      <th>Included Sections &amp; Data Elements</th>
      <th>Suppressed Elements &amp; Security Boundary</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Internal Comprehensive MoM</b></td>
      <td>User &amp; Internal Team</td>
      <td>
        - Executive Overview.<br/>
        - Full Speaker Diarized Transcript.<br/>
        - Tab 1 "My Thoughts" Scratchpad Notes.<br/>
        - 7-State Decision Evolution (Internal Lineage).<br/>
        - Complete Action Items &amp; Internal Blockers.<br/>
        - Raw Disfluency &amp; Hesitation Analysis.
      </td>
      <td>Requires local user authentication; marked <code>INTERNAL CONFIDENTIAL</code>.</td>
    </tr>
    <tr>
      <td><b>Client Review MoM</b></td>
      <td>External Clients, Partners, Investors</td>
      <td>
        - Polished Executive Summary.<br/>
        - Confirmed Public Decisions (<code>status='confirmed'</code>).<br/>
        - Assigned Action Items with Deadlines.<br/>
        - Next Scheduled Milestone.<br/>
        - Professional Corporate Styling.
      </td>
      <td>
        - <b>100% SUPPRESSION</b> of Tab 1 "My Thoughts" Scratchpad.<br/>
        - Suppression of unconfirmed / rejected internal debates.<br/>
        - Suppression of raw transcript disfluencies.<br/>
        - Redaction of internal cost / margin notes.
      </td>
    </tr>
  </tbody>
</table>

<h3>5.2 Mathematical Verification of Export Sanitization</h3>
<ul>
  <li><b>Hard Query Constraint:</b> When compiling the <code>Client Review MoM</code>, the SQL query explicitly excludes the <code>my_thoughts</code> table:
    <br/><code>SELECT * FROM meetings WHERE id = :id; -- my_thoughts is NEVER joined or selected</code>
  </li>
  <li><b>Automated Post-Generation Sanitization Check:</b>
    <ul>
      <li>Before writing the final PDF, HTML, or Markdown document to disk, the exporter runs an automated substring audit comparing every paragraph in <code>my_thoughts</code> against the output text.</li>
      <li>If any sentence from the user's private scratchpad is detected in the client export payload, the exporter aborts immediately with a <code>SanitizationFailureException</code> and displays an emergency alert.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>6. Forensic Security Acceptance Criteria</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Verification ID</th>
      <th>Security Domain</th>
      <th>Quantitative / Qualitative Benchmark</th>
      <th>Verification Method</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>SEC-VER-01</b></td>
      <td>Zero Cloud Sockets</td>
      <td>0 outbound network sockets opened during full voice typing and meeting recording sessions.</td>
      <td>Dynamic network socket inspection in <code>test_phase12_security_privacy_hardening.py</code>.</td>
    </tr>
    <tr>
      <td><b>SEC-VER-02</b></td>
      <td>MCP Dictation Isolation</td>
      <td>MCP server returns <code>404 / Method Not Found</code> on any query targeting dictation logs.</td>
      <td>Automated MCP protocol test asserting 0 dictation tool registrations.</td>
    </tr>
    <tr>
      <td><b>SEC-VER-03</b></td>
      <td>Call-Only Window Privacy</td>
      <td>Minimizing meeting window during video capture records 0 frames of background applications.</td>
      <td>Minimized window privacy frame replacement assertion.</td>
    </tr>
    <tr>
      <td><b>SEC-VER-04</b></td>
      <td>DPAPI Token Encryption</td>
      <td>OAuth tokens on disk cannot be decrypted without active Windows user credentials.</td>
      <td>Local encrypted credential store test.</td>
    </tr>
    <tr>
      <td><b>SEC-VER-05</b></td>
      <td>Client MoM Sanitization</td>
      <td>Client Review MoM contains 0 characters of text from Tab 1 "My Thoughts" scratchpad.</td>
      <td>Export payload sanitization string audit test.</td>
    </tr>
    <tr>
      <td><b>SEC-VER-06</b></td>
      <td>1-Click Consent Fail-Closed</td>
      <td>Dismissing or ignoring call detection popup results in 0 bytes written to audio storage.</td>
      <td>Call consent fail-closed audit test.</td>
    </tr>
  </tbody>
</table>

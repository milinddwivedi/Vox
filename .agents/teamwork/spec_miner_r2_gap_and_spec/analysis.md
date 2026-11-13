<h2>VOX Evolution: Technical Contracts, Architecture Specifications, and Deliverable Templates</h2>

<p><b>Author:</b> teamwork_spec_miner (Specification Miner &amp; Technical Contract Specialist)<br/>
<b>Date:</b> 2026-11-08<br/>
<b>Working Directory:</b> <code>d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_r2_gap_and_spec\</code><br/>
<b>Target Document:</b> <code>analysis.md</code><br/>
<b>Authority:</b> <code>ORIGINAL_REQUEST.md</code> and VOX Codebase Audit<br/>
<b>Formatting Mandate:</b> Zero markdown <code>#</code> or <code>*</code> headers across all sections. All headings use HTML tags (<code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, <code>&lt;h4&gt;</code>) or bold text.</p>

<hr/>

<h2>1. Executive Summary &amp; Specification Mining Overview</h2>

<p>This technical specification establishes the binding architectural contracts, data schemas, state machines, and deliverable frameworks required to transform VOX into a premium, production-grade voice-first desktop productivity application inspired by Wispr Flow. Organized around two primary core experiences&mdash;<b>Voice Dictation &amp; Writing</b> and <b>Meeting Intelligence &amp; Notes</b>&mdash;this document defines the concrete interfaces, error behaviors, security boundaries, and validation criteria for implementation workers and validation auditors.</p>

<p>The specifications cover five core engineering pillars:</p>
<ol>
  <li><b>Microsoft Outlook Calendar Integration:</b> A robust, three-tier calendar provider architecture (MS Graph API OAuth 2.0 PKCE, Local MAPI COM fallback, and deterministic Mock provider) with minimal <code>Calendars.Read</code> scopes, 5/15-minute advance polling, Windows DPAPI credential protection, and 1-click meeting capture.</li>
  <li><b>Five Writing Modes Transformation Contract:</b> Strict input/output contracts for <code>Verbatim</code>, <code>Clean</code>, <code>Polished</code>, <code>Structured</code>, and <code>Email</code> modes, backed by an immutable raw transcript preservation guarantee, <code>Win+F1</code> through <code>Win+F5</code> global hotkeys, and real-time floating preview.</li>
  <li><b>Meeting Workspace Contract:</b> A unified 3-section layout (<code>My Thoughts</code> | <code>Transcript</code> | <code>Summary</code>) featuring private scratchpad isolation, clickable timestamp audio sync, 7-state decision evolution tracking, grounded action item matrices, risks/blockers registers, 1-click multi-format follow-up email generation, per-participant checklists, and scoped local AI queries.</li>
  <li><b>Product Information Architecture:</b> Streamlined desktop reorganization into five top-level workspaces (<code>Dictation</code>, <code>Meetings</code>, <code>History</code>, <code>Vocabulary</code>, <code>Settings</code>) with secondary capabilities (Screen Recording, Ask Vox, Quick Switcher) seamlessly integrated without navigational clutter.</li>
  <li><b>Deliverable Templates &amp; Evidence Criteria:</b> Exhaustive templates, required sections, and forensic evidence requirements for all six mandatory project reports.</li>
</ol>

<hr/>

<h2>2. Microsoft Outlook Calendar Integration Specification</h2>

<p>The VOX Calendar Integration delivers automated upcoming meeting detection, acoustic vocabulary priming, and 1-click meeting recording without external bot accounts or cloud transcript storage.</p>

<h3>2.1 Three-Tier Calendar Architecture</h3>

<p>To support air-gapped corporate enterprises, standard office desktop environments, and zero-network automated testing, VOX defines an abstract calendar interface (<code>BaseCalendarProvider</code>) implemented by three discrete providers:</p>

<pre><code>from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

class CalendarProviderType(str, Enum):
    MS_GRAPH = "ms_graph"
    LOCAL_MAPI = "local_mapi"
    MOCK = "mock"

@dataclass
class CalendarEvent:
    event_id: str
    subject: str
    start_time: datetime
    end_time: datetime
    location: str
    organizer_name: str
    organizer_email: str
    attendees: List[Dict[str, str]]  # [{"name": "...", "email": "..."}]
    join_url: Optional[str]
    platform: str  # "teams", "zoom", "meet", "webex", "in_person"
    is_cancelled: bool
    is_recurring: bool
    sensitivity: str  # "normal", "private", "confidential"

class BaseCalendarProvider(ABC):
    @abstractmethod
    def authenticate(self, interactive: bool = True) -> bool:
        """Authenticate or verify valid session token."""
        pass

    @abstractmethod
    def disconnect(self) -> bool:
        """Revoke tokens, delete encrypted credentials, and clear memory."""
        pass

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return True if valid credential or local COM handle is active."""
        pass

    @abstractmethod
    def get_upcoming_events(self, window_minutes: int = 60) -> List[CalendarEvent]:
        """Fetch scheduled events within the specified forward window."""
        pass

    @abstractmethod
    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider type, health, last poll timestamp, and error state."""
        pass
</code></pre>

<h4>Provider 1: Microsoft Graph API Provider (Cloud / Modern Desktop)</h4>
<ul>
  <li><b>OAuth 2.0 PKCE Flow:</b> Uses RFC 7636 Authorization Code with Proof Key for Code Exchange (S256 challenge). Runs a local HTTP loopback server at <code>http://127.0.0.1:18824/oauth/callback</code>.</li>
  <li><b>Authorization Endpoint:</b> <code>https://login.microsoftonline.com/common/oauth2/v2.0/authorize</code></li>
  <li><b>Token Endpoint:</b> <code>https://login.microsoftonline.com/common/oauth2/v2.0/token</code></li>
  <li><b>Query Endpoint:</b>
    <br/><code>GET https://graph.microsoft.com/v1.0/me/calendarView?startDateTime={start_iso}&amp;endDateTime={end_iso}&amp;$select=id,subject,start,end,location,organizer,attendees,onlineMeeting,isCancelled,sensitivity&amp;$top=25</code></li>
  <li><b>Minimal Scopes:</b> Strictly requests <code>Calendars.Read</code> and <code>offline_access</code>. Never requests <code>Mail.Read</code>, <code>Files.Read</code>, or write scopes.</li>
</ul>

<h4>Provider 2: Local Windows Outlook MAPI Provider (Corporate Desktop Fallback)</h4>
<ul>
  <li><b>Zero Cloud Egress:</b> Designed for corporate workstations where tenant policies block third-party Graph API client apps.</li>
  <li><b>Win32 COM Automation:</b> Connects to the local running Outlook process via <code>win32com.client.Dispatch("Outlook.Application")</code>.</li>
  <li><b>Namespace &amp; Folder:</b> <code>GetNamespace("MAPI").GetDefaultFolder(9)</code> (where 9 = <code>olFolderCalendar</code>).</li>
  <li><b>Query Filter:</b> Uses Jet filter: <code>"[Start] &gt;= '{start_str}' AND [End] &lt;= '{end_str}'"</code> with <code>Items.IncludeRecurrences = True</code>.</li>
  <li><b>Attributes Extracted:</b> <code>Subject</code>, <code>Start</code>, <code>End</code>, <code>Location</code>, <code>Organizer</code>, <code>RequiredAttendees</code>, <code>MeetingStatus</code>.</li>
</ul>

<h4>Provider 3: Mock Calendar Provider (Zero-Network Test Fixture)</h4>
<ul>
  <li><b>CI/CD &amp; Unit Test Isolation:</b> Operates completely offline with zero network or COM requirements.</li>
  <li><b>Configurable Event Queues:</b> Injects deterministic events configured via test fixtures (e.g. event starting in 14 minutes, event starting in 4 minutes, cancelled event, past event).</li>
  <li><b>Failure Simulation:</b> Capable of injecting simulated <code>401 Unauthorized</code>, <code>429 RateLimit</code>, <code>500 ServerError</code>, network disconnect timeouts, and malformed JSON payloads to verify error recovery logic.</li>
</ul>

<h3>2.2 Polling Mechanism &amp; Upcoming Meeting Detection State Machine</h3>

<p>The calendar daemon runs inside a dedicated background worker (<code>CalendarMonitorWorker</code>) with a default 60-second polling cadence. It evaluates upcoming events against three alert thresholds:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Alert Threshold</th>
      <th>Trigger Window</th>
      <th>Deduplication Key</th>
      <th>System Action</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>15-Minute Advance</b></td>
      <td>15 &ge; (Start - Now) &gt; 5 min</td>
      <td><code>alert_15m_{event_id}_{start_epoch}</code></td>
      <td>Pre-fetches attendee list; injects attendee names into Faster-Whisper custom vocabulary table for acoustic priming.</td>
    </tr>
    <tr>
      <td><b>5-Minute Imminent</b></td>
      <td>5 &ge; (Start - Now) &gt; 0 min</td>
      <td><code>alert_5m_{event_id}_{start_epoch}</code></td>
      <td>Pops native toast / Qt floating prompt with meeting details and 1-click "Start Recording" button.</td>
    </tr>
    <tr>
      <td><b>Starting Now</b></td>
      <td>0 &ge; (Start - Now) &gt; -2 min</td>
      <td><code>alert_start_{event_id}_{start_epoch}</code></td>
      <td>Updates prompt badge to "Starting Now" / pulses active indicator.</td>
    </tr>
  </tbody>
</table>

<p><b>State Deduplication Invariant:</b> To prevent notification spam, all alerted events are logged in the SQLite table <code>calendar_alerts</code>:</p>
<pre><code>CREATE TABLE IF NOT EXISTS calendar_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,  -- '15min', '5min', 'start'
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dismissed INTEGER DEFAULT 0,
    accepted INTEGER DEFAULT 0,
    UNIQUE(event_id, alert_type)
);
</code></pre>

<h3>2.3 Advance Notifications &amp; 1-Click Meeting Start Workflow</h3>

<ul>
  <li><b>Native UI Surfaces:</b>
    <ul>
      <li><b>Primary:</b> Qt Floating Notification Banner (<code>VoxMeetingPromptPopup</code>) positioned at the bottom-right of the primary screen, styled with theme tokens, glassmorphism, and subtle border glow.</li>
      <li><b>Secondary Fallback:</b> Windows System Tray notification (<code>QSystemTrayIcon.showMessage</code>) when main window or popups are suppressed in presentation mode.</li>
    </ul>
  </li>
  <li><b>Interactive User Actions:</b>
    <ul>
      <li><b>"🎙️ Start Local Capture" (1-Click):</b> Immediately configures VOX meeting recording session:
        <ol>
          <li>Sets meeting title to event subject (e.g. "Weekly Architecture Sync").</li>
          <li>Associates event ID and attendee roster with meeting database record.</li>
          <li>Activates dual-track audio capture (mic + WASAPI loopback).</li>
          <li>Dismisses notification and switches UI status to recording.</li>
        </ol>
      </li>
      <li><b>"Snooze (5 min)":</b> Hides notification and re-arms alert in 5 minutes.</li>
      <li><b>"Dismiss / Ignore":</b> Clears notification and suppresses further alerts for this event instance.</li>
    </ul>
  </li>
</ul>

<h3>2.4 Authentication Lifecycle, Secure Storage, &amp; Error Recovery</h3>

<ul>
  <li><b>Secure Credential Storage (Windows DPAPI):</b>
    <ul>
      <li>OAuth tokens (<code>access_token</code>, <code>refresh_token</code>, <code>expires_at</code>, <code>account_upn</code>) are serialized to JSON and encrypted using the Windows Data Protection API (<code>CryptProtectData</code> via <code>ctypes.windll.crypt32</code>).</li>
      <li>Ciphertext is saved to <code>data/calendar_auth.dat</code>. Zero plain-text tokens ever touch disk or log files.</li>
    </ul>
  </li>
  <li><b>Token Refresh Lifecycle:</b>
    <ul>
      <li>Tokens typically expire after 3600 seconds (1 hour).</li>
      <li>VOX checks token freshness before every polling round. If <code>expires_at - now &lt; 300</code> (5 minutes remaining), it proactively executes a refresh token exchange against the token endpoint.</li>
    </ul>
  </li>
  <li><b>Disconnect &amp; Logout Protocol:</b>
    <ul>
      <li>Explicit user action in Settings &gt; Integrations &gt; "Disconnect Outlook".</li>
      <li>Zeroes memory buffers, deletes <code>data/calendar_auth.dat</code>, purges cached events from memory, and resets UI to "Connect Outlook Calendar".</li>
    </ul>
  </li>
  <li><b>Offline &amp; Error Recovery State Machine:</b>
    <ul>
      <li><b>Network Loss:</b> Polling failure increments backoff exponent (retry at 60s, 120s, 240s, max 300s). Status pill displays "Calendar: Offline (Cached)". Previously cached events remain visible.</li>
      <li><b>Invalid Grant / Refresh Failure (401/400):</b> If refresh fails due to password change or tenant revocation, transition provider status to <code>AUTH_REQUIRED</code>. Surface non-intrusive warning icon in Settings. Never crash or block dictation.</li>
      <li><b>Rate Limiting (429):</b> Parse <code>Retry-After</code> HTTP header; pause polling worker until header duration elapses.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>3. Five Writing Modes Technical Contract</h2>

<p>VOX voice typing provides five distinct transformation modes that adapt spoken speech to different communication contexts while strictly preserving the original raw recognized text.</p>

<h3>3.1 Five Modes Definition &amp; Transformation Rules</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Mode</th>
      <th>Transformation Objective</th>
      <th>Processing Pipeline</th>
      <th>Output Format &amp; Tone</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>1. Verbatim</b></td>
      <td>Zero alteration; exact acoustic transcript.</td>
      <td>Bypasses filler word pruning, spoken punctuation interpretation, and LLM rewriting. Only basic orthography applied.</td>
      <td>Literal recognized words, preserving colloquialisms, repetitions, and spoken punctuation as literal words ("full stop", "comma").</td>
    </tr>
    <tr>
      <td><b>2. Clean</b></td>
      <td>Fluent spoken prose with conversational clutter removed.</td>
      <td>1. Spoken punctuation parsed.<br/>2. Verbal self-corrections resolved.<br/>3. Conversational fillers pruned ("um", "uh", "like", "you know").<br/>4. Capitalization &amp; spacing normalized.</td>
      <td>Clean, natural conversational text representing exactly what the speaker intended without conversational stumbles.</td>
    </tr>
    <tr>
      <td><b>3. Polished</b></td>
      <td>High-articulation executive prose.</td>
      <td>Executes Clean pipeline, then applies local LLM / neural rule restructuring to elevate syntax, eliminate redundancy, and improve vocabulary.</td>
      <td>Professional, publication-ready prose with crisp grammar and parallel sentence structure.</td>
    </tr>
    <tr>
      <td><b>4. Structured</b></td>
      <td>Hierarchical executive outline and bullet points.</td>
      <td>Executes Clean pipeline, analyzes discourse markers ("first", "second", "key takeaway", "action item"), groups concepts into thematic bullets.</td>
      <td>Markdown outline featuring <code>**Summary:**</code>, bulleted points (<code>- ...</code>), and action items (<code>- [ ] ...</code>).</td>
    </tr>
    <tr>
      <td><b>5. Email</b></td>
      <td>Complete, ready-to-send business email.</td>
      <td>Executes Clean pipeline, infers core topic, extracts action points, and structures text into standard business email syntax.</td>
      <td>Standard email syntax: <code>Subject: [Topic]</code>, <code>Hi Team,</code>, structured body paragraphs, <code>Best regards,</code>.</td>
    </tr>
  </tbody>
</table>

<h3>3.2 Transformation Pipeline Input/Output Contract</h3>

<pre><code>@dataclass
class TransformationRequest:
    raw_transcript: str               # Raw acoustic output from Whisper
    mode: str                         # "verbatim" | "clean" | "polished" | "structured" | "email"
    vocabulary: List[Dict[str, Any]]  # Custom user domain terms
    spoken_punctuation: bool = True   # Enable command parsing (ignored in verbatim)
    output_script: str = "auto"       # "auto" | "roman" | "devanagari"
    context_hint: Optional[str] = None # Target application context (e.g. "Slack", "Outlook")

@dataclass
class TransformationResult:
    raw_transcript: str               # Guaranteed immutable copy of original
    transformed_text: str             # Mode-processed result ready for insertion
    mode_applied: str                 # Exact mode executed
    filler_words_pruned: List[str]    # List of removed filler tokens
    self_corrections_applied: int     # Count of verbal corrections resolved
    processing_latency_ms: float      # Execution duration in milliseconds
</code></pre>

<h3>3.3 Raw Transcript Preservation Guarantee</h3>

<div style="background:#F0FDF4; border-left:4px solid #10B981; padding:12px; margin:14px 0;">
  <b>NON-NEGOTIABLE PRESERVATION INVARIANT:</b><br/>
  Under no circumstances may mode transformation mutate or discard the original recognized speech. VOX enforces a strict two-column storage schema in SQLite:
  <br/><code>dictation_history (id, session_id, raw_transcript, transformed_text, active_mode, latency_ms, created_at)</code><br/>
  The user can retroactively re-render any historical dictation into any of the other four modes at any time.
</div>

<h3>3.4 Real-Time Preview UI &amp; Shortcut Switcher</h3>

<ul>
  <li><b>Global Shortcuts Architecture:</b>
    <ul>
      <li><code>Win + F1</code>: Verbatim Mode</li>
      <li><code>Win + F2</code>: Clean Mode</li>
      <li><code>Win + F3</code>: Polished Mode</li>
      <li><code>Win + F4</code>: Structured Mode</li>
      <li><code>Win + F5</code>: Email Mode</li>
      <li><code>Win + Tab</code> (while pill active): Cycle forward through modes.</li>
    </ul>
  </li>
  <li><b>Dual-Tier Floating Pill UI (Ergonomics):</b>
    <ul>
      <li><b>Upper Tier (272px &times; 44px):</b> Cancel button (<code>✕</code>), 12-bar dynamic acoustic equalizer (<code>DynamicWaveformCapsule</code>), monospace session timer (<code>00:00</code>), and stop/finalize coral button (<code>■ #E05638</code>).</li>
      <li><b>Lower Tier (340px &times; 36px):</b> Five interactive mode chips displaying active state, keyboard shortcut hints, and a mode chevron selector (<code>↕</code>).</li>
      <li><b>Live Speech Preview Bubble:</b> Anchored 12px above upper tier. Renders streaming tokens with dynamic crimson red strikethrough styling for retracted self-corrections and pruned conversational fillers:
        <br/><code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;filler/retracted text&lt;/span&gt;</code></li>
      <li><b>Win32 Focus Invariance:</b> Pill and Preview Bubble enforce <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>) with <code>WA_ShowWithoutActivating</code>, ensuring the user's active document or IDE never loses cursor focus.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>4. Meeting Workspace Contract</h2>

<p>The VOX Meeting Workspace delivers an end-to-end, local-first meeting intelligence studio structured around a strict 3-section layout.</p>

<h3>4.1 Strict 3-Section Layout Structure</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Section Name</th>
      <th>Primary Purpose</th>
      <th>Key Features &amp; User Interactions</th>
      <th>Privacy &amp; Data Security Boundary</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>1. "My Thoughts"</b></td>
      <td>Personal private scratchpad for confidential impressions, strategies, and notes during/after calls.</td>
      <td>- Timestamp note marker button (<code>[MM:SS]</code> via <code>Ctrl+M</code>) that anchors thoughts to playback time.<br/>- Auto-save with 500ms debounce.<br/>- Summary inclusion toggle.<br/>- Real-time word and character counter.</td>
      <td><b>Strictly Sequestered:</b> 100% excluded from client MoM exports. External MCP AI queries return <code>403 Forbidden</code> unless user explicitly toggles "Allow External AI (MCP)".</td>
    </tr>
    <tr>
      <td><b>2. "Transcript"</b></td>
      <td>Chronological, speaker-diarized interactive meeting transcript.</td>
      <td>- Full-text search with live match counter and scroll-to-match.<br/>- Clickable timestamp pills that seek synchronized audio playback.<br/>- Inline speaker rename dialog with cascading update across all segments.<br/>- Karaoke-style playback highlight tracking active speech segment.</td>
      <td>Stored locally in SQLite <code>meeting_transcripts</code> table. Zero cloud transmission.</td>
    </tr>
    <tr>
      <td><b>3. "Summary"</b></td>
      <td>Executive MoM, structured decisions, grounded action items, and follow-up deliverables.</td>
      <td>- Executive narrative overview.<br/>- 7-state decision evolution table.<br/>- Grounded action items matrix with owner, due date, and verbatim quote.<br/>- Risks vs Blockers register.<br/>- 1-click follow-up email dialog.<br/>- Per-participant action checklists with Slack copy.</td>
      <td>Synthesized entirely by local LLM engine. Contains zero confidential data from "My Thoughts" unless explicitly authorized.</td>
    </tr>
  </tbody>
</table>

<h3>4.2 7-State Decision Evolution Contract</h3>

<p>Decisions during meetings are rarely static&mdash;they evolve from proposals to tentative agreements, confirmed outcomes, or reversals. VOX models this via a 7-state state machine:</p>

<pre><code>class DecisionStatus(str, Enum):
    PROPOSED = "proposed"          # Floated idea, under discussion
    TENTATIVE = "tentative"        # Provisional consensus, pending confirmation
    CONFIRMED = "confirmed"        # Finalized, agreed by stakeholders
    REJECTED = "rejected"          # Explicitly declined or abandoned
    UNRESOLVED = "unresolved"      # Discussed with conflicting views, postponed
    SUPERSEDED = "superseded"      # Replaced by a subsequent decision in same or later call
    REVERSED = "reversed"          # Prior decision explicitly overturned

class DecisionCategory(str, Enum):
    STRATEGIC = "Strategic"
    TECHNICAL = "Technical"
    PRODUCT = "Product"
    BUDGET = "Budget"
    PROCESS = "Process"
    GENERAL = "General"

@dataclass
class DecisionRecord:
    id: str                        # Deterministic hash: "DEC-001"
    meeting_id: int
    decision: str                  # Clear single-sentence statement of decision
    category: DecisionCategory
    status: DecisionStatus
    evolution_relationship: str    # "new" | "repeated" | "superseded" | "reversed"
    rationale: str                 # Why it was decided
    evidence_quote: str            # Verbatim excerpt from transcript
    timestamp_str: str             # "MM:SS" anchor for playback seek
    confidence_score: float        # Extraction confidence [0.0 - 1.0]
</code></pre>

<h3>4.3 Grounded Action Items &amp; Risks/Blockers Schema</h3>

<pre><code>@dataclass
class GroundedActionItem:
    id: str                        # "ACT-001"
    meeting_id: int
    task_description: str          # Actionable verb-first task
    owner: str                     # Extracted assignee name or "Unassigned"
    due_date_str: str              # Verified calendar target "YYYY-MM-DD" or "TBD"
    uncertainty_reason: Optional[str] # Explanation if relative date was ambiguous
    priority: str                  # "High" | "Medium" | "Low"
    status: str                    # "Open" | "In Progress" | "Blocked" | "Done"
    evidence_quote: str            # Verbatim citation anchor
    timestamp_str: str             # "MM:SS"

@dataclass
class RiskBlockerRecord:
    id: str                        # "RSK-001"
    meeting_id: int
    description: str               # Clear articulation of risk or blocker
    category: str                  # "Technical" | "Timeline" | "Financial" | "Operational"
    is_blocker: bool               # True if currently halting forward progress
    impact_level: str              # "Critical" | "High" | "Medium" | "Low"
    mitigation_strategy: str       # Proposed resolution or contingency
    owner: str                     # Responsible party
</code></pre>

<h3>4.4 One-Click Follow-Up Email &amp; Participant Checklists Contract</h3>

<ul>
  <li><b>Follow-Up Email Generator (<code>VoxFollowUpEmailDialog</code>):</b>
    <ul>
      <li>Produces three synchronized representations:
        <ol>
          <li><b>Rich HTML:</b> Formatted with executive styling, HTML tables for action items, and clean typography for pasting into Outlook / webmail.</li>
          <li><b>Standard Markdown:</b> Formatted for Notion, GitHub Discussions, or plain-text editors.</li>
          <li><b>RFC 2368 <code>mailto:</code> URI:</b> URL-encoded string launching the default Windows mail client with Subject and Body pre-filled.</li>
        </ol>
      </li>
      <li>Dialog offers one-click copy buttons: <code>📋 Copy Markdown</code>, <code>🌐 Copy Rich HTML</code>, <code>✉️ Open in Mail Client</code>.</li>
    </ul>
  </li>
  <li><b>Per-Participant Checklists:</b>
    <ul>
      <li>Groups all action items by owner:
        <pre><code><b>Sarah Connor</b>
- [ ] Finalize PLM schema migration (Due: 2026-11-15)
- [ ] Review CAD assembly constraints (Due: 2026-11-18)

<b>Alex Mercer</b>
- [ ] Benchmark Faster-Whisper INT8 quantization (Due: 2026-11-12)
</code></pre>
      </li>
      <li>Includes <code>📋 Copy Slack Checklists</code> button that generates Slack-compatible formatting with <code>@mentions</code>.</li>
    </ul>
  </li>
</ul>

<h3>4.5 Bilingual Diarization, Language Detection, &amp; Scoped Ask Vox</h3>

<ul>
  <li><b>Dual-Track Physical Diarization:</b>
    <ul>
      <li>Channel 0 (Microphone): Isolated local user audio, labeled <code>You (Mic)</code> (badge color <code>#D97706</code> Amber).</li>
      <li>Channel 1 (WASAPI Loopback): System speaker output containing remote conference attendees, labeled <code>Speaker 1</code>, <code>Speaker 2</code> (badge color <code>#0D9488</code> Teal).</li>
    </ul>
  </li>
  <li><b>Language Identification &amp; Code-Switching:</b>
    <ul>
      <li>Evaluates Whisper language tokens per segment.</li>
      <li>Detects Hindi/English code-switching in Indian accents, tagging segments with <code>[EN]</code>, <code>[HI]</code>, or <code>[HINGLISH]</code> badges.</li>
      <li>Preserves proper nouns and domain terms via custom phonetic vocabulary replacement rules.</li>
    </ul>
  </li>
  <li><b>Scoped In-Report "Ask Vox" Pane:</b>
    <ul>
      <li>Positioned at the base of Section 3 (Summary).</li>
      <li>Strictly bounds context to the active meeting's transcripts, decisions, and action items.</li>
      <li>Generates streaming answers supported by verbatim citations and <code>[MM:SS]</code> seekable links.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>5. Information Architecture &amp; Navigation Contract</h2>

<p>VOX reorganizes its user experience into five primary top-level workspaces, eliminating navigation clutter while maintaining full access to secondary capabilities.</p>

<h3>5.1 Primary Five-Section Product Information Architecture</h3>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>#</th>
      <th>Workspace Name</th>
      <th>Primary Route / Key</th>
      <th>Core Workflows &amp; Controls</th>
      <th>Shortcut</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>1</b></td>
      <td><b>Dictation</b></td>
      <td><code>/dictation</code></td>
      <td>Voice typing control hub, quick transcription testbed, writing mode selector (Verbatim, Clean, Polished, Structured, Email), live microphone VU equalizer, input device selector, floating pill configuration.</td>
      <td><code>Ctrl + 1</code></td>
    </tr>
    <tr>
      <td><b>2</b></td>
      <td><b>Meetings</b></td>
      <td><code>/meetings</code></td>
      <td>Meeting intelligence hub, calendar upcoming schedule banner, active meeting recording trigger, call consent prompt history, unified 3-section meeting workspace (My Thoughts, Transcript, Summary), Advanced Studio mode toggle.</td>
      <td><code>Ctrl + 2</code></td>
    </tr>
    <tr>
      <td><b>3</b></td>
      <td><b>History</b></td>
      <td><code>/history</code></td>
      <td>Searchable meeting library and dictation history, SQLite FTS5 search, date/platform filters, batch export (TXT, Markdown, SRT, VTT, JSON), media playback controls.</td>
      <td><code>Ctrl + 3</code></td>
    </tr>
    <tr>
      <td><b>4</b></td>
      <td><b>Vocabulary</b></td>
      <td><code>/vocabulary</code></td>
      <td>Custom personal and domain dictionary, spoken phrase to replacement mapping, categories (Technical, Acronyms, Names, Brand), active status toggles, import/export CSV/JSON, Whisper prompt injection preview.</td>
      <td><code>Ctrl + 4</code></td>
    </tr>
    <tr>
      <td><b>5</b></td>
      <td><b>Settings</b></td>
      <td><code>/settings</code></td>
      <td>Comprehensive preferences: Global hotkeys recorder, audio device selection, AI model &amp; precision (FP16/INT8), Microsoft Calendar integration &amp; OAuth, MCP server controls &amp; session tokens, UI theme (Dark/Light) &amp; animations, Windows startup.</td>
      <td><code>Ctrl + 5</code> or <code>Ctrl + ,</code></td>
    </tr>
  </tbody>
</table>

<h3>5.2 Secondary Features Integration Architecture</h3>

<p>Rather than cluttering the primary five-section navigation rail, secondary features are integrated contextually:</p>

<ul>
  <li><b>Screen Recording:</b>
    <ul>
      <li>Embedded as an optional recording mode within the <b>Meetings</b> workspace (Audio Only vs Audio + Screen Video).</li>
      <li>Accessible via the global Command Palette (<code>Ctrl + K</code> &gt; "Start Screen Recording").</li>
      <li>Recordings are archived and searchable within the <b>History</b> workspace.</li>
    </ul>
  </li>
  <li><b>Ask Vox (Knowledge Query):</b>
    <ul>
      <li>Embedded as an in-context query strip at the bottom of the <b>Meeting Workspace</b> (scoped to the active meeting).</li>
      <li>Available as a global repository search in the <b>History</b> workspace (scoped across all archived meetings).</li>
      <li>Accessible via Command Palette (<code>Ctrl + K</code> &gt; "Ask Vox: ...").</li>
    </ul>
  </li>
  <li><b>Quick Switcher / Command Palette (<code>Ctrl + K</code>):</b>
    <ul>
      <li>A lightweight floating palette that opens anywhere in the app via <code>Ctrl + K</code>.</li>
      <li>Allows instant keyboard navigation to any workspace, recent meeting, vocabulary entry, or action command ("Record Meeting", "Toggle Dark Mode", "Export Last MoM").</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>6. Deliverable Templates &amp; Evidence Criteria</h2>

<p>To ensure flawless compliance with project governance and audit requirements, this section defines the exact structure, mandatory sections, and evidence criteria for all six required project deliverables.</p>

<h3>6.1 Deliverable 1: VOX_WISPR_FLOW2_ANALYSIS.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_WISPR_FLOW2_ANALYSIS.md</code></li>
  <li><b>Purpose:</b> Authoritative forensic investigation of the updated reference video (<code>Whisper FLow2.mp4</code>) and uploaded reference images.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Reference Video &amp; Interaction Analysis Report (Whisper FLow2)&lt;/h2&gt;
&lt;h2&gt;1. Executive Summary &amp; Forensic Asset Specifications&lt;/h2&gt;
&lt;h2&gt;2. Video Stream Specifications &amp; Technical Metadata&lt;/h2&gt;
&lt;h2&gt;3. Dual-Tier Floating Pill Control &amp; Micro-Interaction Forensics&lt;/h2&gt;
&lt;h2&gt;4. Live Speech Preview &amp; Real-Time Strikethrough Forensics&lt;/h2&gt;
&lt;h2&gt;5. Writing Modes &amp; Text Transformation Pipeline Forensics&lt;/h2&gt;
&lt;h2&gt;6. Bot-Free Meeting Intelligence &amp; 3-Section Workspace Forensics&lt;/h2&gt;
&lt;h2&gt;7. Model Context Protocol (MCP) &amp; Privacy Boundary Forensics&lt;/h2&gt;
&lt;h2&gt;8. Visual Design Tokens, Typography, &amp; Animation Curves&lt;/h2&gt;
&lt;h2&gt;9. Concrete Requirements &amp; Target Specifications for VOX&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Actual PyAV / FFprobe container demuxing data: Duration (seconds), frame count, video codec, audio sample rate, resolution.</li>
      <li>High-density frame timestamp log correlating exact video timestamps (e.g. <code>00:14.2s</code>, <code>00:48.5s</code>) with UI state transitions.</li>
      <li>Pixel measurements of pill dimensions (Upper tier 272&times;44px, Lower tier 340&times;36px, Preview bubble 12px offset).</li>
      <li>Color hex codes extracted from reference frames (Coral red <code>#E05638</code>, Teal <code>#2DD4BF</code>, Strikethrough <code>#D32F2F</code>).</li>
    </ul>
  </li>
</ul>

<h3>6.2 Deliverable 2: VOX_CURRENT_PRODUCT_AUDIT.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_CURRENT_PRODUCT_AUDIT.md</code></li>
  <li><b>Purpose:</b> Comprehensive structural and capability audit of the existing VOX codebase.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Current Product &amp; Codebase Capability Audit&lt;/h2&gt;
&lt;h2&gt;1. Executive Audit Summary &amp; Architectural Baseline&lt;/h2&gt;
&lt;h2&gt;2. Codebase Inventory &amp; Module Architecture Breakdown&lt;/h2&gt;
&lt;h2&gt;3. Core Experience 1: Voice Dictation &amp; Floating UI Audit&lt;/h2&gt;
&lt;h2&gt;4. Core Experience 2: Meeting Intelligence &amp; Workspace Audit&lt;/h2&gt;
&lt;h2&gt;5. External Integrations Audit: Calendar, Call Detection, &amp; MCP&lt;/h2&gt;
&lt;h2&gt;6. Performance, Resource Utilization, &amp; Latency Baselines&lt;/h2&gt;
&lt;h2&gt;7. Windows OS Integration &amp; Packaging Readiness Audit&lt;/h2&gt;
&lt;h2&gt;8. Code Quality, Test Coverage, &amp; Defect Catalog&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Accurate line counts and class/method inventories across all <code>src/voxlocal/</code> packages.</li>
      <li>Direct file and line number citations for working vs broken vs missing functionality.</li>
      <li>Empirical baseline performance metrics cited from <code>baseline_results.txt</code> (startup time, inference latency, idle CPU/RAM).</li>
      <li>Unit and integration test pass/fail audit across the 52 test files in <code>tests/</code>.</li>
    </ul>
  </li>
</ul>

<h3>6.3 Deliverable 3: VOX_PRODUCT_GAP_ANALYSIS.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_PRODUCT_GAP_ANALYSIS.md</code></li>
  <li><b>Purpose:</b> Definitive gap analysis comparing Wispr Flow reference capabilities, current VOX capabilities, and target requirements.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Product Gap Analysis &amp; Remediation Matrix&lt;/h2&gt;
&lt;h2&gt;1. Executive Summary &amp; Strategic Gap Assessment&lt;/h2&gt;
&lt;h2&gt;2. Side-by-Side Feature Gap Matrix (Wispr Flow vs. Current VOX vs. Target VOX)&lt;/h2&gt;
&lt;h2&gt;3. Deep Dive Gap 1: Voice Dictation &amp; 5 Writing Modes Delta&lt;/h2&gt;
&lt;h2&gt;4. Deep Dive Gap 2: Microsoft Outlook Calendar Integration Delta&lt;/h2&gt;
&lt;h2&gt;5. Deep Dive Gap 3: Meeting Workspace &amp; Follow-Up Automation Delta&lt;/h2&gt;
&lt;h2&gt;6. Deep Dive Gap 4: Product Information Architecture &amp; UI Refinement Delta&lt;/h2&gt;
&lt;h2&gt;7. Prioritized Implementation Backlog &amp; Risk Register&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Comprehensive feature gap table classifying each requirement as <code>FULL</code>, <code>PARTIAL</code>, <code>MISSING</code>, or <code>REGRESSED</code>.</li>
      <li>Specific technical root cause analysis for each gap with architectural impact.</li>
      <li>Implementation complexity and risk scores (Low, Medium, High, Critical) with mitigations.</li>
    </ul>
  </li>
</ul>

<h3>6.4 Deliverable 4: VOX_NEW_PRODUCT_UX_SPEC.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_NEW_PRODUCT_UX_SPEC.md</code></li>
  <li><b>Purpose:</b> Authoritative UI/UX, interaction, design token, and layout specification for the evolved VOX application.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Desktop Application — UX Specification &amp; Design System&lt;/h2&gt;
&lt;h2&gt;1. UX Philosophy &amp; Core Interaction Principles&lt;/h2&gt;
&lt;h2&gt;2. Design System: Tokens, Typography, Colors, &amp; Motion Curves&lt;/h2&gt;
&lt;h2&gt;3. Information Architecture &amp; Five Primary Workspaces&lt;/h2&gt;
&lt;h2&gt;4. Dual-Tier Floating Recording Pill &amp; Live Preview Bubble Specification&lt;/h2&gt;
&lt;h2&gt;5. Meeting Studio 3-Section Workspace Specification&lt;/h2&gt;
&lt;h2&gt;6. Calendar Integration Notifications &amp; Prompt UX Specification&lt;/h2&gt;
&lt;h2&gt;7. Global Shortcut Architecture &amp; Accessibility Specification&lt;/h2&gt;
&lt;h2&gt;8. Wireframes, Layout Grids, &amp; Component Schematics&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Exact CSS / QSS styling rules and color palette tokens for Dark and Light themes.</li>
      <li>Complete ASCII / tabular wireframes for all 5 workspaces and floating widgets.</li>
      <li>State transition diagrams for floating pill, calendar prompt, and meeting studio.</li>
      <li>Comprehensive shortcut binding table with conflict resolution rules.</li>
    </ul>
  </li>
</ul>

<h3>6.5 Deliverable 5: VOX_IMPLEMENTATION_CHANGELOG.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_IMPLEMENTATION_CHANGELOG.md</code></li>
  <li><b>Purpose:</b> Chronological, file-by-file engineering changelog documenting all changes made during the evolution milestone.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Implementation Changelog &amp; Architecture Decision Records&lt;/h2&gt;
&lt;h2&gt;1. Milestone Overview &amp; Execution Summary&lt;/h2&gt;
&lt;h2&gt;2. Architecture Decision Records (ADRs)&lt;/h2&gt;
&lt;h2&gt;3. File-by-File Code Modifications &amp; Additions&lt;/h2&gt;
&lt;h2&gt;4. Component Implementation: Calendar Integration&lt;/h2&gt;
&lt;h2&gt;5. Component Implementation: Five Writing Modes Pipeline&lt;/h2&gt;
&lt;h2&gt;6. Component Implementation: Meeting Studio 3-Section Workspace&lt;/h2&gt;
&lt;h2&gt;7. Component Implementation: Information Architecture &amp; UI Redesign&lt;/h2&gt;
&lt;h2&gt;8. Database Migrations &amp; Schema Updates&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Exact file paths and line ranges for every created or modified file.</li>
      <li>Formal Architecture Decision Records (Context, Decision, Consequences, Status).</li>
      <li>Git diff or verbatim code snippets highlighting non-obvious engineering solutions.</li>
    </ul>
  </li>
</ul>

<h3>6.6 Deliverable 6: VOX_FINAL_VALIDATION_REPORT.md</h3>
<ul>
  <li><b>Target Path:</b> <code>d:\Milind\Voice Assistant\VOX_FINAL_VALIDATION_REPORT.md</code></li>
  <li><b>Purpose:</b> Independent QA, test execution, benchmark verification, and release sign-off report.</li>
  <li><b>Mandatory HTML Section Structure:</b>
    <pre><code>&lt;h2&gt;VOX Final Release Validation &amp; Quality Assurance Report&lt;/h2&gt;
&lt;h2&gt;1. Executive Release Readiness &amp; Certification Summary&lt;/h2&gt;
&lt;h2&gt;2. Acceptance Criteria Verification Matrix&lt;/h2&gt;
&lt;h2&gt;3. Automated Test Suite Execution Results&lt;/h2&gt;
&lt;h2&gt;4. Performance Benchmarks vs. Baseline Results&lt;/h2&gt;
&lt;h2&gt;5. End-to-End User Journey Verification&lt;/h2&gt;
&lt;h2&gt;6. Security, Privacy, &amp; MCP Quarantine Verification&lt;/h2&gt;
&lt;h2&gt;7. Windows Executable Build &amp; Packaging Verification&lt;/h2&gt;
&lt;h2&gt;8. Defect Resolution, Known Limitations, &amp; Sign-off&lt;/h2&gt;
</code></pre>
  </li>
  <li><b>Required Evidence Criteria:</b>
    <ul>
      <li>Verbatim terminal execution logs of all test suites (unit, integration, E2E).</li>
      <li>Side-by-side performance benchmark table comparing target metrics with <code>baseline_results.txt</code>.</li>
      <li>Verification of standalone executable build (<code>dist\VoxLocal\VoxLocal.exe</code>) with file size, checksum, and clean launch.</li>
      <li>Evidence of screenshot / UI capture validation.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>7. Features Discovered Table</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>#</th>
      <th>Category</th>
      <th>Feature</th>
      <th>Description</th>
      <th>Inputs</th>
      <th>Outputs</th>
      <th>Error Behavior</th>
      <th>Discovered Via</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td>Calendar</td>
      <td>MS Graph OAuth 2.0 PKCE</td>
      <td>Minimal-scope (<code>Calendars.Read</code>) OAuth flow with local loopback callback.</td>
      <td>User consent, tenant ID, client ID.</td>
      <td>DPAPI-encrypted access/refresh tokens.</td>
      <td>Network retry with exponential backoff; fallback to local MAPI or mock.</td>
      <td>ORIGINAL_REQUEST.md &amp; VOX_INTEGRATION_SECURITY_SPEC.md</td>
    </tr>
    <tr>
      <td>2</td>
      <td>Calendar</td>
      <td>Local MAPI COM Provider</td>
      <td>Zero-network local Outlook calendar extraction via Win32 COM dispatch.</td>
      <td>Local running Outlook MAPI session.</td>
      <td>Normalized <code>CalendarEvent</code> objects.</td>
      <td>COM error captured; degrades gracefully to empty list or mock.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
    <tr>
      <td>3</td>
      <td>Calendar</td>
      <td>Mock Calendar Provider</td>
      <td>Deterministic offline calendar fixture for zero-network automated testing.</td>
      <td>Test event fixtures / error injection flags.</td>
      <td>Synthesized calendar events or simulated HTTP/COM exceptions.</td>
      <td>Deterministic error propagation as configured.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
    <tr>
      <td>4</td>
      <td>Calendar</td>
      <td>Upcoming Meeting Polling</td>
      <td>Background daemon polling events at 15-min and 5-min advance thresholds.</td>
      <td>System clock, calendar events.</td>
      <td>Acoustic priming trigger (15m), Toast alert trigger (5m).</td>
      <td>Deduplication table prevents duplicate alerts; silent catch on network drop.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
    <tr>
      <td>5</td>
      <td>Calendar</td>
      <td>1-Click Meeting Capture</td>
      <td>Notification banner allowing single click to prime vocabulary and start recording.</td>
      <td>User click on notification banner.</td>
      <td>Configured meeting session with auto-populated title and attendee priming.</td>
      <td>Ignores on timeout; fail-closed zero audio buffer retention.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
    <tr>
      <td>6</td>
      <td>Dictation</td>
      <td>Five Writing Modes</td>
      <td>Verbatim, Clean, Polished, Structured, Email transformation pipeline.</td>
      <td>Raw recognized speech text, mode identifier.</td>
      <td>Transformed text with mode-specific formatting.</td>
      <td>Fallback to Clean mode on unexpected error; raw transcript preserved.</td>
      <td>ORIGINAL_REQUEST.md &amp; dictation_processor.py</td>
    </tr>
    <tr>
      <td>7</td>
      <td>Dictation</td>
      <td>Raw Transcript Invariance</td>
      <td>Guaranteed persistent storage of immutable raw acoustic transcript.</td>
      <td>Whisper raw tokens.</td>
      <td>Persisted raw_transcript in SQLite database.</td>
      <td>Write failure logged; in-memory buffer held until storage recovered.</td>
      <td>ORIGINAL_REQUEST.md &amp; database.py</td>
    </tr>
    <tr>
      <td>8</td>
      <td>Dictation</td>
      <td>Global Mode Shortcuts</td>
      <td><code>Win+F1</code> through <code>Win+F5</code> hotkeys for instantaneous mode switching.</td>
      <td>Keyboard hook events.</td>
      <td>Active mode state updated, lower pill chip highlights refreshed.</td>
      <td>Conflict check skips reserved system shortcuts.</td>
      <td>ORIGINAL_REQUEST.md &amp; floating_pill.py</td>
    </tr>
    <tr>
      <td>9</td>
      <td>Dictation</td>
      <td>Real-Time Preview Bubble</td>
      <td>Floating card above pill rendering live tokens with crimson strikethrough.</td>
      <td>Streaming speech tokens from transcriber.</td>
      <td>Rich HTML preview with filler/strikethrough styling.</td>
      <td>Silently resets on dictation cancellation or commit.</td>
      <td>ORIGINAL_REQUEST.md &amp; floating_pill.py</td>
    </tr>
    <tr>
      <td>10</td>
      <td>Workspace</td>
      <td>Strict 3-Section Layout</td>
      <td>Consolidated Meeting Studio featuring My Thoughts, Transcript, and Summary.</td>
      <td>Meeting session data.</td>
      <td>Rendered 3-section workspace UI.</td>
      <td>Graceful empty-state rendering for new meetings.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_studio.py</td>
    </tr>
    <tr>
      <td>11</td>
      <td>Workspace</td>
      <td>Private Scratchpad Isolation</td>
      <td>Tab 1 "My Thoughts" strictly isolated from client exports and external MCP AI.</td>
      <td>User text notes, timestamp marks.</td>
      <td>Auto-saved confidential notes.</td>
      <td>Client exports redact thoughts; MCP returns 403 Forbidden by default.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_studio.py</td>
    </tr>
    <tr>
      <td>12</td>
      <td>Workspace</td>
      <td>Interactive Transcript Sync</td>
      <td>Speaker-diarized transcript with playback sync, search, and inline renaming.</td>
      <td>Audio stream, user search query, speaker renames.</td>
      <td>Synchronized media playback, filtered segments, renamed speakers.</td>
      <td>Search returns zero matches if query unfound; preserves segment data.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_studio.py</td>
    </tr>
    <tr>
      <td>13</td>
      <td>Workspace</td>
      <td>7-State Decision Tracker</td>
      <td>Structured tracking of decisions: proposed, tentative, confirmed, rejected, etc.</td>
      <td>Extracted decision records from local LLM.</td>
      <td>Interactive table with status badges and transcript citations.</td>
      <td>Fallback to extractive pattern matching if LLM unavailable.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_intelligence.py</td>
    </tr>
    <tr>
      <td>14</td>
      <td>Workspace</td>
      <td>1-Click Follow-Up Email</td>
      <td>Generates executive follow-up email in Rich HTML, Markdown, and RFC 2368 mailto.</td>
      <td>Meeting summary and action items.</td>
      <td>Modal preview with Copy HTML, Copy Markdown, Open Mail Client.</td>
      <td>Fallback template generated if local LLM fails.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_studio.py</td>
    </tr>
    <tr>
      <td>15</td>
      <td>Workspace</td>
      <td>Per-Participant Checklists</td>
      <td>Groups action items by owner with Slack-ready @mention formatting.</td>
      <td>Action item records.</td>
      <td>Markdown checkboxes and Slack copy-ready payload.</td>
      <td>Unassigned items grouped under "Unassigned" section.</td>
      <td>ORIGINAL_REQUEST.md &amp; meeting_studio.py</td>
    </tr>
    <tr>
      <td>16</td>
      <td>Architecture</td>
      <td>5-Section Navigation Rail</td>
      <td>Reorganized desktop UI: Dictation, Meetings, History, Vocabulary, Settings.</td>
      <td>Navigation clicks or <code>Ctrl+1</code>..<code>5</code> shortcuts.</td>
      <td>Active workspace stack transition.</td>
      <td>State preserved across workspace switches.</td>
      <td>ORIGINAL_REQUEST.md &amp; main_window.py</td>
    </tr>
    <tr>
      <td>17</td>
      <td>Architecture</td>
      <td>Secondary Features Integration</td>
      <td>Screen recording, Ask Vox, and Command Palette integrated without top-level clutter.</td>
      <td>Contextual triggers or <code>Ctrl+K</code> command palette.</td>
      <td>Contextual drawer, inline panel, or modal workflow.</td>
      <td>Dismissed cleanly via Escape key.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
    <tr>
      <td>18</td>
      <td>Deliverables</td>
      <td>6 Comprehensive Deliverables</td>
      <td>Structured markdown reports with zero markdown #/* headers and full test evidence.</td>
      <td>Forensic video data, codebase audit, test execution, benchmarks.</td>
      <td>6 durable specification and audit documents.</td>
      <td>Strictly verified by compliance linters and QA scripts.</td>
      <td>ORIGINAL_REQUEST.md</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>8. Edge Cases &amp; Boundary Behaviors</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>#</th>
      <th>Feature</th>
      <th>Edge Case Input / Scenario</th>
      <th>Observed / Specified Behavior</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td>
      <td>Calendar OAuth</td>
      <td>User cancels or closes browser during OAuth 2.0 PKCE loopback redirect.</td>
      <td>Loopback server times out after 120 seconds; surfaces friendly error in Settings; zero corrupt tokens saved.</td>
    </tr>
    <tr>
      <td>2</td>
      <td>Calendar Polling</td>
      <td>Meeting time changes in Outlook while 15-min advance notification is pending.</td>
      <td>Next polling cycle updates event start time in SQLite cache; recalibrates or suppresses invalid alert.</td>
    </tr>
    <tr>
      <td>3</td>
      <td>Calendar MAPI</td>
      <td>Outlook desktop is installed but not running when MAPI provider initializes.</td>
      <td>MAPI provider catches COM connection error (<code>0x800401E3</code>); cleanly defers without freezing UI thread.</td>
    </tr>
    <tr>
      <td>4</td>
      <td>Writing Modes</td>
      <td>User speaks a 10-minute uninterrupted rambling narrative in Email mode.</td>
      <td>Processor extracts core intent without truncation; synthesizes concise executive body paragraphs with clean sign-off.</td>
    </tr>
    <tr>
      <td>5</td>
      <td>Writing Modes</td>
      <td>User dictates Hindi-English code-switched technical terms in Verbatim mode.</td>
      <td>Preserves exact spoken phonetics and Devanagari/Roman words without forced English translation.</td>
    </tr>
    <tr>
      <td>6</td>
      <td>Mode Shortcuts</td>
      <td>User triggers <code>Win+F1</code> while focus is inside an active full-screen video game or remote desktop.</td>
      <td>Low-level keyboard hook catches key combination; switches mode state; floating pill updates without stealing focus.</td>
    </tr>
    <tr>
      <td>7</td>
      <td>My Thoughts</td>
      <td>User creates a private thought note containing confidential client pricing during an active call.</td>
      <td>Note is saved in <code>my_thoughts</code> table; Client MoM export algorithm strictly redacts text; MCP server blocks tool query.</td>
    </tr>
    <tr>
      <td>8</td>
      <td>Transcript Sync</td>
      <td>User clicks timestamp marker <code>[14:22]</code> when media file has not finished offline transcription.</td>
      <td>Sync handler verifies media duration; if playback file exists, seeks audio to 862.0s; otherwise displays non-blocking toast.</td>
    </tr>
    <tr>
      <td>9</td>
      <td>Action Items</td>
      <td>Spoken action item mentions ambiguous relative date ("let's wrap this up next Wednesday").</td>
      <td>Engine resolves date against anchor meeting timestamp; populates <code>due_date_str</code> with verified target; logs uncertainty reason if ambiguous.</td>
    </tr>
    <tr>
      <td>10</td>
      <td>Follow-up Email</td>
      <td>Meeting has zero identified decisions and zero action items (informal chat).</td>
      <td>Email generator gracefully structures conversational overview without rendering empty tables or broken bullet lists.</td>
    </tr>
    <tr>
      <td>11</td>
      <td>Header Compliance</td>
      <td>Worker generates report with markdown <code># Header</code> or <code>* Header</code>.</td>
      <td>Fails compliance validation immediately; pre-commit regex check rejects file until converted to HTML tags.</td>
    </tr>
    <tr>
      <td>12</td>
      <td>Screen Recording</td>
      <td>User minimizes meeting window while dual audio + video recording is active.</td>
      <td>Video worker switches immediately to neutral privacy placeholder frame; wall-clock PTS is preserved to maintain A/V lip-sync.</td>
    </tr>
  </tbody>
</table>

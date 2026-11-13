<h2>VOX Desktop Productivity Assistant — Comprehensive Target Architecture</h2>

<p><b>Document Identifier:</b> VOX-ARCH-SPEC-2026-V2<br/>
<b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Document Purpose:</b> Authoritative system architecture, data flow diagrams, pipeline engineering, database schema, and threading model unifying all four product pillars (Voice Intelligence, Screen Intelligence, Meeting Intelligence, VOX Brain).<br/>
<b>Reference Architecture:</b> Local-First Enterprise Assistant, Zero-Bot Native Desktop Integration, ADR-001 through ADR-004 Compliant.<br/>
<b>Formatting Mandate:</b> Strict HTML header compliance (zero markdown <code>#</code> or <code>*</code> headers across all sections).</p>

<hr/>

<h2>1. Executive Architectural Blueprint &amp; System Principles</h2>

<p>VOX is engineered as a <b>100% local-first, zero-cloud-dependent desktop intelligence ecosystem</b> for Windows 10 and 11. It executes end-to-end multi-modal capture, neural acoustic transcription, local language model inference, and cross-application text injection entirely on the client workstation.</p>

<h3>1.1 The Capture &rarr; Understand &rarr; Create &rarr; Execute Core Data Pipeline</h3>

<pre>
+===================================================================================================+
|                                    CAPTURE (Ambient Ingestion)                                    |
|  +---------------------------+  +-------------------------------+  +---------------------------+  |
|  | Windows Audio APIs        |  | Display Capture               |  | Win32 Telemetry &amp; Focus  |  |
|  | - Microphone WASAPI       |  | - Window Bounded Capture      |  | - Foreground HWND Tracker |  |
|  | - System Loopback WASAPI  |  | - Screen Snipping &amp; Canvas   |  | - Active App Window Title |  |
|  | - Pre-Buffer Ring (450ms) |  | - Hardware Cursor + Ripples   |  | - Audio Session Registry  |  |
|  +---------------------------+  +-------------------------------+  +---------------------------+  |
+==================================================+================================================+
                                                   | Raw Audio Frames / Window Context / Video Frames
                                                   v
+===================================================================================================+
|                                UNDERSTAND (Local Neural Processing)                               |
|  +--------------------------------+  +---------------------------------------------------------+  |
|  | Acoustic Speech-to-Text        |  | Semantic Comprehension &amp; Meeting Intelligence           |  |
|  | - Streaming Silero VAD (160ms) |  | - Local Quantized LLM (Llama-3.2-1B-Instruct int8)      |  |
|  | - Faster-Whisper Turbo int8    |  | - 7-State Grounded Decision Evolution Engine            |  |
|  | - Hinglish &amp; Devanagari Norm   |  | - Deterministic Relative Date Arithmetic Parser         |  |
|  | - Real-Time Disfluency Gating  |  | - Quarantined Session RAG ("Ask Vox" Vector Search)    |  |
|  +--------------------------------+  +---------------------------------------------------------+  |
+==================================================+================================================+
                                                   | Structured Tokens / Grounded Entities / Minutes
                                                   v
+===================================================================================================+
|                                   CREATE (Artifact Synthesis)                                     |
|  +--------------------------------+  +---------------------------------------------------------+  |
|  | Voice Typing Transformations   |  | Structured Meeting Deliverables                         |  |
|  | - Verbatim / Cleaned Stream    |  | - Executive Meeting Minutes &amp; Topic Summaries           |  |
|  | - Magic Mode (Win+F1 Rewrite)  |  | - Grounded Action Items Matrix with Transcripts Quotes  |  |
|  | - Structured Mode (Win+F2)     |  | - Risks vs Blockers Register &amp; Participant Checklists   |  |
|  | - Email Mode (Win+F3 Draft)    |  | - 1-Click Executive Follow-Up Email Synthesis           |  |
|  +--------------------------------+  +---------------------------------------------------------+  |
+==================================================+================================================+
                                                   | Polished Text / Formatted Documents / Emails
                                                   v
+===================================================================================================+
|                                  EXECUTE (Frictionless Delivery)                                  |
|  +--------------------------------+  +---------------------------------------------------------+  |
|  | Active Window Text Injection   |  | Export &amp; Inter-Process Delivery                         |  |
|  | - Win32 SendInput (UNICODE)    |  | - SQLite WAL Storage (data/voxlocal.db)                 |  |
|  | - Direct WM_CHAR Fallback      |  | - Fragmented MP4 Video Storage (frag_keyframe)          |  |
|  | - Atomic Clipboard Paste       |  | - 1-Click Mailto / Rich HTML Dispatch                   |  |
|  | - Modifier Key Safety Release  |  | - Permission-Controlled Local MCP Loopback Server       |  |
|  +--------------------------------+  +---------------------------------------------------------+  |
+===================================================================================================+
</pre>

<hr/>

<h2>2. Audio Ingestion &amp; Hardware Separation Pipeline</h2>

<p>The audio architecture is the foundation for both zero-latency voice typing and bot-free meeting intelligence. Capturing clean, uncorrupted audio without feedback loops requires physical hardware separation.</p>

<h3>2.1 Hardware-Separated Dual-Track WASAPI Architecture</h3>
<p>VOX interfaces directly with the Windows Core Audio APIs (Windows Audio Session API &mdash; WASAPI) using COM-based low-latency audio clients in shared mode:</p>

<ul>
  <li><b>Microphone Stream (Capture Endpoint):</b>
    <ul>
      <li>Captures the user's vocal narration directly from the active physical microphone (USB headset, built-in array, studio mic).</li>
      <li>Sample Rate: 48,000 Hz or 44,100 Hz native, resampled in real-time via high-quality polyphase sinc interpolation to <b>16,000 Hz 16-bit Mono PCM</b> (Whisper native input format).</li>
      <li>Dedicated Ring Buffer: Circular lock-free audio buffer with continuous <b>450ms acoustic pre-buffering</b>, guaranteeing that sudden voice onsets never lose initial consonants.</li>
    </ul>
  </li>
  <li><b>System Render Loopback Stream (Loopback Endpoint):</b>
    <ul>
      <li>Captures remote participants' voices from video calls (Teams, Zoom, Meet, Slack Huddles) via WASAPI loopback on the default audio output render endpoint.</li>
      <li>Operates strictly in loopback mode (<code>AUDCLNT_STREAMFLAGS_LOOPBACK</code>), pulling decoded audio frames after application mixing without needing virtual audio cable drivers.</li>
    </ul>
  </li>
  <li><b>Acoustic Pre-Conditioning DSP Chain:</b>
    <ul>
      <li><b>DC Blocking Filter:</b> High-pass filter (-3 dB cutoff at 35 Hz) eliminating hardware DC bias.</li>
      <li><b>Mains Hum Notch Filter:</b> Ultra-narrow notch filter tuned to 50 Hz (Europe/Asia) and 60 Hz (Americas) providing &gt; 12 dB hum attenuation.</li>
      <li><b>Stationary Fan Noise Gating:</b> Spectral subtraction filter attenuating constant PC fan or air-conditioning hum by -14 dB without vocal distortion.</li>
      <li><b>True-Peak Limiter:</b> Soft-knee limiter clamping sample amplitudes to <code>|sample| &le; 0.9801</code> to prevent digital clipping before neural inference.</li>
    </ul>
  </li>
  <li><b>Audio Persistence &amp; Mixing:</b>
    <ul>
      <li>Raw tracks are persisted independently to the meeting session directory: <code>mic_track.wav</code> and <code>system_track.wav</code>.</li>
      <li>An auto-gain-normalized mix is generated asynchronously: <code>meeting_mixed.wav</code>, ensuring uniform playback levels regardless of remote participant volume differences.</li>
    </ul>
  </li>
</ul>

<hr/>

<h2>3. Speech-to-Text &amp; Voice Intelligence Pipeline</h2>

<p>The speech-to-text pipeline converts continuous raw PCM audio into semantically structured, punctuation-normalized, and code-switched text with sub-220ms latency.</p>

<h3>3.1 Model Architecture &amp; Quantization</h3>
<ul>
  <li><b>Inference Engine:</b> <code>Faster-Whisper</code> (CTranslate2 execution engine) utilizing the <b>Whisper Large-v3-Turbo</b> architecture.</li>
  <li><b>Quantization:</b>
    <ul>
      <li><b>NVIDIA GPU (CUDA):</b> Quantized to <code>float16</code> or <code>int8_float16</code>, achieving 8.2&times; real-time decoding speed with ~1.8 GB VRAM allocation.</li>
      <li><b>CPU Fallback (SIMD AVX2/AVX-512):</b> Quantized to <code>int8</code>, utilizing multi-threaded CPU execution (4-8 worker threads) with &lt; 480 MB RAM footprint.</li>
    </ul>
  </li>
</ul>

<h3>3.2 Streaming Voice Activity Detection (VAD) &amp; Speculative Decoding</h3>
<ul>
  <li><b>Silero VAD v4:</b> Evaluates 30ms / 160ms audio chunks with ultra-low latency (&lt; 2ms compute per chunk). Maintains a rolling speech probability metric.</li>
  <li><b>Utterance Segmentation:</b>
    <ul>
      <li>Speech onset trigger: 3 consecutive frames with speech probability &gt; 0.65.</li>
      <li>Speech pause boundary: 800ms - 1200ms of sustained silence triggers utterance finalization in hands-free mode.</li>
    </ul>
  </li>
  <li><b>Provisional Streaming Tokens:</b> Emits provisional hypothesis tokens every 240ms to feed the floating speech preview bubble, giving the user immediate visual confirmation of speech recognition.</li>
</ul>

<h3>3.3 Multilingual Recognition &amp; Indian English / Hinglish Engine</h3>
<p>VOX features a dedicated multilingual linguistic post-processing engine (<code>DictationProcessor</code> in <code>src/voxlocal/engine/dictation_processor.py</code>):</p>

<ul>
  <li><b>Hinglish Code-Switching Detection:</b> Dynamically detects transitions between Hindi and English vocabulary.</li>
  <li><b>Devanagari to Roman Hinglish (<code>hi_latn</code>):</b> Implements rule-based and phonetic transliteration with Hindi schwa-deletion rules (e.g., spoken <i>"karna hai"</i> transcribed as Roman script rather than Devanagari <i>"करना है"</i> when user selects Latin script preference).</li>
  <li><b>Devanagari Orthographic Repair:</b> Corrects common Whisper Devanagari spelling confusions (e.g., converts <i>"टाएप"</i> to <i>"टाइप"</i>, <i>"चाहीए"</i> to <i>"चाहिए"</i>, <i>"अफिज"</i> to <i>"ऑफिस"</i>).</li>
  <li><b>Technical Loanword Restoration:</b> Contains a dictionary of &gt; 250 common IT, corporate, and software loanwords spoken in Hindi conversations, restoring them to proper English orthography (e.g., restoring <i>"स्क्रीन शेयर"</i> to <i>"screen share"</i>, <i>"बग फिक्स"</i> to <i>"bug fix"</i>, <i>"पीआर रिव्यू"</i> to <i>"PR review"</i>).</li>
</ul>

<h3>3.4 Active Window Context Conditioning</h3>
<ul>
  <li>Before decoding an utterance, VOX samples the active foreground window title and executable path (e.g., <code>"Jira - PROJ-4019: Refactor Auth Service - Google Chrome"</code>).</li>
  <li>Extracts salient domain tokens (acronyms, tickets, project names) and injects them into Whisper's <code>initial_prompt</code> parameter.</li>
  <li>This primes the acoustic decoder to favor domain-specific terminology (e.g., "DRHP", "Zepto", "Kubernetes", "OAuth") over common dictionary words without hallucination loops.</li>
</ul>

<h3>3.5 Sub-200ms Active Window Text Insertion Engine</h3>
<p>Delivering transcribed text into target applications without stealing caret focus or triggering UIPI privilege errors is achieved via a 3-tier cascade (<code>TextInserter</code> in <code>src/voxlocal/win32/text_inserter.py</code>):</p>

<ol>
  <li><b>Tier 1: Win32 SendInput (Unicode):</b>
    <ul>
      <li>Constructs an array of <code>INPUT</code> structures with <code>INPUT_KEYBOARD</code> and <code>KEYEVENTF_UNICODE</code>.</li>
      <li>Emits UTF-16 code units directly into the foreground window's input thread queue. Text appears at the active cursor position in native Windows apps, Word, Notepad, and web browsers in &lt; 15ms.</li>
    </ul>
  </li>
  <li><b>Tier 2: Direct Win32 WM_CHAR Message Target:</b>
    <ul>
      <li>If <code>SendInput</code> is blocked due to User Interface Privilege Isolation (UIPI) or elevated administrator windows, VOX resolves the focused child HWND via <code>GetGUIThreadInfo()</code> and delivers characters via direct <code>WM_CHAR</code> and <code>WM_KEYDOWN</code> messages.</li>
    </ul>
  </li>
  <li><b>Tier 3: Atomic Safe Clipboard Paste Fallback:</b>
    <ul>
      <li>For Electron applications (Microsoft Teams, Slack, VS Code, Discord) or complex web rich-text editors where rapid character sequences may occasionally suffer token re-ordering:</li>
      <li>VOX snapshots the existing clipboard contents (formats: text, HTML, image, files).</li>
      <li>Writes transcribed text to the clipboard, issues programmatic <code>Ctrl+V</code> (key-down/key-up), verifies paste event consumption, and restores the user's prior clipboard content within 15ms.</li>
    </ul>
  </li>
  <li><b>Modifier Key Protection:</b> Programmatically detects and releases any held modifier keys (Ctrl, Alt, Shift, Win) prior to emission to prevent unintended execution of target application shortcuts.</li>
</ol>

<hr/>

<h2>4. Intelligence Engine &amp; Meeting Analysis Pipeline</h2>

<p>The intelligence engine (<code>MeetingIntelligence</code> in <code>src/voxlocal/engine/meeting_intelligence.py</code>) processes multi-party conversational transcripts into structured business deliverables using local quantized neural models and deterministic mathematical algorithms.</p>

<h3>4.1 Local LLM Inference Engine</h3>
<ul>
  <li><b>Local Foundation Model:</b> <b>Llama-3.2-1B-Instruct int8</b> (or 3B-Instruct where VRAM allows), executed offline via <code>llama.cpp</code> / <code>CTransformers</code> / <code>ONNX Runtime</code>.</li>
  <li><b>Memory Allocation:</b> Static 380 MB RAM footprint, execution latency &lt; 850ms for meeting summaries.</li>
  <li><b>Deterministic Prompts:</b> Enforces zero temperature (<code>temperature = 0.0</code>) and strict JSON schema constrained decoding, guaranteeing reproducible, hallucination-free outputs.</li>
</ul>

<h3>4.2 Grounded Decisions &amp; 7-State Evolution Tracking (ADR-001)</h3>
<p>Decisions are extracted and tracked across meetings using an orthogonal two-dimensional state space:</p>

<ul>
  <li><b>Business Status:</b>
    <ul>
      <li><code>proposed</code>: Mentioned as an option or recommendation without unanimous agreement.</li>
      <li><code>confirmed</code>: Explicitly agreed upon by meeting participants.</li>
      <li><code>rejected</code>: Explicitly discarded or vetoed during discussion.</li>
      <li><code>unresolved</code>: Discussed with conflicting viewpoints and deferred to a future session.</li>
    </ul>
  </li>
  <li><b>Evolutionary Relationship:</b>
    <ul>
      <li><code>new</code>: Introduced for the first time in this meeting.</li>
      <li><code>repeated</code>: Reaffirmed from a prior meeting without modification.</li>
      <li><code>superseded</code>: Replaces or modifies a prior meeting's decision (requires valid <code>supersedes_id</code> foreign key).</li>
      <li><code>reversed</code>: Overturns or cancels a decision confirmed in a prior meeting.</li>
    </ul>
  </li>
  <li><b>Relational Lineage (ADR-001):</b> Protected in the SQLite database via foreign key constraints:
    <br/><code>supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT</code>
    <br/>Ensures an unalterable, fully traceable audit trail across complex multi-meeting projects.</li>
</ul>

<h3>4.3 Action Item Extraction with Verifiable Citations &amp; Date Arithmetic (ADR-003)</h3>
<ul>
  <li><b>Mandatory Transcript Grounding:</b> Every extracted action item is mathematically required to possess:
    <ul>
      <li><code>task_description</code>: Specific action to be performed.</li>
      <li><code>owner</code>: Assigned person or role.</li>
      <li><code>evidence_quote</code>: Exact verbatim quote from transcript.</li>
      <li><code>timestamp_sec</code>: Precise second in recording where assignment occurred.</li>
    </ul>
  </li>
  <li><b>Deterministic Relative Date Parsing (ADR-003):</b>
    <ul>
      <li>Relative temporal expressions ("by Friday", "next Tuesday", "in two weeks", "by end of month") are calculated deterministically using the meeting's creation timestamp as the absolute reference anchor.</li>
      <li>Outputs standard ISO 8601 calendar strings: <code>YYYY-MM-DD</code>.</li>
      <li>Ambiguous temporal phrases ("sometime soon", "next sprint", "eventually") are strictly resolved to <code>due_date = None</code> with <code>resolution_status = "ambiguous"</code> and an explanatory <code>uncertainty_reason</code>, eliminating hallucinated deadlines.</li>
    </ul>
  </li>
  <li><b>User-Edit Preservation (ADR-002):</b> Re-running AI finalization or regenerating summaries preserves all user edits, manual additions, and checked completion states without data loss.</li>
</ul>

<h3>4.4 Quarantined Session RAG ("Ask Vox" Copilot) (ADR-004)</h3>
<ul>
  <li><b>Quarantine Boundary:</b> In-report questions submitted to the Ask Vox pane enforce strict meeting-level filtering:
    <br/><code>WHERE meeting_id = :active_meeting_id</code>
  </li>
  <li><b>Search Algorithm:</b> Hybrid search combining SQLite FTS5 BM25 lexical ranking and local dense vector embeddings (e.g., <code>all-MiniLM-L6-v2</code> quantized).</li>
  <li><b>Leakage Prevention:</b> Candidate chunks from any other meeting are physically impossible to retrieve, guaranteeing zero data leakage between confidential client accounts.</li>
</ul>

<hr/>

<h2>5. Relational Database Schema &amp; Storage Architecture</h2>

<p>VOX utilizes a high-performance, robust relational SQLite database (<code>data/voxlocal.db</code>) operating in Write-Ahead Logging (WAL) mode.</p>

<h3>5.1 WAL Configuration &amp; Integrity Primitives</h3>
<ul>
  <li><code>PRAGMA journal_mode = WAL;</code> &mdash; Enables concurrent readers while writing without table locks.</li>
  <li><code>PRAGMA synchronous = NORMAL;</code> &mdash; Balances high write throughput with power-loss safety.</li>
  <li><code>PRAGMA foreign_keys = ON;</code> &mdash; Enforces relational integrity and prevents orphaned records.</li>
  <li><code>PRAGMA busy_timeout = 5000;</code> &mdash; Prevents database lock contention across background worker threads.</li>
  <li><b>Automated Snapshot Verification:</b> <code>create_verified_snapshot()</code> generates consistent online backups verified via <code>PRAGMA integrity_check</code> before committing major migrations.</li>
</ul>

<h3>5.2 Relational Tables &amp; Entity Relationships (DDL)</h3>

<pre>
-- 1. Meetings Table
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'manual', -- 'teams', 'zoom', 'meet', 'slack', 'manual'
    session_uid TEXT UNIQUE NOT NULL,       -- Stable session identifier
    status TEXT NOT NULL DEFAULT 'active',   -- 'active', 'completed', 'archived'
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    duration_seconds REAL DEFAULT 0.0,
    has_video INTEGER NOT NULL DEFAULT 0,
    video_path TEXT,
    audio_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Transcripts Table
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker_tag TEXT NOT NULL,              -- 'You (Local)', 'Speaker 1', 'Sarah Jenkins'
    speaker_channel INTEGER NOT NULL,       -- 0: Microphone, 1: Loopback System Audio
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    text_content TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Decisions Table (ADR-001 Foreign Key Lineage)
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    decision_text TEXT NOT NULL,
    business_status TEXT NOT NULL,          -- 'proposed', 'confirmed', 'rejected', 'unresolved'
    evolution_status TEXT NOT NULL,         -- 'new', 'repeated', 'superseded', 'reversed'
    evidence_quote TEXT NOT NULL,
    timestamp_sec REAL,
    speaker_tag TEXT,
    supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Action Items Table (ADR-002 &amp; ADR-003 Grounding)
CREATE TABLE IF NOT EXISTS action_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    task_description TEXT NOT NULL,
    owner TEXT NOT NULL,
    due_date TEXT,                          -- ISO 'YYYY-MM-DD' or NULL
    resolution_status TEXT NOT NULL,        -- 'resolved', 'ambiguous', 'none'
    uncertainty_reason TEXT,
    evidence_quote TEXT NOT NULL,
    timestamp_sec REAL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    user_modified INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Risks &amp; Blockers Table
CREATE TABLE IF NOT EXISTS risks_blockers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,                -- 'risk', 'blocker'
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',-- 'low', 'medium', 'high', 'critical'
    owner TEXT,
    mitigation_strategy TEXT,
    evidence_quote TEXT,
    timestamp_sec REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. My Thoughts Table (Tab 1 Personal Scratchpad)
CREATE TABLE IF NOT EXISTS my_thoughts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    raw_markdown TEXT NOT NULL DEFAULT '',
    include_in_summary INTEGER NOT NULL DEFAULT 0,
    allow_external_mcp INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Personal &amp; Domain Vocabulary Table
CREATE TABLE IF NOT EXISTS vocab_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spoken_phrase TEXT NOT NULL UNIQUE,     -- Spoken phonetic input (e.g. 'zepto')
    formatted_text TEXT NOT NULL,           -- Output format (e.g. 'Zepto')
    category TEXT DEFAULT 'general',        -- 'brand', 'acronym', 'person', 'tech'
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. FTS5 Virtual Tables for Full-Text Search
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    text_content,
    speaker_tag,
    content='transcripts',
    content_rowid='id'
);
</pre>

<hr/>

<h2>6. Process, Concurrency, &amp; Threading Model</h2>

<p>To deliver stutter-free UI rendering at 60 FPS while concurrently capturing 1080p video, decoding audio streams, and executing neural inference, VOX employs a <b>multi-threaded, asynchronous architecture</b> using PyQt6 and Python's native threading primitives:</p>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Thread / Worker Name</th>
      <th>Execution Technology</th>
      <th>Priority / Scheduling</th>
      <th>Responsibilities &amp; Invariants</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Main GUI Thread</b></td>
      <td>PyQt6 <code>QApplication</code> Event Loop</td>
      <td>Normal Interactive (GUI)</td>
      <td>Renders UI widgets, handles mouse/keyboard events, updates floating pill at 30 FPS, coordinates window drag-and-drop. Strictly prohibited from performing file I/O, database queries, or inference.</td>
    </tr>
    <tr>
      <td><b>WASAPI Capture Thread</b></td>
      <td>Native <code>threading.Thread</code> (C-level COM loop)</td>
      <td>Time-Critical (<code>THREAD_PRIORITY_TIME_CRITICAL</code>)</td>
      <td>Polls WASAPI mic and loopback audio client event buffers. Pushes raw audio frames into lock-free ring buffers. Must never block or allocate memory dynamically.</td>
    </tr>
    <tr>
      <td><b>Whisper Inference Worker</b></td>
      <td>PyQt6 <code>QThread</code> + CTranslate2 Worker</td>
      <td>Above Normal</td>
      <td>Consumes audio chunks from ring buffer. Executes Silero VAD, acoustic feature extraction, and Faster-Whisper decoding. Emits Qt signals (<code>transcription_chunk_ready</code>) back to GUI.</td>
    </tr>
    <tr>
      <td><b>Meeting Intelligence Worker</b></td>
      <td><code>QThreadPool</code> Task (Concurrent Worker)</td>
      <td>Normal Background</td>
      <td>Executes local LLM inference, extractive summarization, decision evolution matching, and relative date parsing. Writes results to SQLite database via dedicated thread-local connections.</td>
    </tr>
    <tr>
      <td><b>Screen Video Capture Worker</b></td>
      <td>Native <code>threading.Thread</code> (DXGI / PyAV)</td>
      <td>High Priority</td>
      <td>Grabs desktop/window surfaces at 30 FPS, renders cursor ripples, and muxes video frames into fragmented MP4 containers via PyAV / FFmpeg.</td>
    </tr>
    <tr>
      <td><b>Win32 Hotkey Hook Thread</b></td>
      <td>Win32 <code>GetMessage()</code> Message Pump</td>
      <td>Highest Priority</td>
      <td>Listens to <code>WH_KEYBOARD_LL</code> low-level keyboard hooks. Emits immediate hotkey signals (PTT down/up, mode switches) to the main controller without latency.</td>
    </tr>
    <tr>
      <td><b>Call Detector Worker</b></td>
      <td>Periodic <code>QTimer</code> / Background Poller</td>
      <td>Low Priority (1.0s interval)</td>
      <td>Monitors Windows audio session registry and top-level HWND titles. Detects Teams, Zoom, and Meet call states without consuming CPU.</td>
    </tr>
    <tr>
      <td><b>Local MCP Server Thread</b></td>
      <td>Asyncio Event Loop (Python <code>asyncio</code>)</td>
      <td>Background Low</td>
      <td>Serves Model Context Protocol endpoints over local loopback stdio/SSE for authorized external AI assistants (ChatGPT, Claude).</td>
    </tr>
  </tbody>
</table>

<h3>6.1 Thread Synchronization &amp; Inter-Process Safety</h3>
<ul>
  <li><b>Qt Signal-Slot Decoupling:</b> All cross-thread communications between background workers and GUI components use Qt's thread-safe queued connections (<code>Qt.ConnectionType.QueuedConnection</code>).</li>
  <li><b>Lock-Free Audio Ring Buffers:</b> Audio frames pass from the WASAPI capture thread to transcribers via pre-allocated, single-producer single-consumer circular buffers, avoiding mutex deadlocks.</li>
  <li><b>Thread-Local SQLite Connections:</b> Every worker thread instantiates its own dedicated SQLite connection handle with <code>check_same_thread = False</code> and explicit transaction management, preventing database handle collisions.</li>
</ul>

<hr/>

<h2>7. Forensic Architectural Acceptance Criteria</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Verification ID</th>
      <th>Architectural Domain</th>
      <th>Engineering Benchmark</th>
      <th>Verification Method</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>ARCH-VER-01</b></td>
      <td>Audio Dual-Track Separation</td>
      <td>Mic and loopback audio persist to isolated tracks with 0 cross-talk bleeding.</td>
      <td>Dual-track acoustic energy ratio test in <code>test_phase9_audio_video_quality.py</code>.</td>
    </tr>
    <tr>
      <td><b>ARCH-VER-02</b></td>
      <td>Active Window Text Latency</td>
      <td>Key release to text insertion completed in &lt; 220ms on CUDA hardware.</td>
      <td>High-resolution QElapsedTimer latency benchmark.</td>
    </tr>
    <tr>
      <td><b>ARCH-VER-03</b></td>
      <td>Foreign Key Lineage (ADR-001)</td>
      <td>Deleting a parent decision referenced by <code>supersedes_id</code> raises <code>sqlite3.IntegrityError</code>.</td>
      <td>Relational foreign key integrity test in <code>test_phase10_local_ai_and_meeting_intelligence.py</code>.</td>
    </tr>
    <tr>
      <td><b>ARCH-VER-04</b></td>
      <td>Date Arithmetic (ADR-003)</td>
      <td>Relative dates resolve deterministically to ISO strings; ambiguous dates resolve to <code>due_date=None</code>.</td>
      <td>Deterministic temporal parsing test battery.</td>
    </tr>
    <tr>
      <td><b>ARCH-VER-05</b></td>
      <td>Meeting RAG Quarantine (ADR-004)</td>
      <td>Ask Vox search queries return 0 results from foreign meetings.</td>
      <td>Cross-meeting isolation validation test.</td>
    </tr>
    <tr>
      <td><b>ARCH-VER-06</b></td>
      <td>Thread Safety &amp; UI Fluidity</td>
      <td>GUI maintains &ge; 30 FPS while Whisper and LLM inference execute simultaneously.</td>
      <td>QTest event loop latency benchmark.</td>
    </tr>
  </tbody>
</table>

<h2>VOX Desktop Productivity Assistant — Comprehensive Product Requirements Document (PRD)</h2>

<p><b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Product Vision:</b> The premier, local-first intelligent desktop productivity assistant combining zero-click voice typing, high-efficiency screen capture, native meeting intelligence, and the VOX Brain into a unified desktop workspace.<br/>
<b>Product Evolution Paradigm:</b> <i>Capture → Understand → Create → Execute</i><br/>
<b>Reference Benchmarks:</b> Wispr Flow Reference Experience, Letterly Desktop Ergonomics, VOX Enterprise Release Candidate Core<br/>
<b>Document Version:</b> 2.0.0-PROD-SPEC<br/>
<b>Classification:</b> Confidential — Engineering & Product Architecture Specification<br/>
<b>Formatting Invariant:</b> Strict HTML heading compliance (zero markdown # or * headers across all sections).</p>

<hr/>

<h2>1. Executive Summary &amp; Strategic Foundation</h2>

<h3>1.1 Mission &amp; Purpose</h3>
<p>VOX evolves from a local dictation and meeting utility into an indispensable desktop companion that transforms human spoken thoughts, visual screen workflows, and multi-party conversations into actionable business execution. By unifying four foundational pillars—<b>Voice Intelligence</b>, <b>Screen Intelligence</b>, <b>Meeting Intelligence</b>, and <b>VOX Brain</b>—VOX eliminates keyboard friction, manual note-taking, and context switching without ever compromising user privacy.</p>

<h3>1.2 The Core Productivity Loop: Capture → Understand → Create → Execute</h3>
<ul>
  <li><b>Capture:</b> Frictionless, ambient ingestion of user input across speech (global push-to-talk microphone stream), visual screen context (window-bounded or full desktop capture), and dual-channel system audio (WASAPI loopback meeting audio).</li>
  <li><b>Understand:</b> 100% offline neural comprehension powered by local quantized models (Whisper Large-v3-Turbo int8 and Llama-3.2-1B-Instruct int8). VOX decodes multilingual speech (English, Hindi, Hinglish), normalizes accents, filters verbal disfluencies, tracks multi-speaker conversational turns, and isolates semantic intent.</li>
  <li><b>Create:</b> Intelligent synthesis of structured artifacts directly from captured context: verbatim transcripts, polished prose, executive meeting minutes, follow-up emails, participant action checklists, and decision evolution registers.</li>
  <li><b>Execute:</b> Immediate, frictionless delivery: low-latency text injection into the active cursor position across all Windows desktop and web applications, one-click email client dispatch, Slack action broadcasts, and local clipboard workflows.</li>
</ul>

<h3>1.3 Non-Negotiable Architectural Guarantees</h3>
<ul>
  <li><b>Zero-Bot Meeting Attendance:</b> VOX never deploys an intrusive, external bot account into Microsoft Teams, Zoom, Google Meet, Slack Huddles, or Webex. Meetings record quietly and natively on the local PC via Windows audio APIs, preserving natural meeting dynamics and complying with strict corporate security policies.</li>
  <li><b>100% Local-First &amp; Zero Cloud Telemetry:</b> All audio processing, speech-to-text inference, speaker diarization, extractive summarization, screen recording, and SQLite storage execute strictly on the local machine. Zero audio frames, keystrokes, screen pixels, or transcripts are transmitted to external cloud servers.</li>
  <li><b>Zero-Hallucination &amp; Evidence Grounding:</b> Every generated outcome (summary, action item, decision, follow-up email, participant checklist) must be mathematically anchored to verifiable transcript citations (<code>evidence_quote</code>, timestamp, speaker). VOX strictly prohibits synthetic deadline fabrication or ungrounded commitments.</li>
  <li><b>Privacy-First Consent &amp; Active App Isolation:</b> Meeting screen recording strictly locks onto the verified meeting stage window; general desktop background applications (spreadsheets, emails, chats) are excluded. Cross-app data sharing and MCP tool execution require explicit user preview and consent.</li>
</ul>

<hr/>

<h2>2. Pillar 1: Voice Intelligence (Voice Typing)</h2>

<h3>2.1 Push-to-Talk &amp; Hands-Free Continuous Streaming</h3>
<p>VOX Voice Intelligence eliminates typing fatigue by converting spoken words into clean text injected directly into the user's active cursor position.</p>
<ul>
  <li><b>Global Push-to-Talk (PTT):</b> The user holds down a designated hotkey (default: <code>Fn</code>, <code>Ctrl+Win</code>, or <code>F8</code>) while speaking. Recording begins immediately on key-down; releasing the key instantly flushes speech buffers, finalizes decoding, and injects text. Target latency from key release to cursor injection: <b>&lt; 220 ms</b>.</li>
  <li><b>Hands-Free Continuous Toggle Mode:</b> The user activates voice typing once via shortcut (default: <code>Ctrl+Shift+Space</code> or <code>Win+Alt+V</code>) or floating pill click. VOX listens continuously, using adaptive Voice Activity Detection (VAD) to segment utterances naturally. When the user pauses for longer than the configurable threshold (default: <code>0.8s - 1.2s</code>), VOX finalizes and types the segment without requiring a key release.</li>
  <li><b>Instant Stop-and-Type Flush:</b> Clicking the Stop control or toggling the hotkey during active speech flushes all pending audio frames through the transcriber, guaranteeing 0% dropped words at utterance termination.</li>
  <li><b>Acoustic Pre-Buffering:</b> A continuous 450ms ring buffer prevents clipping of initial plosive and fricative consonants (e.g., "P", "T", "S", "B") during sudden speech onset.</li>
</ul>

<h3>2.2 Live Streaming Overlay &amp; Non-Activating Visual Feedback</h3>
<ul>
  <li><b>Real-Time Streaming Bubble:</b> As the user speaks, a lightweight floating transcript bubble appears adjacent to the text cursor or floating pill, displaying real-time provisional hypothesis tokens (with grayed-out prefix and bold active token) so the user sees instantaneous speculative feedback.</li>
  <li><b>Win32 Non-Activating Window (WS_EX_NOACTIVATE):</b> The floating overlay and pill control utilize Win32 extended styles <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code>. User clicks on overlays never steal focus or activate keyboard caret transfer from the target application (Notepad, Word, Teams, Chrome, VS Code).</li>
  <li><b>Dynamic Waveform &amp; Level Indicator:</b> Real-time dynamic audio waveform bars render speech modulation and mic activity at 30 FPS, providing immediate visual confirmation of audio capture.</li>
</ul>

<h3>2.3 Sub-200ms Active Window Text Injection</h3>
<ul>
  <li><b>Unicode SendInput Engine:</b> Text is injected into the active foreground window via native Win32 <code>SendInput</code> utilizing <code>KEYEVENTF_UNICODE</code>. Characters are delivered directly into the target control's input queue without altering the Windows system clipboard.</li>
  <li><b>Direct Win32 WM_CHAR Target Fallback:</b> If <code>SendInput</code> is blocked due to User Interface Privilege Isolation (UIPI), elevated administrator windows, or remote desktop hooks, VOX automatically discovers the focused child control HWND and delivers characters via direct <code>WM_CHAR</code> and <code>WM_KEYDOWN</code> messages.</li>
  <li><b>Atomic Safe Clipboard Injection:</b> For Electron-based applications (Microsoft Teams, Slack, VS Code, Discord) or complex rich-text web editors where character-by-character input may suffer re-ordering, VOX provides an atomic clipboard paste mechanism. The engine backs up the current clipboard format, writes the transcribed text, issues <code>Ctrl+V</code>, waits for paste completion, and atomically restores the user's prior clipboard content within 15ms.</li>
  <li><b>Modifier Key Protection:</b> Before injecting any keystroke or paste sequence, VOX detects and programmatically releases held modifier keys (Ctrl, Alt, Shift, Win), eliminating accidental trigger of target application keyboard shortcuts.</li>
</ul>

<h3>2.4 Multilingual Recognition &amp; Code-Switching</h3>
<ul>
  <li><b>Supported Languages:</b> English (US, UK, Global), Hindi (Devanagari script), Indian English, and conversational Hinglish code-switching.</li>
  <li><b>Native Hinglish Code-Switching:</b> Recognizes seamless, natural mid-sentence transitions between Hindi and English (e.g., <i>"Kal subah client ke saath meeting schedule kar do and make sure presentation is ready."</i>).</li>
  <li><b>Devanagari to Roman Hinglish Mode (<code>hi_latn</code>):</b> Provides a user-selectable toggle to output conversational Hinglish in the Latin/Roman script rather than Devanagari, utilizing linguistic schwa deletion and natural phonetic mapping (e.g., <i>"karna hai"</i> instead of <i>"करना है"</i>).</li>
  <li><b>Devanagari Orthographic Normalization:</b> Automatically corrects common Whisper Devanagari spelling anomalies (e.g., converts <i>"टाएप"</i> to <i>"टाइप"</i>, <i>"चाहीए"</i> to <i>"चाहिए"</i>, <i>"अफिज"</i> to <i>"ऑफिस"</i>).</li>
  <li><b>English Technical Loanword Restoration:</b> Recognizes over 250 common technology, business, and corporate loanwords spoken in Hindi sentences and restores them into standardized English orthography (e.g., restores <i>"स्क्रीन शेयर"</i> to <i>"screen share"</i>, <i>"बग फिक्स"</i> to <i>"bug fix"</i>, <i>"माइक्रोसॉफ्ट टीम्स"</i> to <i>"Microsoft Teams"</i>).</li>
</ul>

<h3>2.5 Indian English Accent Normalization &amp; Phonetic Repair</h3>
<ul>
  <li><b>Acoustic Accent Tuning:</b> Integrated acoustic rules tuned for Indian English phonetic variations (e.g., retroflex consonants, v/w interchange, aspirated plosives).</li>
  <li><b>Idiomatic Expression Mapping:</b> Accurately transcribes Indian corporate idioms and technical terminology without corruption (e.g., "prepone", "do the needful", "revert back", "loop in", "touch base", "PR review", "standup call").</li>
</ul>

<h3>2.6 Contextual Active-Window Prompt Conditioning &amp; Domain Adaptation</h3>
<ul>
  <li><b>Window-Aware Decoding Bias:</b> VOX samples the active window title and process name (e.g., <code>"Jira - PROJ-1042: Refactor Auth Service"</code>, <code>"Windchill PLM - Part 90214"</code>, <code>"HubSpot Deals"</code>) and extracts salient technical keywords.</li>
  <li><b>Whisper Conditioning Injection:</b> Extracted domain keywords are injected into Whisper's conditioning context without injecting conversational hallucination loops, priming the decoder to favor specialized acronyms, product names, and client codes.</li>
</ul>

<h3>2.7 Personal &amp; Domain Custom Vocabulary Engine</h3>
<ul>
  <li><b>User-Configurable Vocabulary Table:</b> Persistent SQLite dictionary allowing users to map spoken phonetic phrases to exact formatted text (e.g., spoken "zepto" → "Zepto", spoken "drhp" → "DRHP", spoken "my zoom" → "https://zoom.us/j/my-room").</li>
  <li><b>Regex &amp; Word-Boundary Matching:</b> Replacements are matched with strict word boundaries and case insensitivity to prevent unintended substring corruption.</li>
  <li><b>Dynamic Priority Ordering:</b> Multi-word phrases are evaluated prior to single-word tokens to ensure greedy, longest-match precision.</li>
</ul>

<h3>2.8 Intelligent Formatting &amp; Spoken Punctuation</h3>
<ul>
  <li><b>Spoken Punctuation Commands:</b> Supports verbal punctuation commands: "period", "full stop", "comma", "question mark", "exclamation point", "new line", "new paragraph", "open quote", "close quote", "colon", "semicolon".</li>
  <li><b>Mid-Sentence Pause Capitalization Avoidance:</b> When speech pauses mid-sentence without terminal punctuation, VOX suppresses initial-letter capitalization on the subsequent segment, ensuring seamless sentence flow.</li>
  <li><b>Numeric, Currency &amp; Math Entity Formatting:</b> Automatically formats spoken numbers into appropriate representations:
    <ul>
      <li>Currency: "fifty dollars" → "$50", "five thousand rupees" → "₹5,000", "twenty euros" → "€20".</li>
      <li>Percentages: "ninety nine point nine percent" → "99.9%".</li>
      <li>Versions: "version three point eight" → "v3.8".</li>
      <li>Math: "ten plus five equals fifteen" → "10 + 5 = 15".</li>
    </ul>
  </li>
</ul>

<h3>2.9 Verbal Filler Filtering &amp; Noise Rejection</h3>
<ul>
  <li><b>Filler Word Removal:</b> Automatically detects and strips conversational filler sounds: "um", "uh", "erm", "er", "hmm", "like", "you know" (when used as filler disfluencies).</li>
  <li><b>Transient Noise Gating:</b> Energy thresholding rejects coughs, desk taps, keyboard clicks, and throat clearing, requiring at least 180ms of sustained voiced speech to trigger an utterance.</li>
  <li><b>Whisper Silence Hallucination Filter:</b> Suppresses known Whisper hallucination strings emitted during background silence (e.g., "Thank you for watching", "Subtitles by Amara.org", "You").</li>
</ul>

<h3>2.10 Operational Transformation Modes: Verbatim vs Cleaned vs Specialized Formats</h3>
<ul>
  <li><b>Verbatim Mode:</b> Outputs exact spoken speech word-for-word, including colloquialisms, repetitions, and spoken punctuation, ideal for legal, medical, or literal transcription.</li>
  <li><b>Cleaned Mode (Default):</b> Eliminates filler words, corrects false starts, applies intelligent capitalization, and normalizes spacing.</li>
  <li><b>Magic Mode (Win+F1):</b> Automatically restructures rambling speech into concise, well-phrased prose while preserving 100% of the speaker's original intent.</li>
  <li><b>Structured Mode (Win+F2):</b> Converts complex explanations into formatted markdown bullet points, numbered lists, or bolded key points.</li>
  <li><b>Email Mode (Win+F3):</b> Formats spoken dictation into an executive email draft with appropriate greeting, body paragraphs, and professional sign-off.</li>
</ul>

<h3>2.11 Real-Time Streaming Self-Correction &amp; Mid-Utterance Rewriting</h3>
<ul>
  <li><b>Verbal Correction Detection:</b> Recognizes verbal pivot cues: "no actually...", "wait sorry...", "scratch that...", "I mean...".</li>
  <li><b>Mid-Utterance Buffer Rewriting:</b> When a pivot cue is detected, the engine strips the preceding flawed thought and replaces it with the revised clause before final text is injected (e.g., <i>"evaluate the DRHP of Zepto... no actually, first let's do the fundamental analysis"</i> → outputs <i>"First, let's do the fundamental analysis of Zepto's DRHP"</i>).</li>
  <li><b>Spoken Undo Commands:</b> Verbal command "delete that" or "undo that" triggers automatic backspace emission corresponding exactly to the character length of the last injected segment.</li>
</ul>

<h3>2.12 User-Editable Output &amp; Focus Management</h3>
<ul>
  <li><b>Optional Review Bar:</b> When enabled in settings, transcribed text is held in a compact floating review chip for 1.5s, allowing instant user editing or cancellation before automatic insertion.</li>
  <li><b>Zero Caret Stealing:</b> Throughout all states (listening, processing, displaying previews), the active cursor position in the target application remains undisturbed.</li>
</ul>

<h3>2.13 Error Recovery &amp; Hardware Resilience</h3>
<ul>
  <li><b>Audio Device Hot-Plugging:</b> Detects sudden disconnection of USB or Bluetooth headsets and seamlessly re-routes capture to the system default microphone without crashing the speech pipeline.</li>
  <li><b>Bluetooth Hands-Free Profile (HFP) Conflict Guard:</b> Detects when Windows drops audio fidelity due to Bluetooth bidirectional profile conflicts and alerts the user with an actionable remedy.</li>
</ul>

<hr/>

<h2>3. Pillar 2: Screen Intelligence (Screen Capture)</h2>

<h3>3.1 Multi-Mode Display, Window, &amp; Region Capture</h3>
<p>VOX Screen Intelligence provides reliable, high-performance visual capture tailored for professional presentations, code reviews, bug demonstrations, and meeting records.</p>
<ul>
  <li><b>Full Desktop Screen Recording:</b> Records entire desktop monitors across single and multi-display setups at 1080p, up to native refresh rate.</li>
  <li><b>Target Window Bounded Capture:</b> Restricts capture coordinates strictly to a selected application window (e.g., presentation slides, browser window, design canvas).</li>
  <li><b>Interactive Region Snipping:</b> Crosshair selection tool allowing users to snip and record specific rectangular screen regions.</li>
</ul>

<h3>3.2 Strict Call-Only Meeting Window Isolation &amp; Privacy Guard</h3>
<ul>
  <li><b>Source-Bound Meeting Isolation:</b> When recording a meeting, VOX binds exclusively to the verified meeting call stage window (Teams, Zoom, Meet). It strictly prohibits falling back to capturing the full desktop (<code>GetDC(0)</code>).</li>
  <li><b>Background Surface Exclusion:</b> If the user switches focus to personal or confidential applications (Outlook, WhatsApp, banking portal, spreadsheet), zero frames of those external windows are captured in the meeting recording.</li>
  <li><b>Privacy Fail-Closed Guard:</b> If the meeting window is minimized, obscured, or closed, the video capture pipeline fails closed, substituting a neutral status frame (<i>"Meeting window minimized — Privacy Guard Active"</i>) with identical wall-clock Presentation Time Stamps (PTS), guaranteeing that audio-video synchronization remains mathematically intact.</li>
</ul>

<h3>3.3 Hardware-Synchronized Audio-Video Recording &amp; AAC Remuxing</h3>
<ul>
  <li><b>Lip-Sync Accuracy:</b> Audio and video streams maintain strict wall-clock timecode synchronization. Audio-to-video drift is guaranteed <b>&lt; 66.6 ms</b> (less than two video frames at 30 FPS).</li>
  <li><b>Dual-Channel Audio Inclusion:</b> Recorded videos combine both the user's high-fidelity microphone narration and system audio playback (remote speakers, video clips, system chimes) via high-efficiency AAC encoding.</li>
</ul>

<h3>3.4 Cursor Dynamics, Click Ripples &amp; Interaction Visualizers</h3>
<ul>
  <li><b>Crisp Hardware Cursor Capture:</b> Accurately captures the native Windows mouse cursor shape, text carets, crosshairs, and resize handles via Win32 <code>DrawIconEx</code>.</li>
  <li><b>Click Ripple Animations:</b> Renders distinct, elegant animated ripple effects on mouse clicks: Champagne Gold for left-click, Sapphire Blue for right-click.</li>
  <li><b>Directional Scroll Indicators:</b> Displays subtle directional chevron indicators during scroll wheel events (Up, Down, Left, Right).</li>
</ul>

<h3>3.5 Dynamic Framerate Scaling &amp; Ultra-Low Bitrate H.264</h3>
<ul>
  <li><b>Framerate Presets:</b> Supports configurable framerates from 5 FPS (ultra-compact meeting presentation slides, ~10 MB/hour) up to 60/120/native FPS (high-motion video and gaming demos).</li>
  <li><b>High-Efficiency H.264 Encoding:</b> Utilizes PyAV / FFmpeg with optimized constant rate factor (CRF) and fast preset, balancing crystal-clear text legibility with minimal storage consumption.</li>
</ul>

<h3>3.6 Live Snip &amp; Markup Annotation Studio</h3>
<ul>
  <li><b>On-Screen Annotation Tools:</b> Allows freehand drawing, arrows, rectangular callouts, highlighter strokes, text labels, and step counters directly over the live recording or frozen snapshot.</li>
  <li><b>Instant Clipboard Snip:</b> Quick shortcut (<code>Ctrl+Shift+S</code>) to snip a region, annotate, and copy directly to clipboard.</li>
</ul>

<h3>3.7 Floating Recording Controls &amp; State Indicators</h3>
<ul>
  <li><b>Compact Floating Bar:</b> Minimal always-on-top pill displaying elapsed recording timer, pause/resume button, stop button, and live snip tool.</li>
  <li><b>Subtle Screen Border Indicator:</b> Displays a discreet, ultra-thin glowing border around the active recording boundary to ensure user awareness.</li>
</ul>

<h3>3.8 Interrupted Recording Recovery &amp; Container Resilience</h3>
<ul>
  <li><b>Fragmented MP4 Streaming:</b> Video writes use fragmented MP4 muxing (<code>frag_keyframe+empty_moov</code>), ensuring that in the event of an abrupt power loss or application termination, the video file up to the exact moment of failure remains 100% readable and playable without moov atom corruption.</li>
</ul>

<hr/>

<h2>4. Pillar 3: Meeting Intelligence (Meeting Studio)</h2>

<h3>4.1 Native Desktop Meeting Detection Without External Bots</h3>
<p>VOX delivers comprehensive meeting intelligence without ever deploying a third-party bot account into the participant roster.</p>
<ul>
  <li><b>Supported Platforms:</b> Microsoft Teams (Desktop &amp; Web), Zoom (Desktop &amp; Web), Google Meet (Chrome, Edge, Brave), Webex, Slack Huddles, Discord, Skype, and WhatsApp Desktop.</li>
  <li><b>Layered Detection Engine:</b> Combines Windows <code>CapabilityAccessManager\ConsentStore\microphone</code> registry telemetry (detecting when an application opens a live microphone handle) with Win32 window classification (differentiating meeting call stages from chat windows).</li>
  <li><b>Zero Bot Awkwardness:</b> Operates completely client-side on the user's workstation. External attendees see no bot in the participant list.</li>
</ul>

<h3>4.2 Privacy-First 1-Click Explicit Consent Workflow</h3>
<ul>
  <li><b>Non-Intrusive Floating Prompt:</b> When an active call is detected, VOX surfaces a discreet, non-activating floating consent pill: <i>"Active Call Detected: [Platform] — Record Meeting?"</i> with options for <b>Audio Only</b> or <b>Audio + Video</b>.</li>
  <li><b>Strict Opt-In Rule:</b> VOX never records audio or video without explicit user consent. If the user ignores or dismisses the prompt, zero audio buffers are retained.</li>
  <li><b>Session Deduplication:</b> Maintains stable session IDs (e.g., <code>teams_66990_2230116</code>); resizing, minimizing, or dragging meeting windows never re-triggers redundant consent dialogs.</li>
</ul>

<h3>4.3 Hardware-Separated Dual-Track WASAPI Audio Capture</h3>
<ul>
  <li><b>Isolated Physical Streams:</b> Captures local microphone audio (user's voice) via dedicated WASAPI microphone stream and remote participants' audio via native Windows WASAPI Loopback (render endpoint audio).</li>
  <li><b>Track Persistence:</b> Stores pristine raw tracks: <code>mic_track.wav</code> (Channel 1: Local Speaker) and <code>system_track.wav</code> (Channel 2: Remote Participants), alongside an auto-gain normalized <code>meeting_mixed.wav</code>.</li>
  <li><b>Studio Acoustic Conditioning:</b> Local microphone audio passes through real-time DSP: DC blocking filter (&gt;30 dB rejection), 50/60 Hz mains hum notch filter (&gt;10 dB), stationary background fan noise reduction, and true-peak limiting (|sample| &le; 0.9801).</li>
</ul>

<h3>4.4 Live Progressive Bilingual Transcription</h3>
<ul>
  <li><b>Streaming Partial Segments:</b> During an ongoing meeting, audio chunks (3s-5s) are transcribed progressively in background worker threads, emitting live timestamped segments into the UI without waiting for meeting completion.</li>
  <li><b>Full Bilingual Code-Switching:</b> Native handling of mixed English, Hindi, and Hinglish dialogue throughout multi-speaker meetings.</li>
</ul>

<h3>4.5 Persistent Speaker Diarization &amp; Voiceprint Clustering</h3>
<ul>
  <li><b>Hardware-Anchored Speaker Attribution:</b> Because the user's voice is physically captured on the mic track and remote participants on the system loopback track, VOX establishes a 100% deterministic baseline separating "You (Local)" from remote participants.</li>
  <li><b>Multi-Speaker Centroid Clustering:</b> Remote audio on the system track is clustered via acoustic feature centroids and Mel-frequency cepstral coefficients (MFCCs) to differentiate individual remote participants (Speaker 1, Speaker 2, Speaker 3).</li>
  <li><b>Affirmation Guard:</b> Utterances under 1.2s require high confidence affirmation before speaker labels are assigned, preventing transient interjections from splitting speaker identities.</li>
  <li><b>User-Friendly Speaker Renaming:</b> One-click UI action allowing users to rename "Speaker 1" to "Client - Sarah Jenkins" retroactively updating all transcript segments and summary references.</li>
</ul>

<h3>4.6 Unified 3-Tab Meeting Workspace</h3>
<p>Meeting Studio consolidates all meeting context into a clean, modern 3-tab layout:</p>
<ul>
  <li><b>Tab 1: "My Thoughts" (Personal Scratchpad):</b> A dedicated, private notes editor allowing the user to type observations, personal thoughts, negotiation strategies, and private follow-ups during or after the meeting. These notes remain strictly private, indexed locally, and can optionally be incorporated into summary generation.</li>
  <li><b>Tab 2: "Transcript":</b> Full, chronological speaker-diarized transcript with millisecond timestamps, speaker tags, search filtering, and clickable timestamp anchors that seek the synchronized audio/video player to the exact second.</li>
  <li><b>Tab 3: "Summary":</b> Executive structured deliverables: executive overview, key discussion topics, structured decisions, grounded action items, and risk blockers.</li>
</ul>

<h3>4.7 Grounded Decisions &amp; 7-State Evolution Tracking</h3>
<ul>
  <li><b>Canonical Decision Schema:</b> Extracted decisions decouple business lifecycle status from cross-meeting evolution.
    <ul>
      <li>Business Status: <code>proposed</code>, <code>confirmed</code>, <code>rejected</code>, <code>unresolved</code>.</li>
      <li>Evolutionary Relationship: <code>new</code>, <code>repeated</code>, <code>superseded</code>, <code>reversed</code>.</li>
    </ul>
  </li>
  <li><b>Relational Provenance (ADR-001):</b> Historical decisions are protected via foreign key constraints: <code>supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT</code> under active SQLite foreign keys, guaranteeing an immutable audit trail across multi-meeting project lifecycles.</li>
</ul>

<h3>4.8 Action Item Extraction with Verifiable Citations</h3>
<ul>
  <li><b>Mandatory Transcript Grounding:</b> Every extracted action item must include:
    <ul>
      <li><code>task_description</code>: Concise, actionable summary of the commitment.</li>
      <li><code>owner</code>: Assigned person or role (e.g., "Sarah", "Engineering Team").</li>
      <li><code>evidence_quote</code>: Exact verbatim quotation from the meeting transcript confirming the assignment.</li>
      <li><code>timestamp_sec</code>: Precise second in the recording where the commitment was verbalized.</li>
    </ul>
  </li>
  <li><b>Deterministic Date Arithmetic (ADR-003):</b> Relative date expressions ("by Friday", "in two weeks") are resolved into concrete ISO calendar dates anchored deterministically to the meeting creation timestamp. Ambiguous temporal phrases ("sometime soon", "next sprint") resolve to <code>due_date = None</code> with explicit <code>resolution_status = "ambiguous"</code> and <code>uncertainty_reason</code>, eliminating hallucinated deadlines.</li>
</ul>

<h3>4.9 One-Click Executive Follow-Up Email Generation</h3>
<ul>
  <li><b>Automated Follow-Up Drafting:</b> Single-click button or voice command (<i>"Hey Vox, write a follow-up email"</i>) synthesizes a professional, executive follow-up email ready for dispatch.</li>
  <li><b>Grounded Content Structure:</b> Automatically incorporates meeting date/title, attendees, key decisions agreed upon, assigned action items grouped by owner with due dates, and proposed next steps.</li>
  <li><b>Direct Client Dispatch:</b> One-click "Copy Markdown", "Copy Rich HTML", or "Open in Outlook / Gmail" actions.</li>
</ul>

<h3>4.10 Per-Participant Commitment Checklist Generation</h3>
<ul>
  <li><b>Checklist Matrix:</b> Deconstructs meeting outcomes into individual, per-participant task checklists (e.g., <i>"Give me a checklist for each person on this call"</i>).</li>
  <li><b>Interactive Checkboxes:</b> Interactive UI widgets allow marking items complete, filtering by person, and exporting directly into Slack or Microsoft Teams markdown format.</li>
</ul>

<h3>4.11 Scoped In-Report "Ask Vox" RAG</h3>
<ul>
  <li><b>Quarantined Session Retrieval (ADR-004):</b> In-report questions submitted to Ask Vox enforce strict meeting-level quarantine, restricting search candidate chunks to 100% of the active meeting's transcript and notes, preventing confidential cross-meeting leakage.</li>
  <li><b>Verifiable Answers with Citations:</b> Answers cite specific timestamps and speakers; unmentioned topics truthfully report that the subject was not discussed in the session.</li>
</ul>

<h3>4.12 Preservation of Existing Meeting Studio Capabilities</h3>
<ul>
  <li><b>Meeting Comparison Engine:</b> Cross-meeting differential analysis comparing decisions, timeline shifts, and action item status across two distinct sessions.</li>
  <li><b>Pre-Meeting Briefing Assistant:</b> Generates instant refresher dossiers summarizing prior meetings, open commitments, and historical context prior to stepping into a recurring call.</li>
  <li><b>Universal User-Edit Preservation (ADR-002):</b> Re-running AI finalization never deletes user-modified, manually added, or completed action items.</li>
  <li><b>Multi-Format Exporters:</b> Export meeting records to TXT, Markdown, SRT, VTT, and standalone MP4 video.</li>
</ul>

<hr/>

<h2>5. Pillar 4: VOX Brain (Unified Workspace &amp; Pill Control)</h2>

<h3>5.1 Compact Floating Pill Control Architecture</h3>
<p>Inspired by the sleek ergonomics of Wispr Flow and Letterly (Reference Image 2), the evolved VOX Floating Pill serves as the universal remote control for desktop intelligence.</p>
<ul>
  <li><b>Collapsed Minimal State:</b> Compact rounded pill (dimensions: ~180px &times; 36px) featuring the VOX icon, live dynamic waveform bars, and current status indicator.</li>
  <li><b>Active Recording State:</b> Expands dynamically to reveal:
    <ul>
      <li>Dismiss / Cancel control (<code>&times;</code>).</li>
      <li>Live multi-channel audio waveform bars reflecting voice modulation.</li>
      <li>Elapsed recording timer (<code>MM:SS</code>).</li>
      <li>Stop / Finalize button (prominent rounded orange/red square).</li>
    </ul>
  </li>
  <li><b>Integrated Mode Pill Selectors:</b> Positioned directly below or inline with the pill, displaying three primary transformation modes with keyboard shortcut badges:
    <ul>
      <li><b>Magic [Win+F1]:</b> Contextual rewriting and polished prose.</li>
      <li><b>Structured [Win+F2]:</b> Markdown bullet points, summaries, and action outlines.</li>
      <li><b>Email [Win+F3]:</b> Executive email drafts with salutation and sign-off.</li>
      <li><b>Mode Cycle Chevron (<code>&#x21D5;</code>):</b> One-click toggle or scroll to cycle through active modes.</li>
    </ul>
  </li>
  <li><b>Screen Border &amp; Drag Ergonomics:</b> The pill can be dragged by its brand grip handle to any screen edge, persisting its position across restarts.</li>
</ul>

<h3>5.2 Unified Desktop Workspace</h3>
<ul>
  <li><b>Single Application Hub:</b> Merges Voice Intelligence settings, Screen Recordings library, Meeting Studio dashboard, and VOX Brain knowledge catalog into a cohesive, responsive Windows 11 application window.</li>
  <li><b>Seamless Docking:</b> Double-clicking the floating pill or pressing <code>Ctrl+Shift+D</code> smoothly summons the full workspace without jarring UI flicker.</li>
</ul>

<h3>5.3 Cross-Application Context Integration</h3>
<ul>
  <li><b>Active Application Intelligence:</b> VOX Brain maintains passive awareness of the foreground application context (window title, document name, active project URL), enabling zero-effort contextual understanding.</li>
  <li><b>Local Desktop Search:</b> Search across all historical meeting transcripts, personal scratchpad thoughts, screen recording notes, and dictation logs using high-speed SQLite FTS5 full-text search.</li>
</ul>

<h3>5.4 Permission-Controlled Local MCP Bridges &amp; External AI Integration</h3>
<ul>
  <li><b>External AI Connectors:</b> Allows users to link their local VOX Brain to desktop AI tools (ChatGPT desktop, Claude desktop) via local loopback API / Model Context Protocol (MCP) servers.</li>
  <li><b>Zero Passive Egress:</b> External AI sharing is never automated. When an external AI tool queries VOX (e.g., <i>"Pull up notes from yesterday's product review"</i>), VOX displays an explicit confirmation dialog showing the exact data payload and requesting user authorization.</li>
  <li><b>Local Calendar &amp; Slack Bridges:</b> Optional read-only local integrations to query Google Calendar / Outlook Calendar for upcoming meeting titles and attendees, priming speaker diarization and custom vocabulary models.</li>
</ul>

<hr/>

<h2>6. Non-Functional, Security &amp; Performance Requirements</h2>

<h3>6.1 Performance Benchmarks</h3>
<ul>
  <li><b>Cold Startup Time:</b> Production executable (<code>dist/VoxLocal/VoxLocal.exe</code>) cold launch to functional floating pill: <b>&lt; 2.0 seconds</b> (baseline: 1.62s).</li>
  <li><b>Warm Startup Time:</b> Restoring from system tray: <b>&lt; 250 milliseconds</b>.</li>
  <li><b>Voice Typing Latency:</b> Speech pause to active window character injection: <b>&lt; 350 milliseconds</b> on CUDA int8; <b>&lt; 650 milliseconds</b> on multi-threaded CPU.</li>
  <li><b>A/V Sync Drift:</b> Video capture audio-to-video drift: <b>&lt; 66.6 milliseconds</b> across 2-hour recording sessions.</li>
  <li><b>Memory Footprint:</b> Idle background memory consumption: <b>&lt; 120 MB RSS</b>; active LLM + Whisper inference: <b>&lt; 480 MB RSS</b>.</li>
</ul>

<h3>6.2 Hardware &amp; OS Compatibility</h3>
<ul>
  <li><b>Operating System:</b> Windows 10 (21H2+) and Windows 11 (all editions), 64-bit x86_64.</li>
  <li><b>GPU Acceleration:</b> Automatic NVIDIA CUDA acceleration (Compute Capability 5.0+; Pascal, Turing, Ampere, Ada Lovelace, Blackwell) with automatic, graceful fallback to multi-threaded CPU SIMD (AVX2/AVX-512).</li>
  <li><b>High-DPI Display Scaling:</b> Crisp vector iconography and antialiased typography across 100%, 125%, 150%, 175%, and 200% Windows display scaling settings without blurriness or color fringing.</li>
</ul>

<h3>6.3 Security, Privacy &amp; Data Governance</h3>
<ul>
  <li><b>Strict Offline Operation:</b> Complete functionality available with zero internet connection. Network model checks are bypassed when Offline Mode is enabled.</li>
  <li><b>Local Encryption &amp; Storage:</b> SQLite WAL database (<code>data/voxlocal.db</code>) with atomic transactions and automated online verified snapshot backups (<code>create_verified_snapshot()</code>).</li>
  <li><b>Diagnostic Sanitization:</b> Application logs automatically scrub authentication tokens, personal file paths, and clipboard passwords.</li>
  <li><b>Enterprise Uninstallation:</b> PowerShell clean uninstaller (<code>uninstall.ps1</code>) completely removes Windows startup registry keys (<code>HKCU\Software\Microsoft\Windows\CurrentVersion\Run</code>) and desktop shortcuts.</li>
</ul>

<hr/>

<h2>7. Verification &amp; Acceptance Criteria Register</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Verification ID</th>
      <th>Requirement Domain</th>
      <th>Acceptance Criteria</th>
      <th>Target Verification Method</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>VER-VT-01</b></td>
      <td>Push-to-Talk Latency</td>
      <td>Key release to text insertion completed in &lt; 250ms on CUDA hardware.</td>
      <td>Automated timestamped latency benchmark in <code>tests/test_voice_typing_e2e.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-VT-02</b></td>
      <td>Hinglish Code-Switching</td>
      <td>Devanagari Hindi, English loanwords, and Romanized Hinglish transcribed with &lt; 5% WER.</td>
      <td>Ground-truth evaluation against bilingual tech corpus in <code>test_phase9_audio_video_quality.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-VT-03</b></td>
      <td>Focus Invariance</td>
      <td>Clicking floating pill and overlays never steals keyboard focus from active Notepad/Word cursor.</td>
      <td>Win32 foreground HWND tracker verification in <code>test_handsfree_and_calls.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-SC-01</b></td>
      <td>Call-Only Window Isolation</td>
      <td>Switching windows during call recording produces 0 frames of non-meeting applications.</td>
      <td>Minimized window privacy guard validation in <code>test_phase12_security_privacy_hardening.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-SC-02</b></td>
      <td>A/V Synchronization</td>
      <td>Video frame PTS and audio track timestamps maintain drift &lt; 66.6ms over 60 minutes.</td>
      <td>Drift verification test in <code>test_phase9_audio_video_quality.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-MT-01</b></td>
      <td>Zero-Bot Local Detection</td>
      <td>Teams, Zoom, Meet calls detected via native Windows registry without bot injection.</td>
      <td>Registry &amp; window classification suite in <code>test_handsfree_and_calls.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-MT-02</b></td>
      <td>Grounded Actions &amp; Decisions</td>
      <td>100% of extracted action items and decisions cite verified transcript quotes and timestamps.</td>
      <td>Grounding &amp; citation test battery in <code>test_phase10_local_ai_and_meeting_intelligence.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-MT-03</b></td>
      <td>Unified 3-Tab Workspace</td>
      <td>Meeting Studio renders My Thoughts, Transcript, and Summary tabs cleanly.</td>
      <td>UI component integration verification in <code>test_phase11_product_experience_and_workflow.py</code>.</td>
    </tr>
    <tr>
      <td><b>VER-BR-01</b></td>
      <td>Pill Mode Switching</td>
      <td>Win+F1 (Magic), Win+F2 (Structured), Win+F3 (Email) switch formatting modes instantly.</td>
      <td>Hotkey binding and transformation mode tests.</td>
    </tr>
    <tr>
      <td><b>VER-BR-02</b></td>
      <td>Privacy Egress Quarantine</td>
      <td>Zero outbound network sockets opened during all operations.</td>
      <td>Static and dynamic network socket egress audit in <code>test_phase12_security_privacy_hardening.py</code>.</td>
    </tr>
  </tbody>
</table>

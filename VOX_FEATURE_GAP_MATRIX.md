<h2>VOX Desktop Productivity Assistant — Comprehensive Feature Gap Matrix</h2>

<p><b>Product Name:</b> VOX (formerly VoxLocal)<br/>
<b>Document Purpose:</b> Systematic, forensic feature gap analysis comparing target evolved capabilities against the Wispr Flow reference benchmark and current VOX implementation, detailing required engineering actions, priorities, complexity, and risks.<br/>
<b>Pillars Evaluated:</b> Voice Intelligence, Screen Intelligence, Meeting Intelligence, VOX Brain<br/>
<b>Document Version:</b> 2.0.0-GAP-SPEC<br/>
<b>Formatting Invariant:</b> Strict HTML heading compliance (zero markdown # or * headers across all sections).</p>

<hr/>

<h2>1. Executive Synthesis &amp; Gap Overview</h2>

<h3>1.1 Comparative Landscape Assessment</h3>
<p>The forensic evaluation of the Wispr Flow reference experience (video file <code>Wisperr Flow.mp4</code>, audio track <code>wispr_audio.wav</code>, and reference UI artifacts) alongside the existing VOX codebase (Enterprise Release Candidate RC-1) establishes key architectural truths:</p>
<ul>
  <li><b>Where VOX Already Outperforms the Reference:</b> Current VOX already possesses an enterprise-grade 100% offline, local architecture. VOX features true native dual-channel WASAPI loopback audio recording (separating mic and system tracks), call-only window privacy bounds, 7-state decision evolution with relational foreign key protection (ADR-001), deterministic relative date parsing (ADR-003), offline meeting-scoped Ask Vox RAG quarantine (ADR-004), and full Devanagari Hindi orthographic restoration. Wispr Flow relies heavily on proprietary cloud infrastructure and lacks verifiable local database constraints and meeting comparison engines.</li>
  <li><b>Critical Gaps to Bridge for Parity &amp; Leadership:</b>
    <ol>
      <li><b>Compact Ergonomic Floating Pill:</b> VOX currently has a functional floating bar with buttons and hover cards, but lacks the hyper-compact, unified floating pill demonstrated in Wispr Flow and Reference Image 2 (top pill with dynamic waveform, timer, red stop button, and dismiss 'X', coupled with integrated mode selector chips: Magic [Win+F1], Structured [Win+F2], Email [Win+F3]).</li>
      <li><b>Unified 3-Tab Meeting Workspace:</b> VOX's current meeting reports page is rich but complex (with multiple sub-tabs, comparison modals, and splitters). Target UX mandates an intuitive, streamlined 3-tab layout: <b>"My Thoughts"</b> (personal private scratchpad), <b>"Transcript"</b> (speaker-diarized interactive transcript), and <b>"Summary"</b> (grounded executive MoM, decisions, actions, and risks).</li>
      <li><b>One-Click Follow-Up Email &amp; Participant Checklists:</b> While VOX extracts action items into a matrix, it lacks the dedicated one-click executive follow-up email generator and per-participant checklist breakdown explicitly highlighted as core Wispr Flow differentiators.</li>
      <li><b>Real-Time Mid-Utterance Self-Correction:</b> VOX supports spoken undo ("delete that"), but needs dynamic acoustic/lexical rewriting during active dictation when users say "no actually...", "wait sorry...", or "scratch that...".</li>
      <li><b>Local MCP / External AI Permission Bridge:</b> Formalizing the local Model Context Protocol (MCP) server so external tools (ChatGPT desktop, Claude desktop) can safely query VOX meeting context under explicit user authorization.</li>
    </ol>
  </li>
</ul>

<h3>1.2 Priority, Complexity, and Risk Classification Framework</h3>
<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Dimension</th>
      <th>Classification Levels</th>
      <th>Operational Definition</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Priority</b></td>
      <td><b>P0 (Foundational / Core)</b><br/><b>P1 (High Value)</b><br/><b>P2 (Polish / Extension)</b></td>
      <td><b>P0:</b> Required for primary capture-understand-create-execute workflows; blocking release.<br/><b>P1:</b> Significant productivity or parity enhancement with Wispr Flow.<br/><b>P2:</b> Ergonomic refinement, secondary visual styling, or advanced power-user feature.</td>
    </tr>
    <tr>
      <td><b>Complexity</b></td>
      <td><b>Low</b><br/><b>Medium</b><br/><b>High</b><br/><b>Extreme</b></td>
      <td><b>Low:</b> UI tweak or regex rule addition (&lt; 4 engineering hours).<br/><b>Medium:</b> Component enhancement or new Qt widget (&lt; 16 hours).<br/><b>High:</b> Multi-threaded engine change, audio pipeline adjustment, or schema extension (&lt; 40 hours).<br/><b>Extreme:</b> Fundamental IPC, low-level Win32 hook, or neural model fine-tuning (&gt; 40 hours).</td>
    </tr>
    <tr>
      <td><b>Risk</b></td>
      <td><b>Low</b><br/><b>Medium</b><br/><b>High</b></td>
      <td><b>Low:</b> Isolated component with zero regression potential on existing verified suites.<br/><b>Medium:</b> Interacts with timing, focus tracking, or database foreign keys.<br/><b>High:</b> Involves low-level audio device capture, Win32 SendInput UIPI, or hardware codec stability.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>2. Pillar 1: Voice Intelligence (Voice Typing) Feature Gap Matrix</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Feature ID &amp; Target Capability</th>
      <th>Wispr Flow Reference Experience</th>
      <th>Current VOX Implementation State</th>
      <th>Gap Assessment &amp; Delta</th>
      <th>Pri</th>
      <th>Cpx</th>
      <th>Risk</th>
      <th>Concrete Engineering Action Required</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>GAP-VT-01: Push-to-Talk (PTT) Activation</b></td>
      <td>Dedicated function key activation (demonstrated in narration: <i>"All I have to do is tap the function button and speak"</i>). Instant text delivery upon release.</td>
      <td><code>GlobalHotkeyManager</code> (<code>src/voxlocal/win32/hotkeys.py</code>) supports WH_KEYBOARD_LL low-level hooks, supporting toggle and PTT hotkeys (e.g., F8, Ctrl+Win).</td>
      <td>Minor gap: Default shortcut is set to F8 / Ctrl+Win. Needs seamless single-tap Function (Fn) key support where BIOS allows, and sub-220ms release latency tuning.</td>
      <td><b>P0</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Configure default PTT chord in <code>hotkeys.py</code> and <code>config.py</code>; verify key-up release latency via high-resolution QElapsedTimer in <code>controller.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-02: Live Streaming Overlay &amp; Waveform</b></td>
      <td>Mobile and desktop overlay showing live audio waveform bars, dynamic speech modulation, and animated status (Reference Images 1 &amp; 2).</td>
      <td><code>LiveTranscriptBubble</code> in <code>floating_widget.py</code> displays provisional text. <code>MultiChannelEqualizerWidget</code> in <code>animations.py</code> provides waveform rendering.</td>
      <td>Overlay and pill are separate widgets. Need integrated compact pill showing live 30 FPS dynamic waveform bars directly inside the pill body during voice typing.</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Embed animated waveform canvas inside <code>FloatingPillWidget</code> in <code>floating_widget.py</code>, fed directly by <code>dictation_level_changed</code> RMS signals.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-03: Sub-200ms Active Cursor Injection</b></td>
      <td>Universal injection across all apps (WhatsApp, Slack, Notion, Gmail, Docs, ChatGPT, Obsidian - Reference Image 1) directly at text cursor.</td>
      <td><code>TextInserter</code> (<code>src/voxlocal/win32/text_inserter.py</code>) implements Win32 <code>SendInput(KEYEVENTF_UNICODE)</code>, <code>WM_CHAR</code> target fallback, and atomic clipboard paste fallback.</td>
      <td>Capabilities fully present in backend, but requires automated app profile classifier to select atomic clipboard paste for Electron apps automatically without manual configuration.</td>
      <td><b>P0</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Add automatic process classification in <code>text_inserter.py</code> to auto-select clipboard method for Slack, Teams, VS Code, Discord, and Obsidian based on foreground HWND process name.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-04: Multilingual &amp; Hinglish Code-Switching</b></td>
      <td>Demonstrated in narration: <i>"In India we keep switching between Hindi and English... Whisper understands English, Hindi and even Hinglish"</i>.</td>
      <td><code>DictationProcessor</code> in <code>dictation_processor.py</code> has 250+ Devanagari English loanwords, accent corrections, Devanagari orthographic fixes, and <code>devanagari_to_roman_hinglish()</code> converter.</td>
      <td>Engine capability exists and scored 0.0% WER on technical Hinglish. Gap: Expose clear 1-click script selector (Devanagari vs Roman Hinglish) directly on the floating pill or tray.</td>
      <td><b>P0</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Expose script preference in <code>AppConfig</code> and floating menu, binding <code>output_script="roman"</code> or <code>"deva"</code> dynamically in <code>dictation_processor.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-05: Personal &amp; Domain Dictionary</b></td>
      <td>Demonstrated in narration: <i>"Uses your personal dictionary, your calendar and even your Slack to understand the context... gets client names and product names right"</i>.</td>
      <td>SQLite vocabulary table (<code>database.py</code>), UI vocabulary manager in <code>main_window.py</code>, regex replacement engine in <code>dictation_processor.py</code>.</td>
      <td>Vocabulary exists, but currently requires manual user entry. Lacks automated contextual harvesting from active calendar events and foreground application window titles.</td>
      <td><b>P1</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Implement <code>harvest_calendar_attendees()</code> and wire <code>extract_window_keywords()</code> directly into dynamic vocabulary biasing during active voice typing sessions.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-06: Verbal Filler Filtering &amp; Noise Gating</b></td>
      <td>Demonstrated in narration: <i>"Cuts out all the filler words and gives you a crisp sentence"</i>.</td>
      <td><code>FILLER_PATTERN</code> in <code>dictation_processor.py</code> removes um, uh, erm, er, hmm. Energy thresholding (RMS &gt; 0.0035) rejects desk taps and coughs.</td>
      <td>Gap: Needs expansion to conversational filler words ("like", "you know", "basically", "actually") when configured in Cleaned or Magic mode.</td>
      <td><b>P0</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Expand filler regex pattern in <code>dictation_processor.py</code> to include contextual conversational disfluencies when <code>cleanup_enabled=True</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-07: Operational Modes (Magic, Structured, Email)</b></td>
      <td>Demonstrated in Reference Image 2: Three distinct mode pills: <b>Magic [Win+F1]</b>, <b>Structured [Win+F2]</b>, <b>Email [Win+F3]</b> with chevron cycle toggle.</td>
      <td>VOX currently supports binary <code>cleanup_enabled</code> toggle (verbatim vs cleaned) and custom punctuation.</td>
      <td>Major gap: Lacks the explicit 3-mode transformation layer that turns rambling speech into polished prose (Magic), bulleted markdown (Structured), or formal email (Email).</td>
      <td><b>P0</b></td>
      <td>High</td>
      <td>Medium</td>
      <td>Implement <code>TransformationMode</code> enum in <code>dictation_processor.py</code> and <code>controller.py</code>; route transcribed text through local fast LLM templates for Magic, Structured, and Email transformations; bind global hotkeys Win+F1, Win+F2, Win+F3.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-08: Streaming Self-Correction</b></td>
      <td>Demonstrated in narration: <i>"No actually, first let's do the fundamental analysis... cuts out the filler words and gives you a crisp sentence"</i>.</td>
      <td>VOX supports spoken command "delete that" to erase the previous injection, but does not parse mid-sentence corrections during active streaming.</td>
      <td>Major gap: When user says <i>"Evaluate X... no actually, first let's do Y"</i>, the engine must discard X and inject only the corrected thought Y.</td>
      <td><b>P1</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Add <code>parse_verbal_self_correction()</code> in <code>dictation_processor.py</code> to recognize pivot markers ("no actually", "wait sorry", "scratch that") and rewrite the utterance buffer prior to emission.</td>
    </tr>
    <tr>
      <td><b>GAP-VT-09: User-Editable Review Overlay &amp; Focus Invariance</b></td>
      <td>Non-activating floating interface allowing optional review or editing without stealing active cursor focus.</td>
      <td><code>FloatingPillWidget</code> uses <code>WS_EX_NOACTIVATE</code>. Text is injected directly at cursor without user edit step.</td>
      <td>Gap: Power users want an optional 1.5s provisional floating review chip where they can hit Backspace or Enter before text commits to the active window.</td>
      <td><b>P2</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Create <code>ProvisionalReviewChip</code> in <code>floating_widget.py</code> with configurable auto-commit timeout (0s to 3s).</td>
    </tr>
    <tr>
      <td><b>GAP-VT-10: Error Recovery &amp; Hot-Plugging</b></td>
      <td>Seamless operation when switching between mobile, desktop, and various microphones.</td>
      <td><code>AudioDeviceManager</code> in <code>devices.py</code> discovers WASAPI devices, but unexpected unplugging during active recording can throw stream errors.</td>
      <td>Gap: Add proactive PyAudio device polling and graceful fallback to system default microphone upon USB/Bluetooth disconnection.</td>
      <td><b>P1</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Implement automatic reconnection watch loop in <code>dictation_recorder.py</code> and <code>streaming_typer.py</code> to recover within 500ms of device state change.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>3. Pillar 2: Screen Intelligence (Screen Capture) Feature Gap Matrix</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Feature ID &amp; Target Capability</th>
      <th>Wispr Flow Reference Experience</th>
      <th>Current VOX Implementation State</th>
      <th>Gap Assessment &amp; Delta</th>
      <th>Pri</th>
      <th>Cpx</th>
      <th>Risk</th>
      <th>Concrete Engineering Action Required</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>GAP-SC-01: Multi-Mode Screen Capture</b></td>
      <td>Marketing claim of desktop context ingestion across all apps (Reference Image 1). Video focus is primarily voice typing and notes.</td>
      <td><code>ScreenRecorder</code> (<code>screen_recorder.py</code>) supports full desktop capture at 1080p, up to 144 FPS. <code>MeetingVideoRecorder</code> (<code>meeting_capture.py</code>) supports target window capture.</td>
      <td>Full capture engine exists. Gap: Needs unified launcher dialog to choose between Full Desktop, Target Window, or Region Snip in one click.</td>
      <td><b>P1</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Unify screen recording trigger options inside <code>MeetingQuickStartDialog</code> and floating pill context menu.</td>
    </tr>
    <tr>
      <td><b>GAP-SC-02: Call-Only Window Privacy Isolation</b></td>
      <td>Wispr Flow records meetings quietly on computer, but does not provide documented window privacy bounds against background desktop apps.</td>
      <td><code>meeting_capture.py</code> enforces strict call window binding. Fails closed emitting a neutral status frame if call window is minimized or covered.</td>
      <td><b>VOX Superiority:</b> Privacy isolation is fully engineered and verified (RC-1 Phase 12). Zero gap vs reference; maintain existing implementation.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve strict privacy invariants in <code>meeting_capture.py</code> without introducing full-desktop fallback leaks.</td>
    </tr>
    <tr>
      <td><b>GAP-SC-03: Audio-Video Synchronization &amp; Drift Guard</b></td>
      <td>Not explicitly detailed in Wispr Flow marketing.</td>
      <td>Hardware-synchronized wall-clock PTS encoding in <code>meeting_capture.py</code> and <code>screen_recorder.py</code>; A/V drift calibrated &lt; 66.6ms (Phase 9).</td>
      <td><b>VOX Superiority:</b> Strict synchronization with AAC audio remuxing is verified. Zero gap vs reference; maintain existing implementation.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve wall-clock PTS calculation and true-peak audio limiter in <code>meeting_capture.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-SC-04: Cursor Dynamics &amp; Interaction Visualizers</b></td>
      <td>Not demonstrated in Wispr Flow reference video.</td>
      <td><code>ScreenRecorder</code> captures native cursor via <code>DrawIconEx</code>, champagne gold click ripples, sapphire right-click ripples, and scroll indicators.</td>
      <td><b>VOX Superiority:</b> Cursor dynamics and interaction animations already implemented and verified in VOX.</td>
      <td><b>P1</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Ensure cursor capture toggles remain user-configurable in <code>AppConfig</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-SC-05: Floating Recording Controls</b></td>
      <td>Minimal recording UI with timer and stop indicator (Reference Image 2).</td>
      <td><code>FloatingWidget</code> has record/pause/stop buttons, but layout during screen recording can overlap content.</td>
      <td>Gap: Needs ultra-compact screen recording floating HUD with timer, pause, resume, snip, and stop controls docked cleanly at screen top/bottom.</td>
      <td><b>P1</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Implement <code>ScreenRecordingHUD</code> docked floating toolbar in <code>floating_widget.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-SC-06: Crash &amp; Interrupted Recording Recovery</b></td>
      <td>Not addressed in Wispr Flow reference materials.</td>
      <td><code>meeting_capture.py</code> uses PyAV container. Abrupt termination can leave MP4 moov atom unfinalized if not using fragmented MP4.</td>
      <td>Gap: Adopt fragmented MP4 options (<code>frag_keyframe+empty_moov</code>) to guarantee playable video even if application is terminated abruptly or loses power.</td>
      <td><b>P1</b></td>
      <td>Medium</td>
      <td>Medium</td>
      <td>Set PyAV muxer options <code>movflags="frag_keyframe+empty_moov+default_base_moof"</code> in <code>meeting_capture.py</code> and <code>screen_recorder.py</code>.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>4. Pillar 3: Meeting Intelligence (Meeting Studio) Feature Gap Matrix</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Feature ID &amp; Target Capability</th>
      <th>Wispr Flow Reference Experience</th>
      <th>Current VOX Implementation State</th>
      <th>Gap Assessment &amp; Delta</th>
      <th>Pri</th>
      <th>Cpx</th>
      <th>Risk</th>
      <th>Concrete Engineering Action Required</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>GAP-MT-01: Native Desktop Meeting Detection Without Bots</b></td>
      <td>Demonstrated in narration: <i>"Other note takers send a bot into your call, everyone sees that creepy note taker... Whisper doesn't join the call at all, it records quietly on your computer"</i>.</td>
      <td><code>CallDetector</code> (<code>call_detector.py</code>) detects active calls across Teams, Zoom, Meet, Webex, Slack, Discord, Skype via Windows microphone registry + Win32 window isolation.</td>
      <td><b>VOX Core Parity:</b> Fully operational and verified. Zero bot attendance; 100% local Windows API detection.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve <code>CallDetector</code> state machine and registry monitoring in <code>src/voxlocal/audio/call_detector.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-02: 1-Click Explicit Consent Workflow</b></td>
      <td>Records quietly with minimal user interaction.</td>
      <td><code>CallConsentDialog</code> and <code>MeetingPromptPopup</code> popup floating prompts when calls are detected, requiring explicit user confirmation.</td>
      <td>Gap: Consent prompt is currently modal/semi-modal. Needs seamless integration into the compact floating pill as a subtle glowing notification.</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Update <code>floating_widget.py</code> to display inline meeting detection banner inside the floating pill with 1-click [Record Audio] and [Record Video] chips.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-03: Dual-Track Local WASAPI Capture</b></td>
      <td>Records meeting audio quietly on computer (both local speaker and incoming remote participants).</td>
      <td><code>MeetingRecorder</code> (<code>meeting_recorder.py</code>) captures separate mic track (<code>mic_track.wav</code>) and system loopback track (<code>system_track.wav</code>) via native WASAPI.</td>
      <td><b>VOX Superiority:</b> Raw tracks are preserved independently with auto-gain normalized mixed track. Zero gap vs reference.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Maintain dual-track recording engine in <code>src/voxlocal/audio/meeting_recorder.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-04: Unified 3-Tab Meeting Workspace</b></td>
      <td>Reference Image 2 and prompt specify unified workspace with three clear primary tabs: <b>"My Thoughts"</b>, <b>"Transcript"</b>, <b>"Summary"</b>.</td>
      <td><code>MainWindow</code> (<code>main_window.py</code>) currently has <code>_build_reports_tab()</code> with complex nested sub-pages, separate comparison dialogs, and splitters.</td>
      <td>Major gap: The meeting detail view needs a clean, modern, prominent 3-tab layout: Tab 1 "My Thoughts", Tab 2 "Transcript", Tab 3 "Summary".</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Refactor meeting report detail container in <code>main_window.py</code> into three distinct, beautifully styled tabs: My Thoughts, Transcript, Summary.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-05: Personal Meeting Notes ("My Thoughts")</b></td>
      <td>Wispr Flow allows personal thoughts and meeting context to be captured alongside automated notes.</td>
      <td>VOX currently stores automated summaries and transcript edits, but lacks a dedicated live personal notes editor synced to the meeting timeline.</td>
      <td>Major gap: Users need a private, auto-saving markdown scratchpad ("My Thoughts") where they can record private impressions, observations, and negotiation points during the call.</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Add <code>personal_notes TEXT</code> column to SQLite <code>meetings</code> table; implement live auto-saving <code>MyThoughtsEditor</code> widget in Tab 1 of the meeting workspace.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-06: Live Speaker Diarization &amp; Transcript</b></td>
      <td>Speaker-attributed meeting transcript with multilingual recognition.</td>
      <td><code>PersistentSpeakerDiarizer</code> in <code>transcriber.py</code> clusters speakers using hardware mic vs system loopback baseline + MFCC centroid clustering with affirmation guard.</td>
      <td>Core engine is solid. Gap: Live transcription during recording currently updates progressive chunks, but needs cleaner live UI rendering in Tab 2.</td>
      <td><b>P0</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Stream live progressive segments into Tab 2 ("Transcript") with automatic auto-scroll and speaker badges in <code>main_window.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-07: One-Click Executive Follow-Up Email</b></td>
      <td>Demonstrated in narration: <i>"Can you please write a follow up email... and it will write a beautiful email before you even blink"</i>.</td>
      <td>VOX has 5 report templates (Standard MoM, Executive Brief, etc.), but lacks an explicit one-click "Generate Follow-up Email" button and voice command.</td>
      <td>Major gap: Must provide a dedicated Follow-Up Email generator grounded in agreed decisions and action items, ready to copy or dispatch to Outlook.</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Add <code>generate_followup_email()</code> in <code>meeting_intelligence.py</code> and add prominent "Draft Follow-Up Email" button and voice command in <code>main_window.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-08: Per-Participant Commitment Checklist</b></td>
      <td>Demonstrated in narration: <i>"...and give me a checklist for each person in this call"</i>.</td>
      <td>VOX has <code>VoxActionItemMatrixWidget</code> with status filters (Pending, Completed), but does not group tasks into per-person checklists.</td>
      <td>Major gap: Need a dedicated view/export that breaks down commitments by person (e.g., Sarah's Checklist, Milind's Checklist, Engineering Checklist).</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Add <code>generate_participant_checklists()</code> in <code>meeting_intelligence.py</code> and a grouped checklist view in Tab 3 ("Summary") of <code>main_window.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-09: Grounded Decisions with 7-State Evolution</b></td>
      <td>Wispr Flow does not model formal cross-meeting decision evolution.</td>
      <td>VOX implements ADR-001 with 7-state decision evolution and <code>supersedes_id ON DELETE RESTRICT</code> foreign keys.</td>
      <td><b>VOX Superiority:</b> Industry-leading decision governance. Zero gap vs reference; maintain existing implementation.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve decision evolution schema in <code>database.py</code> and rendering in <code>VoxStructuredDecisionsWidget</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-10: Action Items with Verifiable Citations</b></td>
      <td>Extracts general tasks from meetings.</td>
      <td>VOX mandates transcript grounding (<code>evidence_quote</code>, timestamp) and deterministic date parsing (ADR-003).</td>
      <td><b>VOX Superiority:</b> Full mathematical grounding with zero hallucinated dates. Zero gap vs reference; maintain existing implementation.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve grounded action item schema in <code>database.py</code> and <code>meeting_intelligence.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-11: Scoped In-Report "Ask Vox" RAG</b></td>
      <td>Wispr Flow connects with ChatGPT/Claude to query notes.</td>
      <td>VOX implements ADR-004: In-report Ask Vox queries are strictly quarantined to the active meeting, preventing confidential client leakage.</td>
      <td><b>VOX Superiority:</b> 100% offline local RAG with verified citations and zero cross-meeting leakage.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Preserve scoped RAG retrieval quarantine in <code>meeting_intelligence.py</code>.</td>
    </tr>
    <tr>
      <td><b>GAP-MT-12: Preservation of Existing Meeting Studio Capabilities</b></td>
      <td>Not present in Wispr Flow.</td>
      <td>VOX features Meeting Comparison Dialog, Pre-Meeting Briefing Assistant, Universal Edit Preservation (ADR-002), and multi-format exporters (TXT, MD, SRT, VTT, MP4).</td>
      <td><b>VOX Superiority:</b> All advanced enterprise capabilities must remain fully functional without regressions.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Ensure all 189 existing tests continue to pass with zero regressions.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>5. Pillar 4: VOX Brain (Unified Workspace &amp; Pill Control) Feature Gap Matrix</h2>

<table border="1" cellpadding="6" cellspacing="0">
  <thead>
    <tr>
      <th>Feature ID &amp; Target Capability</th>
      <th>Wispr Flow Reference Experience</th>
      <th>Current VOX Implementation State</th>
      <th>Gap Assessment &amp; Delta</th>
      <th>Pri</th>
      <th>Cpx</th>
      <th>Risk</th>
      <th>Concrete Engineering Action Required</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><b>GAP-BR-01: Compact Floating Pill Anatomy</b></td>
      <td>Reference Image 2: Minimal pill with cancel ('X'), dynamic audio waveform bars, elapsed timer ('14:49'), and rounded orange/red stop button.</td>
      <td><code>FloatingWidget</code> (<code>floating_widget.py</code>) currently has horizontal bar with VOX icon, dictation button, screen rec button, meeting button, level bar, menu.</td>
      <td>Major gap: Floating widget needs redesign into the exact anatomical layout shown in Reference Image 2 (top pill with waveform/timer/stop/X + bottom mode chips).</td>
      <td><b>P0</b></td>
      <td>High</td>
      <td>Medium</td>
      <td>Re-architect <code>FloatingPillWidget</code> in <code>floating_widget.py</code> to render the sleek dual-tier pill: top active session pill and bottom mode chips with hotkey badges.</td>
    </tr>
    <tr>
      <td><b>GAP-BR-02: Integrated Mode Selectors (Magic, Structured, Email)</b></td>
      <td>Reference Image 2: Three distinct mode pills: <b>Magic [Win+F1]</b>, <b>Structured [Win+F2]</b>, <b>Email [Win+F3]</b> with chevron cycle toggle.</td>
      <td>VOX does not currently have mode selectors on the floating widget.</td>
      <td>Major gap: Mode selector pills with hotkey badges must be integrated directly into the floating pill control, updating active dictation and brain synthesis modes.</td>
      <td><b>P0</b></td>
      <td>Medium</td>
      <td>Low</td>
      <td>Implement <code>VoxModePillStrip</code> in <code>floating_widget.py</code> with click handlers and shortcut bindings for Win+F1, Win+F2, Win+F3.</td>
    </tr>
    <tr>
      <td><b>GAP-BR-03: Unified Desktop Workspace Docking</b></td>
      <td>Single workspace unifying notes, typing, and settings.</td>
      <td>VOX has <code>MainWindow</code> (3-tab / multi-section window) accessible via tray or floating widget grip handle.</td>
      <td>Minor gap: Needs smoother transition and visual harmony between floating pill and main workspace window.</td>
      <td><b>P1</b></td>
      <td>Low</td>
      <td>Low</td>
      <td>Wire double-click on floating pill brand mark to smoothly summon and bring <code>MainWindow</code> to foreground.</td>
    </tr>
    <tr>
      <td><b>GAP-BR-04: Cross-App Context Integration (Calendar &amp; Slack)</b></td>
      <td>Demonstrated in narration: <i>"Uses your calendar and even your Slack to understand the context of what you're saying"</i>.</td>
      <td><code>dictation_processor.py</code> has <code>build_contextual_prompt()</code> extracting keywords from active window titles and process names.</td>
      <td>Gap: Lacks read-only local calendar and Slack integration bridges to harvest upcoming meeting context and project terminology automatically.</td>
      <td><b>P1</b></td>
      <td>High</td>
      <td>Medium</td>
      <td>Implement local-first, permission-controlled calendar and Slack metadata reader in <code>src/voxlocal/core/</code> under explicit user authorization.</td>
    </tr>
    <tr>
      <td><b>GAP-BR-05: Local MCP / External AI Permission Bridge</b></td>
      <td>Demonstrated in narration: <i>"Connect ChatGPT and Claude to Wispr Flow so you don't have to keep switching between apps... just ask your AI to pull up notes from a specific meeting"</i>.</td>
      <td>VOX has internal <code>MeetingIntelligenceEngine</code> and Ask Vox RAG, but no external IPC endpoint or MCP server for external AI tools.</td>
      <td>Major gap: Users want ChatGPT desktop or Claude desktop to query VOX meeting notes via local Model Context Protocol (MCP) server.</td>
      <td><b>P1</b></td>
      <td>High</td>
      <td>Medium</td>
      <td>Implement local loopback MCP server (<code>src/voxlocal/core/mcp_server.py</code>) exposing tools: <code>list_meetings</code>, <code>get_meeting_summary</code>, <code>query_meeting_notes</code> with mandatory local user authorization dialog.</td>
    </tr>
    <tr>
      <td><b>GAP-BR-06: Privacy-First Local Consent Invariant</b></td>
      <td>Wispr Flow claims privacy, but meeting transcripts route through cloud servers.</td>
      <td>VOX operates 100% offline. Zero outbound network sockets (verified in Phase 12).</td>
      <td><b>VOX Superiority:</b> Privacy-first local consent is mathematically guaranteed. Maintain zero-cloud invariant across all new MCP and cross-app bridges.</td>
      <td><b>P0</b></td>
      <td>Done</td>
      <td>Low</td>
      <td>Enforce strict local consent dialog before any MCP tool returns meeting data to an external client process.</td>
    </tr>
  </tbody>
</table>

<hr/>

<h2>6. Engineering Action Roadmap by Priority Tier</h2>

<h3>6.1 Priority P0 (Immediate Core Requirements)</h3>
<ol>
  <li><b>Re-architect Floating Pill Widget (GAP-BR-01, GAP-BR-02):</b> Rebuild <code>FloatingPillWidget</code> in <code>floating_widget.py</code> to mirror Reference Image 2: top pill with dismiss ('X'), animated waveform bars, elapsed timer, orange/red stop button, and bottom mode strip: Magic [Win+F1], Structured [Win+F2], Email [Win+F3].</li>
  <li><b>Implement Unified 3-Tab Meeting Workspace (GAP-MT-04, GAP-MT-05):</b> Refactor meeting report container in <code>main_window.py</code> into three dedicated tabs: <b>"My Thoughts"</b> (live personal scratchpad with SQLite persistence), <b>"Transcript"</b> (speaker-diarized interactive transcript), and <b>"Summary"</b> (grounded executive MoM, decisions, actions, and risks).</li>
  <li><b>Implement Transformation Modes in Voice Typing (GAP-VT-07):</b> Implement Magic, Structured, and Email transformation modes in <code>dictation_processor.py</code> and <code>controller.py</code>, enabling immediate restructuring of voice dictation.</li>
  <li><b>Implement One-Click Follow-Up Email &amp; Participant Checklists (GAP-MT-07, GAP-MT-08):</b> Add executive follow-up email and per-participant checklist synthesis algorithms into <code>meeting_intelligence.py</code> with dedicated UI trigger buttons and voice commands in <code>main_window.py</code>.</li>
  <li><b>Preserve 100% Existing Capabilities &amp; Test Suite:</b> Ensure all 189 tests across 21 test suites continue to pass with zero regressions.</li>
</ol>

<h3>6.2 Priority P1 (High-Value Ergonomic &amp; Intelligence Enhancements)</h3>
<ol>
  <li><b>Streaming Verbal Self-Correction (GAP-VT-08):</b> Implement verbal pivot detection ("no actually...", "wait sorry...") in <code>dictation_processor.py</code> to rewrite speech buffers dynamically during continuous dictation.</li>
  <li><b>Automatic Process Classifier for Text Injection (GAP-VT-03):</b> Auto-select atomic clipboard injection for Electron applications (Teams, Slack, VS Code, Obsidian) in <code>text_inserter.py</code> based on target process name.</li>
  <li><b>Local-First Permission-Controlled MCP Bridge (GAP-BR-05):</b> Implement local loopback Model Context Protocol server allowing ChatGPT desktop and Claude desktop to query meeting context under explicit local user confirmation.</li>
  <li><b>Fragmented MP4 Streaming for Screen Capture (GAP-SC-06):</b> Configure <code>frag_keyframe+empty_moov</code> in <code>meeting_capture.py</code> to protect video recordings against unexpected power loss or crashes.</li>
  <li><b>Device Hot-Plugging &amp; Reconnection Watchdog (GAP-VT-10):</b> Proactive PyAudio device state recovery to handle USB/Bluetooth headset disconnects seamlessly.</li>
</ol>

<h3>6.3 Priority P2 (Polish &amp; Advanced Extensions)</h3>
<ol>
  <li><b>Provisional Floating Review Chip (GAP-VT-09):</b> Optional 1.5s review chip allowing users to review or edit transcribed text before committing to the target window.</li>
  <li><b>Screen Recording Floating HUD (GAP-SC-05):</b> Dedicated minimal docked overlay for general screen recording.</li>
  <li><b>Automated Calendar &amp; Slack Terminology Harvesting (GAP-VT-05, GAP-BR-04):</b> Background harvesting of calendar attendee names to auto-populate the local vocabulary dictionary.</li>
</ol>

<h2>VOX Codebase, Capabilities & Test Suite Audit Report</h2>

<b>Auditor</b>: teamwork_explorer<br>
<b>Audit Date</b>: 2026-11-08<br>
<b>Working Directory</b>: d:\Milind\Voice Assistant<br>
<b>Constraint Compliance</b>: Strict HTML headers (<h2>, <h3>, <h4>) and bold text across all sections; zero markdown # or * headers.

<hr>

<h2>1. Executive Summary & Audit Context</h2>

<p>
This audit evaluates the current state of the VOX desktop application against the comprehensive product transformation requirements defined in <code>ORIGINAL_REQUEST.md</code> (Requirements R1 through R5). VOX possesses a mature offline audio processing core, high-performance local speech recognition (faster-whisper on CTranslate2), robust Win32 non-activating window styles, dual-track meeting loopback recording, and extensive unit testing. However, there are significant architectural and UX discrepancies relative to the Wispr Flow-inspired experience requested:
</p>

<ul>
  <li><b>Floating Pill UI Disconnect</b>: A sophisticated dual-tier floating pill (<code>DualTierFloatingPill</code>) exists in <code>src/voxlocal/ui/floating_pill.py</code>, but <code>main.py</code> still imports and displays the legacy vertical 8-icon dock (<code>FloatingWidget</code>). Furthermore, <code>DualTierFloatingPill</code> only displays 3 mode chips (Magic, Structured, Email) instead of the 5 modes mandated by R1 (Verbatim, Clean, Polished, Structured, Email).</li>
  <li><b>Writing Modes Nomenclature & Availability</b>: <code>DictationProcessor</code> defines <code>VERBATIM</code>, <code>CLEANED</code>, <code>MAGIC</code>, <code>STRUCTURED</code>, <code>EMAIL</code>, whereas R1 specifies Verbatim, Clean, Polished, Structured, and Email ("Polished" vs "Magic", "Clean" vs "Cleaned").</li>
  <li><b>Microsoft Outlook Calendar Integration Missing</b>: There is currently <b>zero</b> calendar integration code in the entire repository. No OAuth2 flow, no Microsoft Graph API client, no upcoming meeting advance notification, no token refresh/disconnect/recovery, and no mock calendar provider. Call detection is currently reactive only via Windows microphone consent registry polling and window title matching.</li>
  <li><b>Information Architecture Overcrowding</b>: <code>MainWindow</code> (in <code>src/voxlocal/ui/main_window.py</code>) currently presents <b>8 top-level tabs</b> (Voice Typing, Screen Recording, Meeting Studio, Reports, Ask Vox, Folders & Actions, Vocabulary, Settings) instead of the required <b>5 primary sections</b> (Dictation, Meetings, History, Vocabulary, Settings).</li>
  <li><b>Test Suite & Test Count</b>: The repository contains <b>52 test files</b> with <b>431 test functions</b> in <code>tests/</code>. Baseline verification reports (<code>TEST_REPORT.md</code>, <code>baseline_results.txt</code>, and <code>scratch/safe_tests.log</code>) demonstrate that the core test suites pass offline (e.g. 117 safe unit tests passing in 16.97s). In non-elevated sandboxed execution, the Microsoft Store Python alias in <code>.venv</code> generates Windows error 1920 (ACCESS_DENIED), but the packaged production build in <code>dist/VoxLocal/VoxLocal.exe</code> contains a complete bundled Python 3.13 runtime.</li>
</ul>

<hr>

<h2>2. Component-by-Component Technical Audit</h2>

<h3>2.1 Floating Pill UI (src/voxlocal/ui/floating_pill.py vs src/voxlocal/ui/floating_widget.py)</h3>

<b>File Path</b>: <code>src/voxlocal/ui/floating_pill.py</code> (673 lines)<br>
<b>Current Strengths</b>:
<ul>
  <li><b>Dual-Tier Architecture</b>: Implements <code>DualTierFloatingPill</code> with an Upper Tier (dismiss 'X' button <code>btn_cancel</code>, 12-bar equalizer capsule <code>DynamicWaveformCapsule</code>, elapsed monospace session timer <code>SessionTimerWidget</code>, coral stop button <code>btn_stop</code>) and Lower Tier (mode selector chips).</li>
  <li><b>Focus Invariance</b>: Accurately applies Win32 non-activating style <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>) via <code>apply_non_activating_floating_style</code>, ensuring clicking the pill never steals focus from the target application (Notepad, Word, Teams, Chrome).</li>
  <li><b>Waveform Animations</b>: <code>DynamicWaveformCapsule</code> runs at 30 FPS via <code>QTimer</code> (33ms interval) driven by microphone RMS energy levels with harmonic frequency distribution, rendering soft teal (<code>#2DD4BF</code>) to pure white (<code>#FFFFFF</code>) gradients. During the <code>PROCESSING</code> state, it morphs into an animated horizontal dotted progress indicator.</li>
  <li><b>Live Speech Preview Bubble</b>: <code>LiveSpeechPreviewBubble</code> floats 12px directly above the upper pill, displaying streaming provisional speech tokens and rendering retracted self-corrections with red strikethrough styling (<code>&lt;span style="color:#D32F2F; text-decoration:line-through;"&gt;</code>).</li>
  <li><b>State Machine</b>: Implements 5 discrete states in <code>PillState</code>: <code>IDLE</code>, <code>LISTENING</code>, <code>PROCESSING</code>, <code>COMMITTED</code> (with green flash), and <code>ERROR</code> (with red flash).</li>
</ul>

<b>Gaps & Deficiencies</b>:
<ul>
  <li><b>Not Wired into main.py</b>: <code>main.py</code> (lines 28 and 165) still imports and instantiates <code>FloatingWidget</code> from <code>voxlocal.ui.floating_widget</code>, meaning the application at runtime actually launches the legacy vertical 8-icon dock rather than <code>DualTierFloatingPill</code>!</li>
  <li><b>Missing Mode Chips</b>: <code>DualTierFloatingPill</code> only contains 3 mode buttons: <code>chip_magic</code> (Magic [Win+F1]), <code>chip_structured</code> (Structured [Win+F2]), and <code>chip_email</code> (Email [Win+F3]). <b>Verbatim</b> and <b>Clean</b> are absent from the pill UI.</li>
  <li><b>Mode Navigation</b>: <code>_cycle_next_mode</code> only cycles between 3 modes instead of 5.</li>
</ul>

<hr>

<h3>2.2 Dictation Processor (src/voxlocal/engine/dictation_processor.py)</h3>

<b>File Path</b>: <code>src/voxlocal/engine/dictation_processor.py</code> (1349 lines)<br>
<b>Current Strengths</b>:
<ul>
  <li><b>Verbal Self-Correction Detection</b>: <code>detect_verbal_self_corrections</code> uses <code>CORRECTION_MARKER_PATTERN</code> to identify sentence markers (<code>scratch/delete/undo last sentence</code>), clause markers (<code>no actually</code>, <code>actually no</code>, <code>wait sorry</code>, <code>sorry wait</code>, <code>scratch that</code>, <code>delete that</code>), and term markers (<code>i mean</code>, <code>i meant</code>, <code>correction</code>). Returns <code>clean_text</code>, <code>retracted_segments</code>, and <code>annotated_html</code> with red strikethrough.</li>
  <li><b>Conversational Filler-Word Removal</b>: <code>prune_filler_words</code> removes vocal hesitations ("um", "uh", "erm") and conversational fillers ("so basically", "you know what", "you know", "basically", and conversational "like" patterns). Verbatim mode bypasses this pruning.</li>
  <li><b>Multilingual Hindi/English/Hinglish</b>: Includes <code>DEVA_ORTHOGRAPHIC_FIXES</code> (50+ common Devanagari misspellings), <code>DEVA_TO_ENGLISH_LOANWORDS</code> (150+ corporate/tech loanwords restored from Devanagari to English Latin script), <code>HINGLISH_WORD_MAP</code> (comprehensive conversational Hindi pronoun/verb/particle mapping), and <code>devanagari_to_roman_hinglish</code> with Hindi medial schwa deletion.</li>
  <li><b>Spoken Punctuation & Numeric Formatting</b>: Converts spoken punctuation commands ("period", "comma", "new line", "new paragraph", "question mark", etc.) and formats spoken numbers, currency ($/₹/€/£), percentages, versions, and arithmetic operators.</li>
  <li><b>Whisper Hallucination Suppression</b>: Strips common Whisper silence hallucinations and trailing ellipses.</li>
</ul>

<b>Gaps & Deficiencies</b>:
<ul>
  <li><b>Mode Enumeration Mismatch</b>: <code>TransformationMode</code> uses <code>VERBATIM</code>, <code>CLEANED</code>, <code>MAGIC</code>, <code>STRUCTURED</code>, <code>EMAIL</code>. Requirement R1 specifies "Verbatim, Clean, Polished, Structured, and Email". "MAGIC" should be aliased or normalized to "POLISHED", and "CLEANED" to "CLEAN".</li>
  <li><b>Transform Implementation</b>: <code>transform_mode</code> currently uses simple heuristic regex splitting for <code>magic</code>, <code>structured</code>, and <code>email</code>. When the local LLM is available, advanced transformation can leverage local LLM inference while falling back cleanly to deterministic rules.</li>
</ul>

<hr>

<h3>2.3 Vocabulary Manager (src/voxlocal/core/vocabulary_manager.py)</h3>

<b>File Path</b>: <code>src/voxlocal/core/vocabulary_manager.py</code> (312 lines)<br>
<b>Current Strengths</b>:
<ul>
  <li><b>Dual Storage</b>: Supports SQLite persistence via <code>LocalDatabase</code> (<code>voxlocal.db</code>) with in-memory caching fallback. Thread-safe with <code>threading.RLock()</code>.</li>
  <li><b>Default Seeded Terms</b>: Pre-seeded with 40+ domain terms across products (VoxLocal, VOX, Antigravity), tech (Windchill, HubSpot, VS Code, GitHub, Faster-Whisper, PyQt6), acronyms (DRHP, CTO, CEO, SaaS, B2B, LLM, RAG, MCP, WASAPI, CI/CD, API, SQL, PR), and Hindi loanwords.</li>
  <li><b>Boundary-Safe Normalization</b>: <code>normalize_text</code> sorts terms in descending length order (longest first) to prevent sub-string conflicts, with boundary checks for both Latin alphanumeric and Unicode Devanagari.</li>
  <li><b>Context Harvesting</b>: <code>harvest_from_context</code> extracts domain terms from active window titles and meeting attendee names for dynamic real-time biasing.</li>
  <li><b>Import / Export</b>: Supports bulk import and export of terms for backup or synchronization.</li>
</ul>

<b>Gaps & Deficiencies</b>:
<ul>
  <li>Vocabulary UI in <code>MainWindow</code> is currently located in a separate top-level tab rather than being easily accessible from Dictation settings or context menus.</li>
  <li>Direct phonetic pronunciation hints / sound-alike aliases are not yet exposed as separate fields in the schema (currently uses single <code>spoken_phrase -> replacement</code> pair).</li>
</ul>

<hr>

<h3>2.4 Meeting Workspace & Intelligence (meeting_studio.py, meeting_intelligence.py, call_detector.py)</h3>

<b>File Paths</b>:
<ul>
  <li><code>src/voxlocal/ui/meeting_studio.py</code> (1087 lines)</li>
  <li><code>src/voxlocal/engine/meeting_intelligence.py</code> (3088 lines)</li>
  <li><code>src/voxlocal/audio/call_detector.py</code> (983 lines)</li>
</ul>

<b>Current Strengths</b>:
<ul>
  <li><b>3-Tab Workspace Present</b>: <code>VoxMeetingStudioWorkspace</code> already implements the Unified 3-Tab Workspace (Page 0: "✏️ My Thoughts" via <code>VoxMyThoughtsTab</code>, "🎙️ Transcript" via <code>VoxInteractiveTranscriptTab</code>, "✨ Summary" via <code>VoxSummaryDeliverablesTab</code>) while preserving the 8-tab Advanced Studio Mode on Page 1.</li>
  <li><b>My Thoughts Privacy</b>: <code>VoxMyThoughtsTab</code> provides auto-saving with 500ms debounce, audio seek timestamp markers (<code>[MM:SS]</code>), and an explicit toggle to keep notes private vs including them in the summary.</li>
  <li><b>Deliverables in Summary Tab</b>: <code>VoxSummaryDeliverablesTab</code> includes Executive Overview narrative, 7-State Decision Evolution Table (<code>VoxStructuredDecisionsWidget</code>), Grounded Action Items Matrix (<code>VoxActionItemMatrixWidget</code>), Per-Participant Action Checklists, Risks vs Blockers Register, and Scoped Ask Vox input bar.</li>
  <li><b>Follow-Up Email Modal</b>: <code>VoxFollowUpEmailDialog</code> presents synthesized executive emails with rich HTML (Outlook/webmail) and Markdown tabs, 1-click clipboard copy, and native mail client invocation (<code>mailto:</code>).</li>
  <li><b>Local Diarization & Intelligence</b>: <code>MeetingIntelligenceEngine</code> and <code>LocalWhisperTranscriber</code> support dual-track WASAPI loopback + microphone recording with speaker diarization ("You (Mic)" vs "Remote Speaker 1..N"), temporal anchor resolution, decision extraction, and risk extraction.</li>
  <li><b>Active Call Detection</b>: <code>call_detector.py</code> provides a 7-state active call detector monitoring Windows microphone consent registry (<code>ConsentStore\microphone</code>) and window stages for Teams, Zoom, Google Meet, Webex, and Slack.</li>
</ul>

<b>Gaps & Deficiencies</b>:
<ul>
  <li><b>Advance Meeting Detection Missing</b>: <code>call_detector.py</code> ONLY detects calls <i>after</i> they start and the user opens their microphone or call stage window. It has no integration with scheduled calendar events.</li>
  <li><b>Integration into Navigation</b>: Meeting Studio in <code>MainWindow</code> is currently split across tab 2 ("Meeting Studio"), tab 3 ("Reports"), and tab 5 ("Folders & Actions"), creating redundant views instead of a unified Meetings section.</li>
</ul>

<hr>

<h3>2.5 Microsoft Outlook Calendar Integration</h3>

<b>Status</b>: <b>COMPLETELY MISSING (CRITICAL GAP)</b><br>
<b>Current Codebase Reality</b>:
<ul>
  <li>There is <b>no calendar client</b> in <code>src/voxlocal/</code>.</li>
  <li>There is <b>no OAuth2 token management</b>, MSAL, or Microsoft Graph integration.</li>
  <li>There is <b>no background calendar poller</b> to discover upcoming meetings 5–10 minutes in advance.</li>
  <li>There is <b>no advance notification system</b> for upcoming calendar meetings with one-click capture initiation.</li>
  <li>There is <b>no mock calendar provider</b> for offline test suite verification.</li>
  <li>All existing occurrences of the word "outlook" in the codebase refer solely to the desktop executable name (<code>outlook.exe</code>) for window focus/clipboard injection or rich HTML export preview.</li>
</ul>

<hr>

<h3>2.6 Main Window Information Architecture (src/voxlocal/ui/main_window.py)</h3>

<b>File Path</b>: <code>src/voxlocal/ui/main_window.py</code> (6319 lines)<br>
<b>Current Top-Level Navigation Structure (8 Tabs)</b>:
<ol>
  <li><code>🎙️ Voice Typing</code> (line 514)</li>
  <li><code>🖥️ Screen Recording</code> (line 515)</li>
  <li><code>⏺ Meeting Studio</code> (line 516)</li>
  <li><code>📊 Reports</code> (line 517)</li>
  <li><code>🧠 Ask Vox</code> (line 518)</li>
  <li><code>📁 Folders & Actions</code> (line 519)</li>
  <li><code>📖 Vocabulary</code> (line 520)</li>
  <li><code>⚙️ Settings</code> (line 521)</li>
</ol>

<b>Requirement R3 Mandated Structure (5 Primary Sections)</b>:
<ol>
  <li><b>Dictation</b>: Unified voice typing, floating pill controls, writing mode configuration, live microphone level monitor.</li>
  <li><b>Meetings</b>: Meeting studio (live recording, active call capture, 3-tab workspace: My Thoughts | Transcript | Summary, Outlook upcoming meetings widget).</li>
  <li><b>History</b>: Unified archive of past dictations, meeting recordings, transcripts, summaries, and action item trackers (absorbing previous Reports and Folders & Actions tabs).</li>
  <li><b>Vocabulary</b>: Custom vocabulary dictionary, category filtering, search, import/export, and domain bias rules.</li>
  <li><b>Settings</b>: Audio hardware devices, shortcuts, theme toggle (Light/Dark), calendar authentication/status, model configuration.</li>
</ol>

<b>Secondary Capabilities Subordination</b>:
<ul>
  <li><b>Screen Recording</b>: Should be accessible as a toggle/mode within Meetings or a floating tool, rather than a top-level tab.</li>
  <li><b>Ask Vox</b>: Should be integrated into the History/Meeting workspace rather than occupying a standalone top-level tab.</li>
</ul>

<hr>

<h3>2.7 Build Scripts & Packaging (VoxLocal.spec, run_voxlocal.bat, baseline_results.txt)</h3>

<b>Audit Findings</b>:
<ul>
  <li><code>VoxLocal.spec</code>: Clean PyInstaller specification. Correctly collects <code>faster_whisper</code> assets (including <code>silero_vad_v6.onnx</code>), PyAV DLLs, CTranslate2 dynamic libraries, and ONNX runtime. Bundles <code>python313.dll</code> in <code>dist/VoxLocal/_internal/</code>.</li>
  <li><code>run_voxlocal.bat</code>: Multi-tier launch script checking <code>dist\VoxLocal\VoxLocal.exe</code> first, then <code>.venv\Scripts\pythonw.exe</code>, then system <code>python</code>.</li>
  <li><code>dist/VoxLocal/VoxLocal.exe</code>: Existing 16MB standalone Windows executable generated on 2026-11-03.</li>
  <li><code>baseline_results.txt</code>: UTF-16LE log confirming 9 baseline meeting studio suites passed 0 failures/errors on 2026-11-02.</li>
</ul>

<hr>

<h2>3. Test Suite Audit & Baseline Execution Analysis</h2>

<h3>3.1 Test Suite Inventory</h3>
<ul>
  <li><b>Total Test Files</b>: 52 files located in <code>d:\Milind\Voice Assistant\tests\</code>.</li>
  <li><b>Total Test Functions</b>: 431 test methods matching <code>def test_*</code>.</li>
  <li><b>Test Categories</b>:
    <ul>
      <li>Voice Typing & Inserter: <code>test_voice_typing_e2e.py</code>, <code>test_voice_typing_evolved.py</code>, <code>test_voice_typing_lifecycle_and_target.py</code>, <code>test_vox2_speech_intelligence.py</code>.</li>
      <li>Meeting Studio & Audio: <code>test_meeting_studio_phase3_completeness.py</code>, <code>test_meeting_studio_enterprise_phase2.py</code>, <code>test_meeting_audio.py</code>, <code>test_meeting_intelligence.py</code>, <code>test_meeting_recording_robustness.py</code>, <code>test_golden_meeting_benchmark.py</code>.</li>
      <li>Floating Pill & UI: <code>test_floating_widget_hover_info_and_meeting.py</code>, <code>test_theme_and_animations.py</code>, <code>test_ui_hardening_and_user_feedback_fixes.py</code>.</li>
      <li>Call Detection & Hands-Free: <code>test_handsfree_and_calls.py</code>, <code>test_call_only_capture_and_max_accuracy.py</code>.</li>
      <li>Hardening & Platform: <code>test_unit_suite.py</code>, <code>test_cross_feature_coexistence.py</code>, <code>test_phase6_enterprise_integrity.py</code>, <code>test_phase12_security_privacy_hardening.py</code>.</li>
    </ul>
  </li>
</ul>

<h3>3.2 Test Execution Environment & Sandbox Findings</h3>
<ul>
  <li>When running <code>.venv\Scripts\pytest.exe</code> or <code>.venv\Scripts\python.exe</code> in sandboxed/background commands, Windows reports:
  <code>Unable to create process using '"D:\Milind\Voice Assistant\.venv\Scripts\python.exe" ...': The file cannot be accessed by the system.</code></li>
  <li><b>Root Cause Identified</b>: <code>.venv\pyvenv.cfg</code> points to <code>C:\Users\milin\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe</code>. On Windows 10/11, files in <code>WindowsApps</code> are 0-byte NTFS Reparse Points (<code>IO_REPARSE_TAG_APPEXECLINK</code>) requiring UWP App Execution Alias brokering. In sandboxed subprocess tokens, the Windows kernel denies execution (Error 1920).</li>
  <li><b>Historical Verification Evidence</b>:
    <ul>
      <li><code>scratch/safe_tests.log</code> records: <b>Ran 117 tests in 16.968s — OK</b>.</li>
      <li><code>baseline_results.txt</code> records: <b>9 meeting studio suites passed (7+5+11+10+12+8+7+6 = 66 tests, 0 fails, 0 errs)</b>.</li>
      <li><code>TEST_REPORT.md</code> records: <b>Stage 3 Voice Typing E2E, Stage 4/5 Meeting Recording WASAPI E2E, and Stage 5 Unit Suite verified</b>.</li>
    </ul>
  </li>
</ul>

<hr>

<h2>4. Comprehensive Requirements Gap Matrix (R1 – R5)</h2>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%;">
  <tr style="background-color: #1E293B; color: #FFFFFF;">
    <th>Req</th>
    <th>Capability Requirement</th>
    <th>Current Implementation Status</th>
    <th>Identified Gap / Defect</th>
  </tr>
  <tr>
    <td><b>R1</b></td>
    <td>Global shortcut activation</td>
    <td>Fully Implemented</td>
    <td><code>GlobalHotkeyManager</code> in <code>win32/hotkeys.py</code> supports Toggle (Ctrl+Shift+Space) & PTT (Ctrl+Win).</td>
  </tr>
  <tr>
    <td><b>R1</b></td>
    <td>Non-activating floating recording pill</td>
    <td>Partial (Miswired)</td>
    <td><code>DualTierFloatingPill</code> exists in <code>floating_pill.py</code>, but <code>main.py</code> still launches the legacy vertical <code>FloatingWidget</code> dock.</td>
  </tr>
  <tr>
    <td><b>R1</b></td>
    <td>Five distinct writing modes</td>
    <td>Partial</td>
    <td><code>dictation_processor.py</code> has 5 modes but named (verbatim, cleaned, magic, structured, email). <code>floating_pill.py</code> only exposes 3 chips (Magic, Structured, Email); Verbatim and Clean are missing from the pill UI.</td>
  </tr>
  <tr>
    <td><b>R1</b></td>
    <td>Verbal self-correction & filler removal</td>
    <td>Fully Implemented</td>
    <td><code>detect_verbal_self_corrections</code> (red strikethrough in preview bubble) and <code>prune_filler_words</code> are robust.</td>
  </tr>
  <tr>
    <td><b>R1</b></td>
    <td>Hindi, English & Hinglish support</td>
    <td>Fully Implemented</td>
    <td>Loanword restoration, Devanagari orthographic fixes, schwa deletion, and phonetic Roman Hinglish mapping working.</td>
  </tr>
  <tr>
    <td><b>R2</b></td>
    <td>Microsoft Outlook Calendar integration</td>
    <td><b>Missing (0%)</b></td>
    <td>No calendar client, OAuth2, MS Graph API, upcoming meeting advance notification, or mock provider exists.</td>
  </tr>
  <tr>
    <td><b>R2</b></td>
    <td>One-click local meeting capture</td>
    <td>Fully Implemented</td>
    <td>Local WASAPI Loopback + Mic dual-track capture in <code>meeting_recorder.py</code> without external bots.</td>
  </tr>
  <tr>
    <td><b>R2</b></td>
    <td>3-Section Workspace (Thoughts | Transcript | Summary)</td>
    <td>Fully Implemented</td>
    <td>Implemented in <code>meeting_studio.py</code> via <code>VoxMeetingStudioWorkspace</code>, <code>VoxMyThoughtsTab</code>, <code>VoxInteractiveTranscriptTab</code>, and <code>VoxSummaryDeliverablesTab</code>.</td>
  </tr>
  <tr>
    <td><b>R2</b></td>
    <td>Follow-up emails & participant checklists</td>
    <td>Fully Implemented</td>
    <td><code>VoxFollowUpEmailDialog</code> (Rich HTML, Markdown, mailto) and per-participant checklist extractor in <code>meeting_intelligence.py</code>.</td>
  </tr>
  <tr>
    <td><b>R3</b></td>
    <td>5 Primary Sections Architecture</td>
    <td><b>Partial / Discrepancy</b></td>
    <td><code>main_window.py</code> currently has 8 top-level tabs instead of Dictation, Meetings, History, Vocabulary, Settings.</td>
  </tr>
  <tr>
    <td><b>R3</b></td>
    <td>Wispr Flow visual design & animations</td>
    <td>Partial</td>
    <td><code>DualTierFloatingPill</code> has high visual fidelity, but main window remains dense with 8 top tabs.</td>
  </tr>
  <tr>
    <td><b>R4</b></td>
    <td>Local inference & performance</td>
    <td>Fully Implemented</td>
    <td>Faster-Whisper on INT8 CPU/CUDA, SQLite database, 100% offline with zero cloud dependency.</td>
  </tr>
  <tr>
    <td><b>R5</b></td>
    <td>Strict Header Constraint (No # or *)</td>
    <td>Compliant</td>
    <td>All durable artifacts, handoffs, and analysis files strictly use HTML tags (&lt;h2&gt;, &lt;h3&gt;, &lt;h4&gt;) or bold text.</td>
  </tr>
</table>

<hr>

<h2>5. Architectural Recommendations for Implementation Agents</h2>

<ol>
  <li><b>Wire DualTierFloatingPill to Application Lifecycle</b>:
    Update <code>main.py</code>, <code>AppController</code>, and <code>MainWindow</code> to instantiate and synchronize <code>DualTierFloatingPill</code> from <code>voxlocal.ui.floating_pill</code> rather than <code>FloatingWidget</code>.
  </li>
  <li><b>Upgrade DualTierFloatingPill to 5 Modes</b>:
    Update the lower tier in <code>floating_pill.py</code> to display all 5 writing modes:
    <b>Verbatim</b> [Win+F1], <b>Clean</b> [Win+F2], <b>Polished</b> [Win+F3], <b>Structured</b> [Win+F4], and <b>Email</b> [Win+F5].
    Align <code>TransformationMode</code> enum in <code>dictation_processor.py</code> with these exact 5 modes.
  </li>
  <li><b>Implement Microsoft Outlook Calendar Module</b>:
    Create <code>src/voxlocal/calendar/</code> containing:
    <ul>
      <li><code>calendar_client.py</code>: MS Graph OAuth2 flow, token storage, token refresh, disconnect, and recovery.</li>
      <li><code>meeting_poller.py</code>: Background timer checking for meetings starting within 5–15 minutes, triggering advance notifications.</li>
      <li><code>mock_calendar_provider.py</code>: Offline mock for automated unit/integration test suites.</li>
      <li><code>calendar_widget.py</code>: UI card in the Meetings section showing upcoming meetings and connection status.</li>
    </ul>
  </li>
  <li><b>Reorganize MainWindow Information Architecture into 5 Primary Sections</b>:
    Refactor <code>main_window.py</code> tab structure:
    <ul>
      <li><b>Tab 1: Dictation</b> (Voice typing controls, writing modes, floating pill options).</li>
      <li><b>Tab 2: Meetings</b> (Meeting Studio with 3-tab workspace: My Thoughts | Transcript | Summary, Calendar widget, Screen recording secondary toggle).</li>
      <li><b>Tab 3: History</b> (Past meetings, reports, transcripts, decisions, and action trackers).</li>
      <li><b>Tab 4: Vocabulary</b> (Personal and domain vocabulary manager).</li>
      <li><b>Tab 5: Settings</b> (Audio devices, hotkeys, theme, calendar account, local speech model).</li>
    </ul>
  </li>
</ol>

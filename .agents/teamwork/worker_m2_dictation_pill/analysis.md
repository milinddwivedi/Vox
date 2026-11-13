<h2>Milestone 2 Analysis: Voice Dictation Engine & Floating UI Implementation</h2>

<p><b>Agent:</b> worker_m2_dictation_pill<br/>
<b>Milestone:</b> Milestone 2 — Voice Dictation Engine &amp; Floating UI<br/>
<b>Reference Specifications:</b> VOX_WISPR_FLOW2_ANALYSIS.md, VOX_NEW_PRODUCT_UX_SPEC.md, PROJECT.md, TEST_INFRA.md</p>

<h3>1. Executive Summary</h3>
<p>Milestone 2 evolves VOX desktop voice typing from a legacy widget into an ergonomic, zero-distraction dual-tier floating pill inspired by Wispr Flow 2. The new architecture provides 5 distinct writing transformation modes (Verbatim, Clean, Polished, Structured, Email), a 12-bar acoustic equalizer driven by a parabolic bell-curve formula, a floating live speech preview bubble with real-time crimson strikethrough markup for verbal self-corrections, strict Win32 focus invariance ensuring zero focus theft from active Windows targets, and seamless text injection.</p>

<h3>2. Architecture &amp; Component Breakdown</h3>

<h4>A. DualTierFloatingPill (src/voxlocal/ui/floating_pill.py)</h4>
<ul>
  <li><b>Upper Tier (272px x 44px):</b>
    <ul>
      <li>Dismiss/Cancel Button: '✕' (28x28px, <code>#pillCancelBtn</code>) cancels dictation session and discards audio buffer without insertion.</li>
      <li>DynamicWaveformCapsule (84x28px): 12-bar equalizer capsule running at 30 FPS. In listening state, bar heights follow the parabolic bell-curve formula: <code>center_dist = abs(i - 5.5) / 5.5</code>; <code>height_scale = max(0.15, 1.0 - (center_dist * center_dist) * (1.0 - audioRmsLevel))</code>. In processing state, morphs into a horizontal animated dotted progress wave.</li>
      <li>SessionTimerWidget: Monospace timer (52x28px for MM:SS, expands to 68px for HH:MM:SS) showing elapsed recording duration.</li>
      <li>Coral Stop Button: '■' (28x28px, <code>#E05638</code> / <code>#EF4444</code>) finalizes speech, triggers Whisper transcription and mode transformation, and injects text.</li>
    </ul>
  </li>
  <li><b>Lower Tier (340px x 36px):</b>
    <ul>
      <li>5 Writing Mode Chips:
        <ol>
          <li><b>Verbatim (Win+F1):</b> <code>chip_verbatim</code> — 100% exact acoustic transcription, zero filler pruning, zero punctuation modification.</li>
          <li><b>Clean (Win+F2):</b> <code>chip_clean</code> — Conversational filler pruning, spoken punctuation conversion, and verbal self-correction.</li>
          <li><b>Polished / Magic (Win+F3):</b> <code>chip_polished</code> (aliased to <code>chip_magic</code>) — Executive prose, capitalization, and concise phrasing.</li>
          <li><b>Structured (Win+F4):</b> <code>chip_structured</code> (selected by default) — Markdown bullet outline, thematic grouping, and action items.</li>
          <li><b>Email (Win+F5):</b> <code>chip_email</code> — Professional email format with Subject suggestion, Greeting, Body, and Sign-off.</li>
        </ol>
      </li>
      <li>Collapse / Cycle Chevron ('⬍'): Single click cycles writing modes; double click collapses lower tier.</li>
      <li>Active Chip Highlight: 1.5px solid white border, semi-transparent elevated background, bold text.</li>
    </ul>
  </li>
  <li><b>Live Speech Preview Bubble:</b>
    <ul>
      <li>Anchored 12px directly above the upper pill.</li>
      <li>Streams real-time interim speech tokens with turquoise cursor (<code>▌</code>).</li>
      <li>Retracted speech and conversational fillers are rendered with crimson red strikethrough styling: <code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;...&lt;/span&gt;</code>.</li>
    </ul>
  </li>
  <li><b>Win32 Focus Invariance:</b>
    <ul>
      <li>Window Flags: <code>FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus</code>.</li>
      <li>Attributes: <code>WA_TranslucentBackground</code>, <code>WA_ShowWithoutActivating</code>.</li>
      <li>HWND Style: <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>).</li>
      <li>All child buttons and chips enforce <code>Qt.FocusPolicy.NoFocus</code>.</li>
      <li>Result: Clicking buttons, chips, or dragging the pill NEVER steals keyboard or window focus from target applications (Notepad, Word, Teams, Chrome, Slack).</li>
    </ul>
  </li>
</ul>

<h4>B. Writing Modes &amp; Dictation Engine (src/voxlocal/engine/dictation_processor.py)</h4>
<ul>
  <li><b>TransformationMode Enum:</b> Formally supports <code>VERBATIM = "verbatim"</code>, <code>CLEAN = "clean"</code>, <code>CLEANED = "cleaned"</code>, <code>POLISHED = "polished"</code>, <code>MAGIC = "magic"</code>, <code>STRUCTURED = "structured"</code>, and <code>EMAIL = "email"</code>.</li>
  <li><b>Verbal Self-Correction Engine:</b> Detects conversational speech corrections ("scratch that", "scratch last sentence", "no actually", "wait sorry", "delete that", "I mean", "correction"). Replaces erroneous segments with corrected speech and generates annotated preview HTML with red strikethrough markup.</li>
  <li><b>Filler Word Pruning:</b> Prunes vocal and conversational fillers ("um", "uh", "you know", "like", "basically", "so basically") across Clean, Polished, Structured, and Email modes while preserving 100% exact acoustic tokens in Verbatim mode.</li>
  <li><b>Devanagari Hindi &amp; Hinglish Loanword Normalization:</b> Normalizes Devanagari Whisper outputs and maps tech/corporate loanwords accurately.</li>
  <li><b>Raw Transcript Immutability:</b> Preserves authentic <code>raw_text</code> in SQLite database while dynamic <code>final_text</code> / <code>cleaned_text</code> reflects mode transformations.</li>
</ul>

<h4>C. Runtime Wiring &amp; Global Shortcuts (main.py, src/voxlocal/win32/hotkeys.py, src/voxlocal/core/controller.py)</h4>
<ul>
  <li><b>main.py:</b> Replaced legacy <code>FloatingWidget</code> instantiation with <code>DualTierFloatingPill</code>. Connected <code>handle_finalize</code> to commit dictation and insert text into the focused target app. Connected <code>handle_cancel</code> to abort and discard.</li>
  <li><b>hotkeys.py:</b> Added <code>on_mode_switch</code> callback to <code>GlobalHotkeyManager</code>. Integrated global keyboard hook detection for Win+F1 through Win+F5, allowing users to switch writing modes instantaneously from any application.</li>
  <li><b>controller.py:</b> Added <code>active_writing_mode</code> property and <code>set_writing_mode()</code> method. Connected hotkey mode switcher to controller. Updated streaming worker to route speech through the active writing mode before natural text injection.</li>
</ul>

<h3>3. Verification &amp; Test Results</h3>
<p>All automated unit and end-to-end test suites were executed cleanly using the local Python 3.13 test runtime:</p>

<h4>Primary Required Test Suites:</h4>
<pre>
.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py
Ran 44 tests in 0.286s
OK (44/44 passed)
</pre>

<h4>Product UX, Architecture &amp; Theme Test Suite:</h4>
<pre>
.venv\Scripts\python.exe -m unittest tests/test_e2e_product_ux_and_architecture.py
Ran 11 tests in 0.225s
OK (11/11 passed)
</pre>

<h4>Combined Milestone 2 Verification:</h4>
<pre>
.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_e2e_product_ux_and_architecture.py
Ran 55 tests in 0.419s
OK (55/55 passed)
</pre>

<h4>End-to-End GUI Smoke Test:</h4>
<pre>
.venv\Scripts\python.exe main.py --smoke-test-ms 500
Exit code: 0
Clean startup, Win32 global keyboard hook installation, DualTierFloatingPill initialization, and clean shutdown.
</pre>

<h3>4. Regression Testing</h3>
<p>Executed regression tests covering micro-interactions, shortcuts, and floating widget compatibility:</p>
<pre>
.venv\Scripts\python.exe -m unittest tests/test_micro_interactions_and_shortcuts.py tests/test_floating_widget_hover_info_and_meeting.py tests/test_ui_hardening_and_user_feedback_fixes.py
Ran 19 tests in 5.918s
OK (19/19 passed)
</pre>

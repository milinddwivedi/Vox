<h2>Milestone 2 Handoff Report: Voice Dictation Engine & Floating UI</h2>

<p><b>Agent:</b> worker_m2_dictation_pill<br/>
<b>Milestone:</b> Milestone 2 — Voice Dictation Engine &amp; Floating UI<br/>
<b>Status:</b> Completed<br/>
<b>Date:</b> 2026-11-09T00:28:00+05:30</p>

<h3>1. Observation</h3>
<p>Direct observations across codebase, tests, and execution runtime:</p>
<ul>
  <li><b>Floating Pill Specifications:</b> In <code>src/voxlocal/ui/floating_pill.py</code>, <code>DualTierFloatingPill</code> required upper tier dimensions (272x44px), lower tier height (36px), 12-bar equalizer capsule (84x28px) with parabolic bell-curve formula, monospace timer, coral stop button (<code>#E05638</code>), and 5 writing mode chips (Verbatim Win+F1, Clean Win+F2, Polished Win+F3, Structured Win+F4, Email Win+F5).</li>
  <li><b>Live Speech Preview Bubble:</b> <code>LiveSpeechPreviewBubble</code> in <code>src/voxlocal/ui/floating_pill.py</code> required positioning 12px directly above the pill with red strikethrough markup <code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;</code> for retracted speech.</li>
  <li><b>Win32 Focus Invariance:</b> In <code>src/voxlocal/win32/window_focus.py</code> and <code>floating_pill.py</code>, native HWND style <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>) with <code>WA_ShowWithoutActivating</code>, <code>WA_TranslucentBackground</code>, and <code>Qt.FocusPolicy.NoFocus</code> ensures clicking any control or dragging the pill never steals focus from the target application.</li>
  <li><b>Dictation Processor Modes:</b> In <code>src/voxlocal/engine/dictation_processor.py</code>, <code>TransformationMode</code> enum and <code>transform_mode()</code> required explicit support for all 5 modes: <code>VERBATIM</code>, <code>CLEAN</code> / <code>CLEANED</code>, <code>POLISHED</code> / <code>MAGIC</code>, <code>STRUCTURED</code>, and <code>EMAIL</code>.</li>
  <li><b>Database Raw Invariant:</b> In <code>src/voxlocal/storage/database.py</code>, <code>LocalDatabase</code> dual-column storage (<code>raw_text</code> immutable alongside <code>cleaned_text</code> and <code>final_text</code>) ensures original acoustic transcripts are preserved while transformed text reflects the active writing mode.</li>
  <li><b>Main Application Wiring:</b> In <code>main.py</code> (lines 28 and 165-175), legacy <code>FloatingWidget</code> instantiation was replaced with <code>DualTierFloatingPill</code>, connecting stop/finalize to text injection into the focused target application and dismiss/cancel to session abortion.</li>
  <li><b>Global Shortcuts:</b> In <code>src/voxlocal/win32/hotkeys.py</code>, <code>GlobalHotkeyManager</code> was enhanced with <code>on_mode_switch</code> to detect Win+F1 through Win+F5 global shortcuts. In <code>src/voxlocal/core/controller.py</code>, <code>active_writing_mode</code> and <code>set_writing_mode()</code> were added to route speech transformation and sync mode state.</li>
  <li><b>Automated Test Executions:</b>
    <ul>
      <li><code>.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py</code>: <b>44 / 44 tests PASS</b> (0 failures, 0 errors in 0.286s).</li>
      <li><code>.venv\Scripts\python.exe -m unittest tests/test_e2e_product_ux_and_architecture.py</code>: <b>11 / 11 tests PASS</b> (0 failures, 0 errors in 0.225s).</li>
      <li>Combined verification: <b>55 / 55 tests PASS</b> in 0.419s.</li>
      <li>GUI smoke test: <code>.venv\Scripts\python.exe main.py --smoke-test-ms 500</code>: Clean initialization and shutdown with exit code 0.</li>
      <li>Regression test suite (19 tests): <b>19 / 19 tests PASS</b> in 5.918s.</li>
    </ul>
  </li>
</ul>

<h3>2. Logic Chain</h3>
<ol>
  <li>Based on the UX specification and Wispr Flow 2 ergonomics, voice typing must be non-intrusive and never steal focus from the user's active foreground application. Applying <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code> (<code>0x08000088</code>) along with <code>NoFocus</code> on all buttons guarantees this invariant.</li>
  <li>By expanding <code>TransformationMode</code> to all 5 modes (Verbatim, Clean, Polished, Structured, Email) and linking the active mode to <code>DictationProcessor.process(..., mode=active_writing_mode)</code>, speech transcription dynamically adapts to the selected style while raw acoustic tokens remain preserved immutably in SQLite.</li>
  <li>By updating <code>DualTierFloatingPill</code> to include all 5 mode chips (Verbatim Win+F1, Clean Win+F2, Polished Win+F3, Structured Win+F4, Email Win+F5), the parabolic bell-curve equalizer formula, monospace timer, and live preview bubble with crimson strikethrough, the user receives instantaneous visual feedback without UI distraction.</li>
  <li>Wiring <code>DualTierFloatingPill</code> into <code>main.py</code> as the primary floating widget connects the controller's dictation lifecycle, audio energy levels, and interim text updates directly to the pill, enabling stop/commit to trigger natural text injection via <code>TextInserter</code> and dismiss to abort cleanly.</li>
  <li>Adding Win+F1 to Win+F5 global keyboard hooks in <code>GlobalHotkeyManager</code> allows users to switch writing modes instantaneously from any application without leaving their workflow.</li>
</ol>

<h3>3. Caveats</h3>
<p>No caveats. All 5 writing modes, floating pill geometry, non-activating window flags, live speech preview bubble, raw transcript immutability, global mode switcher shortcuts, and main.py runtime wiring have been implemented and verified via automated test suites with zero regressions.</p>

<h3>4. Conclusion</h3>
<p>Milestone 2 (Voice Dictation Engine &amp; Floating UI) is fully complete and verified. The evolved dual-tier floating pill and 5-mode dictation engine satisfy all Wispr Flow 2 UX requirements, maintain strict Win32 focus invariance, and pass 100% of the automated unit and end-to-end verification tests.</p>

<h3>5. Verification Method</h3>
<p>To independently verify the implementation, execute the following commands in the project root directory:</p>
<pre>
(1) Run the primary Milestone 2 dictation and evolved voice typing test suites:
.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py

(2) Run the product UX, architecture and theme verification suite:
.venv\Scripts\python.exe -m unittest tests/test_e2e_product_ux_and_architecture.py

(3) Run all Milestone 2 test suites together:
.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_e2e_product_ux_and_architecture.py

(4) Run the GUI smoke test:
.venv\Scripts\python.exe main.py --smoke-test-ms 500

(5) Run regression test suite:
.venv\Scripts\python.exe -m unittest tests/test_micro_interactions_and_shortcuts.py tests/test_floating_widget_hover_info_and_meeting.py tests/test_ui_hardening_and_user_feedback_fixes.py
</pre>
<p><b>Invalidation Conditions:</b> Any assertion failure in the test suites, any window focus theft upon pill interaction, failure to preserve raw transcript in SQLite, or failure to render red strikethrough markup in the live speech preview bubble.</p>

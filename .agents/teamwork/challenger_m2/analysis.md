<h2>Adversarial Challenge Analysis: Milestone 2 Voice Dictation Engine & Floating UI</h2>

<p><b>Author:</b> teamwork_challenger (critic, specialist)<br>
<b>Target Milestone:</b> Milestone 2 — Voice Dictation Engine & Floating UI<br>
<b>Verdict:</b> REQUEST_CHANGES</p>

<h3>Executive Summary</h3>
<p>An exhaustive adversarial challenge was conducted across the five required target dimensions of Milestone 2:
Five Writing Modes, Verbal Self-Correction Detection & Negation Guards, Multilingual & Hinglish Robustness, Win32 Focus Invariance & Geometry, and the designated test suite execution command.
While the floating UI architecture, Win32 extended window styles, and Hinglish transliteration demonstrate solid baseline implementations, empirical testing uncovered four significant defects, including test suite command failures, compound retraction parsing failures, false-positive negation triggers, and Verbatim mode punctuation leaks.</p>

<h3>Challenge Vector 1: Test Suite Command Execution & Qt Platform Pollution</h3>

<h4>1. Observation</h4>
<p>Running the project-mandated test execution command:</p>
<pre><code>.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_adversarial_vox_capabilities.py</code></pre>
<p>Exits with code 1 and produces two test failures:</p>
<pre><code>======================================================================
FAIL: test_v3_01_floating_pill_focus_invariance (tests.test_adversarial_vox_capabilities.TestAdversarialVoxCapabilities.test_v3_01_floating_pill_focus_invariance)
Verify DualTierFloatingPill has Win32 non-activating styles and Qt NoFocus policy.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "D:\Milind\Voice Assistant\tests\test_adversarial_vox_capabilities.py", line 352, in test_v3_01_floating_pill_focus_invariance
    self.assertTrue(bool(ex_style & WS_EX_NOACTIVATE), "WS_EX_NOACTIVATE must be present")
AssertionError: False is not true : WS_EX_NOACTIVATE must be present

======================================================================
FAIL: test_v3_02_preview_bubble_focus_invariance (tests.test_adversarial_vox_capabilities.TestAdversarialVoxCapabilities.test_v3_02_preview_bubble_focus_invariance)
Verify LiveSpeechPreviewBubble has Win32 non-activating styles and Qt NoFocus policy.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "D:\Milind\Voice Assistant\tests\test_adversarial_vox_capabilities.py", line 373, in test_v3_02_preview_bubble_focus_invariance
    self.assertTrue(bool(ex_style & WS_EX_NOACTIVATE), "WS_EX_NOACTIVATE must be present")
AssertionError: False is not true : WS_EX_NOACTIVATE must be present

Ran 59 tests in 0.436s
FAILED (failures=2)
</code></pre>

<h4>2. Root Cause Analysis</h4>
<p>In <code>tests/test_e2e_dictation_and_modes.py</code> (lines 30-31 and 47-53):</p>
<pre><code>os.environ["QT_QPA_PLATFORM"] = "offscreen"
_APP_INSTANCE = QApplication(["--platform", "offscreen"])
</code></pre>
<p>Because <code>QApplication</code> is a process-wide singleton, initializing it with <code>--platform offscreen</code> permanently forces Qt into offscreen rendering mode for the entire Python test process. In Qt's offscreen platform (<code>qoffscreen.dll</code>), widgets do not create real Win32 top-level windows (user32 HWNDs). When <code>apply_non_activating_floating_style(hwnd)</code> calls <code>SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ...)</code>, the Win32 API rejects the fake handle (error 1400: Invalid window handle). Consequently, downstream tests in <code>tests/test_adversarial_vox_capabilities.py</code> fail when inspecting <code>WS_EX_NOACTIVATE</code>.</p>
<p>When run in reverse order or individually, the tests pass because <code>test_adversarial_vox_capabilities.py</code> initializes <code>QApplication(sys.argv)</code> with the native <code>qwindows.dll</code> platform. However, the exact command requested in the milestone prompt fails.</p>

<h3>Challenge Vector 2: Verbal Self-Correction Detection & Compound Retractions</h3>

<h4>1. Defect A: Unhandled Compound Retraction Marker "no wait scratch that"</h4>
<p><b>Observation:</b> The prompt specifically targets the compound retraction phrase <i>"no wait scratch that"</i>. In <code>src/voxlocal/engine/dictation_processor.py</code> (lines 593-598), <code>CORRECTION_MARKER_PATTERN</code> defines:</p>
<pre><code>CORRECTION_MARKER_PATTERN = re.compile(
    r"(?P&lt;sentence_marker&gt;[,;]?\s*\b(?:scratch\s+last\s+sentence|delete\s+last\s+sentence|undo\s+last\s+sentence)\b[.\s]*)"
    r"|(?P&lt;clause_marker&gt;[,;]?\s*\b(?:no\s+actually|actually\s+no|wait\s+sorry|sorry\s+wait|scratch\s+that|delete\s+that|undo\s+that)\b[,\s]*)"
    r"|(?P&lt;term_marker&gt;[,;]?\s*\b(?:i\s+mean|i\s+meant|correction)\b[,\s]*)",
    re.IGNORECASE,
)
</code></pre>
<p>Empirical Test Input:</p>
<pre><code>input_text = "I will attend on Monday no wait scratch that Tuesday"
res = DictationProcessor.detect_verbal_self_corrections(input_text)
// Result:
clean_text = "I will attend on Monday no Tuesday"
retracted_segments = ["wait scratch that"]
</code></pre>
<p><b>Impact:</b> The target erroneous word <code>"Monday"</code> and the marker fragment <code>"no"</code> were NOT retracted! The resulting committed text is nonsensical: <i>"I will attend on Monday no Tuesday"</i>.</p>

<h4>2. Defect B: Failure to Retract Numerical Clauses with "no wait scratch that"</h4>
<p>Empirical Test Input:</p>
<pre><code>input_text = "The price is fifty dollars no wait scratch that sixty dollars"
res = DictationProcessor.detect_verbal_self_corrections(input_text)
// Result:
clean_text = "The price is fifty dollars sixty dollars"
retracted_segments = ["no wait scratch that"]
</code></pre>
<p><b>Impact:</b> The retracted marker was removed, but the preceding phrase <code>"fifty dollars"</code> was NOT stripped from the clean output. Both amounts remain present in the output.</p>

<h4>3. Defect C: False-Positive Negation Leak on "no scratch that"</h4>
<p>Empirical Test Input:</p>
<pre><code>input_text = "This file has no scratch that can be seen"
res = DictationProcessor.detect_verbal_self_corrections(input_text)
// Result:
clean_text = "This can be seen"
retracted_segments = ["file has no scratch that"]
has_corrections = True
</code></pre>
<p><b>Impact:</b> In sentences where "scratch that" is part of standard conversational syntax (e.g., "no scratch that can be seen" or "there is no scratch that I know of"), the clause marker eagerly matches and destroys valid user text.</p>

<h3>Challenge Vector 3: Five Writing Modes & Punctuation Invariant</h3>

<h4>1. Verbatim Mode Spoken Punctuation & Number Formatting Leak</h4>
<p><b>Observation:</b> In <code>src/voxlocal/engine/dictation_processor.py</code> (lines 1236-1239):</p>
<pre><code>mode_str = str(getattr(mode, "value", mode)).lower()
if mode_str == "verbatim":
    cleanup_enabled = False
</code></pre>
<p>However, <code>spoken_punctuation</code> and <code>format_numeric_entities</code> are guarded separately:</p>
<pre><code>if spoken_punctuation:
    for pattern_str, symbol in cls.SPOKEN_PUNCTUATION_RULES:
        ...
text = cls.format_numeric_entities(text)
</code></pre>
<p>Neither <code>spoken_punctuation</code> nor <code>format_numeric_entities</code> checks whether <code>mode == "verbatim"</code>. Consequently, calling <code>DictationProcessor.process(text, mode="verbatim")</code> with default arguments (which is how <code>StreamingTyper.finalize_session</code> calls it) converts:</p>
<pre><code>"The price is fifty dollars comma and this is verbatim full stop"
-> "The price is $50, and this is verbatim."
</code></pre>
<p><b>Impact:</b> Verbatim mode violates its interface contract of preserving the exact raw acoustic transcript without punctuation or entity alteration, unless callers remember to manually pass <code>spoken_punctuation=False</code>.</p>

<h4>2. Mode Switching Dynamics & Buffer Invariant</h4>
<p>Rapid switching across all five modes (Verbatim, Clean, Polished, Structured, Email) was stressed 100 times in memory. The mode switching state machine operates reliably and does not corrupt raw speech in memory.</p>

<h3>Challenge Vector 4: Multilingual & Hinglish Robustness</h3>
<p>Empirical tests confirmed robust behavior across mixed Hindi-English phrases:</p>
<ul>
<li><code>"aaj ka meeting prepone karo"</code> -> Preserves <code>prepone</code> and proper capitalization.</li>
<li><code>"PR merge ho gaya kya"</code> -> Preserves <code>PR merge</code>.</li>
<li><code>"screen share dekh rahe ho"</code> -> Preserves <code>screen share</code>.</li>
<li><code>"स्क्रीन शेयर करके कोड रिव्यू करते हैं"</code> -> Transliterates to <code>"screen share karke code review karte hain"</code> with loanword restoration.</li>
</ul>

<h3>Challenge Vector 5: Win32 Focus Invariance & Geometry</h3>
<p>When tested in native Windows GUI environment:</p>
<ul>
<li><b>Window Styles:</b> Both <code>DualTierFloatingPill</code> and <code>LiveSpeechPreviewBubble</code> report extended style <code>0x08080088</code>, satisfying <code>WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW</code>.</li>
<li><b>Focus Invariance:</b> All child chips (<code>chip_verbatim</code>, <code>chip_magic</code>, <code>chip_structured</code>, <code>chip_email</code>), buttons (<code>btn_cancel</code>, <code>btn_stop</code>), and mouse drag events enforce <code>Qt.FocusPolicy.NoFocus</code> and never steal keyboard focus from an active external Notepad/browser caret.</li>
<li><b>Live Speech Preview:</b> Live preview bubble correctly formats retracted speech with red strikethrough styling <code>&lt;span style="color:#D32F2F; text-decoration:line-through; font-weight:500;"&gt;...&lt;/span&gt;</code>.</li>
</ul>

<h3>Required Remediation Items</h3>
<ol>
<li><b>Fix Qt Platform Pollution in Tests:</b> In <code>tests/test_e2e_dictation_and_modes.py</code>, remove <code>os.environ["QT_QPA_PLATFORM"] = "offscreen"</code> and standardise on <code>app = QApplication.instance() or QApplication(sys.argv)</code> so test suites can run together in any order without failing Win32 style assertions.</li>
<li><b>Add Compound Retraction "no wait scratch that":</b> Update <code>CORRECTION_MARKER_PATTERN</code> to match <code>(?:no\s+wait\s+scratch\s+that|wait\s+scratch\s+that|no\s+scratch\s+that)</code> and ensure preceding target clauses are cleanly excised.</li>
<li><b>Harden Negation Guard against False Positives:</b> Prevent phrases like <code>"has no scratch that"</code> or <code>"there is no scratch that"</code> from triggering clause retractions.</li>
<li><b>Enforce Verbatim Mode Spoken Punctuation Invariant:</b> In <code>DictationProcessor.process</code>, if <code>mode_str == "verbatim"</code>, automatically disable <code>spoken_punctuation</code> and bypass numeric formatting unless explicitly enabled.</li>
</ol>

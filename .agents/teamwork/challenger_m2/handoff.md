<h2>Handoff Report — Milestone 2 Adversarial Stress Testing</h2>

<p><b>Agent:</b> teamwork_challenger (critic, specialist)<br>
<b>Target:</b> Milestone 2 — Voice Dictation Engine & Floating UI<br>
<b>Verdict:</b> REQUEST_CHANGES</p>

<h3>1. Observation</h3>

<p><b>Observation 1 (Test Suite Execution Failure):</b></p>
<p>Executing the designated test command:</p>
<pre><code>.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_adversarial_vox_capabilities.py</code></pre>
<p>Verbatim output:</p>
<pre><code>FAIL: test_v3_01_floating_pill_focus_invariance (tests.test_adversarial_vox_capabilities.TestAdversarialVoxCapabilities.test_v3_01_floating_pill_focus_invariance)
AssertionError: False is not true : WS_EX_NOACTIVATE must be present

FAIL: test_v3_02_preview_bubble_focus_invariance (tests.test_adversarial_vox_capabilities.TestAdversarialVoxCapabilities.test_v3_02_preview_bubble_focus_invariance)
AssertionError: False is not true : WS_EX_NOACTIVATE must be present

FAILED (failures=2)
Ran 59 tests in 0.436s
</code></pre>

<p><b>Observation 2 (Test Process Pollution):</b></p>
<p>File <code>tests/test_e2e_dictation_and_modes.py</code> lines 30-31 and 47-53 contains:</p>
<pre><code>os.environ["QT_QPA_PLATFORM"] = "offscreen"
...
def get_qapp() -&gt; QApplication:
    global _APP_INSTANCE
    app = QApplication.instance()
    if app is None:
        _APP_INSTANCE = QApplication(["--platform", "offscreen"])
        return _APP_INSTANCE
    return app
</code></pre>
<p>When run first in a multi-file unittest invocation, this permanently sets the process-wide Qt platform plugin to <code>offscreen</code>, invalidating real Win32 HWND queries in downstream test files.</p>

<p><b>Observation 3 (Compound Retraction Failure):</b></p>
<p>In <code>src/voxlocal/engine/dictation_processor.py</code> lines 593-598, <code>CORRECTION_MARKER_PATTERN</code> defines:</p>
<pre><code>CORRECTION_MARKER_PATTERN = re.compile(
    r"(?P&lt;sentence_marker&gt;[,;]?\s*\b(?:scratch\s+last\s+sentence|delete\s+last\s+sentence|undo\s+last\s+sentence)\b[.\s]*)"
    r"|(?P&lt;clause_marker&gt;[,;]?\s*\b(?:no\s+actually|actually\s+no|wait\s+sorry|sorry\s+wait|scratch\s+that|delete\s+that|undo\s+that)\b[,\s]*)"
    r"|(?P&lt;term_marker&gt;[,;]?\s*\b(?:i\s+mean|i\s+meant|correction)\b[,\s]*)",
    re.IGNORECASE,
)
</code></pre>
<p>Testing compound retraction <code>"no wait scratch that"</code>:</p>
<pre><code>input_text = "I will attend on Monday no wait scratch that Tuesday"
res = DictationProcessor.detect_verbal_self_corrections(input_text)
// Clean: "I will attend on Monday no Tuesday"
// Retracted: ["wait scratch that"]
</code></pre>
<p>The target phrase <code>"Monday"</code> and preceding word <code>"no"</code> are not retracted.</p>

<p><b>Observation 4 (False-Positive Negation Leak):</b></p>
<pre><code>input_text = "This file has no scratch that can be seen"
res = DictationProcessor.detect_verbal_self_corrections(input_text)
// Clean: "This can be seen"
// Retracted: ["file has no scratch that"]
// has_corrections: True
</code></pre>
<p>Valid user speech is corrupted because <code>no scratch that</code> triggers false-positive retraction.</p>

<p><b>Observation 5 (Verbatim Mode Punctuation Conversion):</b></p>
<p>In <code>src/voxlocal/engine/dictation_processor.py</code> line 1237:</p>
<pre><code>mode_str = str(getattr(mode, "value", mode)).lower()
if mode_str == "verbatim":
    cleanup_enabled = False
</code></pre>
<p>Lines 1284-1294 run <code>spoken_punctuation</code> and <code>format_numeric_entities</code> without checking if <code>mode_str == "verbatim"</code>. Consequently, <code>DictationProcessor.process("total is fifty dollars comma verbatim full stop", mode="verbatim")</code> outputs <code>"total is $50, verbatim."</code> instead of preserving exact raw acoustic speech.</p>

<h3>2. Logic Chain</h3>
<ol>
<li><b>Step 1 (From Observation 1 &amp; 2):</b> <code>test_e2e_dictation_and_modes.py</code> injects <code>QT_QPA_PLATFORM=offscreen</code> at module load time. Because Qt's <code>QApplication</code> instance cannot be reconfigured once created within a single OS process, all tests that run afterward inherit an offscreen platform. In offscreen platform, Win32 API <code>SetWindowLongPtrW</code> rejects the mock HWNDs, leaving <code>WS_EX_NOACTIVATE</code> unset. Therefore, running the multi-file test suite command fails.</li>
<li><b>Step 2 (From Observation 3):</b> The regex <code>CORRECTION_MARKER_PATTERN</code> lacks the compound phrase <code>no wait scratch that</code>. When encountered, regex matching splits the phrase into a partial match on <code>wait scratch that</code>, stranding the word <code>no</code> and failing to calculate the proper preceding clause boundary, which leaves the retracted target (e.g. <code>Monday</code>) in the output text.</li>
<li><b>Step 3 (From Observation 4):</b> The negation guard in <code>detect_verbal_self_corrections</code> only checks for negative auxiliaries (e.g. <code>don't</code>, <code>never</code>) preceding the marker, but does not guard against attributive uses of <code>no &lt;noun&gt; that</code>. Consequently, <code>no scratch that</code> matches as a retraction marker and deletes valid words.</li>
<li><b>Step 4 (From Observation 5):</b> The contract for Verbatim mode requires 100% exact acoustic speech preservation without filler pruning, spoken punctuation conversion, or numeric entity formatting. However, only <code>cleanup_enabled</code> is disabled; spoken punctuation rules and numeric formatters continue to execute on verbatim inputs.</li>
<li><b>Step 5 (Synthesis to Verdict):</b> Since the test suite command fails and two primary functional requirements (accurate self-correction of compound retractions and verbatim text preservation) have verified edge-case defects, Milestone 2 cannot be approved in its current state.</li>
</ol>

<h3>3. Caveats</h3>
<p>The core UI design, 12-bar equalizer animation, dual-tier layout, Win32 <code>WS_EX_NOACTIVATE</code> integration under native Windows execution, and Hinglish transliteration pipeline are well-architected and pass their individual test suites. The defects found are localized to test harness platform isolation, regex marker completeness, and parameter gating in <code>DictationProcessor</code>.</p>

<h3>4. Conclusion &amp; Verdict</h3>
<p><b>Verdict: REQUEST_CHANGES</b></p>
<p>The implementation requires the following specific changes before Milestone 2 can be approved:</p>
<ol>
<li>Update <code>tests/test_e2e_dictation_and_modes.py</code> to use native <code>QApplication.instance() or QApplication(sys.argv)</code> without forcing <code>QT_QPA_PLATFORM="offscreen"</code>, so the combined test suite command passes cleanly.</li>
<li>Add compound retraction phrases including <code>no wait scratch that</code> to <code>CORRECTION_MARKER_PATTERN</code> and ensure preceding target words/clauses are cleanly removed.</li>
<li>Add negation guards for phrases like <code>has no scratch that</code> to avoid false-positive retractions.</li>
<li>In <code>DictationProcessor.process</code>, automatically disable <code>spoken_punctuation</code> and <code>format_numeric_entities</code> when <code>mode_str == "verbatim"</code>.</li>
</ol>

<h3>5. Verification Method</h3>
<p>To verify the fixes:</p>
<ol>
<li>Run the exact project test suite:
<pre><code>.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_adversarial_vox_capabilities.py</code></pre>
Expected: Ran 59 tests, 0 failures, 0 errors.</li>
<li>Run the empirical challenger stress suite:
<pre><code>.venv\Scripts\python.exe -m unittest tests/test_adversarial_m2_empirical.py</code></pre>
Expected: All tests pass, including compound retractions and negation guards.</li>
<li>Invalidation conditions: Any test failure or assertion failure in the commands above indicates the defects remain unresolved.</li>
</ol>

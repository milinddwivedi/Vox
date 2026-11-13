<h2>Progress Log — Milestone 2 Remediation</h2>

<p><b>Last visited:</b> 2026-11-08T19:51:00Z<br>
<b>Status:</b> All Remediations Complete & Verified</p>

<h3>Milestones & Tasks</h3>
<ul>
<li>[x] 1. QPA Environment Pollution in Multi-Suite Runs (tests/test_e2e_dictation_and_modes.py)
  <ul>
    <li>Removed module-level unconditional <code>os.environ["QT_QPA_PLATFORM"] = "offscreen"</code></li>
    <li>Preserved and restored environment in setUpClass/tearDownClass</li>
    <li>Instantiated native <code>QApplication(sys.argv)</code> in get_qapp() so downstream Win32 extended style tests retain valid HWNDs</li>
  </ul>
</li>
<li>[x] 2. Compound Verbal Retraction & Numerical Retractions (src/voxlocal/engine/dictation_processor.py)
  <ul>
    <li>Enhanced <code>CORRECTION_MARKER_PATTERN</code> with compound markers: "no wait scratch that", "scratch that no actually", "no actually scratch that", "wait scratch that", "actually wait scratch that"</li>
    <li>Added <code>_is_numeric_or_currency_token</code> helper and numerical span retraction for currency/quantifier pairs (e.g. "fifty dollars no wait scratch that sixty dollars" -> "sixty dollars")</li>
  </ul>
</li>
<li>[x] 3. False-Positive Negation Guard (src/voxlocal/engine/dictation_processor.py)
  <ul>
    <li>Added <code>is_existential_scratch</code> guard when "no scratch that" is preceded by determiners/verbs like "has", "have", "had", "is", "was", "there", "found", "saw", "see"</li>
    <li>Verified that "This file has no scratch that can be seen" remains unmodified with no false corrections</li>
  </ul>
</li>
<li>[x] 4. Verbatim Mode Spoken Punctuation (src/voxlocal/engine/dictation_processor.py)
  <ul>
    <li>Updated <code>DictationProcessor.process</code> signature with <code>spoken_punctuation: bool | None = None</code></li>
    <li>When mode is verbatim, defaulted <code>spoken_punctuation = False</code> unless explicitly passed as True</li>
    <li>Bypassed numeric entity formatting in verbatim mode to guarantee 100% literal acoustic transcript preservation</li>
  </ul>
</li>
<li>[x] 5. Run & Verify Test Suites
  <ul>
    <li>Ran <code>.venv\Scripts\python.exe -m unittest tests/test_adversarial_m2_empirical.py</code> -> 13 tests passed cleanly</li>
    <li>Ran <code>.venv\Scripts\python.exe -m unittest tests/test_e2e_dictation_and_modes.py tests/test_voice_typing_evolved.py tests/test_adversarial_vox_capabilities.py</code> -> 59 tests passed cleanly</li>
    <li>Ran combined 4 suites -> 72 tests passed cleanly</li>
  </ul>
</li>
</ul>

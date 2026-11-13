<h2>DISPATCH — 2026-11-09T01:30:00Z</h2>

<h3>Objective</h3>
<p>Reorganize VOX MainWindow (<code>src/voxlocal/ui/main_window.py</code>) into the 5 primary workspaces (Dictation, Meetings, History, Vocabulary, Settings) with Light/Dark theme support, contextual embedding of secondary capabilities (Screen Recording, Ask Vox, Ctrl+K Command Palette), and performance optimizations as defined in <code>PROJECT.md</code> and <code>VOX_NEW_PRODUCT_UX_SPEC.md</code>.</p>

<h3>Required Scope &amp; Files</h3>
<ul>
  <li><code>src/voxlocal/ui/main_window.py</code>: MainWindow 5-workspace restructure.</li>
  <li><code>src/voxlocal/ui/theme.py</code>: Theme tokens and palettes.</li>
  <li><code>tests/test_e2e_product_ux_and_architecture.py</code>: Test suite to verify and pass.</li>
  <li><code>PROJECT.md</code>: Project specification and contracts.</li>
  <li><code>ORIGINAL_REQUEST.md</code>: Verbatim user requirements.</li>
</ul>

<h3>Mandatory Constraints</h3>
<ul>
  <li><b>NO MARKDOWN HEADERS:</b> Under NO circumstances may any header begin with markdown <code>#</code> or <code>*</code> across all artifacts, direct responses, and durable files (all headers must use HTML tags <code>&lt;h2&gt;</code>, <code>&lt;h3&gt;</code>, <code>&lt;h4&gt;</code> or bold text).</li>
  <li><b>INTEGRITY WARNING:</b> DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.</li>
</ul>

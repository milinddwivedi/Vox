<h2>VOX — Deep Reconnaissance &amp; Wispr Flow 1.6.1457 Parity Specification</h2>

<p><strong>Benchmark Reference:</strong> Installed Wispr Flow Desktop (<code>1.6.1457</code>, inspected read-only from <code>C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar</code> and AppData schemas).<br>
<strong>Product Identity:</strong> Rebuilt natively in Python/PyQt6 under the <strong>VOX</strong> product name using VOX's local ASR, diarization, bilingual EN/HI/Hinglish post-processing, Win32 focus-invariant insertion, call-only video/audio capture, and Microsoft Calendar PKCE integration—with zero copied proprietary assets or binaries.</p>

<h3>1. Visual System, Palette Tokens &amp; Typography</h3>

<h4>Light Mode Tokens (Default Hub Surface)</h4>
<ul>
  <li><code>--sand-50</code>: <code>#fcfcfb</code> (Primary app/Hub background and popover background)</li>
  <li><code>--sand-100</code>: <code>#faf9f7</code> (Hover surface and card tint)</li>
  <li><code>--sand-200</code>: <code>#f8f7f3</code> (Row / filter hover tint)</li>
  <li><code>--sand-500</code>: <code>#f5f4f0</code> (Subtle content well / secondary background tertiary)</li>
  <li><code>--sand-600</code>: <code>#eeebe3</code> (Primary border, divider, pill well background)</li>
  <li><code>--sand-700</code>: <code>#c5c0b3</code> (Toggle / dropzone border)</li>
  <li><code>--sand-800</code>: <code>#a39d8e</code> (Muted icon / secondary control border)</li>
  <li><code>--sand-900</code>: <code>#736e62</code> (Warm muted secondary label)</li>
  <li><code>--sand-950</code>: <code>#4d4a42</code> (Focus ring / dark FlowBar button fill)</li>
  <li><code>--pure-white</code>: <code>#ffffff</code> (Card surface, keycap background)</li>
  <li><code>--vast-950</code>: <code>#1a1a1a</code> (Primary foreground text and primary dark CTA fill)</li>
  <li><code>--vast-600</code>: <code>#71716e</code> (Tertiary foreground text / timestamps)</li>
  <li><code>--keycap-drop</code>: <code>#d3ced6</code> (Keycap subtle drop border)</li>
</ul>

<h4>Dark Mode Tokens</h4>
<ul>
  <li><code>--vast-950</code>: <code>#1a1a1a</code> (Dark canvas background)</li>
  <li><code>--vast-925</code>: <code>#202020</code> (Tertiary surface)</li>
  <li><code>--vast-900</code>: <code>#30302f</code> (Secondary surface &amp; borders)</li>
  <li><code>--vast-800</code>: <code>#464544</code> (Keycap / hover surface)</li>
  <li><code>--vast-700</code>: <code>#5b5b59</code> (Keycap edge / divider)</li>
  <li><code>--vast-50</code> / <code>--vast-100</code> / <code>--vast-400</code>: <code>#e9e8e1</code> / <code>#deddd7</code> / <code>#9d9c98</code> (Text hierarchy)</li>
  <li><code>--pure-black</code>: <code>#000000</code> (Elevated dark card &amp; FlowBar pill surface)</li>
  <li><code>--destructive</code>: <code>#ff5757</code> (Coral/red stop &amp; error highlight)</li>
</ul>

<h4>Signature Accents</h4>
<ul>
  <li><strong>Dawn (Lavender / Plum):</strong> <code>#fcf7ff</code> (100), <code>#f7ebff</code> (300), <code>#f0d7ff</code> (500 — signature pill CTA fill), <code>#d2ace9</code> (600), <code>#a26ec1</code> (700), <code>#502b66</code> (900)</li>
  <li><strong>Fathom (Emerald / Teal):</strong> <code>#e2f5f2</code> (50), <code>#84cdc1</code> (400), <code>#4daa9d</code> (600), <code>#369489</code> (700), <code>#1a5c5c</code> (900), <code>#034f46</code> (950 — deep green pill/highlight)</li>
  <li><strong>Lumen (Signature Soft Yellow):</strong> <code>#ffffef</code> (300), <code>#ffffeb</code> (400 — onboarding / highlight banners), <code>#f7f7db</code> (500)</li>
  <li><strong>Mode Accents:</strong> Glow <code>#ffa946</code> (Command mode bars), Signal <code>#ffbcf2</code> (Instruct mode bars), Chord <code>#965ace</code>, Pulse <code>#bd5768</code></li>
</ul>

<h4>Typography &amp; Scale</h4>
<ul>
  <li><strong>UI Sans:</strong> <code>Figtree</code> (fallback <code>Segoe UI</code> / system sans) across sizes <code>18/28px</code>, <code>16/24px</code>, <code>14/20px</code>, <code>12/20px</code>, <code>10/18px</code> (weights <code>400</code>, <code>550</code>, <code>600</code>).</li>
  <li><strong>Display Serif:</strong> <code>EB Garamond</code> (fallback <code>Georgia</code>) for editorial hero headers and welcome titles.</li>
  <li><strong>Keycap Monospace:</strong> <code>Google Sans Code</code> / <code>Consolas</code> / monospace with <code>18px</code> compact keycaps (<code>border-radius: 4px</code>).</li>
</ul>

<h3>2. Window Topology &amp; Geometry</h3>
<ol>
  <li><strong>VOX Hub (Main Workspace):</strong> Default size <code>1350&times;850</code> (min ~<code>960&times;640</code>) with persistent left navigation sidebar and light warm sand canvas (<code>#fcfcfb</code>).</li>
  <li><strong>Floating FlowBar (Status Capsule):</strong> Always-on-top, non-activating docking overlay supporting <code>bottom</code>, <code>left</code>, and <code>right</code> screen edges:
    <ul>
      <li><code>resting</code>: <code>40&times;8 px</code> bottom (<code>8&times;40 px</code> side), <code>rgba(0,0,0,0.5)</code> fill, <code>border-radius: 6px</code>, <code>1px solid hsla(0,0%,100%,0.5)</code> border.</li>
      <li><code>ready</code> (hover/context-menu): <code>50&times;30 px</code> black pill (<code>#000000</code>, <code>border-radius: 22.5px</code>, <code>1px solid #30302f</code>) with dark shortcut hint tooltip + optional flanking Scratchpad quick picker.</li>
      <li><code>active_ptt</code> (Push-to-Talk): <code>73&times;30 px</code> pill with live 10-bar parabolic center-weighted waveform (<code>2px</code> wide bars, <code>#ffffff</code> active speech, <code>#ffa946</code> command mode, <code>#ffbcf2</code> instruct mode).</li>
      <li><code>active_popo</code> (Hands-Free): <code>102.5&times;30 px</code> pill containing circular Cancel (<code>18&times;18 px</code>, <code>#4d4a42</code>), 10-bar live waveform, and circular Stop/Finish button (<code>18&times;18 px</code>, white/coral stop glyph).</li>
      <li><code>processing</code>: <code>98&times;30 px</code> pill with animated spinner/dots.</li>
      <li><code>polish_processing</code>: <code>136&times;30 px</code> pill with animated prismatic/lavender border glow.</li>
      <li><code>polish_completed</code>: <code>152&times;30 px</code> status pill with quick undo/view diff action.</li>
      <li><code>error</code>: <code>91&times;30 px</code> pill with destructive red border (<code>#ff5757</code>).</li>
    </ul>
  </li>
  <li><strong>FlowBar Context Menu:</strong> Right-click popup card (<code>border-radius: 10px</code>, shadow-lg) containing <code>Hide for 1 hour</code>, <code>Mute reminder for 12 hours</code> / <code>Configure reminder</code>, <code>Settings</code>, <code>Microphone</code> submenu (device selector + Notetaker mic note), <code>Formatting options</code> / <code>Languages</code> submenu, <code>Transcript history</code> (opens Hub Home), <code>Paste last transcript</code>.</li>
  <li><strong>Floating Scratchpad:</strong> Quick capture editor window (<code>720&times;760</code> default, <code>500&times;600</code> min) with compact formatting toolbar (bold, italic, underline, lists, checklist, inline code, blockquote), live voice dictation button, AI Transform/Rewrite bar (<code>Follow up or ask a question</code> + quick presets), Copy / Send / New Tab / Version History, and Recent notes drawer.</li>
  <li><strong>Floating Notetaker Drawer (Meeting Recorder):</strong> Compact <code>500&times;420</code> to expanded <code>920&times;660</code> resizable call companion showing title, live mic + system loopback indicator, section pills (<code>Brief</code>, <code>My Notes</code>, <code>Summary</code>, <code>Action Items</code>, <code>Transcript</code>, <code>Chat</code>), inline personal notes editor (<em>My thoughts</em> private while recording), live speaker-labeled transcript search &amp; copy, and <code>Ask anything</code> meeting assistant.</li>
  <li><strong>Calendar &amp; Call Detection Reminder Pill:</strong> Stacks cleanly above/beside FlowBar with upcoming Microsoft Calendar countdown (<code>In X min</code> / <code>Now</code>), one-click <code>Join + Start</code> / <code>Start Notetaker</code>, <code>Snooze 2 min</code>, and call auto-detect prompt (<code>Meeting detected — Want notes for this call?</code>).</li>
  <li><strong>Overlay Toast &amp; Onboarding Feature Tour:</strong> Non-stealing dark/light toast notifications (e.g., <code>Cleaned up! See what's changed</code>, <code>No audio detected</code>, <code>Reminder</code>) and interactive Onboarding + Try-It-Out (TIY) flow.</li>
</ol>

<h3>3. Hub Navigation, Functional Pages &amp; Settings Parity</h3>
<ul>
  <li><strong>Left Sidebar Navigation:</strong>
    <ul>
      <li><code>Home</code> (Dictation activity &amp; KPI stats: Total words written + fun comparative tagline, Words Per Minute, Day streak, Apps used, plus chronological <code>Today's dictations</code> / <code>Yesterday's dictations</code> feed with search, hover <code>Copy</code>, <code>Undo AI edit</code>, <code>Edit</code>, <code>Retry</code>, <code>Delete</code>, and Upcoming Meetings + Notetaker widget card).</li>
      <li><code>Notetaker</code> (Calendar-synced Upcoming Meetings strip with one-click <code>Join + Start</code> + <code>Start Notetaker</code>, call-only privacy video capture indicator, <code>Past notes</code> / <code>Folders</code> tabs, search across meetings &amp; speakers, full meeting detail view with <code>Summary</code>, <code>My Thoughts</code>, <code>Transcript</code>, <code>Action Items / Tasks</code>, <code>Recording &amp; Call Video</code>, <code>Assign Speakers</code> dialog, and <code>Ask Anything</code> AI Q&amp;A).</li>
      <li><code>Dictionary</code> (Personal vocabulary &amp; correction rules <code>misspelling &rarr; replacement</code>, <code>Auto-learned ✨</code> badge when user edits inserted text, search, sort by <code>Newest / Oldest / Starred / A-Z</code>, star pin, bulk select delete, TXT/CSV import/export).</li>
      <li><code>Snippets</code> (Voice trigger phrases &rarr; rich text expansions, character counter, similarity detection, search, sort, bulk select).</li>
      <li><code>Style</code> (Five tabs: <code>Personal messages</code>, <code>Work messages</code>, <code>Email</code>, <code>Other</code>, and <code>Auto cleanup</code>. Each app category picks among <code>Formal.</code>, <code>Casual</code>, <code>very casual</code>, <code>Excited!</code> with live highlighted punctuation/capitalization preview cards; <code>Auto cleanup</code> configures <code>None / Light / Medium / High</code> rewrite intensity).</li>
      <li><code>Scratchpad</code> (Hub view of quick notes with grid/list toggle, pin/delete, <code>Add to Flow Bar</code> toggle, hotkey badge, and rich editor).</li>
      <li><code>Insights</code> (Detailed dictation velocity, WPM trend, words per day/week heatmap, top apps, auto-learned stats).</li>
      <li>Bottom utility links: <code>Settings</code> (opens structured multi-section Settings modal/view) &amp; <code>Help / Replay Tour</code>.</li>
    </ul>
  </li>
  <li><strong>Settings Navigation &amp; Controls:</strong>
    <ul>
      <li><strong>Settings Group:</strong> <code>General</code> (Theme Light/Dark/System, Shortcuts Editor dialog with Push-to-Talk / Hands-Free / Command / Instruct / Open Notetaker / Open Scratchpad / Paste Last / Copy Last / Transform, Microphone selector + priority ranking, Languages + Hinglish toggle), <code>System</code> (Launch at login, Show Flow Bar at all times, Flow Bar edge position <code>Bottom / Left / Right</code>, Dictation &amp; notification sounds, Mute/pause audio while dictating, Smart Formatting, Auto-add corrected words to dictionary, Email auto signature, Scratchpad open behavior, Restart tutorial, Reset local app cache), <code>Notetaker</code> (Automatically detect any call, Start Notetaker automatically, Stop Notetaker when a call ends, Auto-open summary when ready, Show live transcript, Auto-detect speaker names, Calendar notifications timing <code>Never / 15s / 1m / 2m</code>, Max recording length <code>30m / 1h / 2h / 3h</code>, Hide Flow Bar and Notetaker in screen share, Video capture mode for calls), <code>Vibe coding</code> (IDE variable/symbol recognition, file path tagging, code casing helpers).</li>
      <li><strong>Account &amp; Data Group:</strong> <code>Account &amp; Calendar</code> (Local offline profile &amp; Microsoft Outlook Calendar PKCE connection status/sync), <code>Data and Privacy</code> (Context awareness toggle, Local transcript retention policy <code>Never delete / 1d / 7d / 30d / 90d / 180d / 365d</code>, Export all VOX data JSON/CSV/TXT, Delete selected data categories).</li>
    </ul>
  </li>
</ul>

<h3>4. Local Data &amp; Engine Architecture Mapping in VOX</h3>
<ul>
  <li>Preserve VOX's genuine local engines: <code>faster-whisper</code> ASR (<code>transcriber.py</code>), bilingual EN/HI/Hinglish normalization + verbal self-corrections + Style &amp; AutoCleanup formatting (<code>dictation_processor.py</code>), speaker diarization &amp; active participant resolution (<code>meeting_intelligence.py</code>), WASAPI loopback + mic mixing (<code>meeting_recorder.py</code>, <code>dictation_recorder.py</code>), call detector (<code>call_detector.py</code>), call-only HWND video capture (<code>meeting_capture.py</code>), Win32 focus-invariant paste + global hotkey state machine (<code>hotkeys.py</code>, <code>text_inserter.py</code>, <code>window_focus.py</code>), and Microsoft Calendar OAuth PKCE (<code>graph_provider.py</code>).</li>
  <li>Extend <code>src/voxlocal/storage/database.py</code> with Wispr-parity tables/columns: <code>snippets</code>, <code>notes</code> (with pinned/versions), <code>app_styles</code>, <code>todos</code> (meeting action items), <code>folders</code> / <code>folder_meetings</code>, <code>dictionary</code> enhancements (<code>replacement</code>, <code>is_starred</code>, <code>auto_learned</code>, <code>frequency_used</code>), and <code>history</code> enhancements (<code>raw_asr_text</code>, <code>formatted_text</code>, <code>edited_text</code>, <code>app_name</code>, <code>app_category</code>, <code>wpm</code>, <code>duration_sec</code>, <code>word_count</code>, <code>undone</code>).</li>
</ul>

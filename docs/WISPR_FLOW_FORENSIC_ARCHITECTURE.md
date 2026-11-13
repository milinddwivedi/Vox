<h2>Wispr Flow v1.6.1400 Forensic Codebase &amp; Architecture Reference</h2>

<p>This document records the complete technical architecture, UI/UX design tokens, floating bar (<code>FlowBar</code>) geometry and state machine, native OS helper protocol (<code>HelperAPI</code>), and dictation accuracy/speed pipeline extracted directly from the installed Wispr Flow production package (<code>C:/Users/milin/AppData/Local/WisprFlow/app-1.6.1400/resources/app.asar</code>, commit <code>656b3fbfe214db4026fff75b3db650fc3edec8bb</code>).</p>

<h3>1. Extracted Codebase Inventory in Workspace</h3>

<p>All core bundles and assets have been extracted from <code>app.asar</code> into the workspace for direct inspection:</p>

- <code>.agents/teamwork/explorer_r2_video_ref/wispr_extracted/package.json</code> (<code>10,807</code> bytes — Electron Forge 7.11.2, React 18.3.1, Zustand 5.0.9, Motion 12.23.25, Lexical 0.32.1, SQLite3 5.1.7, OpusScript WASM)
- <code>.agents/teamwork/explorer_r2_video_ref/wispr_extracted/extracted_main.js</code> (<code>10,598,186</code> bytes — Main process orchestrator, SQLite ORM, gRPC/WebSocket streaming client, HelperAPI IPC bridge, typing-nudge detector, auto-learn dictionary diff engine)
- <code>.agents/teamwork/explorer_r2_video_ref/extracted_status.js</code> (<code>1,355,855</code> bytes — The floating <code>FlowBar</code> renderer, CSS modules, waveform physics, and state machine)
- <code>.agents/teamwork/explorer_r2_video_ref/extracted_hub.js</code> (<code>6,332,115</code> bytes — Main Hub desktop window: Home feed, Dictionary, Snippets, Context Styles, Notes, Insights, and Settings)
- <code>.agents/teamwork/explorer_r2_video_ref/extracted_meeting_recorder.js</code> (<code>2,435,740</code> bytes — Meeting Notetaker drawer, live transcript word counter, and summary UI)
- <code>.agents/teamwork/explorer_r2_video_ref/extracted_overlay.js</code> (<code>716,244</code> bytes — Screen overlay toasts, coachmarks, and meeting join nudges)
- <code>.agents/teamwork/explorer_r2_video_ref/wispr_extracted/extracted_contextMenu.js</code> (<code>1,025,677</code> bytes — Right-click popup menu for the FlowBar)
- <code>.agents/teamwork/explorer_r2_video_ref/wispr_extracted/extracted_scratchpad.js</code> (<code>1,130,217</code> bytes — Quick floating Scratchpad window)
- <code>.agents/teamwork/explorer_r2_video_ref/wispr_extracted/sounds/</code> (<code>dictation-start.wav</code>, <code>dictation-stop.wav</code>, <code>paste.wav</code>, <code>popo-lock.wav</code> — Acoustic feedback chimes)

<h3>2. Floating Bar (FlowBar) Exact Design, Dimensions &amp; Functioning</h3>

<p>In <code>.webpack/renderer/status/index.js</code> (CSS module <code>31201</code>), the floating bar is built around a core container called <code>.thoughtBubble</code> inside an invisible <code>40px</code> tall hover zone (<code>.hoverArea</code>) anchored at <code>[data-bar-position="bottom"]</code>:</p>

- <strong>Resting State (<code>.thoughtBubble.resting</code> — Idle Unhovered):</strong>
  - Dimensions: <code>40px &times; 8px</code> ultra-thin pill (<code>padding: 0 11px</code>, <code>border-radius: 6px</code>).
  - Colors: <code>background-color: rgba(0, 0, 0, 0.5)</code> with <code>border: 1px solid hsla(0, 0%, 100%, 0.5)</code>.
  - Inner Content: A miniature 5-bar static waveform (<code>13px &times; 5px</code>) at 50% opacity. Because it is only <code>8px</code> tall when idle, it never obscures desktop content or taskbars.
- <strong>Ready / Hovered State (<code>.thoughtBubble.ready</code>):</strong>
  - Dimensions: Smoothly springs via <code>transition: all 0.1s cubic-bezier(0.05, 0.6, 0.4, 0.95)</code> to <code>50px &times; 30px</code> (<code>border-radius: 22.5px</code>, <code>background: #000000</code>, <code>border: 1px solid #30302f</code>).
  - Flanking Circular Pickers: Left and right <code>30px &times; 30px</code> circular icon buttons (<code>.leftPickersContainer</code>, <code>.rightPickersContainer</code>) appear beside the pill for quick access to Transform/Polish, Meeting Notepad, and Context Menu.
  - Hover Tooltip: Displays a dark pill tooltip <code>4px</code> above the bar: <code>Click or hold Ctrl+Win to start dictating</code>.
- <strong>Active Push-to-Talk State (<code>.thoughtBubble.activePtt</code>):</strong>
  - Dimensions: Expands to <code>73px &times; 30px</code> (<code>border-radius: 22.5px</code>, <code>padding: 0 11px</code>).
  - Waveform Physics: Renders a 12-bar center-weighted waveform (<code>height: 18px</code>, <code>gap: 2.5px</code>). Each bar height is scaled by a parabolic center envelope <code>g = max(0, 1 - h^2 * (spread / 48))</code> where <code>h</code> is the distance from the center bar, modulated in real time by <code>--wave-mult</code> from the microphone RMS/FFT level and staggered by <code>0.1s * phase[i]</code>.
- <strong>Active Hands-Free Toggle State (<code>.thoughtBubble.activePopo</code> — Push-On / Push-Off):</strong>
  - Dimensions: Expands to <code>102.5px &times; 30px</code> (<code>padding: 0 6px</code>, <code>gap: 8px</code>).
  - Controls: Left circular Cancel button (<code>&times;</code>, <code>18px &times; 18px</code>), center 9-bar live waveform, and right coral-red Stop button (<code>#ff6c4c</code> square stop icon inside an <code>18px &times; 18px</code> circle).
- <strong>Processing &amp; Polish States (<code>.thoughtBubble.processing</code> / <code>.polishProcessing</code>):</strong>
  - Dimensions: <code>98px &times; 30px</code> (or <code>136px &times; 30px</code> during Polish transform).
  - Visual Feedback: Shows an 8-spoke rotating spinner or animated dots alongside stage-rolling status text (<code>Cleaning up</code> &rarr; <code>Shaping draft</code> &rarr; <code>Tightening</code> &rarr; <code>Done. See changes</code>), optionally framed by a rotating conic rainbow border (<code>conic-gradient(from 0deg, #f0d7ff, #ffbcf2, #ff6c4c, #ffa946, #7232a6, #f0d7ff)</code>) during AI Command/Instruct mode.
- <strong>Acoustic Feedback Chimes:</strong>
  - Plays instant low-latency WAV chimes on every state transition: <code>dictation-start.wav</code> when recording begins, <code>popo-lock.wav</code> when locking into hands-free mode, <code>dictation-stop.wav</code> when recording stops, and <code>paste.wav</code> when text is injected.

<h3>3. How Wispr Flow Achieves High Speed, Accuracy &amp; Context Logic</h3>

<p>Forensic inspection of <code>.webpack/main/index.js</code> and <code>.webpack/renderer/hub/index.js</code> reveals six concrete engineering mechanisms behind Wispr Flow's speed and accuracy:</p>

- <strong>1. Incremental Audio Encoding While Speaking (<code>incrementalOpusEncoder.worker.js</code>):</strong>
  - Rather than waiting for the user to stop speaking before reading and encoding the audio buffer, Wispr Flow streams PCM frames from <code>recorderWorklet.js</code> directly into a background worker that encodes <code>16kHz</code> audio chunks continuously while the user speaks. When the hotkey is released, only the final tail frame (<code>~60ms</code>) remains to be processed.
- <strong>2. Active Window &amp; Textbox Context Extraction (<code>HelperAPIRequestTextBoxInfo</code>):</strong>
  - The moment dictation starts, the native C# helper (<code>Wispr Flow Helper</code>) inspects the focused Windows UI Automation element and captures:
    - <code>appName</code> &amp; <code>windowTitle</code> (e.g., <code>Cursor - controller.py</code>, <code>Slack - #engineering</code>, <code>Outlook</code>)
    - <code>beforeText</code> &amp; <code>afterText</code> (text immediately preceding and following the caret in the active input box)
    - <code>selectedText</code> (highlighted text for Command Mode replacement or context)
    - <code>nearestTexts</code> / <code>contents</code> (visible screen/editor identifiers)
- <strong>3. IDE Variable Recognition &amp; File Tagging ("Vibe Coding"):</strong>
  - When dictating inside VS Code, Cursor, Windsurf, or terminal coding agents (<code>CodingCLIAgent</code>), Wispr Flow extracts camelCase, PascalCase, and snake_case symbols from the active editor window title and surrounding code so spoken identifiers (e.g., "dual tier floating pill" or "transcribe dictation") match exact code symbols or backtick-formatted identifiers.
- <strong>4. App-Category Style &amp; Tone Adaptation:</strong>
  - Automatically classifies the target app into one of four built-in style profiles:
    - <code>Work messages</code> (Slack, Microsoft Teams, Discord)
    - <code>Email</code> (Gmail, Microsoft Outlook, Superhuman)
    - <code>Personal messages</code> (WhatsApp, Telegram, Messenger)
    - <code>Other / IDE</code> (Docs, Notion, Code Editors)
  - Each category applies the user's preferred capitalization and punctuation style (<code>Formal</code>, <code>Casual</code>, or <code>Very Casual</code>).
- <strong>5. Deterministic + LLM Self-Correction (Backtrack &amp; Auto-Cleanup):</strong>
  - Strips hesitation fillers (<code>um</code>, <code>uh</code>, <code>er</code>, <code>like</code>, <code>you know</code>), resolves mid-utterance self-corrections ("let's meet at 3 no actually 4 pm" &rarr; "Let's meet at 4 PM"), formats spoken numbered/bulleted lists automatically, and expands voice Snippets (e.g., saying "intro email" or "my LinkedIn" drops in the full saved block).
- <strong>6. Auto-Learning Personal Dictionary (<code>UpdateEditedText</code> + <code>diff-match-patch</code>):</strong>
  - After pasting a transcript into the target app, Wispr Flow monitors the active textbox for a few seconds. If the user manually fixes the spelling of a name or jargon word, <code>diff-match-patch</code> isolates the token-level correction and auto-adds the word to the personal Dictionary (<code>DictionaryItemAutoAdded</code>).

<h3>4. UI/UX Comparison: Why Wispr Flow Looks Modern vs Why Legacy VOX Felt Outdated</h3>

- <strong>Color Palette &amp; Surface Craft:</strong>
  - Wispr Flow uses a warm editorial ivory palette in Light Mode (<code>--sand-100: #faf9f7</code>, <code>--sand-200: #f8f7f3</code>, <code>--sand-600: #eeebe3</code>, ink text <code>#1a1a1a</code>, soft lavender accents <code>--dawn-500: #f0d7ff</code>) and a deep, distraction-free carbon obsidian in Dark Mode (<code>#0d0f12</code> / <code>#15181e</code>).
  - By contrast, legacy VOX used cold sterile gray-blues, heavy borders, and a 1990s-style multi-colored neon wave widget (<code>VoxKineticTubesReactor</code>) across the top of the Dictation page.
- <strong>Information Hierarchy:</strong>
  - Wispr Flow's Hub leads with a clean editorial greeting header, 3 concise metric pills (Streak, Average Speed WPM, Total Words), a focused "Quick Dictation / Try It Out" card, and a chronological, scannable Activity Feed where each dictation row shows timestamp, target app badge, clean text, and 1-click Copy / Undo actions.
- <strong>Floating Pill Restraint:</strong>
  - Wispr Flow's FlowBar stays out of the way as a sleek dark micro-capsule until hovered or triggered, whereas legacy overlays were bulky and lacked tactile audio chimes.

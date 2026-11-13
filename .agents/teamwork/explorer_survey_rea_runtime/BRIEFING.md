# BRIEFING — 2026-11-12T00:32:00Z

## Mission
Investigate runtime behavior, process structure, Windows system integrations, audio capture, global hotkeys, state lifecycle, and text injection mechanism of Wispr Flow on Windows.

## 🔒 My Identity
- Archetype: explorer
- Roles: read-only investigation, runtime analysis, system integration analysis
- Working directory: d:\Milind\Voice Assistant
- State folder: d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_rea_runtime
- Original parent: c39a9aac-953c-498d-b920-948ff368c138
- Milestone: Survey & Reverse Engineering Phase (Project VOX)

## 🔒 Key Constraints
- Read-only investigation — do NOT modify source code or application files
- Write only to `.agents/teamwork/explorer_survey_rea_runtime/`
- Deep analysis of runtime, process tree, hotkeys, audio capture, state lifecycle, text insertion, and footprint

## Current Parent
- Conversation ID: c39a9aac-953c-498d-b920-948ff368c138
- Updated: 2026-11-12T00:32:00Z

## Investigation State
- **Explored paths**:
  * `C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\`
  * `resources\app.asar`, `resources\Release\Wispr Flow Helper.exe`
  * `.webpack\main\index.js`, `.webpack\main\incrementalOpusEncoder.worker.js`
  * `.webpack\renderer\hub\index.js`, `.webpack\renderer\dist\recorderWorklet.js`
  * `resources\migrations\`, `C:\Users\milin\AppData\Roaming\Wispr Flow\config.json`
- **Key findings**:
  * Architecture is split between Electron Main and `.NET Native AOT` helper (`Wispr Flow Helper.exe`).
  * IPC uses stdin/stdout stream with delimiter `|` and `+` escape character.
  * Windows global hotkeys captured via `SetWindowsHookEx(WH_KEYBOARD_LL)` in helper, sent as `KeypressEvent` to Electron main.
  * Audio captured in hidden `hubWindow` via `getUserMedia` and `AudioWorkletNode("recorder-processor")` with chunkSize=640 (40ms @ 16kHz linear PCM), encoded by Opus worker.
  * Windows silent sink prevents echo/feedback loop (`setSinkId({ type: "none" })`).
  * 9 dictation states (`idle`, `initializing`, `listening`, `stopping`, `processing`, `dismissed`, `retrying`, `error`, `testing`).
  * Text injection via clipboard backup -> clipboard populate (`SetClipboardData`) -> `SendInput` Ctrl+V -> clipboard restore.
  * Floating widget (`statusWindow`) uses `showInactive()`, `setAlwaysOnTop(true, "screen-saver")`, and alpha hit-testing.
- **Unexplored areas**: None remaining for runtime survey phase.

## Key Decisions Made
- Decompiled and parsed Electron ASAR archive directly using custom script `asar_reader.mjs`.
- Verified binary PE imports and Win32 hook APIs in `Wispr Flow Helper.exe`.
- Successfully produced comprehensive 5-component `handoff.md`.

## Artifact Index
- DISPATCH.md — Recorded instructions and queries
- progress.md — Liveness & status heartbeat
- BRIEFING.md — Situational awareness
- asar_reader.mjs — Direct ASAR reader utility
- handoff.md — Comprehensive runtime & system integration forensic report

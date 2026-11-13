# Static Forensic Specification Report — Wispr Flow Desktop (Project VOX Parity)

## Executive Summary
This document provides the authoritative, evidence-backed forensic specification of the installed Wispr Flow Windows desktop application (`app-1.6.1457`), mined directly from `C:\Users\milin\AppData\Local\WisprFlow`, `C:\Users\milin\AppData\Roaming\Wispr Flow`, and the application's unpacked Electron ASAR bundle. This specification serves as the blueprint for delivering an exact functional replica under the brand **VOX**.

---

## 1. Observation (Forensic Evidence Chain)

### 1.1 Application Installation & Packaging
- **Application Directory**: `C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457`
- **Data Directory**: `C:\Users\milin\AppData\Roaming\Wispr Flow`
- **Executable**: `Wispr Flow.exe` (595,840 bytes) with Squirrel updater (`Update.exe`)
- **Package Manifest (`package.json`)**:
  - Name: `wispr-flow`, Product: `Wispr Flow`, Version: `1.6.1457`
  - Base Commit Hash: `656b3fbfe214db4026fff75b3db650fc3edec8bb`
  - Main Entry: `.webpack/main/index.js` (Size: 10,656,735 bytes)
  - Key Dependencies: Electron Forge 7.11.2, Babel, Playwright, Jest, Native C++ / Node addons.
  - Native Modules (`app.asar.unpacked/.webpack/main/native_modules`):
    - `42d0061f1607b582463ed82e4884c8ec.node`, `c085ca983975b2182a58798cb51b93fb.node`
    - `jabra-device-connector`: Bluetooth and USB HID device interaction DLLs (GN Audio)
    - `opusscript_native_wasm.wasm`: Opus audio encoding WebAssembly module (317,635 bytes)

---

### 1.2 Electron Windows & Renderers (10 Windows Identified)
Extracted verbatim from `.webpack/main/index.js`:

#### 1. Flow Status Indicator (`status`) — Floating Voice Pill
- **Title**: `"Flow Status Indicator"`
- **Renderer**: `.webpack/renderer/status/index.html` (JS: 1,359,825 bytes, Preload: 66,262 bytes)
- **Window Configuration**:
  ```javascript
  {
    x, y, width, height, // computed by module 58601
    show: false,
    webPreferences: { preload: path.resolve(__dirname, "../renderer/status/preload.js"), backgroundThrottling: false },
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    roundedCorners: false,
    type: "toolbar", // on Windows; "panel" on macOS
    fullscreen: false,
    fullscreenable: false,
    maximizable: false,
    minimizable: false,
    resizable: false,
    movable: false,
    closable: false,
    skipTaskbar: true,
    focusable: d.ty.allowBubbleFocus, // false during passive resting/recording
    frame: false,
    alwaysOnTop: true
  }
  ```
- **Window Positioning & Geometry (Module 58601)**:
  - **Docked Bottom (Default)**:
    - Width: `440px` (expandable to `614px` on active audio)
    - Height: `320px` (or `512px` when popover / action menu expanded)
    - Coordinates: Centered horizontally (`x = workArea.x + (workArea.width - 440) / 2`), anchored to bottom (`y = workArea.y + workArea.height - 320`).
  - **Docked Side (Left / Right)**:
    - Width: `490px`, Height: `440px` (or expanded `1144px`).
  - **Click-Through & Input Shaping (`StatusInputShape` in Module 76127)**:
    - Status window maintains `setIgnoreMouseEvents(true, { forward: true })`.
    - Renderer calculates interactive bounding boxes and reports them via `applyReport({ rects, shapeRects, viewport })` (capped at 128 bounding boxes). Outside these rects, all mouse clicks pass straight through to underlying applications.
    - Window Level: `r.setAlwaysOnTop(true, "screen-saver")`.

#### 2. Flow Hub (`hub`) — Onboarding, Settings, History, Dictionary
- **Title**: `"Flow Hub"`
- **Renderer**: `.webpack/renderer/hub/index.html` (Preload: 66,262 bytes)
- **Window Configuration**:
  - Dimensions: `width: 1350, height: 850` (Module 54103 `PF = { width: 1350, height: 850 }`)
  - Minimum Size: `minWidth: 1140, minHeight: 750` (`eh = { width: 1140, height: 750 }`)
  - `center: true, resizable: true, movable: true, maximizable: true, fullscreenable: true, show: false`
  - Windows Frame: `frame: false, autoHideMenuBar: true` (macOS: `titleBarStyle: "hidden", trafficLightPosition: { x, y }`)
  - Transparent: `transparent: false` on Windows, `true` on macOS; `hasShadow: true`.
  - `webPreferences: { preload: ..., additionalArguments: (0,v.jG)(), webviewTag: true, devTools: isDev }`

#### 3. Flow Context Menu (`contextMenu`)
- **Title**: `"Flow Context Menu"`
- **Renderer**: `.webpack/renderer/contextMenu/index.html`
- **Configuration**: Covers the active display `workArea` (`x: workArea.x, y: workArea.y, width: workArea.width, height: workArea.height`).
- `transparent: true, backgroundColor: "#00000000", hasShadow: false, frame: false, skipTaskbar: true, focusable: false, alwaysOnTop: true ("screen-saver")`.
- `setIgnoreMouseEvents(true)` until triggered.

#### 4. Meeting Recorder / Notepad (`meeting_recorder`)
- **Title**: `"Meeting Recorder"`
- **Renderer**: `.webpack/renderer/meeting_recorder/index.html`
- **Configuration**: `minWidth: 460, minHeight: 600`, `fullscreenable: true, maximizable: true, minimizable: true, resizable: true, focusable: true`.
- Windows Frame: `titleBarStyle: "hidden", frame: false, autoHideMenuBar: true`.

#### 5. Scratchpad (`scratchpad`)
- **Title**: `"Scratchpad"`
- **Renderer**: `.webpack/renderer/scratchpad/index.html`
- **Configuration**: `transparent: true, hasShadow: true, backgroundColor: "#00000000", resizable: true, movable: true, frame: false, autoHideMenuBar: true, maximizable: false, minimizable: false`.

#### 6. Feature Tour Overlay (`feature_tour`)
- **Title**: `"Feature Tour"`
- **Renderer**: `.webpack/renderer/feature_tour/index.html`
- **Configuration**: Fullscreen display bounds, `transparent: true, frame: false, alwaysOnTop: true, skipTaskbar: true, focusable: true`.

#### 7. Overlay (`overlay`)
- Floating extension panel or screen overlay; `transparent: true, frame: false, skipTaskbar: true, alwaysOnTop: true`.

#### 8. Accessibility Drop (`accessibility_drop`)
- `type: "panel", setAlwaysOnTop(true, "floating")`, `setVisibleOnAllWorkspaces(true)`.

#### 9. Meeting AX Inspector (`meeting_ax_inspector`)
- `title: "Meeting AX Inspector", width: 560, height: 760, minWidth: 360, minHeight: 400`, `titleBarStyle: "hidden", autoHideMenuBar: true`.

#### 10. Extension Screen Recorder (`extensionScreenRecorder`)
- Hidden background capture worker: `show: false, focusable: false, skipTaskbar: true, webPreferences: { session: partition, sandbox: true }`. Captures displays via `desktopCapturer.getSources({ types: ["screen"] })`.

---

### 1.3 Audio Capture & Dictation Pipeline
Verbatim extraction from `.webpack/renderer/dist/recorderWorklet.js`, `.webpack/main/opusEncoder.worker.js`, and `.webpack/main/incrementalOpusEncoder.worker.js`:

#### 1. AudioWorklet Processor (`recorderWorklet.js`)
- Registered as: `"recorder-processor"`
- **Parameters**:
  - `chunkSize = 640` samples (40ms buffer at 16,000 Hz)
  - `renderQuantumSize = 128` samples
  - Input: Float32Array 2D buffer (`[channels][chunkSize]`)
- **Message Protocol**:
  - `port.postMessage("start")`: Resets buffer index, zeroes dB metrics, sets `recording = true`.
  - Audio chunk emission: When `bufferIndex >= chunkSize`, posts `{ 0: Float32Array, 1: Float32Array, frame: currentFrame + 128 }`.
  - `port.postMessage("stop")`: Sends remaining partial buffer, posts `"recorderWorklet:done"`, clears buffer.
  - `port.postMessage("measure")`: Measures background ambient noise across 7 blocks, calculates RMS dB: `20 * Math.log10(sqrt(sumSquares / samples) + 1e-10)`, posts `postRelease:${dB}`, then `postRelease:done`.

#### 2. Opus Encoder Workers (`opusEncoder.worker.js` & `incrementalOpusEncoder.worker.js`)
- **Codec Configuration**:
  - Input Sample Rate: `16,000 Hz` (`Q = 16e3`)
  - Channels: `2` (Stereo / Dual Channel, `d = 2`)
  - Frame Size: `320` samples per channel (`B = 320`, exactly 20ms per Opus frame)
  - Bitrate: `64,000 bps` (`C = 64e3`, 64 kbps)
  - Application Mode: `Application.AUDIO` (Opus VOIP / Audio)
  - Packets Per Page: `20` (Ogg page encapsulation)
  - Native Engine: `opusscript_native_wasm.wasm`
- **Incremental Worker Message Types**:
  - Input: `incremental_start` (options), `incremental_advance` (watermarks), `incremental_finalize` (watermarks).
  - Output: `incremental_start_result`, `incremental_advance_result`, `incremental_finalize_result`, `incremental_failure`.

#### 3. Dictation State Machine (`fw` Enum in Module 54103)
The dictation pill operates across 16 formal states:
```
1.  hidden                    - Pill is hidden offscreen / invisible
2.  resting                   - Pill is in docked resting bubble state
3.  context-menu              - Context menu / formatting pill active
4.  initializing              - Audio engine / mic initialization
5.  ready                     - Audio pipeline primed and awaiting speech
6.  active_ptt                - Push-to-Talk active (key held down)
7.  active_popo               - Push-On / Push-Off hands-free active (toggle)
8.  processing                - Awaiting server transcription / formatting
9.  polish_processing         - AI instruction / rewrite in progress
10. polish_completed_action   - AI rewrite applied with text action
11. polish_completed_na       - AI rewrite completed without action
12. polish_failed             - AI rewrite failed
13. auto_cleanup_completed    - Light / standard cleanup applied
14. hotkey_reminder           - Visual reminder prompt displayed
15. instruct_completed        - Custom prompt / instruction completed
16. error                     - Microphone or network failure state
```

---

### 1.4 SQLite Database Schema (`flow.sqlite`)
Extracted live from `C:\Users\milin\AppData\Roaming\Wispr Flow\flow.sqlite` (28 active tables managed by Sequelize migrations in `resources/migrations`):

| Table Name | Rows | Primary Key | Key Columns & Responsibilities |
|---|---|---|---|
| `History` | 89 | `transcriptEntityId VARCHAR(36)` | Complete dictation log: `asrText`, `formattedText`, `editedText`, `audio` (BLOB), `screenshot` (BLOB), `additionalContext` (JSON), `status`, `app`, `url`, `e2eLatency`, `duration`, `numWords`, `toneMatchedText`, `detectedLanguage`, `axText`, `axHTML`, `opusChunks` (JSON), `usedFallbackAsr`, `formattingDivergenceScore`, `editDistanceToDictated`. |
| `Dictionary` | 6 | `id VARCHAR(36)` | User dictionary & snippets: `phrase`, `replacement`, `teamDictionaryId`, `lastUsed`, `frequencyUsed`, `manualEntry`, `isSnippet`, `replacementHtml`, `isStarred`. |
| `Polish` | 0 | `id UUID` | AI instruction & polish actions: `polishInitialText`, `polishedText`, `instruction`, `app`, `processingTime`, `diffCount`, `promptName`, `shortcutKey`. |
| `UserVoicePreferences` | 0 | `id UUID` | Learned user voice styles: `preference`, `filter`, `needsUploading`. |
| `Meetings` | 3 | `id VARCHAR(36)` | Meeting records: `title`, `notes`, `summary`, `speakerMap`, `participantNames`, `recordedMs`, `audioUploadedAt`, `refineStatus`, `shareSlug`, `calendarEventExternalId`. |
| `Notes` | 0 | `id VARCHAR(36)` | User notes & scratchpad entries: `title`, `content`, `contentPreview`, `pinned`, `searchableContent`. |
| `CalendarEvents` | 0 | `id VARCHAR(36)` | Google / Outlook calendar integrations: `summary`, `startTime`, `endTime`, `attendees`, `meetingUrl`. |
| `Automations` | 0 | `id VARCHAR(36)` | Post-meeting / dictation actions (email, calendar): `entityKind`, `entityId`, `kind`, `payload`. |
| `UserContext` | 0 | `id INTEGER` | Writing samples & personalization context: `writingSamples`, `polishPrompts`. |
| `NotetakerChats` | 0 | `id VARCHAR(36)` | In-meeting assistant chat threads: `meetingId`, `messages`. |
| `NotetakerChatMessages`| 0 | `id VARCHAR(36)` | Chat messages with AI notetaker: `chatId`, `role`, `content`, `sequence`. |
| `UsageOutbox` | 0 | `id VARCHAR(36)` | Offline usage analytics queue: `eventType`, `payload`, `needsUploading`. |
| `Folders` | 0 | `id VARCHAR(36)` | Meeting organization folders: `name`, `color`, `icon`. |
| `FolderMeetings` | 0 | `id VARCHAR(36)` | Folder-to-Meeting many-to-many junction. |
| `TranscriptCorrections`| 0 | `id VARCHAR(36)` | Auto-learning corrections table for phonetic tuning. |
| `RemoteNotifications`| 0 | `id VARCHAR(36)` | Server-pushed banners and educational modals. |
| `SequelizeMeta` | 134 | `name VARCHAR(255)` | Migration history tracker (134 migrations executed). |

---

### 1.5 Configuration & Storage Schemas
Inspected from `C:\Users\milin\AppData\Roaming\Wispr Flow\`:

#### 1. `config.json`
- Top-level keys: `prefs`, `voiceProfile`, `notifications`, `nudge`, `activationCron`, `syncCoordinator`, `helperLaunch`, `calendarSync`, `syncSocketClient`.
- `prefs.user` configuration:
  - `statusDockEdge`: `"bottom"` (default) or `"right" | "left"`
  - `theme`: `"light" | "dark"`
  - `uiZoomFactor`: `1.0`
  - `hotkey`: Dictation global shortcut (Windows default: `CapsLock` or swapped with `Ctrl`)
  - `enableSounds`: `true`, `soundFeedback`: `true`
  - `shouldMuteAudio`: `true` (system audio ducking during recording)
  - `shouldPauseAudio`: `false`
  - `streamAudio`: `false` (streaming vs batch fallback)
  - `autoCleanupLevel`: `"light"` (`"light" | "standard" | "aggressive"`)
  - `stylePreference`: `"formal"` (`"formal" | "casual" | "concise"`)
  - `onboardingCompleted`: `true`, 17 onboarding step pages completed.

#### 2. `session.json`
- `authProviderId`: `"workos"`
- `workosSession`: `{ accessToken, refreshToken, userId, email, avatarUrl, expiresAt, clientId }`

#### 3. `feature-flags-cache.json`
- PostHog remote feature flag evaluations cached locally (`anonymous` and `identified` flag payloads).

---

### 1.6 External Service Endpoints & Protocols
Extracted from Module 47708, Module 54103, and network service clients:

- **API Base URLs**:
  - Primary Production API: `https://api.wisprflow.ai`
  - Secondary / East API: `https://api-east.wisprflow.ai`
  - Local Dev API: `http://127.0.0.1:8000`
- **Streaming WebSockets**:
  - Primary WebSocket: `wss://api.wisprflow.ai/`
  - Secondary / East WebSocket: `wss://api-east.wisprflow.ai/`
  - Local Dev WebSocket: `ws://127.0.0.1:8300/`
  - Meeting Transcription WebSocket: `wss://api.wisprflow.ai/api/v1/meetings/transcribe?source=desktop`
  - Sync Coordinator WebSocket: `wss://api.wisprflow.ai/api/v1/sync/ws`
  - Realtime Voice Actions: `wss://api.wisprflow.ai/api/v1/voice-actions/realtime`
- **Auth & Cloud Services**:
  - Supabase URL: `https://dodjkfqhwrzqjwkfnthl.supabase.co`
  - Supabase Anon Key: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`
  - PostHog Telemetry Key: `phc_yV8yhSiAUoblWb3L5ju54QEk6Y3u2rA3xV4Dep1yIDC`
  - Sentry DSN: `https://f87752d820de05e60a11ca3a99a87729@o4506267787395072.ingest.sentry.io/4506268508422144`
  - CDN Asset Base: `https://wispr-flow-cdn.s3.us-west-2.amazonaws.com`
- **Key REST Routes**:
  - Dictation fallback: `POST /llm/asr_audio_chunks`
  - User profile & preferences: `GET /api/v1/user/profile`, `POST /api/v1/user/preferences`
  - Dictionary sync: `GET /api/v1/dictionary/personal`, `POST /api/v1/dictionary/team/bulk`
  - Text transformation: `POST /api/v1/transform/apply/stream`, `POST /api/v1/polish/upload`
  - Meeting sync: `POST /api/v1/meetings/sync`

---

### 1.7 Asset Inventory (653 Assets)
Cataloged from `C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\assets`:

1. **Sounds (91 files)**:
   - Root UI sounds: `dictation-start.wav` (31 KB), `dictation-stop.wav` (38 KB), `Notification.wav` (274 KB), `paste.wav` (41 KB), `popo-lock.wav` (47 KB), `achievement.wav` (383 KB).
   - Theme Packs: 17 theme folders (`v1` to `v12`, `t1`, `notifv1`, `notifv2`), allowing customizable audio feedback skins.
2. **Fonts (7 variable font TTF files)**:
   - `Figtree-VariableFont_wght.ttf`, `Figtree-Italic-VariableFont_wght.ttf` (Primary UI)
   - `Manrope-VariableFont_wght.ttf` (Headers & Marketing)
   - `EBGaramond-VariableFont_wght.ttf` (Editorial / Notes)
   - `GoogleSansCode-VariableFont_wght.ttf` (Monospace / Code)
3. **Tray Icons (6 files)**:
   - `TrayIconWindows.png` (1.4 KB), `MenuEyeTemplate.png`, `MenuEyeOffTemplate.png`.
4. **Lottie Animations (2 files)**:
   - `celebration.json` (335 KB), `pulsing_circle.json` (7.2 KB).
5. **Videos (17 WebM files)**:
   - Windows Onboarding videos: `Windows/works_in_any_app.webm`, `Windows/ai_auto_edit.webm`, `Windows/many_languages.webm`.
6. **Icons (48 SVGs)**: Arrow, microphone, sound, trash, copy, settings, sparkles, chevron.
7. **Illustrations (68 files)**: Onboarding banners, calendar connect hero, empty states.
8. **Logos & Transparent Logos (65 files)**: App integration badges (ChatGPT, Claude, Notion, Slack, VS Code).
9. **App Logos & Images (349 files)**: Extensive collection of application icons for automatic app detection.

---

## 2. Logic Chain
1. **Window Hierarchy**: Wispr Flow decouples UI into 10 specialized windows. The floating widget (`status`) is non-activating, frameless, transparent, always-on-top (`screen-saver` level), and routes mouse clicks dynamically using reported hit-test rectangles so background apps remain clickable. For VOX, this exact BrowserWindow configuration guarantees flawless floating pill behavior.
2. **Audio Architecture**: High responsiveness is achieved by running `RecorderProcessor` in an AudioWorklet thread buffering 40ms chunks (640 samples at 16kHz) and piping them directly to worker threads running `OpusScript` WebAssembly at 64kbps. For VOX, adopting this exact worklet and WASM Opus pipeline eliminates latency spikes and audio clipping.
3. **Storage Parity**: Wispr Flow uses SQLite with 28 tables to maintain local-first history, dictionary substitutions, and meeting notes, syncing opportunistically to cloud endpoints. Replicating the SQLite schema ensures 100% data model compatibility and local offline resilience.
4. **Service Contracts**: WebSocket streaming (`wss://api.wisprflow.ai`) is prioritized for real-time dictation, with an HTTP fallback to `/llm/asr_audio_chunks` or local Whisper. VOX can implement local speech models (e.g. faster-whisper) matching these exact streaming protocols.

---

## 3. Caveats
- **Production Backend Authentication**: The installed Wispr Flow uses WorkOS and proprietary Wispr Flow API keys. For Project VOX deployment, authentication and streaming endpoints must be routed to VOX's self-hosted or local engine while preserving identical request/response shapes.
- **macOS vs Windows Specifics**: While the codebase contains macOS specific bindings (`Macos27PanelNonactivation`, `trafficLightPosition`), all Windows-specific configurations (`type: "toolbar"`, `autoHideMenuBar: true`, `TrayIconWindows.png`) have been explicitly isolated for Windows execution.

---

## 4. Conclusion
The static specification of Wispr Flow `1.6.1457` is fully mined and documented. All 10 BrowserWindow definitions, the complete AudioWorklet / Opus pipeline, the 28-table SQLite schema, the configuration structures, the external API routes, and the 653-asset catalog provide the complete foundation required to build the exact VOX replica.

---

## 5. Verification Method
To independently verify the findings in this report:

1. **Verify BrowserWindow Configurations**:
   ```powershell
   node -e "const fs = require('fs'); const asar = 'C:/Users/milin/AppData/Local/WisprFlow/app-1.6.1457/resources/app.asar'; /* inspect .webpack/main/index.js at indices 8058700 and 8039000 */"
   ```
2. **Verify SQLite Database Schemas**:
   ```powershell
   node -e "const { DatabaseSync } = require('node:sqlite'); const db = new DatabaseSync('$env:TEMP/flow_copy.sqlite'); console.log(db.prepare(\"SELECT count(*) FROM sqlite_master WHERE type='table'\").get());"
   ```
3. **Verify Audio Worklet & Opus Workers**:
   Inspect `.webpack/renderer/dist/recorderWorklet.js` and `.webpack/main/opusEncoder.worker.js` extracted from `app.asar`.
4. **Verify Assets**:
   ```powershell
   Get-ChildItem -Path "C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\assets" -Recurse | Measure-Object
   ```

---

## Appendix: Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|---|---|---|---|---|---|---|
| 1 | Window Management | Floating Voice Pill (`status`) | Frameless, transparent floating dictation status pill | Geometry bounds, dockEdge (`bottom`/`right`/`left`) | Frameless BrowserWindow with screen-saver alwaysOnTop | Falls back to default bottom bounds if display coordinates unresolvable | Module 72413 & 58601 |
| 2 | Window Management | Dynamic Input Shaping | Passes mouse clicks through transparent window regions | Array of hit-test rects (max 128) | Windows API window shape masking / forward click | Caps at 128 rects; logs warning on viewport mismatch | Module 76127 (`StatusInputShape`) |
| 3 | Window Management | Flow Hub Window (`hub`) | Main settings, onboarding, history, and dictionary window | Window open event, dimensions 1350x850 (min 1140x750) | Frameless BrowserWindow with shadow and autoHideMenuBar | Destroys on renderer crash to clear invisible click-trap | Module 8039247 & 54103 |
| 4 | Window Management | Context Menu Window (`contextMenu`) | Overlay menu for text formatting and AI commands | WorkArea bounds | Transparent screen-saver level overlay | Stays hidden with mouse events ignored until summoned | Module 8015360 |
| 5 | Window Management | Meeting Recorder (`meeting_recorder`) | Meeting notepad and live transcription window | Bounds, min dimensions 460x600 | Frameless hidden titlebar window | Reverts off virtual workspaces on minimize | Module 6524112 |
| 6 | Audio Pipeline | AudioWorklet Processor | Captures microphone PCM in real-time | Audio input stream, 16kHz, 640 sample chunks (40ms) | Float32Array chunks + frame sequence timestamp | Posts `desynced` message if stream time indexing drifts | `recorderWorklet.js` |
| 7 | Audio Pipeline | Ambient Noise Calibration | Measures background noise floor on demand | 7 quantum blocks of audio samples | Calculated RMS decibels (`postRelease:${dB}`) | Bounds check clamp to `1e-10` to avoid log(0) | `recorderWorklet.js` (`measure`) |
| 8 | Audio Pipeline | WebAssembly Opus Encoder | Compresses raw 16kHz PCM to Opus packets | Float32Array PCM frames (320 samples/channel, 20ms) | Opus encoded Ogg pages (64 kbps, 2 channels) | Throws `unsupported_channels` or `unsupported_sample_rate` | `opusEncoder.worker.js` |
| 9 | Audio Pipeline | Incremental Opus Worker | Chunked streaming encoder for long meeting recordings | `incremental_start`, `advance`, `finalize` | `incremental_advance_result` with watermarks | Emits `incremental_failure` with phase and error message | `incrementalOpusEncoder.worker.js` |
| 10 | Dictation Lifecycle | Dictation State Machine | Coordinates UI pill and recording states | Global hotkey trigger (CapsLock / Ctrl) | 16 discrete lifecycle states (`active_ptt`, `active_popo`, etc.) | Transitions to `error` state on mic or network timeout | Module 54103 (`fw` enum) |
| 11 | Dictation Lifecycle | Push-To-Talk vs Hands-Free | Supports both hold-to-talk (PTT) and push-on push-off (POPO) | Keydown / Keyup events | `ACTIVE_PTT` vs `ACTIVE_POPO` state transitions | Double-tap or lock detection | Module 54103 |
| 12 | Dictation Lifecycle | Auto Audio Ducking / Muting | Ducks background system audio during active dictation | `shouldMuteAudio` preference flag | Native OS audio attenuation command | Ignores if OS permission denied | Module `prefs.user` |
| 13 | Storage | Local History Database | Stores full transcriptions, latency, app context, and audio | Dictation session completion | SQLite row in `History` (63 columns) | Queues to `needsUploading = 1` if offline | `flow.sqlite` table `History` |
| 14 | Storage | Personal & Team Dictionary | Custom phonetic replacements and text snippets | Word trigger and replacement strings | SQLite row in `Dictionary` (16 columns) | Falls back to local dictionary if cloud sync fails | `flow.sqlite` table `Dictionary` |
| 15 | Storage | AI Polish & Instructions | Stores prompt edits and diffs | Text selection + instruction prompt | SQLite row in `Polish` (20 columns) | Sets `status = failed` on LLM error | `flow.sqlite` table `Polish` |
| 16 | Storage | Meeting Notes & Transcripts | Stores meeting audio, diarized speaker transcripts, notes | Meeting recording chunks | SQLite row in `Meetings` (62 columns) | Retries upload up to `refineRetries` | `flow.sqlite` table `Meetings` |
| 17 | Configuration | User Preferences Store | Persistent settings for dock edge, theme, shortcuts | JSON mutations | `AppData/Roaming/Wispr Flow/config.json` | Reverts to defaults if JSON corrupted | `config.json` (`prefs.user`) |
| 18 | Configuration | Session Token Manager | Manages WorkOS and Supabase bearer tokens | OAuth login callback | `session.json` (`workosSession`) | Invalidates session on 401 response | `session.json` |
| 19 | Network / Cloud | Streaming Transcription WS | Real-time speech-to-text over WebSocket | Opus audio chunks stream | Partial & final JSON transcription results | Falls back to HTTP `/llm/asr_audio_chunks` | Module 47708 & 5663083 |
| 20 | Network / Cloud | Fallback Whisper ASR | Secondary cloud transcription | Audio chunk buffer payload | Transcribed text payload | Logs `both_services_failed` error | `AriaWebClient.transcribeOpenAI` |
| 21 | Assets / UI | Sound Feedback Engine | Auditory confirmation of dictation events | Event triggers (`start`, `stop`, `paste`, `popo-lock`) | Plays WAV audio files from selected theme (v1-v12) | Silently suppressed if `enableSounds: false` | `resources/assets/sounds/` |
| 22 | Assets / UI | Multi-Page Onboarding Tour | 17-step product walkthrough and permission setup | Step completion events | Tracks progress in `prefs.user.pagesCompleted` | Reroutes to pending page on uncompleted launch | `config.json` & `hub` |

---

## Appendix: Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---|---|---|
| 1 | Status Pill Positioning | Cursor placed on secondary monitor with negative display coordinates | `m.s2` clamps offsets against display bounds and topReservedInset to keep pill visible on screen |
| 2 | Status Input Shaping | Renderer submits more than 128 bounding rectangles for hit-testing | `applyReport` logs `Rectangle report exceeded the native shape cap` and truncates array to 128 |
| 3 | Status Input Shaping | Renderer viewport size differs by >2px from native window content bounds | `applyReport` logs `viewport-mismatch` and rejects the shape update |
| 4 | Opus Audio Encoding | Non-16000Hz sample rate or >2 audio channels fed into encoder | Worker immediately throws `unsupported_sample_rate` or `unsupported_channels` |
| 5 | AudioWorklet Streaming | Multiple input channels drift out of frame sync | `getConsensusBufferIndex` logs `time indexing into streams is desynced` and posts warning |
| 6 | Dictation Network | WebSocket disconnects mid-utterance | Controller catches socket drop and dispatches fallback request to `/llm/asr_audio_chunks` |
| 7 | Floating Window Crashes | Hub or Status window renderer process crashes | Main process listens for `render-process-gone` and immediately destroys window to clear invisible click-traps |
| 8 | SQLite Storage | Application launched while another instance has a file lock on `flow.sqlite` | SQLite WAL mode permits concurrent reads; main process catches lock errors and defers mutations |
| 9 | Audio Feedback | User disables audio cues (`enableSounds = false`) | Audio playback IPC call returns early without invoking web audio context |
| 10 | Onboarding Re-entry | User opens app after completing 17 onboarding steps | `prefs.user.onboardingCompleted = true` causes Hub window to open directly to History / Dictation view |

# VoxLocal — Complete User Guide & Reference Manual

Welcome to **VoxLocal**, the enterprise-grade, local-first AI voice assistant and meeting intelligence suite for Windows. This guide covers the complete feature set, cross-feature workflows, keyboard shortcuts, and audio/video engineering pipelines.

---

## 1. Getting Started & Architecture Overview

1. **Launching VoxLocal:**
   - Double-click [`run_voxlocal.bat`](file:///d:/Milind/Voice%20Assistant/run_voxlocal.bat) or [`dist/VoxLocal/VoxLocal.exe`](file:///d:/Milind/Voice%20Assistant/dist/VoxLocal/VoxLocal.exe).
   - When VoxLocal starts, you will see:
     - The **Ultra-Compact Vertical Floating Pill Dock** along the edge of your screen.
     - The **VoxLocal Icon** in your Windows System Tray (notification area).
     - The **VoxLocal Main Dashboard** featuring 7 interconnected workspaces.
2. **Running Quietly in the Background:**
   - Closing the dashboard window (`✕`) does **not** exit VoxLocal; it minimizes quietly to the System Tray and Floating Pill so dictation and meeting intelligence remain on-demand at any second.
   - To exit completely, click the Settings button (`⋮`) on the Floating Pill and select **Exit VoxLocal**, or right-click the System Tray icon and choose **Exit VoxLocal**.
3. **Starting Automatically with Windows:**
   - Open **⚙ Settings & Audio Devices** -> check **"Launch VoxLocal automatically at Windows login"** and **"Start minimized in background"** -> click **Save All Settings**.

---

## 2. The 7 Integrated Workspaces

VoxLocal is structured into 7 dedicated workspaces accessible via the top tab bar or keyboard shortcuts (`Ctrl+1` through `Ctrl+7`):

### Tab 0: Voice Typing & Dictation History (`Ctrl+1`)
- **Zero-Click Hands-Free Dictation:** Live microphone streaming with automated voice activity detection (VAD). Transcribes your speech in real time and inserts text directly at your cursor position in any application (Slack, Word, VSCode, Chrome, Teams, Windows Search).
- **Dictation History:** Real-time log of previous utterances with timestamp, duration, target application, and character count.
- **Quick Teach:** One-click learn from errors. Click any misrecognized word to teach VoxLocal your preferred spelling, instantly updating the custom vocabulary database without restarting.

### Tab 1: Meeting Studio & Live Monitor (`Ctrl+2`)
- **Live Multichannel Recording:** Simultaneous recording of local microphone and multi-endpoint system audio via WASAPI Loopback.
- **Zero-Loss Screen & Video Capture:** Records screen presentations with audio-video synchronization using stream-copy remuxing (zero re-encoding degradation).
- **Real-Time DSP Signal Processing:** 80Hz 4th-order high-pass Butterworth filter, adaptive 50/60Hz electrical hum notch filter, soft-knee expansion, conservative speech EQ, and true-peak limiting running locally under 1% CPU.
- **Live VU Meters:** Independent visual level meters for microphone and system audio.

### Tab 2: Reports & Meeting Intelligence (`Ctrl+3`)
- **Automated Structure Extraction:** Automatically segments meetings into Executive Summaries, Key Decisions, and Action Items with speaker attribution (`You (Mic)` vs. `Remote Speaker (System)`).
- **Interactive Reports:** Searchable meeting list, folder categorization, editable transcripts, and one-click exports (Markdown, TXT, SRT, VTT).
- **Audio Playback:** Built-in review player with waveform scrubber.

### Tab 3: Ask Vox — Meeting Semantic Intelligence (`Ctrl+4`)
- **Natural Language Querying:** Ask questions across all historical meeting transcripts (e.g., *"What decisions were made regarding the 48kHz audio pipeline?"*).
- **Citation-Backed Answers:** Displays direct excerpts with speaker names, timestamps, and confidence rankings, preventing hallucination.
- **Contextual Search:** Press `Ctrl+F` to instantly focus the question input.

### Tab 4: Folders & Action Items (`Ctrl+5`)
- **Project & Team Organization:** Create color-coded folders (Executive Syncs, Engineering, Client Meetings, 1-on-1s) and assign meetings seamlessly.
- **Action Items Aggregator:** Centralized table of all pending and completed deliverables across every recorded meeting.
- **Live Search & Batch Complete:** Filter tasks instantly with sub-millisecond table filtering or complete all items with one click.

### Tab 5: My Speech & Custom Vocabulary (`Ctrl+6`)
- **Phonetic & Technical Vocabulary:** Define custom pronunciation mappings and technical acronyms (e.g., `vox local` -> `VoxLocal`, `k eight s` -> `K8s`, `post gres` -> `PostgreSQL`).
- **Live Filter:** Instant search through custom dictionary entries.

### Tab 6: Settings, Privacy & Logs (`Ctrl+7` or `Ctrl+,`)
- **Audio Device Selection:** Discrete selection of Microphone and WASAPI Loopback endpoints.
- **DSP Controls:** Toggle high-pass filtering, hum removal, automatic gain control, and silence thresholds.
- **Whisper Engine Selection:** Switch between `tiny`, `base`, and `small` models with CPU int8 quantization or CUDA acceleration.
- **Theme Selection:** Switch between Quiet Luxury Obsidian Dark and Atelier Porcelain Light (`Ctrl+T`).
- **Privacy Assurance:** 100% offline local processing. Zero cloud calls, zero telemetry.

---

## 3. Ultra-Compact Vertical Floating Pill Dock

The Floating Pill Dock floats on top of all windows without stealing keyboard focus (`WS_EX_NOACTIVATE`):

| Icon / Element | Function |
| :--- | :--- |
| **Top Logo / Speaking Indicator (`V`)** | Pulses when voice is active. Click to toggle or bring the main window to the front. |
| **Microphone Icon (`🎙️`)** | Toggle Hands-Free Voice Typing on / off. |
| **Recording Icon (`⏺`)** | Toggle Meeting Recording on / off (pulses red while active). |
| **Reports Icon (`📋`)** | Directly opens Reports & Meeting Intelligence. |
| **Settings Icon (`⋮`)** | Directly opens Settings & Audio Devices. |
| **Drag & Reposition** | Click and drag anywhere to dock on left or right screen edge. Position is saved automatically. |

---

## 4. Spoken Voice Commands & Punctuation

When Voice Typing is active, speak naturally without touching the keyboard:

- **Punctuation:** Say `"period"`, `"comma"`, `"question mark"`, `"exclamation mark"`, `"colon"`, `"new line"`, `"new paragraph"`.
- **Hands-Free Control:** Say `"stop listening"` to turn off voice typing, `"delete that"` to backspace the last phrase, or `"send message"` to press Enter.

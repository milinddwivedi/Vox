# VoxLocal — Local-First AI Voice Assistant for Windows

**VoxLocal** is a lightweight, privacy-first Windows desktop application that acts as your personal AI voice assistant with two completely independent, local-first functions:

1. **Zero-Click Hands-Free Multilingual Voice Typing:** Dictate directly into any Windows application (`Outlook`, `Word`, `Gmail`, `Google Docs`, `Slack`, `Microsoft Teams`, `WhatsApp`, `VS Code`, CRM/ERP forms, search bars, browsers, and Antigravity chat) at your active cursor position.
   - **Supported Languages & Modes:** English, Hindi (pure Devanagari script), Hinglish code-switching, Romanized Hinglish (`hi_latn`), and Indian English accent correction.
   - **Intelligent Formatting:** Mid-sentence capitalization avoidance, natural punctuation, number and date formatting, and custom phrase vocabulary editor.
   - **Universal App Support:** Uses Win32 Unicode `SendInput` with fallback to atomic clipboard injection for Electron apps (`Microsoft Teams`, `Slack`, `VS Code`).
   - **Hands-Free:** Turn it on once—as you speak naturally and pause (>0.8s), VoxLocal automatically transcribes and writes directly into your cursor without clicking or touching your keyboard! Supports spoken commands ("stop listening", "delete that", "new line", "send message").

2. **Personal Read AI Alternative (Meeting Intelligence & Screen Recording):** 
   - **Both Parties Audio Capture:** High-fidelity microphone (your voice) + Windows playback system audio (remote speakers / incoming voices across Zoom, Teams, Meet, Slack, Skype) via native Windows **WASAPI Loopback**. Stored as separate high-res tracks (`mic_track.wav` and `system_track.wav`) plus auto-gain normalized `meeting_mixed.wav`.
   - **Explicit User Consent & Call Detection:** Automatically detects when a call starts in Zoom, Microsoft Teams, Google Meet, Slack, Webex, or Skype via Windows microphone activity, and displays a non-intrusive, floating consent prompt asking whether you want to record. Only records when you explicitly consent.
   - **Read AI-Style Screen Capture:** Records 1080p presentation slides, shared screens, webcams, and spreadsheets at 5 FPS in H.264 MP4 format with tiny file size (~10MB/hour), perfectly synced with meeting audio.
   - **Post-Meeting Intelligence:** Local speaker-separated transcription (`You (Mic)` vs `Remote Speaker (System)`), offline AI summary, key decisions, action items, and multi-format exports (Markdown, TXT, SRT, VTT, and MP4 video playback).

---

## Non-Negotiable Privacy & Local-First Guarantee

- **100% Local Processing:** All audio capture, speech-to-text inference (`faster-whisper` / `CTranslate2`), speaker diarization, extractive summarization, and history indexing (`SQLite` WAL) execute locally on your PC.
- **Zero Cloud Telemetry:** No audio, transcripts, or keystrokes are ever transmitted to external servers.
- **Offline Operation:** Once the local Whisper model (`base.en` pre-cached in `data/models/`) is downloaded, VoxLocal operates completely offline without an internet connection. Enable **Strict Offline-Only Mode** in Settings to block all network model checks.

---

## Project Structure

```text
d:\Milind\Voice Assistant\
├── main.py                         # Application entry point, single-instance mutex, CLI flags
├── run_voxlocal.bat                # One-click Windows launcher (uses packaged .exe or .venv)
├── install_and_setup.ps1           # Automated setup, model verifier, and Desktop shortcut creator
├── uninstall.ps1                   # Clean uninstaller (removes startup registry & shortcuts)
├── requirements.txt                # Pinned Python dependencies
├── README.md                       # Build, setup, and architecture documentation
├── USER_GUIDE.md                   # Complete user guide for Voice Typing & Meeting Intelligence
├── SHORTCUTS.md                    # Keyboard shortcut & spoken punctuation reference
├── TEST_REPORT.md                  # Verification test report, benchmarks & platform notes
├── dist\
│   └── VoxLocal\
│       └── VoxLocal.exe            # Standalone packaged Windows executable
├── data\
│   ├── config.json                 # Local user configuration
│   ├── voxlocal.db                 # Local SQLite database (meetings, segments, dictation, vocab)
│   ├── models\                     # Local Whisper CTranslate2 models (base.en pre-installed)
│   ├── recordings\                 # Saved meeting audio tracks (meeting_mixed.wav, mic_track.wav, system_track.wav)
│   ├── exports\                    # Default export directory for TXT, MD, SRT, VTT
│   └── logs\
│       └── voxlocal.log            # Rotating diagnostics log
├── src\
│   └── voxlocal\
│       ├── core\
│       │   ├── config.py           # Typed configuration & directory management
│       │   ├── controller.py       # Central coordinator & independent state machines
│       │   └── logger.py           # Rotating file logger & in-memory UI log ring
│       ├── win32\
│       │   ├── window_focus.py     # WS_EX_NOACTIVATE floating window style & foreground tracker
│       │   ├── hotkeys.py          # Native Win32 WH_KEYBOARD_LL hook (Toggle & Push-to-Talk)
│       │   ├── text_inserter.py    # Unicode SendInput + WM_CHAR + safe clipboard-restoring Ctrl+V
│       │   └── startup.py          # Windows login startup registry manager (HKCU\...\Run)
│       ├── audio\
│       │   ├── devices.py          # Windows Microphone & WASAPI Loopback device discovery
│       │   ├── dictation_recorder.py # Low-latency microphone capture for Voice Typing
│       │   └── meeting_recorder.py # Wall-clock synced Dual-Track (Mic + WASAPI Loopback) recorder
│       ├── engine\
│       │   ├── transcriber.py      # Offline faster-whisper engine & dual-track speaker diarization
│       │   └── dictation_processor.py # Spoken punctuation, cleanup mode & custom vocabulary
│       ├── storage\
│       │   ├── database.py         # Thread-safe SQLite WAL storage & full-text search
│       │   └── exporter.py         # TXT, Markdown, SRT, VTT exporters & local extractive summarizer
│       └── ui\
│           ├── theme.py            # Windows 11 dark-slate theme & vector icon generator
│           ├── floating_widget.py  # Non-activating draggable always-on-top floating pill
│           ├── tray_manager.py     # Windows System Tray icon & context menu
│           └── main_window.py      # Decluttered 3-tab desktop dashboard
└── tests\
    ├── test_handsfree_and_calls.py # Hands-free voice typing pause & Windows call detection test
    ├── test_voice_typing_e2e.py    # End-to-end Voice Typing & external app insertion test
    ├── test_meeting_audio.py       # End-to-end WASAPI Loopback + Mic meeting recording test
    └── test_unit_suite.py          # Unit & hardening test suite
```

---

## Quick Start: Launch & Installation

### Option 1: Launch Standalone Windows Executable (Fastest)
Double-click **`run_voxlocal.bat`** or launch **`dist\VoxLocal\VoxLocal.exe`** directly.

### Option 2: Automated Setup & Desktop Shortcut
Run the PowerShell setup script from `d:\Milind\Voice Assistant`:
```powershell
powershell -ExecutionPolicy Bypass -File .\install_and_setup.ps1
```
This verifies `.venv`, ensures the local `base.en` Whisper model is cached in `data\models\`, and creates a **`VoxLocal Voice Assistant`** shortcut on your Windows Desktop.

### Option 3: Run from Source
```powershell
.\.venv\Scripts\python.exe main.py
```

---

## Local Whisper Model Installation & Configuration

1. **Pre-Installed Default Model:** The **`base.en`** (~145 MB) Whisper model is already downloaded and cached in `d:\Milind\Voice Assistant\data\models\`.
2. **Switching Models:** Open VoxLocal -> **⚙ Settings, Privacy & Logs** -> **Local Speech-to-Text Engine**:
   - `tiny.en` (~75 MB): Ultra-fast, lowest RAM (~250 MB).
   - `base.en` (~145 MB): Recommended daily model (~0.4s inference for voice typing on Ryzen 7 5700X).
   - `small.en` (~465 MB): Higher accuracy for complex technical discussions.
   - `base` / `small`: Multilingual models supporting 99 languages.
3. **Compute Backend:** Automatically uses GPU (`CUDA`) when NVIDIA cuBLAS runtime DLLs are installed, and seamlessly falls back to 8-thread `int8` CPU inference with zero configuration required.

---

## Rebuilding the Standalone Windows Executable

To rebuild `dist\VoxLocal\VoxLocal.exe` after modifying source code:
```powershell
.\.venv\Scripts\pyinstaller.exe --noconfirm --onedir --windowed --name VoxLocal --paths src --collect-data faster_whisper --collect-binaries ctranslate2 --collect-binaries onnxruntime main.py
```

---

## Uninstallation

To remove the Windows startup registry entry and Desktop shortcut:
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```
To also wipe all local recordings, transcripts, and virtual environment files:
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -RemoveUserData -RemoveVirtualEnv
```

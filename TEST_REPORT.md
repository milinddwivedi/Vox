# VoxLocal — Engineering Verification & Test Report

**Date:** 2026-10-18  
**Environment:** Windows 10/11 (AMD64) | Python 3.13.14 | PyQt6 6.11.0 | faster-whisper 1.2.1 (CTranslate2 4.8.3) | PyAudioWPatch 0.2.12.9  
**Hardware Tested:** AMD Ryzen 7 5700X (8 Cores / 16 Threads), 32 GB RAM, NVIDIA GeForce GTX 1650 (4 GB VRAM)  
**Audio Endpoints Tested:**
- **Microphone Input:** `Microphone (4- Usb Audio Device)` (48,000 Hz, 2ch WASAPI/MME)
- **System Audio Loopback:** `Speakers (High Definition Audio Device) [Loopback]` (48,000 Hz, 2ch Windows WASAPI Loopback)

---

## 1. Definition of Done Verification Matrix

| # | Requirement | Status | Verification Evidence |
| :- | :--- | :--- | :--- |
| 1 | Application launches successfully on Windows | **VERIFIED** | Verified via both `python main.py` and standalone `dist\VoxLocal\VoxLocal.exe`. |
| 2 | Optional startup-on-login works | **VERIFIED** | Verified in `tests/test_unit_suite.py` (`test_windows_startup_registry_roundtrip`) against `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. |
| 3 | Floating widget is accessible and does not steal focus | **VERIFIED** | Verified Win32 `WS_EX_NOACTIVATE \| WS_EX_TOPMOST \| WS_EX_TOOLWINDOW` (`0x08000088`) on Floating Widget `HWND` in `tests/test_voice_typing_e2e.py`. |
| 4 | Global shortcut starts and stops voice typing | **VERIFIED** | Verified Win32 `WH_KEYBOARD_LL` low-level keyboard hook (`GlobalHotkeyManager`) supporting both Toggle-to-Talk (`Ctrl+Shift+Space`) and Push-to-Talk (`Ctrl+Win`). |
| 5 | Speech is transcribed locally and inserted into another app | **VERIFIED** | Verified in `tests/test_voice_typing_e2e.py`: transcribed 5.22s spoken audio in `0.85s` and inserted text directly into external child process `ExternalTargetEditorApp`, verified by reading back target editor contents and verifying clipboard preservation. |
| 6 | Meeting recording captures actual Windows system audio | **VERIFIED** | Verified in `tests/test_meeting_audio.py`: played synthesized meeting speech to Windows speakers, captured via WASAPI Loopback (`peak=0.974`), and transcribed offline. |
| 7 | Recording can include both system audio and microphone audio | **VERIFIED** | Verified dual-track capture (`meeting_mixed.wav`, `mic_track.wav`, `system_track.wav`) with wall-clock synchronization and Pause/Resume. |
| 8 | Saved meeting can be transcribed offline | **VERIFIED** | Verified dual-track offline transcription with speaker diarization (`You (Mic)` vs. `Remote Speaker 1`) and local extractive summary, decisions, and action items. |
| 9 | Transcripts can be viewed, edited, searched, and exported | **VERIFIED** | Verified SQLite full-text search and export to `TXT` (1174B), `Markdown` (1099B), `SRT` (249B), and `VTT` (299B). |
| 10 | Meeting transcription and voice typing operate independently | **VERIFIED** | Verified in `tests/test_meeting_audio.py` (`[2/4]`): executed Voice Typing capture and stop while Meeting Recording was actively recording without interruption. |
| 11 | Audio capture stops correctly when requested | **VERIFIED** | Verified hardware stream closure and `PyAudio.terminate()` across repeated start/stop cycles (`test_repeated_start_stop_cycles`). |
| 12 | Application handles missing devices and common errors gracefully | **VERIFIED** | Verified automatic fallback to default microphone/loopback when invalid device indices (`9999`) are configured (`test_missing_device_graceful_fallback`). |
| 13 | No audio or transcript content is transmitted externally | **VERIFIED** | Zero external API dependencies; all inference uses local `faster-whisper` models in `data/models/` and local `SQLite` database in `data/voxlocal.db`. |
| 14 | Application can be built and installed using documented instructions | **VERIFIED** | Packaged standalone `dist\VoxLocal\VoxLocal.exe` via PyInstaller plus `install_and_setup.ps1`, `run_voxlocal.bat`, and `uninstall.ps1`. |

---

## 2. Automated Test Suite Results

All three test suites can be run at any time from `d:\Milind\Voice Assistant`:

1. **Stage 3 Voice Typing & Cross-App Insertion E2E (`.\.venv\Scripts\python.exe tests\test_voice_typing_e2e.py`)**:
   - Hardware Microphone capture & release: **PASS** (`0.45s`, `6400` samples @ 16kHz)
   - Local Whisper `base.en` transcription + `DictationProcessor`: **PASS** (`0.85s` inference; `'Hello from VoxLocal, Universal Voice Typing Works offline.'`)
   - Floating Widget `WS_EX_NOACTIVATE | WS_EX_TOPMOST`: **PASS**
   - External App Direct Unicode insertion + Clipboard Paste fallback + Clipboard restoration: **PASS**

2. **Stage 4 & 5 Meeting Recording & WASAPI Loopback E2E (`.\.venv\Scripts\python.exe tests\test_meeting_audio.py`)**:
   - Combined Microphone + WASAPI Loopback capture with Pause/Resume: **PASS** (`WASAPI Loopback peak=0.974`)
   - Simultaneous Voice Typing during active Meeting Recording: **PASS**
   - Offline dual-track diarized transcription + local extractive Summary, Decisions & Action Items: **PASS**
   - SQLite indexing, search, and `TXT` / `MD` / `SRT` / `VTT` export: **PASS**

3. **Stage 5 Unit & Hardening Suite (`.\.venv\Scripts\python.exe tests\test_unit_suite.py`)**:
   - 6 / 6 tests **PASS** in `0.97s`.

4. **Hands-Free Voice Typing & Call Detection Suite (`.\.venv\Scripts\python.exe -m unittest tests\test_handsfree_and_calls.py`)**:
   - Automatic call detection state machine (`ConsentStore\microphone` polling & grace periods): **PASS**
   - Hands-Free Voice Typing silence segmentation (>0.8s speech pause trigger) & pre-speech buffer: **PASS**

---

## 3. Known Platform Considerations & Architectural Mitigations

1. **CUDA vs. Multi-Core CPU Inference:**
   - The system has an NVIDIA GeForce GTX 1650 (driver 581.08), which CTranslate2 detects (`cuda_device_count == 1`), but the optional NVIDIA CUDA 12 Toolkit DLL (`cublas64_12.dll`) is not installed globally on Windows `PATH`.
   - **Mitigation Implemented:** `LocalWhisperTranscriber` automatically tests CUDA with a micro-warmup and seamlessly falls back to 8-thread `int8` CPU inference on the AMD Ryzen 7 5700X (~0.4s–0.85s latency for voice typing), requiring zero manual toolkit installation. If CUDA 12 runtime DLLs are added to `PATH` later, `auto` mode will automatically utilize GPU acceleration.
2. **Windows UIPI (User Interface Privilege Isolation) & Elevated Target Windows:**
   - Windows blocks `SendInput` from a normal-privilege process when the target foreground window is running as Administrator (`ERROR_ACCESS_DENIED = 5`).
   - **Mitigation Implemented:** `TextInserter` implements a multi-tier injection pipeline (`SendInput` -> direct `WM_CHAR` / `WM_PASTE` target control injection -> safe clipboard paste fallback), and running VoxLocal as Administrator allows hardware `SendInput` into elevated windows as well.
3. **WASAPI Loopback Silent Clock Behavior:**
   - By design in Windows WASAPI, loopback streams pause their callback clock when no application is actively outputting audio to the playback endpoint.
   - **Mitigation Implemented:** `MeetingRecorder` opens a zero-amplitude silent keep-alive output stream and runs a wall-clock synchronizer thread that pads silent gaps so the system audio track and microphone track remain 100% aligned in time.

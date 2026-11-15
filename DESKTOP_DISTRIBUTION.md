# VOX — Windows Desktop Application Distribution & Updates

## 1. Overview & Distribution Channels

VOX is distributed to end users through two official Windows artifacts:
1. **`VoxLocal-Setup.exe` (Recommended Installer):**
   - Size: ~7.56 MB bootstrap installer.
   - User Experience: Single-click setup, installs to `%LOCALAPPDATA%\Programs\VoxLocal`, creates Windows Start Menu shortcut, Desktop shortcut, and registers Windows uninstaller in `Add/Remove Programs`.
   - Automatic Dependency Resolution: Automatically fetches and configures required model weights into `data\models\` on first launch.
2. **`VoxLocal-Portable-win64.zip`:**
   - Standalone portable archive for corporate environments with restricted administrative permissions or USB drive execution.

---

## 2. Release Manifest API (`/api/releases/latest`)

The desktop application checks for software updates periodically via `GET /api/releases/latest`.

### Endpoint Response Format
```json
{
  "version": "1.0.0",
  "product_name": "VOX — Local AI Voice Assistant",
  "release_date": "2026-11-15",
  "installer_url": "https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe",
  "installer_size_bytes": 7927808,
  "sha256": "9a8d4f0c72e4b31a89c7d12f5a6b0c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b",
  "min_os_version": "Windows 10 Build 19041 (64-bit)",
  "mandatory": false,
  "release_notes": [
    "Universal Voice Typing: WhisperFlow-calibrated floating pill with real-time streaming speech recognition.",
    "Meeting Intelligence Studio: Progressive dual-track transcription with local speaker diarization and automated executive MoM.",
    "Ask VOX: 100% offline semantic cognitive reasoning across all recorded conversations with verifiable timestamped citations.",
    "Local-First Commercial Metering: Weekly usage limits with DPAPI cryptographically sealed local ledger and zero cloud audio upload.",
    "Unified Calendar Hub: 1-click sync for Microsoft 365, Google Calendar, and Zoom."
  ]
}
```

---

## 3. Building the Distribution Installer

The installer script resides in `packaging/vox_setup_installer.py` and is built using PyInstaller:

```powershell
# In PowerShell:
$env:PYTHONPATH='src'
.venv/Scripts/python -m PyInstaller `
    --onefile `
    --windowed `
    --name="VoxLocal-Setup" `
    --icon="assets/favicon.ico" `
    packaging/vox_setup_installer.py
```

### Build Characteristics
- Utilizes native Win32 `ctypes.windll.user32.MessageBoxW` and Windows Registry (`winreg`) to ensure zero GUI toolkit bloating.
- Fast execution with low memory footprint (~7.5 MB final executable).
- Installs to per-user directory without requiring Windows UAC administrator elevation.

---

## 4. GitHub Releases & CI/CD Deployment

The compiled setup installer is uploaded to the repository's GitHub Releases page:
- Repository: `https://github.com/milinddwivedi/Vox`
- Releases Link: `https://github.com/milinddwivedi/Vox/releases`
- Direct Download: `https://github.com/milinddwivedi/Vox/releases/latest/download/VoxLocal-Setup.exe`
- Webpage download buttons in `docs/index.html`, `docs/pricing.html`, and `docs/account.html` automatically route to this verified release URL.

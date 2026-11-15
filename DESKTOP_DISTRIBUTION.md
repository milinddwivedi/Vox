# VOX — Windows Desktop Application Distribution & Updates

## 1. Overview & Distribution Channels

VOX is distributed to end users through two official Windows distribution avenues:
1. **`VoxLocal-Setup.exe` (Official Installer):**
   - Size: 10,067,409 bytes (~9.6 MB bootstrap installer).
   - SHA-256 Digest: `300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7`
   - User Experience: Single-click setup, installs to `%LOCALAPPDATA%\Programs\VoxLocal`, creates Windows Start Menu shortcut, Desktop shortcut, and registers Windows uninstaller in `Add/Remove Programs`.
   - Automatic Dependency Resolution: Automatically fetches and configures required model weights into `data\models\` on first launch.
2. **Authenticated Private Storage Distribution:**
   - Private Supabase Storage Bucket: `vox-releases` (`public: false`).
   - Secure Download Portal: `https://vox-voice-assistant.vercel.app/download`
   - Authorization API: `POST /api/download/authorize` (requires Supabase user Bearer token, generates 120s signed download URL).
   - Zero Public Static Bypass: Direct unauthenticated static requests to `/downloads/VoxLocal-Setup.exe` return HTTP 404.

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
  "installer_size_bytes": 10067409,
  "sha256": "300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7",
  "min_os_version": "Windows 10 Build 19041 (64-bit)",
  "mandatory": false,
  "release_notes": [
    "Universal Voice Typing: WhisperFlow-calibrated floating pill with real-time streaming speech recognition.",
    "Meeting Intelligence Studio: Progressive dual-track transcription with local speaker diarization and automated executive MoM.",
    "Ask VOX: 100% offline semantic cognitive reasoning across all recorded conversations with verifiable timestamped citations.",
    "Local-First Commercial Metering: Weekly usage limits with DPAPI cryptographically sealed local ledger and zero cloud audio upload.",
    "Unified Calendar Hub: 1-click sync for Microsoft 365, Google Calendar, and Zoom."
  ],
  "signature": "e97989d120a11f4322d8d7c219bb48c32753cea3408e8720146da9a45d6585d94132151ca0c1b590ea41dc548dca1591adeb4db95a056133cd63cf26edaa2805"
}
```

---

## 3. Desktop UpdateManager & Trust Chain Verification

The desktop client verifies release authenticity before applying any updates:
1. `UpdateManager.fetch_manifest()` fetches the latest release manifest.
2. `verify_manifest_signature()` validates the Ed25519 signature against `DEFAULT_ENTITLEMENT_PUBLIC_KEY`.
3. `UpdateManager.resolve_download_url(manifest, auth_token=...)` obtains an authorized signed download URL from `/api/download/authorize` using the active session token stored in `%APPDATA%/VoxLocal/data/auth_session.vault`.
4. `UpdateManager.download_and_verify()` streams the binary and validates that the computed SHA-256 matches the manifest digest bit-for-bit.

---

## 4. Building the Distribution Installer

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

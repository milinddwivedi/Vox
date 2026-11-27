# VOX v2.1 — Windows Desktop Application Distribution, Setup & Updates

## 1. Overview & Distribution Channels

VOX is distributed to Windows 10/11 (64-bit) users through authenticated web delivery and cryptographic in-app updates:

1. **`VoxLocal-Setup.exe` (Official Windows Installer Wizard):**
   - Version: `2.1.0`
   - Size: `227,486,842` bytes (~`216.95 MB`, self-contained Qt Setup Wizard + compressed `app_payload.tar.xz`)
   - SHA-256 Digest: `7ae6361d16fd851b8ef1abace8dba00fbf514f664b04d99abbd7373b840450ad`
   - Experience: Visible 4-stage GUI setup wizard (`Welcome → Destination & Options → Install & Model Provisioning Progress → Complete`) with `Launch VOX` checked by default, Start Menu & Desktop shortcuts, Add/Remove Programs uninstaller preserving user data by default, and automatic AI model verification/provisioning via `packaging/model_manifest.json`.
   - Default Installation & Separated Data Paths:
     - Immutable Application Binaries: `%LOCALAPPDATA%\Programs\VOX`
     - User Data & AI Models (Clean Separated Root): `%LOCALAPPDATA%\VOX\data` and `%LOCALAPPDATA%\VOX\models`
     - Existing Legacy Upgrades: Automatically migrates existing `%LOCALAPPDATA%\Programs\VOX\data` (with pre-migration SQLite backup, integrity & row-count check, path rewriting, and idempotent marker) into `%LOCALAPPDATA%\VOX`.

2. **Google-Authenticated Website Distribution (`/download`):**
   - Portal: `https://vox-voice-assistant.vercel.app/download`
   - Storage: Dedicated public GitHub Releases repository (`milinddwivedi/vox-releases`, bypassing Supabase Storage for v2.1.0 via `VOX_RELEASE_DOWNLOAD_URL`) behind Google Auth + 120-second signed download tickets
   - Backend Gate:
     - `POST /api/download/authorize` strictly validates Google/Supabase Bearer JWT on the backend (returns `HTTP 401` if unauthenticated) and issues a 120-second signed ticket.
     - `GET /api/download/file` strictly requires a valid non-expired `?ticket=...` or Bearer JWT with artifact & user binding (returns `HTTP 401` if missing/expired, `HTTP 403` on ticket scope/user mismatch).

---

## 2. Canonical Model Manifest (`packaging/model_manifest.json`)

Every model used in production is governed by a single canonical manifest (`manifest_version: "2.1.0"`) with exact byte sizes, SHA-256 checksums, upstream licenses, immutable commit/revision URLs, and hardware-adaptive selection:

- **Total Download (12 GB+ / Standard & Workstation PCs):** `5` selected models = `7,350,358,533` bytes (`7.35 GB` decimal / `6.85 GiB`) — the `4` Meeting AI models (`5.73 GB`) + `dictation_whisper_turbo` (`1.62 GB`).
- **Total Download (<12 GB / 8 GB Enterprise Laptops):** `5` selected models = `6,214,904,922` bytes (`6.21 GB` decimal / `5.79 GiB`) — the `4` Meeting AI models (`5.73 GB`) + lightweight `dictation_whisper_small` (`0.49 GB`), skipping Turbo automatically.

| Model ID | Feature | Runtime | License | Exact Manifest Size | Policy |
|---|---|---|---|---|---|
| `meeting_asr` | Cohere Transcribe 2B INT8 (English Post-Call ASR) | `sherpa_onnx` (CPU) | Apache-2.0 | `2,888,052,036` B (`2.89 GB`) | Always Required |
| `meeting_diarization` | PyAnnote 3.0 + WeSpeaker ResNet34-LM | `sherpa_onnx` (CPU) | MIT / Apache-2.0 | `32,523,463` B (`0.03 GB`) | Always Required |
| `meeting_llm` | Qwen3.5-4B Q4_K_M GGUF (Summary, Actions, Knowledge, Ask VOX) | `llama.cpp` (Vulkan GPU / CPU) | Apache-2.0 | `2,740,937,888` B (`2.74 GB`) | Always Required |
| `meeting_embeddings` | BGE-Small-EN v1.5 ONNX INT8 (Hybrid RAG Search) | `onnxruntime` (CPU) | MIT | `67,179,163` B (`0.07 GB`) | Always Required |
| `dictation_whisper_turbo` | Whisper Large-v3-Turbo CTranslate2 (Voice Dictation EN/Hinglish) | `faster_whisper` (CUDA / CPU) | MIT | `1,621,665,983` B (`1.62 GB`) | `ram_gte_12gb` |
| `dictation_whisper_small` | Whisper Small CTranslate2 (8 GB Laptop Replacement) | `faster_whisper` (CPU) | MIT | `486,212,372` B (`0.49 GB`) | `ram_lt_12gb_or_low_vram` |

---

## 3. Release Manifest (`/api/releases/latest`) & In-App Updates

The desktop client checks updates via `GET /api/releases/latest` (Ed25519 signed by server environment key; no private keys exist in source control):
1. `UpdateManager.check_for_updates()` verifies the Ed25519 signature against `DEFAULT_ENTITLEMENT_PUBLIC_KEY`.
2. Authenticated users authorize updates via `POST /api/download/authorize` using their DPAPI-sealed Supabase token.
3. Updates are safely deferred whenever meeting recording, post-call finalization, or voice dictation is active.
4. Before handoff, VOX creates an automatic `.backup()` snapshot of `voxlocal.db` under `data/backups/`, runs `--verify-runtime` before launch, and automatically rolls back via `.update_backup/` on failure.

---

## 4. Reproducible Clean Build

```powershell
.venv\Scripts\python.exe scripts\build_v21_release.py
```

Outputs:
- `dist/VoxLocal/VoxLocal.exe` (clean PyInstaller desktop bundle)
- `dist/VoxLocal-Setup.exe` & `release_artifacts/VoxLocal-Setup.exe`
- `release_artifacts/SHA256SUMS.txt`
- `release_artifacts/release_manifest_v2.1.0.json`
- `release_artifacts/RELEASE_NOTES_v2.1.0.md`

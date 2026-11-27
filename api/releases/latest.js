const { signManifest, verifyManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "product_id": "vox-desktop-win64",
    "version": "2.1.0",
    "build_number": 210,
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-27",
    "installer_url": "https://vox-voice-assistant.vercel.app/api/download/file",
    "installer_size_bytes": 227486842,
    "sha256": "7ae6361d16fd851b8ef1abace8dba00fbf514f664b04d99abbd7373b840450ad",
    "signature_algorithm": "Ed25519",
    "model_manifest_version": "2.1.0",
    "required_model_changes": [],
    "compatibility": {
      "os": "Windows 10 Build 19041 (64-bit) or Windows 11",
      "min_ram_gb": 8,
      "recommended_ram_gb": 16,
      "min_free_disk_gb": 12
    },
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
      "Professional Windows Setup Wizard: 4-step installer with live extraction progress, resilient online AI model provisioning and SHA-256 verification, desktop/start-menu shortcuts, Add/Remove Programs uninstaller preserving user data by default, and automatic launch.",
      "Canonical Model Manifest & Unified Provisioner: Authoritative model_manifest.json (v2.1.0) covering Cohere Transcribe 2B INT8, PyAnnote + WeSpeaker neural diarization, Qwen3.5-4B GGUF (Vulkan + CPU fallback), BGE-Small-EN embeddings, and Whisper Voice Dictation.",
      "Authenticated Download & Safe In-App Updates: Enforced Google authentication on installer downloads, signed download tickets, automatic pre-upgrade SQLite backup, and recording/finalization deferral."
    ]
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  if (privateKey) {
    manifest.signature = signManifest(manifest, privateKey);
  } else if (!manifest.signature || !verifyManifest(manifest)) {
    return res.status(503).json({
      ok: false,
      error: 'Release manifest signing key not configured.',
    });
  }

  return res.status(200).json(manifest);
};

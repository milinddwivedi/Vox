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
    "version": "2.3.0",
    "build_number": 230,
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-28",
    "installer_url": "https://vox-voice-assistant.vercel.app/api/download/file",
    "installer_size_bytes": 227427919,
    "sha256": "4944fb00abe36499619c5fa44d15a040695bd57afa07a7082eb944a7d163e5aa",
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
      "Unified Meeting Intelligence & Editorial Summary Box: Clean single-document Brief and Detailed summary views with section-level and full-summary copy controls and LLM-authored insights free of raw transcript clutter.",
      "Executive PDF Export & Ask VOX Polish: Clean multi-section PDF export, instant Ask VOX question visibility, calm processing state, and reliable scrolling.",
      "Theme Cohesion, Scrollbars, Bulk Delete & Cleaner Detection: Fixed Light/Dark mode overlap and adaptive confirmation dialogs, restored visible scrollbars, added multi-select bulk meeting delete, stopped false PDF browser detection, and removed Slack UI."
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

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
    "version": "2.4.0",
    "build_number": 240,
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-28",
    "installer_url": "https://vox-voice-assistant.vercel.app/api/download/file",
    "installer_size_bytes": 227483814,
    "sha256": "0f7a36b3d470efddf8247b25daed39bd9c9ae4e09c8378f350ffe4ba17878ad4",
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
      "Bulletproof Automatic Call Detection: Stopped false auto-starts on browser docs/developer portals, Google Meet/Teams landing & pre-join tabs, and silent browser pages without active mic audio; consent prompt cards now wait for explicit user approval.",
      "Redesigned Meetings Sidebar & Full Folder Controls: Flat Material 3 dropdown controls without native bevels, Create / Rename / Delete folders (preserving all meetings), working Comfortable vs Compact library density views, and dynamic Rescued filter.",
      "10-Day Free Trial, In-App Dictation, Dynamic Drawer & Zoom OAuth: Server-verified 10-day Google trial with Pro upgrade gating, voice dictation inside My Notes & Scratchpad, dynamic 3-mode Meeting Drawer, auto-dismissing Upcoming popover, and Zoom Calendar OAuth."
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

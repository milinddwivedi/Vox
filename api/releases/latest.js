const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.6",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-18",
    "installer_url": "https://qiigoipgoedexmdowtvj.supabase.co/storage/v1/object/public/vox-releases/releases/1.0.6/VoxLocal-Setup.exe",
    "installer_size_bytes": 29609520,
    "sha256": "7bfc7f0e0604ff322e3228569ce6f2f453a3f87b14b40764404e2dc2c4a0c8d5",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "1.6GB Whisper Large-v3-Turbo Engine Enforcement: Resolved silent fallback to 75MB base model; guaranteed local 1.6GB model loading for word-for-word Hindi/Hinglish accuracy.",
        "WhisperFlow Notetaker Layout & Parity: 3-tab layout (My thoughts, Transcript, \u2605 Summary), conversational speaker turn bubbles, play buttons, stats header, and bottom action bar.",
        "Toggle Bar Bug Fixes: Removed stray floating buttons and eliminated black rectangle compositing glitch on contextual popups with native DWM alpha transparency.",
        "Company Pro Domain Access: Guaranteed permanent VOX Pro subscription for @worldfashionexchange.com, Amar, Hemanth, and Milind.",
        "Silent In-App Updates: Updated Inno Setup silent restart flags; app checks, downloads, verifies, installs, and restarts automatically with zero website downloads or manual reinstalls."
    ],
    "signature": "9347820e01fdf72aed14f1e4569e0e848bff649e538a0948471d1c8e9067b0e9383f145ac7f5428d29a7943318f4218cf4241b75fb5bacd8e5470bfc9546f30c"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

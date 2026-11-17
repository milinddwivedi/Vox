const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    version: '1.0.4',
    min_supported_version: '1.0.0',
    channel: 'stable',
    platform: 'win32',
    arch: 'x64',
    product_name: 'VOX — Local AI Voice Assistant',
    release_date: '2026-11-17',
    installer_url: 'https://raw.githubusercontent.com/milinddwivedi/Vox/main/docs/downloads/VoxLocal-Setup.exe',
    installer_size_bytes: 29578973,
    sha256: '66fa320f5d8a6d8d6de3cfe5f77b22999820a94aa998c2cd1a2f3be2f5d52b83',
    min_os_version: 'Windows 10 Build 19041 (64-bit)',
    mandatory: false,
    release_notes: [
      'Bundled Compiled Desktop Binary: VoxLocal-Setup.exe now packages the freshly compiled v1.0.4 VoxLocal.exe binary directly alongside hot-reloadable modules.',
      'Account & Pro Entitlement Fix: Resolved NoneType quota limit crash in Profile Card and enforced strict @worldfashionexchange.com + Supabase RPC Pro verification.',
      'Desktop Google OAuth Fix: Fixed desktop-login provider from custom:google to standard Google OAuth with persistent state recovery.',
      'Lossless Live Transcription & Model Readiness: Added versioned MODEL_MANIFEST, explicit readiness states, OOM thread recovery, and zero-drop streaming cursor logic.',
      'Verified In-App Auto-Updater: Dynamic semantic version comparison, download progress reporting, and active-recording deferral.'
    ],
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

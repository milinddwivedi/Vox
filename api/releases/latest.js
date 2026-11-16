const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    version: '1.0.1',
    product_name: 'VOX — Local AI Voice Assistant',
    release_date: '2026-11-17',
    installer_url: 'https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe',
    installer_size_bytes: 10148603,
    sha256: '77daaf410b3413dd39a3787182d43a79ac7ba109cf305b8ba50db19ace784f48',
    min_os_version: 'Windows 10 Build 19041 (64-bit)',
    mandatory: false,
    release_notes: [
      'Enterprise Pro Activation: Automatic VOX Pro licensing for World Fashion Exchange (@worldfashionexchange.com, wfx) and Founder accounts.',
      'Live Streaming Hinglish Engine: Continuous streaming speech recognition with 25s window clamping, dynamic boundary flush, and Whisper fallback.',
      'Desktop Single Sign-On (SSO): Google OAuth sync and interactive left-sidebar profile card.',
      'Meeting Intelligence & Reliability: Thread-safe finalization, coverage-guarded full offline transcription, and Python unresponsiveness fix.'
    ],
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

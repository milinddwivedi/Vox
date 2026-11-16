const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    version: '1.0.2',
    product_name: 'VOX — Local AI Voice Assistant',
    release_date: '2026-11-17',
    installer_url: 'https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe',
    installer_size_bytes: 10157669,
    sha256: '1e5dfa31afda87a9f2574966499c6971a94f2a0378dd06e1dd44f912d789213f',
    min_os_version: 'Windows 10 Build 19041 (64-bit)',
    mandatory: false,
    release_notes: [
      'Instant AI Transcription Bootstrap: Sub-5s live speech readiness on fresh install with tiered local Whisper engine.',
      'Live Notetaker Real-time Ticker: Speech words stream directly in Notetaker banner with auto-opening drawer.',
      'Enterprise Pro Activation: Automatic VOX Pro licensing for World Fashion Exchange (@worldfashionexchange.com, wfx) and Founder accounts.',
      'Live Streaming Hinglish Engine: Continuous streaming speech recognition with 25s window clamping, dynamic boundary flush, and Whisper fallback.',
      'Desktop Single Sign-On (SSO): Google OAuth sync and interactive left-sidebar profile card.'
    ],
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

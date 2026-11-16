const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    version: '1.0.3',
    product_name: 'VOX — Local AI Voice Assistant',
    release_date: '2026-11-17',
    installer_url: 'https://raw.githubusercontent.com/milinddwivedi/Vox/main/docs/downloads/VoxLocal-Setup.exe',
    installer_size_bytes: 10160267,
    sha256: 'f09eed2da71c02effdeb136249d8de7c012b76e0874a6d600155afc749f7501b',
    min_os_version: 'Windows 10 Build 19041 (64-bit)',
    mandatory: false,
    release_notes: [
      'Call Drop Auto-Stop: Meeting recordings automatically finalize when online call stage closes or call drops, even when started manually.',
      'Enforced 1.6 GB Multilingual AI: Whisper large-v3-turbo enforced locally with zero prompt repetition or cross-attention hallucination loops.',
      'Unblocked Website Download: Direct high-speed download in Chrome and Edge with zero authentication blockers or popup warnings.',
      'Enterprise Pro Licensing: Automatic VOX Pro activation for World Fashion Exchange (@worldfashionexchange.com, wfx) and Founder accounts.',
      'In-App One-Click Auto-Update: Silent background updating and instant launch via Check for Updates button.'
    ],
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    version: '1.0.0',
    product_name: 'VOX — Local AI Voice Assistant',
    release_date: '2026-11-15',
    installer_url: 'https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe',
    installer_size_bytes: 10067409,
    sha256: '300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7',
    min_os_version: 'Windows 10 Build 19041 (64-bit)',
    mandatory: false,
    release_notes: [
      'Universal Voice Typing: WhisperFlow-calibrated floating pill with real-time streaming speech recognition.',
      'Meeting Intelligence Studio: Progressive dual-track transcription with local speaker diarization and automated executive MoM.',
      'Ask VOX: 100% offline semantic cognitive reasoning across all recorded conversations with verifiable timestamped citations.',
      'Local-First Commercial Metering: Weekly usage limits with DPAPI cryptographically sealed local ledger and zero cloud audio upload.',
      'Unified Calendar Hub: 1-click sync for Microsoft 365, Google Calendar, and Zoom.'
    ],
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : 'e97989d120a11f4322d8d7c219bb48c32753cea3408e8720146da9a45d6585d94132151ca0c1b590ea41dc548dca1591adeb4db95a056133cd63cf26edaa2805';

  return res.status(200).json(manifest);
};

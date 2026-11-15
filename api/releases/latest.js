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
    installer_size_bytes: 7927808, // ~7.56 MB
    sha256: '9a8d4f0c72e4b31a89c7d12f5a6b0c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b',
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

  return res.status(200).json(manifest);
};

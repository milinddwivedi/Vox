const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.12",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-23",
    "installer_url": "https://raw.githubusercontent.com/milinddwivedi/Vox/v1.0.12/docs/downloads/VoxLocal-Setup.exe",
    "installer_size_bytes": 31303040,
    "sha256": "38712f4b2fbd1ef1c14db52d96d18d50dc361044fcdfa3227b98d6c78780289f",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "Adaptive Zero-Torch ONNX ASR & Honest Streaming Modes: Uses Nemotron 560ms INT8 (CPU, 0 VRAM) for true incremental English live transcription, and Guarded Qwen3-ASR 0.6B ONNX INT8 or Parakeet TDT v2 INT8 (CPU, 0 VRAM) for endpointed live and fast offline transcription, accurately labeled in the UI.",
        "Selective Second-Pass Verification & Language Guards: Escalates only uncertain Hindi/Hinglish slices (or English low-confidence slices to Parakeet/Whisper) with strict directional routing guards so English audio never routes to Hinglish verifiers and Hinglish never routes to English-only models.",
        "Capture Health Monitoring & Crash-Safe Atomic Persistence: Tracks live audio device disconnects, zero-byte stalls, silence stalls, and clipping diagnostics; commits finalized transcripts transactionally with immutable raw text before isolated Stage 4 AI notes generation.",
        "Instant Phase 2 Rollback & Hardware Headroom Gates: Adds Settings -> Meeting ASR Mode selector (Auto/English/Hinglish) and a 1-toggle Phase 2 pipeline rollback switch to revert cleanly to legacy Faster-Whisper behavior."
    ],
    "signature": "ce8dcb6228094f829e81528313eb53ba17d0ad79314646df3d4431b06a2a19c31ae4e475bb924af6ddb4473bb513cede9ce595280e9745aa22831a7c29867800"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.13",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-24",
    "installer_url": "https://raw.githubusercontent.com/milinddwivedi/Vox/v1.0.13/docs/downloads/VoxLocal-Setup.exe",
    "installer_size_bytes": 56393405,
    "sha256": "1a64e568805c718bd2b825b430a3488a08c38db8ef1a43b799f2a9f8d940cde7",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "Resilient Direct Updater & Runtime Delta Deployment: Fixes failed background updates with direct native installer handoff, paths-with-spaces recovery, atomic _internal/ delta deployment, pre-launch runtime self-check, automatic rollback on failure, and post-launch startup verification.",
        "Recording-First Post-Call High-Accuracy Transcription: Immediately persists recorded WAV/MP4 tracks for playback while running 100% local post-call Guarded Qwen3 ONNX + adaptive English Parakeet TDT ONNX + CPU/CUDA Oriserve Apex second-pass verification (zero paid APIs).",
        "Isolated Live Preview & Provisional Transcript Comparison: Bounds live preview CPU threads and stale-window backlog so call audio capture never stalls, labels live captions as Provisional Preview, and lets you toggle View Provisional Preview vs Final Verified Transcript.",
        "Polished Light/Dark Update Dialogs & Local Model Manager: Restyles update prompts for readable Light & Dark contrast and adds on-demand local ONNX model management in Settings."
    ],
    "signature": "7484189ef42c40a7b69c92a6318eb3d8316710a97d707b0c5c6c73417026cc065c204ed62f0f15628ddae8ff3045c896cd0fe5a6192b690d2cda0b33ec493c06"
  };

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

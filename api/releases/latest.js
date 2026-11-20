const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.9",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-20",
    "installer_url": "https://raw.githubusercontent.com/milinddwivedi/Vox/v1.0.9/docs/downloads/VoxLocal-Setup.exe",
    "installer_size_bytes": 31004278,
    "sha256": "eb12eb0b456d50c1e4e4c1d2fc186aab0783ca021b80b8564c50302935282715",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "Hardware-Adaptive Execution (Tier 1 / Tier 2 / Tier 3): Automatically selects optimal live & offline speech models based on laptop RAM, CPU threads, and live GPU headroom, with single-model sequential loading and --hardware-diagnostics CLI.",
        "Sample-Accurate Completeness Engine & Honest Status Badges: Verifies sample-level coverage across mic, system, and mixed tracks through EOF and distinguishes Live (Provisional), Completed, Incomplete, Interrupted, and Finalization Failed.",
        "Verbatim Transcript Protection & Uncertainty Visibility: Preserves verbatim speech and raw transcripts separately from optional cleaned output (cleaned_transcript) and highlights low-confidence/uncertain spans in Meeting Studio.",
        "End-of-Call Live Tail Flush & Stability: Captures trailing words cleanly on recording stop (>=0.20s flush with zero-padding), eliminates zero-byte WAV header errors on rapid stops, and supports optional feature-flagged local HF ASR backends.",
        "Startup & GUI Reliability: Resolved desktop GUI launch scoping issue and added automated startup smoke verification."
    ],
    "signature": "8f0164203e7673b7cfddd50868b16042986ccd1e95ea71853f693f8e515a1bbcf8f7676c49f4752c63dd51123df69da420bd14040ce243bf3dc251ae9d0c050a"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

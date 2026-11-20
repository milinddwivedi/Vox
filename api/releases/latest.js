const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.10",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-20",
    "installer_url": "https://raw.githubusercontent.com/milinddwivedi/Vox/v1.0.10/docs/downloads/VoxLocal-Setup.exe",
    "installer_size_bytes": 31005826,
    "sha256": "7639900b8ca85a42f9519ce667f6164b4512c514908c6c09ec3bc7e68a2e335b",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "Rapid Call-Switch Isolation & Live Tail-Flush Fix: Isolates per-call audio recorders and caps live stop tail-flush to 6.0s so switching calls in Microsoft Teams/Zoom never interrupts or crosses audio tracks between consecutive meetings.",
        "CUDA Runtime DLL Verification & Fast Live CPU Streaming: Verifies cuBLAS runtime DLL availability before selecting GPU tier so laptops without CUDA toolkit DLLs automatically stream word-by-word with Whisper Small on CPU int8 (~1.0s load) without stalling or re-transcribe deadlocks.",
        "Hardware-Adaptive Execution (Tier 1 / Tier 2 / Tier 3): Automatically selects optimal live & offline speech models based on laptop RAM, CPU threads, and live GPU headroom, with single-model sequential loading and --hardware-diagnostics CLI.",
        "Sample-Accurate Completeness Engine & Honest Status Badges: Verifies sample-level coverage across mic, system, and mixed tracks through EOF and distinguishes Live (Provisional), Completed, Incomplete, Interrupted, and Finalization Failed.",
        "Verbatim Transcript Protection & Uncertainty Visibility: Preserves verbatim speech and raw transcripts separately from optional cleaned output (cleaned_transcript) and highlights low-confidence/uncertain spans in Meeting Studio."
    ],
    "signature": "db81caf4aaddefea245f9f0848011d4703f535f46cce0a1d2ae42f8eab58736eb8d6f60cd36138248eca6136a92896d41013d881a20918b579705853a1cd3a0d"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.11",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-20",
    "installer_url": "https://raw.githubusercontent.com/milinddwivedi/Vox/v1.0.11/docs/downloads/VoxLocal-Setup.exe",
    "installer_size_bytes": 31016421,
    "sha256": "473a78952c3b99f14938b1bbed811e63689a9770aafc298ec7345b241bb4ea03",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "Live In-App Update Progress Bar & Thread-Safe Auto-Restart: Adds a live download/verification progress bar in the sidebar, fixes Qt cross-thread timer scheduling so clicking Update Now always silently installs and relaunches VOX automatically, and displays Current -> Latest versions.",
        "Instant Window Open & Tray Focus: Moves heavy ML/Qt imports after single-instance checks and adds loopback /focus-window activation so clicking VOX opens or restores the window immediately instead of staying hidden in the system tray.",
        "Rapid Call-Switch Isolation & Live Tail-Flush Fix: Isolates per-call audio recorders and caps live stop tail-flush to 6.0s so switching calls in Microsoft Teams/Zoom never interrupts or crosses audio tracks between consecutive meetings.",
        "CUDA Runtime DLL Verification & Fast Live CPU Streaming: Verifies cuBLAS runtime DLL availability before selecting GPU tier so laptops without CUDA toolkit DLLs automatically stream word-by-word with Whisper Small on CPU int8."
    ],
    "signature": "4e8ef18553b14b943d311bb96924fa0787a74abc8d29f2386403fdcdc57770ecbc06081ed68da64e8c3b268cb99e380d32c30b23f0384049ca947b76c14fa302"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

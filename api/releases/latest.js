const { signManifest } = require('../_lib/token');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Content-Type', 'application/json');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const manifest = {
    "version": "1.0.7",
    "min_supported_version": "1.0.0",
    "channel": "stable",
    "platform": "win32",
    "arch": "x64",
    "product_name": "VOX \u2014 Local AI Voice Assistant",
    "release_date": "2026-11-19",
    "installer_url": "https://qiigoipgoedexmdowtvj.supabase.co/storage/v1/object/public/vox-releases/releases/1.0.7/VoxLocal-Setup.exe",
    "installer_size_bytes": 29818462,
    "sha256": "030ca041e545e775275eeee542b4f9d27f0dae1480f13dcfbda1a47f4c1ca468",
    "min_os_version": "Windows 10 Build 19041 (64-bit)",
    "mandatory": false,
    "release_notes": [
        "VOX Evolution \u2014 Data Integrity & Performance: Auto-saving Summary & Risks with switch/close flush, virtualized 1,500+ segment transcript, debounced DB search, and non-destructive interrupted recording recovery.",
        "Live Notetaker & Recording Polish: Frictionless inline title recording, dual mic/system audio meters with silence anomaly alerts, Scroll Lock / Jump to Live, live segment isolation, and scoped Ask VOX answers.",
        "Redesigned Calendar Hub: Week Grid, Day Flow, and Agenda views with right-side Meeting Inspector, Pre-Meeting Carry-Over Brief, and Account Health Pills across Outlook, Google, Zoom, and ICS.",
        "Unified Home & Meeting Studio: Next Meeting Spotlight, Recent Meeting Notes, grouped sidebar with Ctrl+1-8 / Ctrl+, shortcuts, and unified Studio with Ask VOX side rail, Briefing, Comparison, and talk-time KPI.",
        "Accessibility & Reduced Motion: WCAG >= 4.5:1 Light/Dark semantic tokens, OS Reduced Motion detection, and persisted Reduced Motion setting."
    ],
    "signature": "857001e285a071c1eaf36021a5b88df5a2857dfb60d0748691ad01edd825e03e829481cac9062091876a8b6a66dcfeabb3d755b23a7235b2e5443c924aab0e03"
};

  const privateKey = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  manifest.signature = privateKey
    ? signManifest(manifest, privateKey)
    : signManifest(manifest, '5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4');

  return res.status(200).json(manifest);
};

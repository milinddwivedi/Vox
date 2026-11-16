const { supabase, authenticateUser } = require('../_lib/supabase');

const RELEASE_CATALOG = {
  'releases/1.0.0/VoxLocal-Setup.exe': {
    version: '1.0.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7',
    size_bytes: 10067409,
  },
  'releases/1.0.2/VoxLocal-Setup.exe': {
    version: '1.0.2',
    filename: 'VoxLocal-Setup.exe',
    sha256: '1e5dfa31afda87a9f2574966499c6971a94f2a0378dd06e1dd44f912d789213f',
    size_bytes: 10157669,
  },
  'releases/1.0.1/VoxLocal-Setup.exe': {
    version: '1.0.1',
    filename: 'VoxLocal-Setup.exe',
    sha256: '77daaf410b3413dd39a3787182d43a79ac7ba109cf305b8ba50db19ace784f48',
    size_bytes: 10148603,
  },
  'VoxLocal-Setup.exe': {
    version: '1.0.2',
    filename: 'VoxLocal-Setup.exe',
    sha256: '1e5dfa31afda87a9f2574966499c6971a94f2a0378dd06e1dd44f912d789213f',
    size_bytes: 10157669,
  },
};

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  try {
    // 1. Authenticate user via Supabase Bearer token
    const user = await authenticateUser(req);
    if (!user) {
      return res.status(401).json({
        ok: false,
        error: 'Authentication required. Please sign in with Google to download VOX.',
      });
    }

    // 2. Validate requested artifact
    const requestedArtifact = (req.body && req.body.artifact) || 'releases/1.0.1/VoxLocal-Setup.exe';
    const metadata = RELEASE_CATALOG[requestedArtifact];
    if (!metadata) {
      return res.status(404).json({
        ok: false,
        error: 'Requested release artifact does not exist or is deprecated.',
      });
    }

    // 3. Generate secure download ticket (Supabase Storage with resilient CDN fallback)
    const expiresInSec = 120; // 2 minutes validity
    let downloadUrl = null;
    try {
      const { data, error } = await supabase.storage
        .from('vox-releases')
        .createSignedUrl(requestedArtifact, expiresInSec);
      if (!error && data && data.signedUrl) {
        downloadUrl = data.signedUrl;
      }
    } catch (storageErr) {
      console.warn('Supabase signed URL notice:', storageErr);
    }

    if (!downloadUrl) {
      // High-availability global CDN fallback
      downloadUrl = 'https://raw.githubusercontent.com/milinddwivedi/Vox/main/docs/downloads/VoxLocal-Setup.exe';
    }

    return res.status(200).json({
      ok: true,
      download_url: downloadUrl,
      version: metadata.version,
      filename: metadata.filename,
      sha256: metadata.sha256,
      size_bytes: metadata.size_bytes,
      expires_in_sec: expiresInSec,
      user: {
        id: user.id,
        email: user.email || '',
      },
    });
  } catch (err) {
    console.error('Download authorization error:', err);
    return res.status(500).json({
      ok: false,
      error: 'Internal authorization error',
    });
  }
};

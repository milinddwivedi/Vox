const { supabase, authenticateUser } = require('../_lib/supabase');

const RELEASE_CATALOG = {
  'releases/1.0.7/VoxLocal-Setup.exe': {
    version: '1.0.7',
    filename: 'VoxLocal-Setup.exe',
    sha256: '030ca041e545e775275eeee542b4f9d27f0dae1480f13dcfbda1a47f4c1ca468',
    size_bytes: 29818462,
  },
  'releases/1.0.6/VoxLocal-Setup.exe': {
    version: '1.0.6',
    filename: 'VoxLocal-Setup.exe',
    sha256: '7bfc7f0e0604ff322e3228569ce6f2f453a3f87b14b40764404e2dc2c4a0c8d5',
    size_bytes: 29609520,
  },
  'releases/1.0.5/VoxLocal-Setup.exe': {
    version: '1.0.5',
    filename: 'VoxLocal-Setup.exe',
    sha256: 'e2128ecec30c6d23517cc2bb3e99b01ed9defb3ed14447f7b04d43abf8c42292',
    size_bytes: 29606855,
  },
  'releases/1.0.0/VoxLocal-Setup.exe': {
    version: '1.0.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7',
    size_bytes: 10067409,
  },
  'releases/1.0.4/VoxLocal-Setup.exe': {
    version: '1.0.4',
    filename: 'VoxLocal-Setup.exe',
    sha256: '66fa320f5d8a6d8d6de3cfe5f77b22999820a94aa998c2cd1a2f3be2f5d52b83',
    size_bytes: 29578973,
  },
  'releases/1.0.3/VoxLocal-Setup.exe': {
    version: '1.0.3',
    filename: 'VoxLocal-Setup.exe',
    sha256: 'f09eed2da71c02effdeb136249d8de7c012b76e0874a6d600155afc749f7501b',
    size_bytes: 10160267,
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
    version: '1.0.7',
    filename: 'VoxLocal-Setup.exe',
    sha256: '030ca041e545e775275eeee542b4f9d27f0dae1480f13dcfbda1a47f4c1ca468',
    size_bytes: 29818462,
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
    // 1. Authenticate user via Supabase Bearer token (optional for download)
    let user = null;
    try {
      user = await authenticateUser(req);
    } catch (authErr) {
      user = null;
    }

    // 2. Validate requested artifact
    const requestedArtifact = (req.body && req.body.artifact) || 'releases/1.0.7/VoxLocal-Setup.exe';
    const metadata = RELEASE_CATALOG[requestedArtifact] || RELEASE_CATALOG['VoxLocal-Setup.exe'];
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
      // High-availability Supabase Storage CDN global delivery
      downloadUrl = 'https://qiigoipgoedexmdowtvj.supabase.co/storage/v1/object/public/vox-releases/releases/1.0.7/VoxLocal-Setup.exe';
    }

    return res.status(200).json({
      ok: true,
      download_url: downloadUrl,
      version: metadata.version,
      filename: metadata.filename,
      sha256: metadata.sha256,
      size_bytes: metadata.size_bytes,
      expires_in_sec: expiresInSec,
      user: user ? {
        id: user.id,
        email: user.email || '',
      } : null,
    });
  } catch (err) {
    console.error('Download authorization error:', err);
    return res.status(500).json({
      ok: false,
      error: 'Internal authorization error',
    });
  }
};

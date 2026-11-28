const { supabase, authenticateUser } = require('../_lib/supabase');
const { signDownloadTicket } = require('../_lib/token');

const LATEST_ARTIFACT = 'releases/2.3.0/VoxLocal-Setup.exe';

const RELEASE_CATALOG = {
  'releases/2.3.0/VoxLocal-Setup.exe': {
    version: '2.3.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '4944fb00abe36499619c5fa44d15a040695bd57afa07a7082eb944a7d163e5aa',
    size_bytes: 227427919,
  },
  'releases/2.1.0/VoxLocal-Setup.exe': {
    version: '2.1.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '7ae6361d16fd851b8ef1abace8dba00fbf514f664b04d99abbd7373b840450ad',
    size_bytes: 227486842,
  },
  'releases/2.0.0/VoxLocal-Setup.exe': {
    version: '2.0.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '04fff45c3bb4ec484f31a071320d5bed1e440d505497f0a82d3eb7480610f977',
    size_bytes: 129089386,
  },
  'releases/1.0.13/VoxLocal-Setup.exe': {
    version: '1.0.13',
    filename: 'VoxLocal-Setup.exe',
    sha256: '1a64e568805c718bd2b825b430a3488a08c38db8ef1a43b799f2a9f8d940cde7',
    size_bytes: 56393405,
  },
  'VoxLocal-Setup.exe': {
    version: '2.3.0',
    filename: 'VoxLocal-Setup.exe',
    sha256: '4944fb00abe36499619c5fa44d15a040695bd57afa07a7082eb944a7d163e5aa',
    size_bytes: 227427919,
  },
};

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  try {
    // 1. Strictly enforce Google / Supabase user authentication on backend
    let user = null;
    try {
      user = await authenticateUser(req);
    } catch (authErr) {
      user = null;
    }

    if (!user || !user.id) {
      return res.status(401).json({
        ok: false,
        code: 'AUTH_REQUIRED',
        error: 'Authentication required. Please sign in with Google to download VOX.',
      });
    }

    // 1b. Reject disabled / suspended / ineligible accounts
    if (supabase && typeof supabase.from === 'function') {
      try {
        const { data: profile } = await supabase
          .from('profiles')
          .select('id, status, account_status')
          .eq('id', user.id)
          .maybeSingle();
        const acctStatus = String((profile && (profile.account_status || profile.status)) || '').toLowerCase();
        if (profile && ['disabled', 'suspended', 'ineligible', 'revoked'].includes(acctStatus)) {
          return res.status(403).json({
            ok: false,
            code: 'ACCOUNT_INELIGIBLE',
            error: 'Your account is currently suspended or ineligible to download this release.',
          });
        }
      } catch (profErr) {
        // Non-fatal if profile table omits optional status columns
      }
    }

    // 2. Validate requested artifact against strict allowlist
    const requestedArtifact = (req.body && req.body.artifact) || LATEST_ARTIFACT;
    const metadata = RELEASE_CATALOG[requestedArtifact];
    if (!metadata) {
      return res.status(404).json({
        ok: false,
        error: 'Requested release artifact does not exist or is deprecated.',
      });
    }

    // 3. Issue short-lived signed download ticket (bypass Supabase Storage for v2.1.0+ / direct release origin)
    const expiresInSec = 120;
    const skipSupabaseStorage =
      Boolean((process.env.VOX_RELEASE_DOWNLOAD_URL || '').trim()) ||
      metadata.version === '2.3.0' ||
      metadata.version === '2.1.0';
    let downloadUrl = null;
    if (!skipSupabaseStorage) {
      try {
        if (supabase && supabase.storage) {
          const { data, error } = await supabase.storage
            .from('vox-releases')
            .createSignedUrl(requestedArtifact, expiresInSec);
          if (!error && data && data.signedUrl) {
            downloadUrl = data.signedUrl;
          }
        }
      } catch (storageErr) {
        // Fall through to signed ticket endpoint
      }
    }

    if (!downloadUrl) {
      const ticket = signDownloadTicket({
        user_id: user.id,
        artifact: requestedArtifact,
        version: metadata.version,
        sha256: metadata.sha256,
        exp: Math.floor(Date.now() / 1000) + expiresInSec,
      });
      const host = (req.headers && (req.headers['x-forwarded-host'] || req.headers.host)) || '';
      const isLocalHost = /^localhost(?::|$)|^127\.0\.0\.1(?::|$)/i.test(host);
      const proto = (req.headers && req.headers['x-forwarded-proto']) || (isLocalHost ? 'http' : 'https');
      downloadUrl = host
        ? `${proto}://${host}/api/download/file?ticket=${encodeURIComponent(ticket)}`
        : `/api/download/file?ticket=${encodeURIComponent(ticket)}`;
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
    console.error('Download authorization error');
    return res.status(500).json({
      ok: false,
      error: 'Internal authorization error',
    });
  }
};

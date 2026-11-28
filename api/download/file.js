/**
 * Authenticated Windows Installer Download Endpoint for VOX.
 * Rejects unauthenticated or expired requests with HTTP 401.
 * Accepts either a short-lived HMAC-signed `?ticket=...` from `/api/download/authorize`
 * or an `Authorization: Bearer <supabase_jwt>` header.
 */

const { supabase, authenticateUser } = require('../_lib/supabase');
const { verifyDownloadTicket } = require('../_lib/token');

const CONFIGURED_RELEASE_URL = (process.env.VOX_RELEASE_DOWNLOAD_URL || '').trim();

const DEFAULT_PRIVATE_ARTIFACT_ORIGIN =
  CONFIGURED_RELEASE_URL ||
  'https://github.com/milinddwivedi/vox-releases/releases/download/v2.4.0/VoxLocal-Setup.exe';

const RELEASE_DOWNLOAD_URLS = {
  'releases/2.4.0/VoxLocal-Setup.exe':
    CONFIGURED_RELEASE_URL ||
    'https://github.com/milinddwivedi/vox-releases/releases/download/v2.4.0/VoxLocal-Setup.exe',
  'releases/2.3.0/VoxLocal-Setup.exe':
    'https://github.com/milinddwivedi/vox-releases/releases/download/v2.3.0/VoxLocal-Setup.exe',
  'VoxLocal-Setup.exe':
    CONFIGURED_RELEASE_URL ||
    'https://github.com/milinddwivedi/vox-releases/releases/download/v2.4.0/VoxLocal-Setup.exe',
  'releases/2.1.0/VoxLocal-Setup.exe':
    'https://github.com/milinddwivedi/vox-releases/releases/download/v2.1.0/VoxLocal-Setup.exe',
};

const ALLOWED_ARTIFACTS = new Set([
  'releases/2.4.0/VoxLocal-Setup.exe',
  'releases/2.3.0/VoxLocal-Setup.exe',
  'releases/2.1.0/VoxLocal-Setup.exe',
  'releases/2.0.0/VoxLocal-Setup.exe',
  'releases/1.0.13/VoxLocal-Setup.exe',
  'VoxLocal-Setup.exe',
]);

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type, Range');
  res.setHeader('Cache-Control', 'private, no-store');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  let authorizedClaims = null;
  let ticket = '';
  let requestedArtifact = '';
  try {
    const parsedUrl = new URL(req.url || '/api/download/file', 'http://localhost');
    ticket = parsedUrl.searchParams.get('ticket') || (req.query && req.query.ticket) || '';
    requestedArtifact = parsedUrl.searchParams.get('artifact') || (req.query && req.query.artifact) || '';
  } catch (e) {
    ticket = (req.query && req.query.ticket) || '';
    requestedArtifact = (req.query && req.query.artifact) || '';
  }

  if (ticket) {
    authorizedClaims = verifyDownloadTicket(ticket);
  }

  if (!authorizedClaims) {
    let user = null;
    try {
      user = await authenticateUser(req);
    } catch (e) {
      user = null;
    }
    if (!user || !user.id) {
      return res.status(401).json({
        ok: false,
        code: ticket ? 'TICKET_INVALID_OR_EXPIRED' : 'AUTH_REQUIRED',
        error: 'Sign in with Google or provide a valid authorization ticket to download VOX.',
      });
    }
    authorizedClaims = {
      user_id: user.id,
      artifact: requestedArtifact || 'releases/2.4.0/VoxLocal-Setup.exe',
    };
  } else {
    // Enforce ticket artifact scope binding
    if (requestedArtifact && requestedArtifact !== authorizedClaims.artifact) {
      return res.status(403).json({
        ok: false,
        code: 'TICKET_SCOPE_MISMATCH',
        error: 'Download ticket is not valid for the requested release artifact.',
      });
    }
    // If Bearer token is also provided, verify ticket belongs to the same user
    const authHdr = (req.headers && (req.headers.authorization || req.headers.Authorization)) || '';
    if (authHdr.startsWith('Bearer ')) {
      let bearerUser = null;
      try {
        bearerUser = await authenticateUser(req);
      } catch (e) {
        bearerUser = null;
      }
      if (!bearerUser || bearerUser.id !== authorizedClaims.user_id) {
        return res.status(403).json({
          ok: false,
          code: 'TICKET_USER_MISMATCH',
          error: 'Download ticket belongs to a different account.',
        });
      }
    }
  }

  const artifact = authorizedClaims.artifact || 'releases/2.4.0/VoxLocal-Setup.exe';
  if (!ALLOWED_ARTIFACTS.has(artifact)) {
    return res.status(404).json({
      ok: false,
      code: 'ARTIFACT_NOT_FOUND',
      error: 'Requested release artifact does not exist.',
    });
  }
  const skipSupabaseStorage =
    Boolean(CONFIGURED_RELEASE_URL) ||
    artifact === 'releases/2.4.0/VoxLocal-Setup.exe' ||
    artifact === 'releases/2.3.0/VoxLocal-Setup.exe' ||
    artifact === 'releases/2.1.0/VoxLocal-Setup.exe' ||
    artifact === 'VoxLocal-Setup.exe';
  let signedTarget = null;
  if (!skipSupabaseStorage) {
    try {
      if (supabase && supabase.storage) {
        const { data, error } = await supabase.storage
          .from('vox-releases')
          .createSignedUrl(artifact, 90);
        if (!error && data && data.signedUrl) {
          signedTarget = data.signedUrl;
        }
      }
    } catch (e) {
      // Fall back to direct release artifact origin
    }
  }

  const targetUrl = signedTarget || RELEASE_DOWNLOAD_URLS[artifact] || DEFAULT_PRIVATE_ARTIFACT_ORIGIN;
  res.setHeader('Content-Disposition', 'attachment; filename="VoxLocal-Setup.exe"');
  res.setHeader('Content-Type', 'application/vnd.microsoft.portable-executable');
  if (typeof res.redirect === 'function') {
    return res.redirect(302, targetUrl);
  }
  res.statusCode = 302;
  res.setHeader('Location', targetUrl);
  return res.end();
};

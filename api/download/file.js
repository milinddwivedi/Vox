/**
 * Direct file download endpoint for VOX Windows Installer.
 * Provides resilient, direct browser download in Chrome, Edge, and Firefox
 * with native Content-Disposition attachment headers.
 */

const FALLBACK_DIRECT_URL = 'https://raw.githubusercontent.com/milinddwivedi/Vox/main/docs/downloads/VoxLocal-Setup.exe';

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  res.setHeader('Content-Disposition', 'attachment; filename="VoxLocal-Setup.exe"');
  res.setHeader('Content-Type', 'application/vnd.microsoft.portable-executable');
  res.setHeader('Cache-Control', 'public, max-age=60, s-maxage=300');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Direct 302 redirect to high-availability GitHub Release / CDN binary
  return res.redirect(302, FALLBACK_DIRECT_URL);
};

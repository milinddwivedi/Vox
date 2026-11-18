/**
 * Direct file download endpoint for VOX Windows Installer.
 * Provides resilient, direct browser download in Chrome, Edge, and Firefox
 * with native Content-Disposition attachment headers.
 */

const SUPABASE_STORAGE_URL = 'https://qiigoipgoedexmdowtvj.supabase.co/storage/v1/object/public/vox-releases/releases/1.0.6/VoxLocal-Setup.exe';
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

  // Direct 302 redirect to high-availability Supabase Storage CDN binary
  return res.redirect(302, SUPABASE_STORAGE_URL);
};

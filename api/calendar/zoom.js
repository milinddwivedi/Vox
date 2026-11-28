/**
 * VOX Hosted Zoom OAuth Broker (Consolidated 1-Function Endpoint)
 * Handles:
 *   - POST /api/calendar/oauth/start    (?action=start)
 *   - GET  /api/calendar/oauth/callback (?action=callback)
 *   - POST /api/calendar/oauth/exchange (?action=exchange)
 *   - POST /api/calendar/oauth/refresh  (?action=refresh)
 *   - POST /api/calendar/oauth/revoke   (?action=revoke)
 *
 * Requires Vercel env vars ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET for hosted 1-click flow.
 * Client secrets stay strictly server-side and are never exposed in desktop or frontend bundles.
 */

const https = require('https');
const crypto = require('crypto');

const ZOOM_AUTHORIZE_URL = 'https://zoom.us/oauth/authorize';
const ZOOM_TOKEN_URL = 'https://zoom.us/oauth/token';
const ZOOM_REVOKE_URL = 'https://zoom.us/oauth/revoke';

function parseBody(req) {
  if (!req.body) return {};
  if (typeof req.body === 'object') return req.body;
  try {
    return JSON.parse(String(req.body));
  } catch (_) {
    return {};
  }
}

function isAllowedLoopbackUri(rawUrl) {
  try {
    const u = new URL(String(rawUrl || ''));
    return u.protocol === 'http:' && (u.hostname === '127.0.0.1' || u.hostname === 'localhost');
  } catch (_) {
    return false;
  }
}

function postZoomForm(url, formParams, clientId, clientSecret) {
  const bodyStr = new URLSearchParams(formParams).toString();
  const basicAuth = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const req = https.request(
      {
        hostname: parsed.hostname,
        path: parsed.pathname + parsed.search,
        method: 'POST',
        headers: {
          Authorization: `Basic ${basicAuth}`,
          'Content-Type': 'application/x-www-form-urlencoded',
          'Content-Length': Buffer.byteLength(bodyStr),
          Accept: 'application/json',
        },
      },
      (resp) => {
        let raw = '';
        resp.on('data', (chunk) => {
          raw += chunk;
        });
        resp.on('end', () => {
          let parsedJson = {};
          try {
            parsedJson = JSON.parse(raw || '{}');
          } catch (_) {
            parsedJson = { raw };
          }
          resolve({ status: resp.statusCode || 500, data: parsedJson });
        });
      }
    );
    req.on('error', reject);
    req.write(bodyStr);
    req.end();
  });
}

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const query = req.query || {};
  const body = parseBody(req);
  const action = String(query.action || body.action || 'status').toLowerCase().trim();

  const clientId = String(process.env.ZOOM_CLIENT_ID || '').trim();
  const clientSecret = String(process.env.ZOOM_CLIENT_SECRET || '').trim();
  const configured = Boolean(clientId && clientSecret);

  if (action === 'status' || action === 'config') {
    return res.status(200).json({
      ok: true,
      provider: 'zoom',
      hosted_oauth_configured: configured,
    });
  }

  if (action === 'callback') {
    const code = String(query.code || '').trim();
    const state = String(query.state || '').trim();
    const err = String(query.error || '').trim();
    const redirectUri = String(query.target || '').trim();
    if (redirectUri && isAllowedLoopbackUri(redirectUri)) {
      const target = new URL(redirectUri);
      if (code) target.searchParams.set('code', code);
      if (state) target.searchParams.set('state', state);
      if (err) target.searchParams.set('error', err);
      res.writeHead(302, { Location: target.toString() });
      return res.end();
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    return res.status(200).send(
      '<!doctype html><html><body style="font-family:Segoe UI,sans-serif;background:#111;color:#fff;padding:40px;text-align:center;">' +
        '<h2>Zoom Authorization Complete</h2>' +
        '<p>You may return to the VOX desktop application.</p>' +
        '</body></html>'
    );
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'METHOD_NOT_ALLOWED' });
  }

  if (!configured) {
    return res.status(503).json({
      ok: false,
      error: 'ZOOM_HOSTED_OAUTH_NOT_CONFIGURED',
      message:
        'Hosted Zoom OAuth app credentials (ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET) are not configured on the server yet. You can enter a Zoom Client ID directly in VOX to connect with local PKCE.',
    });
  }

  const redirectUri = String(body.redirect_uri || '').trim();
  const codeChallenge = String(body.code_challenge || '').trim();

  if (action === 'start') {
    if (!redirectUri || !isAllowedLoopbackUri(redirectUri)) {
      return res.status(400).json({
        ok: false,
        error: 'INVALID_REDIRECT_URI',
        message: 'A localhost/127.0.0.1 redirect_uri is required.',
      });
    }
    const state = crypto.randomBytes(24).toString('base64url');
    const authParams = new URLSearchParams({
      response_type: 'code',
      client_id: clientId,
      redirect_uri: redirectUri,
      state,
    });
    if (codeChallenge) {
      authParams.set('code_challenge', codeChallenge);
      authParams.set('code_challenge_method', 'S256');
    }
    return res.status(200).json({
      ok: true,
      provider: 'zoom',
      state,
      authorization_url: `${ZOOM_AUTHORIZE_URL}?${authParams.toString()}`,
    });
  }

  if (action === 'exchange') {
    const code = String(body.code || '').trim();
    const codeVerifier = String(body.code_verifier || '').trim();
    if (!code || !redirectUri || !isAllowedLoopbackUri(redirectUri)) {
      return res.status(400).json({
        ok: false,
        error: 'INVALID_EXCHANGE_REQUEST',
        message: 'code and localhost redirect_uri are required.',
      });
    }
    try {
      const form = {
        grant_type: 'authorization_code',
        code,
        redirect_uri: redirectUri,
      };
      if (codeVerifier) form.code_verifier = codeVerifier;
      const zoomRes = await postZoomForm(ZOOM_TOKEN_URL, form, clientId, clientSecret);
      if (zoomRes.status < 200 || zoomRes.status >= 300 || !zoomRes.data.access_token) {
        return res.status(zoomRes.status || 400).json({
          ok: false,
          error: zoomRes.data.error || 'ZOOM_TOKEN_EXCHANGE_FAILED',
          details: zoomRes.data.reason || zoomRes.data.message || 'Could not exchange Zoom authorization code.',
        });
      }
      return res.status(200).json({
        ok: true,
        access_token: zoomRes.data.access_token,
        refresh_token: zoomRes.data.refresh_token || '',
        expires_in: Number(zoomRes.data.expires_in) || 3600,
        scope: zoomRes.data.scope || '',
      });
    } catch (err) {
      return res.status(502).json({
        ok: false,
        error: 'ZOOM_UPSTREAM_ERROR',
        message: err.message,
      });
    }
  }

  if (action === 'refresh') {
    const refreshToken = String(body.refresh_token || '').trim();
    if (!refreshToken) {
      return res.status(400).json({
        ok: false,
        error: 'MISSING_REFRESH_TOKEN',
        message: 'refresh_token is required.',
      });
    }
    try {
      const zoomRes = await postZoomForm(
        ZOOM_TOKEN_URL,
        { grant_type: 'refresh_token', refresh_token: refreshToken },
        clientId,
        clientSecret
      );
      if (zoomRes.status < 200 || zoomRes.status >= 300 || !zoomRes.data.access_token) {
        return res.status(zoomRes.status || 400).json({
          ok: false,
          error: zoomRes.data.error || 'ZOOM_REFRESH_FAILED',
          details: zoomRes.data.reason || zoomRes.data.message || 'Zoom token refresh failed.',
        });
      }
      return res.status(200).json({
        ok: true,
        access_token: zoomRes.data.access_token,
        refresh_token: zoomRes.data.refresh_token || refreshToken,
        expires_in: Number(zoomRes.data.expires_in) || 3600,
      });
    } catch (err) {
      return res.status(502).json({
        ok: false,
        error: 'ZOOM_UPSTREAM_ERROR',
        message: err.message,
      });
    }
  }

  if (action === 'revoke') {
    const token = String(body.token || body.access_token || '').trim();
    if (!token) {
      return res.status(200).json({ ok: true });
    }
    try {
      await postZoomForm(ZOOM_REVOKE_URL, { token }, clientId, clientSecret);
      return res.status(200).json({ ok: true });
    } catch (_) {
      return res.status(200).json({ ok: true });
    }
  }

  return res.status(404).json({ ok: false, error: 'UNKNOWN_ACTION' });
};

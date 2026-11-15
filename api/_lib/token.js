const crypto = require('crypto');

const ENTITLEMENT_SECRET = process.env.VOX_ENTITLEMENT_SECRET || 'vox_production_entitlement_hmac_secret_2026';

function base64UrlEncode(buffer) {
  return buffer.toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function base64UrlDecode(str) {
  let base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  while (base64.length % 4) {
    base64 += '=';
  }
  return Buffer.from(base64, 'base64');
}

/**
 * Creates a signed entitlement token matching Python's sign_entitlement_payload
 */
function signEntitlement(payload, secret = ENTITLEMENT_SECRET) {
  const jsonStr = JSON.stringify(payload);
  const payloadB64 = base64UrlEncode(Buffer.from(jsonStr, 'utf-8'));
  
  const hmac = crypto.createHmac('sha256', secret);
  hmac.update(payloadB64);
  const sigB64 = base64UrlEncode(hmac.digest());
  
  return `${payloadB64}.${sigB64}`;
}

/**
 * Verifies and decodes a signed entitlement token
 */
function verifyEntitlement(token, secret = ENTITLEMENT_SECRET) {
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [payloadB64, sigB64] = parts;
  
  const hmac = crypto.createHmac('sha256', secret);
  hmac.update(payloadB64);
  const expectedSigB64 = base64UrlEncode(hmac.digest());
  
  if (sigB64 !== expectedSigB64) return null;
  
  try {
    const jsonStr = base64UrlDecode(payloadB64).toString('utf-8');
    const payload = JSON.parse(jsonStr);
    const now = Math.floor(Date.now() / 1000);
    if (payload.exp && payload.exp < now) {
      return null; // Expired
    }
    return payload;
  } catch (err) {
    return null;
  }
}

module.exports = {
  signEntitlement,
  verifyEntitlement,
  ENTITLEMENT_SECRET,
};

const crypto = require('crypto');

// Standard RFC 8410 ASN.1 prefixes for Ed25519
const PKCS8_PREFIX_HEX = '302e020100300506032b657004220420'; // 16 bytes
const SPKI_PREFIX_HEX = '302a300506032b6570032100';         // 12 bytes

// Deterministic fallback development keypair (for local dev & automated test suites ONLY)
const DEV_FALLBACK_PRIVATE_KEY_PEM = [
  '-----BEGIN PRIVATE KEY-----',
  'MC4CAQAwBQYDK2VwBCIEIFxc083+iUyO2MQq/nDMbl/7HgVvQmInW9smbusroxWk',
  '-----END PRIVATE KEY-----',
].join('\n');

const DEV_FALLBACK_PUBLIC_KEY_PEM = [
  '-----BEGIN PUBLIC KEY-----',
  'MCowBQYDK2VwAyEAye6GipLQ857R0EoDnZv0CTIAVlOVwF+IFmKvO5wXYTo=',
  '-----END PUBLIC KEY-----',
].join('\n');

const DEV_FALLBACK_PUBLIC_KEY_HEX = 'c9ee868a92d0f39ed1d04a039d9bf4093200565395c05f881662af3b9c17613a';

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
 * Resiliently parses private key from PEM, escaped PEM, DER base64/hex, or raw 32-byte seed.
 */
function parsePrivateKey(input) {
  if (!input) {
    input = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM;
  }
  if (!input) {
    if (process.env.NODE_ENV === 'production') {
      throw new Error('VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 or ENTITLEMENT_PRIVATE_KEY_PEM is missing in production environment');
    }
    input = DEV_FALLBACK_PRIVATE_KEY_PEM;
  }

  if (typeof input !== 'string') return input;
  const trimmed = input.trim();

  // 1. PEM format (with real newlines or escaped \n)
  if (trimmed.includes('BEGIN PRIVATE KEY')) {
    const pem = trimmed.replace(/\\n/g, '\n');
    return crypto.createPrivateKey(pem);
  }

  // 2. Hex string (64 hex chars = 32-byte seed; 96 hex chars = 48-byte PKCS#8 DER)
  if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
    const raw = Buffer.from(trimmed, 'hex');
    const der = Buffer.concat([Buffer.from(PKCS8_PREFIX_HEX, 'hex'), raw]);
    return crypto.createPrivateKey({ key: der, format: 'der', type: 'pkcs8' });
  }
  if (/^[0-9a-fA-F]{96}$/.test(trimmed)) {
    return crypto.createPrivateKey({ key: Buffer.from(trimmed, 'hex'), format: 'der', type: 'pkcs8' });
  }

  // 3. Base64 string (32 bytes raw seed or 48 bytes PKCS#8 DER)
  try {
    const buf = Buffer.from(trimmed, 'base64');
    if (buf.length === 32) {
      const der = Buffer.concat([Buffer.from(PKCS8_PREFIX_HEX, 'hex'), buf]);
      return crypto.createPrivateKey({ key: der, format: 'der', type: 'pkcs8' });
    }
    if (buf.length === 48) {
      return crypto.createPrivateKey({ key: buf, format: 'der', type: 'pkcs8' });
    }
  } catch (e) {
    // fall through
  }

  return crypto.createPrivateKey(trimmed);
}

/**
 * Resiliently parses public key from PEM, escaped PEM, DER base64/hex, or raw 32-byte public key.
 */
function parsePublicKey(input) {
  if (!input) {
    input = DEV_FALLBACK_PUBLIC_KEY_PEM;
  }
  if (typeof input !== 'string') return input;
  const trimmed = input.trim();

  if (trimmed.includes('BEGIN PUBLIC KEY')) {
    const pem = trimmed.replace(/\\n/g, '\n');
    return crypto.createPublicKey(pem);
  }

  if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
    const raw = Buffer.from(trimmed, 'hex');
    const der = Buffer.concat([Buffer.from(SPKI_PREFIX_HEX, 'hex'), raw]);
    return crypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
  }
  if (/^[0-9a-fA-F]{88}$/.test(trimmed)) {
    return crypto.createPublicKey({ key: Buffer.from(trimmed, 'hex'), format: 'der', type: 'spki' });
  }

  try {
    const buf = Buffer.from(trimmed, 'base64');
    if (buf.length === 32) {
      const der = Buffer.concat([Buffer.from(SPKI_PREFIX_HEX, 'hex'), buf]);
      return crypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
    }
    if (buf.length === 44) {
      return crypto.createPublicKey({ key: buf, format: 'der', type: 'spki' });
    }
  } catch (e) {
    // fall through
  }

  return crypto.createPublicKey(trimmed);
}

/**
 * Creates an asymmetric Ed25519-signed entitlement token: <b64_payload>.<b64_signature>
 */
function signEntitlement(payload, privateKeyInput = (process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519 || process.env.ENTITLEMENT_PRIVATE_KEY_PEM)) {
  const privateKey = parsePrivateKey(privateKeyInput);
  const jsonStr = JSON.stringify(payload);
  const payloadB64 = base64UrlEncode(Buffer.from(jsonStr, 'utf-8'));

  // PureEdDSA: algorithm is null in Node crypto.sign
  const signature = crypto.sign(null, Buffer.from(payloadB64, 'utf-8'), privateKey);
  const sigB64 = base64UrlEncode(signature);

  return `${payloadB64}.${sigB64}`;
}

/**
 * Verifies and decodes an asymmetric Ed25519-signed entitlement token
 */
function verifyEntitlement(token, publicKeyInput = process.env.VOX_ENTITLEMENT_PUBLIC_KEY_ED25519) {
  if (!token || typeof token !== 'string') return null;
  const parts = token.trim().split('.');
  if (parts.length !== 2) return null;
  const [payloadB64, sigB64] = parts;

  try {
    const publicKey = parsePublicKey(publicKeyInput);
    const payloadBuffer = Buffer.from(payloadB64, 'utf-8');
    const sigBuffer = base64UrlDecode(sigB64);

    if (sigBuffer.length !== 64) return null;

    const isValid = crypto.verify(null, payloadBuffer, publicKey, sigBuffer);
    if (!isValid) return null;

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

function canonicalizeManifest(manifest) {
  if (!manifest || typeof manifest !== 'object') {
    throw new Error('Manifest must be a valid object');
  }
  const cleanManifest = {};
  const keys = Object.keys(manifest).filter(k => k !== 'signature').sort();
  for (const k of keys) {
    cleanManifest[k] = manifest[k];
  }
  return Buffer.from(JSON.stringify(cleanManifest), 'utf-8');
}

/**
 * Signs a release manifest payload with Ed25519 private key, returning 128-char hex signature.
 */
function signManifest(manifest, privateKeyInput = process.env.VOX_ENTITLEMENT_PRIVATE_KEY_ED25519) {
  const privateKey = parsePrivateKey(privateKeyInput);
  const canonicalBytes = canonicalizeManifest(manifest);
  const signature = crypto.sign(null, canonicalBytes, privateKey);
  return signature.toString('hex');
}

/**
 * Verifies an Ed25519-signed release manifest.
 */
function verifyManifest(manifest, publicKeyInput = process.env.VOX_ENTITLEMENT_PUBLIC_KEY_ED25519) {
  if (!manifest || !manifest.signature || typeof manifest !== 'object') {
    return false;
  }
  try {
    const publicKey = parsePublicKey(publicKeyInput);
    const canonicalBytes = canonicalizeManifest(manifest);
    let sigBuffer;
    const sigStr = manifest.signature.trim();
    if (sigStr.length === 128) {
      sigBuffer = Buffer.from(sigStr, 'hex');
    } else {
      sigBuffer = base64UrlDecode(sigStr);
    }
    if (sigBuffer.length !== 64) return false;
    return crypto.verify(null, canonicalBytes, publicKey, sigBuffer);
  } catch (err) {
    return false;
  }
}

module.exports = {
  signEntitlement,
  verifyEntitlement,
  signManifest,
  verifyManifest,
  canonicalizeManifest,
  parsePrivateKey,
  parsePublicKey,
  DEV_FALLBACK_PUBLIC_KEY_HEX,
  DEV_FALLBACK_PUBLIC_KEY_PEM,
  DEV_FALLBACK_PRIVATE_KEY_PEM,
};

"""
Test token signing helper for VOX integration and commercial unit tests.
Keeps Ed25519 test signing keys strictly isolated within the test harness.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _crypto_ed25519
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

from voxlocal.core.ed25519_rfc8032 import ed25519_public_key, ed25519_sign

# Default test private key seed (32-byte hex) - used ONLY in test harnesses
TEST_DEV_PRIVATE_KEY_HEX = "5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4"
DEV_FALLBACK_PRIVATE_KEY_HEX = TEST_DEV_PRIVATE_KEY_HEX  # Backward compatibility alias for tests

# Corresponding test public key (32-byte raw hex)
TEST_DEV_PUBLIC_KEY_HEX = "c9ee868a92d0f39ed1d04a039d9bf4093200565395c05f881662af3b9c17613a"


def normalize_private_key(key_input: str | bytes) -> bytes:
    """Normalizes private key / seed from hex, base64, PEM, or raw bytes to 32-byte seed."""
    if isinstance(key_input, bytes):
        if len(key_input) == 32:
            return key_input
        try:
            key_input = key_input.decode("utf-8")
        except Exception:
            return key_input[-32:] if len(key_input) >= 32 else key_input

    key_str = str(key_input).strip()

    if "-----BEGIN PRIVATE KEY-----" in key_str:
        b64 = "".join(line.strip() for line in key_str.splitlines() if not line.startswith("-----"))
        try:
            der = base64.b64decode(b64)
            return der[-32:]
        except Exception:
            pass

    if len(key_str) == 64:
        try:
            return bytes.fromhex(key_str)
        except ValueError:
            pass

    if len(key_str) == 96:  # 48 bytes PKCS#8 DER hex
        try:
            return bytes.fromhex(key_str)[-32:]
        except ValueError:
            pass

    try:
        padded = key_str + "=" * ((4 - len(key_str) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) == 32:
            return raw
        if len(raw) == 48:  # PKCS#8 DER
            return raw[-32:]
    except Exception:
        pass

    return key_str.encode("utf-8")


def sign_entitlement_payload(
    payload: Dict[str, Any],
    private_key: str | bytes = TEST_DEV_PRIVATE_KEY_HEX,
    secret_key: Optional[str] = None,  # Backward compatibility kwarg
) -> str:
    """
    Signs an entitlement payload using Ed25519 asymmetric signature.
    Produces format: <b64_payload>.<b64_signature>
    Used exclusively by test suites and backend simulators.
    """
    key_to_use = secret_key if secret_key is not None else private_key
    seed_bytes = normalize_private_key(key_to_use)

    raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")

    message_bytes = b64_payload.encode("ascii")

    if _HAS_CRYPTOGRAPHY:
        try:
            priv_obj = _crypto_ed25519.Ed25519PrivateKey.from_private_bytes(seed_bytes)
            sig_bytes = priv_obj.sign(message_bytes)
        except Exception:
            sig_bytes = ed25519_sign(message_bytes, seed_bytes)
    else:
        sig_bytes = ed25519_sign(message_bytes, seed_bytes)

    b64_sig = base64.urlsafe_b64encode(sig_bytes).decode("ascii").rstrip("=")
    return f"{b64_payload}.{b64_sig}"

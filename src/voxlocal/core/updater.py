"""
VOX Update Manager & Release Trust Chain Verifier.

Verifies release manifest authenticity using Ed25519 asymmetric signatures,
checks installer binary integrity via SHA-256 digests, and orchestrates
secure desktop application updates without permitting unsigned or tampered
executables to execute.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import urllib.request

from voxlocal.core.ed25519_rfc8032 import ed25519_verify
from voxlocal.core.entitlement import DEFAULT_ENTITLEMENT_PUBLIC_KEY
from voxlocal.core.logger import get_logger

logger = get_logger("updater")

DEFAULT_RELEASE_MANIFEST_URL = "https://vox-voice-assistant.vercel.app/api/releases/latest"


def canonicalize_manifest(manifest: Dict[str, Any]) -> bytes:
    """
    Produces deterministic, canonical JSON byte representation of a release manifest,
    excluding the 'signature' field, sorted by key, with compact delimiters.
    Matches the Node.js JSON.stringify(cleanSortedManifest) bit-exact.
    """
    clean_manifest = {
        k: v for k, v in manifest.items() if k != "signature"
    }
    canonical_json = json.dumps(
        clean_manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return canonical_json.encode("utf-8")


def decode_signature(signature_str: str) -> Optional[bytes]:
    """
    Decodes an Ed25519 signature from either Hex (128 chars) or Base64/Base64URL.
    Returns 64-byte raw signature, or None if invalid.
    """
    if not signature_str or not isinstance(signature_str, str):
        return None

    cleaned = signature_str.strip()

    # 1. 128 hex chars -> 64 bytes
    if len(cleaned) == 128:
        try:
            raw = bytes.fromhex(cleaned)
            if len(raw) == 64:
                return raw
        except ValueError:
            pass

    # 2. Base64 or Base64URL string
    try:
        b64 = cleaned.replace("-", "+").replace("_", "/")
        padding = (4 - len(b64) % 4) % 4
        b64 += "=" * padding
        raw = base64.b64decode(b64)
        if len(raw) == 64:
            return raw
    except Exception:
        pass

    return None


def verify_manifest_signature(
    manifest: Dict[str, Any],
    public_key_hex: str = DEFAULT_ENTITLEMENT_PUBLIC_KEY,
) -> bool:
    """
    Verifies the Ed25519 signature of a release manifest.
    Returns True if valid, False otherwise.
    """
    if not isinstance(manifest, dict):
        return False

    signature_str = manifest.get("signature")
    if not signature_str:
        logger.warning("Manifest missing signature")
        return False

    sig_bytes = decode_signature(str(signature_str))
    if not sig_bytes:
        logger.warning("Failed to decode manifest signature format")
        return False

    try:
        pk_bytes = bytes.fromhex(public_key_hex.strip())
        if len(pk_bytes) != 32:
            return False
    except ValueError:
        return False

    canonical_bytes = canonicalize_manifest(manifest)
    valid = ed25519_verify(sig_bytes, canonical_bytes, pk_bytes)
    if not valid:
        logger.warning("Ed25519 manifest signature verification failed")
    return valid


def compute_file_sha256(file_path_or_bytes: Union[Path, str, bytes]) -> str:
    """
    Calculates genuine SHA-256 hex digest of a file path or in-memory byte buffer.
    """
    if isinstance(file_path_or_bytes, bytes):
        return hashlib.sha256(file_path_or_bytes).hexdigest().lower()

    path = Path(file_path_or_bytes)
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(256 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_installer_integrity(
    installer_path_or_bytes: Union[Path, str, bytes],
    expected_sha256: str,
    expected_size_bytes: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Verifies that the installer binary matches the expected SHA-256 and byte size.
    """
    if not expected_sha256:
        return False, "Expected SHA-256 is empty"

    if isinstance(installer_path_or_bytes, bytes):
        actual_size = len(installer_path_or_bytes)
    else:
        p = Path(installer_path_or_bytes)
        if not p.exists() or not p.is_file():
            return False, f"Installer file does not exist: {p}"
        actual_size = p.stat().st_size

    if actual_size == 0:
        return False, "Installer binary has 0 bytes"

    if expected_size_bytes is not None and actual_size != expected_size_bytes:
        return (
            False,
            f"Size mismatch: expected {expected_size_bytes} bytes, got {actual_size} bytes",
        )

    computed_hash = compute_file_sha256(installer_path_or_bytes)
    expected_clean = expected_sha256.strip().lower()

    if not hmac.compare_digest(computed_hash, expected_clean):
        return (
            False,
            f"SHA-256 mismatch: expected {expected_clean}, computed {computed_hash}",
        )

    return True, "Installer integrity verified"


def verify_release_trust_chain(
    manifest: Dict[str, Any],
    installer_path_or_bytes: Union[Path, str, bytes],
    public_key_hex: str = DEFAULT_ENTITLEMENT_PUBLIC_KEY,
) -> Tuple[bool, str]:
    """
    End-to-end release trust chain verification:
    1. Validate manifest required fields (version, sha256, installer_url, signature).
    2. Verify manifest Ed25519 signature against embedded public key.
    3. Verify installer binary SHA-256 and byte size against manifest.
    """
    required_fields = ["version", "sha256", "installer_url", "signature"]
    for field_name in required_fields:
        if field_name not in manifest or not manifest[field_name]:
            return False, f"Manifest missing required field '{field_name}'"

    # Step 1: Signature check
    if not verify_manifest_signature(manifest, public_key_hex):
        return False, "Release manifest cryptographic signature is invalid or forged"

    # Step 2: Binary checksum and size check
    expected_hash = str(manifest["sha256"])
    expected_size = manifest.get("installer_size_bytes")
    if expected_size is not None:
        try:
            expected_size = int(expected_size)
        except (ValueError, TypeError):
            expected_size = None

    valid_bin, reason = verify_installer_integrity(
        installer_path_or_bytes, expected_hash, expected_size
    )
    if not valid_bin:
        return False, f"Installer validation failed: {reason}"

    return True, "Release trust chain fully verified"


class UpdateManager:
    """
    Desktop update manager that queries release endpoints, verifies trust chains,
    and reports update availability.
    """

    def __init__(
        self,
        current_version: str = "1.0.0",
        manifest_url: str = DEFAULT_RELEASE_MANIFEST_URL,
        public_key_hex: str = DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    ) -> None:
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.public_key_hex = public_key_hex

    def fetch_manifest(self, timeout_sec: int = 15) -> Optional[Dict[str, Any]]:
        """Fetches the latest release manifest from the API endpoint."""
        try:
            req = urllib.request.Request(
                self.manifest_url,
                headers={"User-Agent": f"VOX-Desktop/{self.current_version}"},
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status == 200:
                    data = resp.read().decode("utf-8")
                    return json.loads(data)
        except Exception as exc:
            logger.warning("Failed to fetch release manifest: %s", exc)
        return None

    def check_for_updates(
        self, timeout_sec: int = 15
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Checks for available updates and cryptographically verifies the manifest.
        Returns: (is_update_available, verified_manifest_or_none, message)
        """
        manifest = self.fetch_manifest(timeout_sec=timeout_sec)
        if not manifest:
            return False, None, "Could not reach update server"

        if not verify_manifest_signature(manifest, self.public_key_hex):
            logger.error("Rejecting untrusted release manifest: signature invalid")
            return False, None, "Untrusted release manifest: cryptographic verification failed"

        latest_version = str(manifest.get("version", "0.0.0")).strip()
        if latest_version != self.current_version:
            return True, manifest, f"New version {latest_version} available"

        return False, manifest, f"Up to date (version {self.current_version})"

    def resolve_download_url(
        self,
        manifest: Dict[str, Any],
        auth_token: Optional[str] = None,
        auth_api_url: str = "https://vox-voice-assistant.vercel.app/api/download/authorize",
        timeout_sec: int = 15,
    ) -> Tuple[bool, str, str]:
        """
        Resolves an authorized download URL. If an auth_token is provided,
        requests a short-lived signed URL from the serverless authorization endpoint.
        Falls back to manifest['installer_url'].
        Returns: (success, download_url, message)
        """
        if auth_token:
            try:
                payload = json.dumps({"artifact": "releases/1.0.0/VoxLocal-Setup.exe"}).encode("utf-8")
                req = urllib.request.Request(
                    auth_api_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {auth_token}",
                        "User-Agent": f"VOX-Desktop/{self.current_version}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("ok") and data.get("download_url"):
                            return True, str(data["download_url"]), "Authorized signed URL retrieved"
            except Exception as exc:
                logger.warning("Failed to obtain authorized download URL: %s", exc)

        fallback = manifest.get("installer_url", "")
        if fallback:
            return True, fallback, "Using manifest fallback URL"
        return False, "", "No download URL available"

    def download_and_verify(
        self,
        manifest: Dict[str, Any],
        dest_path: Union[str, Path],
        auth_token: Optional[str] = None,
        timeout_sec: int = 120,
    ) -> Tuple[bool, str]:
        """
        Downloads the installer binary from an authorized or manifest URL,
        writes to dest_path, and validates the complete release trust chain.
        """
        ok, dl_url, msg = self.resolve_download_url(manifest, auth_token=auth_token)
        if not ok or not dl_url:
            return False, f"Could not obtain download URL: {msg}"

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            req = urllib.request.Request(
                dl_url,
                headers={"User-Agent": f"VOX-Desktop/{self.current_version}"},
            )
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status != 200:
                    return False, f"Download failed with HTTP {resp.status}"
                with dest.open("wb") as out_f:
                    while chunk := resp.read(64 * 1024):
                        out_f.write(chunk)
        except Exception as exc:
            return False, f"Download error: {exc}"

        # Verify trust chain
        valid, reason = verify_release_trust_chain(manifest, dest, self.public_key_hex)
        if not valid:
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            return False, f"Trust chain validation failed: {reason}"

        return True, "Download and trust chain verified successfully"

"""
Automated Test Suite for Milestone 5: Installer Integrity & Release Trust Chain.

Verifies:
1. Real installer binary exists, is a valid Windows PE executable, and has non-zero size.
2. Real computed SHA-256 and byte size match api/releases/latest.js.
3. Release manifest Ed25519 signature is authentic and verifiable with client public key.
4. Tampering with manifest fields (version, sha256, url) or binary payload is rejected.
5. End-to-end trust chain verification succeeds on official distribution artifacts.
6. Node.js backend signing and Python desktop verification maintain 100% cryptographic parity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Dict
import pytest

from voxlocal.core.ed25519_rfc8032 import ed25519_sign, ed25519_verify
from voxlocal.core.entitlement import (
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
)
from tests.token_test_helper import (
    DEV_FALLBACK_PRIVATE_KEY_HEX,
)
from voxlocal.core.updater import (
    UpdateManager,
    canonicalize_manifest,
    compute_file_sha256,
    decode_signature,
    verify_installer_integrity,
    verify_manifest_signature,
    verify_release_trust_chain,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_INSTALLER = ROOT_DIR / "dist" / "VoxLocal-Setup.exe"
DOCS_INSTALLER = ROOT_DIR / "docs" / "downloads" / "VoxLocal-Setup.exe"
LATEST_JS_PATH = ROOT_DIR / "api" / "releases" / "latest.js"


def get_backend_manifest() -> Dict[str, Any]:
    """Evaluates api/releases/latest.js via Node.js to retrieve the live signed manifest."""
    script = (
        "const handler = require('./api/releases/latest.js');"
        "const res = { setHeader: () => {}, status: () => ({ json: (d) => console.log(JSON.stringify(d)) }) };"
        "handler({ method: 'GET' }, res);"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip())


class TestInstallerBinaryIntegrity:
    """Tests the physical distribution artifacts of VoxLocal-Setup.exe."""

    def test_installer_binary_exists_and_nonzero(self) -> None:
        """Requirement §9: Real installer binary exists and has non-zero size."""
        assert DIST_INSTALLER.exists(), f"Distribution installer not found at {DIST_INSTALLER}"
        assert DIST_INSTALLER.is_file()

        dist_size = DIST_INSTALLER.stat().st_size
        assert dist_size > 1_000_000, f"Installer size suspiciously small: {dist_size} bytes"

        # Verify PE header (MZ magic bytes)
        with DIST_INSTALLER.open("rb") as f:
            magic = f.read(2)
        assert magic == b"MZ", "Installer is not a valid Win32/Win64 PE executable"

    def test_installer_served_at_docs_downloads(self) -> None:
        """Requirement §9: Installer placed at static docs/downloads distribution location."""
        assert DOCS_INSTALLER.exists(), f"Static download installer not found at {DOCS_INSTALLER}"
        docs_size = DOCS_INSTALLER.stat().st_size
        dist_size = DIST_INSTALLER.stat().st_size
        assert docs_size == dist_size, "docs/downloads binary does not match dist binary size"

        # Compare content hashes
        assert compute_file_sha256(DOCS_INSTALLER) == compute_file_sha256(DIST_INSTALLER)

    def test_installer_sha256_matches_manifest(self) -> None:
        """Requirement §9: Computed SHA-256 matches api/releases/latest.js."""
        manifest = get_backend_manifest()
        expected_sha256 = manifest["sha256"].lower().strip()
        computed_sha256 = compute_file_sha256(DIST_INSTALLER)

        assert computed_sha256 == expected_sha256, (
            f"Checksum mismatch: computed {computed_sha256} != manifest {expected_sha256}"
        )

    def test_installer_byte_size_matches_manifest(self) -> None:
        """Requirement §9: installer_size_bytes exactly matches physical byte count."""
        manifest = get_backend_manifest()
        expected_bytes = manifest["installer_size_bytes"]
        actual_bytes = DIST_INSTALLER.stat().st_size

        assert actual_bytes == expected_bytes, (
            f"Size mismatch: physical {actual_bytes} != manifest {expected_bytes}"
        )


class TestReleaseManifestCryptographicTrustChain:
    """Tests Ed25519 asymmetric manifest signing, verification, and tamper detection."""

    def test_manifest_signature_is_valid_ed25519(self) -> None:
        """Requirement §9: Manifest signature is valid Ed25519 signature."""
        manifest = get_backend_manifest()
        assert "signature" in manifest, "Manifest missing 'signature' property"

        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is True, "Release manifest cryptographic signature verification failed"

    def test_tampered_manifest_version_rejected(self) -> None:
        """Requirement §9: Tampering with version invalidates Ed25519 signature."""
        manifest = get_backend_manifest()
        manifest["version"] = "9.9.9"
        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is False, "Tampered version was erroneously accepted"

    def test_tampered_manifest_sha256_rejected(self) -> None:
        """Requirement §9: Tampering with installer SHA-256 digest invalidates signature."""
        manifest = get_backend_manifest()
        manifest["sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is False, "Tampered SHA-256 was erroneously accepted"

    def test_tampered_manifest_installer_url_rejected(self) -> None:
        """Requirement §9: Tampering with download URL invalidates signature."""
        manifest = get_backend_manifest()
        manifest["installer_url"] = "https://attacker.example.com/malicious.exe"
        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is False, "Tampered URL was erroneously accepted"

    def test_tampered_manifest_size_rejected(self) -> None:
        """Requirement §9: Tampering with installer size invalidates signature."""
        manifest = get_backend_manifest()
        manifest["installer_size_bytes"] = 1234
        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is False, "Tampered byte size was erroneously accepted"

    def test_forged_manifest_signature_rejected(self) -> None:
        """Requirement §9: Random or corrupted signature string is rejected."""
        manifest = get_backend_manifest()
        # Flip first character of hex signature
        orig_sig = manifest["signature"]
        flipped_sig = ("0" if orig_sig[0] != "0" else "1") + orig_sig[1:]
        manifest["signature"] = flipped_sig
        valid = verify_manifest_signature(manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid is False, "Forged signature was erroneously accepted"

    def test_untrusted_public_key_rejected(self) -> None:
        """Requirement §9: Manifest signed by legitimate key fails verification with foreign key."""
        manifest = get_backend_manifest()
        # Foreign 32-byte public key
        foreign_pk = "11" * 32
        valid = verify_manifest_signature(manifest, foreign_pk)
        assert valid is False, "Manifest was erroneously verified against an unauthorized public key"


class TestInstallerPayloadTamperDetection:
    """Tests binary tampering detection and integrity enforcement."""

    def test_tampered_binary_byte_detected(self, tmp_path: Path) -> None:
        """Requirement §9: Modifying a single byte of installer binary fails integrity check."""
        manifest = get_backend_manifest()
        expected_sha = manifest["sha256"]

        tampered_file = tmp_path / "VoxLocal-Setup.exe"
        data = bytearray(DIST_INSTALLER.read_bytes()[:10000])
        # Corrupt one byte in the PE header or code
        data[50] = (data[50] + 1) % 256
        tampered_file.write_bytes(data)

        valid, reason = verify_installer_integrity(tampered_file, expected_sha)
        assert valid is False
        assert "mismatch" in reason.lower()

    def test_truncated_binary_detected_by_size_and_hash(self, tmp_path: Path) -> None:
        """Requirement §9: Incomplete or truncated download is safely rejected."""
        manifest = get_backend_manifest()
        expected_sha = manifest["sha256"]
        expected_size = manifest["installer_size_bytes"]

        truncated_file = tmp_path / "VoxLocal-Setup.exe"
        truncated_file.write_bytes(b"MZ\x90\x00Truncated")

        valid, reason = verify_installer_integrity(
            truncated_file, expected_sha, expected_size
        )
        assert valid is False
        assert "size mismatch" in reason.lower()

    def test_empty_installer_file_rejected(self, tmp_path: Path) -> None:
        """Requirement §9: Zero-byte file rejected."""
        empty_file = tmp_path / "empty.exe"
        empty_file.write_bytes(b"")

        valid, reason = verify_installer_integrity(empty_file, "somehash", 100)
        assert valid is False
        assert "0 bytes" in reason.lower()


class TestEndToEndTrustChainVerification:
    """Tests the full trust chain from manifest fetching to local binary verification."""

    def test_e2e_trust_chain_on_official_artifacts(self) -> None:
        """Full trust chain passes on official manifest + official installer executable."""
        manifest = get_backend_manifest()
        valid, reason = verify_release_trust_chain(
            manifest, DIST_INSTALLER, DEFAULT_ENTITLEMENT_PUBLIC_KEY
        )
        assert valid is True, f"Full trust chain failed: {reason}"
        assert "fully verified" in reason.lower()

    def test_e2e_trust_chain_fails_if_manifest_signature_missing(self) -> None:
        manifest = get_backend_manifest()
        del manifest["signature"]
        valid, reason = verify_release_trust_chain(
            manifest, DIST_INSTALLER, DEFAULT_ENTITLEMENT_PUBLIC_KEY
        )
        assert valid is False
        assert "missing required field" in reason.lower()

    def test_update_manager_offline_handling(self) -> None:
        """UpdateManager safely handles unreachable manifest endpoints."""
        mgr = UpdateManager(
            current_version="1.0.0",
            manifest_url="http://127.0.0.1:59999/nonexistent",
        )
        is_avail, manifest, msg = mgr.check_for_updates(timeout_sec=1)
        assert is_avail is False
        assert manifest is None
        assert "could not reach" in msg.lower()


class TestCrossLanguageCryptographicParity:
    """Verifies that Node.js and Python implementations produce bit-exact canonical structures and signatures."""

    def test_canonical_manifest_representation_parity(self) -> None:
        """Node.js canonicalizeManifest and Python canonicalize_manifest produce identical byte strings."""
        sample_manifest = {
            "version": "1.0.0",
            "release_date": "2026-11-15",
            "sha256": "abcdef",
            "mandatory": False,
            "installer_size_bytes": 10069610,
            "signature": "ignored_signature_value",
        }

        py_canonical = canonicalize_manifest(sample_manifest).decode("utf-8")

        node_script = (
            "const { canonicalizeManifest } = require('./api/_lib/token.js');"
            f"const m = {json.dumps(sample_manifest)};"
            "console.log(canonicalizeManifest(m).toString('utf-8'));"
        )
        node_res = subprocess.run(
            ["node", "-e", node_script],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        node_canonical = node_res.stdout.strip()

        assert py_canonical == node_canonical, "Canonical manifest JSON differs between Python and Node"

    def test_python_verifies_node_signature(self) -> None:
        """Python ed25519_verify validates token signed by Node signManifest."""
        test_payload = {
            "version": "2.0.0-beta",
            "product_name": "VOX Cross Verification Test",
            "sha256": "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff",
            "installer_url": "https://example.com/setup.exe",
        }
        node_script = (
            "const { signManifest } = require('./api/_lib/token.js');"
            f"const m = {json.dumps(test_payload)};"
            "m.signature = signManifest(m);"
            "console.log(JSON.stringify(m));"
        )
        node_res = subprocess.run(
            ["node", "-e", node_script],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        signed_manifest = json.loads(node_res.stdout.strip())

        assert verify_manifest_signature(signed_manifest, DEFAULT_ENTITLEMENT_PUBLIC_KEY) is True

    def test_node_verifies_python_signature(self) -> None:
        """Node verifyManifest validates token signed by Python ed25519_sign."""
        test_payload = {
            "version": "3.0.0",
            "product_name": "Python Signed Manifest",
            "sha256": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
            "installer_url": "https://example.com/py-setup.exe",
        }
        canonical_bytes = canonicalize_manifest(test_payload)
        seed_bytes = bytes.fromhex(DEV_FALLBACK_PRIVATE_KEY_HEX)
        sig = ed25519_sign(canonical_bytes, seed_bytes)
        test_payload["signature"] = sig.hex()

        node_script = (
            "const { verifyManifest } = require('./api/_lib/token.js');"
            f"const m = {json.dumps(test_payload)};"
            "console.log(verifyManifest(m));"
        )
        node_res = subprocess.run(
            ["node", "-e", node_script],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        assert node_res.stdout.strip() == "true", "Node failed to verify Python-generated Ed25519 signature"

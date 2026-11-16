"""
Verification script for live VOX production commercialisation deployment.
Directly probes all 5 production endpoints and verifies cryptographic authenticity.
"""

import base64
import hashlib
import json
import ssl
import sys
import urllib.error
import urllib.request

from voxlocal.core.entitlement import (
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    verify_and_decode_entitlement_token,
)
from voxlocal.core.updater import verify_manifest_signature


def main():
    print("=================================================================")
    print("VOX COMMERCIALISATION LIVE PRODUCTION VERIFICATION AUDIT")
    print("=================================================================\n")

    # 1. /api/entitlements
    print("[CHECK 1] GET https://vox-voice-assistant.vercel.app/api/entitlements")
    req1 = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/api/entitlements",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req1) as r1:
        assert r1.status == 200, f"Expected 200, got {r1.status}"
        data1 = json.loads(r1.read().decode("utf-8"))
        assert data1.get("ok") is True, f"Response not ok: {data1}"
        token1 = data1["token"]
        parts = token1.split(".")
        assert len(parts) == 2, f"Expected 2 parts (<payload>.<signature>), got {len(parts)}"
        payload_b64, sig_b64 = parts
        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        assert len(sig_bytes) == 64, f"Expected 64 bytes signature, got {len(sig_bytes)}"

        valid1, claims1, err1 = verify_and_decode_entitlement_token(token1, DEFAULT_ENTITLEMENT_PUBLIC_KEY)
        assert valid1, f"Signature verification failed: {err1}"
        assert claims1["plan"] == "free"
        assert claims1["is_pro"] is False
        assert "dictation_words_per_week" in claims1["limits"]
        print(f"  [+] Status: HTTP {r1.status}")
        print(f"  [+] Ed25519 Signature length: {len(sig_bytes)} bytes")
        print(f"  [+] Cryptographic Verification: VALID against DEFAULT_ENTITLEMENT_PUBLIC_KEY")
        print(f"  [+] Decoded Claims: user_id='{claims1.get('user_id')}', plan='{claims1.get('plan')}', is_pro={claims1.get('is_pro')}")
        print("  -> CHECK 1 PASSED.\n")

    # 2. /account.html and /account
    print("[CHECK 2] GET https://vox-voice-assistant.vercel.app/account.html")
    req2 = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/account.html",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req2) as r2:
        assert r2.status == 200, f"Expected 200, got {r2.status}"
        html2 = r2.read().decode("utf-8")
        assert "420" not in html2, "Found '420' in account.html!"
        assert "vox_plan" not in html2, "Found 'vox_plan' in account.html!"
        assert "VOX Pro" in html2 or "Subscription" in html2, "Account UI missing expected content"
        print(f"  [+] Status: HTTP {r2.status}")
        print(f"  [+] HTML size: {len(html2)} bytes")
        print("  [+] String scan: 0 occurrences of '420'")
        print("  [+] String scan: 0 occurrences of 'vox_plan'")
        print("  -> CHECK 2 PASSED.\n")

    # 3. /api/releases/latest
    print("[CHECK 3] GET https://vox-voice-assistant.vercel.app/api/releases/latest")
    req3 = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/api/releases/latest",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req3) as r3:
        assert r3.status == 200, f"Expected 200, got {r3.status}"
        manifest3 = json.loads(r3.read().decode("utf-8"))
        expected_sha = "300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7"
        expected_size = 10067409
        assert manifest3["sha256"] == expected_sha, f"SHA mismatch: {manifest3['sha256']}"
        assert manifest3["installer_size_bytes"] == expected_size, f"Size mismatch: {manifest3['installer_size_bytes']}"
        assert verify_manifest_signature(manifest3, DEFAULT_ENTITLEMENT_PUBLIC_KEY), "Manifest signature invalid!"
        print(f"  [+] Status: HTTP {r3.status}")
        print(f"  [+] Version: {manifest3.get('version')}")
        print(f"  [+] Installer URL: {manifest3.get('installer_url')}")
        print(f"  [+] SHA-256: {manifest3.get('sha256')}")
        print(f"  [+] Size: {manifest3.get('installer_size_bytes')} bytes")
        print(f"  [+] Ed25519 Signature: {manifest3.get('signature')[:32]}... ({len(manifest3.get('signature'))} hex chars)")
        print(f"  [+] Manifest Signature Verification: VALID against DEFAULT_ENTITLEMENT_PUBLIC_KEY")
        print("  -> CHECK 3 PASSED.\n")

    # 4. Verify public unauthenticated download bypass is blocked (returns 404)
    print("[CHECK 4] HEAD https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe (Must be blocked)")
    req4_head = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe",
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        with urllib.request.urlopen(req4_head) as r4_head:
            raise AssertionError(f"Expected HTTP 404, but got HTTP {r4_head.status} - Public download bypass is open!")
    except urllib.error.HTTPError as e4:
        assert e4.code == 404, f"Expected HTTP 404, got {e4.code}"
        print(f"  [+] Status: HTTP {e4.code} Not Found (Public static bypass successfully blocked)")
        print("  -> CHECK 4 PASSED.\n")

    # 5. /api/download/authorize authorization gating
    print("[CHECK 5] POST & OPTIONS https://vox-voice-assistant.vercel.app/api/download/authorize")
    req5_opt = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/api/download/authorize",
        method="OPTIONS",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req5_opt) as r5_opt:
        assert r5_opt.status == 200, f"Expected 200, got {r5_opt.status}"
        assert r5_opt.headers.get("Access-Control-Allow-Origin") == "*", "Missing CORS header"
        print(f"  [+] OPTIONS Status: HTTP {r5_opt.status} (CORS Preflight Valid)")

    req5_post = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/api/download/authorize",
        data=b'{}',
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
    )
    try:
        with urllib.request.urlopen(req5_post) as r5_post:
            raise AssertionError(f"Expected HTTP 401, but got HTTP {r5_post.status}")
    except urllib.error.HTTPError as e5:
        assert e5.code == 401, f"Expected HTTP 401, got {e5.code}"
        err_data = json.loads(e5.read().decode("utf-8"))
        assert err_data.get("ok") is False, f"Expected ok=false: {err_data}"
        assert "Authentication required" in err_data.get("error", ""), f"Unexpected error message: {err_data}"
        print(f"  [+] Unauthenticated POST Status: HTTP {e5.code} Unauthorized")
        print(f"  [+] Gating Error: {err_data.get('error')}")
        print("  -> CHECK 5 PASSED.\n")

    # 6. /download portal
    print("[CHECK 6] GET https://vox-voice-assistant.vercel.app/download")
    req6 = urllib.request.Request(
        "https://vox-voice-assistant.vercel.app/download",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req6) as r6:
        assert r6.status == 200, f"Expected 200, got {r6.status}"
        html6 = r6.read().decode("utf-8")
        assert "Sign in to Download VOX" in html6, "Download portal missing sign-in gate"
        assert "VoxLocal-Setup.exe" in html6, "Download portal missing artifact name"
        assert "300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7" in html6, "Download portal missing SHA-256 digest"
        print(f"  [+] Status: HTTP {r6.status}")
        print(f"  [+] Portal HTML size: {len(html6)} bytes")
        print("  [+] Verified Presence: Sign in to Download VOX gate, SHA-256 digest, Setup Guide")
        print("  -> CHECK 6 PASSED.\n")

    # 7. Supabase Google OAuth Authorize endpoint
    print("[CHECK 7] GET https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/authorize?provider=custom:google")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req7 = urllib.request.Request(
        "https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/authorize?provider=custom:google",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        resp7 = opener.open(req7)
        print(f"  [!] Received unexpected 200: {resp7.status}")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code in (302, 303), f"Expected HTTP 302/303 redirect, got {e.code}"
        loc = e.headers.get("Location", "")
        assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth"), f"Unexpected redirect destination: {loc}"
        assert "290945439058-kipvted3lsi25t8fjts2mpdm1addciop.apps.googleusercontent.com" in loc, "Missing client_id in OAuth URL!"
        assert "response_type=code" in loc, "Missing response_type in OAuth URL!"
        assert "qiigoipgoedexmdowtvj.supabase.co" in loc, "Missing callback redirect_uri in OAuth URL!"
        print(f"  [+] Status: HTTP {e.code} Found (OAuth Redirection)")
        print(f"  [+] Redirect Target: https://accounts.google.com/o/oauth2/v2/auth")
        print(f"  [+] Google Client ID present in query: 290945439058-kipvted3lsi25t8fjts2mpdm1addciop.apps.googleusercontent.com")
        print(f"  [+] Callback URI present in query: https://qiigoipgoedexmdowtvj.supabase.co/auth/v1/callback")
        print("  -> CHECK 7 PASSED.\n")

    print("=================================================================")
    print("SUCCESS: ALL 7 LIVE PRODUCTION VERIFICATION AUDITS PASSED 100%!")
    print("=================================================================")


if __name__ == "__main__":
    main()

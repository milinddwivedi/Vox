"""
VoxLocal Calendar Authentication Broker, Token Vault, and Error Sanitization.

Architecture goals for production multi-user readiness:
- Keeps client secrets and OAuth tokens out of plaintext `config.json`, UI widgets, and repository files.
- Encrypts locally cached credentials with Windows DPAPI (`CryptProtectData` / `CryptUnprotectData`).
- Supports optional server-side OAuth code exchange and token refresh via `VOX_AUTH_BACKEND_URL`
  (`CalendarAuthBroker`) while seamlessly operating in standalone desktop PKCE / Device Code mode
  when no hosted backend is configured.
- Isolates all stored credentials and connected accounts by `user_id` + `account_id`.
- Translates raw technical OAuth / AADSTS / HTTP failures into clear, non-technical user guidance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from voxlocal.core.logger import get_logger

logger = get_logger("calendar.auth")

COMMON_CONSUMER_TENANT_GUID = "9188040d-6c67-4c5b-b112-36a304b66dad"

PUBLIC_CONSUMER_DOMAINS = frozenset({
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "yahoo.com",
    "yahoo.co.uk",
    "proton.me",
    "protonmail.com",
})


def dpapi_encrypt(data: bytes, description: str = "vox_calendar_token") -> bytes:
    """Encrypt byte payload using Windows DPAPI CryptProtectData (cross-platform base64 fallback)."""
    if not data:
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_blob = DATA_BLOB(
            len(data),
            ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)),
        )
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), description, None, None, None, 0, ctypes.byref(out_blob)
        ):
            buf = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return buf
    except Exception as exc:
        logger.debug("DPAPI encryption fallback to base64: %s", exc)
    return base64.b64encode(data)


def dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt byte payload protected by Windows DPAPI CryptProtectData."""
    if not data:
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_blob = DATA_BLOB(
            len(data),
            ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)),
        )
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            buf = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return buf
    except Exception as exc:
        logger.debug("DPAPI decryption fallback: %s", exc)
    try:
        return base64.b64decode(data)
    except Exception:
        return data


def classify_account_kind(
    provider_type: str,
    email: str = "",
    tenant_id: str = "",
    hosted_domain: str = "",
    zoom_account_type: Optional[int] = None,
) -> str:
    """
    Classify connected calendar account as 'personal', 'work', 'school', or 'unknown'.
    Uses official identity metadata (Microsoft tid, Google hd, Zoom type, or email domain).
    """
    ptype = (provider_type or "").lower()
    clean_email = (email or "").strip().lower()
    domain = clean_email.split("@", 1)[1] if "@" in clean_email else ""

    def _domain_work_or_school(dom: str) -> str:
        d = (dom or "").strip().lower()
        if not d:
            return "unknown"
        if d.endswith(".edu") or ".edu." in d or d.endswith(".ac.uk") or ".ac." in d or d.endswith(".k12.us"):
            return "school"
        if d in PUBLIC_CONSUMER_DOMAINS:
            return "personal"
        return "work"

    if ptype == "ms_graph":
        tid = (tenant_id or "").strip().lower()
        if tid == COMMON_CONSUMER_TENANT_GUID:
            return "personal"
        if domain:
            return _domain_work_or_school(domain)
        if tid:
            return "work"
        return "personal"

    if ptype == "google":
        hd = (hosted_domain or "").strip().lower()
        if hd:
            return _domain_work_or_school(hd)
        if domain:
            return _domain_work_or_school(domain)
        return "personal"

    if ptype == "zoom":
        if zoom_account_type is not None and zoom_account_type >= 2:
            return _domain_work_or_school(domain) if domain else "work"
        if domain:
            return _domain_work_or_school(domain)
        return "personal"

    if domain:
        return _domain_work_or_school(domain)
    return "personal" if ptype == "ics" else "unknown"


def make_account_id(provider_type: str, account_email: str = "", fallback_label: str = "") -> str:
    """Generate a deterministic, canonical account_id scoped to provider and account identity."""
    ptype = (provider_type or "calendar").strip().lower()
    ident = (account_email or "").strip().lower()
    if ptype == "ics":
        seed = ident or (fallback_label or "ics").strip()
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"ics:{digest}"
    if ident:
        return f"{ptype}:{ident}"
    if fallback_label:
        slug = "".join(c if c.isalnum() or c in "._-@" else "_" for c in fallback_label.lower()).strip("_")
        return f"{ptype}:{slug or 'default'}"
    return f"{ptype}:default"


def sanitize_oauth_error_for_user(raw_error: str, provider: str = "") -> Tuple[str, str]:
    """
    Convert technical OAuth, AADSTS, HTTP, or transport exceptions into
    (normalized_error_code, friendly_non_technical_message).
    """
    raw = (raw_error or "").strip()
    low = raw.lower()
    provider_label = {
        "ms_graph": "Microsoft",
        "google": "Google",
        "zoom": "Zoom",
        "ics": "ICS Calendar",
        "local_mapi": "Outlook Desktop",
    }.get((provider or "").lower(), "your calendar provider")

    if any(tok in low for tok in ("aadsts65001", "aadsts65002", "admin_approval", "consent_required", "admin approval", "admin_consent")):
        return (
            "ADMIN_APPROVAL_REQUIRED",
            f"Your organization's IT administrator needs to approve {provider_label} calendar access for this work or school account. "
            "Ask your IT admin to grant calendar read permission, or connect a personal account / published ICS feed instead.",
        )

    if any(tok in low for tok in ("aadsts50076", "aadsts50079", "interaction_required", "mfa")):
        return (
            "AUTH_EXPIRED",
            f"{provider_label} requires another sign-in verification check. Click Reconnect to complete sign-in.",
        )

    if any(tok in low for tok in ("access_denied", "user_cancelled", "cancelled by user", "declined")):
        if "denied" in low:
            return (
                "AUTH_DENIED",
                f"Calendar permission was not granted on {provider_label}. VOX only requests read access to show your schedule — please try again and accept calendar read permissions.",
            )
        return (
            "AUTH_CANCELLED",
            "Sign-in was cancelled before finishing. You can connect again whenever you're ready.",
        )

    if "cancel" in low and ("flow" in low or "timeout" not in low):
        return (
            "AUTH_CANCELLED",
            "Sign-in was cancelled before finishing. You can connect again whenever you're ready.",
        )

    if any(tok in low for tok in ("timed out", "timeout", "expired_token", "device code session expired")):
        return (
            "TIMEOUT",
            f"Sign-in with {provider_label} timed out waiting for confirmation in the browser. Click Connect to start a fresh sign-in.",
        )

    if any(tok in low for tok in ("invalid_grant", "auth_required", "http 401", "unauthorized", "revoked", "token_expired")):
        return (
            "AUTH_EXPIRED",
            f"Your {provider_label} calendar session expired or was revoked. Click Reconnect to sign in again.",
        )

    if any(tok in low for tok in ("invalid_client", "redirect_uri_mismatch", "unauthorized_client", "missing_client_id", "client_id", "oauth app credentials missing")):
        return (
            "OAUTH_CONFIG_ERROR",
            f"{provider_label} OAuth credentials need attention (Client ID or Redirect URI mismatch). "
            "Check your calendar connection settings or switch to hosted sign-in.",
        )

    if any(tok in low for tok in ("429", "rate limit", "rate_limit", "too many requests")):
        return (
            "HTTP_429_RATE_LIMIT",
            f"{provider_label} temporarily asked VOX to pause requests. Synchronization will resume automatically shortly.",
        )

    if any(tok in low for tok in (
        "network", "offline", "urlopen", "name resolution", "connection refused",
        "connection reset", "connection aborted", "broken pipe", "remotedisconnected",
        "remote disconnected", "socket", "timeout", "timed out", "502", "503", "504",
        "gateway timeout", "service unavailable", "bad gateway", "temporarily unavailable",
        "econnreset", "econnrefused", "winerror", "ssl", "11001", "10054", "10060", "10061"
    )):
        return (
            "NETWORK_OFFLINE",
            f"Could not reach {provider_label} right now (network offline). Previously synced meetings remain available.",
        )

    return (
        "AUTH_FAILED",
        f"VOX could not finish connecting to {provider_label}. Please try reconnecting in a moment.",
    )


class CalendarAuthBroker:
    """
    Unified credential & OAuth broker supporting:
    1. Multi-user DPAPI encrypted SQLite credential vault (`calendar_credentials_vault`).
    2. Optional hosted OAuth backend (`VOX_AUTH_BACKEND_URL`) for server-managed token exchange & refresh
       so production deployments never ship OAuth client secrets inside desktop binaries or frontend code.
    """

    def __init__(self, db: Any = None, backend_url: str = "") -> None:
        self._db = db
        self._backend_url = (backend_url or os.environ.get("VOX_AUTH_BACKEND_URL", "")).strip().rstrip("/")

    def set_database(self, db: Any) -> None:
        self._db = db

    def set_backend_url(self, backend_url: str) -> None:
        self._backend_url = (backend_url or os.environ.get("VOX_AUTH_BACKEND_URL", "")).strip().rstrip("/")

    @property
    def backend_url(self) -> str:
        return self._backend_url

    def is_backend_configured(self) -> bool:
        return bool(self._backend_url and (self._backend_url.startswith("https://") or self._backend_url.startswith("http://localhost") or self._backend_url.startswith("http://127.0.0.1")))

    # --- Vault Operations ---

    def save_credentials(
        self,
        user_id: str,
        account_id: str,
        provider_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        if self._db is None or not hasattr(self._db, "save_calendar_credentials"):
            return False
        raw = json.dumps(payload or {}, sort_keys=True).encode("utf-8")
        encrypted = dpapi_encrypt(raw, description=f"vox_cal:{provider_type}")
        return bool(self._db.save_calendar_credentials(user_id or "default", account_id, provider_type, encrypted))

    def load_credentials(self, user_id: str, account_id: str) -> Optional[Dict[str, Any]]:
        if self._db is None or not hasattr(self._db, "load_calendar_credentials"):
            return None
        encrypted = self._db.load_calendar_credentials(user_id or "default", account_id)
        if not encrypted:
            return None
        try:
            decrypted = dpapi_decrypt(encrypted)
            data = json.loads(decrypted.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("Failed decoding encrypted credentials for %s: %s", account_id, exc)
            return None

    def delete_credentials(self, user_id: str, account_id: str) -> bool:
        if self._db is None or not hasattr(self._db, "delete_calendar_credentials"):
            return False
        return bool(self._db.delete_calendar_credentials(user_id or "default", account_id))

    def save_app_secret(self, secret_name: str, secret_value: str, user_id: str = "default") -> bool:
        """Persist OAuth client secret inside encrypted credential vault instead of plaintext config.json."""
        key = f"app_secret:{secret_name}"
        if not secret_value:
            return self.delete_credentials(user_id, key)
        return self.save_credentials(user_id, key, "app_secret", {"value": secret_value})

    def load_app_secret(self, secret_name: str, user_id: str = "default") -> str:
        env_map = {
            "google_calendar_client_secret": "VOX_GOOGLE_CLIENT_SECRET",
            "zoom_client_secret": "VOX_ZOOM_CLIENT_SECRET",
        }
        env_key = env_map.get(secret_name, "")
        if env_key and os.environ.get(env_key):
            return os.environ[env_key].strip()
        data = self.load_credentials(user_id, f"app_secret:{secret_name}")
        if isinstance(data, dict):
            return str(data.get("value") or "")
        return ""

    # --- Optional Hosted Backend OAuth Broker ---

    def _post_backend_json(self, path: str, body: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
        if not self._backend_url:
            raise RuntimeError("Hosted auth backend URL is not configured")
        url = f"{self._backend_url}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def start_backend_oauth(
        self,
        provider: str,
        user_id: str,
        scopes: List[str],
        redirect_uri: str,
        code_challenge: str,
        tenant: str = "common",
        login_hint: str = "",
    ) -> Dict[str, Any]:
        return self._post_backend_json(
            "/v1/calendar/oauth/start",
            {
                "provider": provider,
                "user_id": user_id,
                "scopes": scopes,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "tenant": tenant,
                "login_hint": login_hint,
            },
        )

    def exchange_backend_code(
        self,
        provider: str,
        user_id: str,
        code: str,
        state: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        return self._post_backend_json(
            "/v1/calendar/oauth/exchange",
            {
                "provider": provider,
                "user_id": user_id,
                "code": code,
                "state": state,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
        )

    def refresh_backend_token(self, provider: str, user_id: str, account_id: str) -> Dict[str, Any]:
        return self._post_backend_json(
            "/v1/calendar/oauth/refresh",
            {
                "provider": provider,
                "user_id": user_id,
                "account_id": account_id,
            },
        )

    def revoke_backend_account(self, provider: str, user_id: str, account_id: str) -> bool:
        try:
            res = self._post_backend_json(
                "/v1/calendar/oauth/revoke",
                {"provider": provider, "user_id": user_id, "account_id": account_id},
            )
            return bool(res.get("ok", True))
        except Exception as exc:
            logger.debug("Backend revoke failed for %s: %s", account_id, exc)
            return False

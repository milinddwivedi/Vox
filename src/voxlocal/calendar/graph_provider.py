"""
VoxLocal Microsoft Graph Calendar Provider.
Production-grade Microsoft Graph Calendar integration supporting:
- Device Code Flow (RFC 8628) for zero-redirect-URI sign-in with WFX corporate work accounts and personal accounts
- Local loopback OAuth 2.0 PKCE flow with configurable redirect URI and custom Azure App Registration
- 30-day horizon with @odata.nextLink pagination and recurring event expansion via /me/calendarView
- Multi-platform meeting link extraction (Teams, Google Meet, Zoom, Webex) from onlineMeeting, body, and location
- DPAPI-encrypted token storage on Windows and transparent token refresh
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voxlocal.calendar.auth_service import (
    classify_account_kind,
    dpapi_decrypt,
    dpapi_encrypt,
    make_account_id,
    sanitize_oauth_error_for_user,
)
from voxlocal.calendar.base import (
    AccountConnectionState,
    BaseCalendarProvider,
    CalendarMeeting,
    CalendarProviderType,
)
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.graph")


def copy_to_clipboard_win32(text: str) -> bool:
    """Safely copy plain text string to Windows system clipboard via Win32 API."""
    if not text:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            return False
        try:
            user32.EmptyClipboard()
            encoded = (text + "\0").encode("utf-16le")
            # GMEM_MOVEABLE = 0x0002
            h_mem = kernel32.GlobalAlloc(0x0002, len(encoded))
            if not h_mem:
                return False
            p_mem = kernel32.GlobalLock(h_mem)
            ctypes.memmove(p_mem, encoded, len(encoded))
            kernel32.GlobalUnlock(h_mem)
            user32.SetClipboardData(13, h_mem)  # CF_UNICODETEXT = 13
            return True
        finally:
            user32.CloseClipboard()
    except Exception as exc:
        logger.debug("Win32 clipboard copy note: %s", exc)
        return False


def extract_meeting_links(text: str) -> Tuple[Optional[str], str]:
    """
    Extract join URL and platform (teams, zoom, meet, webex, generic) from text or HTML.
    Returns (join_url, platform).
    """
    if not text:
        return None, "generic"

    # 1. Microsoft Teams
    m_teams = re.search(r"https://teams\.(?:microsoft|live)\.com/l/meetup-join/[^\s\"'<>]+", text, re.IGNORECASE)
    if m_teams:
        return m_teams.group(0), "teams"

    # 2. Zoom
    m_zoom = re.search(r"https://[a-zA-Z0-9_\-\.]*zoom(?:\.us|gov\.com)/j/[0-9]+[^\s\"'<>]*", text, re.IGNORECASE)
    if m_zoom:
        return m_zoom.group(0), "zoom"

    # 3. Google Meet
    m_meet = re.search(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}", text, re.IGNORECASE)
    if m_meet:
        return m_meet.group(0), "meet"

    # 4. Cisco Webex
    m_webex = re.search(r"https://[a-zA-Z0-9_\-\.]*webex\.com/(?:meet|join)/[^\s\"'<>]+", text, re.IGNORECASE)
    if m_webex:
        return m_webex.group(0), "webex"

    return None, "generic"


class MsGraphCalendarProvider(BaseCalendarProvider):
    """
    Production-grade Microsoft Graph Calendar Provider with OAuth 2.0 PKCE,
    Device Code Flow (RFC 8628), DPAPI encryption, and offline error recovery.
    """

    OFFICE_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"  # Microsoft Office preauthorized client for corporate tenants
    GRAPH_CLI_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"  # Multi-audience client supporting Personal + Work accounts on /common
    LEGACY_AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
    DEFAULT_CLIENT_ID = GRAPH_CLI_CLIENT_ID
    OFFICE_SCOPES = ["https://graph.microsoft.com/.default", "offline_access"]
    SCOPES = ["Calendars.Read", "User.Read", "offline_access"]
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    PERSONAL_EMAIL_DOMAINS = {
        "outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com",
        "outlook.in", "outlook.co.uk", "hotmail.co.uk", "live.in",
        "gmail.com", "googlemail.com", "yahoo.com", "icloud.com", "me.com",
        "aol.com", "proton.me", "protonmail.com",
    }

    @classmethod
    def _effective_scopes_for_client(cls, client_id: str) -> List[str]:
        if client_id == cls.OFFICE_CLIENT_ID:
            return list(cls.OFFICE_SCOPES)
        return list(cls.SCOPES)

    @classmethod
    def detect_account_type(cls, hint: Optional[str]) -> str:
        """
        Automatically classify an optional email/tenant hint into 'personal', 'work', or 'common'.
        Allows one unified UI passage while diversifying backend auth parameters automatically.
        """
        val = (hint or "").strip().lower()
        if not val or val == "common":
            return "common"
        if val == "consumers":
            return "personal"
        if val == "organizations":
            return "work"
        if "@" in val:
            domain = val.rsplit("@", 1)[-1].strip()
            if domain in cls.PERSONAL_EMAIL_DOMAINS:
                return "personal"
            return "work"
        return "work"

    def _set_authority(self, tenant_id: str) -> None:
        self.tenant_id = (tenant_id or "common").strip() or "common"
        self.authority = f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id)}"
        self.auth_endpoint = f"{self.authority}/oauth2/v2.0/authorize"
        self.token_endpoint = f"{self.authority}/oauth2/v2.0/token"
        self.device_code_endpoint = f"{self.authority}/oauth2/v2.0/devicecode"

    def __init__(
        self,
        client_id: Optional[str] = None,
        auth_file_path: Optional[Path] = None,
        tenant_id: Optional[str] = None,
        account_hint: Optional[str] = None,
        account_id: str = "",
        user_id: str = "default",
        auth_broker: Any = None,
    ) -> None:
        raw_client_id = (client_id or os.environ.get("MS_GRAPH_CLIENT_ID", "")).strip()
        if raw_client_id == self.LEGACY_AZURE_CLI_CLIENT_ID:
            raw_client_id = ""
        self._custom_client_id = raw_client_id
        self.account_hint = (account_hint or "").strip()
        raw_tenant = (tenant_id or os.environ.get("MS_GRAPH_TENANT_ID", "")).strip()
        self._explicit_tenant = bool(raw_tenant and raw_tenant.lower() != "common")

        # Automatic backend diversification between Personal, Work, and Unified (/common)
        acct_type = self.detect_account_type(self.account_hint or raw_tenant)
        self._account_type = acct_type
        if self._custom_client_id:
            self.client_id = self._custom_client_id
            eff_tenant = raw_tenant if (raw_tenant and "@" not in raw_tenant) else "common"
        elif acct_type == "work":
            self.client_id = self.OFFICE_CLIENT_ID
            eff_tenant = raw_tenant if (raw_tenant and "@" not in raw_tenant) else "organizations"
        elif acct_type == "personal":
            self.client_id = self.GRAPH_CLI_CLIENT_ID
            eff_tenant = "common"
        else:
            # Unified default ('common') works for both Personal Microsoft accounts and Work accounts
            self.client_id = self.GRAPH_CLI_CLIENT_ID
            eff_tenant = "common"

        self._active_scopes = self._effective_scopes_for_client(self.client_id)
        self._set_authority(eff_tenant)
        self.user_id = (user_id or "default").strip() or "default"
        self._auth_broker = auth_broker
        self._explicit_auth_file_path = auth_file_path is not None
        self.auth_file_path = auth_file_path or (Path("data") / "calendar_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_upn: Optional[str] = self.account_hint or None
        self._display_name: str = ""
        self._resolved_tenant_guid: str = ""
        self._account_kind: str = classify_account_kind("ms_graph", self.account_hint)
        self._account_id: str = (account_id or "").strip() or make_account_id(
            CalendarProviderType.MS_GRAPH.value,
            self.account_hint,
        )
        self._selected_calendars: List[str] = []
        self._available_calendars: List[Dict[str, Any]] = []
        self._delta_links: Dict[str, str] = {}
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.MS_GRAPH.value,
            "account_id": self._account_id,
            "is_healthy": True,
            "last_poll_time": None,
            "last_sync_at": None,
            "error_state": None,
            "error_detail": None,
            "friendly_error": None,
            "connection_state": AccountConnectionState.DISCONNECTED.value,
            "account_upn": self._account_upn,
            "display_name": self._display_name,
            "account_kind": self._account_kind,
            "tenant_id": self.tenant_id,
            "selected_calendars": [],
            "available_calendars": [],
        }
        self.retry_after_seconds: int = 0
        self.backoff_interval_sec: int = 60

        self._load_cached_credentials()

    @staticmethod
    def _extract_jwt_claims(token: Optional[str]) -> Dict[str, Any]:
        if not token or token.count(".") < 2:
            return {}
        try:
            payload_b64 = token.split(".")[1]
            padding = "=" * ((4 - len(payload_b64) % 4) % 4)
            decoded = base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
            parsed = json.loads(decoded.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _sync_status_identity(self) -> None:
        if self._account_upn:
            self._account_id = self._account_id if ":" in self._account_id and not self._account_id.endswith(":default") else make_account_id(
                CalendarProviderType.MS_GRAPH.value,
                self._account_upn,
            )
            self._account_kind = classify_account_kind(
                "ms_graph",
                email=self._account_upn,
                tenant_id=self._resolved_tenant_guid,
            )
        self._status["account_id"] = self._account_id
        self._status["account_upn"] = self._account_upn
        self._status["display_name"] = self._display_name
        self._status["account_kind"] = self._account_kind
        self._status["tenant_id"] = self._resolved_tenant_guid or self.tenant_id
        self._status["selected_calendars"] = list(self._selected_calendars)
        self._status["available_calendars"] = list(self._available_calendars)

    def _apply_token_data(self, token_data: Dict[str, Any]) -> None:
        self._access_token = token_data.get("access_token")
        self._refresh_token = token_data.get("refresh_token")
        self._expires_at = token_data.get("expires_at")
        self._account_upn = token_data.get("account_upn") or self._account_upn
        self._display_name = str(token_data.get("display_name") or self._display_name or "")
        self._resolved_tenant_guid = str(token_data.get("resolved_tenant_guid") or self._resolved_tenant_guid or "")
        if isinstance(token_data.get("selected_calendars"), list):
            self._selected_calendars = [str(c) for c in token_data["selected_calendars"] if c]
        if isinstance(token_data.get("available_calendars"), list):
            self._available_calendars = list(token_data["available_calendars"])
        if isinstance(token_data.get("delta_links"), dict):
            self._delta_links = {str(k): str(v) for k, v in token_data["delta_links"].items() if v}

        cached_client_id = str(token_data.get("client_id") or "").strip()
        has_tokens = bool(self._access_token or self._refresh_token)
        if (
            has_tokens
            and cached_client_id
            and cached_client_id != self.LEGACY_AZURE_CLI_CLIENT_ID
            and not self._custom_client_id
            and not self.account_hint
        ):
            self.client_id = cached_client_id
        cached_tenant = str(token_data.get("tenant_id") or "").strip()
        if has_tokens and cached_tenant and not self.account_hint and not self._explicit_tenant:
            self._set_authority(cached_tenant)
        cached_scopes = token_data.get("scopes")
        if has_tokens and isinstance(cached_scopes, list) and cached_scopes and not self.account_hint:
            self._active_scopes = [str(s) for s in cached_scopes]
        else:
            self._active_scopes = self._effective_scopes_for_client(self.client_id)

        if has_tokens:
            self._status["connection_state"] = AccountConnectionState.CONNECTED.value
            self._status["error_state"] = None
            self._status["friendly_error"] = None
        self._sync_status_identity()

    def _load_cached_credentials(self) -> bool:
        if self._auth_broker is not None and self._account_id:
            vault_data = self._auth_broker.load_credentials(self.user_id, self._account_id)
            if isinstance(vault_data, dict) and (vault_data.get("access_token") or vault_data.get("refresh_token")):
                self._apply_token_data(vault_data)
                return True
        if not self.auth_file_path.exists():
            return False
        try:
            ciphertext = self.auth_file_path.read_bytes()
            plaintext = dpapi_decrypt(ciphertext)
            token_data = json.loads(plaintext.decode("utf-8"))
            self._apply_token_data(token_data)
            return True
        except Exception as exc:
            logger.error("Failed loading cached DPAPI calendar credentials: %s", exc)
            return False

    def _save_credentials(self) -> None:
        self._sync_status_identity()
        token_data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "account_upn": self._account_upn,
            "display_name": self._display_name,
            "resolved_tenant_guid": self._resolved_tenant_guid,
            "account_kind": self._account_kind,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "scopes": list(self._active_scopes),
            "selected_calendars": list(self._selected_calendars),
            "available_calendars": list(self._available_calendars),
            "delta_links": dict(self._delta_links),
        }
        if self._auth_broker is not None and self._account_id:
            try:
                self._auth_broker.save_credentials(
                    self.user_id,
                    self._account_id,
                    CalendarProviderType.MS_GRAPH.value,
                    token_data,
                )
            except Exception as exc:
                logger.warning("Failed saving credentials into DB vault: %s", exc)
        if self._explicit_auth_file_path or self._auth_broker is None:
            try:
                self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
                plaintext = json.dumps(token_data).encode("utf-8")
                ciphertext = dpapi_encrypt(plaintext)
                self.auth_file_path.write_bytes(ciphertext)
            except Exception as exc:
                logger.error("Failed saving DPAPI encrypted credentials: %s", exc)

    def _record_token_response(self, token_resp: Dict[str, Any]) -> None:
        self._access_token = token_resp["access_token"]
        self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
        expires_in = int(token_resp.get("expires_in", 3600) or 3600)
        self._expires_at = time.time() + expires_in
        claims = self._extract_jwt_claims(token_resp.get("id_token") or self._access_token)
        if claims.get("tid"):
            self._resolved_tenant_guid = str(claims["tid"])
        if claims.get("name") and not self._display_name:
            self._display_name = str(claims["name"])
        claim_email = str(claims.get("preferred_username") or claims.get("upn") or claims.get("email") or "").strip()
        if claim_email and not self._account_upn:
            self._account_upn = claim_email
        upn = self._fetch_user_profile_upn()
        if upn:
            self._account_upn = upn
        if self._account_upn:
            self._account_id = make_account_id(CalendarProviderType.MS_GRAPH.value, self._account_upn)
        self._account_kind = classify_account_kind(
            "ms_graph",
            email=self._account_upn or "",
            tenant_id=self._resolved_tenant_guid,
        )
        self._status["is_healthy"] = True
        self._status["error_state"] = None
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.CONNECTED.value
        self._sync_status_identity()
        self._save_credentials()

    def is_token_near_expiry(self, threshold_seconds: int = 300) -> bool:
        """Return True if access token expires within threshold_seconds."""
        if not self._expires_at:
            return True
        remaining = self._expires_at - time.time()
        return remaining < threshold_seconds

    def is_authenticated(self) -> bool:
        """Return True if a valid or refreshable token is present."""
        if not self._access_token:
            if self._refresh_token:
                return self.refresh_token()
            return False
        if not self.is_token_near_expiry():
            return True
        if self._refresh_token:
            return self.refresh_token()
        return False

    def refresh_token(self) -> bool:
        """Refresh expired access token using hosted backend broker or local refresh_token."""
        if self._auth_broker is not None and self._auth_broker.is_backend_configured():
            try:
                token_resp = self._auth_broker.refresh_backend_token(
                    CalendarProviderType.MS_GRAPH.value,
                    self.user_id,
                    self._account_id,
                )
                if token_resp.get("access_token"):
                    self._record_token_response(token_resp)
                    return True
            except Exception as exc:
                code, friendly = sanitize_oauth_error_for_user(str(exc), "ms_graph")
                self._status["is_healthy"] = False
                self._status["error_state"] = code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.EXPIRED.value
                return False

        if not self._refresh_token:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
            self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "scope": " ".join(self._active_scopes),
        }).encode("utf-8")
        req = urllib.request.Request(self.token_endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
                expires_in = token_resp.get("expires_in", 3600)
                self._expires_at = time.time() + expires_in
                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                self._status["error_detail"] = None
                self._status["friendly_error"] = None
                self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                return True
        except urllib.error.HTTPError as http_err:
            raw_body = http_err.read().decode("utf-8", errors="replace")
            logger.error("Failed refreshing MS Graph token (HTTP %s): %s", http_err.code, raw_body[:300])
            code, friendly = sanitize_oauth_error_for_user(f"HTTP {http_err.code} {raw_body}", "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = "REFRESH_FAILED" if http_err.code not in (400, 401) else code
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = (
                AccountConnectionState.ADMIN_APPROVAL_REQUIRED.value
                if code == "ADMIN_APPROVAL_REQUIRED"
                else AccountConnectionState.EXPIRED.value
            )
            return False
        except Exception as exc:
            logger.error("Failed refreshing MS Graph token: %s", exc)
            code, friendly = sanitize_oauth_error_for_user(str(exc), "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_OFFLINE" if code == "NETWORK_OFFLINE" else "REFRESH_FAILED"
            self._status["friendly_error"] = friendly
            return False

    # -------------------------------------------------------------------------
    # RFC 8628 DEVICE CODE FLOW (Unified Personal & Work Account Sign-In)
    # -------------------------------------------------------------------------
    def request_device_code(self) -> Dict[str, Any]:
        """
        Request user code and device verification URL from Microsoft Identity Platform.
        Returns dict with keys: user_code, device_code, verification_uri, interval, expires_in.
        """
        self._active_scopes = self._effective_scopes_for_client(self.client_id)
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "scope": " ".join(self._active_scopes),
        }).encode("utf-8")
        req = urllib.request.Request(self.device_code_endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            if not self._custom_client_id:
                alt_client = self.GRAPH_CLI_CLIENT_ID if self.client_id == self.OFFICE_CLIENT_ID else self.OFFICE_CLIENT_ID
                self.client_id = alt_client
                self._active_scopes = self._effective_scopes_for_client(self.client_id)
                fallback_data = urllib.parse.urlencode({
                    "client_id": self.client_id,
                    "scope": " ".join(self._active_scopes),
                }).encode("utf-8")
                fallback_req = urllib.request.Request(self.device_code_endpoint, data=fallback_data, method="POST")
                fallback_req.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urllib.request.urlopen(fallback_req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise

    def poll_device_code_tokens(
        self,
        device_code: str,
        interval: int = 5,
        timeout_sec: int = 900,
        expires_in: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        on_code_received: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Poll token endpoint until user signs in with their device code.
        Automatically diversifies to preauthorized Office client if an enterprise tenant blocks dynamic scopes (AADSTS65002).
        """
        effective_timeout = expires_in if expires_in is not None else timeout_sec
        deadline = time.monotonic() + float(effective_timeout)
        eff_interval = max(0, int(interval))
        tried_office_fallback = False

        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return False, "Sign-in was cancelled."

            data = urllib.parse.urlencode({
                "client_id": self.client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            }).encode("utf-8")
            req = urllib.request.Request(self.token_endpoint, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    token_resp = json.loads(resp.read().decode("utf-8"))
                    self._record_token_response(token_resp)
                    return True, f"Connected as {self._account_upn or 'Microsoft Account'}"
            except urllib.error.HTTPError as http_err:
                try:
                    body = http_err.read().decode("utf-8", errors="replace")
                    err_json = json.loads(body)
                    err_code = err_json.get("error", "")
                    err_desc = str(err_json.get("error_description") or "")
                    if err_code == "authorization_pending":
                        pass  # User has not finished yet, continue polling
                    elif err_code == "slow_down":
                        eff_interval += 5
                    elif err_code == "code_expired":
                        return False, "The device code expired. Please start sign-in again."
                    elif (
                        not tried_office_fallback
                        and not self._custom_client_id
                        and self.client_id != self.OFFICE_CLIENT_ID
                        and ("AADSTS65002" in err_desc or "AADSTS65001" in err_desc)
                    ):
                        # Automatically diversify to preauthorized Office client for corporate tenant
                        tried_office_fallback = True
                        self.client_id = self.OFFICE_CLIENT_ID
                        self._active_scopes = list(self.OFFICE_SCOPES)
                        self._set_authority("organizations")
                        new_resp = self.request_device_code()
                        device_code = str(new_resp.get("device_code", ""))
                        new_user_code = str(new_resp.get("user_code", ""))
                        new_uri = str(new_resp.get("verification_uri", "https://microsoft.com/devicelogin"))
                        copy_to_clipboard_win32(new_user_code)
                        if callable(on_code_received):
                            on_code_received(new_user_code, new_uri)
                        continue
                    else:
                        desc = self._parse_aad_error(body, f"HTTP Error {http_err.code}: {http_err.reason}")
                        return False, desc
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("Device code polling note: %s", exc)

            # Wait poll interval
            sleep_chunk = 0.5
            slept = 0.0
            while slept < eff_interval:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Sign-in was cancelled."
                time.sleep(sleep_chunk)
                slept += sleep_chunk

        return False, "Sign-in timed out. Please try again."

    def authenticate_with_device_code(
        self,
        on_code_received: Optional[Callable[[str, str], None]] = None,
        timeout_sec: int = 900,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Complete device code authentication:
        1. Requests user code from Microsoft
        2. Automatically copies code to Windows clipboard
        3. Opens https://microsoft.com/devicelogin in browser
        4. Polls token endpoint
        """
        try:
            device_resp = self.request_device_code()
            user_code = str(device_resp.get("user_code", ""))
            device_code = str(device_resp.get("device_code", ""))
            verification_uri = str(device_resp.get("verification_uri", "https://microsoft.com/devicelogin"))
            interval = int(device_resp.get("interval", 5))
            expires_in = int(device_resp.get("expires_in", 900))

            # Copy code to Windows clipboard for seamless pasting
            copy_to_clipboard_win32(user_code)

            if on_code_received:
                on_code_received(user_code, verification_uri)

            # Open Microsoft device login page
            webbrowser.open(verification_uri)

            return self.poll_device_code_tokens(
                device_code=device_code,
                interval=interval,
                timeout_sec=min(timeout_sec, expires_in),
                cancel_event=cancel_event,
                on_code_received=on_code_received,
            )
        except Exception as exc:
            err_msg = f"Failed requesting device code from Microsoft: {exc}"
            logger.error(err_msg)
            return False, err_msg

    # -------------------------------------------------------------------------
    # RFC 7636 OAUTH 2.0 PKCE BROWSER FLOW
    # -------------------------------------------------------------------------
    @staticmethod
    def generate_pkce_pair() -> Tuple[str, str]:
        """Generate RFC 7636 PKCE (code_verifier, code_challenge_s256)."""
        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return code_verifier, code_challenge

    def build_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: str,
    ) -> str:
        """Build the Microsoft v2.0 OAuth2 PKCE authorization URL."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self._active_scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if self.account_hint and "@" in self.account_hint:
            params["login_hint"] = self.account_hint
        return f"{self.auth_endpoint}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _parse_aad_error(raw_body: str, fallback: str) -> str:
        try:
            err_json = json.loads(raw_body)
            desc = str(err_json.get("error_description") or "").strip()
            if "AADSTS65002" in desc:
                return (
                    "Microsoft blocked dynamic calendar consent for this client ID (AADSTS65002). "
                    "Click 'Sign in with Microsoft Code' (which uses preauthorized Microsoft Office permissions), "
                    "use the 'Outlook ICS / MAPI' tab (zero admin approval required), or enter your own Azure App Client ID."
                )
            if "AADSTS50011" in desc:
                return (
                    "Redirect URI mismatch (AADSTS50011). Use 'Sign in with Microsoft Code' (recommended for WFX accounts) "
                    "or register http://localhost in your Azure App Registration under Mobile & Desktop applications."
                )
            if "AADSTS65001" in desc or "AADSTS700016" in desc:
                return (
                    "Your organization requires admin consent for this app. "
                    "Use 'Sign in with Microsoft Code', switch to the 'Outlook ICS / MAPI' tab, or use your company's Azure Client ID."
                )
            if desc:
                return desc.splitlines()[0][:240]
        except Exception:
            pass
        return fallback

    def _fetch_user_profile_upn(self) -> Optional[str]:
        """Resolve the signed-in user's UPN / email and display name from Graph /me."""
        if not self._access_token:
            return None
        try:
            url = f"{self.GRAPH_BASE_URL}/me?$select=mail,userPrincipalName,displayName"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                disp = str(data.get("displayName") or "").strip()
                if disp:
                    self._display_name = disp
                return str(
                    data.get("mail")
                    or data.get("userPrincipalName")
                    or disp
                    or ""
                ).strip() or None
        except Exception as exc:
            logger.debug("Failed fetching user profile UPN: %s", exc)
            return None

    def authenticate_interactive_pkce(
        self,
        callback_port: int = 8400,
        timeout_sec: int = 180,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Execute interactive PKCE OAuth authorization with local HTTP listener.
        Uses Microsoft Graph CLI client, custom client ID, or hosted auth backend with http://localhost:{port}.
        """
        use_backend = self._auth_broker is not None and self._auth_broker.is_backend_configured()
        if not self._custom_client_id and auth_url_opener is None and not use_backend:
            logger.info("No custom Azure Client ID configured; routing to Device Code Flow for universal Microsoft sign-in.")
            return self.authenticate_with_device_code(timeout_sec=timeout_sec, cancel_event=cancel_event)
        self._active_scopes = self._effective_scopes_for_client(self.client_id)

        code_verifier, code_challenge = self.generate_pkce_pair()
        expected_state = secrets.token_urlsafe(24)
        result_box: Dict[str, Any] = {"code": None, "state": None, "error": None, "done": threading.Event()}

        class _OAuthCallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                code_list = params.get("code")
                state_list = params.get("state")
                err_list = params.get("error")
                err_desc = params.get("error_description", ["Unknown error"])[0]

                if err_list:
                    friendly_err = MsGraphCalendarProvider._parse_aad_error(
                        json.dumps({"error_description": err_desc}),
                        err_desc,
                    )
                    result_box["error"] = friendly_err
                    title_h = "VOX Sign-In Unsuccessful"
                    body_h = friendly_err
                elif not code_list or not state_list:
                    result_box["error"] = "Missing authorization code or state"
                    title_h = "VOX Sign-In Error"
                    body_h = "Authorization response was missing required parameters."
                elif state_list[0] != expected_state:
                    result_box["error"] = "State verification failed (possible CSRF)"
                    title_h = "VOX Security Alert"
                    body_h = "State verification failed. For security, this sign-in attempt was rejected."
                else:
                    result_box["code"] = code_list[0]
                    result_box["state"] = state_list[0]
                    title_h = "VOX Calendar Connected!"
                    body_h = "Your Microsoft 365 calendar was successfully authorized. You may close this tab."

                page = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>{title_h}</title>"
                    "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#161513;color:#F5F2EC;"
                    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
                    ".card{background:#201E1B;border:1px solid #353028;border-radius:14px;padding:28px 34px;max-width:460px}"
                    "h2{margin:0 0 10px;font-weight:600}p{color:#B8B2A6;line-height:1.5;margin:0}</style></head>"
                    f"<body><div class='card'><h2>{title_h}</h2><p>{body_h}</p></div>"
                    "<script>setTimeout(()=>window.close(), 1800);</script></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                result_box["done"].set()

            def log_message(self, format: str, *args: Any) -> None:
                return

        server: Optional[HTTPServer] = None
        try:
            # Bind to preferred port (8400) or fallback to dynamic port
            try:
                server = HTTPServer(("127.0.0.1", int(callback_port)), _OAuthCallbackHandler)
            except OSError:
                server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)

            server.timeout = 0.5
            actual_port = int(server.server_address[1])
            redirect_uri = f"http://localhost:{actual_port}"
            auth_url = self.build_authorization_url(redirect_uri, expected_state, code_challenge)

            def _serve_loop() -> None:
                deadline = time.monotonic() + float(timeout_sec)
                while not result_box["done"].is_set() and time.monotonic() < deadline:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    try:
                        server.handle_request()
                    except Exception:
                        break

            srv_thread = threading.Thread(target=_serve_loop, daemon=True, name="vox-oauth-callback")
            srv_thread.start()

            opener = auth_url_opener or webbrowser.open
            opener(auth_url)

            deadline = time.monotonic() + float(timeout_sec)
            while not result_box["done"].is_set() and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Sign-in cancelled."
                time.sleep(0.2)

            if not result_box["done"].is_set():
                return False, f"Sign-in timed out after {timeout_sec} seconds."

            if result_box["error"]:
                return False, str(result_box["error"])

            code = result_box["code"]
            return self._exchange_code_for_tokens(code, redirect_uri, code_verifier)

        except Exception as exc:
            return False, f"Local callback server error: {exc}"
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str, code_verifier: str) -> Tuple[bool, str]:
        """Exchange authorization code for access and refresh tokens using hosted backend or direct PKCE."""
        if self._auth_broker is not None and self._auth_broker.is_backend_configured():
            try:
                token_resp = self._auth_broker.exchange_backend_code(
                    provider=CalendarProviderType.MS_GRAPH.value,
                    user_id=self.user_id,
                    code=code,
                    state="",
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                )
                self._record_token_response(token_resp)
                return True, f"Connected as {self._account_upn or 'Microsoft 365 User'}"
            except Exception as exc:
                err_code, friendly = sanitize_oauth_error_for_user(str(exc), "ms_graph")
                self._status["is_healthy"] = False
                self._status["error_state"] = err_code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.FAILED.value
                return False, friendly

        token_data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": " ".join(self._active_scopes),
        }).encode("utf-8")

        req = urllib.request.Request(self.token_endpoint, data=token_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
                self._record_token_response(token_resp)
                return True, f"Connected as {self._account_upn or 'Microsoft 365 User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            desc = self._parse_aad_error(body, f"HTTP Error {http_err.code}: {http_err.reason}")
            err_code, friendly = sanitize_oauth_error_for_user(body, "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code or "OAUTH_FAILED"
            self._status["error_detail"] = desc
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = (
                AccountConnectionState.ADMIN_APPROVAL_REQUIRED.value
                if err_code == "ADMIN_APPROVAL_REQUIRED"
                else AccountConnectionState.FAILED.value
            )
            return False, desc
        except Exception as exc:
            err_code, friendly = sanitize_oauth_error_for_user(str(exc), "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code or "NETWORK_ERROR"
            self._status["friendly_error"] = friendly
            return False, str(exc)

    def authenticate(
        self,
        interactive: bool = True,
        timeout_sec: int = 180,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> bool:
        """Verify cached session or initiate interactive PKCE / Device Code authentication."""
        if self.is_authenticated():
            return True
        if not interactive:
            return False
        if (
            self._custom_client_id
            or auth_url_opener is not None
            or (self._auth_broker is not None and self._auth_broker.is_backend_configured())
        ):
            ok, _msg = self.authenticate_interactive_pkce(
                timeout_sec=timeout_sec,
                auth_url_opener=auth_url_opener,
                cancel_event=cancel_event,
            )
            return ok
        ok, _msg = self.authenticate_with_device_code(
            timeout_sec=timeout_sec,
            cancel_event=cancel_event,
        )
        return ok

    def disconnect(self) -> bool:
        """Revoke tokens, wipe DPAPI cache & vault credentials, and transition to disconnected."""
        if self._auth_broker is not None and self._account_id:
            if self._auth_broker.is_backend_configured():
                self._auth_broker.revoke_backend_account(
                    CalendarProviderType.MS_GRAPH.value,
                    self.user_id,
                    self._account_id,
                )
            self._auth_broker.delete_credentials(self.user_id, self._account_id)

        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._account_upn = None
        self._display_name = ""
        self._delta_links.clear()
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception as exc:
            logger.error("Failed removing DPAPI auth file: %s", exc)

        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.DISCONNECTED.value
        self._status["account_upn"] = None
        self._status["display_name"] = ""
        return True

    def set_selected_calendars(self, calendar_ids: List[str]) -> None:
        clean = [str(cid).strip() for cid in (calendar_ids or []) if str(cid).strip()]
        if clean != self._selected_calendars:
            self._delta_links.clear()
        self._selected_calendars = clean
        self._status["selected_calendars"] = list(clean)
        if self._access_token or self._refresh_token:
            self._save_credentials()

    def get_selected_calendars(self) -> List[str]:
        return list(self._selected_calendars)

    def _http_get_json_with_recovery(self, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        """Execute Graph GET request with one automatic 401 refresh + retry."""
        for attempt in range(2):
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            req.add_header("Prefer", 'outlook.timezone="UTC"')
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8")), 200, None
            except urllib.error.HTTPError as http_err:
                if http_err.code == 401 and attempt == 0 and self._refresh_token:
                    if self.refresh_token():
                        continue
                    return None, 401, "AUTH_EXPIRED"
                if http_err.code == 401:
                    return None, 401, "AUTH_REQUIRED"
                if http_err.code == 429:
                    try:
                        self.retry_after_seconds = int(http_err.headers.get("Retry-After", 30))
                    except Exception:
                        self.retry_after_seconds = 30
                    return None, 429, "HTTP_429_RATE_LIMIT"
                if http_err.code == 410:
                    return None, 410, "DELTA_EXPIRED"
                raw_body = ""
                try:
                    raw_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                return None, http_err.code, raw_body or f"HTTP_{http_err.code}"
            except Exception as exc:
                logger.error("Failed querying MS Graph endpoint: %s", exc)
                return None, None, "NETWORK_OFFLINE"
        return None, 401, "AUTH_REQUIRED"

    def list_calendars(self) -> List[Dict[str, Any]]:
        """Fetch user's calendars via Microsoft Graph /me/calendars."""
        if not self.is_authenticated():
            return list(self._available_calendars)
        url = f"{self.GRAPH_BASE_URL}/me/calendars?$select=id,name,isDefaultCalendar,canEdit,owner&$top=50"
        data, code, _err = self._http_get_json_with_recovery(url)
        if code == 200 and isinstance(data, dict):
            cals: List[Dict[str, Any]] = []
            for item in data.get("value", []):
                cid = str(item.get("id") or "")
                if not cid:
                    continue
                owner_info = item.get("owner") or {}
                cals.append({
                    "id": cid,
                    "name": str(item.get("name") or "Calendar").strip(),
                    "primary": bool(item.get("isDefaultCalendar", False)),
                    "owner": str(owner_info.get("address") or owner_info.get("name") or ""),
                })
            self._available_calendars = cals
            self._status["available_calendars"] = list(cals)
            self._save_credentials()
            return cals
        return list(self._available_calendars)

    def _parse_graph_item(
        self,
        item: Dict[str, Any],
        calendar_id: str = "",
        calendar_name: str = "",
    ) -> Tuple[Optional[CalendarMeeting], bool, str]:
        m_id = str(item.get("id") or "")
        if "@removed" in item or item.get("isCancelled", False):
            return None, True, m_id

        title = str(item.get("subject") or "Untitled Meeting").strip()
        st_obj = item.get("start") or {}
        et_obj = item.get("end") or {}
        st_str = str(st_obj.get("dateTime") or "")
        et_str = str(et_obj.get("dateTime") or "")
        try:
            st_norm = st_str.replace("Z", "+00:00")
            et_norm = et_str.replace("Z", "+00:00")
            start_time = datetime.fromisoformat(st_norm)
            end_time = datetime.fromisoformat(et_norm)
            if start_time.tzinfo is None and str(st_obj.get("timeZone") or "").upper() in ("UTC", ""):
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None and str(et_obj.get("timeZone") or "").upper() in ("UTC", ""):
                end_time = end_time.replace(tzinfo=timezone.utc)
        except Exception:
            return None, False, m_id

        location_name = str((item.get("location") or {}).get("displayName") or "").strip()
        org_email_obj = (item.get("organizer") or {}).get("emailAddress") or {}
        organizer_name = str(org_email_obj.get("name") or "").strip()
        organizer_email = str(org_email_obj.get("address") or "").strip()
        eff_organizer = (
            f"{organizer_name} ({organizer_email})"
            if (organizer_name and organizer_email and organizer_name != organizer_email)
            else (organizer_name or organizer_email)
        )

        attendees: List[str] = []
        for att in item.get("attendees", []):
            em_data = att.get("emailAddress") or {}
            a_name = str(em_data.get("name") or "").strip()
            a_email = str(em_data.get("address") or "").strip()
            if a_name:
                attendees.append(f"{a_name} ({a_email})" if (a_email and a_name != a_email) else a_name)
            elif a_email:
                attendees.append(a_email)

        online_mtg = item.get("onlineMeeting") or {}
        join_url = online_mtg.get("joinUrl")
        platform = "generic"
        body_content = str((item.get("body") or {}).get("content") or "")
        if join_url:
            _, platform = extract_meeting_links(str(join_url))
            if platform == "generic" and "teams" in str(join_url).lower():
                platform = "teams"
        else:
            found_url, found_plat = extract_meeting_links(f"{location_name}\n{body_content}")
            if found_url:
                join_url = found_url
                platform = found_plat

        ev_type = str(item.get("type") or "").strip()
        series_master_id = str(item.get("seriesMasterId") or "")
        is_recurring = bool(series_master_id or ev_type in ("occurrence", "exception", "seriesMaster"))
        is_modified = ev_type == "exception"

        meeting = CalendarMeeting(
            id=m_id,
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location_name or ("Online Meeting" if join_url else ""),
            join_url=join_url,
            attendees=attendees,
            organizer=eff_organizer,
            is_online=bool(join_url or "teams" in location_name.lower() or "zoom" in location_name.lower() or "meet" in location_name.lower()),
            is_cancelled=False,
            sensitivity=str(item.get("sensitivity") or "normal"),
            platform=platform,
            is_recurring=is_recurring,
            provider_type=CalendarProviderType.MS_GRAPH.value,
            provider_account=self._account_upn or "",
            description=re.sub(r"<[^>]+>", " ", body_content)[:500].strip(),
            account_id=self._account_id,
            calendar_id=calendar_id,
            calendar_name=calendar_name,
            ical_uid=str(item.get("iCalUId") or ""),
            series_master_id=series_master_id,
            is_modified_occurrence=is_modified,
            timezone_name=str(st_obj.get("timeZone") or "UTC"),
            conference_id=str(online_mtg.get("conferenceId") or ""),
        )
        return meeting, False, m_id

    def sync_events_delta(self, window_minutes: int = 43200, use_delta: bool = True) -> Dict[str, Any]:
        """
        Perform full or incremental delta synchronization against Microsoft Graph
        /me/calendarView/delta (or /me/calendars/{id}/calendarView/delta).
        Returns {"meetings": [...], "removed_ids": [...], "is_incremental": bool, "ok": bool, "error_code": ...}.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            return {"meetings": [], "removed_ids": [], "is_incremental": False, "ok": False, "error_code": "AUTH_REQUIRED"}

        if self.is_token_near_expiry() and not self.refresh_token():
            return {"meetings": [], "removed_ids": [], "is_incremental": False, "ok": False, "error_code": "AUTH_EXPIRED"}

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        start_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        cal_name_map = {c.get("id", ""): c.get("name", "") for c in self._available_calendars}
        cal_targets = list(self._selected_calendars) if self._selected_calendars else ["default"]

        all_meetings: List[CalendarMeeting] = []
        removed_ids: List[str] = []
        used_incremental = False
        had_error: Optional[str] = None

        for cal_key in cal_targets:
            delta_url = self._delta_links.get(cal_key) if use_delta else None
            if delta_url:
                used_incremental = True
                url: Optional[str] = delta_url
            else:
                base_path = "/me/calendarView/delta" if cal_key == "default" else f"/me/calendars/{urllib.parse.quote(cal_key)}/calendarView/delta"
                url = (
                    f"{self.GRAPH_BASE_URL}{base_path}"
                    f"?startDateTime={start_iso}&endDateTime={end_iso}"
                    f"&$select=id,subject,start,end,location,organizer,attendees,onlineMeeting,isCancelled,sensitivity,body,iCalUId,seriesMasterId,type"
                    f"&$top=100"
                )

            retried_after_410 = False
            max_pages = 20
            while url and max_pages > 0:
                max_pages -= 1
                res_data, code, err_code = self._http_get_json_with_recovery(url)
                if code == 410 and not retried_after_410:
                    # Sync state expired -> reset delta link and run fresh window scan
                    retried_after_410 = True
                    used_incremental = False
                    self._delta_links.pop(cal_key, None)
                    base_path = "/me/calendarView/delta" if cal_key == "default" else f"/me/calendars/{urllib.parse.quote(cal_key)}/calendarView/delta"
                    url = (
                        f"{self.GRAPH_BASE_URL}{base_path}"
                        f"?startDateTime={start_iso}&endDateTime={end_iso}"
                        f"&$select=id,subject,start,end,location,organizer,attendees,onlineMeeting,isCancelled,sensitivity,body,iCalUId,seriesMasterId,type"
                        f"&$top=100"
                    )
                    continue

                if res_data is None:
                    had_error = err_code or f"HTTP_{code}"
                    break

                cal_name = cal_name_map.get(cal_key, "") if cal_key != "default" else ""
                for item in res_data.get("value", []):
                    mtg, is_rem, ev_id = self._parse_graph_item(item, calendar_id="" if cal_key == "default" else cal_key, calendar_name=cal_name)
                    if is_rem and ev_id:
                        removed_ids.append(ev_id)
                    elif mtg is not None:
                        all_meetings.append(mtg)

                if res_data.get("@odata.deltaLink"):
                    self._delta_links[cal_key] = str(res_data["@odata.deltaLink"])
                url = res_data.get("@odata.nextLink")

            if had_error:
                break

        if had_error:
            err_code, friendly = sanitize_oauth_error_for_user(had_error, "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = had_error if had_error in ("AUTH_REQUIRED", "AUTH_EXPIRED", "HTTP_429_RATE_LIMIT", "NETWORK_OFFLINE") else err_code
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = (
                AccountConnectionState.EXPIRED.value
                if self._status["error_state"] in ("AUTH_REQUIRED", "AUTH_EXPIRED")
                else AccountConnectionState.FAILED.value
            )
            return {
                "meetings": all_meetings,
                "removed_ids": removed_ids,
                "is_incremental": used_incremental,
                "ok": False,
                "error_code": self._status["error_state"],
            }

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.CONNECTED.value
        self._status["last_sync_at"] = now_iso
        if self._delta_links:
            self._save_credentials()
        return {
            "meetings": all_meetings,
            "removed_ids": removed_ids,
            "is_incremental": used_incremental,
            "ok": True,
            "error_code": None,
        }

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        """
        Fetch scheduled meetings within forward window (default 30 days = 43200 minutes)
        using Microsoft Graph /me/calendarView with pagination, reactive 401 refresh, and link extraction.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            return []

        if self.is_token_near_expiry():
            if not self.refresh_token():
                return []

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        start_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        cal_name_map = {c.get("id", ""): c.get("name", "") for c in self._available_calendars}
        cal_targets = list(self._selected_calendars) if self._selected_calendars else ["default"]

        all_meetings: List[CalendarMeeting] = []
        had_error: Optional[str] = None

        for cal_key in cal_targets:
            base_path = "/me/calendarView" if cal_key == "default" else f"/me/calendars/{urllib.parse.quote(cal_key)}/calendarView"
            url: Optional[str] = (
                f"{self.GRAPH_BASE_URL}{base_path}"
                f"?startDateTime={start_iso}&endDateTime={end_iso}"
                f"&$select=id,subject,start,end,location,organizer,attendees,onlineMeeting,isCancelled,sensitivity,body,iCalUId,seriesMasterId,type"
                f"&$top=100"
            )
            max_pages = 10
            cal_name = cal_name_map.get(cal_key, "") if cal_key != "default" else ""

            while url and max_pages > 0:
                max_pages -= 1
                res_data, code, err_code = self._http_get_json_with_recovery(url)
                if res_data is None:
                    had_error = err_code or f"HTTP_{code}"
                    break
                for item in res_data.get("value", []):
                    mtg, _is_rem, _ev_id = self._parse_graph_item(item, calendar_id="" if cal_key == "default" else cal_key, calendar_name=cal_name)
                    if mtg is not None:
                        all_meetings.append(mtg)
                url = res_data.get("@odata.nextLink")

            if had_error:
                break

        if had_error:
            err_code, friendly = sanitize_oauth_error_for_user(had_error, "ms_graph")
            self._status["is_healthy"] = False
            self._status["error_state"] = had_error if had_error in ("AUTH_REQUIRED", "AUTH_EXPIRED", "HTTP_429_RATE_LIMIT", "NETWORK_OFFLINE") else err_code
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = (
                AccountConnectionState.EXPIRED.value
                if self._status["error_state"] in ("AUTH_REQUIRED", "AUTH_EXPIRED")
                else AccountConnectionState.FAILED.value
            )
            return all_meetings

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.CONNECTED.value
        self._status["last_sync_at"] = now_iso
        return all_meetings

    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health status dictionary."""
        return dict(self._status)

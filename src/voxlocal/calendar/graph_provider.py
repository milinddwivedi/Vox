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

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.graph")


def dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt byte payload using Windows DPAPI CryptProtectData."""
    if not data:
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), "vox_calendar_token", None, None, None, 0, ctypes.byref(out_blob)
        ):
            buf = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return buf
    except Exception as exc:
        logger.warning("DPAPI encryption fallback to b64: %s", exc)
    return base64.b64encode(data)


def dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt byte payload using Windows DPAPI CryptUnprotectData."""
    if not data:
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            buf = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return buf
    except Exception as exc:
        logger.warning("DPAPI decryption fallback: %s", exc)
    try:
        return base64.b64decode(data)
    except Exception:
        return data


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

    # Azure CLI Public Client ID widely used for device code and local loopback desktop access
    DEFAULT_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
    SCOPES = ["Calendars.Read", "User.Read", "offline_access"]
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        client_id: Optional[str] = None,
        auth_file_path: Optional[Path] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self.client_id = (client_id or os.environ.get("MS_GRAPH_CLIENT_ID", self.DEFAULT_CLIENT_ID)).strip() or self.DEFAULT_CLIENT_ID
        self.tenant_id = (tenant_id or os.environ.get("MS_GRAPH_TENANT_ID", "common")).strip() or "common"
        self.authority = f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id)}"
        self.auth_endpoint = f"{self.authority}/oauth2/v2.0/authorize"
        self.token_endpoint = f"{self.authority}/oauth2/v2.0/token"
        self.device_code_endpoint = f"{self.authority}/oauth2/v2.0/devicecode"
        self.auth_file_path = auth_file_path or (Path("data") / "calendar_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_upn: Optional[str] = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.MS_GRAPH.value,
            "is_healthy": True,
            "last_poll_time": None,
            "error_state": None,
            "error_detail": None,
            "account_upn": None,
        }
        self.retry_after_seconds: int = 0
        self.backoff_interval_sec: int = 60

        self._load_cached_credentials()

    def _load_cached_credentials(self) -> bool:
        if not self.auth_file_path.exists():
            return False
        try:
            ciphertext = self.auth_file_path.read_bytes()
            plaintext = dpapi_decrypt(ciphertext)
            token_data = json.loads(plaintext.decode("utf-8"))
            self._access_token = token_data.get("access_token")
            self._refresh_token = token_data.get("refresh_token")
            self._expires_at = token_data.get("expires_at")
            self._account_upn = token_data.get("account_upn")
            self._status["account_upn"] = self._account_upn
            return True
        except Exception as exc:
            logger.error("Failed loading cached DPAPI calendar credentials: %s", exc)
            return False

    def _save_credentials(self) -> None:
        try:
            self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
            token_data = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
                "account_upn": self._account_upn,
            }
            plaintext = json.dumps(token_data).encode("utf-8")
            ciphertext = dpapi_encrypt(plaintext)
            self.auth_file_path.write_bytes(ciphertext)
        except Exception as exc:
            logger.error("Failed saving DPAPI encrypted credentials: %s", exc)

    def is_token_near_expiry(self, threshold_seconds: int = 300) -> bool:
        """Return True if access token expires within threshold_seconds."""
        if not self._expires_at:
            return True
        remaining = self._expires_at - time.time()
        return remaining < threshold_seconds

    def is_authenticated(self) -> bool:
        """Return True if a valid or refreshable token is present."""
        if not self._access_token:
            return False
        if not self.is_token_near_expiry():
            return True
        if self._refresh_token:
            return self.refresh_token()
        return False

    def refresh_token(self) -> bool:
        """Refresh expired access token using refresh_token."""
        if not self._refresh_token:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
            return False
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "scope": " ".join(self.SCOPES),
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
                return True
        except Exception as exc:
            logger.error("Failed refreshing MS Graph token: %s", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "REFRESH_FAILED"
            return False

    # -------------------------------------------------------------------------
    # RFC 8628 DEVICE CODE FLOW (Recommended for Corporate / WFX Work Accounts)
    # -------------------------------------------------------------------------
    def request_device_code(self) -> Dict[str, Any]:
        """
        Request user code and device verification URL from Microsoft Entra ID.
        Returns dict with keys: user_code, device_code, verification_uri, interval, expires_in.
        """
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "scope": " ".join(self.SCOPES),
        }).encode("utf-8")
        req = urllib.request.Request(self.device_code_endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def poll_device_code_tokens(
        self,
        device_code: str,
        interval: int = 5,
        timeout_sec: int = 900,
        expires_in: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Poll token endpoint until user signs in with their device code.
        """
        effective_timeout = expires_in if expires_in is not None else timeout_sec
        deadline = time.monotonic() + float(effective_timeout)
        eff_interval = max(0, int(interval))

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
                    self._access_token = token_resp["access_token"]
                    self._refresh_token = token_resp.get("refresh_token")
                    expires_in = token_resp.get("expires_in", 3600)
                    self._expires_at = time.time() + expires_in
                    upn = self._fetch_user_profile_upn()
                    if upn:
                        self._account_upn = upn
                        self._status["account_upn"] = upn
                    self._save_credentials()
                    self._status["is_healthy"] = True
                    self._status["error_state"] = None
                    return True, f"Connected as {self._account_upn or 'Microsoft 365 User'}"
            except urllib.error.HTTPError as http_err:
                try:
                    body = http_err.read().decode("utf-8", errors="replace")
                    err_json = json.loads(body)
                    err_code = err_json.get("error", "")
                    if err_code == "authorization_pending":
                        pass  # User has not finished yet, continue polling
                    elif err_code == "slow_down":
                        eff_interval += 5
                    elif err_code == "code_expired":
                        return False, "The device code expired. Please start sign-in again."
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
            "scope": " ".join(self.SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{self.auth_endpoint}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _parse_aad_error(raw_body: str, fallback: str) -> str:
        try:
            err_json = json.loads(raw_body)
            desc = str(err_json.get("error_description") or "").strip()
            if "AADSTS50011" in desc:
                return (
                    "Redirect URI mismatch (AADSTS50011). Use the 'Sign In with Code' tab (recommended for WFX accounts) "
                    "or register http://localhost:8080/callback in your Azure App Registration."
                )
            if "AADSTS65001" in desc or "AADSTS700016" in desc:
                return (
                    "Your organization requires admin consent for this app. "
                    "Use the 'Sign In with Code' tab or use your company's registered Azure Client ID."
                )
            if desc:
                return desc.splitlines()[0][:240]
        except Exception:
            pass
        return fallback

    def _fetch_user_profile_upn(self) -> Optional[str]:
        """Resolve the signed-in user's UPN / email from Graph /me."""
        if not self._access_token:
            return None
        try:
            url = f"{self.GRAPH_BASE_URL}/me?$select=mail,userPrincipalName,displayName"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return str(
                    data.get("mail")
                    or data.get("userPrincipalName")
                    or data.get("displayName")
                    or ""
                ).strip() or None
        except Exception as exc:
            logger.debug("Failed fetching user profile UPN: %s", exc)
            return None

    def authenticate_interactive_pkce(
        self,
        callback_port: int = 8080,
        timeout_sec: int = 180,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Execute interactive PKCE OAuth authorization with local HTTP listener.
        Standardizes on http://localhost:8080/callback.
        """
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
                    result_box["error"] = err_desc
                    title_h = "VOX Sign-In Unsuccessful"
                    body_h = f"Microsoft returned: {err_desc}"
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
            # Bind to 8080 or fallback to dynamic port if 8080 is busy
            try:
                server = HTTPServer(("127.0.0.1", int(callback_port)), _OAuthCallbackHandler)
            except OSError:
                server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)

            server.timeout = 0.5
            actual_port = int(server.server_address[1])
            redirect_uri = f"http://localhost:{actual_port}/callback"
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
        """Exchange authorization code for access and refresh tokens using PKCE."""
        token_data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": " ".join(self.SCOPES),
        }).encode("utf-8")

        req = urllib.request.Request(self.token_endpoint, data=token_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token")
                expires_in = token_resp.get("expires_in", 3600)
                self._expires_at = time.time() + expires_in
                upn = self._fetch_user_profile_upn()
                if upn:
                    self._account_upn = upn
                    self._status["account_upn"] = upn
                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                return True, f"Connected as {self._account_upn or 'Microsoft 365 User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            desc = self._parse_aad_error(body, f"HTTP Error {http_err.code}: {http_err.reason}")
            self._status["is_healthy"] = False
            self._status["error_state"] = "OAUTH_FAILED"
            self._status["error_detail"] = desc
            return False, desc
        except Exception as exc:
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_ERROR"
            return False, str(exc)

    def authenticate(self, interactive: bool = True, cancel_event: Optional[threading.Event] = None) -> bool:
        """Verify cached session or initiate interactive authentication."""
        if self.is_authenticated():
            return True
        if not interactive:
            return False
        # Default interactive auth invokes device code flow for maximum compatibility
        ok, _msg = self.authenticate_with_device_code(cancel_event=cancel_event)
        return ok

    def disconnect(self) -> bool:
        """Revoke tokens, wipe DPAPI cache, and transition to disconnected."""
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._account_upn = None
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception as exc:
            logger.error("Failed removing DPAPI auth file: %s", exc)

        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["account_upn"] = None
        return True

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        """
        Fetch scheduled meetings within forward window (default 30 days = 43200 minutes)
        using Microsoft Graph /me/calendarView with pagination and link extraction.
        """
        self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        if not self.is_authenticated():
            return []

        if self.is_token_near_expiry():
            if not self.refresh_token():
                return []

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        start_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        url = (
            f"{self.GRAPH_BASE_URL}/me/calendarView"
            f"?startDateTime={start_iso}&endDateTime={end_iso}"
            f"&$select=id,subject,start,end,location,organizer,attendees,onlineMeeting,isCancelled,sensitivity,body"
            f"&$top=100"
        )

        all_meetings: List[CalendarMeeting] = []
        max_pages = 5  # Safety cap: up to 500 events

        while url and max_pages > 0:
            max_pages -= 1
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("Authorization", f"Bearer {self._access_token}")
                req.add_header("Prefer", 'outlook.timezone="UTC"')

                with urllib.request.urlopen(req, timeout=20) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    items = res_data.get("value", [])

                    for item in items:
                        if item.get("isCancelled", False):
                            continue
                        m_id = str(item.get("id", ""))
                        title = str(item.get("subject") or "Untitled Meeting").strip()
                        st_str = item.get("start", {}).get("dateTime", "")
                        et_str = item.get("end", {}).get("dateTime", "")
                        try:
                            start_time = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                            end_time = datetime.fromisoformat(et_str.replace("Z", "+00:00"))
                        except Exception:
                            continue

                        location_name = str(item.get("location", {}).get("displayName") or "").strip()
                        organizer_name = str(item.get("organizer", {}).get("emailAddress", {}).get("name") or "").strip()
                        organizer_email = str(item.get("organizer", {}).get("emailAddress", {}).get("address") or "").strip()
                        eff_organizer = f"{organizer_name} ({organizer_email})" if (organizer_name and organizer_email and organizer_name != organizer_email) else (organizer_name or organizer_email)

                        attendees: List[str] = []
                        for att in item.get("attendees", []):
                            em_data = att.get("emailAddress", {})
                            a_name = em_data.get("name", "").strip()
                            a_email = em_data.get("address", "").strip()
                            if a_name:
                                attendees.append(f"{a_name} ({a_email})" if (a_email and a_name != a_email) else a_name)
                            elif a_email:
                                attendees.append(a_email)

                        # Extract Online Meeting link and platform
                        online_mtg = item.get("onlineMeeting") or {}
                        join_url = online_mtg.get("joinUrl")
                        platform = "teams" if join_url else "generic"

                        body_content = str(item.get("body", {}).get("content", ""))
                        if not join_url:
                            # Search in location and body for Teams, Zoom, Meet, Webex
                            found_url, found_plat = extract_meeting_links(f"{location_name}\n{body_content}")
                            if found_url:
                                join_url = found_url
                                platform = found_plat
                        else:
                            # Re-verify platform from join_url
                            _, platform = extract_meeting_links(join_url)

                        all_meetings.append(
                            CalendarMeeting(
                                id=m_id,
                                title=title,
                                start_time=start_time,
                                end_time=end_time,
                                location=location_name or ("Online Meeting" if join_url else ""),
                                join_url=join_url,
                                attendees=attendees,
                                organizer=eff_organizer,
                                is_online=bool(join_url or "teams" in location_name.lower() or "zoom" in location_name.lower()),
                                is_cancelled=False,
                                sensitivity=str(item.get("sensitivity", "normal")),
                                platform=platform,
                                provider_type=CalendarProviderType.MS_GRAPH.value,
                                provider_account=self._account_upn or "",
                                description=re.sub(r"<[^>]+>", " ", body_content)[:500].strip(),
                            )
                        )

                    url = res_data.get("@odata.nextLink")

            except urllib.error.HTTPError as http_err:
                if http_err.code == 401:
                    self._status["error_state"] = "AUTH_REQUIRED"
                elif http_err.code == 429:
                    self.retry_after_seconds = int(http_err.headers.get("Retry-After", 30))
                    self._status["error_state"] = "HTTP_429_RATE_LIMIT"
                self._status["is_healthy"] = False
                break
            except Exception as exc:
                logger.error("Failed querying MS Graph calendarView: %s", exc)
                self._status["is_healthy"] = False
                self._status["error_state"] = "NETWORK_OFFLINE"
                break

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        return all_meetings

    def get_provider_status(self) -> Dict[str, Any]:
        """Return provider health status dictionary."""
        return dict(self._status)

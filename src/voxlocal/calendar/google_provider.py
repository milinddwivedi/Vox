"""
VoxLocal Google Calendar Provider.
Production-grade Google Calendar integration supporting:
- Personal Gmail & Corporate Google Workspace accounts (distinguishing `hd` workspace domains)
- Google OAuth 2.0 PKCE / local HTTP callback listener (RFC 7636) or hosted backend broker
- Google Calendar API v3 calendarList + events synchronization with incremental `syncToken` & `410 Gone` recovery
- Expanding recurring events (`singleEvents=true`) & modified occurrence detection (`originalStartTime`)
- Automatic meeting platform extraction (Google Meet, Zoom, Teams, Webex) separated from calendar source
- DPAPI token encryption / SQLite vault and reactive 401 refresh + offline recovery
"""

from __future__ import annotations

import base64
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
from voxlocal.calendar.graph_provider import extract_meeting_links
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.google")


class GoogleCalendarProvider(BaseCalendarProvider):
    """
    Google Calendar integration conforming to BaseCalendarProvider.
    Supports personal Gmail and Google Workspace accounts with multi-calendar selection
    and incremental syncToken synchronization.
    """

    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
    USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
    SCOPES = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        auth_file_path: Optional[Path] = None,
        account_id: str = "",
        user_id: str = "default",
        auth_broker: Any = None,
    ) -> None:
        self.user_id = (user_id or "default").strip() or "default"
        self._auth_broker = auth_broker
        self.client_id = (client_id or os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "")).strip()
        vault_secret = ""
        if not client_secret and self._auth_broker is not None:
            vault_secret = self._auth_broker.load_app_secret("google_calendar_client_secret", user_id=self.user_id)
        self.client_secret = (
            client_secret or os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "") or vault_secret
        ).strip()
        self._explicit_auth_file_path = auth_file_path is not None
        self.auth_file_path = auth_file_path or (Path("data") / "google_calendar_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_email: Optional[str] = None
        self._display_name: str = ""
        self._hosted_domain: str = ""
        self._account_kind: str = "personal"
        self._account_id: str = (account_id or "").strip() or make_account_id(CalendarProviderType.GOOGLE.value, "")
        self._selected_calendars: List[str] = []
        self._available_calendars: List[Dict[str, Any]] = []
        self._sync_tokens: Dict[str, str] = {}
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.GOOGLE.value,
            "account_id": self._account_id,
            "is_healthy": True,
            "last_poll_time": None,
            "last_sync_at": None,
            "error_state": None,
            "error_detail": None,
            "friendly_error": None,
            "connection_state": AccountConnectionState.DISCONNECTED.value,
            "account_email": None,
            "display_name": "",
            "account_kind": self._account_kind,
            "hosted_domain": "",
            "selected_calendars": [],
            "available_calendars": [],
        }
        self.retry_after_seconds: int = 0

        self._load_cached_credentials()

    def _sync_status_identity(self) -> None:
        if self._account_email:
            if not self._account_id or self._account_id.endswith(":default"):
                self._account_id = make_account_id(CalendarProviderType.GOOGLE.value, self._account_email)
            self._account_kind = classify_account_kind(
                "google",
                email=self._account_email,
                hosted_domain=self._hosted_domain,
            )
        self._status["account_id"] = self._account_id
        self._status["account_email"] = self._account_email
        self._status["display_name"] = self._display_name
        self._status["account_kind"] = self._account_kind
        self._status["hosted_domain"] = self._hosted_domain
        self._status["selected_calendars"] = list(self._selected_calendars)
        self._status["available_calendars"] = list(self._available_calendars)

    def _apply_token_data(self, data: Dict[str, Any]) -> None:
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")
        self._expires_at = data.get("expires_at")
        self._account_email = data.get("account_email") or self._account_email
        self._display_name = str(data.get("display_name") or self._display_name or "")
        self._hosted_domain = str(data.get("hosted_domain") or self._hosted_domain or "")
        if isinstance(data.get("selected_calendars"), list):
            self._selected_calendars = [str(c) for c in data["selected_calendars"] if c]
        if isinstance(data.get("available_calendars"), list):
            self._available_calendars = list(data["available_calendars"])
        if isinstance(data.get("sync_tokens"), dict):
            self._sync_tokens = {str(k): str(v) for k, v in data["sync_tokens"].items() if v}
        if not self.client_id and data.get("client_id"):
            self.client_id = data["client_id"]
        if not self.client_secret and data.get("client_secret"):
            self.client_secret = data["client_secret"]
        if self._access_token or self._refresh_token:
            self._status["connection_state"] = AccountConnectionState.CONNECTED.value
            self._status["error_state"] = None
            self._status["friendly_error"] = None
        self._sync_status_identity()

    def _load_cached_credentials(self) -> bool:
        if self._auth_broker is not None and self._account_id and not self._account_id.endswith(":default"):
            vault_data = self._auth_broker.load_credentials(self.user_id, self._account_id)
            if isinstance(vault_data, dict) and (vault_data.get("access_token") or vault_data.get("refresh_token")):
                self._apply_token_data(vault_data)
                return True
        if not self.auth_file_path.exists():
            return False
        try:
            ciphertext = self.auth_file_path.read_bytes()
            plaintext = dpapi_decrypt(ciphertext)
            data = json.loads(plaintext.decode("utf-8"))
            self._apply_token_data(data)
            return True
        except Exception as exc:
            logger.error("Failed loading cached Google Calendar credentials: %s", exc)
            return False

    def _save_credentials(self) -> None:
        self._sync_status_identity()
        token_data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "account_email": self._account_email,
            "display_name": self._display_name,
            "hosted_domain": self._hosted_domain,
            "account_kind": self._account_kind,
            "client_id": self.client_id,
            "selected_calendars": list(self._selected_calendars),
            "available_calendars": list(self._available_calendars),
            "sync_tokens": dict(self._sync_tokens),
        }
        if self._auth_broker is not None and self._account_id:
            try:
                self._auth_broker.save_credentials(
                    self.user_id,
                    self._account_id,
                    CalendarProviderType.GOOGLE.value,
                    token_data,
                )
            except Exception as exc:
                logger.warning("Failed storing Google credentials in DB vault: %s", exc)
        if self._explicit_auth_file_path or self._auth_broker is None:
            try:
                self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
                file_payload = dict(token_data)
                if self.client_secret:
                    file_payload["client_secret"] = self.client_secret
                plaintext = json.dumps(file_payload).encode("utf-8")
                ciphertext = dpapi_encrypt(plaintext)
                self.auth_file_path.write_bytes(ciphertext)
            except Exception as exc:
                logger.error("Failed saving Google Calendar DPAPI credentials: %s", exc)

    def is_token_near_expiry(self, threshold_seconds: int = 300) -> bool:
        if not self._expires_at:
            return True
        return (self._expires_at - time.time()) < threshold_seconds

    def is_authenticated(self) -> bool:
        if not self._access_token:
            if self._refresh_token:
                ok = self.refresh_token()
                if not ok and self._status.get("error_state") == "NETWORK_OFFLINE":
                    return True
                return ok
            return False
        if not self.is_token_near_expiry():
            return True
        if self._refresh_token:
            ok = self.refresh_token()
            if not ok and self._status.get("error_state") == "NETWORK_OFFLINE":
                return True
            return ok
        return False

    def refresh_token(self) -> bool:
        """Refresh Google OAuth 2.0 access token via hosted backend or refresh_token."""
        if self._auth_broker is not None and self._auth_broker.is_backend_configured():
            try:
                token_resp = self._auth_broker.refresh_backend_token(
                    CalendarProviderType.GOOGLE.value,
                    self.user_id,
                    self._account_id,
                )
                if token_resp.get("access_token"):
                    self._access_token = token_resp["access_token"]
                    self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
                    self._expires_at = time.time() + int(token_resp.get("expires_in", 3600) or 3600)
                    self._save_credentials()
                    self._status["is_healthy"] = True
                    self._status["error_state"] = None
                    self._status["friendly_error"] = None
                    self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                    return True
            except Exception as exc:
                code, friendly = sanitize_oauth_error_for_user(str(exc), "google")
                self._status["is_healthy"] = False
                self._status["error_state"] = code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.EXPIRED.value
                return False

        if not self._refresh_token or not self.client_id:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
            self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False

        data_dict = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        if self.client_secret:
            data_dict["client_secret"] = self.client_secret

        data = urllib.parse.urlencode(data_dict).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
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
            logger.error("Failed refreshing Google token (HTTP %s): %s", http_err.code, raw_body[:300])
            code, friendly = sanitize_oauth_error_for_user(f"HTTP {http_err.code} {raw_body}", "google")
            self._status["is_healthy"] = False
            self._status["error_state"] = code if http_err.code in (400, 401) else "REFRESH_FAILED"
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False
        except Exception as exc:
            logger.warning("Network or transient exception refreshing Google token: %s", exc)
            code, friendly = sanitize_oauth_error_for_user(str(exc), "google")
            is_offline = (code == "NETWORK_OFFLINE" or "getaddrinfo" in str(exc).lower() or "timed out" in str(exc).lower() or "11001" in str(exc))
            if is_offline:
                self._status["is_healthy"] = True
                self._status["error_state"] = "NETWORK_OFFLINE"
                self._status["friendly_error"] = "Offline — using cached schedule. Will automatically sync when reconnected."
                if not self._status.get("connection_state") or self._status.get("connection_state") == AccountConnectionState.DISCONNECTED.value:
                    self._status["connection_state"] = AccountConnectionState.CONNECTED.value
            else:
                self._status["is_healthy"] = False
                self._status["error_state"] = "REFRESH_FAILED"
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False

    def build_authorization_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        """Build Google OAuth 2.0 authorization URL with PKCE."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent select_account",
        }
        return f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    def authenticate_interactive(
        self,
        callback_port: int = 8080,
        timeout_sec: int = 180,
        cancel_event: Optional[threading.Event] = None,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
    ) -> Tuple[bool, str]:
        """
        Interactive Google OAuth with loopback server on http://localhost:{port}/google_callback.
        """
        use_backend = self._auth_broker is not None and self._auth_broker.is_backend_configured()
        if not self.client_id and not use_backend:
            msg = "Google Client ID is required. Enter it in VOX Calendar Settings or configure a hosted sign-in server."
            self._status["error_state"] = "OAUTH_CONFIG_ERROR"
            self._status["friendly_error"] = msg
            return False, msg

        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        expected_state = secrets.token_urlsafe(24)

        result_box: Dict[str, Any] = {"code": None, "state": None, "error": None, "done": threading.Event()}

        class _GoogleCallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                code_list = params.get("code")
                state_list = params.get("state")
                err_list = params.get("error")

                if err_list:
                    _code, friendly_msg = sanitize_oauth_error_for_user(err_list[0], "google")
                    result_box["error"] = friendly_msg
                    title = "Google Sign-In Unsuccessful"
                    body = friendly_msg
                elif not code_list or not state_list:
                    result_box["error"] = "Missing authorization code"
                    title = "Error"
                    body = "Authorization response was incomplete."
                elif state_list[0] != expected_state:
                    result_box["error"] = "State verification failed"
                    title = "Security Alert"
                    body = "State verification failed."
                else:
                    result_box["code"] = code_list[0]
                    result_box["state"] = state_list[0]
                    title = "Google Calendar Connected!"
                    body = "Your Google Calendar is now connected to VOX. You can close this tab."

                page = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>{title}</title>"
                    "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#161513;color:#F5F2EC;"
                    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
                    ".card{background:#201E1B;border:1px solid #353028;border-radius:14px;padding:28px 34px;max-width:460px}"
                    "h2{margin:0 0 10px;font-weight:600}p{color:#B8B2A6;line-height:1.5;margin:0}</style></head>"
                    f"<body><div class='card'><h2>{title}</h2><p>{body}</p></div>"
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
            try:
                server = HTTPServer(("127.0.0.1", int(callback_port)), _GoogleCallbackHandler)
            except OSError:
                server = HTTPServer(("127.0.0.1", 0), _GoogleCallbackHandler)

            server.timeout = 0.5
            actual_port = int(server.server_address[1])
            redirect_uri = f"http://localhost:{actual_port}/google_callback"
            if use_backend:
                start_resp = self._auth_broker.start_backend_oauth(
                    provider=CalendarProviderType.GOOGLE.value,
                    user_id=self.user_id,
                    scopes=self.SCOPES,
                    redirect_uri=redirect_uri,
                    code_challenge=code_challenge,
                )
                auth_url = str(start_resp.get("authorization_url") or "")
                if start_resp.get("state"):
                    expected_state = str(start_resp["state"])
            else:
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

            srv_thread = threading.Thread(target=_serve_loop, daemon=True, name="vox-google-oauth")
            srv_thread.start()

            opener = auth_url_opener or webbrowser.open
            opener(auth_url)

            deadline = time.monotonic() + float(timeout_sec)
            while not result_box["done"].is_set() and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Google sign-in was cancelled."
                time.sleep(0.2)

            if not result_box["done"].is_set():
                return False, f"Sign-in timed out after {timeout_sec} seconds."

            if result_box["error"]:
                return False, str(result_box["error"])

            code = result_box["code"]
            return self._exchange_code_for_tokens(code, redirect_uri, code_verifier)

        except Exception as exc:
            _code, friendly = sanitize_oauth_error_for_user(str(exc), "google")
            return False, friendly
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str, code_verifier: str) -> Tuple[bool, str]:
        if self._auth_broker is not None and self._auth_broker.is_backend_configured():
            try:
                token_resp = self._auth_broker.exchange_backend_code(
                    provider=CalendarProviderType.GOOGLE.value,
                    user_id=self.user_id,
                    code=code,
                    state="",
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                )
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
                self._expires_at = time.time() + int(token_resp.get("expires_in", 3600) or 3600)
                self._fetch_user_email()
                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                self._status["friendly_error"] = None
                self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                return True, f"Connected Google Calendar as {self._account_email or 'User'}"
            except Exception as exc:
                err_code, friendly = sanitize_oauth_error_for_user(str(exc), "google")
                self._status["is_healthy"] = False
                self._status["error_state"] = err_code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.FAILED.value
                return False, friendly

        data_dict = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            data_dict["client_secret"] = self.client_secret

        data = urllib.parse.urlencode(data_dict).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token", self._refresh_token)
                expires_in = token_resp.get("expires_in", 3600)
                self._expires_at = time.time() + expires_in

                email = self._fetch_user_email()
                if email:
                    self._account_email = email
                    self._account_id = make_account_id(CalendarProviderType.GOOGLE.value, email)

                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                self._status["error_detail"] = None
                self._status["friendly_error"] = None
                self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                return True, f"Connected Google Calendar as {self._account_email or 'User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            err_code, friendly = sanitize_oauth_error_for_user(f"HTTP {http_err.code} {body}", "google")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code
            self._status["error_detail"] = f"HTTP {http_err.code}"
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = AccountConnectionState.FAILED.value
            return False, friendly
        except Exception as exc:
            err_code, friendly = sanitize_oauth_error_for_user(str(exc), "google")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code
            self._status["friendly_error"] = friendly
            return False, friendly

    def _fetch_user_email(self) -> Optional[str]:
        if not self._access_token:
            return None
        try:
            req = urllib.request.Request(self.USERINFO_ENDPOINT, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                email = str(data.get("email") or "").strip() or None
                if data.get("name"):
                    self._display_name = str(data["name"]).strip()
                if data.get("hd"):
                    self._hosted_domain = str(data["hd"]).strip()
                if email:
                    self._account_email = email
                    self._account_kind = classify_account_kind(
                        "google",
                        email=email,
                        hosted_domain=self._hosted_domain,
                    )
                return email
        except Exception as exc:
            logger.debug("Failed fetching Google user email: %s", exc)
            return None

    def authenticate(
        self,
        interactive: bool = True,
        cancel_event: Optional[threading.Event] = None,
        timeout_sec: int = 180,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
    ) -> bool:
        if self.is_authenticated():
            return True
        if not interactive:
            return False
        ok, _msg = self.authenticate_interactive(
            timeout_sec=timeout_sec,
            cancel_event=cancel_event,
            auth_url_opener=auth_url_opener,
        )
        return ok

    def disconnect(self) -> bool:
        if self._auth_broker is not None and self._account_id:
            if self._auth_broker.is_backend_configured():
                self._auth_broker.revoke_backend_account(
                    CalendarProviderType.GOOGLE.value,
                    self.user_id,
                    self._account_id,
                )
            self._auth_broker.delete_credentials(self.user_id, self._account_id)

        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._account_email = None
        self._display_name = ""
        self._hosted_domain = ""
        self._sync_tokens.clear()
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception as exc:
            logger.error("Failed removing Google auth cache: %s", exc)
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.DISCONNECTED.value
        self._status["account_email"] = None
        self._status["display_name"] = ""
        return True

    def set_selected_calendars(self, calendar_ids: List[str]) -> None:
        clean = [str(cid).strip() for cid in (calendar_ids or []) if str(cid).strip()]
        if clean != self._selected_calendars:
            self._sync_tokens.clear()
        self._selected_calendars = clean
        self._status["selected_calendars"] = list(clean)
        if self._access_token or self._refresh_token:
            self._save_credentials()

    def get_selected_calendars(self) -> List[str]:
        return list(self._selected_calendars)

    @staticmethod
    def _with_query_param(url: str, key: str, value: str) -> str:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        qs[key] = [value]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    def _http_get_json_with_recovery(self, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        for attempt in range(2):
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
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
                    return None, 410, "SYNC_TOKEN_EXPIRED"
                raw_body = ""
                try:
                    raw_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                return None, http_err.code, raw_body or f"HTTP_{http_err.code}"
            except Exception as exc:
                logger.error("Failed querying Google Calendar endpoint: %s", exc)
                return None, None, "NETWORK_OFFLINE"
        return None, 401, "AUTH_REQUIRED"

    def list_calendars(self) -> List[Dict[str, Any]]:
        """List accessible Google calendars via /users/me/calendarList."""
        if not self.is_authenticated():
            return list(self._available_calendars)
        url = f"{self.CALENDAR_API_BASE}/users/me/calendarList?maxResults=100"
        data, code, _err = self._http_get_json_with_recovery(url)
        if code == 200 and isinstance(data, dict):
            cals: List[Dict[str, Any]] = []
            for item in data.get("items", []):
                cid = str(item.get("id") or "")
                if not cid:
                    continue
                cals.append({
                    "id": cid,
                    "name": str(item.get("summaryOverride") or item.get("summary") or cid).strip(),
                    "primary": bool(item.get("primary", False)),
                    "access_role": str(item.get("accessRole") or ""),
                })
            self._available_calendars = cals
            self._status["available_calendars"] = list(cals)
            self._save_credentials()
            return cals
        return list(self._available_calendars)

    def _parse_google_item(
        self,
        item: Dict[str, Any],
        calendar_id: str = "primary",
        calendar_name: str = "",
    ) -> Tuple[Optional[CalendarMeeting], bool, str]:
        m_id = str(item.get("id") or "")
        if item.get("status") == "cancelled":
            return None, True, m_id

        title = str(item.get("summary") or "Untitled Meeting").strip()
        st_obj = item.get("start") or {}
        et_obj = item.get("end") or {}
        st_str = st_obj.get("dateTime") or st_obj.get("date")
        et_str = et_obj.get("dateTime") or et_obj.get("date")
        if not st_str or not et_str:
            return None, False, m_id

        try:
            start_time = datetime.fromisoformat(str(st_str).replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(str(et_str).replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
        except Exception:
            return None, False, m_id

        location = str(item.get("location") or "").strip()
        description = str(item.get("description") or "").strip()

        organizer_obj = item.get("organizer") or {}
        org_name = str(organizer_obj.get("displayName") or "")
        org_email = str(organizer_obj.get("email") or "")
        eff_org = (
            f"{org_name} ({org_email})"
            if (org_name and org_email and org_name != org_email)
            else (org_name or org_email)
        )

        attendees: List[str] = []
        for att in item.get("attendees", []):
            a_name = str(att.get("displayName") or "").strip()
            a_email = str(att.get("email") or "").strip()
            if a_name:
                attendees.append(f"{a_name} ({a_email})" if (a_email and a_name != a_email) else a_name)
            elif a_email:
                attendees.append(a_email)

        join_url: Optional[str] = None
        platform = "generic"
        conf_id = ""
        conf_data = item.get("conferenceData") or {}
        if conf_data.get("conferenceId"):
            conf_id = str(conf_data["conferenceId"])
        for ep in conf_data.get("entryPoints", []):
            if ep.get("entryPointType") == "video" and ep.get("uri"):
                join_url = str(ep["uri"])
                _, detected = extract_meeting_links(join_url)
                platform = detected if detected != "generic" else "meet"
                break

        if not join_url and item.get("hangoutLink"):
            join_url = str(item["hangoutLink"])
            platform = "meet"

        if not join_url:
            found_url, found_plat = extract_meeting_links(f"{location}\n{description}")
            if found_url:
                join_url = found_url
                platform = found_plat

        rec_id = str(item.get("recurringEventId") or "")
        is_mod = bool(item.get("originalStartTime") and rec_id)
        meeting = CalendarMeeting(
            id=m_id,
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location or ("Google Meet" if platform == "meet" else ""),
            join_url=join_url,
            attendees=attendees,
            organizer=eff_org,
            is_online=bool(join_url or "meet" in location.lower() or "zoom" in location.lower()),
            is_cancelled=False,
            platform=platform,
            provider_type=CalendarProviderType.GOOGLE.value,
            provider_account=self._account_email or "",
            is_recurring=bool(rec_id),
            description=re.sub(r"<[^>]+>", " ", description)[:500].strip(),
            account_id=self._account_id,
            calendar_id=calendar_id,
            calendar_name=calendar_name,
            ical_uid=str(item.get("iCalUID") or ""),
            series_master_id=rec_id,
            is_modified_occurrence=is_mod,
            timezone_name=str(st_obj.get("timeZone") or ""),
            conference_id=conf_id,
        )
        return meeting, False, m_id

    def sync_events_incremental(self, window_minutes: int = 43200, use_sync_token: bool = True) -> Dict[str, Any]:
        """
        Full or incremental Google Calendar sync via `syncToken` with automatic `410 Gone` fallback.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            return {"meetings": [], "removed_ids": [], "is_incremental": False, "ok": False, "error_code": "AUTH_REQUIRED"}

        if self.is_token_near_expiry() and not self.refresh_token():
            if self._status.get("error_state") == "NETWORK_OFFLINE":
                return {"meetings": [], "removed_ids": [], "is_incremental": False, "ok": True, "error_code": "NETWORK_OFFLINE"}
            return {"meetings": [], "removed_ids": [], "is_incremental": False, "ok": False, "error_code": "AUTH_EXPIRED"}

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        start_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        cal_name_map = {c.get("id", ""): c.get("name", "") for c in self._available_calendars}
        cal_targets = list(self._selected_calendars) if self._selected_calendars else ["primary"]

        all_meetings: List[CalendarMeeting] = []
        removed_ids: List[str] = []
        used_incremental = False
        had_error: Optional[str] = None

        for cal_id in cal_targets:
            encoded_cal = urllib.parse.quote(cal_id, safe="")
            sync_tok = self._sync_tokens.get(cal_id) if use_sync_token else None

            def _build_initial_url() -> str:
                return (
                    f"{self.CALENDAR_API_BASE}/calendars/{encoded_cal}/events"
                    f"?singleEvents=true"
                    f"&showDeleted=true"
                    f"&timeMin={urllib.parse.quote(start_iso)}"
                    f"&timeMax={urllib.parse.quote(end_iso)}"
                    f"&maxResults=250"
                )

            if sync_tok:
                used_incremental = True
                base_url = (
                    f"{self.CALENDAR_API_BASE}/calendars/{encoded_cal}/events"
                    f"?singleEvents=true"
                    f"&showDeleted=true"
                    f"&syncToken={urllib.parse.quote(sync_tok)}"
                    f"&maxResults=250"
                )
            else:
                base_url = _build_initial_url()

            url: Optional[str] = base_url
            retried_410 = False
            max_pages = 20
            cal_name = cal_name_map.get(cal_id, "")

            while url and max_pages > 0:
                max_pages -= 1
                data, code, err_code = self._http_get_json_with_recovery(url)
                if code == 410 and not retried_410:
                    retried_410 = True
                    used_incremental = False
                    self._sync_tokens.pop(cal_id, None)
                    base_url = _build_initial_url()
                    url = base_url
                    continue
                if data is None:
                    had_error = err_code or f"HTTP_{code}"
                    break

                for item in data.get("items", []):
                    mtg, is_rem, ev_id = self._parse_google_item(item, calendar_id=cal_id, calendar_name=cal_name)
                    if is_rem and ev_id:
                        removed_ids.append(ev_id)
                    elif mtg is not None:
                        all_meetings.append(mtg)

                if data.get("nextSyncToken"):
                    self._sync_tokens[cal_id] = str(data["nextSyncToken"])

                next_page = data.get("nextPageToken")
                url = self._with_query_param(base_url, "pageToken", str(next_page)) if next_page else None

            if had_error:
                break

        if had_error:
            err_code, friendly = sanitize_oauth_error_for_user(had_error, "google")
            is_offline = (had_error == "NETWORK_OFFLINE" or err_code == "NETWORK_OFFLINE")
            self._status["is_healthy"] = True if is_offline else False
            self._status["error_state"] = "NETWORK_OFFLINE" if is_offline else (had_error if had_error in ("AUTH_REQUIRED", "AUTH_EXPIRED", "HTTP_429_RATE_LIMIT") else err_code)
            self._status["friendly_error"] = "Working offline — showing cached schedule. Will reconnect automatically." if is_offline else friendly
            self._status["connection_state"] = (
                AccountConnectionState.CONNECTED.value
                if is_offline
                else (
                    AccountConnectionState.EXPIRED.value
                    if self._status["error_state"] in ("AUTH_REQUIRED", "AUTH_EXPIRED")
                    else AccountConnectionState.FAILED.value
                )
            )
            return {
                "meetings": all_meetings,
                "removed_ids": removed_ids,
                "is_incremental": used_incremental,
                "ok": True if is_offline else False,
                "error_code": self._status["error_state"],
            }

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.CONNECTED.value
        self._status["last_sync_at"] = now_iso
        if self._sync_tokens:
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
        Fetch scheduled Google Calendar meetings (default 30-day window).
        singleEvents=true expands recurring events automatically into distinct occurrences.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            return []

        if self.is_token_near_expiry():
            if not self.refresh_token() and self._status.get("error_state") != "NETWORK_OFFLINE":
                return []

        now_dt = datetime.now(timezone.utc)
        end_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        start_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        cal_name_map = {c.get("id", ""): c.get("name", "") for c in self._available_calendars}
        cal_targets = list(self._selected_calendars) if self._selected_calendars else ["primary"]

        all_meetings: List[CalendarMeeting] = []
        had_error: Optional[str] = None

        for cal_id in cal_targets:
            encoded_cal = urllib.parse.quote(cal_id, safe="")
            base_url = (
                f"{self.CALENDAR_API_BASE}/calendars/{encoded_cal}/events"
                f"?singleEvents=true"
                f"&orderBy=startTime"
                f"&timeMin={urllib.parse.quote(start_iso)}"
                f"&timeMax={urllib.parse.quote(end_iso)}"
                f"&maxResults=250"
            )
            url: Optional[str] = base_url
            max_pages = 10
            cal_name = cal_name_map.get(cal_id, "")

            while url and max_pages > 0:
                max_pages -= 1
                data, code, err_code = self._http_get_json_with_recovery(url)
                if data is None:
                    had_error = err_code or f"HTTP_{code}"
                    break
                for item in data.get("items", []):
                    mtg, _is_rem, _ev_id = self._parse_google_item(item, calendar_id=cal_id, calendar_name=cal_name)
                    if mtg is not None:
                        all_meetings.append(mtg)
                next_page = data.get("nextPageToken")
                url = self._with_query_param(base_url, "pageToken", str(next_page)) if next_page else None

            if had_error:
                break

        if had_error:
            err_code, friendly = sanitize_oauth_error_for_user(had_error, "google")
            is_offline = (had_error == "NETWORK_OFFLINE" or err_code == "NETWORK_OFFLINE")
            self._status["is_healthy"] = True if is_offline else False
            self._status["error_state"] = "NETWORK_OFFLINE" if is_offline else (had_error if had_error in ("AUTH_REQUIRED", "AUTH_EXPIRED", "HTTP_429_RATE_LIMIT") else err_code)
            self._status["friendly_error"] = "Working offline — showing cached schedule. Will reconnect automatically." if is_offline else friendly
            self._status["connection_state"] = (
                AccountConnectionState.CONNECTED.value
                if is_offline
                else (
                    AccountConnectionState.EXPIRED.value
                    if self._status["error_state"] in ("AUTH_REQUIRED", "AUTH_EXPIRED")
                    else AccountConnectionState.FAILED.value
                )
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
        return dict(self._status)

"""
VoxLocal Zoom Meetings Provider.
Production-grade Zoom Meetings API integration supporting:
- Zoom User OAuth 2.0 + PKCE (RFC 7636) with local HTTP callback listener or hosted backend broker
- Retrieving upcoming and scheduled user meetings (/users/me/meetings) with pagination
- Expanding Zoom recurring meeting occurrences (`type == 8` with `occurrences`)
- Account classification (`personal` Basic vs `work` Licensed/Enterprise via `/users/me`)
- DPAPI / SQLite vault encrypted token storage and reactive 401 refresh + offline recovery
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
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

logger = get_logger("calendar.zoom")


class ZoomCalendarProvider(BaseCalendarProvider):
    """
    Zoom Meetings API provider conforming to BaseCalendarProvider.
    Fetches upcoming and scheduled meetings for the authorized Zoom user.
    """

    AUTH_ENDPOINT = "https://zoom.us/oauth/authorize"
    TOKEN_ENDPOINT = "https://zoom.us/oauth/token"
    ZOOM_API_BASE = "https://api.zoom.us/v2"

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
        self.client_id = (client_id or os.environ.get("ZOOM_CLIENT_ID", "")).strip()
        vault_secret = ""
        if not client_secret and self._auth_broker is not None:
            vault_secret = self._auth_broker.load_app_secret("zoom_client_secret", user_id=self.user_id)
        self.client_secret = (
            client_secret or os.environ.get("ZOOM_CLIENT_SECRET", "") or vault_secret
        ).strip()
        self._explicit_auth_file_path = auth_file_path is not None
        self.auth_file_path = auth_file_path or (Path("data") / "zoom_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_email: Optional[str] = None
        self._display_name: str = ""
        self._zoom_account_type: Optional[int] = None
        self._account_kind: str = "personal"
        self._account_id: str = (account_id or "").strip() or make_account_id(CalendarProviderType.ZOOM.value, "")
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.ZOOM.value,
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
        }
        self.retry_after_seconds: int = 0

        self._load_cached_credentials()

    def _sync_status_identity(self) -> None:
        if self._account_email:
            if not self._account_id or self._account_id.endswith(":default"):
                self._account_id = make_account_id(CalendarProviderType.ZOOM.value, self._account_email)
            self._account_kind = classify_account_kind(
                "zoom",
                email=self._account_email,
                zoom_account_type=self._zoom_account_type,
            )
        self._status["account_id"] = self._account_id
        self._status["account_email"] = self._account_email
        self._status["display_name"] = self._display_name
        self._status["account_kind"] = self._account_kind

    def _apply_token_data(self, data: Dict[str, Any]) -> None:
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")
        self._expires_at = data.get("expires_at")
        self._account_email = data.get("account_email") or self._account_email
        self._display_name = str(data.get("display_name") or self._display_name or "")
        if data.get("zoom_account_type") is not None:
            try:
                self._zoom_account_type = int(data["zoom_account_type"])
            except Exception:
                pass
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
            logger.error("Failed loading cached Zoom credentials: %s", exc)
            return False

    def _save_credentials(self) -> None:
        self._sync_status_identity()
        token_data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "account_email": self._account_email,
            "display_name": self._display_name,
            "zoom_account_type": self._zoom_account_type,
            "account_kind": self._account_kind,
            "client_id": self.client_id,
        }
        if self._auth_broker is not None and self._account_id:
            try:
                self._auth_broker.save_credentials(
                    self.user_id,
                    self._account_id,
                    CalendarProviderType.ZOOM.value,
                    token_data,
                )
            except Exception as exc:
                logger.warning("Failed storing Zoom credentials in DB vault: %s", exc)
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
                logger.error("Failed saving Zoom DPAPI credentials: %s", exc)

    def is_token_near_expiry(self, threshold_seconds: int = 300) -> bool:
        if not self._expires_at:
            return True
        return (self._expires_at - time.time()) < threshold_seconds

    def is_authenticated(self) -> bool:
        if not self._access_token:
            if self._refresh_token:
                return self.refresh_token()
            return False
        if not self.is_token_near_expiry():
            return True
        if self._refresh_token:
            return self.refresh_token()
        return False

    def _get_basic_auth_header(self) -> str:
        creds = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return f"Basic {base64.b64encode(creds).decode('ascii')}"

    def _use_hosted_backend(self) -> bool:
        if self.client_id:
            return False
        if self._auth_broker is None:
            return False
        if hasattr(self._auth_broker, "is_backend_configured_for"):
            return bool(self._auth_broker.is_backend_configured_for("zoom"))
        return bool(self._auth_broker.is_backend_configured())

    def refresh_token(self) -> bool:
        """Refresh Zoom OAuth access token via hosted backend or direct/PKCE refresh_token."""
        if self._use_hosted_backend() or (not self.client_id and self._auth_broker is not None and self._auth_broker.is_backend_configured()):
            try:
                token_resp = self._auth_broker.refresh_backend_token(
                    CalendarProviderType.ZOOM.value,
                    self.user_id,
                    self._account_id,
                    refresh_token=self._refresh_token or "",
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
                code, friendly = sanitize_oauth_error_for_user(str(exc), "zoom")
                self._status["is_healthy"] = False
                self._status["error_state"] = code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.EXPIRED.value
                return False

        if not self._refresh_token or not self.client_id:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
            self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False

        form = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        if not self.client_secret:
            form["client_id"] = self.client_id
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
        if self.client_secret:
            req.add_header("Authorization", self._get_basic_auth_header())
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
            logger.error("Failed refreshing Zoom token (HTTP %s): %s", http_err.code, raw_body[:300])
            code, friendly = sanitize_oauth_error_for_user(f"HTTP {http_err.code} {raw_body}", "zoom")
            self._status["is_healthy"] = False
            self._status["error_state"] = code if http_err.code in (400, 401) else "REFRESH_FAILED"
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = AccountConnectionState.EXPIRED.value
            return False
        except Exception as exc:
            logger.error("Failed refreshing Zoom token: %s", exc)
            code, friendly = sanitize_oauth_error_for_user(str(exc), "zoom")
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_OFFLINE" if code == "NETWORK_OFFLINE" else "REFRESH_FAILED"
            self._status["friendly_error"] = friendly
            return False

    def build_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: str = "",
    ) -> str:
        """Build Zoom OAuth authorization URL with optional PKCE S256."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    def authenticate_interactive(
        self,
        callback_port: int = 8080,
        timeout_sec: int = 180,
        cancel_event: Optional[threading.Event] = None,
        auth_url_opener: Optional[Callable[[str], bool]] = None,
    ) -> Tuple[bool, str]:
        """
        Interactive Zoom OAuth callback on http://localhost:{port}/zoom_callback with PKCE S256.
        """
        use_backend = self._use_hosted_backend()
        if not self.client_id and not use_backend:
            msg = "Zoom Client ID is required (leave Client Secret blank for PKCE public client, or configure hosted sign-in)."
            self._status["error_state"] = "OAUTH_CONFIG_ERROR"
            self._status["friendly_error"] = msg
            return False, msg

        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        expected_state = secrets.token_urlsafe(24)
        result_box: Dict[str, Any] = {"code": None, "state": None, "error": None, "done": threading.Event()}

        class _ZoomCallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                code_list = params.get("code")
                state_list = params.get("state")
                err_list = params.get("error")

                if err_list:
                    _code, friendly_msg = sanitize_oauth_error_for_user(err_list[0], "zoom")
                    result_box["error"] = friendly_msg
                    title = "Zoom Authorization Unsuccessful"
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
                    title = "Zoom Connected to VOX!"
                    body = "Your Zoom meetings are now synchronized with VOX. You may close this tab."

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

        class _ExclusiveHTTPServer(HTTPServer):
            allow_reuse_address = False

        server: Optional[HTTPServer] = None
        try:
            try:
                server = _ExclusiveHTTPServer(("127.0.0.1", int(callback_port)), _ZoomCallbackHandler)
            except OSError:
                server = _ExclusiveHTTPServer(("127.0.0.1", 0), _ZoomCallbackHandler)

            server.timeout = 0.5
            actual_port = int(server.server_address[1])
            redirect_uri = f"http://localhost:{actual_port}/zoom_callback"
            if use_backend:
                try:
                    start_resp = self._auth_broker.start_backend_oauth(
                        provider=CalendarProviderType.ZOOM.value,
                        user_id=self.user_id,
                        scopes=[],
                        redirect_uri=redirect_uri,
                        code_challenge=code_challenge,
                    )
                except urllib.error.HTTPError as http_err:
                    raw_b = http_err.read().decode("utf-8", errors="replace")
                    if http_err.code == 503 or "ZOOM_HOSTED_OAUTH_NOT_CONFIGURED" in raw_b:
                        friendly = (
                            "Hosted 1-click Zoom OAuth is not configured on the VOX server yet. "
                            "Enter your Zoom App's Client ID in the Zoom tab (Client Secret optional for PKCE) "
                            "with Redirect URL http://localhost:8080/zoom_callback."
                        )
                        self._status["error_state"] = "ZOOM_HOSTED_OAUTH_NOT_CONFIGURED"
                        self._status["friendly_error"] = friendly
                        return False, friendly
                    raise
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

            srv_thread = threading.Thread(target=_serve_loop, daemon=True, name="vox-zoom-oauth")
            srv_thread.start()

            opener = auth_url_opener or webbrowser.open
            opener(auth_url)

            deadline = time.monotonic() + float(timeout_sec)
            while not result_box["done"].is_set() and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Zoom sign-in cancelled."
                time.sleep(0.2)

            if not result_box["done"].is_set():
                return False, f"Sign-in timed out after {timeout_sec} seconds."

            if result_box["error"]:
                return False, str(result_box["error"])

            code = result_box["code"]
            return self._exchange_code_for_tokens(code, redirect_uri, code_verifier)

        except Exception as exc:
            _code, friendly = sanitize_oauth_error_for_user(str(exc), "zoom")
            return False, friendly
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str, code_verifier: str = "") -> Tuple[bool, str]:
        if self._use_hosted_backend():
            try:
                token_resp = self._auth_broker.exchange_backend_code(
                    provider=CalendarProviderType.ZOOM.value,
                    user_id=self.user_id,
                    code=code,
                    state="",
                    code_verifier=code_verifier,
                    redirect_uri=redirect_uri,
                )
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token")
                self._expires_at = time.time() + int(token_resp.get("expires_in", 3600) or 3600)
                self._fetch_user_profile()
                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                self._status["friendly_error"] = None
                self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                return True, f"Connected Zoom as {self._account_email or 'Zoom User'}"
            except Exception as exc:
                err_code, friendly = sanitize_oauth_error_for_user(str(exc), "zoom")
                self._status["is_healthy"] = False
                self._status["error_state"] = err_code
                self._status["friendly_error"] = friendly
                self._status["connection_state"] = AccountConnectionState.FAILED.value
                return False, friendly

        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        if not self.client_secret:
            form["client_id"] = self.client_id
        data = urllib.parse.urlencode(form).encode("utf-8")

        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
        if self.client_secret:
            req.add_header("Authorization", self._get_basic_auth_header())
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                token_resp = json.loads(resp.read().decode("utf-8"))
                self._access_token = token_resp["access_token"]
                self._refresh_token = token_resp.get("refresh_token")
                expires_in = token_resp.get("expires_in", 3600)
                self._expires_at = time.time() + expires_in

                email = self._fetch_user_profile()
                if email:
                    self._account_email = email
                    self._account_id = make_account_id(CalendarProviderType.ZOOM.value, email)

                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                self._status["error_detail"] = None
                self._status["friendly_error"] = None
                self._status["connection_state"] = AccountConnectionState.CONNECTED.value
                return True, f"Connected Zoom as {self._account_email or 'Zoom User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            err_code, friendly = sanitize_oauth_error_for_user(f"HTTP {http_err.code} {body}", "zoom")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code
            self._status["error_detail"] = f"HTTP {http_err.code}"
            self._status["friendly_error"] = friendly
            self._status["connection_state"] = AccountConnectionState.FAILED.value
            return False, friendly
        except Exception as exc:
            err_code, friendly = sanitize_oauth_error_for_user(str(exc), "zoom")
            self._status["is_healthy"] = False
            self._status["error_state"] = err_code
            self._status["friendly_error"] = friendly
            return False, friendly

    def _fetch_user_profile(self) -> Optional[str]:
        if not self._access_token:
            return None
        try:
            req = urllib.request.Request(f"{self.ZOOM_API_BASE}/users/me", method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                full_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
                if full_name:
                    self._display_name = full_name
                if data.get("type") is not None:
                    try:
                        self._zoom_account_type = int(data["type"])
                    except Exception:
                        pass
                email = str(data.get("email") or full_name or "").strip() or None
                if email:
                    self._account_email = email
                    self._account_kind = classify_account_kind(
                        "zoom",
                        email=email,
                        zoom_account_type=self._zoom_account_type,
                    )
                return email
        except Exception as exc:
            logger.debug("Failed fetching Zoom user profile: %s", exc)
            return None

    def authenticate(
        self,
        interactive: bool = True,
        timeout_sec: int = 180,
        cancel_event: Optional[threading.Event] = None,
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
                    CalendarProviderType.ZOOM.value,
                    self.user_id,
                    self._account_id,
                )
            self._auth_broker.delete_credentials(self.user_id, self._account_id)

        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._account_email = None
        self._display_name = ""
        self._zoom_account_type = None
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception as exc:
            logger.error("Failed removing Zoom auth file: %s", exc)
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["error_detail"] = None
        self._status["friendly_error"] = None
        self._status["connection_state"] = AccountConnectionState.DISCONNECTED.value
        self._status["account_email"] = None
        self._status["display_name"] = ""
        return True

    def _http_get_json_with_recovery(self, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[str]]:
        for attempt in range(2):
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
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
                return None, http_err.code, f"HTTP_{http_err.code}"
            except Exception as exc:
                logger.error("Failed querying Zoom meetings: %s", exc)
                return None, None, "NETWORK_OFFLINE"
        return None, 401, "AUTH_REQUIRED"

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        """Fetch scheduled and upcoming Zoom meetings (including fixed-time recurring occurrences)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self._status["last_poll_time"] = now_iso
        if not self.is_authenticated():
            return []

        if self.is_token_near_expiry():
            if not self.refresh_token():
                return []

        all_meetings: List[CalendarMeeting] = []
        seen_ids: set[str] = set()
        now_dt = datetime.now(timezone.utc)
        horizon_dt = now_dt + timedelta(minutes=max(15, window_minutes))
        had_error: Optional[str] = None

        for m_type in ("upcoming", "scheduled"):
            url: Optional[str] = f"{self.ZOOM_API_BASE}/users/me/meetings?type={m_type}&page_size=100"
            max_pages = 10
            while url and max_pages > 0:
                max_pages -= 1
                data, code, err_code = self._http_get_json_with_recovery(url)
                if data is None:
                    had_error = err_code or f"HTTP_{code}"
                    break

                for item in data.get("meetings", []):
                    base_id = str(item.get("id") or "")
                    if not base_id:
                        continue
                    title = str(item.get("topic") or "Zoom Meeting").strip()
                    join_url = str(item.get("join_url") or "").strip()
                    agenda = str(item.get("agenda") or "").strip()
                    mtg_type = int(item.get("type") or 2)
                    is_recurring = mtg_type in (3, 8)
                    tz_name = str(item.get("timezone") or "")

                    # Expand recurring fixed-time occurrences when Zoom provides them
                    occurrences = item.get("occurrences")
                    if isinstance(occurrences, list) and occurrences:
                        for occ in occurrences:
                            if occ.get("status") == "deleted":
                                continue
                            occ_id = str(occ.get("occurrence_id") or "")
                            full_id = f"{base_id}_{occ_id}" if occ_id else base_id
                            if full_id in seen_ids:
                                continue
                            st_str = str(occ.get("start_time") or "")
                            duration = int(occ.get("duration") or item.get("duration") or 30)
                            if not st_str:
                                continue
                            try:
                                start_time = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                                if start_time.tzinfo is None:
                                    start_time = start_time.replace(tzinfo=timezone.utc)
                                end_time = start_time + timedelta(minutes=duration)
                            except Exception:
                                continue
                            if start_time > horizon_dt or end_time < now_dt:
                                continue
                            seen_ids.add(full_id)
                            all_meetings.append(
                                CalendarMeeting(
                                    id=full_id,
                                    title=title,
                                    start_time=start_time,
                                    end_time=end_time,
                                    location="Zoom Meeting",
                                    join_url=join_url,
                                    attendees=[],
                                    organizer=self._account_email or "Zoom User",
                                    is_online=True,
                                    is_cancelled=False,
                                    platform="zoom",
                                    is_recurring=True,
                                    provider_type=CalendarProviderType.ZOOM.value,
                                    provider_account=self._account_email or "",
                                    description=agenda,
                                    account_id=self._account_id,
                                    series_master_id=base_id,
                                    timezone_name=tz_name,
                                    conference_id=base_id,
                                )
                            )
                        continue

                    if base_id in seen_ids:
                        continue
                    st_str = str(item.get("start_time") or "")
                    duration = int(item.get("duration") or 30)
                    if not st_str:
                        continue
                    try:
                        start_time = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                        if start_time.tzinfo is None:
                            start_time = start_time.replace(tzinfo=timezone.utc)
                        end_time = start_time + timedelta(minutes=duration)
                    except Exception:
                        continue
                    if start_time > horizon_dt or end_time < now_dt:
                        continue

                    seen_ids.add(base_id)
                    all_meetings.append(
                        CalendarMeeting(
                            id=base_id,
                            title=title,
                            start_time=start_time,
                            end_time=end_time,
                            location="Zoom Meeting",
                            join_url=join_url,
                            attendees=[],
                            organizer=self._account_email or "Zoom User",
                            is_online=True,
                            is_cancelled=False,
                            platform="zoom",
                            is_recurring=is_recurring,
                            provider_type=CalendarProviderType.ZOOM.value,
                            provider_account=self._account_email or "",
                            description=agenda,
                            account_id=self._account_id,
                            timezone_name=tz_name,
                            conference_id=base_id,
                        )
                    )

                next_page_token = data.get("next_page_token")
                if next_page_token:
                    url = f"{self.ZOOM_API_BASE}/users/me/meetings?type={m_type}&page_size=100&next_page_token={urllib.parse.quote(next_page_token)}"
                else:
                    url = None

            if had_error:
                break

        if had_error:
            err_code, friendly = sanitize_oauth_error_for_user(had_error, "zoom")
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
        return dict(self._status)

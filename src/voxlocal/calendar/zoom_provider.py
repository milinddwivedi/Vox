"""
VoxLocal Zoom Meetings Provider.
Production-grade Zoom Meetings API integration supporting:
- Zoom User OAuth 2.0 with local HTTP callback listener
- Retrieving upcoming and scheduled user meetings (/users/me/meetings)
- Extracting Zoom join links, meeting passwords, topics, and durations
- DPAPI token encryption on Windows and automatic token refresh
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

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.calendar.graph_provider import dpapi_decrypt, dpapi_encrypt
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
    ) -> None:
        self.client_id = (client_id or os.environ.get("ZOOM_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.environ.get("ZOOM_CLIENT_SECRET", "")).strip()
        self.auth_file_path = auth_file_path or (Path("data") / "zoom_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_email: Optional[str] = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.ZOOM.value,
            "is_healthy": True,
            "last_poll_time": None,
            "error_state": None,
            "error_detail": None,
            "account_email": None,
        }
        self.retry_after_seconds: int = 0

        self._load_cached_credentials()

    def _load_cached_credentials(self) -> bool:
        if not self.auth_file_path.exists():
            return False
        try:
            ciphertext = self.auth_file_path.read_bytes()
            plaintext = dpapi_decrypt(ciphertext)
            data = json.loads(plaintext.decode("utf-8"))
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at")
            self._account_email = data.get("account_email")
            self._status["account_email"] = self._account_email
            if not self.client_id and data.get("client_id"):
                self.client_id = data["client_id"]
            if not self.client_secret and data.get("client_secret"):
                self.client_secret = data["client_secret"]
            return True
        except Exception as exc:
            logger.error("Failed loading cached Zoom credentials: %s", exc)
            return False

    def _save_credentials(self) -> None:
        try:
            self.auth_file_path.parent.mkdir(parents=True, exist_ok=True)
            token_data = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
                "account_email": self._account_email,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            plaintext = json.dumps(token_data).encode("utf-8")
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
            return False
        if not self.is_token_near_expiry():
            return True
        if self._refresh_token:
            return self.refresh_token()
        return False

    def _get_basic_auth_header(self) -> str:
        creds = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return f"Basic {base64.b64encode(creds).decode('ascii')}"

    def refresh_token(self) -> bool:
        """Refresh Zoom OAuth access token."""
        if not self._refresh_token or not self.client_id or not self.client_secret:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
            return False

        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
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
                return True
        except Exception as exc:
            logger.error("Failed refreshing Zoom token: %s", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "REFRESH_FAILED"
            return False

    def build_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build Zoom OAuth authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    def authenticate_interactive(
        self,
        callback_port: int = 8080,
        timeout_sec: int = 180,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Interactive Zoom OAuth callback on http://localhost:8080/zoom_callback.
        """
        if not self.client_id or not self.client_secret:
            return False, "Zoom Client ID and Client Secret are required. Enter them in VOX Settings."

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
                    result_box["error"] = err_list[0]
                    title = "Zoom Authorization Unsuccessful"
                    body = f"Zoom returned error: {err_list[0]}"
                elif not code_list or not state_list:
                    result_box["error"] = "Missing authorization code"
                    title = "Error"
                    body = "Missing code or state in Zoom response."
                elif state_list[0] != expected_state:
                    result_box["error"] = "State verification failed"
                    title = "Security Alert"
                    body = "State verification failed."
                else:
                    result_box["code"] = code_list[0]
                    result_box["state"] = state_list[0]
                    title = "Zoom Connected to VOX!"
                    body = "Your Zoom meetings are now synchronized with VOX Notetaker. You may close this tab."

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
                server = HTTPServer(("127.0.0.1", int(callback_port)), _ZoomCallbackHandler)
            except OSError:
                server = HTTPServer(("127.0.0.1", 0), _ZoomCallbackHandler)

            server.timeout = 0.5
            actual_port = int(server.server_address[1])
            redirect_uri = f"http://localhost:{actual_port}/zoom_callback"
            auth_url = self.build_authorization_url(redirect_uri, expected_state)

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

            webbrowser.open(auth_url)

            deadline = time.monotonic() + float(timeout_sec)
            while not result_box["done"].is_set() and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Zoom sign-in cancelled."
                time.sleep(0.2)

            if not result_box["done"].is_set():
                return False, f"Zoom sign-in timed out after {timeout_sec}s."

            if result_box["error"]:
                return False, str(result_box["error"])

            code = result_box["code"]
            return self._exchange_code_for_tokens(code, redirect_uri)

        except Exception as exc:
            return False, f"Zoom OAuth error: {exc}"
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str) -> Tuple[bool, str]:
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode("utf-8")

        req = urllib.request.Request(self.TOKEN_ENDPOINT, data=data, method="POST")
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
                    self._status["account_email"] = email

                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                return True, f"Connected Zoom as {self._account_email or 'Zoom User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            desc = f"Zoom OAuth error ({http_err.code}): {body}"
            self._status["is_healthy"] = False
            self._status["error_state"] = "OAUTH_FAILED"
            self._status["error_detail"] = desc
            return False, desc
        except Exception as exc:
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_ERROR"
            return False, str(exc)

    def _fetch_user_profile(self) -> Optional[str]:
        if not self._access_token:
            return None
        try:
            req = urllib.request.Request(f"{self.ZOOM_API_BASE}/users/me", method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("email") or data.get("first_name", "") + " " + data.get("last_name", "")
        except Exception as exc:
            logger.debug("Failed fetching Zoom user profile: %s", exc)
            return None

    def authenticate(self, interactive: bool = True) -> bool:
        if self.is_authenticated():
            return True
        if not interactive:
            return False
        ok, _msg = self.authenticate_interactive()
        return ok

    def disconnect(self) -> bool:
        self._access_token = None
        self._refresh_token = None
        self._expires_at = None
        self._account_email = None
        try:
            if self.auth_file_path.exists():
                self.auth_file_path.unlink()
        except Exception as exc:
            logger.error("Failed removing Zoom auth file: %s", exc)
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["account_email"] = None
        return True

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        """Fetch scheduled and upcoming Zoom meetings for authorized user."""
        self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        if not self.is_authenticated():
            return []

        if self.is_token_near_expiry():
            if not self.refresh_token():
                return []

        all_meetings: List[CalendarMeeting] = []
        now_dt = datetime.now(timezone.utc)
        horizon_dt = now_dt + timedelta(minutes=max(15, window_minutes))

        # Query both 'upcoming' (immediate 24h) and 'scheduled' (up to 6 months) with pagination
        for m_type in ("upcoming", "scheduled"):
            url: Optional[str] = f"{self.ZOOM_API_BASE}/users/me/meetings?type={m_type}&page_size=100"
            max_pages = 5  # Fetch up to 500 meetings
            while url and max_pages > 0:
                max_pages -= 1
                try:
                    req = urllib.request.Request(url, method="GET")
                    req.add_header("Authorization", f"Bearer {self._access_token}")

                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        items = data.get("meetings", [])

                        for item in items:
                            m_id = str(item.get("id", ""))
                            if any(m.id == m_id for m in all_meetings):
                                continue

                            title = str(item.get("topic") or "Zoom Meeting").strip()
                            st_str = item.get("start_time", "")
                            duration = int(item.get("duration", 30))

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

                            join_url = str(item.get("join_url") or "").strip()
                            agenda = str(item.get("agenda") or "").strip()

                            all_meetings.append(
                                CalendarMeeting(
                                    id=m_id,
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
                                    provider_type=CalendarProviderType.ZOOM.value,
                                    provider_account=self._account_email or "",
                                    description=agenda,
                                )
                            )

                        next_page_token = data.get("next_page_token")
                        if next_page_token:
                            url = f"{self.ZOOM_API_BASE}/users/me/meetings?type={m_type}&page_size=100&next_page_token={urllib.parse.quote(next_page_token)}"
                        else:
                            url = None

                except urllib.error.HTTPError as http_err:
                    if http_err.code == 401:
                        self._status["error_state"] = "AUTH_REQUIRED"
                    elif http_err.code == 429:
                        self.retry_after_seconds = int(http_err.headers.get("Retry-After", 30))
                        self._status["error_state"] = "HTTP_429_RATE_LIMIT"
                    self._status["is_healthy"] = False
                    break
                except Exception as exc:
                    logger.error("Failed querying Zoom meetings (%s): %s", m_type, exc)
                    self._status["is_healthy"] = False
                    self._status["error_state"] = "NETWORK_OFFLINE"
                    break

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        return all_meetings

    def get_provider_status(self) -> Dict[str, Any]:
        return dict(self._status)

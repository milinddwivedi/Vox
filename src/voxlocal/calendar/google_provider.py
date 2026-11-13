"""
VoxLocal Google Calendar Provider.
Production-grade Google Calendar integration supporting:
- Google OAuth 2.0 PKCE / local HTTP callback listener (RFC 7636)
- Google Calendar API v3 events synchronization (/calendars/primary/events)
- Expanding recurring events with singleEvents=true and orderBy=startTime
- Automatic link and platform detection (Google Meet, Zoom, Teams, Webex)
- DPAPI token encryption on Windows and seamless offline error recovery
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

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting, CalendarProviderType
from voxlocal.calendar.graph_provider import dpapi_decrypt, dpapi_encrypt, extract_meeting_links
from voxlocal.core.logger import get_logger

logger = get_logger("calendar.google")


class GoogleCalendarProvider(BaseCalendarProvider):
    """
    Google Calendar integration conforming to BaseCalendarProvider.
    Fetches real upcoming meetings, recurring instances, and video links.
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
    ) -> None:
        self.client_id = (client_id or os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "")).strip()
        self.auth_file_path = auth_file_path or (Path("data") / "google_calendar_auth.dat")
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._account_email: Optional[str] = None
        self._status: Dict[str, Any] = {
            "provider_type": CalendarProviderType.GOOGLE.value,
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
            logger.error("Failed loading cached Google Calendar credentials: %s", exc)
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
            logger.error("Failed saving Google Calendar DPAPI credentials: %s", exc)

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

    def refresh_token(self) -> bool:
        """Refresh Google OAuth 2.0 access token using refresh_token."""
        if not self._refresh_token or not self.client_id:
            self._status["error_state"] = "NO_REFRESH_TOKEN"
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
                return True
        except Exception as exc:
            logger.error("Failed refreshing Google token: %s", exc)
            self._status["is_healthy"] = False
            self._status["error_state"] = "REFRESH_FAILED"
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
    ) -> Tuple[bool, str]:
        """
        Interactive Google OAuth with loopback server on http://localhost:8080/google_callback.
        """
        if not self.client_id:
            return False, "Google Client ID is required. Enter it in VOX Calendar Settings."

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
                    result_box["error"] = err_list[0]
                    title = "Google Sign-In Unsuccessful"
                    body = f"Google returned error: {err_list[0]}"
                elif not code_list or not state_list:
                    result_box["error"] = "Missing authorization code"
                    title = "Error"
                    body = "Missing code or state in Google response."
                elif state_list[0] != expected_state:
                    result_box["error"] = "State verification failed"
                    title = "Security Alert"
                    body = "State verification failed."
                else:
                    result_box["code"] = code_list[0]
                    result_box["state"] = state_list[0]
                    title = "Google Calendar Connected!"
                    body = "Your Google Calendar is now connected to VOX Notetaker. You can close this tab."

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

            webbrowser.open(auth_url)

            deadline = time.monotonic() + float(timeout_sec)
            while not result_box["done"].is_set() and time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "Google sign-in cancelled."
                time.sleep(0.2)

            if not result_box["done"].is_set():
                return False, f"Google sign-in timed out after {timeout_sec}s."

            if result_box["error"]:
                return False, str(result_box["error"])

            code = result_box["code"]
            return self._exchange_code_for_tokens(code, redirect_uri, code_verifier)

        except Exception as exc:
            return False, f"Google OAuth error: {exc}"
        finally:
            if server:
                try:
                    server.server_close()
                except Exception:
                    pass

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str, code_verifier: str) -> Tuple[bool, str]:
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

                # Fetch email
                email = self._fetch_user_email()
                if email:
                    self._account_email = email
                    self._status["account_email"] = email

                self._save_credentials()
                self._status["is_healthy"] = True
                self._status["error_state"] = None
                return True, f"Connected Google Calendar as {self._account_email or 'User'}"
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            desc = f"Google OAuth error ({http_err.code}): {body}"
            self._status["is_healthy"] = False
            self._status["error_state"] = "OAUTH_FAILED"
            self._status["error_detail"] = desc
            return False, desc
        except Exception as exc:
            self._status["is_healthy"] = False
            self._status["error_state"] = "NETWORK_ERROR"
            return False, str(exc)

    def _fetch_user_email(self) -> Optional[str]:
        if not self._access_token:
            return None
        try:
            req = urllib.request.Request(self.USERINFO_ENDPOINT, method="GET")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("email")
        except Exception as exc:
            logger.debug("Failed fetching Google user email: %s", exc)
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
            logger.error("Failed removing Google auth cache: %s", exc)
        self._status["is_healthy"] = True
        self._status["error_state"] = "DISCONNECTED"
        self._status["account_email"] = None
        return True

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        """
        Fetch scheduled Google Calendar meetings (default 30-day window).
        singleEvents=true expands recurring events automatically into distinct occurrences.
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
            f"{self.CALENDAR_API_BASE}/calendars/primary/events"
            f"?singleEvents=true"
            f"&orderBy=startTime"
            f"&timeMin={urllib.parse.quote(start_iso)}"
            f"&timeMax={urllib.parse.quote(end_iso)}"
            f"&maxResults=250"
        )

        all_meetings: List[CalendarMeeting] = []
        max_pages = 5

        while url and max_pages > 0:
            max_pages -= 1
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("Authorization", f"Bearer {self._access_token}")

                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    items = data.get("items", [])

                    for item in items:
                        if item.get("status") == "cancelled":
                            continue

                        m_id = str(item.get("id", ""))
                        title = str(item.get("summary") or "Untitled Meeting").strip()

                        # Parse Start & End times
                        st_obj = item.get("start", {})
                        et_obj = item.get("end", {})
                        st_str = st_obj.get("dateTime") or st_obj.get("date")
                        et_str = et_obj.get("dateTime") or et_obj.get("date")
                        if not st_str or not et_str:
                            continue

                        try:
                            start_time = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                            end_time = datetime.fromisoformat(et_str.replace("Z", "+00:00"))
                            # If date only (all-day), give UTC timezone
                            if start_time.tzinfo is None:
                                start_time = start_time.replace(tzinfo=timezone.utc)
                            if end_time.tzinfo is None:
                                end_time = end_time.replace(tzinfo=timezone.utc)
                        except Exception:
                            continue

                        location = str(item.get("location") or "").strip()
                        description = str(item.get("description") or "").strip()

                        organizer_obj = item.get("organizer", {})
                        org_name = organizer_obj.get("displayName", "")
                        org_email = organizer_obj.get("email", "")
                        eff_org = f"{org_name} ({org_email})" if (org_name and org_email and org_name != org_email) else (org_name or org_email)

                        attendees: List[str] = []
                        for att in item.get("attendees", []):
                            a_name = att.get("displayName", "").strip()
                            a_email = att.get("email", "").strip()
                            if a_name:
                                attendees.append(f"{a_name} ({a_email})" if (a_email and a_name != a_email) else a_name)
                            elif a_email:
                                attendees.append(a_email)

                        # Detect Meet, Zoom, Teams links
                        join_url = None
                        platform = "generic"

                        # 1. Check conferenceData
                        conf_data = item.get("conferenceData", {})
                        for ep in conf_data.get("entryPoints", []):
                            if ep.get("entryPointType") == "video" and ep.get("uri"):
                                join_url = ep["uri"]
                                platform = "meet"
                                break

                        # 2. Check hangoutLink
                        if not join_url and item.get("hangoutLink"):
                            join_url = item["hangoutLink"]
                            platform = "meet"

                        # 3. Check location and description for Zoom, Teams, Meet
                        if not join_url:
                            found_url, found_plat = extract_meeting_links(f"{location}\n{description}")
                            if found_url:
                                join_url = found_url
                                platform = found_plat

                        all_meetings.append(
                            CalendarMeeting(
                                id=m_id,
                                title=title,
                                start_time=start_time,
                                end_time=end_time,
                                location=location or ("Google Meet" if platform == "meet" else ""),
                                join_url=join_url,
                                attendees=attendees,
                                organizer=eff_org,
                                is_online=bool(join_url or "meet" in location.lower()),
                                is_cancelled=False,
                                platform=platform,
                                provider_type=CalendarProviderType.GOOGLE.value,
                                provider_account=self._account_email or "",
                                is_recurring=bool(item.get("recurringEventId")),
                                description=re.sub(r"<[^>]+>", " ", description)[:500].strip(),
                            )
                        )

                    next_page = data.get("nextPageToken")
                    if next_page:
                        url = f"{url}&pageToken={urllib.parse.quote(next_page)}"
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
                logger.error("Failed querying Google Calendar events: %s", exc)
                self._status["is_healthy"] = False
                self._status["error_state"] = "NETWORK_OFFLINE"
                break

        self._status["is_healthy"] = True
        self._status["error_state"] = None
        return all_meetings

    def get_provider_status(self) -> Dict[str, Any]:
        return dict(self._status)

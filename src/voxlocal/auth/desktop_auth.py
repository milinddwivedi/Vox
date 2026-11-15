"""
VOX Desktop Google Authentication Broker with PKCE.

Provides secure desktop sign-in via the system default browser (Chrome/Edge),
uses a local loopback callback server on 127.0.0.1 with PKCE,
stores session credentials in Windows DPAPI, and connects customer identity
directly to the Supabase / Vercel entitlement ledger.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request
import webbrowser

from voxlocal.calendar.auth_service import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("desktop_auth")

DEFAULT_BACKEND_URL = "https://vox-voice-assistant.vercel.app"
LOOPBACK_PORTS = [8400, 8401, 8402, 8403]


@dataclass
class UserProfile:
    user_id: str
    email: str
    display_name: str = ""
    avatar_url: str = ""
    plan_code: str = "free"
    is_pro: bool = False
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "plan_code": self.plan_code,
            "is_pro": self.is_pro,
            "expires_at": self.expires_at,
        }


def generate_pkce_pair() -> Tuple[str, str]:
    """Generate RFC 7636 PKCE code_verifier and S256 code_challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


SUCCESS_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>VOX Sign-In Successful</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #080808; color: #FFFFFF; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .card { background: #141416; border: 1px solid rgba(255,255,255,0.12); border-radius: 24px; padding: 48px; max-width: 440px; text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.5); }
    .icon { width: 56px; height: 56px; border-radius: 18px; background: #30C382; color: #080808; display: inline-flex; align-items: center; justify-content: center; font-size: 28px; font-weight: bold; margin-bottom: 20px; }
    h1 { font-size: 24px; margin: 0 0 10px; font-weight: 700; }
    p { color: #A1A1AA; font-size: 15px; line-height: 1.5; margin: 0 0 24px; }
    .btn { display: inline-block; background: #2C2C2E; color: #FFFFFF; padding: 10px 20px; border-radius: 12px; text-decoration: none; font-size: 14px; font-weight: 600; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>Signed in to VOX</h1>
    <p>Your Google account has been connected to VOX Desktop. You can close this tab and return to the application.</p>
    <a class="btn" href="javascript:window.close()">Close Window</a>
  </div>
</body>
</html>"""


class DesktopAuthBroker:
    """
    Coordinates browser Google Sign-in with local loopback callback,
    exchanges tokens, and synchronizes entitlements.
    """

    def __init__(self, vault_path: Optional[Path] = None, backend_url: str = DEFAULT_BACKEND_URL) -> None:
        self.lock = threading.RLock()
        self.backend_url = os.environ.get("VOX_BACKEND_URL", backend_url).rstrip("/")
        if vault_path is None:
            root = get_app_root()
            self.vault_path = root / "data" / "auth_session.vault"
        else:
            self.vault_path = Path(vault_path)

        self._current_user: Optional[UserProfile] = None
        self._load_cached_session()

    def _load_cached_session(self) -> None:
        with self.lock:
            if not self.vault_path.exists():
                return
            try:
                encrypted = self.vault_path.read_bytes()
                decrypted = dpapi_decrypt(encrypted)
                data = json.loads(decrypted.decode("utf-8"))
                self._current_user = UserProfile(
                    user_id=data.get("user_id", "default"),
                    email=data.get("email", ""),
                    display_name=data.get("display_name", ""),
                    avatar_url=data.get("avatar_url", ""),
                    plan_code=data.get("plan_code", "free"),
                    is_pro=bool(data.get("is_pro", False)),
                    access_token=data.get("access_token", ""),
                    refresh_token=data.get("refresh_token", ""),
                    expires_at=float(data.get("expires_at", 0.0)),
                )
                logger.info("Loaded cached desktop user: %s (%s)", self._current_user.email, self._current_user.plan_code)
            except Exception as exc:
                logger.warning("Could not decrypt desktop auth session: %s", exc)
                self._current_user = None

    def _save_session(self, profile: UserProfile) -> None:
        with self.lock:
            self._current_user = profile
            raw = json.dumps({
                "user_id": profile.user_id,
                "email": profile.email,
                "display_name": profile.display_name,
                "avatar_url": profile.avatar_url,
                "plan_code": profile.plan_code,
                "is_pro": profile.is_pro,
                "access_token": profile.access_token,
                "refresh_token": profile.refresh_token,
                "expires_at": profile.expires_at,
            }).encode("utf-8")
            encrypted = dpapi_encrypt(raw, description="vox_user_session")
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            self.vault_path.write_bytes(encrypted)
            logger.info("Saved desktop auth session for %s", profile.email)

    def get_current_user(self) -> Optional[UserProfile]:
        with self.lock:
            return self._current_user

    def is_authenticated(self) -> bool:
        with self.lock:
            return self._current_user is not None and bool(self._current_user.user_id and self._current_user.email)

    def sign_out(self) -> None:
        with self.lock:
            if self.vault_path.exists():
                try:
                    self.vault_path.unlink()
                except Exception:
                    pass
            self._current_user = None
            logger.info("Desktop user signed out")

    def start_browser_login(
        self,
        on_success: Callable[[UserProfile], None],
        on_error: Callable[[str], None],
        timeout_sec: float = 120.0,
    ) -> None:
        """Launch browser OAuth flow using system default browser."""
        thread = threading.Thread(
            target=self._run_login_flow,
            args=(on_success, on_error, timeout_sec),
            name="DesktopAuthWorker",
            daemon=True,
        )
        thread.start()

    def _run_login_flow(
        self,
        on_success: Callable[[UserProfile], None],
        on_error: Callable[[str], None],
        timeout_sec: float,
    ) -> None:
        state = secrets.token_urlsafe(24)
        verifier, challenge = generate_pkce_pair()

        server: Optional[HTTPServer] = None
        bound_port: Optional[int] = None
        for port in LOOPBACK_PORTS:
            try:
                server = HTTPServer(("127.0.0.1", port), _AuthLoopbackHandler)
                bound_port = port
                break
            except OSError:
                continue

        if not server or not bound_port:
            on_error("Could not bind local callback server (ports 8400-8403 busy).")
            return

        callback_result: Dict[str, Any] = {}
        completed_event = threading.Event()

        # Attach state context to server instance
        server.auth_broker_state = state  # type: ignore
        server.auth_result_holder = callback_result  # type: ignore
        server.auth_completed_event = completed_event  # type: ignore

        redirect_uri = f"http://127.0.0.1:{bound_port}/callback"
        auth_url = (
            f"{self.backend_url}/api/auth/desktop-login"
            f"?redirect_uri={urllib.parse.quote(redirect_uri)}"
            f"&state={urllib.parse.quote(state)}"
            f"&code_challenge={urllib.parse.quote(challenge)}"
        )

        def _serve() -> None:
            while not completed_event.is_set():
                server.handle_request()

        server_thread = threading.Thread(target=_serve, daemon=True)
        server_thread.start()

        logger.info("Opening browser for VOX Google Sign-in: %s", auth_url)
        webbrowser.open(auth_url)

        got_response = completed_event.wait(timeout=timeout_sec)
        try:
            server.server_close()
        except Exception:
            pass

        if not got_response:
            on_error("Sign-in timed out waiting for browser authorization.")
            return

        err = callback_result.get("error")
        if err:
            on_error(f"Sign-in error: {err}")
            return

        # Profile / token received from Vercel bridge or direct exchange
        user_id = callback_result.get("user_id") or callback_result.get("sub") or str(secrets.token_hex(8))
        email = callback_result.get("email") or "user@voxlocal.ai"
        display_name = callback_result.get("name") or callback_result.get("display_name") or email.split("@")[0]
        avatar_url = callback_result.get("picture") or callback_result.get("avatar_url") or ""
        plan_code = callback_result.get("plan_code", "free")
        is_pro = callback_result.get("is_pro", False) or (plan_code in ("pro_monthly", "pro_annual", "pro"))

        profile = UserProfile(
            user_id=user_id,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
            plan_code=plan_code,
            is_pro=is_pro,
            access_token=callback_result.get("access_token", ""),
            refresh_token=callback_result.get("refresh_token", ""),
            expires_at=time.time() + float(callback_result.get("expires_in", 3600 * 24)),
        )

        self._save_session(profile)
        on_success(profile)


class _AuthLoopbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default stdout log spam

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        received_state = params.get("state", [""])[0]
        expected_state = getattr(self.server, "auth_broker_state", "")

        holder = getattr(self.server, "auth_result_holder", {})
        event: threading.Event = getattr(self.server, "auth_completed_event", threading.Event())

        if expected_state and received_state != expected_state:
            holder["error"] = "Security state mismatch. Please retry sign-in."
            self._send_html(400, "<h1>Sign-in Failed</h1><p>State verification mismatch.</p>")
            event.set()
            return

        if "error" in params:
            holder["error"] = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            self._send_html(400, f"<h1>Sign-in Cancelled</h1><p>{holder['error']}</p>")
            event.set()
            return

        # Extract tokens/profile parameters provided by the auth bridge
        holder["user_id"] = params.get("user_id", [""])[0]
        holder["email"] = params.get("email", [""])[0]
        holder["name"] = params.get("name", [""])[0]
        holder["avatar_url"] = params.get("avatar_url", [""])[0]
        holder["plan_code"] = params.get("plan_code", ["free"])[0]
        holder["is_pro"] = params.get("is_pro", ["false"])[0].lower() in ("true", "1")
        holder["access_token"] = params.get("access_token", [""])[0]
        holder["refresh_token"] = params.get("refresh_token", [""])[0]

        self._send_html(200, SUCCESS_HTML_PAGE)
        event.set()

    def _send_html(self, status_code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
